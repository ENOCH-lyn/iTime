"""Pydantic schemas for AI function tool arguments."""
from datetime import date
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_QUERY_DAYS = 93


class ToolInputBase(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EmptyInput(ToolInputBase):
    pass


def _check_range(start: date, end: date) -> None:
    if end < start:
        raise ValueError("date_from 必须早于或等于 date_to。")
    if (end - start).days + 1 > MAX_QUERY_DAYS:
        raise ValueError(f"查询跨度不得超过 {MAX_QUERY_DAYS} 天。")


class CalendarRangeInput(ToolInputBase):
    date_from: date
    date_to: date

    @model_validator(mode="after")
    def _validate(self) -> "CalendarRangeInput":
        _check_range(self.date_from, self.date_to)
        return self


class EventIdInput(ToolInputBase):
    event_id: UUID


class TaskIdInput(ToolInputBase):
    task_id: UUID


class ListTasksFilter(ToolInputBase):
    status: str | None = Field(default=None, max_length=24)
    due_from: date | None = None
    due_to: date | None = None
    tag: str | None = Field(default=None, max_length=40)
    keyword: str | None = Field(default=None, max_length=200)
    cursor: str | None = Field(default=None, max_length=256)
    limit: int = Field(default=20, ge=1, le=50)

    @model_validator(mode="after")
    def _validate(self) -> "ListTasksFilter":
        if self.due_from is not None and self.due_to is not None:
            _check_range(self.due_from, self.due_to)
        return self


class ListTasksInput(ToolInputBase):
    filter: ListTasksFilter = Field(default_factory=ListTasksFilter)


class SearchTasksInput(ToolInputBase):
    query: str = Field(min_length=1, max_length=200)
    limit: int = Field(default=10, ge=1, le=20)


class ListPlansFilter(ToolInputBase):
    kind: str | None = Field(default=None, max_length=24)
    period_from: date | None = None
    period_to: date | None = None

    @model_validator(mode="after")
    def _validate(self) -> "ListPlansFilter":
        if self.period_from is not None and self.period_to is not None:
            if self.period_to < self.period_from:
                raise ValueError("period_from 必须早于或等于 period_to。")
        return self


class ListPlansInput(ToolInputBase):
    filter: ListPlansFilter = Field(default_factory=ListPlansFilter)


class PlanIdInput(ToolInputBase):
    plan_id: UUID


class ReviewRangeInput(ToolInputBase):
    date_from: date
    date_to: date

    @model_validator(mode="after")
    def _validate(self) -> "ReviewRangeInput":
        _check_range(self.date_from, self.date_to)
        return self


class ReviewIdInput(ToolInputBase):
    review_id: UUID


class DifficultyRangeInput(ToolInputBase):
    date_from: date
    date_to: date

    @model_validator(mode="after")
    def _validate(self) -> "DifficultyRangeInput":
        _check_range(self.date_from, self.date_to)
        return self


class SearchMemoryInput(ToolInputBase):
    query: str = Field(min_length=1, max_length=200)
    limit: int = Field(default=5, ge=1, le=10)


class MemoryFilter(ToolInputBase):
    kind: str | None = Field(default=None, max_length=40)
    tags: list[str] | None = Field(default=None, max_length=10)
    lifecycle: str = Field(default="active", pattern="^(active|trashed)$")


class ListMemoriesInput(ToolInputBase):
    filter: MemoryFilter = Field(default_factory=MemoryFilter)
    limit: int = Field(default=20, ge=1, le=50)


class WritePreviewArgs(ToolInputBase):
    operation_id: str = Field(min_length=1, max_length=64)


class CreateTaskArgs(WritePreviewArgs):
    title: str = Field(min_length=1, max_length=200)
    due_at: date | None = None
    estimated_minutes: int | None = Field(default=None, ge=0, le=100000)
    tags: list[str] | None = Field(default=None, max_length=10)
    project_id: UUID | None = None
    note: str | None = Field(default=None, max_length=4000)


class UpdateTaskArgs(WritePreviewArgs):
    task_id: UUID
    expected_version: int = Field(ge=1)
    patch: dict = Field(default_factory=dict)


class CompleteTaskArgs(WritePreviewArgs):
    task_id: UUID
    expected_version: int = Field(ge=1)
    actual_minutes: int | None = Field(default=None, ge=0, le=100000)
    note: str | None = Field(default=None, max_length=4000)


class StartReviewArgs(WritePreviewArgs):
    date: date


class WriteReviewReportArgs(WritePreviewArgs):
    date: date
    summary: str = Field(min_length=1, max_length=4000)
    highlights: list[str] = Field(default_factory=list, max_length=20)
    blockers: list[str] = Field(default_factory=list, max_length=20)
    next_focus: list[str] = Field(default_factory=list, max_length=20)
    supplemental_facts: str | None = Field(default=None, max_length=4000)


class SaveMemoryArgs(WritePreviewArgs):
    kind: str = Field(min_length=1, max_length=40)
    title: str = Field(min_length=1, max_length=120)
    content: str = Field(min_length=1, max_length=4000)
    tags: list[str] | None = Field(default=None, max_length=10)


class UpdateMemoryArgs(WritePreviewArgs):
    memory_id: UUID
    expected_version: int = Field(ge=1)
    kind: str | None = Field(default=None, max_length=40)
    title: str | None = Field(default=None, max_length=120)
    content: str | None = Field(default=None, max_length=4000)
    tags: list[str] | None = Field(default=None, max_length=10)


class TrashMemoryArgs(WritePreviewArgs):
    memory_id: UUID
    expected_version: int = Field(ge=1)


class CreateDifficultyArgs(WritePreviewArgs):
    date: date
    severity: int = Field(ge=1, le=5)
    title: str = Field(min_length=1, max_length=120)
    note: str = Field(min_length=1, max_length=4000)
    related_task_id: UUID | None = None


class PlanBlockDraft(ToolInputBase):
    title: str = Field(min_length=1, max_length=200)
    period_from: date
    period_to: date
    note: str | None = Field(default=None, max_length=4000)


class ProposePlanArgs(WritePreviewArgs):
    kind: str = Field(min_length=1, max_length=24)
    period_from: date
    period_to: date
    blocks: list[PlanBlockDraft] = Field(default_factory=list, max_length=40)
    rationale: str | None = Field(default=None, max_length=4000)


class ConfirmPlanArgs(WritePreviewArgs):
    proposal_id: UUID
    expected_version: int = Field(ge=1)


class UpdatePlanBlockArgs(WritePreviewArgs):
    plan_id: UUID
    block_id: UUID
    expected_version: int = Field(ge=1)
    patch: dict = Field(default_factory=dict)
