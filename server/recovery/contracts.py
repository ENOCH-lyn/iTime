from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class RecoveryScenario(StrEnum):
    SMOKE = "smoke"
    LATEST = "latest"
    PITR = "pitr"
    DELETION_REPLAY = "deletion-replay"
    FAULTS = "faults"
    UI = "ui"


@dataclass(frozen=True)
class RecoveryConfig:
    run_id: str
    environment: str
    docker_context: str
    source_database: str
    restore_database: str
    http_origin: str
    guard_value: str
    output_dir: Path

    @property
    def compose_project(self) -> str:
        return f"itime-recovery-{self.run_id}"


@dataclass(frozen=True)
class CleanupManifest:
    run_id: str
    compose_project: str
    source_database: str
    restore_database: str
    source_data_volume: str
    restore_data_volume: str
    repository_volume: str
    source_spool_volume: str
    restore_spool_volume: str
    network: str
    output_dir: Path
