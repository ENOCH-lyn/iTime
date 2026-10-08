from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from itime.calendar.schemas import SeriesFields
from itime.schemas import Input, clean_required

type ImportSourceType = Literal[
    "xlsx_file",
    "ics_file",
    "image",
    "public_url",
    "text",
]
type ImportBatchState = Literal[
    "queued",
    "processing",
    "needs_review",
    "confirmed",
    "cancelled",
    "failed",
    "expired",
]


class ImportUploadOperation(Input):
    operation_id: UUID
    filename: str = Field(min_length=1, max_length=255)
    format: Literal["xlsx", "ics"]
    content_sha256: str = Field(min_length=64, max_length=64)


class AIImportCreateInput(Input):
    source_type: Literal["public_url", "text"]
    content: str = Field(min_length=1, max_length=12000)
    conversation_id: UUID | None = None
    message_id: UUID | None = None

    @field_validator("content")
    @classmethod
    def clean_content(cls, value: str) -> str:
        return clean_required(value)

    @model_validator(mode="after")
    def validate_association(self):
        if self.message_id is not None and self.conversation_id is None:
            raise ValueError("关联消息时必须同时提供会话")
        if self.source_type == "public_url" and len(self.content) > 2048:
            raise ValueError("网页地址不能超过 2048 个字符")
        return self


class AIImportOperation(Input):
    operation_id: UUID
    source_type: ImportSourceType
    content_sha256: str = Field(min_length=64, max_length=64)
    filename: str | None = Field(default=None, min_length=1, max_length=255)
    conversation_id: UUID | None = None
    message_id: UUID | None = None


class ImportCandidateUpdate(SeriesFields):
    operation_id: UUID
    expected_batch_version: int = Field(ge=1)
    expected_version: int = Field(ge=1)


class ImportDecision(Input):
    candidate_id: UUID
    expected_version: int = Field(ge=1)
    action: Literal["confirm", "skip"]


class ImportConfirm(Input):
    operation_id: UUID
    expected_batch_version: int = Field(ge=1)
    decisions: list[ImportDecision] = Field(min_length=1, max_length=50)

    @field_validator("decisions")
    @classmethod
    def unique_candidates(cls, values: list[ImportDecision]) -> list[ImportDecision]:
        ids = [value.candidate_id for value in values]
        if len(ids) != len(set(ids)):
            raise ValueError("同一候选不能重复提交")
        return values


class ImportFilename(Input):
    filename: str = Field(min_length=1, max_length=255)

    @field_validator("filename")
    @classmethod
    def clean_filename(cls, value: str) -> str:
        cleaned = clean_required(value)
        if "/" in cleaned or "\\" in cleaned or "\x00" in cleaned:
            raise ValueError("文件名不能包含路径")
        return cleaned
