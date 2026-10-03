import asyncio
from datetime import date
from uuid import UUID, uuid4

import psycopg
import pytest

from itime.config import Settings
from itime.database import Database
from itime.errors import AppError
from itime.planning.plan_service import (
    create_plan_version,
    read_plan_history,
    read_week_plan,
)
from itime.planning.validation import PlanningToolOutput, normalize_plan_payload
from tests.conftest import isolated_database, psycopg_url

pytestmark = pytest.mark.integration

WEEK_START = date(2026, 10, 5)


def seed_owner_and_tasks(database_url: str, count: int = 3) -> tuple[UUID, list[UUID]]:
    owner_id = uuid4()
    task_ids = [uuid4() for _ in range(count)]
    with psycopg.connect(psycopg_url(database_url)) as connection:
        connection.execute(
            """
            INSERT INTO users (id, email, password_hash, email_verified_at)
            VALUES (%s, %s, 'test-password-hash', now())
            """,
            (owner_id, f"{owner_id}@example.com"),
        )
        for index, task_id in enumerate(task_ids, start=1):
            connection.execute(
                """
                INSERT INTO tasks (
                    id, owner_id, title, estimated_minutes, estimate_source
                ) VALUES (%s, %s, %s, 60, 'user')
                """,
                (task_id, owner_id, f"任务 {index}"),
            )
        connection.commit()
    return owner_id, task_ids


def current_revision(database_url: str, owner_id: UUID) -> int:
    with psycopg.connect(psycopg_url(database_url)) as connection:
        return connection.execute(
            """
            SELECT revision
            FROM planning_input_revisions
            WHERE owner_id=%s
            """,
            (owner_id,),
        ).fetchone()[0]


def candidate(task_id: UUID, index: int) -> dict:
    return {
        "candidate_key": f"task-{index}",
        "task_id": str(task_id),
        "occurrence_key": None,
        "title": f"任务 {index}",
        "estimated_minutes": 60,
        "estimate_source": "user",
        "splittable": True,
        "minimum_block_minutes": 15,
        "due_kind": "none",
        "due_date": None,
        "due_at": None,
        "steps": [],
    }


def snapshot(
    task_ids: list[UUID],
    *,
    scope: str = "week",
    target_date: str = "2026-10-05",
    buffer_minutes: int = 0,
) -> dict:
    return {
        "scope": scope,
        "target_date": target_date,
        "week_start_date": "2026-10-05",
        "week_end_date": "2026-10-11",
        "time_zone": "Asia/Shanghai",
        "density": "balanced",
        "buffer_minutes": buffer_minutes,
        "availability_start": "08:00:00",
        "availability_end": "22:00:00",
        "task_candidates": [
            candidate(task_id, index)
            for index, task_id in enumerate(task_ids, start=1)
        ],
        "fixed_occurrences": [],
    }


def normalized_plan(
    plan_snapshot: dict,
    blocks: list[tuple[int, str, str, str]],
    *,
    unscheduled: list[tuple[int, int]] | None = None,
    summary: str = "测试计划",
):
    output = PlanningToolOutput.model_validate(
        {
            "summary": summary,
            "assumptions": ["按当前任务估时"],
            "blocks": [
                {
                    "candidate_key": f"task-{candidate_index}",
                    "local_date": local_date,
                    "starts_at": starts_at,
                    "ends_at": ends_at,
                    "step_ids": [],
                }
                for candidate_index, local_date, starts_at, ends_at in blocks
            ],
            "unscheduled_items": [
                {
                    "candidate_key": f"task-{candidate_index}",
                    "remaining_minutes": minutes,
                    "reason": "no_capacity",
                    "explanation": "本次范围容量不足",
                }
                for candidate_index, minutes in (unscheduled or [])
            ],
        }
    )
    return normalize_plan_payload(plan_snapshot, output)


async def save_version(
    database: Database,
    owner_id: UUID,
    plan_snapshot: dict,
    normalized,
    *,
    expected_revision: int,
    expected_plan_version: int,
    operation_id: UUID | None = None,
) -> dict:
    async with database.transaction() as session:
        return await create_plan_version(
            session,
            owner_id,
            normalized,
            snapshot=plan_snapshot,
            source="manual",
            proposal_id=None,
            expected_revision=expected_revision,
            expected_plan_version=expected_plan_version,
            operation_id=operation_id or uuid4(),
        )


