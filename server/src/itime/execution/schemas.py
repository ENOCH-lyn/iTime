from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from itime.schemas import Input, OperationInput, clean_required


class FocusStartInput(OperationInput):
    mode: Literal["stopwatch", "countdown"]
    target_seconds: int | None = Field(default=None, ge=60, le=86_400)
    title: str | None = Field(default=None, max_length=200)
    task_id: UUID | None = None
    occurrence_key: str | None = Field(default=None, max_length=120)
    scheduled_block_id: UUID | None = None

    @field_validator("title")
    @classmethod
    def clean_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("专注标题不能只包含空白字符")
        return cleaned

    @field_validator("occurrence_key")
    @classmethod
    def clean_occurrence_key(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("任务实例标识不能只包含空白字符")
        return cleaned

    @model_validator(mode="after")
    def valid_shape(self) -> "FocusStartInput":
        if (self.mode == "countdown") != (self.target_seconds is not None):
            raise ValueError("倒计时必须提供目标时长，正计时不能提供目标时长")
        if (
            self.title is None
            and self.task_id is None
            and self.scheduled_block_id is None
        ):
            raise ValueError("自由专注必须提供标题")
        if self.occurrence_key is not None and self.task_id is None:
            raise ValueError("任务实例必须同时提供任务")
        return self


class FocusTransitionInput(OperationInput):
    expected_version: int = Field(ge=1)
    action: Literal["pause", "resume", "finish", "abandon"]
    client_recorded_at: datetime | None = None

    @field_validator("client_recorded_at")
    @classmethod
    def require_time_zone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("离线操作时间必须包含 UTC 偏移")
        return value


class FocusProgressInput(OperationInput):
    expected_version: int = Field(ge=1)
    expected_task_version: int = Field(ge=1)
    expected_occurrence_version: int | None = Field(default=None, ge=0)
    completed_step_ids: list[UUID] = Field(default_factory=list, max_length=100)
    client_recorded_at: datetime | None = None

    @field_validator("completed_step_ids")
    @classmethod
    def unique_step_ids(cls, values: list[UUID]) -> list[UUID]:
        if len(values) != len(set(values)):
            raise ValueError("已完成步骤不能重复")
        return values

    @field_validator("client_recorded_at")
    @classmethod
    def require_time_zone(cls, value: datetime | None) -> datetime | None:
        return FocusTransitionInput.require_time_zone(value)


class FocusTaskCompletionInput(OperationInput):
    expected_version: int = Field(ge=1)
    expected_task_version: int = Field(ge=1)
    expected_occurrence_version: int | None = Field(default=None, ge=0)


class FocusManualFields(Input):
    title: str = Field(min_length=1, max_length=200)
    task_id: UUID | None = None
    occurrence_key: str | None = Field(default=None, max_length=120)
    local_started_at: datetime
    local_ended_at: datetime
    time_zone: str = Field(min_length=1, max_length=64)
    start_utc_offset_minutes: int | None = Field(default=None, ge=-840, le=840)
    end_utc_offset_minutes: int | None = Field(default=None, ge=-840, le=840)
    effective_duration_seconds: int = Field(ge=0, le=86_400)

    @field_validator("title", "time_zone")
    @classmethod
    def clean_required_text(cls, value: str) -> str:
        return clean_required(value)

    @field_validator("occurrence_key")
    @classmethod
    def clean_manual_occurrence_key(cls, value: str | None) -> str | None:
        return FocusStartInput.clean_occurrence_key(value)

    @field_validator("local_started_at", "local_ended_at")
    @classmethod
    def require_local_time(cls, value: datetime) -> datetime:
        if value.tzinfo is not None:
            raise ValueError("补记时间不能包含 UTC 偏移")
        return value

    @model_validator(mode="after")
    def occurrence_requires_task(self) -> "FocusManualFields":
        if self.occurrence_key is not None and self.task_id is None:
            raise ValueError("任务实例必须同时提供任务")
        return self


class FocusManualCreateInput(FocusManualFields):
    operation_id: UUID


class FocusRevisionInput(FocusManualFields):
    operation_id: UUID
    expected_version: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=500)

    @field_validator("reason")
    @classmethod
    def clean_reason(cls, value: str) -> str:
        return clean_required(value)


class DifficultyFields(Input):
    content: str = Field(min_length=1, max_length=2000)
    category: str | None = Field(default=None, max_length=40)
    emotion: str | None = Field(default=None, max_length=40)
    task_id: UUID | None = None
    focus_session_id: UUID | None = None
    occurrence_key: str | None = Field(default=None, max_length=120)
    occurred_at: datetime | None = None

    @field_validator("content")
    @classmethod
    def clean_content(cls, value: str) -> str:
        return clean_required(value)

    @field_validator("category", "emotion", mode="before")
    @classmethod
    def clean_optional_text(cls, value: object) -> object:
        if value is None or not isinstance(value, str):
            return value
        cleaned = value.strip()
        return cleaned or None

    @field_validator("occurrence_key")
    @classmethod
    def clean_difficulty_occurrence_key(cls, value: str | None) -> str | None:
        return FocusStartInput.clean_occurrence_key(value)

    @field_validator("occurred_at")
    @classmethod
    def require_occurred_time_zone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("困难发生时间必须包含 UTC 偏移")
        return value

    @model_validator(mode="after")
    def difficulty_occurrence_requires_task(self) -> "DifficultyFields":
        if self.occurrence_key is not None and self.task_id is None:
            raise ValueError("任务实例必须同时提供任务")
        return self


class DifficultyCreateInput(DifficultyFields):
    operation_id: UUID


class DifficultyUpdateInput(DifficultyFields):
    operation_id: UUID
    expected_version: int = Field(ge=1)


class DifficultyLifecycleInput(OperationInput):
    expected_version: int = Field(ge=1)
