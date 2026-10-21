import asyncio
import os
import re
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import timedelta
from uuid import uuid4

import httpx
import psycopg
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select

from itime.auth.models import MailOutbox
from itime.auth.security import COOKIE_NAME, Security, digest, now
from itime.config import Settings
from itime.database import Database
from itime.mail_worker import deliver_one
from tests.conftest import SERVER, psycopg_url

pytestmark = pytest.mark.integration
PASSWORD = "Valid-test-password-123"
NEW_PASSWORD = "New-valid-password-456"
API = "/api/v1/auth"
MAILPIT = "http://127.0.0.1:58025"


@pytest.fixture(autouse=True)
def clean_rate_buckets(database_url):
    with psycopg.connect(psycopg_url(database_url), autocommit=True) as connection:
        connection.execute("DELETE FROM auth_rate_buckets")
        connection.execute(
            "UPDATE registration_policies "
            "SET mode='open', require_email_verification=true, "
            "version=version+1, updated_at=now(), "
            "updated_by_user_id=NULL WHERE id=1"
        )
    yield
    with psycopg.connect(psycopg_url(database_url), autocommit=True) as connection:
        connection.execute("DELETE FROM auth_rate_buckets")
        connection.execute(
            "UPDATE registration_policies "
            "SET mode='open', require_email_verification=true, "
            "version=version+1, updated_at=now(), "
            "updated_by_user_id=NULL WHERE id=1"
        )


def address() -> str:
    return f"itime-{uuid4().hex}@example.com"


def register(client, email, password=PASSWORD):
    response = client.post(f"{API}/register", json={"email": email, "password": password})
    assert response.status_code == 202, response.text
    return response


async def drain_mail(database_url):
    settings = Settings(database_url=database_url, environment="test")
    database = Database(settings)
    try:
        for _ in range(100):
            if not await deliver_one(database, settings):
                break
        else:
            pytest.fail("测试邮件队列未能在 100 次内清空")
    finally:
        await database.close()


def message_for(email, purpose="verify"):
    with httpx.Client(base_url=MAILPIT, trust_env=False, timeout=3) as mailbox:
        response = mailbox.get("/api/v1/search", params={"query": f"to:{email}"})
        response.raise_for_status()
        messages = response.json()["messages"]
        subject = "验证邮箱" if purpose == "verify" else "重设密码"
        messages = [m for m in messages if subject in m["Subject"]]
        assert messages, f"未找到 {purpose} 测试邮件"
        return mailbox.get(f"/api/v1/message/{messages[0]['ID']}").json()


def email_token(database_url, email, purpose="verify"):
    asyncio.run(drain_mail(database_url))
    body = message_for(email, purpose)["Text"]
    match = re.search(r"#token=([A-Za-z0-9_-]{43})", body)
    assert match is not None
    return match.group(1)


def login(client, email, password=PASSWORD, device="测试浏览器"):
    response = client.post(
        f"{API}/login",
        json={"email": email, "password": password, "device_name": device},
    )
    assert response.status_code == 200, response.text
    client.headers["x-csrf-token"] = response.json()["csrf_token"]
    return response


def verify(client, token, device="邮件验证浏览器"):
    response = client.post(
        f"{API}/verify-email",
        json={"token": token, "device_name": device},
    )
    if response.status_code == 200:
        client.headers["x-csrf-token"] = response.json()["csrf_token"]
    return response


@pytest.fixture
def account(client, database_url):
    email = address()
    register(client, email)
    token = email_token(database_url, email)
    response = verify(client, token)
    assert response.status_code == 200, response.text
    return email


@contextmanager
def browser(live_server):
    with httpx.Client(base_url=live_server.origin, trust_env=False, timeout=10) as client:
        yield client


