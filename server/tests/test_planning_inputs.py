import asyncio
from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
from threading import Event
from uuid import uuid4

import httpx
import psycopg
import pytest

from itime.config import Settings
from itime.database import Database
from itime.main import create_app  # noqa: F401
from itime.planning.schemas import PlanningRequestFields
from itime.planning.snapshot_service import (
    build_planning_snapshot,
    lock_planning_revision,
    planning_context,
)
from tests.conftest import psycopg_url
from tests.test_calendar_additions import addition_payload
from tests.test_calendar_exceptions import exception_payload
from tests.test_calendar_series import (
    browser,
    create_account,
    create_series,
    recurrence,
)
from tests.test_calendar_subscriptions import (
    FakeFetcher,
    create_subscription,
    daily_ics,
    result,
    run_worker,
)
from tests.test_task_schedules import occurrence_status_payload, schedule_payload
from tests.test_tasks import create_task

pytestmark = pytest.mark.integration


def context_params(
    *,
    scope: str = "week",
    target_date: str = "2026-10-05",
    task_ids: list[str] | None = None,
    **changes,
) -> list[tuple[str, str]]:
    params = [
        ("scope", scope),
        ("date", target_date),
    ]
    params.extend((key, str(value)) for key, value in changes.items())
    params.extend(("task_id", task_id) for task_id in task_ids or [])
    return params


def get_context(client: httpx.Client, **changes) -> httpx.Response:
    return client.get(
        "/api/v1/planning/context",
        params=context_params(**changes),
    )


def test_context_requires_auth_and_time_settings(client, database_url, live_server):
    assert get_context(client).status_code == 401

    from tests.test_tasks import create_account as create_task_account

    create_task_account(client, database_url)
    missing = get_context(client)
    assert missing.status_code == 409
    assert missing.json()["error"]["code"] == "time_settings_required"

    with browser(live_server) as initialized:
        create_account(initialized, database_url)
        response = get_context(initialized, scope="day", target_date="2026-10-05")
        assert response.status_code == 200, response.text
        context = response.json()["context"]
        assert context["scope"] == "day"
        assert context["target_date"] == "2026-10-05"
        assert context["range_start_date"] == "2026-10-05"
        assert context["range_end_date"] == "2026-10-05"
        assert context["week_start_date"] == "2026-10-05"
        assert context["week_end_date"] == "2026-10-11"
        assert context["time_zone"] == "Asia/Shanghai"
        assert context["defaults"] == {
            "density": "balanced",
            "buffer_minutes": 15,
            "availability_start": "08:00:00",
            "availability_end": "22:00:00",
        }
        assert context["task_candidates"] == []
        assert context["fixed_occurrences"] == []
        assert context["current_plan"] is None
        assert len(context["content_sha256"]) == 64
        int(context["content_sha256"], 16)


