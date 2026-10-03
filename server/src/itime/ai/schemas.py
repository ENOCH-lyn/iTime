import re
from datetime import datetime
from typing import Annotated, Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, Field, SecretStr, field_validator

from itime.ai.pricing import format_price_per_million, parse_price_per_million
from itime.schemas import Input

Capability = Literal["calendar_screenshot", "conversation", "planning"]
ConfigStatus = Literal["draft", "tested", "published", "retired"]
MODEL_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,254}$")
CURRENCY_PATTERN = re.compile(r"^[A-Z]{3}$")


class AIPriceInput(Input):
    currency: str = Field(min_length=3, max_length=3)
    input_per_million_tokens: str = Field(min_length=1, max_length=32)
    cached_input_per_million_tokens: str | None = Field(
        default=None,
        min_length=1,
        max_length=32,
    )
    output_per_million_tokens: str = Field(min_length=1, max_length=32)

    @field_validator("currency")
    @classmethod
    def valid_currency(cls, value: str) -> str:
        value = value.strip().upper()
        if not CURRENCY_PATTERN.fullmatch(value):
            raise ValueError("币种必须是三个英文字母")
        return value

    @field_validator(
        "input_per_million_tokens",
        "cached_input_per_million_tokens",
        "output_per_million_tokens",
        mode="before",
    )
    @classmethod
    def valid_price(cls, value):
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("价格必须使用十进制字符串")
        normalized = value.strip()
        return format_price_per_million(parse_price_per_million(normalized))


class AIPriceOutput(BaseModel):
    currency: str
    input_per_million_tokens: str
    cached_input_per_million_tokens: str
    output_per_million_tokens: str


class AIConfigCreateInput(Input):
    capability: Capability
    endpoint_url: str = Field(min_length=1, max_length=2048)
    model: str = Field(min_length=1, max_length=255)
    api_key: Annotated[SecretStr, Field(min_length=1, max_length=8192)]
    timeout_seconds: int = Field(ge=1, le=300)
    max_output_tokens: int = Field(ge=1, le=32768)
    pricing: AIPriceInput

    @field_validator("endpoint_url")
    @classmethod
    def valid_endpoint_url(cls, value: str) -> str:
        value = value.strip()
        if not value or "?" in value or "#" in value:
            raise ValueError("端点 URL 不得包含查询或片段")
        parsed = urlsplit(value)
        try:
            port = parsed.port
        except ValueError as exc:
            raise ValueError("端点 URL 端口无效") from exc
        del port
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or any(character.isspace() or ord(character) < 32 for character in value)
        ):
            raise ValueError("端点 URL 必须是无凭据的完整 HTTP(S) URL")
        return value.rstrip("/")

    @field_validator("model")
    @classmethod
    def valid_model(cls, value: str) -> str:
        value = value.strip()
        if not MODEL_PATTERN.fullmatch(value):
            raise ValueError("模型名称格式无效")
        return value

    @field_validator("api_key")
    @classmethod
    def nonempty_api_key(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value()
        if not raw.strip() or "\x00" in raw:
            raise ValueError("API Key 不能为空")
        return value


class AIConfigVersionOutput(BaseModel):
    id: UUID
    capability: Capability
    version: int
    endpoint_url: str
    model: str
    timeout_seconds: int
    max_output_tokens: int
    pricing: AIPriceOutput | None
    pricing_configured: bool
    key_configured: Literal[True] = True
    status: ConfigStatus
    current: bool
    test_in_progress: bool
    last_test_started_at: datetime | None
    last_test_completed_at: datetime | None
    last_test_error_code: str | None
    last_test_error_stage: str | None
    created_by_user_id: UUID | None
    last_tested_by_user_id: UUID | None
    published_by_user_id: UUID | None
    created_at: datetime
    updated_at: datetime
    published_at: datetime | None
    retired_at: datetime | None


class AIConfigVersionListOutput(BaseModel):
    items: list[AIConfigVersionOutput]
    next_cursor: dict[str, datetime | UUID] | None


class AIJobCancelInput(Input):
    operation_id: UUID
