from typing import Literal

from pydantic import Field, field_validator

from itime.schemas import Input, OperationInput


class PrivacySettingsUpdate(OperationInput):
    expected_version: int = Field(ge=1)
    quality_improvement_enabled: bool


class BodyLogAccessInput(Input):
    purpose: Literal["troubleshooting"]


class QualitySampleAccessInput(Input):
    purpose: Literal[
        "manual_quality_review",
        "prompt_optimization",
        "effect_evaluation",
    ]


class QualitySampleReviewInput(Input):
    expected_version: int = Field(ge=1)
    outcome: Literal["acceptable", "needs_improvement"]
    review_notes: str | None = Field(default=None, max_length=2000)

    @field_validator("review_notes")
    @classmethod
    def clean_notes(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None
