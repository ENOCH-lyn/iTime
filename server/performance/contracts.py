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


@dataclass(frozen=True)
class SeedProfile:
    name: str
    accounts: int
    active_accounts: int
    tasks_per_active: int
    sync_events_per_active: int
    history_accounts: int = 0
    calendar_series_per_account: int = 0
    subscription_events_per_account: int = 0
    conversation_messages_per_account: int = 0

    def __post_init__(self) -> None:
        values = (
            self.accounts,
            self.active_accounts,
            self.tasks_per_active,
            self.sync_events_per_active,
            self.history_accounts,
            self.calendar_series_per_account,
            self.subscription_events_per_account,
            self.conversation_messages_per_account,
        )
        if any(value < 0 for value in values):
            raise ValueError("性能数据数量不能为负数")
        if self.accounts < 1 or self.active_accounts > self.accounts:
            raise ValueError("活动账号数量必须位于总账号范围内")
        if self.tasks_per_active > 1000:
            raise ValueError("单账号任务日程不能超过当前 1000 条边界")
        if self.subscription_events_per_account > 5000:
            raise ValueError("单账号订阅事件不能超过当前 5000 条边界")

    @classmethod
    def standard(
        cls,
        *,
        accounts: int = 100,
        active_accounts: int = 50,
        tasks_per_active: int = 300,
        sync_events_per_active: int = 3000,
    ) -> "SeedProfile":
        return cls(
            "standard",
            accounts,
            active_accounts,
            tasks_per_active,
            sync_events_per_active,
        )

    @classmethod
    def history(
        cls,
        *,
        accounts: int = 5,
        tasks_per_account: int = 1000,
        calendar_series_per_account: int = 1000,
        subscription_events_per_account: int = 5000,
        conversation_messages_per_account: int = 5000,
        sync_events_per_account: int = 10000,
    ) -> "SeedProfile":
        return cls(
            "history",
            accounts,
            accounts,
            tasks_per_account,
            sync_events_per_account,
            history_accounts=accounts,
            calendar_series_per_account=calendar_series_per_account,
            subscription_events_per_account=subscription_events_per_account,
            conversation_messages_per_account=conversation_messages_per_account,
        )


@dataclass(frozen=True)
class AccountCredential:
    email: str
    password: str
    dataset: str


@dataclass(frozen=True)
class SeedManifest:
    profile: str
    seed: int
    generated_at: str
    accounts: int
    active_accounts: int
    counts: dict[str, int]
    credentials: tuple[AccountCredential, ...]

    def to_public_dict(self) -> dict[str, object]:
        return {
            "profile": self.profile,
            "seed": self.seed,
            "generated_at": self.generated_at,
            "accounts": self.accounts,
            "active_accounts": self.active_accounts,
            "counts": dict(sorted(self.counts.items())),
        }


@dataclass(frozen=True)
class RequestStat:
    name: str
    count: int
    p50_ms: float
    p95_ms: float
    p99_ms: float
    failures: int


@dataclass(frozen=True)
class RequestFailure:
    name: str
    status_code: int
    error_code: str
    count: int


@dataclass(frozen=True)
class ResourceSummary:
    cpu_mean_ratio: float
    cpu_peak_over_90_seconds: int
    memory_peak_ratio: float
    memory_growth_ratio: float
    database_connection_peak_ratio: float
    blocked_over_two_seconds: int
    pool_timeouts: int
    server_5xx: int
    sampling_gap_seconds: int


@dataclass(frozen=True)
class GateResult:
    scenario: str
    passed: bool
    failures: tuple[str, ...]
    warnings: tuple[str, ...]
