from datetime import date, time
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from itime.schemas import Input

PlanScope = Literal["day", "week"]
PlanDensity = Literal["loose", "balanced", "tight"]
ProposalEffectiveStatus = Literal[
    "pending",
    "applied",
    "rejected",
    "expired",
    "conflicted",
]

DEFAULT_AVAILABILITY_START = time(8, 0)
DEFAULT_AVAILABILITY_END = time(22, 0)
DEFAULT_BUFFER_MINUTES = 15
MAX_SELECTED_TASKS = 100
MAX_INSTRUCTION_LENGTH = 4000


class PlanningRequestFields(Input):
    scope: PlanScope
    target_date: date
    density: PlanDensity = "balanced"
    buffer_minutes: int = Field(
        default=DEFAULT_BUFFER_MINUTES,
        ge=0,
        le=60,
        multiple_of=5,
    )
    availability_start: time = DEFAULT_AVAILABILITY_START
    availability_end: time = DEFAULT_AVAILABILITY_END
    task_ids: list[UUID] = Field(default_factory=list, max_length=MAX_SELECTED_TASKS)
    instruction: str = Field(default="", max_length=MAX_INSTRUCTION_LENGTH)

    @field_validator("availability_start", "availability_end")
    @classmethod
    def valid_local_time(cls, value: time) -> time:
        if value.tzinfo is not None:
            raise ValueError("可规划时段必须使用不带 UTC 偏移的本地时间")
        if value.second or value.microsecond or value.minute % 15:
            raise ValueError("可规划时段必须按 15 分钟对齐")
        return value

    @field_validator("task_ids")
    @classmethod
    def unique_task_ids(cls, values: list[UUID]) -> list[UUID]:
        if len(values) != len(set(values)):
            raise ValueError("任务不能重复")
        return values

    @field_validator("instruction")
    @classmethod
    def clean_instruction(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def valid_availability(self) -> "PlanningRequestFields":
        if self.availability_start >= self.availability_end:
            raise ValueError("可规划结束时刻必须晚于开始时刻")
        return self


class PlanningContextQuery(PlanningRequestFields):
    pass


class PlanningGenerationInput(PlanningRequestFields):
    operation_id: UUID


class ProposalActionInput(Input):
    operation_id: UUID
    expected_proposal_version: int = Field(ge=1)


class ManualPlanBlockInput(Input):
    task_id: UUID
    occurrence_key: str | None = Field(default=None, min_length=1, max_length=120)
    local_date: date
    starts_at: time
    ends_at: time
    step_ids: list[UUID] = Field(default_factory=list, max_length=100)

    @field_validator("starts_at", "ends_at")
    @classmethod
    def valid_block_time(cls, value: time) -> time:
        if value.tzinfo is not None:
            raise ValueError("计划块必须使用不带 UTC 偏移的本地时间")
        if value.second or value.microsecond or value.minute % 15:
            raise ValueError("计划块必须按 15 分钟对齐")
        return value

    @field_validator("step_ids")
    @classmethod
    def unique_step_ids(cls, values: list[UUID]) -> list[UUID]:
        if len(values) != len(set(values)):
            raise ValueError("步骤不能重复")
        return values

    @model_validator(mode="after")
    def positive_duration(self) -> "ManualPlanBlockInput":
        if self.starts_at >= self.ends_at:
            raise ValueError("计划块结束时刻必须晚于开始时刻")
        return self


class ManualUnscheduledItemInput(Input):
    task_id: UUID
    occurrence_key: str | None = Field(default=None, min_length=1, max_length=120)
    remaining_minutes: int = Field(ge=1, le=525_600)
    reason: Literal[
        "no_capacity",
        "deadline_conflict",
        "fixed_conflict",
        "missing_estimate",
        "outside_scope",
    ]
    explanation: str = Field(min_length=1, max_length=500)

    @field_validator("explanation")
    @classmethod
    def clean_explanation(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("未排原因不能为空")
        return cleaned


class ManualPlanVersionInput(Input):
    operation_id: UUID
    week_start_date: date
    expected_plan_version: int = Field(ge=0)
    density: PlanDensity = "balanced"
    buffer_minutes: int = Field(ge=0, le=60, multiple_of=5)
    availability_start: time
    availability_end: time
    blocks: list[ManualPlanBlockInput] = Field(max_length=100)
    unscheduled_items: list[ManualUnscheduledItemInput] = Field(
        default_factory=list,
        max_length=100,
    )

    @field_validator("availability_start", "availability_end")
    @classmethod
    def valid_local_time(cls, value: time) -> time:
        return PlanningRequestFields.valid_local_time(value)

    @field_validator("week_start_date")
    @classmethod
    def valid_week_start(cls, value: date) -> date:
        if value.weekday() != 0:
            raise ValueError("周计划开始日期必须是周一")
        return value

    @model_validator(mode="after")
    def valid_availability(self) -> "ManualPlanVersionInput":
        if self.availability_start >= self.availability_end:
            raise ValueError("可规划结束时刻必须晚于开始时刻")
        return self
