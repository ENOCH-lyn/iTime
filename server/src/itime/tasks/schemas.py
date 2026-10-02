from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class OperationInput(Input):
    operation_id: UUID


def clean_required(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("不能只包含空白字符")
    return cleaned


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