def test_context_includes_no_due_tasks_full_steps_and_open_recurrences(
    client,
    database_url,
):
    _, owner_id = create_account(client, database_url)
    no_due = create_task(
        client,
        title="无截止任务",
        estimated_minutes=None,
        steps=[{"title": "第一步"}, {"title": "第二步"}, {"title": "第三步"}],
        links=[],
        labels=[],
    )
    repeating = create_task(
        client,
        title="重复复习",
        estimated_minutes=45,
        steps=[{"title": "读笔记"}, {"title": "做练习"}],
        links=[],
        labels=[],
    )
    scheduled = client.put(
        f"/api/v1/tasks/{repeating['id']}/schedule",
        json=schedule_payload(
            due_kind="datetime",
            due_date="2026-10-05",
            due_time="17:00:00",
            recurrence=recurrence("daily", count=3),
        ),
    )
    assert scheduled.status_code == 200, scheduled.text
    task_version = scheduled.json()["task"]["version"]
    occurrence_key = "2026-10-05T17:00:00"
    progress = client.put(
        f"/api/v1/tasks/{repeating['id']}/occurrences/status",
        json=occurrence_status_payload(
            occurrence_key,
            expected_task_version=task_version,
            status="open",
            completed_step_ids=[repeating["steps"][0]["id"]],
        ),
    )
    assert progress.status_code == 200, progress.text

    completed = create_task(client, title="已完成", steps=[], links=[], labels=[])
    completion = client.put(
        f"/api/v1/tasks/{completed['id']}/completion",
        json={
            "operation_id": str(uuid4()),
            "expected_version": completed["version"],
            "completed": True,
        },
    )
    assert completion.status_code == 200
    archived = create_task(client, title="已归档", steps=[], links=[], labels=[])
    archive = client.put(
        f"/api/v1/tasks/{archived['id']}/archive",
        json={
            "operation_id": str(uuid4()),
            "expected_version": archived["version"],
            "archived": True,
        },
    )
    assert archive.status_code == 200
    trashed = create_task(client, title="回收站", steps=[], links=[], labels=[])
    trash = client.request(
        "DELETE",
        f"/api/v1/tasks/{trashed['id']}",
        json={
            "operation_id": str(uuid4()),
            "expected_version": trashed["version"],
        },
    )
    assert trash.status_code == 200

    response = get_context(client, target_date="2026-10-11")
    assert response.status_code == 200, response.text
    context = response.json()["context"]
    assert context["week_start_date"] == "2026-10-05"
    assert context["week_end_date"] == "2026-10-11"
    candidates = context["task_candidates"]
    by_title: dict[str, list[dict]] = {}
    for candidate in candidates:
        by_title.setdefault(candidate["title"], []).append(candidate)

    assert set(by_title) == {"无截止任务", "重复复习"}
    no_due_candidate = by_title["无截止任务"][0]
    assert no_due_candidate["task_id"] == no_due["id"]
    assert no_due_candidate["occurrence_key"] is None
    assert no_due_candidate["due_kind"] == "none"
    assert [step["title"] for step in no_due_candidate["steps"]] == [
        "第一步",
        "第二步",
        "第三步",
    ]
    assert [step["position"] for step in no_due_candidate["steps"]] == [0, 1, 2]

    repeat_candidates = by_title["重复复习"]
    assert len(repeat_candidates) == 3
    assert len({item["candidate_key"] for item in repeat_candidates}) == 3
    first = next(
        item for item in repeat_candidates if item["occurrence_key"] == occurrence_key
    )
    assert first["step_progress"] == {"total": 2, "completed": 1}
    assert [step["completed"] for step in first["steps"]] == [True, False]
    assert all(item["status"] == "open" for item in repeat_candidates)

    with psycopg.connect(psycopg_url(database_url)) as connection:
        assert connection.execute(
            "SELECT count(*) FROM ai_jobs WHERE owner_id=%s",
            (owner_id,),
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM planning_input_snapshots WHERE owner_id=%s",
            (owner_id,),
        ).fetchone()[0] == 0


def test_context_uses_effective_busy_calendar_occurrences(client, database_url):
    create_account(client, database_url)
    series = create_series(
        client,
        name="课程",
        start_date="2026-10-05",
        starts_at="09:00:00",
        ends_at="10:00:00",
        recurrence=recurrence("daily", count=3),
    )["series"]
    free = create_series(
        client,
        name="空闲标记",
        start_date="2026-10-05",
        starts_at="13:00:00",
        ends_at="14:00:00",
        busy=False,
    )
    assert free["series"]["busy"] is False
    cancelled = client.post(
        f"/api/v1/calendar/series/{series['id']}/exceptions",
        json=exception_payload(
            expected_series_version=series["version"],
            occurrence_key="2026-10-06T09:00:00",
            action="cancelled",
        ),
    )
    assert cancelled.status_code == 201, cancelled.text
    moved = client.post(
        f"/api/v1/calendar/series/{series['id']}/exceptions",
        json=exception_payload(
            expected_series_version=cancelled.json()["series"]["version"],
            occurrence_key="2026-10-07T09:00:00",
            action="moved",
            moved_start_date="2026-10-08",
            moved_starts_at="11:00:00",
            moved_ends_at="12:00:00",
            moved_end_day_offset=0,
        ),
    )
    assert moved.status_code == 201, moved.text
    makeup = client.post(
        f"/api/v1/calendar/series/{series['id']}/additions",
        json=addition_payload(
            expected_series_version=moved.json()["series"]["version"],
            source_occurrence_key="2026-10-06T09:00:00",
            local_start_date="2026-10-06",
            starts_at="15:00:00",
            ends_at="16:00:00",
        ),
    )
    assert makeup.status_code == 201, makeup.text

    response = get_context(client)
    assert response.status_code == 200, response.text
    fixed = response.json()["context"]["fixed_occurrences"]
    assert {item["name"] for item in fixed} == {"课程"}
    assert all(item["busy"] is True and item["status"] == "scheduled" for item in fixed)
    assert not any(item["occurrence_key"] == "2026-10-06T09:00:00" for item in fixed)
    assert any(
        item["occurrence_key"] == "2026-10-07T09:00:00"
        and item["local_start_date"] == "2026-10-08"
        and item["local_start_time"] == "11:00:00"
        for item in fixed
    )
    assert any(item["occurrence_type"] == "addition" for item in fixed)


