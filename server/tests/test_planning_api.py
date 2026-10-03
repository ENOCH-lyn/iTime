from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import psycopg
import pytest
from cryptography.fernet import Fernet
from psycopg.errors import CheckViolation

from itime.config import Settings
from tests.conftest import isolated_database, migrate, psycopg_url
from tests.quota_helpers import ensure_quota_policy
from tests.test_calendar_series import browser, create_account, create_series
from tests.test_tasks import create_task

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def cleanup_created_users(database_url):
    with psycopg.connect(psycopg_url(database_url)) as connection:
        existing_ids = {
            row[0] for row in connection.execute("SELECT id FROM users")
        }
    yield
    with psycopg.connect(
        psycopg_url(database_url),
        autocommit=True,
    ) as connection:
        created_ids = [
            row[0]
            for row in connection.execute("SELECT id FROM users")
            if row[0] not in existing_ids
        ]
        for user_id in created_ids:
            connection.execute("DELETE FROM users WHERE id=%s", (user_id,))


def _current_week() -> tuple[date, date]:
    today = datetime.now(UTC).astimezone(ZoneInfo("Asia/Shanghai")).date()
    start = today - timedelta(days=today.weekday())
    return start, start + timedelta(days=6)


def _publish_planning_config(database_url: str) -> None:
    settings = Settings(database_url=database_url, environment="test")
    cipher = Fernet(
        (settings.ai_encryption_key or settings.encryption_key).get_secret_value()
    )
    with psycopg.connect(
        psycopg_url(database_url),
        autocommit=True,
    ) as connection:
        version = connection.execute(
            """
            UPDATE ai_config_capabilities
            SET next_version=next_version + 1, updated_at=now()
            WHERE capability='planning'
            RETURNING next_version - 1
            """
        ).fetchone()[0]
        config_id = uuid4()
        connection.execute(
            """
            INSERT INTO ai_config_versions (
                id, capability, version, endpoint_url, model,
                api_key_ciphertext, timeout_seconds, max_output_tokens,
                price_currency, input_price_nanos_per_million_tokens,
                cached_input_price_nanos_per_million_tokens,
                output_price_nanos_per_million_tokens, status,
                last_test_id, last_test_started_at, last_test_completed_at,
                published_at
            ) VALUES (
                %s, 'planning', %s, 'https://ai.example.com/v1',
                'planning-api-test', %s, 10, 512,
                'CNY', 1000000000, 500000000, 2000000000,
                'published', %s, now(), now(), now()
            )
            """,
            (
                config_id,
                version,
                cipher.encrypt(b"sk-planning-api-test"),
                uuid4(),
            ),
        )
        connection.execute(
            """
            UPDATE ai_config_capabilities
            SET current_version_id=%s, updated_at=now()
            WHERE capability='planning'
            """,
            (config_id,),
        )


def _generation_payload(target_date: date, operation_id: UUID | None = None) -> dict:
    return {
        "operation_id": str(operation_id or uuid4()),
        "scope": "day",
        "target_date": target_date.isoformat(),
        "density": "balanced",
        "buffer_minutes": 0,
        "availability_start": "08:00:00",
        "availability_end": "22:00:00",
        "task_ids": [],
        "instruction": "先安排当前任务",
    }


def _manual_payload(
    week_start: date,
    task_id: str,
    *,
    operation_id: UUID | None = None,
    expected_plan_version: int = 0,
    starts_at: str = "09:00:00",
    ends_at: str = "10:00:00",
    step_ids: list[str] | None = None,
    unscheduled_items: list[dict] | None = None,
) -> dict:
    return {
        "operation_id": str(operation_id or uuid4()),
        "week_start_date": week_start.isoformat(),
        "expected_plan_version": expected_plan_version,
        "density": "balanced",
        "buffer_minutes": 0,
        "availability_start": "08:00:00",
        "availability_end": "22:00:00",
        "blocks": [
            {
                "task_id": task_id,
                "occurrence_key": None,
                "local_date": week_start.isoformat(),
                "starts_at": starts_at,
                "ends_at": ends_at,
                "step_ids": step_ids or [],
            }
        ],
        "unscheduled_items": unscheduled_items or [],
    }


