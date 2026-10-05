import json
import os
import re
from dataclasses import asdict
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select, text

from itime.account_deletion.models import AccountDeletion
from itime.calendar.models import CalendarSeries, CalendarSource
from itime.config import Settings
from itime.database import Database
from itime.execution.models import DifficultyRecord
from itime.review.models import Memory
from itime.tasks.models import Task
from recovery.contracts import LedgerEntry, RecoveryLedger

SCHEMA_VERSION = 1
DIGEST = re.compile(r"^[0-9a-f]{64}$")
ACCOUNT_STATES = frozenset({"pending", "processing", "completed"})
OBJECT_STATES = frozenset({"trashed", "purged"})
LIFECYCLE_MODELS = (
    ("calendar-series", CalendarSeries),
    ("calendar-source", CalendarSource),
    ("difficulty", DifficultyRecord),
    ("memory", Memory),
    ("task", Task),
)
LEDGER_FIELDS = {
    "schema_version",
    "generated_at",
    "source_system_identifier",
    "previous_digest",
    "entries",
}
ENTRY_FIELDS = {
    "kind",
    "subject",
    "owner_id",
    "state",
    "deleted_at",
    "purge_after",
    "purged_at",
    "version",
}


class LedgerError(RuntimeError):
    """删除台账生成、加密或验证失败。"""


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise LedgerError("删除台账时间必须包含时区")
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace(
        "+00:00",
        "Z",
    )


