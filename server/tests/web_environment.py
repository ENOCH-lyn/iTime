"""Run the built Vue app, real API and mail worker against a disposable local database."""

import asyncio
import json
import os
import secrets
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import ExitStack, contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import httpx
import pyotp
from sqlalchemy import delete, select
from sqlalchemy.engine import make_url

from itime.admin.models import AdminCredential, AdminEnrollment, AdminRecoveryCode
from itime.admin.operations import create_admin
from itime.admin.security import normalize_recovery_code
from itime.ai.models import AIConfigCapability, AIConfigVersion
from itime.auth.models import User
from itime.auth.security import Security, now
from itime.config import Settings
from itime.database import Database
from itime.quota.models import AIQuotaPolicyState, AIQuotaPolicyVersion
from itime.quota.service import RESERVATION_ESTIMATOR_VERSION
from tests.conftest import SERVER, isolated_database

WEB = SERVER.parent / "web"
ADMIN_PASSWORD = "Playwright-admin-password-123"


def recognized_calendar_draft() -> dict:
    return {
        "kind": "fixed",
        "term_id": None,
        "name": "高等数学",
        "description": "",
        "location": "教学楼 A101",
        "busy": True,
        "time_zone": "Asia/Shanghai",
        "start_date": "2026-10-05",
        "starts_at": "09:00:00",
        "ends_at": "10:30:00",
        "end_day_offset": 0,
        "start_period_number": None,
        "end_period_number": None,
        "ambiguous_utc_offset_minutes": None,
        "recurrence": {
            "frequency": "none",
            "interval": 1,
            "weekdays": [],
            "month": None,
            "day_of_month": None,
            "until": None,
            "count": None,
            "week_numbers": [],
        },
    }


def planning_tool_arguments(request_body: dict) -> dict:
    snapshot = json.loads(request_body["messages"][-1]["content"])[
        "planning_snapshot"
    ]
    prompt = json.dumps(request_body["messages"], ensure_ascii=False)
    candidates = snapshot["task_candidates"]
    estimates = []
    adopted_estimates = {}
    for candidate in candidates:
        estimate = candidate["estimated_minutes"] or max(
            45,
            candidate["minimum_block_minutes"],
        )
        adopted_estimates[candidate["candidate_key"]] = estimate
        if candidate["estimated_minutes"] is None:
            estimates.append(
                {
                    "candidate_key": candidate["candidate_key"],
                    "estimated_minutes": estimate,
                    "reason": "按测试任务规模给出可复核估时。",
                }
            )
    unscheduled = []
    start_hour, start_minute = map(
        int,
        snapshot["availability_start"].split(":")[:2],
    )
    cursor = start_hour * 60 + start_minute
    blocks = []
    for index, candidate in enumerate(candidates):
        estimate = adopted_estimates[candidate["candidate_key"]]
        if index < 2 and estimate <= 180:
            end_total = cursor + estimate
            blocks.append(
                {
                    "candidate_key": candidate["candidate_key"],
                    "local_date": snapshot["target_date"],
                    "starts_at": f"{cursor // 60:02d}:{cursor % 60:02d}:00",
                    "ends_at": f"{end_total // 60:02d}:{end_total % 60:02d}:00",
                    "step_ids": [
                        step["id"]
                        for step in candidate["steps"]
                        if not step["completed"]
                    ][:1],
                }
            )
            cursor = end_total + snapshot["buffer_minutes"]
            continue
        unscheduled.append(
            {
                "candidate_key": candidate["candidate_key"],
                "remaining_minutes": estimate,
                "reason": "no_capacity",
                "explanation": "当前提案先保留这一项，确认后可手工调整。",
            }
        )
    return {
        "summary": (
            "已按长期记忆调整计划。"
            if "计划时优先安排上午" in prompt
            else "先完成最重要的一项，其余任务保留为待安排。"
        ),
        "assumptions": ["按当前可规划时段和已有估时安排。"],
        "estimates": estimates,
        "blocks": blocks,
        "unscheduled_items": unscheduled,
    }


