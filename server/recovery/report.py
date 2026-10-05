import json
import os
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass, is_dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from performance.report import (
    SensitiveArtifactError,
    assert_no_sensitive_artifacts,
)

MAX_RPO_SECONDS = 3600.0
MAX_RTO_SECONDS = 86400.0
_RECOVERY_SENSITIVE_KEYS = frozenset(
    {
        "database_url",
        "fernet_key",
        "jwt",
        "repo1_cipher_pass",
        "repository_key",
    }
)
_DATABASE_URL = re.compile(
    r"(?i)postgres(?:ql)?(?:\+[a-z0-9]+)?://[^\s]+"
)
_CIPHER_PASS = re.compile(r"(?i)repo\d*-cipher-pass\s*[:=]")
_BODY_SENTINEL = "recovery-secret-body-sentinel"


@dataclass(frozen=True)
class RecoveryEvidence:
    run_id: str
    scenario: str
    recovery_target_at: datetime
    failure_at: datetime
    latest_recovered_at: datetime
    restore_started_at: datetime
    service_ready_at: datetime
    migration_passed: bool
    ledger_passed: bool
    finalizer_passed: bool
    invariants_passed: bool
    ui_required: bool
    ui_passed: bool
    remaining_resources: tuple[str, ...]


@dataclass(frozen=True)
class RecoveryGateResult:
    passed: bool
    rpo_seconds: float
    rto_seconds: float
    failures: tuple[str, ...]

    def to_public_dict(self) -> dict[str, object]:
        return asdict(self)


def _utc(value: datetime, field: str) -> datetime:
    if value.tzinfo is None:
        raise ValueError(f"{field} must include a UTC offset")
    return value.astimezone(UTC)


def evaluate_recovery_evidence(
    evidence: RecoveryEvidence,
) -> RecoveryGateResult:
    failure_at = _utc(evidence.failure_at, "failure_at")
    latest_recovered_at = _utc(
        evidence.latest_recovered_at,
        "latest_recovered_at",
    )
    restore_started_at = _utc(
        evidence.restore_started_at,
        "restore_started_at",
    )
    service_ready_at = _utc(
        evidence.service_ready_at,
        "service_ready_at",
    )
    _utc(evidence.recovery_target_at, "recovery_target_at")
    rpo_seconds = (failure_at - latest_recovered_at).total_seconds()
    rto_seconds = (service_ready_at - restore_started_at).total_seconds()
    failures: list[str] = []
    if rpo_seconds < 0:
        failures.append("RPO evidence is temporally inconsistent")
    elif rpo_seconds > MAX_RPO_SECONDS:
        failures.append(
            f"RPO {rpo_seconds:g}s exceeds {MAX_RPO_SECONDS:g}s"
        )
    if rto_seconds < 0:
        failures.append("RTO evidence is temporally inconsistent")
    elif rto_seconds > MAX_RTO_SECONDS:
        failures.append(
            f"RTO {rto_seconds:g}s exceeds {MAX_RTO_SECONDS:g}s"
        )
    gates = (
        ("migration", evidence.migration_passed),
        ("ledger", evidence.ledger_passed),
        ("finalizer", evidence.finalizer_passed),
        ("invariants", evidence.invariants_passed),
    )
    failures.extend(f"{name} gate failed" for name, passed in gates if not passed)
    if evidence.ui_required and not evidence.ui_passed:
        failures.append("ui gate failed")
    if evidence.remaining_resources:
        failures.append(
            "recovery resources remain: "
            + ", ".join(sorted(evidence.remaining_resources))
        )
    return RecoveryGateResult(
        passed=not failures,
        rpo_seconds=rpo_seconds,
        rto_seconds=rto_seconds,
        failures=tuple(failures),
    )


def _jsonable(value: object) -> object:
    if is_dataclass(value) and not isinstance(value, type):
        return _jsonable(asdict(value))
    if isinstance(value, Mapping):
        return {
            str(key): _jsonable(nested)
            for key, nested in value.items()
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(nested) for nested in value]
    if isinstance(value, datetime):
        return _utc(value, "datetime").isoformat().replace("+00:00", "Z")
    if isinstance(value, Path):
        return str(value)
    return value


