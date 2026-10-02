import asyncio
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import psycopg
import pyotp
import pytest

from itime.admin.operations import AdminOperationError, create_admin, reset_admin_totp
from itime.admin.security import ADMIN_COOKIE_NAME
from itime.auth.security import Security, digest
from itime.config import Settings
from itime.database import Database
from tests.conftest import SERVER, psycopg_url

pytestmark = pytest.mark.integration
PASSWORD = "Valid-admin-password-123"
NEW_PASSWORD = "New-admin-password-456"
API = "/api/v1/admin/auth"


@pytest.fixture(autouse=True)
def clean_rate_buckets(database_url):
    with psycopg.connect(psycopg_url(database_url), autocommit=True) as connection:
        connection.execute("DELETE FROM auth_rate_buckets")


def address() -> str:
    return f"admin-{uuid4().hex}@example.com"


async def create_admin_account(database_url, email, password=PASSWORD):
    settings = Settings(database_url=database_url, environment="test")
    database = Database(settings)
    try:
        return await create_admin(database, Security(settings), settings, email, password)
    finally:
        await database.close()


def token_from(link: str) -> str:
    match = re.search(r"#token=([A-Za-z0-9_-]{43})$", link)
    assert match is not None
    return match.group(1)


def setup_admin(client, database_url, email=None):
    email = email or address()
    link = asyncio.run(create_admin_account(database_url, email))
    token = token_from(link)
    started = client.post(f"{API}/setup", json={"token": token, "password": PASSWORD})
    assert started.status_code == 200, started.text
    secret = started.json()["secret"]
    confirmed = client.post(
        f"{API}/setup/confirm",
        json={
            "token": token,
            "password": PASSWORD,
            "code": pyotp.TOTP(secret).now(),
            "device_name": "管理测试浏览器",
        },
    )
    assert confirmed.status_code == 200, confirmed.text
    client.headers["x-csrf-token"] = confirmed.json()["csrf_token"]
    return email, secret, confirmed.json()["recovery_codes"]


def login(client, email, method, code, password=PASSWORD):
    response = client.post(
        f"{API}/login",
        json={
            "email": email,
            "password": password,
            "method": method,
            "code": code,
            "device_name": "第二管理浏览器",
        },
    )
    if response.status_code == 200:
        client.headers["x-csrf-token"] = response.json()["csrf_token"]
    return response


def next_totp(secret: str, offset: int = 1) -> str:
    return pyotp.TOTP(secret).at(datetime.now(UTC), counter_offset=offset)


def test_cli_create_setup_and_secret_storage(client, database_url, live_server):
    email = address()
    environment = {
        **os.environ,
        "ITIME_ENVIRONMENT": "test",
        "ITIME_DATABASE_URL": database_url,
        "ITIME_PUBLIC_ORIGIN": live_server.origin,
    }
    created = subprocess.run(
        [
            sys.executable, "-m", "itime.admin.cli", "create",
            "--email", email, "--password-stdin",
        ],
        cwd=SERVER,
        env=environment,
        input=PASSWORD + "\n",
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    )
    assert PASSWORD not in created.stdout + created.stderr
    token = token_from(created.stdout.strip().splitlines()[-1])
    started = client.post(f"{API}/setup", json={"token": token, "password": PASSWORD})
    assert started.status_code == 200
    secret = started.json()["secret"]
    assert secret in started.json()["provisioning_uri"]
    confirmed = client.post(
        f"{API}/setup/confirm",
        json={
            "token": token,
            "password": PASSWORD,
            "code": pyotp.TOTP(secret).now(),
            "device_name": "Mac 管理浏览器",
        },
    )
    assert confirmed.status_code == 200
    codes = confirmed.json()["recovery_codes"]
    assert len(codes) == 10 and len(set(codes)) == 10
    raw_session = client.cookies.get(ADMIN_COOKIE_NAME)
    assert "HttpOnly" in confirmed.headers["set-cookie"]
    assert "SameSite=strict" in confirmed.headers["set-cookie"]
    assert "Path=/api/v1/admin" in confirmed.headers["set-cookie"]
    assert confirmed.json()["auth_method"] == "totp"
    assert "recovery_codes" not in client.get(f"{API}/me").text
    with psycopg.connect(psycopg_url(database_url)) as connection:
        ciphertext, token_hash = connection.execute(
            "SELECT c.totp_secret_ciphertext, s.token_hash"
            " FROM admin_credentials c JOIN admin_sessions s ON s.user_id=c.user_id"
            " JOIN users u ON u.id=c.user_id WHERE u.email=%s",
            (email,),
        ).fetchone()
        stored_codes = [
            row[0] for row in connection.execute(
                "SELECT code_hash FROM admin_recovery_codes r"
                " JOIN users u ON u.id=r.user_id WHERE u.email=%s",
                (email,),
            ).fetchall()
        ]
        assert secret.encode() not in ciphertext
        assert token_hash == digest(raw_session) and token_hash != raw_session
        assert all(code not in stored_codes for code in codes)
        assert connection.execute(
            "SELECT count(*) FROM admin_enrollments e"
            " JOIN users u ON u.id=e.user_id WHERE u.email=%s",
            (email,),
        ).fetchone()[0] == 0
    logs = live_server.log_path.read_text()
    for private in (PASSWORD, secret, raw_session, *codes):
        assert private not in logs


