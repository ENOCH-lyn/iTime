from datetime import date, time
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AnyHttpUrl, Field, field_validator, model_validator

from itime.calendar.schemas import RecurrenceInput
from itime.schemas import Input, OperationInput, clean_required


class ProjectCreate(OperationInput):
    kind: Literal["project", "goal"]
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=20_000)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        return clean_required(value)


class ProjectUpdate(ProjectCreate):
    expected_version: int = Field(ge=1)


class LinkInput(Input):
    title: str = Field(default="", max_length=120)
    url: AnyHttpUrl

    @field_validator("title")
    @classmethod
    def clean_title(cls, value: str) -> str:
        return value.strip()


class StepDraft(Input):
    title: str = Field(min_length=1, max_length=300)

    @field_validator("title")
    @classmethod
    def clean_title(cls, value: str) -> str:
        return clean_required(value)


class TaskFields(OperationInput):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=20_000)
    project_id: UUID | None = None
    estimated_minutes: int | None = Field(default=None, ge=1, le=525_600)
    priority: int = Field(default=0, ge=0, le=3)
    labels: list[Annotated[str, Field(min_length=1, max_length=40)]] = Field(
        default_factory=list,
        max_length=10,
    )
    links: list[LinkInput] = Field(default_factory=list, max_length=20)

    @field_validator("title")
    @classmethod
    def clean_title(cls, value: str) -> str:
        return clean_required(value)

    @field_validator("labels")
    @classmethod
    def clean_labels(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for value in values:
            name = " ".join(value.split())
            if not name:
                raise ValueError("标签不能只包含空白字符")
            normalized = name.lower()
            if normalized not in seen:
                cleaned.append(name)
                seen.add(normalized)
        return cleaned


class TaskCreate(TaskFields):
    steps: list[StepDraft] = Field(default_factory=list, max_length=100)


class TaskUpdate(TaskFields):
    expected_version: int = Field(ge=1)


class CompletionInput(OperationInput):
    expected_version: int = Field(ge=1)
    completed: bool


class TaskLifecycleInput(OperationInput):
    expected_version: int = Field(ge=1)


class TaskArchiveInput(TaskLifecycleInput):
    archived: bool


class StepCreate(OperationInput):
    expected_task_version: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=300)

    @field_validator("title")
    @classmethod
    def clean_title(cls, value: str) -> str:
        return clean_required(value)


class StepUpdate(StepCreate):
    completed: bool


class StepDelete(OperationInput):
    expected_task_version: int = Field(ge=1)


class StepReorder(OperationInput):
    expected_task_version: int = Field(ge=1)
    step_ids: list[UUID] = Field(min_length=1, max_length=100)

    @field_validator("step_ids")
    @classmethod
    def unique_ids(cls, values: list[UUID]) -> list[UUID]:
        if len(values) != len(set(values)):
            raise ValueError("步骤不能重复")
        return values


class BulkTarget(Input):
    id: UUID
    expected_version: int = Field(ge=1)


class TaskBulkInput(OperationInput):
    items: list[BulkTarget] = Field(min_length=1, max_length=100)
    action: Literal[
        "complete",
        "reopen",
        "set_priority",
        "set_project",
        "add_labels",
        "remove_labels",
    ]
    priority: int | None = Field(default=None, ge=0, le=3)
    project_id: UUID | None = None
    labels: list[Annotated[str, Field(min_length=1, max_length=40)]] = Field(
        default_factory=list,
        max_length=10,
    )

    @field_validator("items")
    @classmethod
    def unique_items(cls, values: list[BulkTarget]) -> list[BulkTarget]:
        if len(values) != len({item.id for item in values}):
            raise ValueError("任务不能重复")
        return values

    @field_validator("labels")
    @classmethod
    def clean_labels(cls, values: list[str]) -> list[str]:
        return TaskFields.clean_labels(values)

    @model_validator(mode="after")
    def action_parameters(self) -> "TaskBulkInput":
        if self.action == "set_priority" and self.priority is None:
            raise ValueError("设置优先级时必须提供 priority")
        if self.action in {"add_labels", "remove_labels"} and not self.labels:
            raise ValueError("批量修改标签时必须提供 labels")
        if self.action != "set_priority" and self.priority is not None:
            raise ValueError("当前批量操作不接受 priority")
        if self.action != "set_project" and self.project_id is not None:
            raise ValueError("当前批量操作不接受 project_id")
        if self.action not in {"add_labels", "remove_labels"} and self.labels:
            raise ValueError("当前批量操作不接受 labels")
        return self


class TaskScheduleFields(Input):
    due_kind: Literal["none", "date", "datetime"] = "none"
    due_date: date | None = None
    due_time: time | None = None
    time_zone: str | None = Field(default=None, min_length=1, max_length=64)
    ambiguous_utc_offset_minutes: int | None = Field(default=None, ge=-840, le=840)
    splittable: bool = True
    minimum_block_minutes: int = Field(default=15, ge=1, le=1440)
    recurrence: RecurrenceInput = Field(default_factory=RecurrenceInput)

    @field_validator("time_zone")
    @classmethod
    def clean_zone(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None


class TaskScheduleUpdate(TaskScheduleFields):
    operation_id: UUID
    expected_task_version: int = Field(ge=1)


class TaskOccurrenceUpdate(OperationInput):
    expected_task_version: int = Field(ge=1)
    expected_occurrence_version: int = Field(ge=0)
    occurrence_key: str = Field(min_length=10, max_length=80)
    status: Literal["open", "completed", "skipped"]
    completed_step_ids: list[UUID] = Field(default_factory=list, max_length=100)

    @field_validator("completed_step_ids")
    @classmethod
    def unique_completed_steps(cls, values: list[UUID]) -> list[UUID]:
        if len(values) != len(set(values)):
            raise ValueError("已完成步骤不能重复")
        return values


class TaskOccurrenceReschedule(OperationInput):
    expected_task_version: int = Field(ge=1)
    expected_occurrence_version: int = Field(ge=0)
    occurrence_key: str = Field(min_length=10, max_length=80)
    due_date: date
    due_time: time | None = None
    ambiguous_utc_offset_minutes: int | None = Field(default=None, ge=-840, le=840)


class TaskMakeupCreate(OperationInput):
    expected_task_version: int = Field(ge=1)
    original_occurrence_key: str = Field(min_length=10, max_length=80)
    due_date: date
    due_time: time | None = None
    ambiguous_utc_offset_minutes: int | None = Field(default=None, ge=-840, le=840)


class TaskScheduleSplit(Input):
    operation_id: UUID
    expected_task_version: int = Field(ge=1)
    occurrence_key: str = Field(min_length=10, max_length=80)
    replacement: TaskScheduleFields