def review_tool_arguments(request_body: dict) -> dict:
    snapshot = json.loads(request_body["messages"][-1]["content"])[
        "review_snapshot"
    ]
    user_sources = []
    if snapshot["user_context"]["supplemental_facts"]:
        user_sources.append("user:supplemental_facts")
    user_sources.extend(
        item["source_key"]
        for item in snapshot["user_context"]["messages"]
        if item["role"] == "user"
    )
    facts = []
    for group in ("planned_blocks", "task_completions", "focus", "difficulties"):
        for item in snapshot["facts"][group]["items"][:1]:
            facts.append(
                {
                    "source_key": item["source_key"],
                    "text": (
                        item.get("title")
                        or item.get("content")
                        or "已有记录"
                    ),
                }
            )
    memory_candidates = (
        [
            {
                "kind": "preference",
                "title": "深度工作时段",
                "content": "上午更适合持续思考。",
                "rationale": "来自用户在复盘中的明确说明。",
                "source_keys": [user_sources[0]],
            },
            {
                "kind": "context",
                "title": "资料整理方式",
                "content": "整理资料时先确定核心入口。",
                "rationale": "来自用户在复盘中的明确说明。",
                "source_keys": [user_sources[0]],
            },
        ]
        if user_sources
        else []
    )
    return {
        "summary": "今天完成了核心事项，也留下了可继续优化的线索。",
        "facts": facts,
        "user_statements": (
            [snapshot["user_context"]["supplemental_facts"]]
            if snapshot["user_context"]["supplemental_facts"]
            else []
        ),
        "insights": [
            {
                "text": "较完整的时间段可能更适合当前任务。",
                "basis": "ai_inference",
            }
        ],
        "next_actions": ["明早继续整理资料。"],
        "uncertainties": [] if facts else ["这一天没有计划或执行记录。"],
        "memory_candidates": memory_candidates,
    }


class FakeAIHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/health":
            self.send_response(200)
            self.end_headers()
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length))
        prompt = json.dumps(body.get("messages", []), ensure_ascii=False)
        if body.get("stream") is True:
            tool_name = (
                body.get("tool_choice", {})
                .get("function", {})
                .get("name")
            )
            if tool_name == "submit_itime_plan":
                planning_snapshot = json.loads(body["messages"][-1]["content"])[
                    "planning_snapshot"
                ]
                if any(
                    marker in planning_snapshot["instruction"]
                    for marker in ("取消测试", "离页恢复测试")
                ):
                    time.sleep(1.5)
                arguments = json.dumps(
                    planning_tool_arguments(body),
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                argument_parts = [
                    arguments[: len(arguments) // 2],
                    arguments[len(arguments) // 2 :],
                ]
                events = [
                    {
                        "choices": [
                            {
                                "index": 0,
                                "delta": {"content": "计划草案已生成。"},
                            }
                        ]
                    },
                    {
                        "choices": [
                            {
                                "index": 0,
                                "delta": {
                                    "tool_calls": [
                                        {
                                            "index": 0,
                                            "id": "playwright-planning-call",
                                            "type": "function",
                                            "function": {
                                                "name": "submit_itime_plan",
                                                "arguments": argument_parts[0],
                                            },
                                        }
                                    ]
                                },
                            }
                        ]
                    },
                    {
                        "choices": [
                            {
                                "index": 0,
                                "delta": {
                                    "tool_calls": [
                                        {
                                            "index": 0,
                                            "function": {
                                                "arguments": argument_parts[1],
                                            },
                                        }
                                    ]
                                },
                            }
                        ]
                    },
                    {
                        "choices": [],
                        "usage": {
                            "prompt_tokens": 180,
                            "completion_tokens": 90,
                            "total_tokens": 270,
                            "prompt_tokens_details": {"cached_tokens": 0},
                        },
                    },
                ]
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self.send_header("x-request-id", "playwright-fake-planning")
                self.end_headers()
                for event in events:
                    self.wfile.write(
                        f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode()
                    )
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
                self.close_connection = True
                return
            if tool_name in {
                "submit_itime_review_question",
                "submit_itime_review_report",
            }:
                if tool_name == "submit_itime_review_report":
                    snapshot = json.loads(body["messages"][-1]["content"])[
                        "review_snapshot"
                    ]
                    if "离页恢复" in snapshot["generation_instruction"]:
                        time.sleep(1.5)
                    arguments = review_tool_arguments(body)
                else:
                    arguments = {"question": "今天最顺利的是哪一段？"}
                encoded_arguments = json.dumps(
                    arguments,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                midpoint = len(encoded_arguments) // 2
                events = [
                    {
                        "choices": [
                            {
                                "index": 0,
                                "delta": {
                                    "tool_calls": [
                                        {
                                            "index": 0,
                                            "id": "playwright-review-call",
                                            "type": "function",
                                            "function": {
                                                "name": tool_name,
                                                "arguments": encoded_arguments[:midpoint],
                                            },
                                        }
                                    ]
                                },
                            }
                        ]
                    },
                    {
                        "choices": [
                            {
                                "index": 0,
                                "delta": {
                                    "tool_calls": [
                                        {
                                            "index": 0,
                                            "function": {
                                                "arguments": encoded_arguments[midpoint:],
                                            },
                                        }
                                    ]
                                },
                            }
                        ]
                    },
                    {
                        "choices": [],
                        "usage": {
                            "prompt_tokens": 120,
                            "completion_tokens": 80,
                            "total_tokens": 200,
                            "prompt_tokens_details": {"cached_tokens": 0},
                        },
                    },
                ]
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self.send_header("x-request-id", "playwright-fake-review")
                self.end_headers()
                for event in events:
                    self.wfile.write(
                        f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode()
                    )
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
                self.close_connection = True
                return
            user_messages = [
                message.get("content", "")
                for message in body.get("messages", [])
                if message.get("role") == "user"
            ]
            user_prompt = str(user_messages[-1] if user_messages else "")
            if "触发失败" in user_prompt:
                encoded = json.dumps({"error": "private-upstream-body"}).encode()
                self.send_response(503)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)
                return
            if "检查记忆上下文" in user_prompt:
                parts = [
                    (
                        "已读取长期记忆。"
                        if "上午更适合持续思考。" in prompt
                        else "未读取长期记忆。"
                    )
                ]
            elif "取消测试" in user_prompt:
                parts = ["已收到这条消息。", "取消后不应出现。"]
            else:
                parts = [
                    "先把今天最重要的事情列出来。",
                    " 然后为第一项留出一段完整时间。",
                ]
            pause = 1.5 if "取消测试" in user_prompt else (
                0.6 if "离页恢复" in user_prompt else 0.12
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.send_header("x-request-id", "playwright-fake-chat")
            self.end_headers()
            try:
                for part in parts:
                    payload = json.dumps(
                        {
                            "choices": [
                                {"index": 0, "delta": {"content": part}}
                            ]
                        },
                        ensure_ascii=False,
                    )
                    self.wfile.write(f"data: {payload}\n\n".encode())
                    self.wfile.flush()
                    time.sleep(pause)
                usage = json.dumps(
                    {
                        "choices": [],
                        "usage": {
                            "prompt_tokens": 100,
                            "completion_tokens": 20,
                            "total_tokens": 120,
                            "prompt_tokens_details": {"cached_tokens": 10},
                        },
                    }
                )
                self.wfile.write(f"data: {usage}\n\n".encode())
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
            except OSError:
                pass
            self.close_connection = True
            return
        content = (
            '{"marker":"09:00"}'
            if "synthetic vision endpoint check" in prompt
            else "ITIME_CHAT_OK"
            if "synthetic text endpoint check" in prompt
            else json.dumps(
                {
                    "items": [
                        {
                            "raw_text": "周一 09:00 高等数学",
                            "confidence": 0.98,
                            "warnings": [],
                            "draft": recognized_calendar_draft(),
                        }
                    ]
                },
                ensure_ascii=False,
            )
        )
        encoded = json.dumps({
            "choices": [{"message": {"content": content}}],
            "usage": {
                "prompt_tokens": 400,
                "completion_tokens": 120,
                "total_tokens": 520,
            },
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("x-request-id", "playwright-fake-ai")
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args) -> None:
        pass


@contextmanager
def fake_ai_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeAIHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


class FakeCalendarHandler(BaseHTTPRequestHandler):
    counts: dict[str, int] = {}
    lock = threading.Lock()

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        if parsed.path == "/health":
            self.send_response(200)
            self.end_headers()
            return
        if not parsed.path.startswith("/calendar/"):
            self.send_response(404)
            self.end_headers()
            return
        scenario = parse_qs(parsed.query).get("scenario", ["basic"])[0]
        with self.lock:
            count = self.counts.get(self.path, 0) + 1
            self.counts[self.path] = count
        if scenario == "failure" and count > 1:
            self.send_response(503)
            self.end_headers()
            return
        if (
            scenario == "basic"
            and count > 1
            and self.headers.get("If-None-Match") == '"basic-v1"'
        ):
            self.send_response(304)
            self.end_headers()
            return
        title = "来源课程更新" if scenario == "update" and count > 1 else "订阅课程"
        events = [
            "BEGIN:VEVENT",
            f"UID:{parsed.path}",
            f"SUMMARY:{title}",
            "LOCATION:教学楼 B203",
            "DTSTART;TZID=Asia/Shanghai:20261006T090000",
            "DTEND;TZID=Asia/Shanghai:20261006T100000",
            "RRULE:FREQ=WEEKLY;BYDAY=TU",
            "END:VEVENT",
        ]
        if scenario == "basic":
            events.extend(
                [
                    "BEGIN:VEVENT",
                    "UID:all-day@example.com",
                    "SUMMARY:全天纪念日",
                    "DTSTART;VALUE=DATE:20261006",
                    "DTEND;VALUE=DATE:20261007",
                    "END:VEVENT",
                    "BEGIN:VEVENT",
                    "UID:sensitive-private-token@example.com",
                    "RECURRENCE-ID:20261007T010000Z",
                    "STATUS:CANCELLED",
                    "END:VEVENT",
                ]
            )
        if scenario == "cancel" and count > 1:
            events = []
        body = (
            "BEGIN:VCALENDAR\r\n"
            "VERSION:2.0\r\n"
            "PRODID:-//iTime Playwright//EN\r\n"
            + "\r\n".join(events)
            + ("\r\n" if events else "")
            + "END:VCALENDAR\r\n"
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/calendar; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header(
            "ETag",
            f'"{scenario}-v{2 if scenario in {"update", "cancel"} and count > 1 else 1}"',
        )
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args) -> None:
        pass


@contextmanager
def fake_calendar_server():
    FakeCalendarHandler.counts = {}
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeCalendarHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://localhost:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@contextmanager
def process(args, cwd, env, log_path):
    with log_path.open("w") as log:
        child = subprocess.Popen(args, cwd=cwd, env=env, stdout=log, stderr=log)
        try:
            yield child
        finally:
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)


def ready(url, child, log_path):
    deadline = time.monotonic() + 20
    with httpx.Client(trust_env=False, timeout=1) as client:
        while time.monotonic() < deadline:
            if child.poll() is not None:
                raise RuntimeError(f"UI 测试服务未启动，请检查 {log_path}")
            try:
                if client.get(url).status_code == 200:
                    return
            except httpx.TransportError:
                pass
            time.sleep(0.1)
    raise RuntimeError(f"UI 测试服务未就绪，请检查 {log_path}")


async def seed_admins(
    database_url: str,
    public_origin: str,
    ai_endpoint: str,
    calendar_origin: str,
) -> list[dict]:
    settings = Settings(
        database_url=database_url,
        environment="test",
        public_origin=public_origin,
    )
    database = Database(settings)
    security = Security(settings)
    result = []
    try:
        for project in ("desktop", "mobile", "tablet"):
            setup_email = f"ui-admin-{project}-setup@example.com"
            setup_link = await create_admin(
                database, security, settings, setup_email, ADMIN_PASSWORD
            )
            entry = {
                "project": project,
                "password": ADMIN_PASSWORD,
                "ai_endpoint": ai_endpoint,
                "calendar_origin": calendar_origin,
                "setup_email": setup_email,
                "setup_link": setup_link,
                "active": {},
            }
            for role in (
                "login",
                "recovery",
                "password",
                "layout",
                "registration",
                "users",
                "management-errors",
                "ai-config",
                "governance",
                "quota",
            ):
                email = f"ui-admin-{project}-{role}@example.com"
                await create_admin(database, security, settings, email, ADMIN_PASSWORD)
                secret = pyotp.random_base32(length=32)
                raw_recovery = secrets.token_hex(10).upper()
                recovery = "-".join(
                    raw_recovery[index:index + 5]
                    for index in range(0, len(raw_recovery), 5)
                )
                normalized = normalize_recovery_code(recovery)
                async with database.transaction() as session:
                    user = await session.scalar(select(User).where(User.email == email))
                    await session.execute(
                        delete(AdminEnrollment).where(AdminEnrollment.user_id == user.id)
                    )
                    session.add(
                        AdminCredential(
                            user_id=user.id,
                            totp_secret_ciphertext=security.cipher.encrypt(secret.encode()),
                            totp_enabled_at=now(),
                            last_totp_counter=None,
                        )
                    )
                    session.add(
                        AdminRecoveryCode(
                            user_id=user.id,
                            code_hash=security.keyed_digest(
                                "admin-recovery:" + normalized
                            ),
                        )
                    )
                entry["active"][role] = {
                    "email": email,
                    "secret": secret,
                    "recovery_code": recovery,
                }
            result.append(entry)
        async with database.transaction() as session:
            for capability, model in (
                ("calendar_screenshot", "vision-test-model"),
                ("conversation", "chat-test-model"),
                ("planning", "planning-test-model"),
                ("review", "review-test-model"),
            ):
                current = now()
                state = await session.get(
                    AIConfigCapability,
                    capability,
                    with_for_update=True,
                )
                config = AIConfigVersion(
                    capability=capability,
                    version=state.next_version,
                    endpoint_url=ai_endpoint,
                    model=model,
                    api_key_ciphertext=security.ai_cipher.encrypt(
                        b"playwright-ai-key"
                    ),
                    timeout_seconds=10,
                    max_output_tokens=4096,
                    price_currency="CNY",
                    input_price_nanos_per_million_tokens=1_000_000_000,
                    cached_input_price_nanos_per_million_tokens=500_000_000,
                    output_price_nanos_per_million_tokens=2_000_000_000,
                    status="published",
                    last_test_id=uuid4(),
                    last_test_started_at=current,
                    last_test_completed_at=current,
                    published_at=current,
                )
                state.next_version += 1
                session.add(config)
                await session.flush()
                state.current_version_id = config.id
            quota_state = await session.get(
                AIQuotaPolicyState,
                1,
                with_for_update=True,
            )
            policy = AIQuotaPolicyVersion(
                version=quota_state.next_version,
                currency="CNY",
                daily_allowance_nanos=1_000_000_000,
                reservation_estimator_version=RESERVATION_ESTIMATOR_VERSION,
                status="published",
                published_at=now(),
            )
            quota_state.next_version += 1
            session.add(policy)
            await session.flush()
            quota_state.current_version_id = policy.id
        return result
    finally:
        await database.close()


def main():
    settings = Settings()
    parsed = make_url(settings.database_url.get_secret_value())
    if settings.environment == "production" or parsed.host not in {"localhost", "127.0.0.1", "::1"}:
        raise RuntimeError("UI 测试只允许本地隔离数据库")
    # SIGTERM from Playwright unwinds all managed processes and the random test DB.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    signal.signal(signal.SIGINT, lambda *_: sys.exit(0))
    logs = Path(tempfile.mkdtemp(prefix="itime-web-services-"))
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        api_port = sock.getsockname()[1]
    with ExitStack() as stack:
        ai_endpoint = stack.enter_context(fake_ai_server())
        calendar_origin = stack.enter_context(fake_calendar_server())
        url = stack.enter_context(isolated_database(
            parsed.set(database="postgres").render_as_string(hide_password=False)
        ))
        public_origin = "http://localhost:4173"
        payload = asyncio.run(
            seed_admins(url, public_origin, ai_endpoint, calendar_origin)
        )
        configured_file = os.environ.get("ITIME_E2E_BOOTSTRAP_FILE")
        if configured_file:
            bootstrap_file = Path(configured_file)
            descriptor = os.open(
                bootstrap_file,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
        else:
            descriptor, generated_file = tempfile.mkstemp(
                prefix="itime-ui-admin-bootstrap-", suffix=".json"
            )
            bootstrap_file = Path(generated_file)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(payload, output)
        stack.callback(bootstrap_file.unlink, missing_ok=True)
        env = {
            **os.environ, "ITIME_ENVIRONMENT": "test", "ITIME_DATABASE_URL": url,
            "ITIME_PUBLIC_ORIGIN": public_origin,
            "ITIME_API_ORIGIN": f"http://127.0.0.1:{api_port}",
            "ITIME_AUTH_IP_LIMIT": "1000", "ITIME_AUTH_IDENTITY_LIMIT": "100",
            "ITIME_MAIL_COOLDOWN_SECONDS": "1",
        }
        api_process = stack.enter_context(process(
            [sys.executable, "-m", "uvicorn", "itime.main:create_app", "--factory",
             "--host", "127.0.0.1", "--port", str(api_port), "--no-access-log"],
            SERVER, env, logs / "api.log",
        ))
        ready(f"http://127.0.0.1:{api_port}/api/ready", api_process, logs / "api.log")
        mail_process = stack.enter_context(process(
            [sys.executable, "-m", "itime.mail_worker"], SERVER, env, logs / "mail.log",
        ))
        ai_process = stack.enter_context(process(
            [sys.executable, "-m", "itime.ai.worker"], SERVER, env, logs / "ai.log",
        ))
        subscription_process = stack.enter_context(process(
            [sys.executable, "-m", "itime.calendar.subscription_worker"],
            SERVER,
            env,
            logs / "subscription.log",
        ))
        export_process = stack.enter_context(process(
            [sys.executable, "-m", "itime.data_export.worker"],
            SERVER,
            env,
            logs / "data-export.log",
        ))
        web_process = stack.enter_context(process(
            [str(WEB / "node_modules/.bin/vite"), "preview", "--host", "localhost",
             "--port", "4173"],
            WEB, env, logs / "web.log",
        ))
        ready("http://localhost:4173", web_process, logs / "web.log")
        print(f"iTime UI isolated services ready; logs: {logs}", flush=True)
        while all(
            child.poll() is None
            for child in (
                api_process,
                mail_process,
                ai_process,
                subscription_process,
                export_process,
                web_process,
            )
        ):
            time.sleep(0.5)
        raise RuntimeError(f"UI 测试进程意外退出，请检查 {logs}")


if __name__ == "__main__":
    main()