def test_real_mail_verification_login_and_secret_storage(client, database_url, live_server):
    email = address()
    register(client, email.upper())
    before = client.post(f"{API}/login", json={"email": email, "password": PASSWORD})
    assert before.status_code == 403
    assert before.json()["error"]["code"] == "email_unverified"
    with psycopg.connect(psycopg_url(database_url)) as connection:
        user_id, encoded = connection.execute(
            "SELECT id, password_hash FROM users WHERE email=%s", (email,)
        ).fetchone()
        assert encoded.startswith("$argon2id$v=19$m=65536,t=3,p=1$")
        cipher = connection.execute(
            "SELECT m.encrypted_message FROM mail_outbox m JOIN email_tokens t ON t.id=m.token_id"
            " WHERE t.user_id=%s",
            (user_id,),
        ).fetchone()[0]
        assert b"#token=" not in cipher and email.encode() not in cipher
    token = email_token(database_url, email)
    verified = verify(client, token)
    assert verified.status_code == 200
    assert (
        client.post(f"{API}/verify-email", json={"token": token}).json()["error"][
            "code"
        ]
        == "invalid_email_token"
    )
    response = verified
    raw = client.cookies.get(COOKIE_NAME)
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "SameSite=lax" in response.headers["set-cookie"]
    assert "Path=/api" in response.headers["set-cookie"]
    assert "password_hash" not in response.text and raw not in response.text
    assert client.get(f"{API}/me").json()["user"]["email"] == email
    with psycopg.connect(psycopg_url(database_url)) as connection:
        stored = connection.execute(
            "SELECT token_hash FROM device_sessions WHERE user_id=%s", (user_id,)
        ).fetchone()[0]
        assert stored == digest(raw) and stored != raw
        assert (
            connection.execute(
                "SELECT encrypted_message FROM mail_outbox m JOIN email_tokens t ON m.token_id=t.id"
                " WHERE t.user_id=%s",
                (user_id,),
            ).fetchone()[0]
            is None
        )
        actions = connection.execute(
            "SELECT action FROM security_events WHERE user_id=%s", (user_id,)
        ).fetchall()
        assert {a[0] for a in actions} >= {
            "registered",
            "email_verified",
            "logged_in_after_email_verification",
        }
    client.get("/api/health")
    logs = live_server.log_path.read_text()
    for secret in (PASSWORD, email, token, raw, cipher.decode()):
        assert secret not in logs


def test_email_verification_token_creates_session_without_password(
    client,
    database_url,
):
    email = address()
    register(client, email)
    token = email_token(database_url, email)

    verified = client.post(
        f"{API}/verify-email",
        json={"token": token, "device_name": "邮件验证浏览器"},
    )

    assert verified.status_code == 200, verified.text
    assert verified.json()["user"]["email"] == email
    assert client.cookies.get(COOKIE_NAME)
    assert client.get(f"{API}/me").status_code == 200


def test_registration_without_email_verification_logs_in_only_new_account(
    client,
    database_url,
    live_server,
):
    with psycopg.connect(
        psycopg_url(database_url),
        autocommit=True,
    ) as connection:
        connection.execute(
            "UPDATE registration_policies "
            "SET require_email_verification=false, version=version+1 "
            "WHERE id=1"
        )
    email = address()

    registered = client.post(
        f"{API}/register",
        json={
            "email": email,
            "password": PASSWORD,
            "device_name": "注册直登浏览器",
        },
    )

    assert registered.status_code == 202, registered.text
    assert registered.json()["verification_required"] is False
    assert registered.json()["session"]["user"]["email"] == email
    assert client.cookies.get(COOKIE_NAME)
    with psycopg.connect(psycopg_url(database_url)) as connection:
        counts = connection.execute(
            """
            SELECT
              (SELECT count(*) FROM email_tokens WHERE user_id=users.id),
              (SELECT count(*) FROM mail_outbox AS mail
               JOIN email_tokens AS token ON token.id=mail.token_id
               WHERE token.user_id=users.id)
            FROM users
            WHERE email=%s
            """,
            (email,),
        ).fetchone()
    assert counts == (0, 0)

    with browser(live_server) as duplicate:
        replayed = duplicate.post(
            f"{API}/register",
            json={"email": email, "password": NEW_PASSWORD},
        )
        assert replayed.status_code == 202
        assert replayed.json()["session"] is None
        assert duplicate.cookies.get(COOKIE_NAME) is None
        assert duplicate.post(
            f"{API}/login",
            json={"email": email, "password": NEW_PASSWORD},
        ).status_code == 401
        assert duplicate.post(
            f"{API}/login",
            json={"email": email, "password": PASSWORD},
        ).status_code == 200