def _seed_plan(
    database_url: str,
    owner_id: UUID,
    task_id: UUID,
    week_start: date,
) -> tuple[UUID, UUID, UUID]:
    plan_id = uuid4()
    first_version_id = uuid4()
    current_version_id = uuid4()
    first_block_id = uuid4()
    current_block_id = uuid4()
    local_start = datetime.combine(
        week_start,
        time(11),
        ZoneInfo("Asia/Shanghai"),
    )
    current_start = datetime.combine(
        week_start + timedelta(days=1),
        time(14),
        ZoneInfo("Asia/Shanghai"),
    )
    with psycopg.connect(psycopg_url(database_url)) as connection:
        revision = connection.execute(
            """
            SELECT revision
            FROM planning_input_revisions
            WHERE owner_id=%s
            """,
            (owner_id,),
        ).fetchone()[0]
        connection.execute("SET CONSTRAINTS ALL DEFERRED")
        connection.execute(
            """
            INSERT INTO plans (
                id, owner_id, week_start_date, time_zone,
                current_version_id, version
            ) VALUES (%s, %s, %s, 'Asia/Shanghai', %s, 2)
            """,
            (plan_id, owner_id, week_start, current_version_id),
        )
        connection.execute(
            """
            INSERT INTO plan_versions (
                id, plan_id, owner_id, version, source, scope,
                target_date, density, buffer_minutes,
                availability_start, availability_end, summary,
                assumptions, input_revision
            ) VALUES
                (
                    %s, %s, %s, 1, 'manual', 'week',
                    %s, 'balanced', 0, '08:00', '22:00',
                    '第一版', '[]'::jsonb, %s
                ),
                (
                    %s, %s, %s, 2, 'manual', 'week',
                    %s, 'balanced', 0, '08:00', '22:00',
                    '当前版', '[]'::jsonb, %s
                )
            """,
            (
                first_version_id,
                plan_id,
                owner_id,
                week_start,
                revision,
                current_version_id,
                plan_id,
                owner_id,
                week_start,
                revision,
            ),
        )
        connection.execute(
            """
            INSERT INTO scheduled_blocks (
                id, plan_version_id, owner_id, position, task_id,
                task_title_snapshot, step_ids, step_titles_snapshot,
                local_date, local_starts_at, local_ends_at, time_zone,
                starts_at, ends_at, estimate_source, estimated_minutes
            ) VALUES
                (
                    %s, %s, %s, 0, %s, '规划 API 任务',
                    '[]'::jsonb, '[]'::jsonb, %s, '11:00', '12:00',
                    'Asia/Shanghai', %s, %s, 'user', 60
                ),
                (
                    %s, %s, %s, 0, %s, '规划 API 任务',
                    '[]'::jsonb, '[]'::jsonb, %s, '14:00', '15:00',
                    'Asia/Shanghai', %s, %s, 'user', 60
                )
            """,
            (
                first_block_id,
                first_version_id,
                owner_id,
                task_id,
                week_start,
                local_start,
                local_start + timedelta(hours=1),
                current_block_id,
                current_version_id,
                owner_id,
                task_id,
                week_start + timedelta(days=1),
                current_start,
                current_start + timedelta(hours=1),
            ),
        )
        connection.commit()
    return plan_id, first_version_id, current_version_id


