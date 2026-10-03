import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import httpx
import psycopg
import pytest

from itime.config import Settings
from itime.database import Database
from itime.planning import proposal_service
from itime.planning.schemas import ProposalActionInput
from tests.conftest import psycopg_url
from tests.test_calendar_series import browser, create_account, create_series
from tests.test_tasks import create_task

pytestmark = pytest.mark.integration


def _target_week() -> tuple[date, date]:
    today = datetime.now(UTC).astimezone(ZoneInfo("Asia/Shanghai")).date()
    week_start = today - timedelta(days=today.weekday())
    return week_start, week_start + timedelta(days=1)


def _normalized_payload(task_id: UUID, target_date: date, *, start_hour: int = 9) -> dict:
    local_start = datetime.combine(target_date, time(start_hour), ZoneInfo("Asia/Shanghai"))
    local_end = local_start + timedelta(hours=1)
    return {
        "summary": "按当前任务生成测试计划",
        "assumptions": ["沿用用户估时"],
        "blocks": [
            {
                "candidate_key": f"task:{task_id}",
                "task_id": str(task_id),
                "occurrence_key": None,
                "local_date": target_date.isoformat(),
                "local_starts_at": local_start.time().isoformat(),
                "local_ends_at": local_end.time().isoformat(),
                "time_zone": "Asia/Shanghai",
                "starts_at": local_start.astimezone(UTC).isoformat().replace("+00:00", "Z"),
                "ends_at": local_end.astimezone(UTC).isoformat().replace("+00:00", "Z"),
                "step_ids": [],
                "estimate_source": "user",
                "estimated_minutes": 60,
            }
        ],
        "unscheduled_items": [],
    }


