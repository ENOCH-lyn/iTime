from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, field_validator

from itime.schemas import Input, OperationInput, clean_required

MemoryKind = Literal["preference", "constraint", "context", "goal"]
BoundedText = Annotated[str, Field(min_length=1, max_length=2000)]


class ReviewCreateInput(OperationInput):
    activity_date: date


class ReviewFactsUpdateInput(OperationInput):
    expected_version: int = Field(ge=1)
    supplemental_facts: str = Field(default="", max_length=8000)

    @field_validator("supplemental_facts")
    @classmethod
    def clean_facts(cls, value: str) -> str:
        return value.strip()


class ReviewQuestionGenerationInput(OperationInput):
    expected_version: int = Field(ge=1)
    answer: str | None = Field(default=None, max_length=8000)

    @field_validator("answer")
    @classmethod
    def clean_answer(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


class ReviewReportGenerationInput(OperationInput):
    expected_version: int = Field(ge=1)
    instruction: str = Field(default="", max_length=2000)

    @field_validator("instruction")
    @classmethod
    def clean_instruction(cls, value: str) -> str:
        return value.strip()


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


class ReviewQuestionOutput(Input):
    question: str = Field(min_length=1, max_length=2000)

    @field_validator("question")
    @classmethod
    def clean_question(cls, value: str) -> str:
        return clean_required(value)


class ReviewFactOutput(Input):
    source_key: str = Field(min_length=1, max_length=160)
    text: str = Field(min_length=1, max_length=2000)

    @field_validator("source_key", "text")
    @classmethod
    def clean_fact(cls, value: str) -> str:
        return clean_required(value)


class ReviewInsightOutput(Input):
    text: str = Field(min_length=1, max_length=2000)
    basis: Literal["ai_inference"] = "ai_inference"

    @field_validator("text")
    @classmethod
    def clean_insight(cls, value: str) -> str:
        return clean_required(value)


class MemoryProposalOutput(Input):
    kind: MemoryKind
    title: str = Field(min_length=1, max_length=120)
    content: str = Field(min_length=1, max_length=2000)
    rationale: str = Field(min_length=1, max_length=1000)
    source_keys: list[str] = Field(min_length=1, max_length=5)

    @field_validator("title", "content", "rationale")
    @classmethod
    def clean_candidate_text(cls, value: str) -> str:
        return clean_required(value)

    @field_validator("source_keys")
    @classmethod
    def valid_source_keys(cls, values: list[str]) -> list[str]:
        cleaned = [clean_required(value) for value in values]
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("记忆候选来源不能重复")
        if any(len(value) > 160 for value in cleaned):
            raise ValueError("记忆候选来源标识过长")
        return cleaned


class ReviewReportOutput(Input):
    summary: str = Field(min_length=1, max_length=4000)
    facts: list[ReviewFactOutput] = Field(max_length=30)
    user_statements: list[BoundedText] = Field(max_length=20)
    insights: list[ReviewInsightOutput] = Field(max_length=20)
    next_actions: list[BoundedText] = Field(max_length=20)
    uncertainties: list[BoundedText] = Field(max_length=20)
    memory_candidates: list[MemoryProposalOutput] = Field(max_length=5)

    @field_validator("summary")
    @classmethod
    def clean_summary(cls, value: str) -> str:
        return clean_required(value)
