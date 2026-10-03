from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

DataExportStatus = Literal["queued", "running", "succeeded", "failed", "expired"]
DATA_EXPORT_SCHEMA_VERSION = 1


class DataExportCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_id: UUID
