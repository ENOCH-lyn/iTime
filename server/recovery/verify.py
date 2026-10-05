from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import text

from itime.auth.security import Security
from itime.config import Settings
from itime.database import Database
from performance.seed_verify import COUNT_QUERIES, collect_invariants

SERVER_ROOT = Path(__file__).resolve().parents[1]
CORE_INVARIANTS = frozenset(
    {
        "active_quota_reservation_without_job",
        "duplicate_active_focus",
        "orphan_change_events",
        "orphan_tasks",
        "task_label_owner_mismatch",
        "task_project_owner_mismatch",
    }
)
RECOVERY_INVARIANT_QUERIES = {
    "unrevoked_sessions": """
        SELECT
          (SELECT count(*) FROM device_sessions WHERE revoked_at IS NULL)
          + (SELECT count(*) FROM admin_sessions WHERE revoked_at IS NULL)
    """,
    "active_ai_jobs": """
        SELECT count(*) FROM ai_jobs WHERE status IN ('queued', 'running')
    """,
    "active_quota_reservations": """
        SELECT count(*) FROM ai_quota_reservations WHERE status='reserved'
    """,
    "running_data_exports": """
        SELECT count(*) FROM data_export_jobs WHERE status='running'
    """,
    "running_calendar_subscriptions": """
        SELECT count(*) FROM calendar_subscriptions WHERE refresh_status='running'
    """,
    "running_public_links": """
        SELECT count(*) FROM task_links WHERE fetch_status='running'
    """,
    "processing_account_deletions": """
        SELECT count(*) FROM account_deletions WHERE status='processing'
    """,
    "completed_account_deletions_with_user": """
        SELECT count(*) FROM account_deletions
        WHERE status='completed' AND user_id IS NOT NULL
    """,
    "terminal_ai_jobs_with_lease": """
        SELECT count(*) FROM ai_jobs
        WHERE status IN ('succeeded', 'failed', 'timed_out', 'cancelled')
          AND (lease_id IS NOT NULL OR lease_until IS NOT NULL)
    """,
    "settled_quota_mismatch": """
        SELECT count(*)
        FROM ai_quota_reservations AS reservation
        JOIN ai_daily_allowances AS daily
          ON daily.id=reservation.daily_allowance_id
         AND daily.owner_id=reservation.owner_id
        LEFT JOIN ai_provider_costs AS cost
          ON cost.id=reservation.provider_cost_id
        WHERE reservation.status='settled'
          AND (
            cost.id IS NULL
            OR cost.job_id IS DISTINCT FROM reservation.job_id
            OR cost.owner_id IS DISTINCT FROM reservation.owner_id
            OR cost.attempt_status <> 'succeeded'
            OR cost.total_cost_nanos IS NULL
            OR cost.price_currency IS DISTINCT FROM daily.currency
            OR reservation.actual_cost_nanos
               IS DISTINCT FROM cost.total_cost_nanos
            OR reservation.charged_nanos
               IS DISTINCT FROM LEAST(
                   reservation.reserved_nanos,
                   cost.total_cost_nanos
               )
            OR reservation.platform_overage_nanos
               IS DISTINCT FROM (
                   cost.total_cost_nanos - reservation.charged_nanos
               )
            OR reservation.usage_source IS DISTINCT FROM cost.usage_source
            OR (
                SELECT count(*)
                FROM ai_quota_ledger_entries AS ledger
                WHERE ledger.reservation_id=reservation.id
                  AND ledger.kind='usage_charge'
                  AND ledger.owner_id=reservation.owner_id
                  AND ledger.daily_allowance_id=reservation.daily_allowance_id
                  AND ledger.job_id=reservation.job_id
                  AND ledger.provider_cost_id
                      IS NOT DISTINCT FROM reservation.provider_cost_id
                  AND ledger.delta_nanos=-reservation.charged_nanos
                  AND ledger.currency=daily.currency
                  AND ledger.usage_source=reservation.usage_source
            ) <> 1
          )
    """,
}


@dataclass(frozen=True)
class RecoveryVerification:
    passed: bool
    current_revision: str | None
    expected_revision: str
    counts: dict[str, int]
    invariants: dict[str, int]
    ledger_entries_total: int
    ledger_entries_applied: int
    failures: tuple[str, ...]

    def to_public_dict(self) -> dict[str, object]:
        return {
            "passed": self.passed,
            "current_revision": self.current_revision,
            "expected_revision": self.expected_revision,
            "counts": dict(sorted(self.counts.items())),
            "invariants": dict(sorted(self.invariants.items())),
            "ledger_entries_total": self.ledger_entries_total,
            "ledger_entries_applied": self.ledger_entries_applied,
            "failures": list(self.failures),
        }