def _parse_timestamp(value: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError("invalid timestamp")
    parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    if parsed.tzinfo is None:
        raise ValueError("invalid timestamp")
    return parsed


def _account_entry(item: AccountDeletion) -> LedgerEntry:
    return LedgerEntry(
        kind="account",
        subject=item.subject_digest,
        owner_id=None,
        state=item.status,
        deleted_at=_timestamp(item.requested_at),
        purge_after=_timestamp(item.purge_after),
        purged_at=(
            _timestamp(item.completed_at)
            if item.completed_at is not None
            else None
        ),
        version=1,
    )


def _lifecycle_entry(kind: str, item: Any) -> LedgerEntry:
    if item.deleted_at is None or item.purge_after is None:
        raise LedgerError("删除对象缺少生命周期时间")
    return LedgerEntry(
        kind=kind,
        subject=str(item.id),
        owner_id=str(item.owner_id),
        state="purged" if item.purged_at is not None else "trashed",
        deleted_at=_timestamp(item.deleted_at),
        purge_after=_timestamp(item.purge_after),
        purged_at=(
            _timestamp(item.purged_at)
            if item.purged_at is not None
            else None
        ),
        version=item.version,
    )


async def build_ledger_snapshot(
    settings: Settings,
    *,
    generated_at: datetime,
    previous_digest: str | None = None,
) -> RecoveryLedger:
    database = Database(settings)
    try:
        async with database.transaction() as session:
            await session.execute(
                text(
                    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, "
                    "READ ONLY, DEFERRABLE"
                )
            )
            system_identifier = await session.scalar(
                text("SELECT system_identifier::text FROM pg_control_system()")
            )
            account_rows = await session.scalars(
                select(AccountDeletion).where(
                    AccountDeletion.status.in_(ACCOUNT_STATES)
                )
            )
            entries = [_account_entry(item) for item in account_rows]
            for kind, model in LIFECYCLE_MODELS:
                rows = await session.scalars(
                    select(model).where(model.deleted_at.is_not(None))
                )
                entries.extend(_lifecycle_entry(kind, item) for item in rows)
    finally:
        await database.close()

    ledger = RecoveryLedger(
        schema_version=SCHEMA_VERSION,
        generated_at=_timestamp(generated_at),
        source_system_identifier=str(system_identifier),
        previous_digest=previous_digest,
        entries=tuple(sorted(entries, key=lambda item: (item.kind, item.subject))),
    )
    _validate_ledger(ledger)
    return ledger


def canonical_payload(ledger: RecoveryLedger) -> bytes:
    return json.dumps(
        asdict(ledger),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def ledger_digest(ledger: RecoveryLedger) -> str:
    return sha256(canonical_payload(ledger)).hexdigest()


def _validate_entry(entry: LedgerEntry) -> None:
    deleted_at = _parse_timestamp(entry.deleted_at)
    purge_after = _parse_timestamp(entry.purge_after)
    purged_at = (
        _parse_timestamp(entry.purged_at)
        if entry.purged_at is not None
        else None
    )
    if purge_after < deleted_at or (purged_at is not None and purged_at < deleted_at):
        raise ValueError("invalid lifecycle order")
    if isinstance(entry.version, bool) or entry.version < 1:
        raise ValueError("invalid version")

    if entry.kind == "account":
        if (
            entry.owner_id is not None
            or not DIGEST.fullmatch(entry.subject)
            or entry.state not in ACCOUNT_STATES
            or (entry.state == "completed") != (entry.purged_at is not None)
        ):
            raise ValueError("invalid account entry")
        return

    if entry.kind not in {kind for kind, _ in LIFECYCLE_MODELS}:
        raise ValueError("invalid lifecycle kind")
    if entry.state not in OBJECT_STATES:
        raise ValueError("invalid lifecycle state")
    if (entry.state == "purged") != (entry.purged_at is not None):
        raise ValueError("invalid lifecycle purge state")
    UUID(entry.subject)
    if entry.owner_id is None:
        raise ValueError("missing lifecycle owner")
    UUID(entry.owner_id)


def _validate_ledger(ledger: RecoveryLedger) -> None:
    if ledger.schema_version != SCHEMA_VERSION:
        raise ValueError("unsupported schema")
    _parse_timestamp(ledger.generated_at)
    if not ledger.source_system_identifier.isdigit():
        raise ValueError("invalid source system identifier")
    if (
        ledger.previous_digest is not None
        and not DIGEST.fullmatch(ledger.previous_digest)
    ):
        raise ValueError("invalid previous digest")

    keys = [(entry.kind, entry.subject) for entry in ledger.entries]
    if keys != sorted(keys) or len(keys) != len(set(keys)):
        raise ValueError("entries must be sorted and unique")
    for entry in ledger.entries:
        _validate_entry(entry)


def validate_ledger(ledger: RecoveryLedger) -> None:
    _validate_ledger(ledger)


def _ledger_from_payload(payload: bytes) -> RecoveryLedger:
    raw = json.loads(payload)
    if not isinstance(raw, dict) or set(raw) != LEDGER_FIELDS:
        raise ValueError("invalid ledger fields")
    raw_entries = raw["entries"]
    if not isinstance(raw_entries, list):
        raise ValueError("invalid ledger entries")

    entries: list[LedgerEntry] = []
    for raw_entry in raw_entries:
        if not isinstance(raw_entry, dict) or set(raw_entry) != ENTRY_FIELDS:
            raise ValueError("invalid ledger entry fields")
        entries.append(LedgerEntry(**raw_entry))

    ledger = RecoveryLedger(
        schema_version=raw["schema_version"],
        generated_at=raw["generated_at"],
        source_system_identifier=raw["source_system_identifier"],
        previous_digest=raw["previous_digest"],
        entries=tuple(entries),
    )
    _validate_ledger(ledger)
    return ledger


def _cipher(key: bytes) -> Fernet:
    try:
        return Fernet(key)
    except (TypeError, ValueError) as error:
        raise LedgerError("删除台账密钥无效") from error


def write_encrypted_snapshot(
    ledger: RecoveryLedger,
    path: Path,
    key: bytes,
) -> Path:
    try:
        _validate_ledger(ledger)
        encrypted = _cipher(key).encrypt(canonical_payload(ledger))
    except (TypeError, ValueError) as error:
        raise LedgerError(f"无法生成删除台账 {path.name}") from error

    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encrypted)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
        directory = os.open(
            path.parent,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
        )
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except OSError as error:
        temporary.unlink(missing_ok=True)
        raise LedgerError(f"无法写入删除台账 {path.name}") from error
    return path


def read_encrypted_snapshot(
    path: Path,
    key: bytes,
    *,
    expected_previous_digest: str | None,
) -> RecoveryLedger:
    try:
        ledger = _ledger_from_payload(_cipher(key).decrypt(path.read_bytes()))
    except (InvalidToken, OSError, TypeError, ValueError) as error:
        raise LedgerError(f"无法读取删除台账 {path.name}") from error
    if ledger.previous_digest != expected_previous_digest:
        raise LedgerError(f"删除台账链校验失败: {path.name}")
    return ledger
