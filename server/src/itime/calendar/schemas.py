from datetime import date, time
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from itime.schemas import Input, OperationInput, clean_required


def clean_time_zone(value: str) -> str:
    return value.strip()


class TimeSettingsUpdate(OperationInput):
    expected_version: int = Field(ge=0)
    time_zone: str = Field(min_length=1, max_length=64)
    week_starts_on: int = Field(default=1, ge=1, le=1)
    activity_day_start_minute: int = Field(default=240, ge=240, le=240)

    @field_validator("time_zone")
    @classmethod
    def clean_zone(cls, value: str) -> str:
        return clean_time_zone(value)


class TermCreate(OperationInput):
    name: str = Field(min_length=1, max_length=120)
    start_date: date
    end_date: date
    first_week_monday: date
    time_zone: str | None = Field(default=None, min_length=1, max_length=64)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        return clean_required(value)

    @field_validator("time_zone")
    @classmethod
    def clean_zone(cls, value: str | None) -> str | None:
        return clean_time_zone(value) if value is not None else None


class TermUpdate(TermCreate):
    expected_version: int = Field(ge=1)


class PeriodInput(Input):
    period_number: int = Field(ge=1, le=99)
    name: str = Field(min_length=1, max_length=80)
    starts_at: time
    ends_at: time

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        return clean_required(value)


class PeriodsReplace(OperationInput):
    expected_version: int = Field(ge=1)
    periods: list[PeriodInput] = Field(default_factory=list)


class RecurrenceInput(Input):
    frequency: Literal["none", "daily", "weekdays", "weekly", "monthly", "yearly"] = (
        "none"
    )
    interval: int = Field(default=1, ge=1, le=366)
    weekdays: list[int] = Field(default_factory=list)
    month: int | None = Field(default=None, ge=1, le=12)
    day_of_month: int | None = Field(default=None, ge=1, le=31)
    until: date | None = None
    count: int | None = Field(default=None, ge=1, le=366)
    week_numbers: list[int] = Field(default_factory=list)


class SeriesFields(Input):
    kind: Literal["course", "fixed"]
    term_id: UUID | None = None
    name: str = Field(min_length=1, max_length=200)
    teacher: str = Field(default="", max_length=200)
    description: str = Field(default="", max_length=20_000)
    location: str = Field(default="", max_length=300)
    busy: bool = True
    time_zone: str | None = Field(default=None, min_length=1, max_length=64)
    start_date: date | None = None
    starts_at: time | None = None
    ends_at: time | None = None
    end_day_offset: int = Field(default=0, ge=0, le=1)
    start_period_number: int | None = Field(default=None, ge=1, le=99)
    end_period_number: int | None = Field(default=None, ge=1, le=99)
    ambiguous_utc_offset_minutes: int | None = Field(default=None, ge=-840, le=840)
    recurrence: RecurrenceInput = Field(default_factory=RecurrenceInput)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        return clean_required(value)

    @field_validator("description", "location", "teacher")
    @classmethod
    def clean_optional_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("time_zone")
    @classmethod
    def clean_zone(cls, value: str | None) -> str | None:
        return clean_time_zone(value) if value is not None else None


class SeriesCreate(SeriesFields):
    operation_id: UUID
    course_group_id: UUID | None = None


class SeriesUpdate(SeriesCreate):
    expected_version: int = Field(ge=1)


class SeriesLifecycleInput(OperationInput):
    expected_version: int = Field(ge=1)


class ExceptionFields(Input):
    action: Literal["cancelled", "moved", "overridden"]
    moved_start_date: date | None = None
    moved_starts_at: time | None = None
    moved_ends_at: time | None = None
    moved_end_day_offset: int | None = Field(default=None, ge=0, le=1)
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

    @field_validator("override_name")
    @classmethod
    def clean_override_name(cls, value: str | None) -> str | None:
        return clean_required(value) if value is not None else None

    @field_validator("override_description", "override_location")
    @classmethod
    def clean_override_text(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @field_validator("moved_time_zone")
    @classmethod
    def clean_moved_zone(cls, value: str | None) -> str | None:
        return clean_time_zone(value) if value is not None else None

    @model_validator(mode="after")
    def action_fields(self) -> "ExceptionFields":
        moved = (
            self.moved_start_date,
            self.moved_starts_at,
            self.moved_ends_at,
            self.moved_end_day_offset,
        )
        overrides = (
            self.override_name,
            self.override_description,
            self.override_location,
            self.override_busy,
        )
        if self.action == "moved":
            if any(value is None for value in moved):
                raise ValueError("调课必须提供完整的新日期和时间")
            if any(value is not None for value in overrides):
                raise ValueError("调课不能同时覆盖内容字段")
        elif self.action == "overridden":
            if any(value is not None for value in moved) or (
                self.moved_time_zone is not None
                or self.moved_ambiguous_utc_offset_minutes is not None
            ):
                raise ValueError("内容覆盖不能携带移动时间")
            if not any(value is not None for value in overrides):
                raise ValueError("内容覆盖至少需要修改一个字段")
        elif any(value is not None for value in moved + overrides) or (
            self.moved_time_zone is not None
            or self.moved_ambiguous_utc_offset_minutes is not None
        ):
            raise ValueError("取消操作不能携带移动或覆盖字段")
        return self


class ExceptionCreate(ExceptionFields):
    operation_id: UUID
    expected_series_version: int = Field(ge=1)
    occurrence_key: str = Field(min_length=10, max_length=64)


class ExceptionUpdate(ExceptionFields):
    operation_id: UUID
    expected_series_version: int = Field(ge=1)
    expected_version: int = Field(ge=1)


class AdditionFields(Input):
    status: Literal["scheduled", "cancelled"] = "scheduled"
    local_start_date: date
    starts_at: time
    ends_at: time
    end_day_offset: int = Field(default=0, ge=0, le=1)
    time_zone: str | None = Field(default=None, min_length=1, max_length=64)
    ambiguous_utc_offset_minutes: int | None = Field(default=None, ge=-840, le=840)
    override_name: str | None = Field(default=None, max_length=200)
    override_description: str | None = Field(default=None, max_length=20_000)
    override_location: str | None = Field(default=None, max_length=300)
    override_busy: bool | None = None

    @field_validator("time_zone")
    @classmethod
    def clean_zone(cls, value: str | None) -> str | None:
        return clean_time_zone(value) if value is not None else None

    @field_validator("override_name")
    @classmethod
    def clean_override_name(cls, value: str | None) -> str | None:
        return clean_required(value) if value is not None else None

    @field_validator("override_description", "override_location")
    @classmethod
    def clean_override_text(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None


class AdditionCreate(AdditionFields):
    status: Literal["scheduled"] = "scheduled"
    operation_id: UUID
    expected_series_version: int = Field(ge=1)
    source_occurrence_key: str = Field(min_length=10, max_length=64)


class AdditionUpdate(AdditionFields):
    operation_id: UUID
    expected_series_version: int = Field(ge=1)
    expected_version: int = Field(ge=1)


class SeriesSplit(Input):
    operation_id: UUID
    expected_version: int = Field(ge=1)
    occurrence_key: str = Field(min_length=10, max_length=64)
    replacement: SeriesFields