def _planning_config(connection) -> UUID:
    existing = connection.execute(
        """
        SELECT id
        FROM ai_config_versions
        WHERE capability='planning'
        ORDER BY version
        LIMIT 1
        """
    ).fetchone()
    if existing is not None:
        return existing[0]
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
            api_key_ciphertext, timeout_seconds, max_output_tokens, status
        ) VALUES (
            %s, 'planning', %s, 'https://ai.example.com/v1',
            'planning-test', %s, 60, 4096, 'draft'
        )
        """,
        (config_id, version, b"encrypted-test-key"),
    )
    return config_id


def _seed_proposal(
    database_url: str,
    owner_id: UUID,
    task_id: UUID,
    *,
    target_date: date,
    title: str,
    created_at: datetime | None = None,
    expires_at: datetime | None = None,
    current_plan_id: UUID | None = None,
    current_plan_version: int | None = None,
    start_hour: int = 9,
) -> tuple[UUID, int]:
    proposal_id = uuid4()
    snapshot_id = uuid4()
    job_id = uuid4()
    created_at = created_at or datetime.now(UTC)
    expires_at = expires_at or created_at + timedelta(days=7)
    week_start = target_date - timedelta(days=target_date.weekday())
    candidate = {
        "candidate_key": f"task:{task_id}",
        "task_id": str(task_id),
        "task_version": 1,
        "occurrence_key": None,
        "occurrence_version": 0,
        "title": "提案测试任务",
        "description": "",
        "project_id": None,
        "priority": 0,
        "status": "open",
        "estimated_minutes": 60,
        "estimate_source": "user",
        "splittable": True,
        "minimum_block_minutes": 15,
        "due_kind": "none",
        "due_date": None,
        "due_at": None,
        "time_zone": None,
        "step_progress": {"total": 0, "completed": 0},
        "completed_step_ids": [],
        "steps": [],
    }
    payload = _normalized_payload(task_id, target_date, start_hour=start_hour)
    with psycopg.connect(psycopg_url(database_url)) as connection:
        revision = connection.execute(
            """
            SELECT revision
            FROM planning_input_revisions
            WHERE owner_id=%s
            """,
            (owner_id,),
        ).fetchone()[0]
        config_id = _planning_config(connection)
        connection.execute(
            """
            INSERT INTO planning_input_snapshots (
                id, owner_id, revision, scope, target_date,
                week_start_date, week_end_date, time_zone, density,
                buffer_minutes, availability_start, availability_end,
                user_instructions, selected_task_ids, task_candidates,
                fixed_occurrences, current_plan_id, current_plan_version,
                preserved_dates, content_sha256, created_at
            ) VALUES (
                %s, %s, %s, 'week', %s, %s, %s, 'Asia/Shanghai', 'balanced',
                0, TIME '08:00', TIME '22:00', '', %s::jsonb, %s::jsonb,
                '[]'::jsonb, %s, %s, '[]'::jsonb, %s, %s
            )
            """,
            (
                snapshot_id,
                owner_id,
                revision,
                target_date,
                week_start,
                week_start + timedelta(days=6),
                json.dumps([str(task_id)]),
                json.dumps([candidate], ensure_ascii=False),
                current_plan_id,
                current_plan_version,
                snapshot_id.hex * 2,
                created_at,
            ),
        )
        connection.execute(
            """
            INSERT INTO ai_jobs (
                id, owner_id, capability, config_version_id,
                planning_snapshot_id, quota_mode, status, input_summary,
                available_at, deadline_at, created_at, updated_at
            ) VALUES (
                %s, %s, 'planning', %s, %s, 'platform', 'queued',
                '{"scope":"week"}', %s, %s, %s, %s
            )
            """,
            (
                job_id,
                owner_id,
                config_id,
                snapshot_id,
                created_at,
                created_at + timedelta(minutes=3),
                created_at,
                created_at,
            ),
        )
        connection.execute(
            """
            INSERT INTO ai_proposals (
                id, owner_id, kind, source_job_id, input_snapshot_id,
                schema_version, status, title, summary, assumptions,
                warnings, payload, expires_at, version, created_at, updated_at
            ) VALUES (
                %s, %s, 'plan', %s, %s, 1, 'pending', %s,
                '按当前任务生成', '["沿用用户估时"]'::jsonb, '[]'::jsonb,
                %s::jsonb, %s, 1, %s, %s
            )
            """,
            (
                proposal_id,
                owner_id,
                job_id,
                snapshot_id,
                title,
                json.dumps(payload, ensure_ascii=False),
                expires_at,
                created_at,
                created_at,
            ),
        )
        connection.execute(
            """
            INSERT INTO ai_proposal_changes (
                id, proposal_id, owner_id, position, action,
                entity_type, after_data, created_at
            ) VALUES (
                %s, %s, %s, 0, 'create', 'scheduled_block',
                %s::jsonb, %s
            )
            """,
            (
                uuid4(),
                proposal_id,
                owner_id,
                json.dumps({"local_date": target_date.isoformat(), "starts_at": "09:00"}),
                created_at,
            ),
        )
        connection.commit()
    return proposal_id, revision


def _action(operation_id: UUID | None = None, *, expected_version: int = 1) -> dict:
    return {
        "operation_id": str(operation_id or uuid4()),
        "expected_proposal_version": expected_version,
    }


def _apply(client: httpx.Client, proposal_id: UUID, payload: dict) -> httpx.Response:
    return client.post(f"/api/v1/proposals/{proposal_id}/apply", json=payload)


def _reject(client: httpx.Client, proposal_id: UUID, payload: dict) -> httpx.Response:
    return client.post(f"/api/v1/proposals/{proposal_id}/reject", json=payload)


def test_lists_and_reads_only_own_proposals_with_effective_status(
    client,
    database_url,
    live_server,
):
    assert client.get("/api/v1/proposals").status_code == 401
    _, owner_text = create_account(client, database_url)
    owner_id = UUID(owner_text)
    target_date, _ = _target_week()
    task = create_task(
        client,
        title="提案测试任务",
        estimated_minutes=60,
        steps=[],
        links=[],
        labels=[],
    )
    task_id = UUID(task["id"])
    current = datetime.now(UTC)
    expired_id, _ = _seed_proposal(
        database_url,
        owner_id,
        task_id,
        target_date=target_date,
        title="已过期提案",
        created_at=current - timedelta(days=8),
        expires_at=current - timedelta(days=1),
    )
    conflicted_id, _ = _seed_proposal(
        database_url,
        owner_id,
        task_id,
        target_date=target_date,
        title="输入已变化提案",
        created_at=current - timedelta(minutes=2),
    )
    with psycopg.connect(psycopg_url(database_url), autocommit=True) as connection:
        connection.execute(
            "UPDATE tasks SET title='提案测试任务（已改）', updated_at=now() WHERE id=%s",
            (task_id,),
        )
    pending_id, _ = _seed_proposal(
        database_url,
        owner_id,
        task_id,
        target_date=target_date,
        title="仍有效提案",
        created_at=current - timedelta(minutes=1),
    )

    first_page = client.get("/api/v1/proposals", params={"limit": 2})
    assert first_page.status_code == 200, first_page.text
    assert [item["id"] for item in first_page.json()["items"]] == [
        str(pending_id),
        str(conflicted_id),
    ]
    assert [item["effective_status"] for item in first_page.json()["items"]] == [
        "pending",
        "conflicted",
    ]
    cursor = first_page.json()["next_cursor"]
    assert cursor is not None
    second_page = client.get("/api/v1/proposals", params={**cursor, "limit": 2})
    assert second_page.status_code == 200, second_page.text
    assert [item["id"] for item in second_page.json()["items"]] == [str(expired_id)]
    assert second_page.json()["items"][0]["effective_status"] == "expired"
    assert second_page.json()["next_cursor"] is None

    detail = client.get(f"/api/v1/proposals/{pending_id}")
    assert detail.status_code == 200, detail.text
    proposal = detail.json()["proposal"]
    assert proposal["effective_status"] == "pending"
    assert proposal["payload"]["blocks"][0]["task_id"] == str(task_id)
    assert proposal["changes"][0]["entity_type"] == "scheduled_block"
    assert proposal["job"]["capability"] == "planning"
    assert proposal["input_snapshot"]["revision"] == proposal["current_state"]["revision"]

    conflict = client.get(f"/api/v1/proposals/{conflicted_id}").json()["proposal"]
    assert conflict["effective_status"] == "conflicted"
    assert "planning_revision" in conflict["conflict"]["categories"]

    with browser(live_server) as other:
        create_account(other, database_url)
        assert other.get(f"/api/v1/proposals/{pending_id}").status_code == 404
        assert other.get("/api/v1/proposals").json()["items"] == []


def test_apply_revalidates_saves_once_and_replays_operation(
    client,
    database_url,
):
    _, owner_text = create_account(client, database_url)
    owner_id = UUID(owner_text)
    target_date, _ = _target_week()
    task = create_task(
        client,
        title="提案测试任务",
        estimated_minutes=60,
        steps=[],
        links=[],
        labels=[],
    )
    proposal_id, _ = _seed_proposal(
        database_url,
        owner_id,
        UUID(task["id"]),
        target_date=target_date,
        title="待确认提案",
    )
    operation_id = uuid4()
    payload = _action(operation_id)

    csrf = client.headers.pop("x-csrf-token")
    denied = _apply(client, proposal_id, payload)
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "invalid_csrf"
    client.headers["x-csrf-token"] = csrf

    applied = _apply(client, proposal_id, payload)
    assert applied.status_code == 200, applied.text
    result = applied.json()
    assert result["operation_id"] == str(operation_id)
    assert result["proposal"]["status"] == "applied"
    assert result["proposal"]["effective_status"] == "applied"
    assert result["proposal"]["version"] == 2
    assert result["application"]["plan_version"] == 1
    assert result["application"]["version"]["source"] == "ai"
    assert result["application"]["version"]["proposal_id"] == str(proposal_id)

    assert _apply(client, proposal_id, payload).json() == result
    reused = _apply(client, proposal_id, _action(operation_id, expected_version=2))
    assert reused.status_code == 409
    assert reused.json()["error"]["code"] == "operation_id_reused"

    another_operation = _apply(client, proposal_id, _action())
    assert another_operation.status_code == 200, another_operation.text
    assert another_operation.json()["application"] == result["application"]
    invalid_terminal_version = _apply(
        client,
        proposal_id,
        _action(expected_version=99),
    )
    assert invalid_terminal_version.status_code == 409
    assert invalid_terminal_version.json()["error"]["code"] == (
        "proposal_version_conflict"
    )
    rejected_after_apply = _reject(client, proposal_id, _action(expected_version=2))
    assert rejected_after_apply.status_code == 409
    assert rejected_after_apply.json()["error"]["code"] == "proposal_applied"

    with psycopg.connect(psycopg_url(database_url)) as connection:
        counts = connection.execute(
            """
            SELECT
                (SELECT count(*) FROM plans WHERE owner_id=%s),
                (SELECT count(*) FROM plan_versions WHERE owner_id=%s),
                (SELECT count(*) FROM scheduled_blocks WHERE owner_id=%s)
            """,
            (owner_id, owner_id, owner_id),
        ).fetchone()
        assert counts == (1, 1, 1)
        saved_scopes = {
            row[0]
            for row in connection.execute(
                """
                SELECT scope
                FROM user_operations
                WHERE owner_id=%s AND scope LIKE 'planning.%%'
                """,
                (owner_id,),
            )
        }
        assert f"planning.proposal.apply:{proposal_id}" in saved_scopes
        assert "planning.plan_version.create" in saved_scopes


def test_apply_returns_precise_version_expiry_and_revision_conflicts(
    client,
    database_url,
):
    _, owner_text = create_account(client, database_url)
    owner_id = UUID(owner_text)
    target_date, _ = _target_week()
    task = create_task(
        client,
        title="提案测试任务",
        estimated_minutes=60,
        steps=[],
        links=[],
        labels=[],
    )
    task_id = UUID(task["id"])

    stale_id, _ = _seed_proposal(
        database_url,
        owner_id,
        task_id,
        target_date=target_date,
        title="版本冲突",
    )
    stale = _apply(client, stale_id, _action(expected_version=2))
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "proposal_version_conflict"
    assert stale.json()["error"]["details"]["current_version"] == 1

    current = datetime.now(UTC)
    expired_id, _ = _seed_proposal(
        database_url,
        owner_id,
        task_id,
        target_date=target_date,
        title="过期冲突",
        created_at=current - timedelta(days=8),
        expires_at=current + timedelta(days=30),
    )
    expired = _apply(client, expired_id, _action())
    assert expired.status_code == 409
    assert expired.json()["error"]["code"] == "proposal_expired"

    revision_id, snapshot_revision = _seed_proposal(
        database_url,
        owner_id,
        task_id,
        target_date=target_date,
        title="修订冲突",
    )
    with psycopg.connect(psycopg_url(database_url), autocommit=True) as connection:
        connection.execute(
            "UPDATE tasks SET description='确认前人工编辑' WHERE id=%s",
            (task_id,),
        )
    conflicted = _apply(client, revision_id, _action())
    assert conflicted.status_code == 409
    error = conflicted.json()["error"]
    assert error["code"] == "proposal_conflicted"
    assert error["details"]["snapshot_revision"] == snapshot_revision
    assert error["details"]["current_revision"] > snapshot_revision
    assert "planning_revision" in error["details"]["categories"]

    with psycopg.connect(psycopg_url(database_url)) as connection:
        assert connection.execute(
            "SELECT count(*) FROM plan_versions WHERE owner_id=%s",
            (owner_id,),
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT status, version FROM ai_proposals WHERE id=%s",
            (revision_id,),
        ).fetchone() == ("pending", 1)


def test_apply_checks_base_plan_version_and_serializes_concurrent_confirmation(
    client,
    database_url,
    live_server,
):
    _, owner_text = create_account(client, database_url)
    owner_id = UUID(owner_text)
    target_date, _ = _target_week()
    task = create_task(
        client,
        title="提案测试任务",
        estimated_minutes=60,
        steps=[],
        links=[],
        labels=[],
    )
    task_id = UUID(task["id"])
    first_id, _ = _seed_proposal(
        database_url,
        owner_id,
        task_id,
        target_date=target_date,
        title="建立基础计划",
    )
    first = _apply(client, first_id, _action())
    assert first.status_code == 200, first.text
    plan_id = UUID(first.json()["application"]["plan_id"])

    older_id, _ = _seed_proposal(
        database_url,
        owner_id,
        task_id,
        target_date=target_date,
        title="同一基础版本 A",
        current_plan_id=plan_id,
        current_plan_version=1,
        start_hour=10,
    )
    winner_id, _ = _seed_proposal(
        database_url,
        owner_id,
        task_id,
        target_date=target_date,
        title="同一基础版本 B",
        current_plan_id=plan_id,
        current_plan_version=1,
        start_hour=11,
    )
    winner = _apply(client, winner_id, _action())
    assert winner.status_code == 200, winner.text
    assert winner.json()["application"]["plan_version"] == 2

    stale = _apply(client, older_id, _action())
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "proposal_conflicted"
    assert "base_plan" in stale.json()["error"]["details"]["categories"]
    assert stale.json()["error"]["details"]["current_plan"]["version"] == 2

    concurrent_id, _ = _seed_proposal(
        database_url,
        owner_id,
        task_id,
        target_date=target_date,
        title="并发确认",
        current_plan_id=plan_id,
        current_plan_version=2,
        start_hour=12,
    )
    cookies = dict(client.cookies)
    csrf = client.headers["x-csrf-token"]

    def confirm() -> httpx.Response:
        with httpx.Client(
            base_url=live_server.origin,
            cookies=cookies,
            headers={"x-csrf-token": csrf},
            timeout=10,
            trust_env=False,
        ) as concurrent_client:
            return _apply(concurrent_client, concurrent_id, _action())

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(lambda _: confirm(), range(2)))
    assert [response.status_code for response in responses] == [200, 200]
    applications = [response.json()["application"] for response in responses]
    assert applications[0] == applications[1]
    assert applications[0]["plan_version"] == 3

    with psycopg.connect(psycopg_url(database_url)) as connection:
        assert connection.execute(
            "SELECT count(*) FROM plan_versions WHERE proposal_id=%s",
            (concurrent_id,),
        ).fetchone()[0] == 1


def test_apply_rebuilds_current_context_before_revalidating_payload(
    client,
    database_url,
):
    _, owner_text = create_account(client, database_url)
    owner_id = UUID(owner_text)
    target_date, _ = _target_week()
    task = create_task(
        client,
        title="提案测试任务",
        estimated_minutes=60,
        steps=[],
        links=[],
        labels=[],
    )
    proposal_id, snapshot_revision = _seed_proposal(
        database_url,
        owner_id,
        UUID(task["id"]),
        target_date=target_date,
        title="当前上下文复校验",
    )
    fixed = create_series(
        client,
        name="确认前新增固定安排",
        start_date=target_date.isoformat(),
        starts_at="09:30:00",
        ends_at="10:30:00",
    )
    assert fixed["series"]["name"] == "确认前新增固定安排"

    # 模拟修订标记意外未推进，证明确认并非仅靠旧快照或修订号放行。
    with psycopg.connect(psycopg_url(database_url), autocommit=True) as connection:
        connection.execute(
            """
            UPDATE planning_input_revisions
            SET revision=%s
            WHERE owner_id=%s
            """,
            (snapshot_revision, owner_id),
        )

    response = _apply(client, proposal_id, _action())
    assert response.status_code == 409, response.text
    error = response.json()["error"]
    assert error["code"] == "proposal_conflicted"
    assert error["details"]["categories"] == ["hard_constraints"]
    assert error["details"]["validation_code"] == "plan_fixed_conflict"

    with psycopg.connect(psycopg_url(database_url)) as connection:
        assert connection.execute(
            "SELECT count(*) FROM plan_versions WHERE proposal_id=%s",
            (proposal_id,),
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT status, version FROM ai_proposals WHERE id=%s",
            (proposal_id,),
        ).fetchone() == ("pending", 1)


def test_reject_is_idempotent_and_does_not_create_plan(
    client,
    database_url,
):
    _, owner_text = create_account(client, database_url)
    owner_id = UUID(owner_text)
    target_date, _ = _target_week()
    task = create_task(
        client,
        title="提案测试任务",
        estimated_minutes=60,
        steps=[],
        links=[],
        labels=[],
    )
    proposal_id, _ = _seed_proposal(
        database_url,
        owner_id,
        UUID(task["id"]),
        target_date=target_date,
        title="待拒绝提案",
    )
    operation_id = uuid4()
    payload = _action(operation_id)

    rejected = _reject(client, proposal_id, payload)
    assert rejected.status_code == 200, rejected.text
    result = rejected.json()
    assert result["proposal"]["status"] == "rejected"
    assert result["proposal"]["effective_status"] == "rejected"
    assert result["proposal"]["version"] == 2
    assert _reject(client, proposal_id, payload).json() == result

    reused = _reject(client, proposal_id, _action(operation_id, expected_version=2))
    assert reused.status_code == 409
    assert reused.json()["error"]["code"] == "operation_id_reused"
    second_operation = _reject(client, proposal_id, _action(expected_version=1))
    assert second_operation.status_code == 200
    assert second_operation.json()["proposal"]["rejected_at"] == (
        result["proposal"]["rejected_at"]
    )
    invalid_terminal_version = _reject(
        client,
        proposal_id,
        _action(expected_version=99),
    )
    assert invalid_terminal_version.status_code == 409
    assert invalid_terminal_version.json()["error"]["code"] == (
        "proposal_version_conflict"
    )

    apply_rejected = _apply(client, proposal_id, _action(expected_version=2))
    assert apply_rejected.status_code == 409
    assert apply_rejected.json()["error"]["code"] == "proposal_rejected"
    with psycopg.connect(psycopg_url(database_url)) as connection:
        assert connection.execute(
            "SELECT count(*) FROM plans WHERE owner_id=%s",
            (owner_id,),
        ).fetchone()[0] == 0


@pytest.mark.asyncio
async def test_apply_rolls_back_plan_and_proposal_when_outer_save_fails(
    client,
    database_url,
    monkeypatch,
):
    _, owner_text = create_account(client, database_url)
    owner_id = UUID(owner_text)
    target_date, _ = _target_week()
    task = create_task(
        client,
        title="提案测试任务",
        estimated_minutes=60,
        steps=[],
        links=[],
        labels=[],
    )
    proposal_id, _ = _seed_proposal(
        database_url,
        owner_id,
        UUID(task["id"]),
        target_date=target_date,
        title="原子回滚提案",
    )

    def fail_outer_save(*args, **kwargs) -> None:
        raise RuntimeError("injected outer operation failure")

    monkeypatch.setattr(proposal_service, "save_operation", fail_outer_save)
    database = Database(Settings(database_url=database_url, environment="test"))
    try:
        with pytest.raises(RuntimeError, match="injected outer operation failure"):
            async with database.transaction() as session:
                await proposal_service.apply_proposal(
                    session,
                    owner_id,
                    proposal_id,
                    ProposalActionInput(
                        operation_id=uuid4(),
                        expected_proposal_version=1,
                    ),
                )
    finally:
        await database.close()

    with psycopg.connect(psycopg_url(database_url)) as connection:
        assert connection.execute(
            "SELECT count(*) FROM plans WHERE owner_id=%s",
            (owner_id,),
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM plan_versions WHERE owner_id=%s",
            (owner_id,),
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT status, version FROM ai_proposals WHERE id=%s",
            (proposal_id,),
        ).fetchone() == ("pending", 1)
