from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from itime.database import Base


class AIConfigCapability(Base):
    __tablename__ = "ai_config_capabilities"
    __table_args__ = (
        CheckConstraint(
            "capability IN ('calendar_screenshot')",
            name="capability",
        ),
        CheckConstraint("next_version >= 1", name="next_version"),
        ForeignKeyConstraint(
            ["current_version_id", "capability"],
            ["ai_config_versions.id", "ai_config_versions.capability"],
            name="fk_ai_config_capabilities_current_version",
            ondelete="RESTRICT",
            use_alter=True,
        ),
    )

    capability: Mapped[str] = mapped_column(String(50), primary_key=True)
    next_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    current_version_id: Mapped[UUID | None]
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class AIConfigVersion(Base):
    __tablename__ = "ai_config_versions"
    __table_args__ = (
        CheckConstraint(
            "capability IN ('calendar_screenshot')",
            name="capability",
        ),
        CheckConstraint(
            "status IN ('draft', 'tested', 'published', 'retired')",
            name="status",
        ),
        CheckConstraint("version >= 1", name="version"),
        CheckConstraint(
            "length(btrim(endpoint_url)) BETWEEN 1 AND 2048",
            name="endpoint_url_length",
        ),
        CheckConstraint(
            "model ~ '^[A-Za-z0-9][A-Za-z0-9._:/-]{0,254}$'",
            name="model",
        ),
        CheckConstraint(
            "octet_length(api_key_ciphertext) > 0",
            name="api_key_ciphertext",
        ),
        CheckConstraint(
            "timeout_seconds BETWEEN 1 AND 300",
            name="timeout_seconds",
        ),
        CheckConstraint(
            "max_output_tokens BETWEEN 1 AND 32768",
            name="max_output_tokens",
        ),
        CheckConstraint(
            "(last_test_error_code IS NULL) = (last_test_error_stage IS NULL)",
            name="test_error_pair",
        ),
        CheckConstraint(
            "(last_test_id IS NULL) = (last_test_started_at IS NULL)",
            name="test_started",
        ),
        CheckConstraint(
            "last_test_completed_at IS NULL OR last_test_started_at IS NOT NULL",
            name="test_completed",
        ),
        CheckConstraint(
            "status NOT IN ('tested', 'published', 'retired') OR "
            "(last_test_completed_at IS NOT NULL AND last_test_error_code IS NULL)",
            name="successful_status",
        ),
        CheckConstraint(
            "(status IN ('published', 'retired')) = (published_at IS NOT NULL)",
            name="published_at",
        ),
        CheckConstraint(
            "(status = 'retired') = (retired_at IS NOT NULL)",
            name="retired_at",
        ),
        ForeignKeyConstraint(
            ["capability"],
            ["ai_config_capabilities.capability"],
            name="fk_ai_config_versions_capability",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("capability", "version", name="uq_ai_config_versions_version"),
        UniqueConstraint("id", "capability", name="uq_ai_config_versions_id_capability"),
        Index(
            "uq_ai_config_versions_published_capability",
            "capability",
            unique=True,
            postgresql_where=text("status = 'published'"),
        ),
        Index(
            "ix_ai_config_versions_capability_created",
            "capability",
            "created_at",
            "id",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    capability: Mapped[str] = mapped_column(String(50))
    version: Mapped[int] = mapped_column(Integer)
    endpoint_url: Mapped[str] = mapped_column(String(2048))
    model: Mapped[str] = mapped_column(String(255))
    api_key_ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    timeout_seconds: Mapped[int] = mapped_column(Integer)
    max_output_tokens: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="draft", server_default="draft")
    last_test_id: Mapped[UUID | None]
    last_test_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_test_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_test_error_code: Mapped[str | None] = mapped_column(String(80))
    last_test_error_stage: Mapped[str | None] = mapped_column(String(40))
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    last_tested_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    published_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
