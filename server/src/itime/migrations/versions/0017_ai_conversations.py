"""persistent AI conversations, messages, and stream events"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0017_ai_conversations"
down_revision = "0016_task_lifecycle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        op.f("ck_ai_config_capabilities_capability"),
        "ai_config_capabilities",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_ai_config_capabilities_capability"),
        "ai_config_capabilities",
        "capability IN ('calendar_screenshot', 'conversation')",
    )
    op.execute(
        """
        INSERT INTO ai_config_capabilities (capability, next_version)
        VALUES ('conversation', 1)
        ON CONFLICT (capability) DO NOTHING
        """
    )

    op.drop_constraint(
        op.f("ck_ai_config_versions_capability"),
        "ai_config_versions",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_ai_config_versions_capability"),
        "ai_config_versions",
        "capability IN ('calendar_screenshot', 'conversation')",
    )

    op.drop_constraint(
        op.f("ck_ai_jobs_capability"),
        "ai_jobs",
        type_="check",
    )
    op.alter_column(
        "ai_jobs",
        "input_media_id",
        existing_type=sa.Uuid(),
        nullable=True,
    )
    op.add_column(
        "ai_jobs",
        sa.Column(
            "last_event_sequence",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
    )
    op.add_column(
        "ai_jobs",
        sa.Column("conversation_id", sa.Uuid(), nullable=True),
    )
    op.create_check_constraint(
        op.f("ck_ai_jobs_capability"),
        "ai_jobs",
        "capability IN ('calendar_screenshot', 'conversation')",
    )
    op.create_check_constraint(
        op.f("ck_ai_jobs_last_event_sequence"),
        "ai_jobs",
        "last_event_sequence >= 0",
    )
    op.create_check_constraint(
        op.f("ck_ai_jobs_input_reference"),
        "ai_jobs",
        "(capability = 'calendar_screenshot' "
        "AND input_media_id IS NOT NULL AND conversation_id IS NULL) "
        "OR (capability = 'conversation' "
        "AND input_media_id IS NULL AND conversation_id IS NOT NULL)",
    )
    op.create_index(
        "ix_ai_jobs_capability_due",
        "ai_jobs",
        ["capability", "available_at", "deadline_at"],
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )

    op.create_table(
        "ai_conversations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column(
            "title_source",
            sa.String(length=8),
            server_default="auto",
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "char_length(btrim(title)) BETWEEN 1 AND 120",
            name=op.f("ck_ai_conversations_title"),
        ),
        sa.CheckConstraint(
            "title_source IN ('auto', 'user')",
            name=op.f("ck_ai_conversations_title_source"),
        ),
        sa.CheckConstraint(
            "version >= 1",
            name=op.f("ck_ai_conversations_version"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name=op.f("fk_ai_conversations_owner_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ai_conversations")),
        sa.UniqueConstraint(
            "id",
            "owner_id",
            name="uq_ai_conversations_id_owner",
        ),
    )
    op.create_index(
        "ix_ai_conversations_owner_archived_updated",
        "ai_conversations",
        ["owner_id", "archived_at", "updated_at", "id"],
    )
    op.create_foreign_key(
        "fk_ai_jobs_conversation_owner",
        "ai_jobs",
        "ai_conversations",
        ["conversation_id", "owner_id"],
        ["id", "owner_id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "uq_ai_jobs_active_conversation",
        "ai_jobs",
        ["conversation_id"],
        unique=True,
        postgresql_where=sa.text(
            "conversation_id IS NOT NULL AND status IN ('queued', 'running')"
        ),
    )

    op.create_table(
        "ai_conversation_messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=10), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("content", sa.Text(), server_default="", nullable=False),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "sequence >= 1",
            name=op.f("ck_ai_conversation_messages_sequence"),
        ),
        sa.CheckConstraint(
            "role IN ('user', 'assistant')",
            name=op.f("ck_ai_conversation_messages_role"),
        ),
        sa.CheckConstraint(
            "status IN "
            "('complete', 'queued', 'running', 'failed', 'timed_out', 'cancelled')",
            name=op.f("ck_ai_conversation_messages_status"),
        ),
        sa.CheckConstraint(
            "char_length(content) <= 100000",
            name=op.f("ck_ai_conversation_messages_content_length"),
        ),
        sa.CheckConstraint(
            "(error_code IS NULL) = (error_message IS NULL)",
            name=op.f("ck_ai_conversation_messages_error_pair"),
        ),
        sa.CheckConstraint(
            "(role = 'user' AND status = 'complete' "
            "AND char_length(btrim(content)) BETWEEN 1 AND 12000 "
            "AND error_code IS NULL AND completed_at IS NOT NULL) "
            "OR (role = 'assistant' AND status IN ('queued', 'running') "
            "AND error_code IS NULL AND completed_at IS NULL) "
            "OR (role = 'assistant' AND status = 'complete' "
            "AND char_length(btrim(content)) BETWEEN 1 AND 100000 "
            "AND error_code IS NULL AND completed_at IS NOT NULL) "
            "OR (role = 'assistant' AND status IN ('failed', 'timed_out') "
            "AND error_code IS NOT NULL AND completed_at IS NOT NULL) "
            "OR (role = 'assistant' AND status = 'cancelled' "
            "AND error_code IS NULL AND completed_at IS NOT NULL)",
            name=op.f("ck_ai_conversation_messages_state"),
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id", "owner_id"],
            ["ai_conversations.id", "ai_conversations.owner_id"],
            name="fk_ai_conversation_messages_conversation_owner",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name=op.f("pk_ai_conversation_messages"),
        ),
        sa.UniqueConstraint(
            "id",
            "owner_id",
            name="uq_ai_conversation_messages_id_owner",
        ),
        sa.UniqueConstraint(
            "id",
            "owner_id",
            "conversation_id",
            name="uq_ai_conversation_messages_id_owner_conversation",
        ),
        sa.UniqueConstraint(
            "conversation_id",
            "sequence",
            name="uq_ai_conversation_messages_sequence",
        ),
    )
    op.create_index(
        "ix_ai_conversation_messages_owner_created",
        "ai_conversation_messages",
        ["owner_id", "created_at", "id"],
    )
    op.create_table(
        "ai_chat_generations",
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("request_message_id", sa.Uuid(), nullable=False),
        sa.Column("response_message_id", sa.Uuid(), nullable=False),
        sa.Column("history_through_sequence", sa.Integer(), nullable=False),
        sa.Column(
            "context_message_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "stream_generation",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "history_through_sequence >= 1",
            name=op.f("ck_ai_chat_generations_history_sequence"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(context_message_ids) = 'array' "
            "AND jsonb_array_length(context_message_ids) <= 40",
            name=op.f("ck_ai_chat_generations_context_message_ids"),
        ),
        sa.CheckConstraint(
            "stream_generation BETWEEN 0 AND 5",
            name=op.f("ck_ai_chat_generations_stream_generation"),
        ),
        sa.CheckConstraint(
            "request_message_id <> response_message_id",
            name=op.f("ck_ai_chat_generations_different_messages"),
        ),
        sa.ForeignKeyConstraint(
            ["job_id", "owner_id"],
            ["ai_jobs.id", "ai_jobs.owner_id"],
            name="fk_ai_chat_generations_job_owner",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id", "owner_id"],
            ["ai_conversations.id", "ai_conversations.owner_id"],
            name="fk_ai_chat_generations_conversation_owner",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["request_message_id", "owner_id", "conversation_id"],
            [
                "ai_conversation_messages.id",
                "ai_conversation_messages.owner_id",
                "ai_conversation_messages.conversation_id",
            ],
            name="fk_ai_chat_generations_request_message",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["response_message_id", "owner_id", "conversation_id"],
            [
                "ai_conversation_messages.id",
                "ai_conversation_messages.owner_id",
                "ai_conversation_messages.conversation_id",
            ],
            name="fk_ai_chat_generations_response_message",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("job_id", name=op.f("pk_ai_chat_generations")),
        sa.UniqueConstraint(
            "request_message_id",
            name="uq_ai_chat_generations_request_message",
        ),
        sa.UniqueConstraint(
            "response_message_id",
            name="uq_ai_chat_generations_response_message",
        ),
    )
    op.create_index(
        "ix_ai_chat_generations_owner_conversation",
        "ai_chat_generations",
        ["owner_id", "conversation_id", "job_id"],
    )

    op.create_table(
        "ai_job_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "sequence >= 1",
            name=op.f("ck_ai_job_events_sequence"),
        ),
        sa.CheckConstraint(
            "generation BETWEEN 0 AND 5",
            name=op.f("ck_ai_job_events_generation"),
        ),
        sa.CheckConstraint(
            "kind IN ('job_state', 'content_reset', 'content_delta')",
            name=op.f("ck_ai_job_events_kind"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(payload) = 'object'",
            name=op.f("ck_ai_job_events_payload"),
        ),
        sa.CheckConstraint(
            "expires_at >= created_at",
            name=op.f("ck_ai_job_events_expires_at"),
        ),
        sa.ForeignKeyConstraint(
            ["job_id", "owner_id"],
            ["ai_jobs.id", "ai_jobs.owner_id"],
            name="fk_ai_job_events_job_owner",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ai_job_events")),
        sa.UniqueConstraint(
            "job_id",
            "sequence",
            name="uq_ai_job_events_sequence",
        ),
    )
    op.create_index(
        "ix_ai_job_events_owner_job_sequence",
        "ai_job_events",
        ["owner_id", "job_id", "sequence"],
    )
    op.create_index(
        "ix_ai_job_events_expiry",
        "ai_job_events",
        ["expires_at", "id"],
    )
    op.execute(
        """
        CREATE FUNCTION prevent_ai_message_identity_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF ROW(
                NEW.id,
                NEW.owner_id,
                NEW.conversation_id,
                NEW.sequence,
                NEW.role,
                NEW.created_at
            ) IS DISTINCT FROM ROW(
                OLD.id,
                OLD.owner_id,
                OLD.conversation_id,
                OLD.sequence,
                OLD.role,
                OLD.created_at
            ) THEN
                RAISE EXCEPTION 'AI message identity fields are immutable'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_ai_conversation_messages_immutable
        BEFORE UPDATE ON ai_conversation_messages
        FOR EACH ROW
        EXECUTE FUNCTION prevent_ai_message_identity_mutation()
        """
    )
    op.execute(
        """
        CREATE FUNCTION enforce_ai_conversation_job_mapping()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            target_job_id uuid;
            job_capability text;
            job_owner_id uuid;
            job_conversation_id uuid;
            job_exists boolean;
            mapping_count integer;
            valid_mapping_count integer;
        BEGIN
            IF TG_TABLE_NAME = 'ai_jobs' THEN
                target_job_id := CASE
                    WHEN TG_OP = 'DELETE' THEN OLD.id
                    ELSE NEW.id
                END;
            ELSE
                target_job_id := CASE
                    WHEN TG_OP = 'DELETE' THEN OLD.job_id
                    ELSE NEW.job_id
                END;
            END IF;

            SELECT capability, owner_id, conversation_id
            INTO job_capability, job_owner_id, job_conversation_id
            FROM ai_jobs
            WHERE id = target_job_id;
            job_exists := FOUND;

            SELECT count(*)
            INTO mapping_count
            FROM ai_chat_generations
            WHERE job_id = target_job_id;

            IF NOT job_exists THEN
                IF mapping_count <> 0 THEN
                    RAISE EXCEPTION 'AI chat generation has no job'
                        USING ERRCODE = '23514';
                END IF;
                RETURN NULL;
            END IF;

            IF job_capability = 'conversation' THEN
                SELECT count(*)
                INTO valid_mapping_count
                FROM ai_chat_generations AS generation
                JOIN ai_conversation_messages AS request_message
                  ON request_message.id = generation.request_message_id
                 AND request_message.owner_id = generation.owner_id
                 AND request_message.conversation_id = generation.conversation_id
                JOIN ai_conversation_messages AS response_message
                  ON response_message.id = generation.response_message_id
                 AND response_message.owner_id = generation.owner_id
                 AND response_message.conversation_id = generation.conversation_id
                WHERE generation.job_id = target_job_id
                  AND generation.owner_id = job_owner_id
                  AND generation.conversation_id = job_conversation_id
                  AND request_message.role = 'user'
                  AND response_message.role = 'assistant';

                IF mapping_count <> 1 OR valid_mapping_count <> 1 THEN
                    RAISE EXCEPTION
                        'conversation AI job requires exactly one valid chat generation'
                        USING ERRCODE = '23514';
                END IF;
            ELSIF mapping_count <> 0 THEN
                RAISE EXCEPTION
                    'non-conversation AI job cannot have a chat generation'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NULL;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER trg_ai_jobs_conversation_mapping
        AFTER INSERT OR UPDATE OR DELETE ON ai_jobs
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW
        EXECUTE FUNCTION enforce_ai_conversation_job_mapping()
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER trg_ai_chat_generations_job_mapping
        AFTER INSERT OR UPDATE OR DELETE ON ai_chat_generations
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW
        EXECUTE FUNCTION enforce_ai_conversation_job_mapping()
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM ai_conversations)
               OR EXISTS (SELECT 1 FROM ai_conversation_messages)
               OR EXISTS (SELECT 1 FROM ai_chat_generations)
               OR EXISTS (SELECT 1 FROM ai_job_events)
               OR EXISTS (
                    SELECT 1 FROM ai_jobs WHERE capability = 'conversation'
               )
               OR EXISTS (
                    SELECT 1 FROM ai_config_versions
                    WHERE capability = 'conversation'
               )
               OR EXISTS (
                    SELECT 1 FROM ai_config_capabilities
                    WHERE capability = 'conversation'
                      AND (
                        next_version <> 1
                        OR current_version_id IS NOT NULL
                      )
               ) THEN
                RAISE EXCEPTION
                    'cannot downgrade 0017_ai_conversations while conversation data exists';
            END IF;
        END
        $$;
        """
    )
    op.execute(
        """
        DELETE FROM user_operations
        WHERE scope LIKE 'ai_conversation.%'
           OR scope LIKE 'ai_message.%'
        """
    )

    op.drop_index("ix_ai_job_events_expiry", table_name="ai_job_events")
    op.drop_index(
        "ix_ai_job_events_owner_job_sequence",
        table_name="ai_job_events",
    )
    op.drop_table("ai_job_events")
    op.execute(
        "DROP TRIGGER trg_ai_chat_generations_job_mapping ON ai_chat_generations"
    )
    op.execute("DROP TRIGGER trg_ai_jobs_conversation_mapping ON ai_jobs")
    op.execute("DROP FUNCTION enforce_ai_conversation_job_mapping()")
    op.execute(
        "DROP TRIGGER trg_ai_conversation_messages_immutable "
        "ON ai_conversation_messages"
    )
    op.execute("DROP FUNCTION prevent_ai_message_identity_mutation()")
    op.drop_index(
        "ix_ai_chat_generations_owner_conversation",
        table_name="ai_chat_generations",
    )
    op.drop_table("ai_chat_generations")
    op.drop_index(
        "ix_ai_conversation_messages_owner_created",
        table_name="ai_conversation_messages",
    )
    op.drop_table("ai_conversation_messages")
    op.drop_index("uq_ai_jobs_active_conversation", table_name="ai_jobs")
    op.drop_constraint(
        "fk_ai_jobs_conversation_owner",
        "ai_jobs",
        type_="foreignkey",
    )
    op.drop_index(
        "ix_ai_conversations_owner_archived_updated",
        table_name="ai_conversations",
    )
    op.drop_table("ai_conversations")

    op.drop_index("ix_ai_jobs_capability_due", table_name="ai_jobs")
    op.drop_constraint(
        op.f("ck_ai_jobs_input_reference"),
        "ai_jobs",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_ai_jobs_last_event_sequence"),
        "ai_jobs",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_ai_jobs_capability"),
        "ai_jobs",
        type_="check",
    )
    op.drop_column("ai_jobs", "conversation_id")
    op.drop_column("ai_jobs", "last_event_sequence")
    op.alter_column(
        "ai_jobs",
        "input_media_id",
        existing_type=sa.Uuid(),
        nullable=False,
    )
    op.create_check_constraint(
        op.f("ck_ai_jobs_capability"),
        "ai_jobs",
        "capability IN ('calendar_screenshot')",
    )

    op.drop_constraint(
        op.f("ck_ai_config_versions_capability"),
        "ai_config_versions",
        type_="check",
    )
    op.execute(
        "DELETE FROM ai_config_capabilities WHERE capability = 'conversation'"
    )
    op.create_check_constraint(
        op.f("ck_ai_config_versions_capability"),
        "ai_config_versions",
        "capability IN ('calendar_screenshot')",
    )
    op.drop_constraint(
        op.f("ck_ai_config_capabilities_capability"),
        "ai_config_capabilities",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_ai_config_capabilities_capability"),
        "ai_config_capabilities",
        "capability IN ('calendar_screenshot')",
    )