def test_planning_generation_requires_write_auth_ai_access_and_replays(
    client,
    database_url,
):
    target_date, _ = _current_week()
    assert client.post(
        "/api/v1/planning/generations",
        json=_generation_payload(target_date),
    ).status_code == 401

    _, owner_text = create_account(client, database_url)
    owner_id = UUID(owner_text)
    create_task(
        client,
        title="规划 API 任务",
        estimated_minutes=60,
        steps=[],
        links=[],
        labels=[],
    )
    ensure_quota_policy(database_url)
    _publish_planning_config(database_url)

    operation_id = uuid4()
    payload = _generation_payload(target_date, operation_id)
    csrf = client.headers.pop("x-csrf-token")
    denied = client.post("/api/v1/planning/generations", json=payload)
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "invalid_csrf"
    client.headers["x-csrf-token"] = csrf

    created = client.post("/api/v1/planning/generations", json=payload)
    assert created.status_code == 202, created.text
    result = created.json()
    assert result["operation_id"] == str(operation_id)
    assert result["job"]["capability"] == "planning"
    assert result["job"]["status"] == "queued"

    replay = client.post("/api/v1/planning/generations", json=payload)
    assert replay.status_code == 202
    assert replay.json() == result

    with psycopg.connect(
        psycopg_url(database_url),
        autocommit=True,
    ) as connection:
        counts = connection.execute(
            """
            SELECT
                (SELECT count(*) FROM ai_jobs WHERE owner_id=%s),
                (SELECT count(*) FROM planning_input_snapshots WHERE owner_id=%s),
                (SELECT count(*) FROM ai_quota_reservations WHERE owner_id=%s)
            """,
            (owner_id, owner_id, owner_id),
        ).fetchone()
        assert counts == (1, 1, 1)
        connection.execute(
            "UPDATE users SET ai_access='frozen' WHERE id=%s",
            (owner_id,),
        )

    frozen = client.post(
        "/api/v1/planning/generations",
        json=_generation_payload(target_date),
    )
    assert frozen.status_code == 403
    assert frozen.json()["error"]["code"] == "ai_access_frozen"


def test_plan_queries_history_and_current_calendar_blocks_are_owner_scoped(
    client,
    database_url,
    live_server,
):
    week_start, week_end = _current_week()
    _, owner_text = create_account(client, database_url)
    task = create_task(
        client,
        title="规划 API 任务",
        estimated_minutes=60,
        steps=[],
        links=[],
        labels=[],
    )
    plan_id, first_version_id, current_version_id = _seed_plan(
        database_url,
        UUID(owner_text),
        UUID(task["id"]),
        week_start,
    )

    listed = client.get(
        "/api/v1/plans",
        params={"start": week_start.isoformat(), "end": week_end.isoformat()},
    )
    assert listed.status_code == 200, listed.text
    payload = listed.json()
    assert payload["start"] == week_start.isoformat()
    assert payload["end"] == week_end.isoformat()
    assert [item["id"] for item in payload["items"]] == [str(plan_id)]
    assert payload["items"][0]["version"] == 2
    assert payload["items"][0]["current_version"]["id"] == str(current_version_id)

    current = client.get(f"/api/v1/plans/{plan_id}")
    assert current.status_code == 200
    assert current.json()["plan"]["current_version"]["summary"] == "当前版"

    historical = client.get(
        f"/api/v1/plans/{plan_id}",
        params={"version": 1},
    )
    assert historical.status_code == 200
    assert historical.json()["plan"]["current_version"]["id"] == str(first_version_id)
    assert historical.json()["plan"]["current_version"]["summary"] == "第一版"

    history = client.get(f"/api/v1/plans/{plan_id}/history")
    assert history.status_code == 200
    assert [item["version"] for item in history.json()["items"]] == [2, 1]

    blocks = client.get(
        "/api/v1/planning/blocks",
        params={
            "start": week_start.isoformat(),
            "end": (week_end + timedelta(days=1)).isoformat(),
        },
    )
    assert blocks.status_code == 200, blocks.text
    assert blocks.json()["time_zone"] == "Asia/Shanghai"
    assert len(blocks.json()["items"]) == 1
    block = blocks.json()["items"][0]
    assert block["plan_id"] == str(plan_id)
    assert block["plan_version_id"] == str(current_version_id)
    assert block["plan_version"] == 2
    assert block["source"] == "plan"
    assert block["local_date"] == (week_start + timedelta(days=1)).isoformat()

    with browser(live_server) as other:
        create_account(other, database_url)
        assert other.get(f"/api/v1/plans/{plan_id}").status_code == 404
        assert other.get(f"/api/v1/plans/{plan_id}/history").status_code == 404
        other_blocks = other.get(
            "/api/v1/planning/blocks",
            params={
                "start": week_start.isoformat(),
                "end": (week_end + timedelta(days=1)).isoformat(),
            },
        )
        assert other_blocks.status_code == 200
        assert other_blocks.json()["items"] == []


