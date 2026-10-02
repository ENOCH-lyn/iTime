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
        content = (
            '{"marker":"09:00"}'
            if "synthetic vision endpoint check" in prompt
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
        encoded = json.dumps(
            {"choices": [{"message": {"content": content}}]}
        ).encode()
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
        current = now()
        async with database.transaction() as session:
            state = await session.get(
                AIConfigCapability,
                "calendar_screenshot",
                with_for_update=True,
            )
            config = AIConfigVersion(
                capability="calendar_screenshot",
                version=state.next_version,
                endpoint_url=ai_endpoint,
                model="vision-test-model",
                api_key_ciphertext=security.ai_cipher.encrypt(b"playwright-ai-key"),
                timeout_seconds=10,
                max_output_tokens=4096,
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
                web_process,
            )
        ):
            time.sleep(0.5)
        raise RuntimeError(f"UI 测试进程意外退出，请检查 {logs}")


if __name__ == "__main__":
    main()