def test_creates_and_serializes_first_week_plan_and_reads_history(admin_url):
    with isolated_database(admin_url) as database_url:
        owner_id, task_ids = seed_owner_and_tasks(database_url, count=2)
        revision = current_revision(database_url, owner_id)
        plan_snapshot = snapshot(task_ids)
        normalized = normalized_plan(
            plan_snapshot,
            [
                (1, "2026-10-05", "09:00:00", "10:00:00"),
                (2, "2026-10-06", "10:00:00", "11:00:00"),
            ],
        )

        async def exercise() -> None:
            database = Database(
                Settings(database_url=database_url, environment="test")
            )
            try:
                created = await save_version(
                    database,
                    owner_id,
                    plan_snapshot,
                    normalized,
                    expected_revision=revision,
                    expected_plan_version=0,
                )
                assert created["plan"]["version"] == 1
                current = created["plan"]["current_version"]
                assert current["source"] == "manual"
                assert current["scope"] == "week"
                assert [item["task_title_snapshot"] for item in current["blocks"]] == [
                    "任务 1",
                    "任务 2",
                ]
                assert current["blocks"][0]["starts_at"] == "2026-10-05T01:00:00Z"
                assert current["unscheduled_items"] == []

                async with database.transaction() as session:
                    read = await read_week_plan(
                        session,
                        owner_id,
                        WEEK_START,
                    )
                    history = await read_plan_history(
                        session,
                        owner_id,
                        UUID(created["plan"]["id"]),
                    )
                assert read == created["plan"]
                assert [item["version"] for item in history] == [1]
                assert history[0] == current
            finally:
                await database.close()

        asyncio.run(exercise())


def test_day_version_preserves_other_dates_and_week_version_replaces_all(admin_url):
    with isolated_database(admin_url) as database_url:
        owner_id, task_ids = seed_owner_and_tasks(database_url)
        database = Database(Settings(database_url=database_url, environment="test"))

        async def exercise() -> None:
            try:
                first_snapshot = snapshot(task_ids)
                first = await save_version(
                    database,
                    owner_id,
                    first_snapshot,
                    normalized_plan(
                        first_snapshot,
                        [
                            (1, "2026-10-05", "09:00:00", "10:00:00"),
                            (2, "2026-10-06", "10:00:00", "11:00:00"),
                        ],
                        unscheduled=[(3, 60)],
                        summary="第一版",
                    ),
                    expected_revision=current_revision(database_url, owner_id),
                    expected_plan_version=0,
                )

                day_snapshot = snapshot(
                    [task_ids[0]],
                    scope="day",
                    target_date="2026-10-05",
                )
                second = await save_version(
                    database,
                    owner_id,
                    day_snapshot,
                    normalized_plan(
                        day_snapshot,
                        [(1, "2026-10-05", "13:00:00", "14:00:00")],
                        summary="只替换周一",
                    ),
                    expected_revision=current_revision(database_url, owner_id),
                    expected_plan_version=1,
                )
                second_version = second["plan"]["current_version"]
                assert second["plan"]["version"] == 2
                assert [
                    (item["local_date"], item["local_starts_at"])
                    for item in second_version["blocks"]
                ] == [
                    ("2026-10-05", "13:00:00"),
                    ("2026-10-06", "10:00:00"),
                ]
                assert [
                    item["task_id"] for item in second_version["unscheduled_items"]
                ] == [str(task_ids[2])]

                week_snapshot = snapshot([task_ids[2]])
                third = await save_version(
                    database,
                    owner_id,
                    week_snapshot,
                    normalized_plan(
                        week_snapshot,
                        [(1, "2026-10-07", "15:00:00", "16:00:00")],
                        summary="整周替换",
                    ),
                    expected_revision=current_revision(database_url, owner_id),
                    expected_plan_version=2,
                )
                third_version = third["plan"]["current_version"]
                assert third["plan"]["version"] == 3
                assert [item["task_id"] for item in third_version["blocks"]] == [
                    str(task_ids[2])
                ]
                assert third_version["unscheduled_items"] == []

                async with database.transaction() as session:
                    history = await read_plan_history(
                        session,
                        owner_id,
                        UUID(first["plan"]["id"]),
                    )
                assert [item["version"] for item in history] == [3, 2, 1]
                assert history[-1]["summary"] == "第一版"
                assert len(history[-1]["blocks"]) == 2
                assert len(history[-1]["unscheduled_items"]) == 1
            finally:
                await database.close()

        asyncio.run(exercise())


