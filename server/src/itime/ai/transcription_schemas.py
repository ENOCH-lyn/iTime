from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from itime.schemas import OperationInput

TranscriptionPurpose = Literal["task_title", "difficulty_content"]
TranscriptionResolution = Literal["pending", "accepted", "discarded", "expired"]


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