@pytest.mark.parametrize(
    "body",
    [
        {"email": "invalid", "password": PASSWORD},
        {"email": "someone@example.com", "password": "tiny"},
        {"email": "someone@example.com", "password": "x" * 129},
        {"email": "someone@example.com", "password": PASSWORD, "is_admin": True},
    ],
)
def test_registration_validation_does_not_leak_input(client, body):
    response = client.post(f"{API}/register", json=body)
    assert response.status_code == 422
    assert body["password"] not in response.text
    assert response.json()["error"]["saved"] == "not_saved"


def test_duplicate_concurrent_registration_and_resend_are_bounded(
    client, live_server, database_url
):
    email = address()
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(
            pool.map(
                lambda _: httpx.post(
                    live_server.origin + API + "/register",
                    json={"email": email, "password": PASSWORD},
                    timeout=10,
                    trust_env=False,
                ),
                range(6),
            )
        )
    assert all(r.status_code == 202 for r in results), [r.text for r in results]
    assert len({r.text for r in results}) == 1
    # 重复注册不能替换初次密码，冷却内不会发第二封邮件。
    register(client, email, NEW_PASSWORD)
    for _ in range(3):
        assert client.post(f"{API}/resend-verification", json={"email": email}).status_code == 202
    with psycopg.connect(psycopg_url(database_url)) as connection:
        assert (
            connection.execute("SELECT count(*) FROM users WHERE email=%s", (email,)).fetchone()[0]
            == 1
        )
        assert (
            connection.execute(
                "SELECT count(*) FROM email_tokens t JOIN users u ON t.user_id=u.id"
                " WHERE u.email=%s",
                (email,),
            ).fetchone()[0]
            == 1
        )
    token = email_token(database_url, email)
    assert verify(client, token).status_code == 200


def test_expired_replaced_and_cross_purpose_tokens(client, database_url):
    email = address()
    register(client, email)
    old_token = email_token(database_url, email)
    with psycopg.connect(psycopg_url(database_url), autocommit=True) as connection:
        connection.execute(
            "UPDATE email_tokens SET created_at=now()-interval '2 minutes',"
            " expires_at=now()-interval '1 second' WHERE token_hash=%s",
            (digest(old_token),),
        )
    expired = client.post(f"{API}/verify-email", json={"token": old_token})
    assert expired.json()["error"]["code"] == "invalid_email_token"
    client.post(f"{API}/resend-verification", json={"email": email})
    token = email_token(database_url, email)
    assert token != old_token
    assert (
        client.post(
            f"{API}/reset-password", json={"token": token, "new_password": NEW_PASSWORD}
        ).status_code
        == 400
    )
    assert verify(client, token).status_code == 200


def test_csrf_device_isolation_revocation_and_logout(client, account, live_server):
    with browser(live_server) as other, browser(live_server) as stranger:
        second = login(other, account, device="第二台设备").json()
        first = client.get(f"{API}/me").json()
        # 第二个用户通过同样真实注册/邮箱流程创建。
        random_email = address()
        register(stranger, random_email)
        # 未登录不能枚举设备；错误 ID 与他人 ID 不暴露归属。
        assert stranger.get(f"{API}/sessions").status_code == 401
        listing = client.get(f"{API}/sessions").json()
        assert len(listing["items"]) == 2
        assert sum(d["current"] for d in listing["items"]) == 1
        client.headers.pop("x-csrf-token")
        rejected = client.delete(f"{API}/sessions/{second['session_id']}")
        assert rejected.status_code == 403
        assert rejected.json()["error"]["code"] == "invalid_csrf"
        assert other.get(f"{API}/me").status_code == 200
        client.headers["x-csrf-token"] = first["csrf_token"]
        assert client.delete(f"{API}/sessions/{uuid4()}").status_code == 404
        assert client.delete(f"{API}/sessions/{second['session_id']}").status_code == 200
        assert client.delete(f"{API}/sessions/{second['session_id']}").status_code == 200
        assert other.get(f"{API}/me").status_code == 401
        assert (
            other.post(
                f"{API}/change-password",
                json={
                    "current_password": PASSWORD,
                    "new_password": NEW_PASSWORD,
                    "revoke_other_sessions": True,
                },
            ).status_code
            == 401
        )
        raw = client.cookies.get(COOKIE_NAME)
        assert client.post(f"{API}/logout").status_code == 200
        assert client.cookies.get(COOKIE_NAME) is None
        assert (
            client.get(f"{API}/me", headers={"cookie": f"{COOKIE_NAME}={raw}"}).status_code == 401
        )
        assert client.post(f"{API}/logout").status_code == 200


