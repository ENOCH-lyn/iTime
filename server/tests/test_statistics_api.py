import asyncio
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import psycopg
import pytest

from itime.database import Database
from itime.statistics.service import read_statistics
from tests.conftest import psycopg_url
from tests.test_task_schedules import initialize_settings
from tests.test_tasks import browser, create_account

pytestmark = pytest.mark.integration


def local_utc(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(
        tzinfo=ZoneInfo("Asia/Shanghai")
    ).astimezone(UTC)


def read_direct(settings, owner_id: UUID, *, current_time: datetime) -> dict:
    database = Database(settings)

    async def execute() -> dict:
        try:
            async with database.transaction() as session:
                return await read_statistics(
                    session,
                    owner_id,
                    scope="week",
                    anchor_date=date(2026, 9, 30),
                    current_time=current_time,
                )
        finally:
            await database.close()

    return asyncio.run(execute())


def seed_statistics_facts(database_url: str, owner_id: UUID) -> dict[str, UUID]:
    ids = {
        name: uuid4()
        for name in (
            "completed_task",
            "repeating_task",
            "reopened_task",
            "plan",
            "old_version",
            "current_version",
            "old_block",
            "current_block",
            "completed_occurrence",
            "skipped_occurrence",
            "timer_focus",
            "manual_focus",
            "abandoned_focus",
            "revised_focus",
            "revision",
        )
    }
    with psycopg.connect(psycopg_url(database_url)) as connection:
        connection.execute(
            """
            INSERT INTO tasks (
                id, owner_id, title, status, completed_at, version
            ) VALUES
                (%s, %s, '普通完成', 'completed', %s, 2),
                (%s, %s, '重复任务', 'open', NULL, 3),
                (%s, %s, '已经重开', 'open', NULL, 3)
            """,
            (
                ids["completed_task"],
                owner_id,
                local_utc("2026-09-29T05:00:00"),
                ids["repeating_task"],
                owner_id,
                ids["reopened_task"],
                owner_id,
            ),
        )
        connection.execute(
            """
            INSERT INTO task_occurrence_states (
                id, task_id, owner_id, occurrence_key, original_date,
                kind, effective_date, status, completed_at,
                completed_step_ids, step_count_snapshot, version,
                created_at, updated_at
            ) VALUES
                (
                    %s, %s, %s, '2026-09-30', DATE '2026-09-30',
                    'base', DATE '2026-09-30', 'completed', %s,
                    '[]', 0, 1, %s, %s
                ),
                (
                    %s, %s, %s, '2026-10-01', DATE '2026-10-01',
                    'base', DATE '2026-10-01', 'skipped', NULL,
                    '[]', 0, 1, %s, %s
                )
            """,
            (
                ids["completed_occurrence"],
                ids["repeating_task"],
                owner_id,
                local_utc("2026-09-30T08:00:00"),
                local_utc("2026-09-30T08:00:00"),
                local_utc("2026-09-30T08:00:00"),
                ids["skipped_occurrence"],
                ids["repeating_task"],
                owner_id,
                local_utc("2026-10-01T09:00:00"),
                local_utc("2026-10-01T09:00:00"),
            ),
        )
        connection.execute(
            """
            INSERT INTO plans (
                id, owner_id, week_start_date, time_zone,
                current_version_id, version
            ) VALUES (%s, %s, DATE '2026-09-28', 'Asia/Shanghai', %s, 2)
            """,
            (ids["plan"], owner_id, ids["current_version"]),
        )
        for version_id, version in (
            (ids["old_version"], 1),
            (ids["current_version"], 2),
        ):
            connection.execute(
                """
                INSERT INTO plan_versions (
                    id, plan_id, owner_id, version, source, scope,
                    target_date, density, buffer_minutes,
                    availability_start, availability_end, summary,
                    assumptions, input_revision
                ) VALUES (
                    %s, %s, %s, %s, 'manual', 'week',
                    DATE '2026-09-28', 'balanced', 15,
                    TIME '00:00', TIME '23:59', '', '[]', 0
                )
                """,
                (version_id, ids["plan"], owner_id, version),
            )
        for block_id, version_id, title, starts_at, ends_at in (
            (
                ids["old_block"],
                ids["old_version"],
                "旧版本块",
                local_utc("2026-09-29T10:00:00"),
                local_utc("2026-09-29T12:00:00"),
            ),
            (
                ids["current_block"],
                ids["current_version"],
                "跨活动日块",
                local_utc("2026-09-29T03:30:00"),
                local_utc("2026-09-29T04:30:00"),
            ),
        ):
            connection.execute(
                """
                INSERT INTO scheduled_blocks (
                    id, plan_version_id, owner_id, position, task_id,
                    task_title_snapshot, step_ids, step_titles_snapshot,
                    local_date, local_starts_at, local_ends_at, time_zone,
                    starts_at, ends_at, estimate_source, estimated_minutes
                ) VALUES (
                    %s, %s, %s, 0, %s, %s, '[]', '[]',
                    DATE '2026-09-29', TIME '03:30', TIME '04:30',
                    'Asia/Shanghai', %s, %s, 'user', 60
                )
                """,
                (
                    block_id,
                    version_id,
                    owner_id,
                    ids["completed_task"],
                    title,
                    starts_at,
                    ends_at,
                ),
            )

        timer_start = local_utc("2026-09-29T03:30:00")
        timer_end = local_utc("2026-09-29T04:30:00")
        connection.execute(
            """
            INSERT INTO focus_sessions (
                id, owner_id, source, title_snapshot, step_snapshots,
                mode, status, started_at, ended_at, accumulated_seconds,
                effective_started_at, effective_ended_at,
                effective_duration_seconds, time_zone, version
            ) VALUES (
                %s, %s, 'timer', '暂停跨界', '[]',
                'stopwatch', 'finished', %s, %s, 2400,
                %s, %s, 2400, 'Asia/Shanghai', 5
            )
            """,
            (
                ids["timer_focus"],
                owner_id,
                timer_start,
                timer_end,
                timer_start,
                timer_end,
            ),
        )
        for sequence, kind, occurred_at in (
            (1, "start", local_utc("2026-09-29T03:30:00")),
            (2, "pause", local_utc("2026-09-29T03:50:00")),
            (3, "resume", local_utc("2026-09-29T04:10:00")),
            (4, "finish", local_utc("2026-09-29T04:30:00")),
        ):
            connection.execute(
                """
                INSERT INTO focus_events (
                    id, owner_id, focus_session_id, sequence, kind,
                    occurred_at, source, details
                ) VALUES (%s, %s, %s, %s, %s, %s, 'online', '{}')
                """,
                (
                    uuid4(),
                    owner_id,
                    ids["timer_focus"],
                    sequence,
                    kind,
                    occurred_at,
                ),
            )

        manual_start = local_utc("2026-09-30T03:30:00")
        manual_end = local_utc("2026-09-30T04:30:00")
        connection.execute(
            """
            INSERT INTO focus_sessions (
                id, owner_id, source, title_snapshot, step_snapshots,
                mode, status, started_at, ended_at, accumulated_seconds,
                effective_started_at, effective_ended_at,
                effective_duration_seconds, time_zone, version
            ) VALUES (
                %s, %s, 'manual', '跨界补记', '[]',
                'stopwatch', 'finished', %s, %s, 1801,
                %s, %s, 1801, 'Asia/Shanghai', 1
            )
            """,
            (
                ids["manual_focus"],
                owner_id,
                manual_start,
                manual_end,
                manual_start,
                manual_end,
            ),
        )
        abandoned_start = local_utc("2026-10-01T10:00:00")
        abandoned_end = abandoned_start + timedelta(minutes=10)
        connection.execute(
            """
            INSERT INTO focus_sessions (
                id, owner_id, source, title_snapshot, step_snapshots,
                mode, status, started_at, ended_at, accumulated_seconds,
                effective_started_at, effective_ended_at,
                effective_duration_seconds, time_zone, version
            ) VALUES (
                %s, %s, 'timer', '主动放弃', '[]',
                'stopwatch', 'abandoned', %s, %s, 600,
                %s, %s, 600, 'Asia/Shanghai', 2
            )
            """,
            (
                ids["abandoned_focus"],
                owner_id,
                abandoned_start,
                abandoned_end,
                abandoned_start,
                abandoned_end,
            ),
        )
        for sequence, kind, occurred_at in (
            (1, "start", abandoned_start),
            (2, "abandon", abandoned_end),
        ):
            connection.execute(
                """
                INSERT INTO focus_events (
                    id, owner_id, focus_session_id, sequence, kind,
                    occurred_at, source, details
                ) VALUES (%s, %s, %s, %s, %s, %s, 'online', '{}')
                """,
                (
                    uuid4(),
                    owner_id,
                    ids["abandoned_focus"],
                    sequence,
                    kind,
                    occurred_at,
                ),
            )
        revised_start = local_utc("2026-10-02T09:00:00")
        revised_end = revised_start + timedelta(minutes=30)
        connection.execute(
            """
            INSERT INTO focus_sessions (
                id, owner_id, source, title_snapshot, step_snapshots,
                mode, status, started_at, ended_at, accumulated_seconds,
                effective_started_at, effective_ended_at,
                effective_duration_seconds, time_zone, version
            ) VALUES (
                %s, %s, 'timer', '修订记录', '[]',
                'stopwatch', 'finished', %s, %s, 1800,
                %s, %s, 1200, 'Asia/Shanghai', 3
            )
            """,
            (
                ids["revised_focus"],
                owner_id,
                revised_start,
                revised_end,
                revised_start,
                revised_end,
            ),
        )
        connection.execute(
            """
            INSERT INTO execution_revisions (
                id, owner_id, focus_session_id, session_version,
                before_data, after_data, reason
            ) VALUES (%s, %s, %s, 3, '{}', '{}', '修正暂停时间')
            """,
            (
                ids["revision"],
                owner_id,
                ids["revised_focus"],
            ),
        )
        connection.commit()
    return ids


def test_aggregate_uses_current_plan_and_current_task_outcomes(
    client,
    database_url,
    settings,
):
    _, owner_id_value = create_account(client, database_url)
    initialize_settings(client)
    owner_id = UUID(owner_id_value)
    seed_statistics_facts(database_url, owner_id)

    result = read_direct(
        settings,
        owner_id,
        current_time=datetime(2026, 10, 4, 8, tzinfo=UTC),
    )
    assert result["period_start_date"] == date(2026, 9, 28)
    assert result["period_end_date"] == date(2026, 10, 4)
    assert len(result["days"]) == 7
    assert result["summary"]["planning"] == {
        "block_count": 1,
        "task_count": 1,
        "planned_seconds": 3600,
    }
    assert result["days"][0]["planning"]["planned_seconds"] == 1800
    assert result["days"][1]["planning"]["planned_seconds"] == 1800
    assert result["summary"]["tasks"] == {
        "completed_count": 2,
        "completed_task_count": 1,
        "completed_occurrence_count": 1,
        "skipped_occurrence_count": 1,
    }


def test_focus_segments_statuses_and_proportional_allocation_are_separate(
    client,
    database_url,
    settings,
):
    _, owner_id_value = create_account(client, database_url)
    initialize_settings(client)
    owner_id = UUID(owner_id_value)
    seed_statistics_facts(database_url, owner_id)

    result = read_direct(
        settings,
        owner_id,
        current_time=datetime(2026, 10, 4, 8, tzinfo=UTC),
    )
    focus = result["summary"]["focus"]
    assert focus == {
        "session_count": 4,
        "total_seconds": 6001,
        "finished_count": 3,
        "finished_seconds": 5401,
        "abandoned_count": 1,
        "abandoned_seconds": 600,
        "manual_count": 1,
        "manual_seconds": 1801,
        "revised_count": 1,
        "ongoing_count": 0,
        "ongoing_seconds": 0,
        "proportional_focus_count": 2,
    }
    assert result["days"][0]["focus"]["total_seconds"] == 1200
    assert result["days"][1]["focus"]["total_seconds"] == 2101
    assert sum(
        day["focus"]["total_seconds"] for day in result["days"]
    ) == focus["total_seconds"]
    assert result["days"][3]["focus"]["abandoned_seconds"] == 600
    assert result["days"][4]["focus"]["revised_count"] == 1


def test_http_contract_errors_empty_state_and_owner_isolation(
    client,
    database_url,
    live_server,
):
    endpoint = "/api/v1/statistics"
    assert client.get(
        endpoint,
        params={"scope": "day", "anchor_date": "2026-09-28"},
    ).status_code == 401

    _, owner_id_value = create_account(client, database_url)
    missing = client.get(
        endpoint,
        params={"scope": "day", "anchor_date": "2026-09-28"},
    )
    assert missing.status_code == 409
    assert missing.json()["error"]["code"] == "time_settings_required"
    initialize_settings(client)

    invalid_scope = client.get(
        endpoint,
        params={"scope": "year", "anchor_date": "2026-09-28"},
    )
    assert invalid_scope.status_code == 422
    future = client.get(
        endpoint,
        params={"scope": "day", "anchor_date": "2999-01-01"},
    )
    assert future.status_code == 422
    assert future.json()["error"]["code"] == "future_statistics_range"

    empty = client.get(
        endpoint,
        params={"scope": "day", "anchor_date": "2026-09-28"},
    )
    assert empty.status_code == 200, empty.text
    empty_body = empty.json()
    assert empty_body["methodology_version"] == "statistics_v1"
    assert empty_body["period_start_date"] == "2026-09-28"
    assert empty_body["period_end_date"] == "2026-09-28"
    assert empty_body["data_end_date"] == "2026-09-28"
    assert empty_body["time_zone"] == "Asia/Shanghai"
    assert empty_body["activity_day_start_minute"] == 240
    assert empty_body["calculated_at"]
    assert empty_body["summary"]["planning"]["block_count"] == 0
    assert empty_body["summary"]["tasks"]["completed_count"] == 0
    assert empty_body["summary"]["focus"]["total_seconds"] == 0
    assert len(empty_body["days"]) == 1

    owner_id = UUID(owner_id_value)
    seed_statistics_facts(database_url, owner_id)
    populated = client.get(
        endpoint,
        params={"scope": "week", "anchor_date": "2026-09-30"},
    )
    assert populated.status_code == 200, populated.text
    assert populated.json()["summary"]["planning"]["block_count"] == 1
    assert populated.json()["summary"]["tasks"]["completed_count"] == 2

    with browser(live_server) as other:
        create_account(other, database_url)
        initialize_settings(other)
        isolated = other.get(
            endpoint,
            params={"scope": "week", "anchor_date": "2026-09-30"},
        )
        assert isolated.status_code == 200, isolated.text
        assert isolated.json()["summary"]["planning"]["block_count"] == 0
        assert isolated.json()["summary"]["tasks"]["completed_count"] == 0
        assert isolated.json()["summary"]["focus"]["session_count"] == 0


def test_open_focus_uses_server_time_and_persisted_active_segments(
    client,
    database_url,
    settings,
):
    _, owner_id_value = create_account(client, database_url)
    initialize_settings(client)
    owner_id = UUID(owner_id_value)
    focus_id = uuid4()
    current_time = datetime(2026, 10, 4, 8, tzinfo=UTC)
    started_at = current_time - timedelta(minutes=10)
    paused_at = current_time - timedelta(minutes=6)
    active_started_at = current_time - timedelta(minutes=5)
    with psycopg.connect(psycopg_url(database_url)) as connection:
        connection.execute(
            """
            INSERT INTO focus_sessions (
                id, owner_id, source, title_snapshot, step_snapshots,
                mode, status, started_at, active_started_at,
                accumulated_seconds, time_zone, version
            ) VALUES (
                %s, %s, 'timer', '仍在进行', '[]',
                'stopwatch', 'running', %s, %s, 240, 'Asia/Shanghai', 3
            )
            """,
            (focus_id, owner_id, started_at, active_started_at),
        )
        for sequence, kind, occurred_at in (
            (1, "start", started_at),
            (2, "pause", paused_at),
            (3, "resume", active_started_at),
        ):
            connection.execute(
                """
                INSERT INTO focus_events (
                    id, owner_id, focus_session_id, sequence, kind,
                    occurred_at, source, details
                ) VALUES (%s, %s, %s, %s, %s, %s, 'online', '{}')
                """,
                (uuid4(), owner_id, focus_id, sequence, kind, occurred_at),
            )
        connection.commit()

    result = read_direct(settings, owner_id, current_time=current_time)
    assert result["summary"]["focus"] == {
        "session_count": 1,
        "total_seconds": 540,
        "finished_count": 0,
        "finished_seconds": 0,
        "abandoned_count": 0,
        "abandoned_seconds": 0,
        "manual_count": 0,
        "manual_seconds": 0,
        "revised_count": 0,
        "ongoing_count": 1,
        "ongoing_seconds": 540,
        "proportional_focus_count": 0,
    }
