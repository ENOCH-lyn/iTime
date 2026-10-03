from typing import Annotated
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    SecretStr,
    field_validator,
)

Password = Annotated[SecretStr, Field(min_length=12, max_length=128)]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class AccountDeletionRequest(Input):
    operation_id: UUID
    email_confirmation: EmailStr
    current_password: Password

    @field_validator("email_confirmation", mode="before")
    @classmethod
    def normalize_email(cls, value):
        return value.strip().lower() if isinstance(value, str) else value


class AccountDeletionCancel(Input):
    email: EmailStr
    password: Password
    device_name: str = Field(
        default="网页浏览器",
        min_length=1,
        max_length=100,
    )

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value):
        return value.strip().lower() if isinstance(value, str) else value
