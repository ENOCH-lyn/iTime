from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from itime.planning.schemas import PlanningRequestFields
from itime.schemas import Input


class ConversationCreateInput(Input):
    operation_id: UUID
    title: str | None = Field(default=None, max_length=120)

    @field_validator("title")
    @classmethod
    def clean_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("会话名称不能为空")
        return cleaned


class ConversationUpdateInput(Input):
    operation_id: UUID
    expected_version: int = Field(ge=1)
    title: str | None = Field(default=None, max_length=120)
    archived: bool | None = None

    @field_validator("title")
    @classmethod
    def clean_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("会话名称不能为空")
        return cleaned

    @model_validator(mode="after")
    def has_change(self) -> "ConversationUpdateInput":
        if self.title is None and self.archived is None:
            raise ValueError("至少提供一项会话修改")
        return self


class ConversationMessageCreateInput(Input):
    operation_id: UUID
    expected_conversation_version: int = Field(ge=1)
    content: str = Field(min_length=1, max_length=12000)
    response_mode: Literal["text", "planning"] = "text"
    planning: PlanningRequestFields | None = None

    @field_validator("content")
    @classmethod
    def clean_content(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("消息不能为空")
        return cleaned

    @model_validator(mode="after")
    def valid_response_mode(self) -> "ConversationMessageCreateInput":
        if self.response_mode == "planning" and self.planning is None:
            raise ValueError("生成规划时必须提供规划范围")
        if self.response_mode == "text" and self.planning is not None:
            raise ValueError("普通回复不能包含规划参数")
        return self