def test_context_includes_subscription_occurrences(client, database_url):
    create_account(client, database_url, time_zone="UTC")
    subscription = create_subscription(client, name="规划订阅")
    with psycopg.connect(
        psycopg_url(database_url),
        autocommit=True,
    ) as connection:
        connection.execute(
            """
            UPDATE calendar_subscriptions
            SET next_refresh_at = CASE
                WHEN source_id=%s THEN now() - interval '1 day'
                ELSE now() + interval '1 day'
            END
            WHERE refresh_status='queued'
            """,
            (subscription["id"],),
        )
    today = datetime.now(UTC).date()
    fetcher = FakeFetcher([result(daily_ics("订阅课程", today))])
    assert asyncio.run(run_worker(database_url, fetcher))

    response = get_context(
        client,
        scope="day",
        target_date=today.isoformat(),
    )
    assert response.status_code == 200, response.text
    subscriptions = [
        item
        for item in response.json()["context"]["fixed_occurrences"]
        if item["source_type"] == "subscription"
    ]
    assert len(subscriptions) == 1
    assert subscriptions[0]["name"] == "订阅课程"
    assert subscriptions[0]["subscription_event_id"] is not None
    assert subscriptions[0]["start_at"].endswith("Z")


def test_context_excludes_trashed_calendar_series_and_subscription(
    client,
    database_url,
):
    create_account(client, database_url, time_zone="UTC")
    series = create_series(
        client,
        name="将回收的固定安排",
        start_date="2026-10-05",
        starts_at="09:00:00",
        ends_at="10:00:00",
    )["series"]
    subscription = create_subscription(client, name="将回收的订阅")
    with psycopg.connect(
        psycopg_url(database_url),
        autocommit=True,
    ) as connection:
        connection.execute(
            """
            UPDATE calendar_subscriptions
            SET next_refresh_at = CASE
                WHEN source_id=%s THEN now() - interval '1 day'
                ELSE now() + interval '1 day'
            END
            WHERE refresh_status='queued'
            """,
            (subscription["id"],),
        )
    assert asyncio.run(
        run_worker(
            database_url,
            FakeFetcher(
                [
                    result(
                        daily_ics(
                            "将回收的订阅安排",
                            date(2026, 10, 5),
                        )
                    )
                ]
            ),
        )
    )
    before = get_context(
        client,
        scope="day",
        target_date="2026-10-05",
    ).json()["context"]["fixed_occurrences"]
    assert {
        item["name"]
        for item in before
    } == {
        "将回收的固定安排",
        "将回收的订阅安排",
    }

    deleted_series = client.request(
        "DELETE",
        f"/api/v1/calendar/series/{series['id']}",
        json={
            "operation_id": str(uuid4()),
            "expected_version": series["version"],
        },
    )
    assert deleted_series.status_code == 200
    current_subscription = client.get(
        f"/api/v1/calendar/subscriptions/{subscription['id']}"
    ).json()["subscription"]
    deleted_source = client.request(
        "DELETE",
        f"/api/v1/calendar/subscriptions/{subscription['id']}",
        json={
            "operation_id": str(uuid4()),
            "expected_version": current_subscription["version"],
        },
    )
    assert deleted_source.status_code == 200

    after = get_context(
        client,
        scope="day",
        target_date="2026-10-05",
    ).json()["context"]["fixed_occurrences"]
    assert {
        item["name"]
        for item in after
    }.isdisjoint({
        "将回收的固定安排",
        "将回收的订阅安排",
    })


def test_context_rejects_cross_owner_selection_and_more_than_100_candidates(
    client,
    database_url,
    live_server,
):
    _, owner_id = create_account(client, database_url)
    with browser(live_server) as other:
        create_account(other, database_url)
        foreign_task = create_task(other, title="其他账号任务", steps=[], links=[], labels=[])
    hidden = get_context(client, task_ids=[foreign_task["id"]])
    assert hidden.status_code == 404
    assert hidden.json()["error"]["code"] == "task_not_found"

    with psycopg.connect(psycopg_url(database_url)) as connection:
        connection.cursor().executemany(
            """
            INSERT INTO tasks (id, owner_id, title)
            VALUES (%s, %s, %s)
            """,
            [
                (uuid4(), owner_id, f"候选 {index:03d}")
                for index in range(101)
            ],
        )
        connection.commit()

    too_many = get_context(client)
    assert too_many.status_code == 422
    assert too_many.json()["error"]["code"] == "planning_candidates_too_large"


