from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from itime.schemas import Input, OperationInput, clean_required

TranscriptionPurpose = Literal["task_title", "difficulty_content"]
TranscriptionResolution = Literal["pending", "accepted", "discarded", "expired"]


class VoiceTranscriptionUploadOperation(Input):
    operation_id: UUID
    purpose: TranscriptionPurpose
    filename: str = Field(min_length=1, max_length=255)
    content_type: Literal["audio/wav"]
    content_sha256: str = Field(min_length=64, max_length=64)
    byte_size: int = Field(ge=45, le=2 * 1024 * 1024)
    duration_ms: int = Field(ge=250, le=60_000)

    @field_validator("filename")
    @classmethod
    def clean_filename(cls, value: str) -> str:
        cleaned = clean_required(value)
        if (
            "/" in cleaned
            or "\\" in cleaned
            or any(ord(character) < 32 for character in cleaned)
        ):
            raise ValueError("文件名不能包含路径或控制字符")
        return cleaned


class VoiceTranscriptionResolveInput(OperationInput):
    expected_version: int = Field(ge=1)
    action: Literal["accept", "discard"]
    transcript_sha256: str | None = Field(default=None, min_length=64, max_length=64)

    @field_validator("transcript_sha256")
    @classmethod
    def valid_transcript_sha256(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.lower()
        if any(character not in "0123456789abcdef" for character in normalized):
            raise ValueError("转写摘要必须是 SHA-256 十六进制字符串")
        return normalized

    @model_validator(mode="after")
    def accept_requires_hash(self) -> "VoiceTranscriptionResolveInput":
        if (self.action == "accept") != (self.transcript_sha256 is not None):
            raise ValueError("应用转写时必须提供文字摘要，丢弃时不能提供")
        return self


class VoiceTranscriptionOutput(BaseModel):
    id: UUID
    job_id: UUID
    purpose: TranscriptionPurpose
    transcript: str | None
    transcript_sha256: str | None
    language: str | None
    resolution: TranscriptionResolution
    ready_at: datetime | None
    resolved_at: datetime | None
    text_purged_at: datetime | None
    expires_at: datetime
    version: int
    created_at: datetime
    updated_at: datetime


class VoiceTranscriptionPage(BaseModel):
    items: list[VoiceTranscriptionOutput]
    next_cursor: dict[str, datetime | UUID] | None
