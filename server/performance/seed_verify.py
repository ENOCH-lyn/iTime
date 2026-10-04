from dataclasses import dataclass

from sqlalchemy import text

from itime.config import Settings
from itime.database import Database
from performance.contracts import SeedManifest

COUNT_QUERIES = {
    "users": "SELECT count(*) FROM users",
    "user_time_settings": "SELECT count(*) FROM user_time_settings",
    "user_privacy_settings": "SELECT count(*) FROM user_privacy_settings",
    "memory_settings": "SELECT count(*) FROM memory_settings",
    "sync_states": "SELECT count(*) FROM sync_states",
    "planning_input_revisions": "SELECT count(*) FROM planning_input_revisions",
    "ai_daily_allowances": "SELECT count(*) FROM ai_daily_allowances",
    "projects": "SELECT count(*) FROM projects",
    "labels": "SELECT count(*) FROM labels",
    "tasks": "SELECT count(*) FROM tasks",
    "task_schedules": "SELECT count(*) FROM task_schedules",
    "task_steps": "SELECT count(*) FROM task_steps",
    "task_labels": "SELECT count(*) FROM task_labels",
    "task_links": "SELECT count(*) FROM task_links",
    "academic_terms": "SELECT count(*) FROM academic_terms",
    "term_periods": "SELECT count(*) FROM term_periods",
    "calendar_sources": "SELECT count(*) FROM calendar_sources",
    "calendar_subscriptions": "SELECT count(*) FROM calendar_subscriptions",
    "calendar_subscription_events": "SELECT count(*) FROM calendar_subscription_events",
    "calendar_series": "SELECT count(*) FROM calendar_series",
    "plans": "SELECT count(*) FROM plans",
    "plan_versions": "SELECT count(*) FROM plan_versions",
    "scheduled_blocks": "SELECT count(*) FROM scheduled_blocks",
    "focus_sessions": "SELECT count(*) FROM focus_sessions",
    "focus_events": "SELECT count(*) FROM focus_events",
    "difficulty_records": "SELECT count(*) FROM difficulty_records",
    "review_sessions": "SELECT count(*) FROM review_sessions",
    "review_messages": "SELECT count(*) FROM review_messages",
    "memories": "SELECT count(*) FROM memories",
    "conversations": "SELECT count(*) FROM ai_conversations",
    "conversation_messages": "SELECT count(*) FROM ai_conversation_messages",
    "change_events": "SELECT count(*) FROM change_events",
    "security_events": "SELECT count(*) FROM security_events",
    "task_lifecycle_active": """
        SELECT count(*) FROM tasks
        WHERE archived_at IS NULL AND deleted_at IS NULL AND purged_at IS NULL
    """,
    "task_lifecycle_archived": """
        SELECT count(*) FROM tasks
        WHERE archived_at IS NOT NULL AND deleted_at IS NULL AND purged_at IS NULL
    """,
    "task_lifecycle_trashed": """
        SELECT count(*) FROM tasks
        WHERE deleted_at IS NOT NULL AND purged_at IS NULL
    """,
    "task_lifecycle_purged": """
        SELECT count(*) FROM tasks WHERE purged_at IS NOT NULL
    """,
}

INVARIANT_QUERIES = {
    "orphan_tasks": """
        SELECT count(*) FROM tasks t
        LEFT JOIN users u ON u.id=t.owner_id
        WHERE u.id IS NULL
    """,
    "task_project_owner_mismatch": """
        SELECT count(*) FROM tasks t
        JOIN projects p ON p.id=t.project_id
        WHERE p.owner_id <> t.owner_id
    """,
    "task_label_owner_mismatch": """
        SELECT count(*) FROM task_labels tl
        JOIN tasks t ON t.id=tl.task_id
        JOIN labels l ON l.id=tl.label_id
        WHERE t.owner_id <> tl.owner_id OR l.owner_id <> tl.owner_id
    """,
    "duplicate_active_focus": """
        SELECT count(*) FROM (
          SELECT owner_id FROM focus_sessions
          WHERE status IN ('running','paused')
          GROUP BY owner_id HAVING count(*) > 1
        ) value
    """,
    "orphan_change_events": """
        SELECT count(*) FROM change_events e
        LEFT JOIN users u ON u.id=e.owner_id
        WHERE u.id IS NULL
    """,
    "active_quota_reservation_without_job": """
        SELECT count(*) FROM ai_quota_reservations r
        LEFT JOIN ai_jobs j ON j.id=r.job_id
        WHERE r.status='reserved' AND j.id IS NULL
    """,
    "external_task_links": """
        SELECT count(*) FROM task_links
        WHERE final_host IS DISTINCT FROM split_part(
            regexp_replace(url, '^https?://', ''),
            '/',
            1
        )
        OR final_host NOT LIKE '%.invalid'
    """,
    "external_ai_configs": """
        SELECT count(*) FROM ai_config_versions
        WHERE endpoint_url NOT LIKE 'http://host.docker.internal:%'
          AND endpoint_url NOT LIKE 'http://127.0.0.1:%'
          AND endpoint_url NOT LIKE 'http://localhost:%'
          AND endpoint_url NOT LIKE 'http://[::1]:%'
    """,
}