def test_plan_block_range_uses_current_display_time_zone(client, database_url):
    week_start, _ = _current_week()
    _, owner_text = create_account(client, database_url)
    owner_id = UUID(owner_text)
    task = create_task(
        client,
        title="跨时区显示任务",
        estimated_minutes=60,
        steps=[],
        links=[],
        labels=[],
    )
    _seed_plan(database_url, owner_id, UUID(task["id"]), week_start)
    with psycopg.connect(
        psycopg_url(database_url),
        autocommit=True,
    ) as connection:
        connection.execute(
            """
            UPDATE user_time_settings
            SET time_zone='America/Los_Angeles',
                version=version + 1,
                updated_at=now()
            WHERE owner_id=%s
            """,
            (owner_id,),
        )

    response = client.get(
        "/api/v1/planning/blocks",
        params={
            "start": week_start.isoformat(),
            "end": (week_start + timedelta(days=1)).isoformat(),
        },
    )

    assert response.status_code == 200, response.text
    assert len(response.json()["items"]) == 1
    assert response.json()["items"][0]["local_date"] == (
        week_start + timedelta(days=1)
    ).isoformat()
    assert response.json()["time_zone"] == "America/Los_Angeles"


def test_manual_plan_versions_are_complete_idempotent_and_emit_changes(
    client,
    database_url,
):
    week_start, week_end = _current_week()
    _, owner_text = create_account(client, database_url)
    owner_id = UUID(owner_text)
    planned = create_task(
        client,
        title="服务端权威标题",
        estimated_minutes=60,
        steps=[{"title": "服务端权威步骤"}],
        links=[],
        labels=[],
    )
    unscheduled = create_task(
        client,
        title="本周未排任务",
        estimated_minutes=60,
        steps=[],
        links=[],
        labels=[],
    )
    operation_id = uuid4()
    payload = _manual_payload(
        week_start,
        planned["id"],
        operation_id=operation_id,
        step_ids=[planned["steps"][0]["id"]],
        unscheduled_items=[
            {
                "task_id": unscheduled["id"],
                "occurrence_key": None,
                "remaining_minutes": 60,
                "reason": "no_capacity",
                "explanation": "本周暂不安排",
            }
        ],
    )

    created = client.post("/api/v1/plans/versions", json=payload)
    assert created.status_code == 201, created.text
    result = created.json()
    assert result["operation_id"] == str(operation_id)
    assert result["plan"]["version"] == 1
    version = result["plan"]["current_version"]
    assert version["source"] == "manual"
    assert version["scope"] == "week"
    assert version["target_date"] == week_start.isoformat()
    assert version["summary"] == "手工周计划"
    assert version["assumptions"] == []
    assert version["blocks"][0]["task_title_snapshot"] == "服务端权威标题"
    assert version["blocks"][0]["step_titles_snapshot"] == ["服务端权威步骤"]
    assert version["blocks"][0]["starts_at"].endswith("Z")
    assert version["blocks"][0]["estimate_source"] == "user"
    assert version["blocks"][0]["estimated_minutes"] == 60

    replay = client.post("/api/v1/plans/versions", json=payload)
    assert replay.status_code == 201
    assert replay.json() == result
    changed = {
        **payload,
        "blocks": [{**payload["blocks"][0], "starts_at": "10:00:00", "ends_at": "11:00:00"}],
    }
    reused = client.post("/api/v1/plans/versions", json=changed)
    assert reused.status_code == 409
    assert reused.json()["error"]["code"] == "operation_id_reused"

    second_payload = _manual_payload(
        week_start,
        planned["id"],
        expected_plan_version=1,
        starts_at="10:00:00",
        ends_at="11:00:00",
        step_ids=[planned["steps"][0]["id"]],
        unscheduled_items=payload["unscheduled_items"],
    )
    second = client.post("/api/v1/plans/versions", json=second_payload)
    assert second.status_code == 201, second.text
    assert second.json()["plan"]["version"] == 2

    stale = client.post(
        "/api/v1/plans/versions",
        json={
            **second_payload,
            "operation_id": str(uuid4()),
            "expected_plan_version": 1,
        },
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "plan_version_conflict"
    assert stale.json()["error"]["details"]["current_version"] == 2

    listed = client.get(
        "/api/v1/plans",
        params={"start": week_start.isoformat(), "end": week_end.isoformat()},
    )
    assert listed.status_code == 200
    assert listed.json()["items"][0]["version"] == 2

    with psycopg.connect(psycopg_url(database_url)) as connection:
        events = connection.execute(
            """
            SELECT action, entity_id, entity_version
            FROM change_events
            WHERE owner_id=%s AND entity_type='plan'
            ORDER BY sequence
            """,
            (owner_id,),
        ).fetchall()
    assert events == [
        ("created", UUID(result["plan"]["id"]), 1),
        ("updated", UUID(result["plan"]["id"]), 2),
    ]


def test_manual_plan_uses_submitted_duration_for_task_without_estimate(
    client,
    database_url,
):
    week_start, _ = _current_week()
    create_account(client, database_url)
    task = create_task(
        client,
        title="尚未估时的任务",
        estimated_minutes=None,
        steps=[],
        links=[],
        labels=[],
    )
    payload = _manual_payload(
        week_start,
        task["id"],
        starts_at="09:00:00",
        ends_at="09:45:00",
    )

    response = client.post("/api/v1/plans/versions", json=payload)

    assert response.status_code == 201, response.text
    block = response.json()["plan"]["current_version"]["blocks"][0]
    assert block["estimate_source"] == "user"
    assert block["estimated_minutes"] == 45


def test_manual_plan_rejects_client_authority_cross_owner_and_hard_conflicts(
    client,
    database_url,
    live_server,
):
    week_start, _ = _current_week()
    _, owner_text = create_account(client, database_url)
    owner_id = UUID(owner_text)
    first = create_task(
        client,
        title="本账号任务",
        estimated_minutes=60,
        steps=[{"title": "本账号步骤"}],
        links=[],
        labels=[],
    )
    second = create_task(
        client,
        title="另一本账号任务",
        estimated_minutes=60,
        steps=[{"title": "不能跨任务引用"}],
        links=[],
        labels=[],
    )

    forged = _manual_payload(
        week_start,
        first["id"],
        unscheduled_items=[
            {
                "task_id": second["id"],
                "occurrence_key": None,
                "remaining_minutes": 60,
                "reason": "no_capacity",
                "explanation": "本周暂不安排",
            }
        ],
    )
    forged["blocks"][0].update(
        {
            "task_title_snapshot": "客户端伪造标题",
            "starts_at_utc": "2026-10-05T01:00:00Z",
            "estimate_source": "user",
        }
    )
    invalid_shape = client.post("/api/v1/plans/versions", json=forged)
    assert invalid_shape.status_code == 422

    wrong_step = _manual_payload(
        week_start,
        first["id"],
        step_ids=[second["steps"][0]["id"]],
        unscheduled_items=[
            {
                "task_id": second["id"],
                "occurrence_key": None,
                "remaining_minutes": 60,
                "reason": "no_capacity",
                "explanation": "本周暂不安排",
            }
        ],
    )
    step_response = client.post("/api/v1/plans/versions", json=wrong_step)
    assert step_response.status_code == 400
    assert step_response.json()["error"]["code"] == "unknown_plan_step"

    wrong_occurrence = _manual_payload(
        week_start,
        first["id"],
        unscheduled_items=[
            {
                "task_id": second["id"],
                "occurrence_key": None,
                "remaining_minutes": 60,
                "reason": "no_capacity",
                "explanation": "本周暂不安排",
            }
        ],
    )
    wrong_occurrence["blocks"][0]["occurrence_key"] = "forged-occurrence"
    occurrence_response = client.post(
        "/api/v1/plans/versions",
        json=wrong_occurrence,
    )
    assert occurrence_response.status_code == 400
    assert occurrence_response.json()["error"]["code"] == "unknown_plan_candidate"

    too_many = _manual_payload(
        week_start,
        first["id"],
        unscheduled_items=[
            {
                "task_id": second["id"],
                "occurrence_key": None,
                "remaining_minutes": 60,
                "reason": "no_capacity",
                "explanation": "本周暂不安排",
            }
        ],
    )
    too_many["blocks"] = [too_many["blocks"][0] for _ in range(101)]
    too_many_response = client.post("/api/v1/plans/versions", json=too_many)
    assert too_many_response.status_code == 413
    assert too_many_response.json()["error"]["code"] == "request_too_large"

    create_series(
        client,
        name="固定占用",
        start_date=week_start.isoformat(),
        starts_at="09:00:00",
        ends_at="10:00:00",
    )
    fixed_conflict = client.post(
        "/api/v1/plans/versions",
        json=_manual_payload(
            week_start,
            first["id"],
            unscheduled_items=[
                {
                    "task_id": second["id"],
                    "occurrence_key": None,
                    "remaining_minutes": 60,
                    "reason": "no_capacity",
                    "explanation": "本周暂不安排",
                }
            ],
        ),
    )
    assert fixed_conflict.status_code == 400
    assert fixed_conflict.json()["error"]["code"] == "plan_fixed_conflict"

    with browser(live_server) as other:
        create_account(other, database_url)
        foreign = create_task(
            other,
            title="其他账号任务",
            estimated_minutes=60,
            steps=[],
            links=[],
            labels=[],
        )
    cross_owner = client.post(
        "/api/v1/plans/versions",
        json=_manual_payload(week_start, foreign["id"]),
    )
    assert cross_owner.status_code == 404
    assert cross_owner.json()["error"]["code"] == "task_not_found"

    with psycopg.connect(psycopg_url(database_url)) as connection:
        assert connection.execute(
            "SELECT count(*) FROM plans WHERE owner_id=%s",
            (owner_id,),
        ).fetchone()[0] == 0
        assert connection.execute(
            """
            SELECT count(*)
            FROM change_events
            WHERE owner_id=%s AND entity_type='plan'
            """,
            (owner_id,),
        ).fetchone()[0] == 0


@pytest.mark.parametrize(
    ("path", "params"),
    [
        (
            "/api/v1/plans",
            {"start": "2026-10-06", "end": "2026-10-11"},
        ),
        (
            "/api/v1/plans",
            {"start": "2026-10-05", "end": "2027-10-10"},
        ),
        (
            "/api/v1/planning/blocks",
            {"start": "2026-10-05", "end": "2026-10-05"},
        ),
        (
            "/api/v1/planning/blocks",
            {"start": "2026-10-05", "end": "2027-01-07"},
        ),
    ],
)
def test_plan_query_ranges_are_bounded(client, database_url, path, params):
    create_account(client, database_url)
    response = client.get(path, params=params)
    assert response.status_code == 422


def test_planning_downgrade_removes_plan_change_events_and_restores_constraint(
    admin_url,
):
    with isolated_database(admin_url) as database_url:
        owner_id = uuid4()
        with psycopg.connect(psycopg_url(database_url)) as connection:
            connection.execute(
                """
                INSERT INTO users (id, email, password_hash, email_verified_at)
                VALUES (%s, %s, 'test-password-hash', now())
                """,
                (owner_id, f"{owner_id}@example.com"),
            )
            connection.execute(
                """
                INSERT INTO change_events (
                    owner_id, entity_type, entity_id, action, entity_version
                ) VALUES (%s, 'plan', %s, 'created', 1)
                """,
                (owner_id, uuid4()),
            )
            connection.commit()

        migrate(database_url, "downgrade", "0021_ai_user_quota")
        with psycopg.connect(psycopg_url(database_url)) as connection:
            assert connection.execute(
                "SELECT count(*) FROM change_events WHERE entity_type='plan'"
            ).fetchone()[0] == 0
            constraint = connection.execute(
                """
                SELECT pg_get_constraintdef(oid)
                FROM pg_constraint
                WHERE conname='ck_change_events_entity_type'
                """
            ).fetchone()[0]
            assert "'plan'" not in constraint
            with pytest.raises(CheckViolation):
                connection.execute(
                    """
                    INSERT INTO change_events (
                        owner_id, entity_type, entity_id, action, entity_version
                    ) VALUES (%s, 'plan', %s, 'created', 1)
                    """,
                    (owner_id, uuid4()),
                )

        migrate(database_url, "upgrade", "head")
        migrate(database_url, "check")