@dataclass(frozen=True)
class EncryptionVerification:
    passed: bool
    sample_counts: dict[str, int]
    failures: tuple[str, ...]

    def to_public_dict(self) -> dict[str, object]:
        return {
            "passed": self.passed,
            "sample_counts": dict(sorted(self.sample_counts.items())),
            "failures": list(self.failures),
        }


def expected_alembic_revision() -> str:
    config = Config(str(SERVER_ROOT / "alembic.ini"))
    return ScriptDirectory.from_config(config).get_current_head()


async def verify_encryption_keys(settings: Settings) -> EncryptionVerification:
    security = Security(settings)
    calendar_key = (
        settings.calendar_subscription_encryption_key
        or settings.encryption_key
    ).get_secret_value()
    export_key = (
        settings.export_encryption_key or settings.encryption_key
    ).get_secret_value()
    decryptors = {
        "base": security.cipher,
        "ai": security.ai_cipher,
        "calendar": Fernet(calendar_key),
        "export": Fernet(export_key),
    }
    queries = {
        "base": """
            SELECT encrypted_message FROM mail_outbox
            WHERE encrypted_message IS NOT NULL
            ORDER BY id LIMIT 1
        """,
        "ai": """
            SELECT api_key_ciphertext FROM ai_config_versions
            ORDER BY id LIMIT 1
        """,
        "calendar": """
            SELECT encrypted_url FROM calendar_subscriptions
            ORDER BY source_id LIMIT 1
        """,
        "export": """
            SELECT artifact_ciphertext FROM data_export_jobs
            WHERE artifact_ciphertext IS NOT NULL
            ORDER BY id LIMIT 1
        """,
    }
    counts: dict[str, int] = {}
    failures: list[str] = []
    database = Database(settings)
    try:
        async with database.sessions() as session:
            for name, query in queries.items():
                encrypted = await session.scalar(text(query))
                if encrypted is None:
                    counts[name] = 0
                    failures.append(f"{name} encryption sample missing")
                    continue
                counts[name] = 1
                try:
                    decryptors[name].decrypt(bytes(encrypted))
                except (InvalidToken, TypeError, ValueError):
                    failures.append(f"{name} encryption key mismatch")
    finally:
        await database.close()
    return EncryptionVerification(
        passed=not failures,
        sample_counts=counts,
        failures=tuple(failures),
    )


async def verify_recovered_database(
    settings: Settings,
    *,
    expected_counts: Mapping[str, int],
    ledger_entries_total: int,
    ledger_entries_applied: int,
) -> RecoveryVerification:
    if ledger_entries_total < 0 or ledger_entries_applied < 0:
        raise ValueError("ledger entry counts must not be negative")
    expected_revision = expected_alembic_revision()
    counts: dict[str, int] = {}
    failures: list[str] = []
    database = Database(settings)
    try:
        async with database.sessions() as session:
            current_revision = await session.scalar(
                text("SELECT version_num FROM alembic_version")
            )
            performance_invariants = await collect_invariants(
                database,
                session=session,
            )
            invariants = {
                name: value
                for name, value in performance_invariants.items()
                if name in CORE_INVARIANTS
            }
            for name, query in RECOVERY_INVARIANT_QUERIES.items():
                invariants[name] = int(await session.scalar(text(query)) or 0)

            for name, expected in sorted(expected_counts.items()):
                if expected < 0:
                    raise ValueError("expected counts must not be negative")
                query = COUNT_QUERIES.get(name)
                if query is None:
                    failures.append(f"unknown expected count: {name}")
                    continue
                actual = int(await session.scalar(text(query)) or 0)
                counts[name] = actual
                if actual != expected:
                    failures.append(
                        f"{name} count expected {expected}, got {actual}"
                    )
    finally:
        await database.close()

    if current_revision != expected_revision:
        failures.append(
            "alembic revision expected "
            f"{expected_revision}, got {current_revision or 'missing'}"
        )
    failures.extend(
        f"invariant {name}={value}"
        for name, value in sorted(invariants.items())
        if value != 0
    )
    if ledger_entries_applied != ledger_entries_total:
        failures.append(
            "deletion ledger applied "
            f"{ledger_entries_applied}/{ledger_entries_total}"
        )
    return RecoveryVerification(
        passed=not failures,
        current_revision=current_revision,
        expected_revision=expected_revision,
        counts=counts,
        invariants=invariants,
        ledger_entries_total=ledger_entries_total,
        ledger_entries_applied=ledger_entries_applied,
        failures=tuple(failures),
    )
