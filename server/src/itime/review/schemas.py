from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator

from itime.schemas import Input, OperationInput, clean_required

MemoryKind = Literal["preference", "constraint", "context", "goal"]


class MemoryFields(Input):
    kind: MemoryKind
    title: str = Field(min_length=1, max_length=120)
    content: str = Field(min_length=1, max_length=2000)

    @field_validator("title", "content")
    @classmethod
    def clean_text(cls, value: str) -> str:
        return clean_required(value)


class MemoryCreateInput(MemoryFields):
    operation_id: UUID


class MemoryUpdateInput(MemoryFields):
    operation_id: UUID
    expected_version: int = Field(ge=1)


class MemoryLifecycleInput(OperationInput):
    expected_version: int = Field(ge=1)


class MemorySettingsUpdateInput(OperationInput):
    enabled: bool
    expected_version: int = Field(ge=1)
