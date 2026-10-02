from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """所有运行配置集中校验；密码字段不出现在 repr 或错误日志中。"""

    model_config = SettingsConfigDict(
        env_prefix="ITIME_",
        env_file=ROOT / ".env.local",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )

    environment: Literal["development", "test", "production"] = "development"
    database_url: SecretStr
    public_origin: str = "http://localhost:5173"
    allowed_hosts: list[str] = ["localhost", "127.0.0.1", "testserver"]
    db_pool_size: int = Field(default=5, ge=1, le=50)
    db_max_overflow: int = Field(default=5, ge=0, le=50)
    db_pool_timeout_seconds: float = Field(default=3, gt=0, le=30)
    db_connect_timeout_seconds: int = Field(default=3, ge=1, le=30)
    db_statement_timeout_ms: int = Field(default=5000, ge=100, le=60000)
    db_lock_timeout_ms: int = Field(default=2000, ge=100, le=30000)
    max_request_bytes: int = Field(default=12 * 1024 * 1024, ge=1024, le=50 * 1024 * 1024)
    readiness_timeout_seconds: float = Field(default=2, gt=0, le=10)

    @field_validator("database_url")
    @classmethod
    def postgres_only(cls, value: SecretStr) -> SecretStr:
        parsed = urlsplit(value.get_secret_value())
        if parsed.scheme != "postgresql+psycopg" or not parsed.hostname or not parsed.path[1:]:
            raise ValueError("数据库必须使用 postgresql+psycopg URL，并指定主机及数据库")
        return value

    @field_validator("public_origin")
    @classmethod
    def origin_only(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("public_origin 必须是完整 HTTP(S) origin，不含路径或凭据")
        return value.rstrip("/")

    @field_validator("allowed_hosts")
    @classmethod
    def explicit_hosts(cls, value: list[str]) -> list[str]:
        if not value or any("*" in host or "/" in host or not host for host in value):
            raise ValueError("allowed_hosts 必须列出明确主机名")
        return value

    @model_validator(mode="after")
    def production_contract(self) -> "Settings":
        if self.environment == "production" and not self.public_origin.startswith("https://"):
            raise ValueError("生产环境 public_origin 必须启用 HTTPS")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