def test_context_validates_target_date_range(client, database_url):
    create_account(client, database_url)
    today = date.today()
    too_old = get_context(
        client,
        scope="day",
        target_date=(today - timedelta(days=32)).isoformat(),
    )
    assert too_old.status_code == 422
    assert too_old.json()["error"]["code"] == "planning_date_out_of_range"
    too_new = get_context(
        client,
        scope="day",
        target_date=(today + timedelta(days=367)).isoformat(),
    )
    assert too_new.status_code == 422
    assert too_new.json()["error"]["code"] == "planning_date_out_of_range"


@pytest.mark.asyncio
async def test_shared_revision_lock_produces_complete_old_or_new_context(database_url):
    owner_id = uuid4()
    task_id = uuid4()
    with psycopg.connect(psycopg_url(database_url)) as connection:
        connection.execute(
            """
            INSERT INTO users (id, email, password_hash, email_verified_at)
            VALUES (%s, %s, 'test-password-hash', now())
            """,
            (owner_id, f"planning-lock-{owner_id}@example.com"),
        )
        connection.execute(
            """
            INSERT INTO user_time_settings (owner_id, time_zone)
            VALUES (%s, 'Asia/Shanghai')
            """,
            (owner_id,),
        )
        connection.execute(
            """
            INSERT INTO tasks (id, owner_id, title, estimated_minutes, estimate_source)
            VALUES (%s, %s, '旧标题', 30, 'user')
            """,
            (task_id, owner_id),
        )
        connection.commit()

    settings = Settings(
        environment="test",
        database_url=database_url,
        db_pool_size=2,
        db_max_overflow=0,
    )
    database = Database(settings)
    request = PlanningRequestFields(scope="day", target_date=date(2026, 10, 5))
    writer_started = Event()

    def update_task() -> None:
        with psycopg.connect(psycopg_url(database_url)) as connection:
            writer_started.set()
            connection.execute(
                "UPDATE tasks SET title='新标题', updated_at=now() WHERE id=%s",
                (task_id,),
            )
            connection.commit()

    try:
        async with database.transaction() as session:
            locked = await lock_planning_revision(session, owner_id, write=False)
            old_revision = locked.revision
            writer = asyncio.create_task(asyncio.to_thread(update_task))
            assert await asyncio.to_thread(writer_started.wait, 2)
            await asyncio.sleep(0.1)
            assert not writer.done()
            old_context = await planning_context(session, owner_id, request)
            assert old_context["revision"] == old_revision
            assert old_context["task_candidates"][0]["title"] == "旧标题"

        await asyncio.wait_for(writer, timeout=3)
        async with database.transaction() as session:
            new_context = await planning_context(session, owner_id, request)
        assert new_context["revision"] > old_revision
        assert new_context["task_candidates"][0]["title"] == "新标题"
        assert new_context["content_sha256"] != old_context["content_sha256"]
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_exclusive_revision_lock_blocks_shared_lock(database_url):
    owner_id = uuid4()
    with psycopg.connect(psycopg_url(database_url)) as connection:
        connection.execute(
            """
            INSERT INTO users (id, email, password_hash, email_verified_at)
            VALUES (%s, %s, 'test-password-hash', now())
            """,
            (owner_id, f"planning-exclusive-{owner_id}@example.com"),
        )
        connection.execute(
            """
            INSERT INTO user_time_settings (owner_id, time_zone)
            VALUES (%s, 'Asia/Shanghai')
            """,
            (owner_id,),
        )
        connection.commit()

    database = Database(
        Settings(
            environment="test",
            database_url=database_url,
            db_pool_size=2,
            db_max_overflow=0,
        )
    )
    reader_started = Event()

    def shared_reader() -> int:
        with psycopg.connect(psycopg_url(database_url)) as connection:
            reader_started.set()
            return connection.execute(
                """
                SELECT revision
                FROM planning_input_revisions
                WHERE owner_id=%s
                FOR SHARE
                """,
                (owner_id,),
            ).fetchone()[0]

    try:
        async with database.transaction() as session:
            revision = await lock_planning_revision(session, owner_id, write=True)
            reader = asyncio.create_task(asyncio.to_thread(shared_reader))
            assert await asyncio.to_thread(reader_started.wait, 2)
            await asyncio.sleep(0.1)
            assert not reader.done()
        assert await asyncio.wait_for(reader, timeout=3) == revision.revision
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_snapshot_persists_same_canonical_context_hash(database_url):
    owner_id = uuid4()
    task_id = uuid4()
    plan_id = uuid4()
    plan_version_id = uuid4()
    block_id = uuid4()
    with psycopg.connect(psycopg_url(database_url)) as connection:
        connection.execute("SET CONSTRAINTS ALL DEFERRED")
        connection.execute(
            """
            INSERT INTO users (id, email, password_hash, email_verified_at)
            VALUES (%s, %s, 'test-password-hash', now())
            """,
            (owner_id, f"planning-snapshot-{owner_id}@example.com"),
        )
        connection.execute(
            """
            INSERT INTO user_time_settings (owner_id, time_zone)
            VALUES (%s, 'America/New_York')
            """,
            (owner_id,),
        )
        connection.execute(
            """
            INSERT INTO tasks (id, owner_id, title, estimated_minutes, estimate_source)
            VALUES (%s, %s, 'DST 周任务', 60, 'user')
            """,
            (task_id, owner_id),
        )
        connection.execute(
            """
            INSERT INTO plans (
                id, owner_id, week_start_date, time_zone,
                current_version_id, version
            ) VALUES (
                %s, %s, DATE '2026-10-26', 'America/New_York', %s, 1
            )
            """,
            (plan_id, owner_id, plan_version_id),
        )
        connection.execute(
            """
            INSERT INTO plan_versions (
                id, plan_id, owner_id, version, source, scope,
                target_date, density, buffer_minutes,
                availability_start, availability_end,
                summary, assumptions, input_revision
            ) VALUES (
                %s, %s, %s, 1, 'manual', 'week',
                DATE '2026-10-26', 'balanced', 15,
                TIME '08:00', TIME '22:00',
                '既有周计划', '[]', 0
            )
            """,
            (plan_version_id, plan_id, owner_id),
        )
        connection.execute(
            """
            INSERT INTO scheduled_blocks (
                id, plan_version_id, owner_id, position, task_id,
                task_title_snapshot, step_ids, step_titles_snapshot,
                local_date, local_starts_at, local_ends_at, time_zone,
                starts_at, ends_at, estimate_source, estimated_minutes
            ) VALUES (
                %s, %s, %s, 0, %s,
                'DST 周任务', '[]', '[]',
                DATE '2026-10-31', TIME '09:00', TIME '10:00',
                'America/New_York',
                TIMESTAMPTZ '2026-10-31 13:00:00+00',
                TIMESTAMPTZ '2026-10-31 14:00:00+00',
                'user', 60
            )
            """,
            (block_id, plan_version_id, owner_id, task_id),
        )
        connection.commit()

    database = Database(
        Settings(
            environment="test",
            database_url=database_url,
            db_pool_size=2,
            db_max_overflow=0,
        )
    )
    request = PlanningRequestFields(
        scope="day",
        target_date=date(2026, 11, 1),
        instruction="  保留周日空闲  ",
    )
    try:
        async with database.transaction() as session:
            context = await planning_context(session, owner_id, request)
            snapshot = await build_planning_snapshot(session, owner_id, request)
            await session.flush()
            assert snapshot.content_sha256 == context["content_sha256"]
            assert snapshot.time_zone == "America/New_York"
            assert snapshot.week_start_date == date(2026, 10, 26)
            assert snapshot.week_end_date == date(2026, 11, 1)
            assert snapshot.user_instructions == "保留周日空闲"
            assert snapshot.task_candidates == context["task_candidates"]
            assert snapshot.fixed_occurrences == context["fixed_occurrences"]
            assert snapshot.current_plan_id == plan_id
            assert snapshot.current_plan_version == 1
            assert snapshot.preserved_dates == ["2026-10-31"]
            assert context["current_plan"]["blocks"][0]["id"] == str(block_id)
            assert sha256(snapshot.content_sha256.encode()).hexdigest() != (
                snapshot.content_sha256
            )

        with psycopg.connect(psycopg_url(database_url)) as connection:
            saved = connection.execute(
                """
                SELECT revision, content_sha256
                FROM planning_input_snapshots
                WHERE id=%s AND owner_id=%s
                """,
                (snapshot.id, owner_id),
            ).fetchone()
        assert saved == (snapshot.revision, snapshot.content_sha256)
    finally:
        await database.close()
