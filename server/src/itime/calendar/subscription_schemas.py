from datetime import date, time
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from itime.schemas import Input, OperationInput, clean_required

OverrideField = Literal["name", "description", "location", "busy"]


class SubscriptionCreate(OperationInput):
    name: str = Field(min_length=1, max_length=255)
    url: str = Field(min_length=1, max_length=4096)
    refresh_interval_minutes: int = Field(default=360, ge=15, le=10080)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        return clean_required(value)


class SubscriptionUpdate(OperationInput):
    name: str = Field(min_length=1, max_length=255)
    url: str | None = Field(default=None, min_length=1, max_length=4096)
    refresh_interval_minutes: int = Field(default=360, ge=15, le=10080)
    expected_version: int = Field(ge=1)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        return clean_required(value)


class SubscriptionAction(OperationInput):
    expected_version: int = Field(ge=1)


class SubscriptionRefresh(OperationInput):
    pass


class SubscriptionOverrideUpdate(OperationInput):
    expected_version: int = Field(ge=0)
    values: dict[OverrideField, str | bool] = Field(default_factory=dict)
    reset_fields: list[OverrideField] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_changes(self) -> "SubscriptionOverrideUpdate":
        if not self.values and not self.reset_fields:
            raise ValueError("至少需要覆盖或重置一个字段")
        if len(set(self.reset_fields)) != len(self.reset_fields):
            raise ValueError("重置字段不能重复")
        if set(self.values).intersection(self.reset_fields):
            raise ValueError("同一字段不能同时覆盖和重置")
        for field_name, value in self.values.items():
            if field_name == "busy":
                if not isinstance(value, bool):
                    raise ValueError("busy 必须是布尔值")
                continue
            if not isinstance(value, str):
                raise ValueError(f"{field_name} 必须是文本")
            cleaned = value.strip()
            limit = {"name": 200, "description": 20_000, "location": 300}[field_name]
            if field_name == "name" and not cleaned:
                raise ValueError("名称不能为空")
            if len(cleaned) > limit:
                raise ValueError(f"{field_name} 超过长度限制")
            self.values[field_name] = cleaned
        return self


class SubscriptionExceptionFields(Input):
    action: Literal["cancelled", "moved", "overridden"]
    moved_all_day: bool | None = None
    moved_start_date: date | None = None
    moved_end_date: date | None = None
    moved_starts_at: time | None = None
    moved_ends_at: time | None = None
    moved_time_zone: str | None = Field(default=None, min_length=1, max_length=64)
    moved_ambiguous_utc_offset_minutes: int | None = Field(
        default=None,
        ge=-840,
        le=840,
    )
    override_name: str | None = Field(default=None, max_length=200)
    override_description: str | None = Field(default=None, max_length=20_000)
    override_location: str | None = Field(default=None, max_length=300)
    override_busy: bool | None = None

    @field_validator("moved_time_zone")
    @classmethod
    def clean_time_zone(cls, value: str | None) -> str | None:
        return clean_required(value) if value is not None else None

    @field_validator("override_name")
    @classmethod
    def clean_override_name(cls, value: str | None) -> str | None:
        return clean_required(value) if value is not None else None

    @field_validator("override_description", "override_location")
    @classmethod
    def clean_override_text(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @model_validator(mode="after")
    def validate_action_fields(self) -> "SubscriptionExceptionFields":
        moved = (
            self.moved_all_day,
            self.moved_start_date,
            self.moved_end_date,
            self.moved_starts_at,
            self.moved_ends_at,
            self.moved_time_zone,
            self.moved_ambiguous_utc_offset_minutes,
        )
        overrides = (
            self.override_name,
            self.override_description,
            self.override_location,
            self.override_busy,
        )
        if self.action == "moved":
            if (
                self.moved_all_day is None
                or self.moved_start_date is None
                or self.moved_end_date is None
                or self.moved_time_zone is None
            ):
                raise ValueError("移动必须提供完整的新日期和时区")
            if self.moved_all_day:
                if (
                    self.moved_starts_at is not None
                    or self.moved_ends_at is not None
                    or self.moved_ambiguous_utc_offset_minutes is not None
                ):
                    raise ValueError("全天移动不能携带时刻或 UTC 偏移")
                duration = (self.moved_end_date - self.moved_start_date).days
                if duration < 1 or duration > 366:
                    raise ValueError("全天移动必须在 1 到 366 天之间")
            else:
                if self.moved_starts_at is None or self.moved_ends_at is None:
                    raise ValueError("定时移动必须提供完整的开始和结束时刻")
                if (
                    self.moved_starts_at.tzinfo is not None
                    or self.moved_ends_at.tzinfo is not None
                ):
                    raise ValueError("移动时刻必须是不带 UTC 偏移的本地墙上时间")
                day_offset = (self.moved_end_date - self.moved_start_date).days
                if day_offset not in {0, 1}:
                    raise ValueError("定时移动只能在当天或次日结束")
            if any(value is not None for value in overrides):
                raise ValueError("移动不能同时覆盖内容字段")
        elif self.action == "overridden":
            if any(value is not None for value in moved):
                raise ValueError("内容覆盖不能携带移动时间")
            if not any(value is not None for value in overrides):
                raise ValueError("内容覆盖至少需要修改一个字段")
        elif any(value is not None for value in moved + overrides):
            raise ValueError("取消不能携带移动或覆盖字段")
        return self


class SubscriptionExceptionCreate(SubscriptionExceptionFields):
    operation_id: UUID
    expected_source_version: int = Field(ge=1)
    master_event_id: UUID
    source_occurrence_key: str = Field(min_length=6, max_length=512)


class SubscriptionExceptionUpdate(SubscriptionExceptionFields):
    operation_id: UUID
    expected_source_version: int = Field(ge=1)
    expected_version: int = Field(ge=1)


class SubscriptionExceptionDelete(OperationInput):
    expected_source_version: int = Field(ge=1)
    expected_version: int = Field(ge=1)
