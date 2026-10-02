from typing import Literal

from pydantic import Field, field_validator, model_validator

from itime.schemas import OperationInput, clean_required

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
