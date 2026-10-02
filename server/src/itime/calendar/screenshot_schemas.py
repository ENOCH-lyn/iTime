from uuid import UUID

from pydantic import Field

from itime.schemas import Input


class ScreenshotUploadOperation(Input):
    operation_id: UUID
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(min_length=1, max_length=40)
    content_sha256: str = Field(min_length=64, max_length=64)


class ScreenshotRecognitionItem(Input):
    raw_text: str = Field(default="", max_length=1000)
    confidence: float | None = Field(default=None, ge=0, le=1)
    draft: dict
    warnings: list[str] = Field(default_factory=list, max_length=20)


class ScreenshotRecognitionOutput(Input):
    items: list[ScreenshotRecognitionItem] = Field(min_length=1, max_length=100)