def test_expected_versions_and_operation_replay_are_enforced(admin_url):
    with isolated_database(admin_url) as database_url:
        owner_id, task_ids = seed_owner_and_tasks(database_url, count=1)
        revision = current_revision(database_url, owner_id)
        plan_snapshot = snapshot(task_ids)
        normalized = normalized_plan(
            plan_snapshot,
            [(1, "2026-10-05", "09:00:00", "10:00:00")],
        )
        operation_id = uuid4()

        async def exercise() -> None:
            database = Database(
                Settings(database_url=database_url, environment="test")
            )
            try:
                first = await save_version(
                    database,
                    owner_id,
                    plan_snapshot,
                    normalized,
                    expected_revision=revision,
                    expected_plan_version=0,
                    operation_id=operation_id,
                )
                replay = await save_version(
                    database,
                    owner_id,
                    plan_snapshot,
                    normalized,
                    expected_revision=revision,
                    expected_plan_version=0,
                    operation_id=operation_id,
                )
                assert replay == first

                changed = normalized.model_copy(update={"summary": "不同内容"})
                with pytest.raises(AppError) as reused:
                    await save_version(
                        database,
                        owner_id,
                        plan_snapshot,
                        changed,
                        expected_revision=revision,
                        expected_plan_version=0,
                        operation_id=operation_id,
                    )
                assert reused.value.code == "operation_id_reused"

                current = current_revision(database_url, owner_id)
                with pytest.raises(AppError) as stale_revision:
                    await save_version(
                        database,
                        owner_id,
                        plan_snapshot,
                        normalized,
                        expected_revision=revision,
                        expected_plan_version=1,
                    )
                assert stale_revision.value.code == "planning_revision_conflict"
                assert stale_revision.value.details["current_revision"] == current

                with pytest.raises(AppError) as stale_plan:
                    await save_version(
                        database,
                        owner_id,
                        plan_snapshot,
                        normalized,
                        expected_revision=current,
                        expected_plan_version=0,
                    )
                assert stale_plan.value.code == "plan_version_conflict"
                assert stale_plan.value.details["current_version"] == 1
            finally:
                await database.close()

        asyncio.run(exercise())


def test_concurrent_first_plan_is_unique_and_same_operation_replays(admin_url):
    with isolated_database(admin_url) as database_url:
        owner_id, task_ids = seed_owner_and_tasks(database_url, count=1)
        revision = current_revision(database_url, owner_id)
        plan_snapshot = snapshot(task_ids)
        normalized = normalized_plan(
            plan_snapshot,
            [(1, "2026-10-05", "09:00:00", "10:00:00")],
        )

        async def concurrent_create(operation_ids: list[UUID]) -> list[dict | AppError]:
            databases = [
                Database(Settings(database_url=database_url, environment="test"))
                for _ in operation_ids
            ]

            async def run(index: int) -> dict | AppError:
                try:
                    return await save_version(
                        databases[index],
                        owner_id,
                        plan_snapshot,
                        normalized,
                        expected_revision=revision,
                        expected_plan_version=0,
                        operation_id=operation_ids[index],
                    )
                except AppError as error:
                    return error

            try:
                return await asyncio.gather(
                    *(run(index) for index in range(len(operation_ids)))
                )
            finally:
                await asyncio.gather(*(database.close() for database in databases))

        shared_operation = uuid4()
        replays = asyncio.run(
            concurrent_create([shared_operation, shared_operation])
        )
        assert all(isinstance(result, dict) for result in replays)
        assert replays[0] == replays[1]

        with psycopg.connect(psycopg_url(database_url)) as connection:
            assert connection.execute(
                "SELECT count(*) FROM plans WHERE owner_id=%s",
                (owner_id,),
            ).fetchone()[0] == 1
            assert connection.execute(
                "SELECT count(*) FROM plan_versions WHERE owner_id=%s",
                (owner_id,),
            ).fetchone()[0] == 1

        second_owner_id, second_task_ids = seed_owner_and_tasks(
            database_url,
            count=1,
        )
        owner_id = second_owner_id
        revision = current_revision(database_url, owner_id)
        plan_snapshot = snapshot(second_task_ids)
        normalized = normalized_plan(
            plan_snapshot,
            [(1, "2026-10-05", "09:00:00", "10:00:00")],
        )
        competing = asyncio.run(concurrent_create([uuid4(), uuid4()]))
        successes = [result for result in competing if isinstance(result, dict)]
        conflicts = [result for result in competing if isinstance(result, AppError)]
        assert len(successes) == 1
        assert len(conflicts) == 1
        assert conflicts[0].code == "planning_revision_conflict"

        with psycopg.connect(psycopg_url(database_url)) as connection:
            assert connection.execute(
                "SELECT count(*) FROM plans WHERE owner_id=%s",
                (owner_id,),
            ).fetchone()[0] == 1
            assert connection.execute(
                "SELECT count(*) FROM plan_versions WHERE owner_id=%s",
                (owner_id,),
            ).fetchone()[0] == 1
