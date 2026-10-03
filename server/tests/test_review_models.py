from datetime import date
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg.errors import CheckViolation, ForeignKeyViolation, UniqueViolation

from itime.database import Base
from itime.review.models import (
    Memory,
    MemoryProposal,
    MemorySettings,
    ReviewAnnotation,
    ReviewInputSnapshot,
    ReviewMessage,
    ReviewReportVersion,
    ReviewSession,
)
from tests.conftest import isolated_database, migrate, psycopg_url

pytestmark = pytest.mark.integration

REVIEW_TABLES = {
    "review_sessions",
    "review_messages",
    "review_input_snapshots",
    "review_report_versions",
    "review_annotations",
    "memory_proposals",
    "memories",
    "memory_settings",
}


def seed_user(connection) -> UUID:
    owner_id = uuid4()
    connection.execute(
        """
        INSERT INTO users (id, email, password_hash, email_verified_at)
        VALUES (%s, %s, 'test-password-hash', now())
        """,
        (owner_id, f"{owner_id}@example.com"),
    )
    return owner_id


def insert_review_session(
    connection,
    owner_id: UUID,
    *,
    activity_date: date = date(2026, 10, 3),
    question_count: int = 0,
) -> UUID:
    review_id = uuid4()
    connection.execute(
        """
        INSERT INTO review_sessions (
            id, owner_id, activity_date, time_zone, supplemental_facts,
            input_revision, question_count, version
        ) VALUES (%s, %s, %s, 'Asia/Shanghai', '', 0, %s, 1)
        """,
        (review_id, owner_id, activity_date, question_count),
    )
    return review_id


def test_review_migration_round_trip_and_orm_consistency(admin_url):
    with isolated_database(admin_url) as database_url:
        with psycopg.connect(psycopg_url(database_url)) as connection:
            tables = {
                row[0]
                for row in connection.execute(
                    """
                    SELECT table_name
                    FROM information_schema.tables
                    WHERE table_schema='public'
                    """
                )
            }
            assert tables >= REVIEW_TABLES
            assert {
                ReviewSession.__table__.name,
                ReviewMessage.__table__.name,
                ReviewInputSnapshot.__table__.name,
                ReviewReportVersion.__table__.name,
                ReviewAnnotation.__table__.name,
                MemoryProposal.__table__.name,
                Memory.__table__.name,
                MemorySettings.__table__.name,
            } == REVIEW_TABLES
            assert Base.metadata.tables.keys() >= REVIEW_TABLES
            assert connection.execute(
                """
                SELECT count(*) FROM ai_config_capabilities
                WHERE capability='review'
                """
            ).fetchone() == (1,)

        migrate(database_url, "downgrade", "0025_execution_difficulties")
        with psycopg.connect(psycopg_url(database_url)) as connection:
            remaining = {
                row[0]
                for row in connection.execute(
                    """
                    SELECT table_name
                    FROM information_schema.tables
                    WHERE table_schema='public'
                    """
                )
            }
            assert not REVIEW_TABLES & remaining
            assert connection.execute(
                """
                SELECT count(*) FROM ai_config_capabilities
                WHERE capability='review'
                """
            ).fetchone() == (0,)

        migrate(database_url, "upgrade", "head")
        migrate(database_url, "check")


def test_one_review_session_per_activity_day_and_question_limit(database_url):
    with psycopg.connect(psycopg_url(database_url)) as connection:
        owner_id = seed_user(connection)
        insert_review_session(connection, owner_id)
        connection.commit()

        with pytest.raises(UniqueViolation):
            with connection.transaction():
                insert_review_session(connection, owner_id)

        with pytest.raises(CheckViolation):
            with connection.transaction():
                insert_review_session(
                    connection,
                    owner_id,
                    activity_date=date(2026, 10, 4),
                    question_count=4,
                )


def test_review_snapshot_owner_and_shape_constraints(database_url):
    with psycopg.connect(psycopg_url(database_url)) as connection:
        owner_id = seed_user(connection)
        other_owner_id = seed_user(connection)
        review_id = insert_review_session(connection, owner_id)
        connection.commit()

        with pytest.raises(ForeignKeyViolation):
            with connection.transaction():
                connection.execute(
                    """
                    INSERT INTO review_input_snapshots (
                        id, owner_id, review_session_id, purpose,
                        activity_date, time_zone, activity_started_at,
                        activity_ended_at, session_input_revision,
                        facts_sha256, facts, user_context, memory_context,
                        generation_instruction
                    ) VALUES (
                        %s, %s, %s, 'report', DATE '2026-10-03',
                        'Asia/Shanghai',
                        TIMESTAMPTZ '2026-10-02 20:00:00+00',
                        TIMESTAMPTZ '2026-10-03 20:00:00+00',
                        0, %s, '{}', '{}', '[]', ''
                    )
                    """,
                    (uuid4(), other_owner_id, review_id, "a" * 64),
                )

        with pytest.raises(CheckViolation):
            with connection.transaction():
                connection.execute(
                    """
                    INSERT INTO review_input_snapshots (
                        id, owner_id, review_session_id, purpose,
                        activity_date, time_zone, activity_started_at,
                        activity_ended_at, session_input_revision,
                        facts_sha256, facts, user_context, memory_context,
                        generation_instruction
                    ) VALUES (
                        %s, %s, %s, 'invalid', DATE '2026-10-03',
                        'Asia/Shanghai',
                        TIMESTAMPTZ '2026-10-02 20:00:00+00',
                        TIMESTAMPTZ '2026-10-03 20:00:00+00',
                        0, %s, '{}', '{}', '[]', ''
                    )
                    """,
                    (uuid4(), owner_id, review_id, "b" * 64),
                )


def test_memory_source_and_lifecycle_constraints(database_url):
    with psycopg.connect(psycopg_url(database_url)) as connection:
        owner_id = seed_user(connection)
        connection.execute(
            """
            INSERT INTO memories (
                id, owner_id, kind, title, content, source_type,
                source_proposal_id, version
            ) VALUES (
                %s, %s, 'preference', '上午安排', '上午适合深度工作',
                'manual', NULL, 1
            )
            """,
            (uuid4(), owner_id),
        )
        connection.commit()

        with pytest.raises(CheckViolation):
            with connection.transaction():
                connection.execute(
                    """
                    INSERT INTO memories (
                        id, owner_id, kind, title, content, source_type,
                        source_proposal_id, version
                    ) VALUES (
                        %s, %s, 'preference', '无来源候选', '无来源候选正文',
                        'review_proposal', NULL, 1
                    )
                    """,
                    (uuid4(), owner_id),
                )

        with pytest.raises(CheckViolation):
            with connection.transaction():
                connection.execute(
                    """
                    INSERT INTO memories (
                        id, owner_id, kind, title, content, source_type,
                        source_proposal_id, deleted_at, purge_after, version
                    ) VALUES (
                        %s, %s, 'context', '非法回收', '仍有正文',
                        'manual', NULL, now(), NULL, 1
                    )
                    """,
                    (uuid4(), owner_id),
                )
