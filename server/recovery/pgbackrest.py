import json
import re
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_SAFE_COMMAND = re.compile(r"^[a-z][a-z0-9-]*$")
_SENSITIVE_ARGUMENTS = (
    "authorization",
    "api-key",
    "api_key",
    "cipher-pass",
    "cookie",
    "fernet",
    "password",
)
_SENSITIVE_OUTPUT = re.compile(
    r"(?i)(?:repo\d*-cipher-pass|api[_-]?key|authorization|cookie|"
    r"fernet[_-]?key|password)\s*[:=]"
)
_DATABASE_URL = re.compile(
    r"(?i)postgres(?:ql)?(?:\+[a-z0-9]+)?://[^\s]+"
)


class PgBackRestError(RuntimeError):
    """pgBackRest invocation or output could not be used safely."""

    def __init__(
        self,
        message: str,
        *,
        stage: str,
        category: str,
        returncode: int | None = None,
    ) -> None:
        super().__init__(message)
        self.stage = stage
        self.category = category
        self.returncode = returncode


@dataclass(frozen=True)
class PgBackRestBackup:
    label: str
    backup_type: str
    started_at: datetime
    completed_at: datetime
    wal_start: str
    wal_stop: str
    database_size_bytes: int
    repository_size_bytes: int

    def to_public_dict(self) -> dict[str, object]:
        return {
            "label": self.label,
            "type": self.backup_type,
            "started_at": self.started_at.isoformat().replace("+00:00", "Z"),
            "completed_at": self.completed_at.isoformat().replace("+00:00", "Z"),
            "wal_start": self.wal_start,
            "wal_stop": self.wal_stop,
            "database_size_bytes": self.database_size_bytes,
            "repository_size_bytes": self.repository_size_bytes,
        }


def _contains_sensitive_text(value: str) -> bool:
    return bool(
        _SENSITIVE_OUTPUT.search(value)
        or _DATABASE_URL.search(value)
        or "gAAAAA" in value
        or ("eyJ" in value and value.count(".") >= 2)
    )


def _classify_failure(stderr: str) -> str:
    lowered = stderr.casefold()
    if _contains_sensitive_text(stderr):
        return "sensitive_output"
    if "permission denied" in lowered:
        return "permission"
    if "cipher" in lowered or "decrypt" in lowered:
        return "repository_key"
    if "wal segment" in lowered or "archive" in lowered:
        return "wal"
    if "checksum" in lowered or "corrupt" in lowered:
        return "corruption"
    if "config" in lowered or "stanza" in lowered:
        return "configuration"
    return "command_failed"


