import json
import os
from pathlib import Path

import pytest

import recovery.run as recovery_run
from itime.config import Settings
from performance.seed import require_safe_seed_settings
from recovery.contracts import RecoveryConfig
from recovery.report import RecoveryGateResult


def state(tmp_path: Path) -> recovery_run.RunState:
    config = RecoveryConfig(
        run_id="abc12345",
        environment="test",
        docker_context="colima-itime-recovery",
        source_database="itime_backup_abc12345",
        restore_database="itime_restore_abc12345",
        http_origin="http://127.0.0.1:18100",
        guard_value="itime-recovery:abc12345",
        output_dir=tmp_path,
    )
    return recovery_run.RunState(
        config=config,
        scenario="smoke",
        env={},
    )


def test_recovery_lifecycle_runs_every_stage_and_cleanup_in_order(tmp_path: Path):
    calls: list[str] = []

    def execute(run_state, stage):
        calls.append(stage)
        if stage == "build-report":
            return RecoveryGateResult(True, 1.0, 2.0, ())
        if stage == "verify-clean":
            return ()
        return None

    result = recovery_run.execute_lifecycle(state(tmp_path), execute=execute)

    assert result.passed
    assert result.remaining_resources == ()
    assert calls == [
        *recovery_run.STAGES,
        "cleanup",
        "verify-clean",
    ]


@pytest.mark.parametrize(
    "failing_stage",
    ["prepare-secrets", "backup", "restore", "finalize", "build-report"],
)
def test_recovery_lifecycle_always_cleans_and_redacts_errors(
    tmp_path: Path,
    failing_stage: str,
):
    calls: list[str] = []

    def execute(run_state, stage):
        calls.append(stage)
        if stage == failing_stage:
            raise RuntimeError(
                "postgresql://itime:secret@localhost/db "
                "repo1-cipher-pass=secret"
            )
        if stage == "verify-clean":
            return ()
        return None

    result = recovery_run.execute_lifecycle(state(tmp_path), execute=execute)

    assert not result.passed
    assert calls[-2:] == ["cleanup", "verify-clean"]
    assert "secret" not in (result.error or "")
    partial = json.loads(
        (tmp_path / "partial-report.json").read_text()
    )
    assert partial["failed_stage"] == failing_stage
    assert partial["error"] == "错误详情包含敏感材料，已拒绝写入。"


def test_recovery_cli_contract_accepts_planned_commands():
    parser = recovery_run.build_parser()
    for scenario in (
        "smoke",
        "latest",
        "pitr",
        "deletion-replay",
        "faults",
        "ui",
    ):
        parsed = parser.parse_args(["scenario", scenario])
        assert parsed.command == "scenario"
        assert parsed.scenario == scenario
    assert parser.parse_args(["baseline"]).command == "baseline"
    assert parser.parse_args(["verify-clean"]).command == "verify-clean"
    cleanup = parser.parse_args(["cleanup", "--run-id", "abc12345"])
    assert cleanup.command == "cleanup"
    assert cleanup.run_id == "abc12345"


def test_performance_seed_accepts_only_the_isolated_recovery_source(
    settings,
):
    source = Settings(
        environment="test",
        database_url=(
            "postgresql+psycopg://itime:test@127.0.0.1/"
            "itime_backup_abc12345"
        ),
        encryption_key=settings.encryption_key,
    )
    require_safe_seed_settings(source)
    with pytest.raises(ValueError):
        require_safe_seed_settings(
            Settings(
                environment="test",
                database_url=(
                    "postgresql+psycopg://itime:test@127.0.0.1/itime"
                ),
                encryption_key=settings.encryption_key,
            )
        )


@pytest.mark.recovery
@pytest.mark.skipif(
    os.environ.get("ITIME_RUN_RECOVERY") != "1",
    reason="设置 ITIME_RUN_RECOVERY=1 执行真实备份恢复",
)
def test_real_full_backup_and_latest_restore():
    result = recovery_run.run_recovery_scenario("smoke")
    assert result.passed, result.error
    assert result.remaining_resources == ()


@pytest.mark.recovery
@pytest.mark.skipif(
    os.environ.get("ITIME_RUN_RECOVERY") != "1",
    reason="设置 ITIME_RUN_RECOVERY=1 执行真实备份恢复",
)
def test_real_point_in_time_restore_excludes_later_commit():
    result = recovery_run.run_recovery_scenario("pitr")
    assert result.passed, result.error
    assert result.remaining_resources == ()


@pytest.mark.recovery
@pytest.mark.skipif(
    os.environ.get("ITIME_RUN_RECOVERY") != "1",
    reason="设置 ITIME_RUN_RECOVERY=1 执行真实备份恢复",
)
def test_real_pitr_replays_deletions_newer_than_restore_target():
    result = recovery_run.run_recovery_scenario("deletion-replay")
    assert result.passed, result.error
    assert result.remaining_resources == ()
    restore = json.loads((result.run_dir / "restore.json").read_text())
    assert restore["replay"]["accounts_purged"] == 1
    assert restore["replay"]["objects_purged"] >= 4


@pytest.mark.recovery
@pytest.mark.skipif(
    os.environ.get("ITIME_RUN_RECOVERY") != "1",
    reason="设置 ITIME_RUN_RECOVERY=1 执行真实备份恢复",
)
def test_real_faults_are_rejected_before_clean_restore():
    result = recovery_run.run_recovery_scenario("faults")
    assert result.passed, result.error
    assert result.remaining_resources == ()
    restore = json.loads((result.run_dir / "restore.json").read_text())
    assert set(restore["fault_results"]) == {
        "corrupted_backup",
        "disk_full",
        "missing_wal",
        "occupied_restore_volume",
        "stale_ledger",
        "truncated_ledger",
        "wrong_ai_encryption_key",
        "wrong_calendar_subscription_encryption_key",
        "wrong_encryption_key",
        "wrong_export_encryption_key",
        "wrong_ledger_key",
        "wrong_repository_key",
    }
    assert all(restore["fault_results"].values())
