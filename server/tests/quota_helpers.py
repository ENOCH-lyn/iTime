from uuid import uuid4

import psycopg

from tests.conftest import psycopg_url


def ensure_quota_policy(
    database_url: str,
    *,
    currency: str = "CNY",
    daily_allowance_nanos: int = 1_000_000_000,
) -> str:
    with psycopg.connect(
        psycopg_url(database_url),
        autocommit=True,
    ) as connection:
        current = connection.execute(
            """
            SELECT policy.id, policy.currency, policy.daily_allowance_nanos
            FROM ai_quota_policy_state AS state
            LEFT JOIN ai_quota_policy_versions AS policy
              ON policy.id=state.current_version_id
            WHERE state.id=1
            """
        ).fetchone()
        if (
            current is not None
            and current[0] is not None
            and current[1] == currency
            and current[2] == daily_allowance_nanos
        ):
            return str(current[0])
        if current is not None and current[0] is not None:
            connection.execute(
                """
                UPDATE ai_quota_policy_versions
                SET status='retired', retired_at=now(), updated_at=now()
                WHERE id=%s AND status='published'
                """,
                (current[0],),
            )
        policy_id = uuid4()
        version = connection.execute(
            """
            UPDATE ai_quota_policy_state
            SET current_version_id=NULL,
                next_version=next_version + 1,
                updated_at=now()
            WHERE id=1
            RETURNING next_version - 1
            """
        ).fetchone()[0]
        connection.execute(
            """
            INSERT INTO ai_quota_policy_versions (
                id, version, currency, daily_allowance_nanos,
                reservation_estimator_version, status, published_at
            ) VALUES (
                %s, %s, %s, %s, 'reservation-utf8-vision-v1',
                'published', now()
            )
            """,
            (policy_id, version, currency, daily_allowance_nanos),
        )
        connection.execute(
            """
            UPDATE ai_quota_policy_state
            SET current_version_id=%s, updated_at=now()
            WHERE id=1
            """,
            (policy_id,),
        )
        return str(policy_id)