def test_setup_failures_do_not_disclose_or_consume_link(client, database_url):
    email = address()
    token = token_from(asyncio.run(create_admin_account(database_url, email)))
    wrong = client.post(f"{API}/setup", json={"token": token, "password": NEW_PASSWORD})
    assert wrong.status_code == 401
    assert wrong.json()["error"]["code"] == "invalid_admin_credentials"
    started = client.post(f"{API}/setup", json={"token": token, "password": PASSWORD})
    secret = started.json()["secret"]
    bad_code = client.post(
        f"{API}/setup/confirm",
        json={
            "token": token, "password": PASSWORD,
            "code": "000000", "device_name": "测试",
        },
    )
    assert bad_code.json()["error"]["code"] == "invalid_admin_code"
    assert client.post(
        f"{API}/setup/confirm",
        json={
            "token": token, "password": PASSWORD,
            "code": pyotp.TOTP(secret).now(), "device_name": "测试",
        },
    ).status_code == 200
    reused = client.post(f"{API}/setup", json={"token": token, "password": PASSWORD})
    assert reused.status_code == 400

    regular = address()
    client.post("/api/v1/auth/register", json={"email": regular, "password": PASSWORD})
    response = login(client, regular, "totp", "123456")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_admin_credentials"


def test_totp_replay_allows_only_one_concurrent_login(client, database_url, live_server):
    email, secret, _ = setup_admin(client, database_url)
    client.post(f"{API}/logout")
    code = next_totp(secret)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(
                lambda _: httpx.post(
                    live_server.origin + API + "/login",
                    json={
                        "email": email, "password": PASSWORD, "method": "totp",
                        "code": code, "device_name": "并发浏览器",
                    },
                    timeout=10,
                    trust_env=False,
                ),
                range(2),
            )
        )
    assert sorted(response.status_code for response in responses) == [200, 401]
    rejected = next(response for response in responses if response.status_code == 401)
    assert rejected.json()["error"]["code"] == "invalid_admin_code"


def test_recovery_code_is_single_use_and_rotation_invalidates_old_codes(
    client, database_url, live_server
):
    email, secret, codes = setup_admin(client, database_url)
    client.post(f"{API}/logout")
    code = codes[0]
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(
                lambda _: httpx.post(
                    live_server.origin + API + "/login",
                    json={
                        "email": email, "password": PASSWORD, "method": "recovery",
                        "code": code, "device_name": "恢复浏览器",
                    },
                    timeout=10,
                    trust_env=False,
                ),
                range(2),
            )
        )
    assert sorted(response.status_code for response in responses) == [200, 401]
    successful = next(response for response in responses if response.status_code == 200)
    client.cookies.set(
        ADMIN_COOKIE_NAME,
        successful.cookies[ADMIN_COOKIE_NAME],
        path="/api/v1/admin",
    )
    client.headers["x-csrf-token"] = successful.json()["csrf_token"]
    rotated = client.post(f"{API}/recovery-codes", json={"code": next_totp(secret)})
    assert rotated.status_code == 200, rotated.text
    assert set(rotated.json()["recovery_codes"]).isdisjoint(codes)
    client.post(f"{API}/logout")
    assert login(client, email, "recovery", codes[1]).status_code == 401
    assert login(client, email, "recovery", rotated.json()["recovery_codes"][0]).status_code == 200


def test_csrf_logout_expiry_and_suspension(client, database_url):
    email, _, _ = setup_admin(client, database_url)
    csrf = client.headers.pop("x-csrf-token")
    rejected = client.post(f"{API}/logout")
    assert rejected.status_code == 403
    client.headers["x-csrf-token"] = csrf
    assert client.post(f"{API}/logout").status_code == 200
    assert client.get(f"{API}/me").status_code == 401
    assert client.post(f"{API}/logout").status_code == 200

    _, secret, _ = setup_admin(client, database_url, address())
    with psycopg.connect(psycopg_url(database_url), autocommit=True) as connection:
        connection.execute(
            "UPDATE admin_sessions SET expires_at=now()-interval '1 second'"
            " WHERE token_hash=%s",
            (digest(client.cookies.get(ADMIN_COOKIE_NAME)),),
        )
    assert client.get(f"{API}/me").status_code == 401
    email2, secret2, _ = setup_admin(client, database_url, address())
    with psycopg.connect(psycopg_url(database_url), autocommit=True) as connection:
        connection.execute("UPDATE users SET status='suspended' WHERE email=%s", (email2,))
    assert client.get(f"{API}/me").status_code == 401
    assert secret and secret2


