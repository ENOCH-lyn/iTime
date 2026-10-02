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
import time
from contextlib import ExitStack, contextmanager
from pathlib import Path

import httpx
import pyotp
from sqlalchemy import delete, select
from sqlalchemy.engine import make_url

from itime.admin.models import AdminCredential, AdminEnrollment, AdminRecoveryCode
from itime.admin.operations import create_admin
from itime.admin.security import normalize_recovery_code
from itime.auth.models import User
from itime.auth.security import Security, now
from itime.config import Settings
from itime.database import Database
from tests.conftest import SERVER, isolated_database

WEB = SERVER.parent / "web"
ADMIN_PASSWORD = "Playwright-admin-password-123"


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


async def seed_admins(database_url: str, public_origin: str) -> list[dict]:
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
        url = stack.enter_context(isolated_database(
            parsed.set(database="postgres").render_as_string(hide_password=False)
        ))
        public_origin = "http://localhost:4173"
        payload = asyncio.run(seed_admins(url, public_origin))
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
        web_process = stack.enter_context(process(
            [str(WEB / "node_modules/.bin/vite"), "preview", "--host", "localhost",
             "--port", "4173"],
            WEB, env, logs / "web.log",
        ))
        ready("http://localhost:4173", web_process, logs / "web.log")
        print(f"iTime UI isolated services ready; logs: {logs}", flush=True)
        while all(child.poll() is None for child in (api_process, mail_process, web_process)):
            time.sleep(0.5)
        raise RuntimeError(f"UI 测试进程意外退出，请检查 {logs}")


if __name__ == "__main__":
    main()
