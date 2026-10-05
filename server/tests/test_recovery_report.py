import asyncio
import json
import subprocess
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from cryptography.fernet import Fernet

from itime.config import Settings
from performance.report import SensitiveArtifactError
from recovery.pgbackrest import (
    PgBackRest,
    PgBackRestError,
    parse_backup_info,
)
from recovery.report import (
    RecoveryEvidence,
    evaluate_recovery_evidence,
    write_recovery_report,
)
from recovery.verify import verify_recovered_database
from tests.conftest import isolated_database, psycopg_url

pytestmark = pytest.mark.integration


@pytest.fixture
def recovery_settings(admin_url: str):
    with isolated_database(admin_url) as database_url:
        yield Settings(
            environment="test",
            database_url=database_url,
            encryption_key=Fernet.generate_key().decode(),
            db_pool_size=2,
            db_max_overflow=0,
        )


def pgbackrest_payload() -> str:
    return json.dumps(
        [
            {
                "name": "itime",
                "status": {"code": 0, "message": "ok"},
                "backup": [
                    {
                        "label": "20261005-120000F",
                        "type": "full",
                        "timestamp": {
                            "start": 1791201600,
                            "stop": 1791201660,
                        },
                        "archive": {
                            "start": "000000010000000000000001",
                            "stop": "000000010000000000000002",
                        },
                        "info": {
                            "size": 1048576,
                            "repository": {"size": 262144},
                        },
                    },
                    {
                        "label": "20261005-120000F_20261005-130000D",
                        "type": "diff",
                        "timestamp": {
                            "start": 1791205200,
                            "stop": 1791205230,
                        },
                        "archive": {
                            "start": "000000010000000000000003",
                            "stop": "000000010000000000000004",
                        },
                        "info": {
                            "size": 131072,
                            "repository": {"size": 32768},
                        },
                    },
                ],
            }
        ]
    )


def test_pgbackrest_builds_argument_arrays_and_parses_info():
    runner = PgBackRest(
        binary="pgbackrest",
        stanza="itime",
        config=Path("/run/secrets/pgbackrest.conf"),
    )
    assert runner.command("backup", "--type=full") == [
        "pgbackrest",
        "--config=/run/secrets/pgbackrest.conf",
        "--stanza=itime",
        "backup",
        "--type=full",
    ]

    backups = parse_backup_info(pgbackrest_payload(), stanza="itime")
    assert [item.label for item in backups] == [
        "20261005-120000F",
        "20261005-120000F_20261005-130000D",
    ]
    assert [item.backup_type for item in backups] == ["full", "diff"]
    assert backups[0].started_at == datetime.fromtimestamp(1791201600, UTC)
    assert backups[1].completed_at == datetime.fromtimestamp(1791205230, UTC)
    assert backups[0].wal_start == "000000010000000000000001"
    assert backups[1].wal_stop == "000000010000000000000004"
    assert backups[0].database_size_bytes == 1048576
    assert backups[1].repository_size_bytes == 32768


@pytest.mark.parametrize(
    "payload",
    [
        "not json",
        "[]",
        '[{"name":"other","status":{"code":0},"backup":[]}]',
        '[{"name":"itime","status":{"code":1},"backup":[]}]',
        '[{"name":"itime","status":{"code":0},"backup":[{"label":"x"}]}]',
        '[{"name":"itime","repo1-cipher-pass":"secret"}]',
    ],
)
def test_pgbackrest_info_rejects_invalid_or_incomplete_payload(payload: str):
    with pytest.raises(PgBackRestError):
        parse_backup_info(payload, stanza="itime")


