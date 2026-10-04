import stat
from pathlib import Path

import pytest

from performance.contracts import RunConfig
from performance.safety import (
    OUTPUT_ROOT,
    CleanupManifest,
    SafetyError,
    exclusive_run_lock,
    require_safe_cleanup,
    require_safe_run,
)


def config(**changes: object) -> RunConfig:
    values: dict[str, object] = {
        "run_id": "abc12345",
        "environment": "test",
        "http_origin": "http://127.0.0.1:18000",
        "database_name": "itime_perf_abc12345",
        "database_host": "127.0.0.1",
        "guard_value": "itime-performance:abc12345",
        "docker_context": "colima-itime-perf",
        "output_dir": OUTPUT_ROOT / "abc12345",
    }
    values.update(changes)
    return RunConfig(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("environment", "production"),
        ("http_origin", "https://itime.example"),
        ("http_origin", "http://127.0.0.1.evil.example:18000"),
        ("http_origin", "ftp://127.0.0.1:18000"),
        ("database_name", "itime"),
        ("database_host", "db.example"),
        ("guard_value", "itime-performance:other"),
        ("docker_context", "default"),
        ("output_dir", OUTPUT_ROOT / "abc12345" / "extra"),
    ],
)
def test_safe_run_rejects_values_outside_the_isolated_boundary(field: str, value: object):
    with pytest.raises(SafetyError):
        require_safe_run(config(**{field: value}))


def test_safe_run_accepts_matching_loopback_configuration():
    require_safe_run(config())
    require_safe_run(
        config(
            http_origin="http://[::1]:18000",
            database_host="::1",
        )
    )


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
def test_safe_run_requires_8_to_32_lowercase_hex_characters(run_id: str):
    with pytest.raises(SafetyError):
        require_safe_run(
            config(
                run_id=run_id,
                database_name=f"itime_perf_{run_id}",
                guard_value=f"itime-performance:{run_id}",
                output_dir=OUTPUT_ROOT / run_id,
            )
        )


def test_safe_run_rejects_path_aliases_and_symlink_escape(tmp_path: Path):
    alias = OUTPUT_ROOT / ".." / "performance" / "abc12345"
    with pytest.raises(SafetyError):
        require_safe_run(config(output_dir=alias))

    output_root = tmp_path / "output"
    output_root.symlink_to(Path("/", "tmp"), target_is_directory=True)
    with pytest.raises(SafetyError):
        require_safe_run(config(output_dir=output_root / "itime-performance" / "abc12345"))


def test_exclusive_run_lock_is_private_and_rejects_a_second_owner(tmp_path: Path):
    lock_path = tmp_path / "performance.lock"

    with exclusive_run_lock(lock_path):
        assert stat.S_IMODE(lock_path.stat().st_mode) == 0o600
        assert lock_path.read_text().strip().isdigit()
        with pytest.raises(FileExistsError):
            with exclusive_run_lock(lock_path):
                pass

    assert not lock_path.exists()


def test_exclusive_run_lock_rejects_a_missing_parent(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        with exclusive_run_lock(tmp_path / "missing" / "performance.lock"):
            pass


def test_safe_cleanup_requires_every_target_to_match_the_exact_run():
    cleanup = CleanupManifest(
        run_id="abc12345",
        compose_project="itime-perf-abc12345",
        database_name="itime_perf_abc12345",
        output_dir=OUTPUT_ROOT / "abc12345",
    )
    require_safe_cleanup(config(), cleanup)

    invalid = (
        CleanupManifest(
            run_id="deadbeef",
            compose_project=cleanup.compose_project,
            database_name=cleanup.database_name,
            output_dir=cleanup.output_dir,
        ),
        CleanupManifest(
            run_id=cleanup.run_id,
            compose_project="itime-perf-other",
            database_name=cleanup.database_name,
            output_dir=cleanup.output_dir,
        ),
        CleanupManifest(
            run_id=cleanup.run_id,
            compose_project=cleanup.compose_project,
            database_name="itime_perf_other",
            output_dir=cleanup.output_dir,
        ),
        CleanupManifest(
            run_id=cleanup.run_id,
            compose_project=cleanup.compose_project,
            database_name=cleanup.database_name,
            output_dir=OUTPUT_ROOT / "deadbeef",
        ),
    )
    for manifest in invalid:
        with pytest.raises(SafetyError):
            require_safe_cleanup(config(), manifest)
