import httpx
import psycopg
import pytest

from itime.planning.provider import PLANNING_SYSTEM_PROMPT
from tests.conftest import psycopg_url
from tests.test_calendar_series import create_account
from tests.test_planning_inputs import get_context
from tests.test_tasks import create_task

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def reset_public_link_context(database_url):
    with psycopg.connect(psycopg_url(database_url), autocommit=True) as connection:
        connection.execute("DELETE FROM task_links")


def mark_ready(
    database_url: str,
    link_id: str,
    *,
    position: int,
    text: str,
) -> None:
    with psycopg.connect(psycopg_url(database_url), autocommit=True) as connection:
        connection.execute(
            """
            UPDATE task_links
            SET fetch_status='ready',
                attempt_count=1,
                last_attempt_at=now(),
                last_success_at=now(),
                final_host=%s,
                media_type='text/html',
                page_title=%s,
                summary=%s,
                extracted_text=%s,
                content_sha256=%s,
                last_error_code=NULL,
                last_error_message=NULL,
                updated_at=now()
            WHERE id=%s
            """,
            (
                f"source-{position}.example",
                f"Page {position}",
                f"Summary {position}",
                text,
                f"{position:x}" * 64,
                link_id,
            ),
        )


def test_planning_candidate_contains_bounded_untrusted_public_sources(
    client,
    database_url,
):
    create_account(client, database_url)
    links = [
        {
            "title": f"Source {position}",
            "url": f"https://source-{position}.example/article",
        }
        for position in range(1, 5)
    ]
    task = create_task(
        client,
        title="Research assignment",
        description="Read the linked requirements.",
        estimated_minutes=60,
        steps=[],
        labels=[],
        links=links,
    )
    for position, link in enumerate(task["links"], start=1):
        mark_ready(
            database_url,
            link["id"],
            position=position,
            text=(f"Source body {position} " + "x" * 5000),
        )

    response = get_context(
        client,
        scope="day",
        target_date="2026-10-05",
        task_ids=[task["id"]],
    )

    assert response.status_code == 200, response.text
    context = response.json()["context"]
    candidate = context["task_candidates"][0]
    assert len(candidate["public_sources"]) == 3
    assert candidate["public_sources_truncated"] is True
    first = candidate["public_sources"][0]
    assert first["link_id"] == task["links"][0]["id"]
    assert first["label"] == "Source 1"
    assert first["source_host"] == "source-1.example"
    assert first["final_host"] == "source-1.example"
    assert first["page_title"] == "Page 1"
    assert first["summary"] == "Summary 1"
    assert first["content_excerpt"].startswith("Source body 1")
    assert len(first["content_excerpt"]) == 4000
    assert first["fetched_at"]
    assert first["content_sha256"] == "1" * 64
    assert first["trust"] == "untrusted_public_web"
    assert sum(
        len(source["content_excerpt"])
        for source in candidate["public_sources"]
    ) <= 24_000

    hash_with_sources = context["content_sha256"]
    with psycopg.connect(
        psycopg_url(database_url),
        autocommit=True,
    ) as connection:
        connection.execute(
            """
            UPDATE task_links
            SET summary='Changed summary',
                extracted_text='Changed source body',
                content_sha256=%s,
                updated_at=now()
            WHERE id=%s
            """,
            ("f" * 64, task["links"][0]["id"]),
        )
    changed = get_context(
        client,
        scope="day",
        target_date="2026-10-05",
        task_ids=[task["id"]],
    ).json()["context"]
    assert changed["content_sha256"] != hash_with_sources
    assert changed["task_candidates"][0]["public_sources"][0]["summary"] == (
        "Changed summary"
    )


def test_planning_sources_exclude_failed_and_other_owner_links(
    client,
    database_url,
    live_server,
):
    create_account(client, database_url)
    own = create_task(
        client,
        title="Own task",
        steps=[],
        labels=[],
        links=[
            {
                "title": "Unavailable",
                "url": "https://failed.example/article",
            }
        ],
    )
    with psycopg.connect(
        psycopg_url(database_url),
        autocommit=True,
    ) as connection:
        connection.execute(
            """
            UPDATE task_links
            SET fetch_status='failed',
                last_attempt_at=now(),
                last_error_code='public_link_http_failed',
                last_error_message='网页读取失败。',
                updated_at=now()
            WHERE id=%s
            """,
            (own["links"][0]["id"],),
        )

    with httpx.Client(
        base_url=live_server.origin,
        timeout=10,
        trust_env=False,
    ) as other:
        create_account(other, database_url)
        other_task = create_task(
            other,
            title="Other task",
            steps=[],
            labels=[],
            links=[
                {
                    "title": "Other source",
                    "url": "https://other.example/article",
                }
            ],
        )
        mark_ready(
            database_url,
            other_task["links"][0]["id"],
            position=9,
            text="Other private task source.",
        )

    response = get_context(
        client,
        scope="day",
        target_date="2026-10-05",
        task_ids=[own["id"]],
    )

    assert response.status_code == 200
    candidate = response.json()["context"]["task_candidates"][0]
    assert candidate["public_sources"] == []
    assert candidate["public_sources_truncated"] is False
    assert "Other private task source" not in response.text


def test_planning_prompt_names_public_sources_as_untrusted_data():
    normalized = PLANNING_SYSTEM_PROMPT.casefold()
    assert "public sources" in normalized
    assert "untrusted" in normalized
    assert "tool authorization" in normalized
    assert "server constraints" in normalized
