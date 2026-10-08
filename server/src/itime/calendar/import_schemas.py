from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator

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
