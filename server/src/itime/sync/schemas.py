from uuid import UUID

from pydantic import Field, model_validator

from itime.schemas import OperationInput


class StepCompletionInput(OperationInput):
    expected_task_version: int = Field(ge=1)
    base_completed: bool
    completed: bool

    @model_validator(mode="after")
    def completion_changes(self) -> "StepCompletionInput":
        if self.base_completed == self.completed:
            raise ValueError("步骤目标状态必须与离线基础状态不同")
        return self


class OccurrenceStepCompletionInput(StepCompletionInput):
    task_id: UUID
    occurrence_key: str = Field(min_length=10, max_length=80)
    step_id: UUID
    expected_occurrence_version: int = Field(ge=0)
