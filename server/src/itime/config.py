from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from cryptography.fernet import Fernet
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
    encryption_key: SecretStr
    ai_encryption_key: SecretStr | None = None
    calendar_subscription_encryption_key: SecretStr | None = None
    export_encryption_key: SecretStr | None = None
    session_hours: int = Field(default=24 * 30, ge=1, le=24 * 90)
    admin_session_hours: int = Field(default=8, ge=1, le=24)
    admin_setup_minutes: int = Field(default=30, ge=5, le=120)
    ai_private_endpoint_hosts: list[str] = Field(default_factory=list)
    ai_job_timeout_seconds: int = Field(default=180, ge=30, le=900)
    ai_job_lease_seconds: int = Field(default=210, ge=30, le=1200)
    ai_body_log_retention_days: int = Field(default=7, ge=1, le=30)
    ai_quality_sample_retention_days: int = Field(default=30, ge=1, le=90)
    calendar_subscription_lease_seconds: int = Field(default=60, ge=30, le=300)
    public_link_lease_seconds: int = Field(default=30, ge=20, le=120)
    public_link_refresh_cooldown_seconds: int = Field(default=30, ge=1, le=300)
    export_job_lease_seconds: int = Field(default=120, ge=30, le=900)
    export_artifact_retention_hours: int = Field(default=24, ge=1, le=168)
    export_job_retention_days: int = Field(default=30, ge=1, le=90)
    sync_event_retention_days: int = Field(default=30, ge=7, le=90)
    export_max_uncompressed_bytes: int = Field(
        default=100 * 1024 * 1024,
        ge=1024 * 1024,
        le=500 * 1024 * 1024,
    )
    export_max_archive_bytes: int = Field(
        default=50 * 1024 * 1024,
        ge=1024 * 1024,
        le=250 * 1024 * 1024,
    )
    verification_minutes: int = Field(default=30, ge=5, le=1440)
    password_reset_minutes: int = Field(default=15, ge=5, le=60)
    mail_cooldown_seconds: int = Field(default=60, ge=1, le=600)
    auth_ip_limit: int = Field(default=60, ge=1, le=1000)
    auth_identity_limit: int = Field(default=15, ge=1, le=100)
    auth_window_seconds: int = Field(default=600, ge=10, le=3600)
    smtp_host: str = "127.0.0.1"
    smtp_port: int = Field(default=51025, ge=1, le=65535)
    smtp_starttls: bool = False
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    smtp_from: str = "iTime <account@itime.local>"
    smtp_timeout_seconds: float = Field(default=5, ge=1, le=30)

    @field_validator(
        "encryption_key",
        "ai_encryption_key",
        "calendar_subscription_encryption_key",
        "export_encryption_key",
    )
    @classmethod
    def fernet_key(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None:
            return None
        try:
            Fernet(value.get_secret_value())
        except (ValueError, TypeError) as exc:
            raise ValueError("加密配置必须是独立保管的 Fernet 密钥") from exc
        return value

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

    @field_validator("ai_private_endpoint_hosts")
    @classmethod
    def explicit_ai_hosts(cls, value: list[str]) -> list[str]:
        normalized = [host.strip().lower() for host in value]
        if any(
            not host
            or "*" in host
            or "/" in host
            or ":" in host
            or "@" in host
            for host in normalized
        ):
            raise ValueError("ai_private_endpoint_hosts 必须列出不含端口的明确主机名")
        return list(dict.fromkeys(normalized))

    @model_validator(mode="after")
    def production_contract(self) -> "Settings":
        if self.environment == "production" and not self.public_origin.startswith("https://"):
            raise ValueError("生产环境 public_origin 必须启用 HTTPS")
        if self.environment == "production" and (
            self.ai_encryption_key is None
            or self.ai_encryption_key.get_secret_value()
            == self.encryption_key.get_secret_value()
        ):
            raise ValueError("生产环境必须配置与基础密钥不同的 ai_encryption_key")
        if self.environment == "production" and (
            self.calendar_subscription_encryption_key is None
            or self.calendar_subscription_encryption_key.get_secret_value()
            in {
                self.encryption_key.get_secret_value(),
                self.ai_encryption_key.get_secret_value()
                if self.ai_encryption_key is not None
                else "",
            }
        ):
            raise ValueError(
                "生产环境必须配置独立的 calendar_subscription_encryption_key"
            )
        existing_encryption_keys = {
            self.encryption_key.get_secret_value(),
            (
                self.ai_encryption_key.get_secret_value()
                if self.ai_encryption_key is not None
                else ""
            ),
            (
                self.calendar_subscription_encryption_key.get_secret_value()
                if self.calendar_subscription_encryption_key is not None
                else ""
            ),
        }
        if self.environment == "production" and (
            self.export_encryption_key is None
            or self.export_encryption_key.get_secret_value()
            in existing_encryption_keys
        ):
            raise ValueError(
                "生产环境必须配置独立的 export_encryption_key"
            )
        if self.smtp_host not in {"localhost", "127.0.0.1", "::1"} and not self.smtp_starttls:
            raise ValueError("外部 SMTP 必须启用 STARTTLS")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