def test_authenticated_account_cannot_revoke_another_users_session(
    client, account, live_server, database_url
):
    with browser(live_server) as stranger:
        email = address()
        register(stranger, email)
        token = email_token(database_url, email)
        sid = verify(stranger, token).json()["session_id"]
        response = client.delete(f"{API}/sessions/{sid}")
        assert response.status_code == 404
        assert stranger.get(f"{API}/me").status_code == 200
        assert all(d["id"] != sid for d in client.get(f"{API}/sessions").json()["items"])


def test_password_reset_is_single_use_and_revokes_every_device(
    client, account, live_server, database_url
):
    with browser(live_server) as other:
        login(other, account)
        found = client.post(f"{API}/forgot-password", json={"email": account})
        missing = client.post(f"{API}/forgot-password", json={"email": address()})
        assert found.status_code == missing.status_code == 202
        assert found.json() == missing.json()
        token = email_token(database_url, account, "reset")
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(
                pool.map(
                    lambda _: httpx.post(
                        live_server.origin + API + "/reset-password",
                        json={"token": token, "new_password": NEW_PASSWORD},
                        timeout=10,
                        trust_env=False,
                    ),
                    range(2),
                )
            )
        assert sorted(r.status_code for r in results) == [200, 400]
        assert client.get(f"{API}/me").status_code == 401
        assert other.get(f"{API}/me").status_code == 401
        wrong = client.post(f"{API}/login", json={"email": account, "password": PASSWORD})
        assert wrong.status_code == 401
        login(client, account, NEW_PASSWORD)


@pytest.mark.parametrize("revoke_other", [False, True])
def test_password_change_rotates_current_cookie_and_honors_device_choice(
    client, account, database_url, live_server, revoke_other
):
    with browser(live_server) as other:
        login(other, account)
        client.post(f"{API}/forgot-password", json={"email": account})
        pending = email_token(database_url, account, "reset")
        previous = client.cookies.get(COOKIE_NAME)
        changed = client.post(
            f"{API}/change-password",
            json={
                "current_password": PASSWORD,
                "new_password": NEW_PASSWORD,
                "revoke_other_sessions": revoke_other,
            },
        )
        assert changed.status_code == 200, changed.text
        assert client.cookies.get(COOKIE_NAME) != previous
        assert client.get(f"{API}/me").status_code == 200
        assert (
            client.get(f"{API}/me", headers={"cookie": f"{COOKIE_NAME}={previous}"}).status_code
            == 401
        )
        assert other.get(f"{API}/me").status_code == (401 if revoke_other else 200)
        assert (
            client.post(
                f"{API}/reset-password", json={"token": pending, "new_password": PASSWORD}
            ).status_code
            == 400
        )


def test_session_expiry_account_status_and_admin_route_cannot_be_bypassed(
    client, account, database_url
):
    with psycopg.connect(psycopg_url(database_url), autocommit=True) as connection:
        connection.execute("UPDATE users SET status='suspended' WHERE email=%s", (account,))
        assert client.get(f"{API}/me").json()["error"]["code"] == "account_suspended"
        assert (
            client.post(f"{API}/login", json={"email": account, "password": PASSWORD}).status_code
            == 403
        )
        connection.execute("UPDATE users SET status='active' WHERE email=%s", (account,))
        connection.execute(
            "UPDATE device_sessions SET expires_at=now()-interval '1 second' WHERE token_hash=%s",
            (digest(client.cookies.get(COOKIE_NAME)),),
        )
        assert client.get(f"{API}/me").status_code == 401
        connection.execute("UPDATE users SET is_admin=true WHERE email=%s", (account,))
        assert (
            client.post(f"{API}/login", json={"email": account, "password": PASSWORD}).json()[
                "error"
            ]["code"]
            == "admin_login_required"
        )