def test_server_totp_reset_revokes_sessions_and_replaces_setup_link(client, database_url):
    email, _, old_codes = setup_admin(client, database_url)
    old_cookie = client.cookies.get(ADMIN_COOKIE_NAME)
    settings = Settings(database_url=database_url, environment="test")
    database = Database(settings)
    try:
        new_link = asyncio.run(reset_admin_totp(database, settings, email))
    finally:
        asyncio.run(database.close())
    assert client.get(f"{API}/me").status_code == 401
    assert old_cookie not in new_link
    assert login(client, email, "recovery", old_codes[0]).status_code == 403
    token = token_from(new_link)
    started = client.post(f"{API}/setup", json={"token": token, "password": PASSWORD})
    assert started.status_code == 200
    secret = started.json()["secret"]
    assert client.post(
        f"{API}/setup/confirm",
        json={
            "token": token, "password": PASSWORD,
            "code": pyotp.TOTP(secret).now(), "device_name": "重置后浏览器",
        },
    ).status_code == 200


def test_admin_password_change_revokes_other_sessions_and_old_reset_link(
    client, database_url, live_server
):
    email, _, codes = setup_admin(client, database_url)
    with httpx.Client(base_url=live_server.origin, trust_env=False) as other:
        assert login(other, email, "recovery", codes[0]).status_code == 200
        client.post("/api/v1/auth/forgot-password", json={"email": email})
        with psycopg.connect(psycopg_url(database_url)) as connection:
            reset_hash = connection.execute(
                "SELECT t.token_hash FROM email_tokens t JOIN users u ON u.id=t.user_id"
                " WHERE u.email=%s AND t.purpose='reset' AND t.used_at IS NULL",
                (email,),
            ).fetchone()[0]
        changed = client.post(
            f"{API}/change-password",
            json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
        )
        assert changed.status_code == 200, changed.text
        client.headers["x-csrf-token"] = changed.json()["csrf_token"]
        assert other.get(f"{API}/me").status_code == 401
        assert client.get(f"{API}/me").status_code == 200
        with psycopg.connect(psycopg_url(database_url)) as connection:
            assert connection.execute(
                "SELECT used_at IS NOT NULL FROM email_tokens WHERE token_hash=%s",
                (reset_hash,),
            ).fetchone()[0]
        client.post(f"{API}/logout")
        assert login(client, email, "recovery", codes[1], PASSWORD).status_code == 401
        assert login(client, email, "recovery", codes[1], NEW_PASSWORD).status_code == 200


def test_public_password_reset_revokes_admin_session(client, database_url):
    email, _, _ = setup_admin(client, database_url)
    client.post("/api/v1/auth/forgot-password", json={"email": email})
    with psycopg.connect(psycopg_url(database_url)) as connection:
        token_hash = connection.execute(
            "SELECT t.token_hash FROM email_tokens t JOIN users u ON u.id=t.user_id"
            " WHERE u.email=%s AND t.purpose='reset' AND t.used_at IS NULL",
            (email,),
        ).fetchone()[0]
    # The plaintext token is intentionally unavailable from storage; replace it only in this
    # isolated test to exercise the public reset transaction against an administrator session.
    raw = "A" * 43
    with psycopg.connect(psycopg_url(database_url), autocommit=True) as connection:
        connection.execute(
            "UPDATE email_tokens SET token_hash=%s WHERE token_hash=%s",
            (digest(raw), token_hash),
        )
    reset = client.post(
        "/api/v1/auth/reset-password",
        json={"token": raw, "new_password": NEW_PASSWORD},
    )
    assert reset.status_code == 200, reset.text
    assert client.get(f"{API}/me").status_code == 401


def test_create_admin_refuses_to_promote_existing_account(client, database_url):
    email = address()
    assert client.post(
        "/api/v1/auth/register", json={"email": email, "password": PASSWORD}
    ).status_code == 202
    with pytest.raises(AdminOperationError, match="不会覆盖或提升"):
        asyncio.run(create_admin_account(database_url, email))
    with psycopg.connect(psycopg_url(database_url)) as connection:
        is_admin = connection.execute(
            "SELECT is_admin FROM users WHERE email=%s", (email,)
        ).fetchone()[0]
        assert not is_admin
