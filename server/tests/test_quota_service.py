import asyncio
from datetime import UTC, datetime
from uuid import UUID, uuid4

import psycopg
import pytest
from cryptography.fernet import Fernet

from itime.ai.models import AIJob
from itime.config import Settings
from itime.database import Database
from itime.errors import AppError
from itime.planning import models as _planning_models  # noqa: F401
from itime.quota.service import (
    BudgetEstimate,
    activity_window,
    estimate_reservation_budget,
    get_or_create_daily_allowance,
    quota_snapshot,
    reserve_job_budget,
)
from tests.conftest import isolated_database, psycopg_url

pytestmark = pytest.mark.integration


def seed_quota_context(database_url: str, *, allowance_nanos: int) -> tuple[str, str]:
    owner_id = uuid4()
    policy_id = uuid4()
    config_id = uuid4()
    settings = Settings(database_url=database_url, environment="test")
    cipher = Fernet(
        (settings.ai_encryption_key or settings.encryption_key).get_secret_value()
    )
    with psycopg.connect(
        psycopg_url(database_url),
        autocommit=True,
    ) as connection:
        connection.execute(
            """
            INSERT INTO users (id, email, password_hash, email_verified_at)
            VALUES (%s, %s, 'test-password-hash', now())
            """,
            (owner_id, f"{owner_id}@example.com"),
        )
        connection.execute(
            """
            INSERT INTO user_time_settings (owner_id, time_zone)
            VALUES (%s, 'Asia/Shanghai')
            """,
            (owner_id,),
        )
        connection.execute(
            """
            INSERT INTO ai_quota_policy_versions (
                id, version, currency, daily_allowance_nanos,
                reservation_estimator_version, status, published_at
            ) VALUES (
                %s, 1, 'CNY', %s,
                'reservation-utf8-vision-v1', 'published', now()
            )
            """,
            (policy_id, allowance_nanos),
        )
        connection.execute(
            """
            UPDATE ai_quota_policy_state
            SET current_version_id=%s, next_version=2, updated_at=now()
            WHERE id=1
            """,
            (policy_id,),
        )
        version = connection.execute(
            """
            UPDATE ai_config_capabilities
            SET next_version=next_version + 1
            WHERE capability='calendar_screenshot'
            RETURNING next_version - 1
            """
        ).fetchone()[0]
        connection.execute(
            """
            INSERT INTO ai_config_versions (
                id, capability, version, endpoint_url, model,
                api_key_ciphertext, timeout_seconds, max_output_tokens,
                price_currency, input_price_nanos_per_million_tokens,
                cached_input_price_nanos_per_million_tokens,
                output_price_nanos_per_million_tokens, status
            ) VALUES (
                %s, 'calendar_screenshot', %s, 'https://ai.example.com/v1',
                'quota-model', %s, 60, 100, 'CNY',
                1000000000, 500000000, 2000000000, 'draft'
            )
            """,
            (config_id, version, cipher.encrypt(b"quota-key")),
        )
    return str(owner_id), str(config_id)


def test_activity_window_uses_local_four_am_and_dst():
    shanghai = activity_window(
        datetime(2026, 10, 2, 19, 59, tzinfo=UTC),
        "Asia/Shanghai",
        240,
    )
    assert shanghai.activity_date.isoformat() == "2026-10-02"
    assert shanghai.window_start_at == datetime(2026, 10, 1, 20, 0, tzinfo=UTC)
    assert shanghai.window_end_at == datetime(2026, 10, 2, 20, 0, tzinfo=UTC)

    new_york = activity_window(
        datetime(2026, 3, 8, 8, 0, tzinfo=UTC),
        "America/New_York",
        240,
    )
    assert new_york.activity_date.isoformat() == "2026-03-08"
    assert new_york.window_start_at == datetime(2026, 3, 8, 8, 0, tzinfo=UTC)
    assert new_york.window_end_at == datetime(2026, 3, 9, 8, 0, tzinfo=UTC)


def test_reservation_estimate_uses_max_output_and_priced_input():
    class Config:
        max_output_tokens = 100
        price_currency = "CNY"
        input_price_nanos_per_million_tokens = 1_000_000_000
        cached_input_price_nanos_per_million_tokens = 500_000_000
        output_price_nanos_per_million_tokens = 2_000_000_000

    estimate = estimate_reservation_budget(
        Config(),
        request_body={
            "messages": [
                {"role": "system", "content": "系统"},
                {"role": "user", "content": "安排今天的任务"},
            ]
        },
        input_summary={},
        capability="conversation",
    )
    assert estimate.input_tokens > 0
    assert estimate.output_tokens == 100
    assert estimate.reserved_nanos >= 200_000
    assert estimate.estimator_version == "reservation-utf8-vision-v1"


