from uuid import UUID

from pydantic import BaseModel, ConfigDict


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class OperationInput(Input):
    operation_id: UUID


def clean_required(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("不能只包含空白字符")
    return cleaned