def test_device_keyset_pagination_and_invalid_cursor(client, account, live_server):
    for index in range(4):
        with browser(live_server) as other:
            login(other, account, device=f"设备{index}")
    first = client.get(f"{API}/sessions", params={"limit": 2}).json()
    second = client.get(f"{API}/sessions", params={"limit": 2, **first["next_cursor"]}).json()
    third = client.get(f"{API}/sessions", params={"limit": 2, **second["next_cursor"]}).json()
    ids = [d["id"] for page in (first, second, third) for d in page["items"]]
    assert len(ids) == len(set(ids)) == 5
    assert third["next_cursor"] is None
    assert client.get(f"{API}/sessions", params={"before_id": str(uuid4())}).status_code == 400
    assert client.get(f"{API}/sessions", params={"limit": 101}).status_code == 422


def test_failed_auth_rate_limit_is_durable_and_cannot_use_forwarded_header(
    client, account, database_url
):
    for _ in range(15):
        response = client.post(
            f"{API}/login",
            json={"email": account, "password": NEW_PASSWORD},
            headers={"x-forwarded-for": f"192.0.2.{_ + 1}"},
        )
        if response.status_code == 429:
            break
        assert response.status_code == 401
    response = client.post(f"{API}/login", json={"email": account, "password": PASSWORD})
    assert response.status_code == 429
    assert response.json()["error"]["details"]["retry_after_seconds"] == 600
    # 限流窗口由服务端决定；模拟到期后恢复，错误密码请求没有回滚计数。
    with psycopg.connect(psycopg_url(database_url), autocommit=True) as connection:
        connection.execute("UPDATE auth_rate_buckets SET expires_at=now()-interval '1 second'")
    login(client, account)


def test_mail_survives_new_worker_process(client, database_url):
    email = address()
    # 先清空其他测试产生的待发项目，再让独立进程读取持久化队列。
    asyncio.run(drain_mail(database_url))
    register(client, email)
    result = subprocess.run(
        [sys.executable, "-m", "itime.mail_worker", "--once"],
        cwd=SERVER,
        env={**os.environ, "ITIME_ENVIRONMENT": "test", "ITIME_DATABASE_URL": database_url},
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    )
    assert message_for(email)["Subject"] == "iTime · 验证邮箱"
    assert email not in result.stdout + result.stderr


async def test_smtp_failure_retry_lease_recovery_and_two_workers(client, database_url, settings):
    await drain_mail(database_url)
    email = address()
    register(client, email)
    broken = settings.model_copy(update={"smtp_port": 1, "smtp_timeout_seconds": 1})
    database = Database(settings)
    try:
        assert await deliver_one(database, broken)
        async with database.transaction() as session:
            mail = await session.scalar(select(MailOutbox).where(MailOutbox.finished_at.is_(None)))
            assert mail.error_code == "smtp_unavailable"
            assert mail.attempts == 1 and mail.encrypted_message is not None
            assert mail.available_at > now()
            mail.available_at = now() - timedelta(seconds=1)
            mail.lease_until = now() + timedelta(seconds=30)
            mail.lease_id = uuid4()
            mail_id = mail.id
        assert not await deliver_one(database, settings)
        async with database.transaction() as session:
            mail = await session.get(MailOutbox, mail_id)
            mail.lease_until = now() - timedelta(seconds=1)
        result = await asyncio.gather(
            deliver_one(database, settings), deliver_one(database, settings)
        )
        assert sorted(result) == [False, True]
        async with database.transaction() as session:
            mail = await session.get(MailOutbox, mail_id)
            assert mail.attempts == 2 and mail.finished_at is not None
            assert mail.error_code is None and mail.encrypted_message is None
        assert message_for(email)["Subject"] == "iTime · 验证邮箱"
    finally:
        await database.close()


