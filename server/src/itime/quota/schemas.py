import re
from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from itime.ai.pricing import format_currency_nanos, parse_currency_amount
from itime.schemas import OperationInput, clean_required

CURRENCY_PATTERN = re.compile(r"^[A-Z]{3}$")


def normalized_currency(value: str) -> str:
    normalized = value.strip().upper()
    if not CURRENCY_PATTERN.fullmatch(normalized):
        raise ValueError("币种必须是三个英文字母")
    return normalized


def normalized_amount(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("金额必须使用十进制字符串")
    return format_currency_nanos(parse_currency_amount(value.strip()))


class QuotaPolicyCreateInput(OperationInput):
    currency: str = Field(min_length=3, max_length=3)
    daily_allowance: str = Field(min_length=1, max_length=32)

    @field_validator("currency")
    @classmethod
    def valid_currency(cls, value: str) -> str:
        return normalized_currency(value)

    @field_validator("daily_allowance", mode="before")
    @classmethod
    def valid_daily_allowance(cls, value: object) -> str:
        return normalized_amount(value)


class QuotaPolicyPublishInput(OperationInput):
    pass


class QuotaAdjustmentInput(OperationInput):
    expected_daily_version: int = Field(ge=0)
    activity_date: date
    mode: Literal["credit", "debit"]
    amount: str = Field(min_length=1, max_length=32)
    reason: str = Field(min_length=1, max_length=500)

    @field_validator("amount", mode="before")
    @classmethod
    def valid_amount(cls, value: object) -> str:
        normalized = normalized_amount(value)
        if parse_currency_amount(normalized) == 0:
            raise ValueError("调整金额必须大于零")
        return normalized

    @field_validator("reason")
    @classmethod
    def valid_reason(cls, value: str) -> str:
        return clean_required(value)


class QuotaPolicyOutput(BaseModel):
    id: UUID
    version: int
    currency: str
    daily_allowance: str
    reservation_estimator_version: str
    status: str
    current: bool
    created_by_user_id: UUID | None
    published_by_user_id: UUID | None
    created_at: datetime
    updated_at: datetime
    published_at: datetime | None
    retired_at: datetime | None


class QuotaPolicyListOutput(BaseModel):
    items: list[QuotaPolicyOutput]
    next_cursor: dict[str, datetime | UUID] | None
