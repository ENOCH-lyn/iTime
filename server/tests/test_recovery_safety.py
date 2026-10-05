import stat
from dataclasses import fields, replace
from pathlib import Path

import pytest

from recovery import safety
from recovery.contracts import CleanupManifest, RecoveryConfig, RecoveryScenario
from recovery.safety import (
    OUTPUT_ROOT,
    RecoverySafetyError,
    exclusive_run_lock,
    expected_cleanup_manifest,
    require_safe_cleanup,
    require_safe_run,
)


def config(**changes: object) -> RecoveryConfig:
    values: dict[str, object] = {
        "run_id": "abc12345",
        "environment": "test",
        "docker_context": "colima-itime-recovery",
        "source_database": "itime_backup_abc12345",
        "restore_database": "itime_restore_abc12345",
        "http_origin": "http://127.0.0.1:18100",
        "guard_value": "itime-recovery:abc12345",
        "output_dir": OUTPUT_ROOT / "abc12345",
    }
    values.update(changes)
    return RecoveryConfig(**values)  # type: ignore[arg-type]


def test_recovery_scenarios_have_stable_cli_values():
    assert {scenario.value for scenario in RecoveryScenario} == {
        "smoke",
        "latest",
        "pitr",
        "deletion-replay",
        "faults",
        "ui",
    }


def test_safe_recovery_accepts_matching_loopback_configuration():
    require_safe_run(config())
    require_safe_run(config(http_origin="https://localhost:18100"))
    require_safe_run(config(http_origin="http://[::1]:18100"))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("environment", "production"),
        ("docker_context", "default"),
        ("source_database", "itime"),
        ("restore_database", "itime_backup_abc12345"),
        ("restore_database", "itime"),
        ("http_origin", "https://itime.example"),
        ("http_origin", "http://127.0.0.1.evil.example:18100"),
        ("http_origin", "ftp://127.0.0.1:18100"),
        ("http_origin", "http://user@127.0.0.1:18100"),
        ("http_origin", "http://127.0.0.1:18100/api"),
        ("http_origin", "http://127.0.0.1:18100?token=secret"),
        ("http_origin", "http://127.0.0.1:not-a-port"),
        ("guard_value", "itime-recovery:other"),
        ("output_dir", OUTPUT_ROOT / "abc12345" / "extra"),
    ],
)
def test_safe_recovery_rejects_values_outside_isolated_boundary(
    field: str,
    value: object,
):
    with pytest.raises(RecoverySafetyError):
        require_safe_run(config(**{field: value}))


@pytest.mark.parametrize(
    "run_id",
    [
        "abc1234",
        "abc1234567890abcdef1234567890abcd",
        "ABC12345",
        "abcdefg!",
        "abc12345/other",
    ],
)
def test_safe_recovery_requires_8_to_32_lowercase_hex_characters(run_id: str):
    with pytest.raises(RecoverySafetyError):
        require_safe_run(
            config(
                run_id=run_id,
                source_database=f"itime_backup_{run_id}",
                restore_database=f"itime_restore_{run_id}",
                guard_value=f"itime-recovery:{run_id}",
                output_dir=OUTPUT_ROOT / run_id,
            )
        )


def test_safe_recovery_rejects_path_aliases_and_symlink_escape(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    alias = OUTPUT_ROOT / ".." / "itime-recovery" / "abc12345"
    with pytest.raises(RecoverySafetyError):
        require_safe_run(config(output_dir=alias))

    output_root = tmp_path / "output"
    output_root.mkdir()
    escaped = tmp_path / "escaped"
    escaped.mkdir()
    run_output = output_root / "abc12345"
    run_output.symlink_to(escaped, target_is_directory=True)
    monkeypatch.setattr(safety, "OUTPUT_ROOT", output_root)

    with pytest.raises(RecoverySafetyError):
        require_safe_run(config(output_dir=run_output))

    output_root_alias = tmp_path / "output-alias"
    output_root_alias.symlink_to(escaped, target_is_directory=True)
    monkeypatch.setattr(safety, "OUTPUT_ROOT", output_root_alias)
    with pytest.raises(RecoverySafetyError):
        require_safe_run(config(output_dir=output_root_alias / "abc12345"))


def test_exclusive_recovery_lock_is_private_and_rejects_second_owner(tmp_path: Path):
    lock_path = tmp_path / "recovery.lock"

    with exclusive_run_lock(lock_path):
        assert stat.S_IMODE(lock_path.stat().st_mode) == 0o600
        assert lock_path.read_text().strip().isdigit()
        with pytest.raises(FileExistsError):
            with exclusive_run_lock(lock_path):
                pass

    assert not lock_path.exists()


def test_exclusive_recovery_lock_rejects_missing_parent(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        with exclusive_run_lock(tmp_path / "missing" / "recovery.lock"):
            pass


def test_safe_cleanup_requires_every_resource_to_match_exact_run():
    run_config = config()
    manifest = expected_cleanup_manifest(run_config)
    require_safe_cleanup(run_config, manifest)

    replacements: dict[str, object] = {
        "run_id": "deadbeef",
        "compose_project": "itime-recovery-deadbeef",
        "source_database": "itime_backup_deadbeef",
        "restore_database": "itime_restore_deadbeef",
        "source_data_volume": "itime-recovery-deadbeef_source_data",
        "restore_data_volume": "itime-recovery-deadbeef_restore_data",
        "repository_volume": "itime-recovery-deadbeef_repository",
        "source_spool_volume": "itime-recovery-deadbeef_spool",
        "restore_spool_volume": "itime-recovery-deadbeef_restore_spool",
        "fault_repository_volume": "itime-recovery-deadbeef_fault_repository",
        "fault_restore_data_volume": "itime-recovery-deadbeef_fault_restore_data",
        "network": "itime-recovery-deadbeef_default",
        "host_network": "itime-recovery-deadbeef_host_access",
        "output_dir": OUTPUT_ROOT / "deadbeef",
    }
    assert {field.name for field in fields(CleanupManifest)} == set(replacements)

    for field_name, invalid_value in replacements.items():
        with pytest.raises(RecoverySafetyError):
            require_safe_cleanup(
                run_config,
                replace(manifest, **{field_name: invalid_value}),
            )
