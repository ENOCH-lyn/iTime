from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from itime.schemas import OperationInput


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
