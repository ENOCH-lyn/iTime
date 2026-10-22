"""Persistent Agent runs for the LangGraph tool loop."""

from alembic import op

revision = "0057_agent_runs"
down_revision = "0056_term_proposals"
branch_labels = None
depends_on = None

_CONVERSATION_MAPPING = """
CREATE OR REPLACE FUNCTION enforce_ai_conversation_job_mapping()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    target_job_id uuid;
    job_capability text;
    job_owner_id uuid;
    job_conversation_id uuid;
    job_resource_type text;
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

    SELECT capability, owner_id, conversation_id, input_summary->>'resource_type'
    INTO job_capability, job_owner_id, job_conversation_id, job_resource_type
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

    IF job_capability = 'conversation'
       OR (job_capability = 'planning'
           AND job_conversation_id IS NOT NULL) THEN
        SELECT count(*)
        INTO valid_mapping_count
        FROM ai_chat_generations AS generation
        LEFT JOIN ai_conversation_messages AS request_message
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
          AND (
              request_message.role = 'user'
              OR (
                  generation.request_message_id IS NULL
                  AND job_resource_type = 'agent_resume'
              )
          )
          AND response_message.role = 'assistant';

        IF mapping_count <> 1 OR valid_mapping_count <> 1 THEN
            RAISE EXCEPTION
                'conversation-linked AI job requires exactly one valid chat generation'
                USING ERRCODE = '23514';
        END IF;
    ELSIF mapping_count <> 0 THEN
        RAISE EXCEPTION
            'AI job without a conversation cannot have a chat generation'
            USING ERRCODE = '23514';
    END IF;
    RETURN NULL;
END;
$$
"""

_ORIGINAL_CONVERSATION_MAPPING = """
CREATE OR REPLACE FUNCTION enforce_ai_conversation_job_mapping()
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

    IF job_capability = 'conversation'
       OR (job_capability = 'planning'
           AND job_conversation_id IS NOT NULL) THEN
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
                'conversation-linked AI job requires exactly one valid chat generation'
                USING ERRCODE = '23514';
        END IF;
    ELSIF mapping_count <> 0 THEN
        RAISE EXCEPTION
            'AI job without a conversation cannot have a chat generation'
            USING ERRCODE = '23514';
    END IF;
    RETURN NULL;
END;
$$
"""


def upgrade() -> None:
    op.execute(
        "ALTER TABLE ai_chat_generations "
        "ALTER COLUMN request_message_id DROP NOT NULL"
    )
    op.execute(_CONVERSATION_MAPPING)
    op.execute(
        """
        CREATE TABLE ai_agent_runs (
            id UUID PRIMARY KEY,
            owner_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            conversation_id UUID NOT NULL
                REFERENCES ai_conversations(id) ON DELETE CASCADE,
            source_message_id UUID
                REFERENCES ai_conversation_messages(id) ON DELETE SET NULL,
            last_job_id UUID REFERENCES ai_jobs(id) ON DELETE SET NULL,
            pending_proposal_id UUID
                REFERENCES ai_action_proposals(id) ON DELETE SET NULL,
            thread_id VARCHAR(64) NOT NULL UNIQUE,
            status VARCHAR(20) NOT NULL DEFAULT 'running'
                CHECK (status IN (
                    'running','waiting_approval','completed','failed','cancelled'
                )),
            step INTEGER NOT NULL DEFAULT 0 CHECK (step >= 0),
            context JSONB NOT NULL DEFAULT '{}'::jsonb
                CHECK (jsonb_typeof(context) = 'object'),
            image_media_id UUID REFERENCES temporary_media(id) ON DELETE SET NULL,
            last_content VARCHAR(100000) NOT NULL DEFAULT '',
            error_code VARCHAR(80),
            error_message VARCHAR(2000),
            version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_ai_agent_runs_owner_conversation "
        "ON ai_agent_runs(owner_id, conversation_id, status)"
    )
    op.execute(
        "CREATE INDEX ix_ai_agent_runs_pending_proposal "
        "ON ai_agent_runs(pending_proposal_id)"
    )


def downgrade() -> None:
    op.execute(_ORIGINAL_CONVERSATION_MAPPING)
    op.execute("DROP TABLE ai_agent_runs")
    op.execute(
        "ALTER TABLE ai_chat_generations "
        "ALTER COLUMN request_message_id SET NOT NULL"
    )