async def test_mail_expiration_and_wrong_key_never_send_token(client, database_url, settings):
    await drain_mail(database_url)
    email = address()
    register(client, email)
    database = Database(settings)
    try:
        wrong_key = settings.model_copy(update={"encryption_key": Fernet.generate_key().decode()})
        # 使用正式 Settings 重新校验 SecretStr，而不是绕过类型的 model_copy。
        wrong_key = Settings(**wrong_key.model_dump())
        assert await deliver_one(database, wrong_key)
        async with database.transaction() as session:
            mail = await session.scalar(
                select(MailOutbox).where(MailOutbox.error_code == "mail_key_unavailable")
            )
            assert mail.finished_at is None and mail.encrypted_message is not None
            mail_id = mail.id
        async with httpx.AsyncClient(base_url=MAILPIT, trust_env=False) as mailbox:
            response = await mailbox.get("/api/v1/search", params={"query": f"to:{email}"})
            assert response.json()["messages"] == []
        async with database.transaction() as session:
            mail = await session.get(MailOutbox, mail_id)
            mail.available_at = now() - timedelta(seconds=1)
        assert await deliver_one(database, settings)
        assert message_for(email)["Subject"] == "iTime · 验证邮箱"
        second = address()
        register(client, second)
        with psycopg.connect(psycopg_url(database_url), autocommit=True) as connection:
            connection.execute(
                "UPDATE email_tokens SET expires_at=now()-interval '1 second'"
                " WHERE user_id=(SELECT id FROM users WHERE email=%s)",
                (second,),
            )
        assert await deliver_one(database, settings)
        async with httpx.AsyncClient(base_url=MAILPIT, trust_env=False) as mailbox:
            response = await mailbox.get("/api/v1/search", params={"query": f"to:{second}"})
            assert response.json()["messages"] == []
    finally:
        await database.close()


async def test_hash_cancellation_does_not_release_capacity_while_thread_runs(settings):
    security = Security(settings)
    entered = threading.Event()
    release = threading.Event()

    def slow():
        entered.set()
        assert release.wait(timeout=3)
        return "done"

    task = asyncio.create_task(security.compute(slow))
    assert await asyncio.to_thread(entered.wait, 2)
    task.cancel()
    await asyncio.sleep(0.02)
    assert not task.done()
    assert security.slots._value == 1
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert security.slots._value == 2


def test_revocation_serializes_against_concurrent_authenticated_writes(
    client, account, live_server, database_url
):
    with browser(live_server) as other:
        result = login(other, account).json()
        cookie = other.cookies.get(COOKIE_NAME)

        def write(_):
            return httpx.post(
                live_server.origin + "/api/__test/authorized-write",
                headers={
                    "cookie": f"{COOKIE_NAME}={cookie}",
                    "x-csrf-token": result["csrf_token"],
                },
                timeout=10,
                trust_env=False,
            )

        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = [pool.submit(write, i) for i in range(6)]
            revoked = client.delete(f"{API}/sessions/{result['session_id']}")
            assert revoked.status_code == 200
            outcomes = [future.result() for future in futures]
        assert {r.status_code for r in outcomes} <= {200, 401}
        for _ in range(3):
            assert write(_).status_code == 401
        with psycopg.connect(psycopg_url(database_url)) as connection:
            count = connection.execute(
                "SELECT count(*) FROM security_events e JOIN users u ON e.user_id=u.id"
                " WHERE u.email=%s AND e.action='test_write'",
                (account,),
            ).fetchone()[0]
        assert count == sum(r.status_code == 200 for r in outcomes)


async def test_smtp_total_timeout_does_not_hold_a_database_connection(
    client, database_url, settings
):
    await drain_mail(database_url)
    register(client, address())
    database = Database(settings)
    opened = asyncio.Event()
    closed = asyncio.Event()
    waiting_writers = []

    async def stalled(reader, writer):
        waiting_writers.append(writer)
        opened.set()
        try:
            await reader.read()
        finally:
            writer.close()
            await writer.wait_closed()
            closed.set()

    server = await asyncio.start_server(stalled, "127.0.0.1", 0)
    slow = settings.model_copy(
        update={"smtp_port": server.sockets[0].getsockname()[1], "smtp_timeout_seconds": 1}
    )
    try:
        started = asyncio.get_running_loop().time()
        task = asyncio.create_task(deliver_one(database, slow))
        await asyncio.wait_for(opened.wait(), 2)
        assert database.engine.pool.checkedout() == 0
        assert await asyncio.wait_for(task, 2)
        elapsed = asyncio.get_running_loop().time() - started
        assert 0.8 <= elapsed < 2
        async with database.transaction() as session:
            mail = await session.scalar(select(MailOutbox).where(MailOutbox.finished_at.is_(None)))
            assert mail.error_code == "smtp_timeout"
            assert mail.encrypted_message is not None
    finally:
        server.close()
        await server.wait_closed()
        for writer in waiting_writers:
            writer.close()
        await asyncio.wait_for(closed.wait(), 2)
        await database.close()
