from datetime import date, time

from pydantic import Field, field_validator

from itime.tasks.schemas import Input, OperationInput, clean_required


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
