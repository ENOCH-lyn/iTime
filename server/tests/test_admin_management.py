import asyncio
import re
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from uuid import uuid4

import httpx
import psycopg
import pyotp
import pytest

from itime.admin.operations import create_admin
from itime.auth.security import Security, digest
from itime.config import Settings
from itime.database import Database
from tests.conftest import psycopg_url

pytestmark = pytest.mark.integration
PASSWORD = "Valid-admin-password-123"
USER_PASSWORD = "Valid-user-password-123"
AUTH = "/api/v1/auth"
ADMIN_AUTH = "/api/v1/admin/auth"
ADMIN = "/api/v1/admin"


@pytest.fixture(autouse=True)
def reset_registration_state(database_url):
    with psycopg.connect(psycopg_url(database_url), autocommit=True) as connection:
        connection.execute("DELETE FROM registration_invitations")
        connection.execute(
            "UPDATE registration_policies"
            " SET mode='open', version=version+1, updated_at=now(),"
            " updated_by_user_id=NULL WHERE id=1"
        )
    yield
    with psycopg.connect(psycopg_url(database_url), autocommit=True) as connection:
        connection.execute("DELETE FROM registration_invitations")
        connection.execute(
            "UPDATE registration_policies"
            " SET mode='open', version=version+1, updated_at=now(),"
            " updated_by_user_id=NULL WHERE id=1"
        )


def address(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}@example.com"


async def create_admin_account(database_url: str, email: str) -> str:
    settings = Settings(database_url=database_url, environment="test")
    database = Database(settings)
    try:
        return await create_admin(
            database,
            Security(settings),
            settings,
            email,
            PASSWORD,
        )
    finally:
        await database.close()


def setup_admin(client: httpx.Client, database_url: str) -> dict:
    email = address("manager")
    link = asyncio.run(create_admin_account(database_url, email))
    token = re.search(r"#token=([A-Za-z0-9_-]{43})$", link).group(1)
    started = client.post(
        f"{ADMIN_AUTH}/setup",
        json={"token": token, "password": PASSWORD},
    )
    secret = started.json()["secret"]
    confirmed = client.post(
        f"{ADMIN_AUTH}/setup/confirm",
        json={
            "token": token,
            "password": PASSWORD,
            "code": pyotp.TOTP(secret).now(),
            "device_name": "用户管理测试",
        },
    )
    assert confirmed.status_code == 200, confirmed.text
    client.headers["x-csrf-token"] = confirmed.json()["csrf_token"]
    return confirmed.json()


@contextmanager
def browser(live_server):
    with httpx.Client(base_url=live_server.origin, timeout=10, trust_env=False) as client:
        yield client


def invitation_token(invite_url: str) -> str:
    match = re.search(r"#invite=([A-Za-z0-9_-]{43})$", invite_url)
    assert match is not None
    return match.group(1)


def test_invitation_policy_is_email_bound_single_account_and_recoverable(
    client, database_url, live_server
):
    setup_admin(client, database_url)
    public_policy = client.get(f"{AUTH}/policy").json()
    assert public_policy["registration_mode"] == "open"
    policy = client.get(f"{ADMIN}/registration-policy").json()
    switched = client.put(
        f"{ADMIN}/registration-policy",
        json={"mode": "invite", "expected_version": policy["version"]},
    )
    assert switched.status_code == 200
    assert switched.json()["mode"] == "invite"
    stale = client.put(
        f"{ADMIN}/registration-policy",
        json={"mode": "open", "expected_version": policy["version"]},
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "registration_policy_version_conflict"
    assert client.get(f"{AUTH}/policy").json()["registration_mode"] == "invite"

    invited_email = address("invited")
    created = client.post(
        f"{ADMIN}/invitations",
        json={"email": invited_email.upper(), "expires_in_hours": 24},
    )
    assert created.status_code == 201, created.text
    assert created.json()["email"] == invited_email
    raw = invitation_token(created.json()["invite_url"])
    with psycopg.connect(psycopg_url(database_url)) as connection:
        stored = connection.execute(
            "SELECT token_hash FROM registration_invitations WHERE id=%s",
            (created.json()["id"],),
        ).fetchone()[0]
    assert stored == digest(raw) and stored != raw

    with browser(live_server) as visitor:
        missing = visitor.post(
            f"{AUTH}/register",
            json={"email": invited_email, "password": USER_PASSWORD},
        )
        assert missing.status_code == 403
        wrong_email = visitor.post(
            f"{AUTH}/register",
            json={
                "email": address("other"),
                "password": USER_PASSWORD,
                "invitation_token": raw,
            },
        )
        assert wrong_email.status_code == 403

    payload = {
        "email": invited_email,
        "password": USER_PASSWORD,
        "invitation_token": raw,
    }
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(
                lambda _: httpx.post(
                    live_server.origin + AUTH + "/register",
                    json=payload,
                    timeout=10,
                    trust_env=False,
                ),
                range(2),
            )
        )
    assert [response.status_code for response in responses] == [202, 202]
    assert len({response.text for response in responses}) == 1
    with psycopg.connect(psycopg_url(database_url)) as connection:
        user_id = connection.execute(
            "SELECT id FROM users WHERE email=%s",
            (invited_email,),
        ).fetchone()[0]
        consumed = connection.execute(
            "SELECT consumed_at IS NOT NULL, consumed_by_user_id"
            " FROM registration_invitations WHERE id=%s",
            (created.json()["id"],),
        ).fetchone()
        assert consumed == (True, user_id)

    consumed_page = client.get(
        f"{ADMIN}/invitations", params={"status": "consumed", "limit": 1}
    ).json()
    assert consumed_page["items"][0]["status"] == "consumed"
    assert "invite_url" not in consumed_page["items"][0]

    revoked_email = address("revoked")
    revoke_target = client.post(
        f"{ADMIN}/invitations",
        json={"email": revoked_email, "expires_in_hours": 1},
    ).json()
    revoked_raw = invitation_token(revoke_target["invite_url"])
    revoked = client.delete(f"{ADMIN}/invitations/{revoke_target['id']}")
    assert revoked.json()["status"] == "revoked"
    with browser(live_server) as visitor:
        rejected = visitor.post(
            f"{AUTH}/register",
            json={
                "email": revoked_email,
                "password": USER_PASSWORD,
                "invitation_token": revoked_raw,
            },
        )
        assert rejected.status_code == 403

    current = client.get(f"{ADMIN}/registration-policy").json()
    opened = client.put(
        f"{ADMIN}/registration-policy",
        json={"mode": "open", "expected_version": current["version"]},
    )
    assert opened.status_code == 200
    with browser(live_server) as visitor:
        assert visitor.post(
            f"{AUTH}/register",
            json={"email": address("open"), "password": USER_PASSWORD},
        ).status_code == 202
        assert visitor.post(
            f"{AUTH}/login",
            json={
                "email": invited_email,
                "password": USER_PASSWORD,
                "invitation_token": raw,
            },
        ).status_code == 422
    assert raw not in live_server.log_path.read_text()