def _required_mapping(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object")
    return value


def _required_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _required_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value


def parse_backup_info(
    payload: str,
    *,
    stanza: str,
) -> tuple[PgBackRestBackup, ...]:
    if _contains_sensitive_text(payload):
        raise PgBackRestError(
            "pgBackRest info 输出包含敏感内容，已拒绝继续。",
            stage="info",
            category="sensitive_output",
        )
    try:
        parsed = json.loads(payload)
        if not isinstance(parsed, list) or len(parsed) != 1:
            raise ValueError("one stanza is required")
        stanza_info = _required_mapping(parsed[0], "stanza")
        if stanza_info.get("name") != stanza:
            raise ValueError("unexpected stanza")
        status = _required_mapping(stanza_info.get("status"), "status")
        if status.get("code") != 0:
            raise ValueError("stanza status is not ok")
        raw_backups = stanza_info.get("backup")
        if not isinstance(raw_backups, list):
            raise ValueError("backup must be an array")

        backups: list[PgBackRestBackup] = []
        for index, raw_backup in enumerate(raw_backups):
            backup = _required_mapping(raw_backup, f"backup[{index}]")
            timestamp = _required_mapping(
                backup.get("timestamp"),
                f"backup[{index}].timestamp",
            )
            archive = _required_mapping(
                backup.get("archive"),
                f"backup[{index}].archive",
            )
            info = _required_mapping(
                backup.get("info"),
                f"backup[{index}].info",
            )
            repository = _required_mapping(
                info.get("repository"),
                f"backup[{index}].info.repository",
            )
            backup_type = _required_string(
                backup.get("type"),
                f"backup[{index}].type",
            )
            if backup_type not in {"full", "diff", "incr"}:
                raise ValueError("unsupported backup type")
            started = _required_int(
                timestamp.get("start"),
                f"backup[{index}].timestamp.start",
            )
            completed = _required_int(
                timestamp.get("stop"),
                f"backup[{index}].timestamp.stop",
            )
            if completed < started:
                raise ValueError("backup stop precedes start")
            backups.append(
                PgBackRestBackup(
                    label=_required_string(
                        backup.get("label"),
                        f"backup[{index}].label",
                    ),
                    backup_type=backup_type,
                    started_at=datetime.fromtimestamp(started, UTC),
                    completed_at=datetime.fromtimestamp(completed, UTC),
                    wal_start=_required_string(
                        archive.get("start"),
                        f"backup[{index}].archive.start",
                    ),
                    wal_stop=_required_string(
                        archive.get("stop"),
                        f"backup[{index}].archive.stop",
                    ),
                    database_size_bytes=_required_int(
                        info.get("size"),
                        f"backup[{index}].info.size",
                    ),
                    repository_size_bytes=_required_int(
                        repository.get("size"),
                        f"backup[{index}].info.repository.size",
                    ),
                )
            )
    except (json.JSONDecodeError, TypeError, ValueError, OverflowError) as error:
        raise PgBackRestError(
            "pgBackRest info 输出无效。",
            stage="info",
            category="invalid_output",
        ) from error
    return tuple(backups)


@dataclass(frozen=True)
class PgBackRest:
    binary: str
    stanza: str
    config: Path
    command_prefix: tuple[str, ...] = ()

    def command(self, operation: str, *arguments: str) -> list[str]:
        if not _SAFE_COMMAND.fullmatch(operation):
            raise PgBackRestError(
                "pgBackRest 操作名无效。",
                stage="command",
                category="invalid_argument",
            )
        values = (
            *self.command_prefix,
            self.binary,
            str(self.config),
            self.stanza,
            *arguments,
        )
        if any(
            not value
            or "\x00" in value
            or "\n" in value
            or any(marker in value.casefold() for marker in _SENSITIVE_ARGUMENTS)
            for value in values
        ):
            raise PgBackRestError(
                "pgBackRest 命令禁止包含敏感参数。",
                stage=operation,
                category="sensitive_argument",
            )
        return [
            *self.command_prefix,
            self.binary,
            f"--config={self.config}",
            f"--stanza={self.stanza}",
            operation,
            *arguments,
        ]

    def run(
        self,
        operation: str,
        *arguments: str,
        timeout: int = 300,
        env: Mapping[str, str] | None = None,
        cwd: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        command = self.command(operation, *arguments)
        options: dict[str, object] = {
            "capture_output": True,
            "check": True,
            "shell": False,
            "text": True,
            "timeout": timeout,
        }
        if env is not None:
            options["env"] = dict(env)
        if cwd is not None:
            options["cwd"] = cwd
        try:
            result = subprocess.run(  # noqa: S603
                command,
                **options,
            )
        except subprocess.TimeoutExpired as error:
            raise PgBackRestError(
                "pgBackRest 执行超时。",
                stage=operation,
                category="timeout",
            ) from error
        except subprocess.CalledProcessError as error:
            stderr = error.stderr if isinstance(error.stderr, str) else ""
            category = _classify_failure(stderr)
            raise PgBackRestError(
                "pgBackRest 执行失败，详细输出未写入报告。",
                stage=operation,
                category=category,
                returncode=error.returncode,
            ) from error
        except OSError as error:
            raise PgBackRestError(
                "pgBackRest 无法启动。",
                stage=operation,
                category="unavailable",
            ) from error
        if _contains_sensitive_text(result.stdout) or _contains_sensitive_text(
            result.stderr
        ):
            raise PgBackRestError(
                "pgBackRest 输出包含敏感内容，已拒绝继续。",
                stage=operation,
                category="sensitive_output",
                returncode=result.returncode,
            )
        return result

    def info(
        self,
        *,
        timeout: int = 30,
        env: Mapping[str, str] | None = None,
        cwd: Path | None = None,
    ) -> tuple[PgBackRestBackup, ...]:
        result = self.run(
            "info",
            "--output=json",
            timeout=timeout,
            env=env,
            cwd=cwd,
        )
        return parse_backup_info(result.stdout, stanza=self.stanza)