@dataclass(frozen=True)
class VerificationResult:
    ok: bool
    counts: dict[str, int]
    violations: tuple[str, ...]


def count_matches(actual: int, expected: int, *, allow_growth: bool) -> bool:
    return actual >= expected if allow_growth else actual == expected


async def verify_counts_and_invariants(
    database: Database,
    manifest: SeedManifest,
    *,
    allow_growth: bool = False,
) -> VerificationResult:
    actual_counts: dict[str, int] = {}
    violations: list[str] = []
    async with database.sessions() as session:
        for name, expected in manifest.counts.items():
            query = COUNT_QUERIES.get(name)
            if query is None:
                violations.append(f"清单包含未知计数：{name}")
                continue
            actual = int(await session.scalar(text(query)) or 0)
            actual_counts[name] = actual
            if not count_matches(actual, expected, allow_growth=allow_growth):
                violations.append(f"{name} 计数不匹配：期望 {expected}，实际 {actual}")

        invariant_values = await collect_invariants(database, session=session)
        violations.extend(
            f"{name}={value}"
            for name, value in invariant_values.items()
            if value
        )

        active_task_owners = int(
            await session.scalar(text("SELECT count(DISTINCT owner_id) FROM tasks")) or 0
        )
        active_change_owners = int(
            await session.scalar(
                text("SELECT count(DISTINCT owner_id) FROM change_events")
            )
            or 0
        )
        if (
            manifest.counts.get("tasks", 0)
            and not count_matches(
                active_task_owners,
                manifest.active_accounts,
                allow_growth=allow_growth,
            )
        ):
            violations.append(
                "任务账号数量不匹配："
                f"期望 {manifest.active_accounts}，实际 {active_task_owners}"
            )
        if (
            manifest.counts.get("change_events", 0)
            and not count_matches(
                active_change_owners,
                manifest.active_accounts,
                allow_growth=allow_growth,
            )
        ):
            violations.append(
                "同步事件账号数量不匹配："
                f"期望 {manifest.active_accounts}，实际 {active_change_owners}"
            )

    if manifest.accounts != manifest.counts.get("users"):
        violations.append("清单账号总数与 users 计数不一致")
    if len(manifest.credentials) != manifest.active_accounts:
        violations.append("凭据数量与活动账号数量不一致")
    if any(
        not credential.email.endswith("@performance.example.com")
        or credential.dataset != manifest.profile
        for credential in manifest.credentials
    ):
        violations.append("凭据包含非性能账号或错误数据集标识")

    return VerificationResult(
        ok=not violations,
        counts=actual_counts,
        violations=tuple(violations),
    )


async def verify_dataset(
    settings: Settings,
    manifest: SeedManifest,
    *,
    database: Database | None = None,
    allow_growth: bool = False,
) -> VerificationResult:
    owned_database = database is None
    active_database = database or Database(settings)
    try:
        return await verify_counts_and_invariants(
            active_database,
            manifest,
            allow_growth=allow_growth,
        )
    finally:
        if owned_database:
            await active_database.close()


async def collect_invariants(
    database: Database,
    *,
    session=None,
) -> dict[str, int]:
    if session is not None:
        return {
            name: int(await session.scalar(text(query)) or 0)
            for name, query in INVARIANT_QUERIES.items()
        }
    async with database.sessions() as owned_session:
        return {
            name: int(await owned_session.scalar(text(query)) or 0)
            for name, query in INVARIANT_QUERIES.items()
        }