def test_pgbackrest_execution_uses_no_shell_and_sanitizes_failures(monkeypatch):
    runner = PgBackRest(
        binary="pgbackrest",
        stanza="itime",
        config=Path("/run/secrets/pgbackrest.conf"),
    )
    calls: list[tuple[list[str], dict[str, object]]] = []

    def succeed(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, pgbackrest_payload(), "")

    monkeypatch.setattr(subprocess, "run", succeed)
    assert len(runner.info(timeout=12)) == 2
    assert calls == [
        (
            [
                "pgbackrest",
                "--config=/run/secrets/pgbackrest.conf",
                "--stanza=itime",
                "info",
                "--output=json",
            ],
            {
                "capture_output": True,
                "check": True,
                "shell": False,
                "text": True,
                "timeout": 12,
            },
        )
    ]

    def fail(command, **kwargs):
        raise subprocess.CalledProcessError(
            37,
            command,
            stderr="repo1-cipher-pass=do-not-leak",
        )

    monkeypatch.setattr(subprocess, "run", fail)
    with pytest.raises(PgBackRestError) as caught:
        runner.run("check")
    assert caught.value.category == "sensitive_output"
    assert caught.value.returncode == 37
    assert "do-not-leak" not in str(caught.value)

    def timeout(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(PgBackRestError) as caught:
        runner.run("backup", "--type=full", timeout=1)
    assert caught.value.category == "timeout"

    with pytest.raises(PgBackRestError, match="敏感"):
        runner.command("backup", "--repo1-cipher-pass=secret")


def evidence(**changes: object) -> RecoveryEvidence:
    restore_started_at = datetime(2026, 10, 5, 12, tzinfo=UTC)
    failure_at = restore_started_at - timedelta(hours=1)
    values = {
        "run_id": "abc12345",
        "scenario": "pitr",
        "recovery_target_at": failure_at,
        "failure_at": failure_at,
        "latest_recovered_at": failure_at - timedelta(seconds=3600),
        "restore_started_at": restore_started_at,
        "service_ready_at": restore_started_at + timedelta(seconds=86400),
        "migration_passed": True,
        "ledger_passed": True,
        "finalizer_passed": True,
        "invariants_passed": True,
        "ui_required": True,
        "ui_passed": True,
        "remaining_resources": (),
    }
    values.update(changes)
    return RecoveryEvidence(**values)


def test_recovery_gate_accepts_exact_rpo_rto_bounds_and_rejects_excess():
    exact = evaluate_recovery_evidence(evidence())
    assert exact.passed
    assert exact.rpo_seconds == 3600
    assert exact.rto_seconds == 86400

    too_old = evaluate_recovery_evidence(
        evidence(
            latest_recovered_at=datetime(2026, 10, 5, 9, 59, 59, 999000, UTC),
        )
    )
    assert not too_old.passed
    assert any("RPO" in item for item in too_old.failures)

    too_slow = evaluate_recovery_evidence(
        evidence(
            service_ready_at=datetime(2026, 10, 6, 12, 0, 0, 1000, UTC),
        )
    )
    assert not too_slow.passed
    assert any("RTO" in item for item in too_slow.failures)


@pytest.mark.parametrize(
    "field",
    [
        "migration_passed",
        "ledger_passed",
        "finalizer_passed",
        "invariants_passed",
        "ui_passed",
    ],
)
def test_recovery_gate_requires_each_verification(field: str):
    result = evaluate_recovery_evidence(replace(evidence(), **{field: False}))
    assert not result.passed
    assert any(field.removesuffix("_passed") in item for item in result.failures)


@pytest.mark.parametrize(
    "payload",
    [
        {"repository_key": "secret"},
        {"nested": {"fernet_key": "secret"}},
        {"jwt": "header.payload.signature"},
        {"Cookie": "itime_session=secret"},
        {"database_url": "postgresql://user:secret@localhost/itime"},
        {"value": "recovery-secret-body-sentinel"},
        {"value": "gAAAAABmQ2RkAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"},
    ],
)
def test_recovery_report_rejects_sensitive_artifacts(
    tmp_path: Path,
    payload: object,
):
    with pytest.raises(SensitiveArtifactError):
        write_recovery_report(
            tmp_path,
            manifest={"run_id": "abc12345", "extra": payload},
            backup={"label": "20261005-120000F"},
            restore={"verification": "passed"},
            evidence=evidence(),
        )
    assert list(tmp_path.iterdir()) == []


def test_recovery_report_writes_all_outputs_atomically_and_rejects_symlink(
    tmp_path: Path,
):
    result = write_recovery_report(
        tmp_path,
        manifest={
            "run_id": "abc12345",
            "scenario": "pitr",
            "commit": "0123456789abcdef",
        },
        backup={
            "label": "20261005-120000F",
            "type": "full",
            "wal_start": "000000010000000000000001",
            "wal_stop": "000000010000000000000002",
        },
        restore={
            "target": "2026-10-05T11:00:00Z",
            "finalizer": {"sessions_revoked": 2},
            "invariants": {"orphan_tasks": 0},
        },
        evidence=evidence(),
    )
    assert result.passed
    assert {
        "manifest.json",
        "backup.json",
        "restore.json",
        "summary.json",
        "report.md",
    } == {item.name for item in tmp_path.iterdir()}
    assert json.loads((tmp_path / "summary.json").read_text())["passed"] is True
    assert "RPO: 3600s" in (tmp_path / "report.md").read_text()
    assert not list(tmp_path.glob(".*.tmp"))
    assert all(
        item.stat().st_mode & 0o777 == 0o600
        for item in tmp_path.iterdir()
    )

    outside = tmp_path.parent / f"{tmp_path.name}-outside.json"
    outside.write_text("unchanged")
    (tmp_path / "summary.json").unlink()
    (tmp_path / "summary.json").symlink_to(outside)
    with pytest.raises(ValueError, match="符号链接"):
        write_recovery_report(
            tmp_path,
            manifest={"run_id": "abc12345"},
            backup={"label": "20261005-120000F"},
            restore={},
            evidence=evidence(),
        )
    assert outside.read_text() == "unchanged"


def test_verify_recovered_database_checks_schema_runtime_and_expected_counts(
    recovery_settings: Settings,
):
    result = asyncio.run(
        verify_recovered_database(
            recovery_settings,
            expected_counts={"users": 0, "tasks": 0},
            ledger_entries_total=0,
            ledger_entries_applied=0,
        )
    )
    assert result.passed
    assert result.current_revision == result.expected_revision
    assert all(value == 0 for value in result.invariants.values())

    database_url = recovery_settings.database_url.get_secret_value()
    with psycopg.connect(psycopg_url(database_url)) as connection:
        owner_id = uuid4()
        connection.execute(
            """
            INSERT INTO users (id, email, password_hash, status)
            VALUES (%s, %s, 'test-password-hash', 'active')
            """,
            (owner_id, f"{owner_id}@example.com"),
        )
        connection.execute(
            """
            INSERT INTO device_sessions (
                id, user_id, token_hash, auth_epoch, device_name,
                created_at, last_seen_at, expires_at
            ) VALUES (%s, %s, %s, 0, 'active device', now(), now(), %s)
            """,
            (uuid4(), owner_id, "7" * 64, datetime.now(UTC) + timedelta(days=1)),
        )
        connection.commit()

    failed = asyncio.run(
        verify_recovered_database(
            recovery_settings,
            expected_counts={"users": 0},
            ledger_entries_total=1,
            ledger_entries_applied=0,
        )
    )
    assert not failed.passed
    assert failed.invariants["unrevoked_sessions"] == 1
    assert "users count expected 0, got 1" in failed.failures
    assert "deletion ledger applied 0/1" in failed.failures