def test_timezone_change_reuses_covering_allowance(admin_url):
    with isolated_database(admin_url) as database_url:
        owner_id, _ = seed_quota_context(
            database_url,
            allowance_nanos=1_000_000,
        )
        accepted_at = datetime(2026, 10, 3, 0, 0, tzinfo=UTC)

        async def execute() -> tuple[str, str]:
            database = Database(
                Settings(database_url=database_url, environment="test")
            )
            try:
                async with database.transaction() as session:
                    first = await get_or_create_daily_allowance(
                        session,
                        UUID(owner_id),
                        accepted_at=accepted_at,
                    )
                with psycopg.connect(
                    psycopg_url(database_url),
                    autocommit=True,
                ) as connection:
                    connection.execute(
                        """
                        UPDATE user_time_settings
                        SET time_zone='America/Los_Angeles',
                            version=version + 1,
                            updated_at=now()
                        WHERE owner_id=%s
                        """,
                        (owner_id,),
                    )
                async with database.transaction() as session:
                    second = await get_or_create_daily_allowance(
                        session,
                        UUID(owner_id),
                        accepted_at=accepted_at,
                    )
                return str(first.id), str(second.id)
            finally:
                await database.close()

        first_id, second_id = asyncio.run(execute())
        assert first_id == second_id


def test_concurrent_reservations_cannot_overdraw(admin_url):
    with isolated_database(admin_url) as database_url:
        owner_id, config_id = seed_quota_context(
            database_url,
            allowance_nanos=500_000,
        )
        media_ids = [uuid4(), uuid4()]
        with psycopg.connect(
            psycopg_url(database_url),
            autocommit=True,
        ) as connection:
            for index, media_id in enumerate(media_ids):
                connection.execute(
                    """
                    INSERT INTO temporary_media (
                        id, owner_id, purpose, original_filename,
                        content_type, content_sha256, byte_size,
                        width, height, encrypted_content, expires_at
                    ) VALUES (
                        %s, %s, 'calendar_screenshot', %s,
                        'image/png', %s, 16, 2, 2, %s,
                        now() + interval '1 day'
                    )
                    """,
                    (
                        media_id,
                        owner_id,
                        f"quota-{index}.png",
                        str(index) * 64,
                        b"encrypted-image",
                    ),
                )

        async def reserve(media_id) -> str:
            database = Database(
                Settings(database_url=database_url, environment="test")
            )
            try:
                async with database.transaction() as session:
                    job = AIJob(
                        id=uuid4(),
                        owner_id=UUID(owner_id),
                        capability="calendar_screenshot",
                        config_version_id=UUID(config_id),
                        input_media_id=media_id,
                        conversation_id=None,
                        quota_mode="user",
                        status="queued",
                        input_summary={"filename": "quota.png"},
                        available_at=datetime.now(UTC),
                        deadline_at=datetime.now(UTC),
                    )
                    session.add(job)
                    await reserve_job_budget(
                        session,
                        owner_id=UUID(owner_id),
                        job=job,
                        accepted_at=datetime(2026, 10, 3, 0, 0, tzinfo=UTC),
                        estimate=BudgetEstimate(
                            currency="CNY",
                            input_tokens=100,
                            output_tokens=100,
                            reserved_nanos=500_000,
                            estimator_version="reservation-utf8-vision-v1",
                        ),
                    )
                    return str(job.id)
            finally:
                await database.close()

        async def reserve_both():
            return await asyncio.gather(
                reserve(media_ids[0]),
                reserve(media_ids[1]),
                return_exceptions=True,
            )

        results = asyncio.run(reserve_both())
        assert sum(isinstance(result, str) for result in results) == 1
        error = next(result for result in results if isinstance(result, Exception))
        assert isinstance(error, AppError)
        assert error.code == "ai_quota_exhausted"

        async def snapshot() -> tuple[int, int]:
            database = Database(
                Settings(database_url=database_url, environment="test")
            )
            try:
                async with database.transaction() as session:
                    daily = await get_or_create_daily_allowance(
                        session,
                        UUID(owner_id),
                        accepted_at=datetime(2026, 10, 3, 0, 0, tzinfo=UTC),
                    )
                    current = await quota_snapshot(session, daily)
                    return current.reserved_nanos, current.available_nanos
            finally:
                await database.close()

        assert asyncio.run(snapshot()) == (500_000, 0)
