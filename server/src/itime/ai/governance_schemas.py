from pydantic import Field

from itime.schemas import OperationInput


class PrivacySettingsUpdate(OperationInput):
    expected_version: int = Field(ge=1)
    quality_improvement_enabled: bool
