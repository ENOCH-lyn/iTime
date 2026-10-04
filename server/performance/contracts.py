from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class Scenario(StrEnum):
    SMOKE = "smoke"
    STEADY = "steady"
    PEAK = "peak"
    HISTORY = "history"
    AI_ISOLATION = "ai-isolation"
    QUEUES = "queues"
    MAINTENANCE = "maintenance"
    SOAK = "soak"
    SATURATION = "saturation"
    UI_LOAD = "ui-load"


@dataclass(frozen=True)
class RunConfig:
    run_id: str
    environment: str
    http_origin: str
    database_name: str
    database_host: str
    guard_value: str
    docker_context: str
    output_dir: Path

    @property
    def compose_project(self) -> str:
        return f"itime-perf-{self.run_id}"


@dataclass(frozen=True)
class ScenarioConfig:
    name: Scenario
    users: int
    spawn_rate: float
    duration_seconds: int
    dataset: str


SCENARIOS = {
    Scenario.SMOKE: ScenarioConfig(Scenario.SMOKE, 2, 2, 30, "standard"),
    Scenario.STEADY: ScenarioConfig(Scenario.STEADY, 20, 4, 900, "standard"),
    Scenario.PEAK: ScenarioConfig(Scenario.PEAK, 50, 10, 300, "standard"),
    Scenario.HISTORY: ScenarioConfig(Scenario.HISTORY, 10, 2, 600, "history"),
    Scenario.SOAK: ScenarioConfig(Scenario.SOAK, 20, 4, 3600, "standard"),
}