def assert_no_sensitive_recovery_artifacts(
    value: object,
    *,
    path: str = "root",
) -> None:
    assert_no_sensitive_artifacts(value, path=path)
    if isinstance(value, Mapping):
        for key, nested in value.items():
            normalized = str(key).casefold().replace("-", "_")
            if normalized in _RECOVERY_SENSITIVE_KEYS:
                raise SensitiveArtifactError(
                    f"敏感字段禁止写入恢复报告：{path}.{key}"
                )
            assert_no_sensitive_recovery_artifacts(
                nested,
                path=f"{path}.{key}",
            )
        return
    if isinstance(value, (list, tuple, set, frozenset)):
        for index, nested in enumerate(value):
            assert_no_sensitive_recovery_artifacts(
                nested,
                path=f"{path}[{index}]",
            )
        return
    if isinstance(value, str) and (
        _DATABASE_URL.search(value)
        or _CIPHER_PASS.search(value)
        or _BODY_SENTINEL in value
    ):
        raise SensitiveArtifactError(
            f"敏感内容禁止写入恢复报告：{path}"
        )


def _validate_output_paths(output_dir: Path, names: tuple[str, ...]) -> None:
    if output_dir.is_symlink():
        raise ValueError("恢复报告目录不能是符号链接")
    output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    resolved_dir = output_dir.resolve(strict=True)
    for name in names:
        target = output_dir / name
        if target.is_symlink():
            raise ValueError(f"恢复报告目标不能是符号链接：{name}")
        if target.parent.resolve(strict=True) != resolved_dir:
            raise ValueError("恢复报告目标逃逸输出目录")


def _write_atomic(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    if temporary.exists() or temporary.is_symlink():
        raise ValueError(f"恢复报告临时目标已存在：{temporary.name}")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(temporary, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _markdown(
    manifest: Mapping[str, Any],
    evidence: RecoveryEvidence,
    result: RecoveryGateResult,
) -> str:
    lines = [
        f"# iTime recovery report: {evidence.scenario}",
        "",
        f"- Run: {evidence.run_id}",
        f"- Commit: {manifest.get('commit', 'unknown')}",
        f"- Result: {'PASS' if result.passed else 'FAIL'}",
        f"- RPO: {result.rpo_seconds:g}s",
        f"- RTO: {result.rto_seconds:g}s",
        "",
        "## Gates",
        "",
        f"- Migration: {'pass' if evidence.migration_passed else 'fail'}",
        f"- Deletion ledger: {'pass' if evidence.ledger_passed else 'fail'}",
        f"- Finalizer: {'pass' if evidence.finalizer_passed else 'fail'}",
        f"- Invariants: {'pass' if evidence.invariants_passed else 'fail'}",
        (
            f"- UI: {'pass' if evidence.ui_passed else 'fail'}"
            if evidence.ui_required
            else "- UI: not required for this scenario"
        ),
        "",
        "## Failures",
        "",
    ]
    lines.extend(f"- {failure}" for failure in result.failures)
    if not result.failures:
        lines.append("- None")
    return "\n".join(lines) + "\n"


def write_recovery_report(
    output_dir: Path,
    *,
    manifest: Mapping[str, object],
    backup: Mapping[str, object],
    restore: Mapping[str, object],
    evidence: RecoveryEvidence,
) -> RecoveryGateResult:
    result = evaluate_recovery_evidence(evidence)
    names = (
        "manifest.json",
        "backup.json",
        "restore.json",
        "summary.json",
        "report.md",
    )
    public_manifest = _jsonable(dict(manifest))
    public_backup = _jsonable(dict(backup))
    public_restore = _jsonable(
        {
            **dict(restore),
            "evidence": evidence,
            "gate": result,
        }
    )
    summary = _jsonable(result)
    artifacts = {
        "manifest": public_manifest,
        "backup": public_backup,
        "restore": public_restore,
        "summary": summary,
    }
    assert_no_sensitive_recovery_artifacts(artifacts)
    markdown = _markdown(
        public_manifest if isinstance(public_manifest, Mapping) else {},
        evidence,
        result,
    )
    assert_no_sensitive_recovery_artifacts(markdown)
    _validate_output_paths(output_dir, names)
    payloads = {
        "manifest.json": json.dumps(
            public_manifest,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        "backup.json": json.dumps(
            public_backup,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        "restore.json": json.dumps(
            public_restore,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        "summary.json": json.dumps(
            summary,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        "report.md": markdown,
    }
    for name in names:
        _write_atomic(output_dir / name, payloads[name])
    return result