def test_user_status_and_ai_access_are_separate_versioned_controls(
    client, database_url, live_server
):
    admin = setup_admin(client, database_url)
    user_email = address("managed")
    with browser(live_server) as user_client:
        registered = user_client.post(
            f"{AUTH}/register",
            json={"email": user_email, "password": USER_PASSWORD},
        )
        assert registered.status_code == 202
        with psycopg.connect(psycopg_url(database_url), autocommit=True) as connection:
            user_id = connection.execute(
                "UPDATE users SET email_verified_at=now() WHERE email=%s RETURNING id",
                (user_email,),
            ).fetchone()[0]
        logged_in = user_client.post(
            f"{AUTH}/login",
            json={"email": user_email, "password": USER_PASSWORD},
        )
        assert logged_in.status_code == 200

        listing = client.get(
            f"{ADMIN}/users",
            params={"search": user_email.upper(), "role": "user"},
        )
        assert listing.status_code == 200
        item = listing.json()["items"][0]
        assert item["id"] == str(user_id)
        assert item["status"] == "active"
        assert item["ai_access"] == "allowed"
        assert item["version"] == 1

        csrf = client.headers.pop("x-csrf-token")
        denied = client.put(
            f"{ADMIN}/users/{user_id}/ai-access",
            json={"ai_access": "frozen", "expected_version": 1},
        )
        assert denied.status_code == 403
        assert denied.json()["error"]["code"] == "invalid_csrf"
        client.headers["x-csrf-token"] = csrf

        frozen = client.put(
            f"{ADMIN}/users/{user_id}/ai-access",
            json={"ai_access": "frozen", "expected_version": 1},
        )
        assert frozen.status_code == 200
        assert frozen.json()["ai_access"] == "frozen"
        assert frozen.json()["version"] == 2
        current_session = user_client.get(f"{AUTH}/me")
        assert current_session.status_code == 200
        assert current_session.json()["user"]["ai_access"] == "frozen"

        stale = client.put(
            f"{ADMIN}/users/{user_id}/status",
            json={"status": "suspended", "expected_version": 1},
        )
        assert stale.status_code == 409
        assert stale.json()["error"]["details"]["current_version"] == 2
        suspended = client.put(
            f"{ADMIN}/users/{user_id}/status",
            json={"status": "suspended", "expected_version": 2},
        )
        assert suspended.status_code == 200
        assert suspended.json()["status"] == "suspended"
        assert suspended.json()["version"] == 3
        assert user_client.get(f"{AUTH}/me").status_code == 401
        blocked_login = user_client.post(
            f"{AUTH}/login",
            json={"email": user_email, "password": USER_PASSWORD},
        )
        assert blocked_login.status_code == 403
        assert blocked_login.json()["error"]["code"] == "account_suspended"

        resumed = client.put(
            f"{ADMIN}/users/{user_id}/status",
            json={"status": "active", "expected_version": 3},
        )
        assert resumed.status_code == 200
        assert resumed.json()["version"] == 4
        assert user_client.post(
            f"{AUTH}/login",
            json={"email": user_email, "password": USER_PASSWORD},
        ).status_code == 200

    protected = client.put(
        f"{ADMIN}/users/{admin['admin']['id']}/status",
        json={"status": "suspended", "expected_version": 1},
    )
    assert protected.status_code == 409
    assert protected.json()["error"]["code"] == "admin_account_protected"
    assert client.put(
        f"{ADMIN}/users/{uuid4()}/status",
        json={"status": "suspended", "expected_version": 1},
    ).status_code == 404

    with psycopg.connect(psycopg_url(database_url)) as connection:
        actions = {
            row[0]
            for row in connection.execute(
                "SELECT action FROM security_events"
                " WHERE user_id=%s AND target_id=%s",
                (admin["admin"]["id"], str(user_id)),
            ).fetchall()
        }
        assert actions >= {"user_ai_frozen", "user_suspended", "user_active"}
