import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from io import BytesIO
from threading import Barrier
from uuid import uuid4

import httpx
import psycopg
import pytest
from openpyxl import Workbook, load_workbook

from itime.calendar.import_parsers import parse_ics, parse_xlsx
from itime.errors import AppError
from tests.conftest import SERVER, LiveServer, isolated_database, migrate, psycopg_url
from tests.test_calendar_series import create_account, login_account

pytestmark = pytest.mark.integration


@pytest.fixture(scope="session")
def import_database_url(admin_url: str) -> Iterator[str]:
    with isolated_database(admin_url) as database_url:
        yield database_url


@pytest.fixture(scope="session")
def import_live_server(
    import_database_url: str,
    tmp_path_factory,
) -> Iterator[LiveServer]:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    log_path = tmp_path_factory.mktemp("import-api") / "server.log"
    with log_path.open("w") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "tests.support_app:create_test_app",
                "--factory",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--no-access-log",
            ],
            cwd=SERVER,
            env={
                **os.environ,
                "ITIME_ENVIRONMENT": "test",
                "ITIME_DATABASE_URL": import_database_url,
                "ITIME_MAX_REQUEST_BYTES": str(6 * 1024 * 1024),
            },
            stdout=log,
            stderr=log,
        )
        try:
            deadline = time.monotonic() + 15
            with httpx.Client(base_url=origin, timeout=1, trust_env=False) as probe:
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        pytest.fail(f"导入测试 API 启动失败，请检查 {log_path}")
                    try:
                        if probe.get("/api/ready").status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    time.sleep(0.05)
                else:
                    pytest.fail(f"导入测试 API 启动超时，请检查 {log_path}")
            yield LiveServer(origin, log_path)
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


@pytest.fixture
def import_client(import_live_server: LiveServer):
    with httpx.Client(
        base_url=import_live_server.origin,
        timeout=15,
        trust_env=False,
    ) as client:
        yield client


def xlsx_bytes(rows: list[list]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "日历安排"
    sheet.append(
        [
            "名称",
            "类型",
            "学期",
            "开始日期",
            "开始时间",
            "结束时间",
            "跨日",
            "开始节次",
            "结束节次",
            "重复",
            "间隔",
            "星期",
            "重复结束日期",
            "重复次数",
            "周次",
            "时区",
            "地点",
            "说明",
            "占用时间",
        ]
    )
    for row in rows:
        sheet.append(row)
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


def xlsx_row(
    name: str,
    *,
    start: str = "2026-10-05",
    starts_at: str = "09:00",
    ends_at: str = "10:00",
    recurrence: str = "不重复",
    weekdays: str = "",
    until: str = "",
) -> list:
    return [
        name,
        "固定安排",
        "",
        start,
        starts_at,
        ends_at,
        0,
        "",
        "",
        recurrence,
        1,
        weekdays,
        until,
        "",
        "",
        "Asia/Shanghai",
        "A101",
        "",
        "是",
    ]


def upload(
    client: httpx.Client,
    filename: str,
    contents: bytes,
    *,
    operation_id=None,
    media_type: str | None = None,
) -> httpx.Response:
    return client.post(
        "/api/v1/calendar/imports/files",
        params={"filename": filename},
        headers={
            "X-Operation-ID": str(operation_id or uuid4()),
            "Content-Type": media_type
            or (
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                if filename.endswith(".xlsx")
                else "text/calendar"
            ),
        },
        content=contents,
    )


def valid_update(candidate: dict, batch: dict, **changes) -> dict:
    draft = {**(candidate["draft"] or {}), **changes}
    return {
        "operation_id": str(uuid4()),
        "expected_batch_version": batch["version"],
        "expected_version": candidate["version"],
        **draft,
    }


def confirm_payload(batch: dict, candidates: list[dict], action: str = "confirm") -> dict:
    return {
        "operation_id": str(uuid4()),
        "expected_batch_version": batch["version"],
        "decisions": [
            {
                "candidate_id": candidate["id"],
                "expected_version": candidate["version"],
                "action": action,
            }
            for candidate in candidates
        ],
    }


def test_parser_isolates_ics_errors_preserves_dst_duration_and_bounds_sparse_xlsx():
    parsed = parse_ics(
        (
            b"BEGIN:VCALENDAR\r\nVERSION:2.0\r\n"
            b"BEGIN:VEVENT\r\nUID:dst@example.com\r\n"
            b"DTSTART;TZID=America/New_York:20260308T013000\r\n"
            b"DURATION:PT2H\r\nSUMMARY:DST duration\r\nEND:VEVENT\r\n"
            b"BEGIN:VEVENT\r\nUID:"
            + b"x"
            * 513
            + b"\r\nDTSTART:20261005T090000Z\r\nDTEND:20261005T100000Z\r\n"
            b"SUMMARY:Too long UID\r\nEND:VEVENT\r\n"
            b"BEGIN:VEVENT\r\nUID:override@example.com\r\n"
            b"RECURRENCE-ID:20261005T090000Z\r\n"
            b"DTSTART:20261005T100000Z\r\nDTEND:20261005T110000Z\r\n"
            b"SUMMARY:Detached\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
        ),
        default_time_zone="America/New_York",
    )
    assert parsed[0].draft["ends_at"] == "04:30:00"
    assert parsed[1].error_code == "value_too_long"
    assert parsed[2].error_code == "unsupported_ics_override"

    malformed = parse_ics(
        (
            b"BEGIN:VCALENDAR\r\nVERSION:2.0\r\n"
            b"BEGIN:VEVENT\r\nUID:bad-rule@example.com\r\n"
            b"DTSTART:20261005T090000Z\r\nDTEND:20261005T100000Z\r\n"
            b"RRULE:FREQ=NOPE;COUNT=X\r\nSUMMARY:Bad rule\r\nEND:VEVENT\r\n"
            b"BEGIN:VEVENT\r\nUID:duplicate-start@example.com\r\n"
            b"DTSTART:20261005T090000Z\r\nDTSTART:20261006T090000Z\r\n"
            b"DTEND:20261005T100000Z\r\nSUMMARY:Duplicate start\r\nEND:VEVENT\r\n"
            b"BEGIN:VEVENT\r\nUID:repeated-duration@example.com\r\n"
            b"DTSTART;TZID=America/New_York:20260308T013000\r\n"
            b"DURATION:PT2H\r\nRRULE:FREQ=WEEKLY;COUNT=2\r\n"
            b"SUMMARY:Repeated duration\r\nEND:VEVENT\r\n"
            b"BEGIN:VEVENT\r\nUID:valid-after-errors@example.com\r\n"
            b"DTSTART:20261007T090000Z\r\nDTEND:20261007T100000Z\r\n"
            b"SUMMARY:Valid after errors\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
        ),
        default_time_zone="America/New_York",
    )
    assert [item.error_code for item in malformed] == [
        "invalid_ics_event",
        "invalid_ics_event",
        "unsupported_ics_duration_recurrence",
        None,
    ]

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "日历安排"
    sheet["A1"] = "名称"
    sheet["A1048576"] = ""
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    with pytest.raises(AppError) as error:
        parse_xlsx(output.getvalue(), term_ids_by_name={})
    assert error.value.code == "too_many_import_candidates"


def test_template_real_xlsx_partial_confirmation_and_duplicate_import(
    import_client,
    import_database_url,
):
    create_account(import_client, import_database_url)
    template = import_client.get("/api/v1/calendar/imports/template.xlsx")
    assert template.status_code == 200, template.text
    workbook = load_workbook(BytesIO(template.content), read_only=True)
    assert workbook.sheetnames == ["日历安排", "填写说明"]
    assert workbook["日历安排"]["A1"].value == "名称"
    workbook.close()

    contents = xlsx_bytes(
        [
            xlsx_row(
                "线性代数",
                recurrence="每周",
                weekdays="1",
                until="2026-10-26",
            ),
            xlsx_row("实验课", starts_at="09:30", ends_at="10:30"),
            ['=CONCAT("危险", "公式")'],
        ]
    )
    operation_id = uuid4()
    created = upload(
        import_client,
        "schedule.xlsx",
        contents,
        operation_id=operation_id,
    )
    assert created.status_code == 201, created.text
    assert (
        upload(
            import_client,
            "schedule.xlsx",
            contents,
            operation_id=operation_id,
        ).json()
        == created.json()
    )
    batch = created.json()["batch"]
    assert batch["candidate_count"] == 3
    assert batch["counts"] == {
        "valid": 2,
        "invalid": 1,
        "duplicate": 0,
        "confirmed": 0,
        "skipped": 0,
    }

    assert (
        import_client.get(
            "/api/v1/calendar/occurrences",
            params={"start": "2026-10-05", "end": "2026-10-06"},
        ).json()["items"]
        == []
    )
    detail = import_client.get(f"/api/v1/calendar/imports/{batch['id']}").json()
    valid = [item for item in detail["candidates"] if item["state"] == "valid"]
    invalid = next(item for item in detail["candidates"] if item["state"] == "invalid")
    assert invalid["error"]["code"] == "xlsx_formula_not_allowed"
    first_confirmation = confirm_payload(batch, [valid[0]])
    confirmed = import_client.post(
        f"/api/v1/calendar/imports/{batch['id']}/confirm",
        json=first_confirmation,
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["batch"]["state"] == "partially_confirmed"
    assert confirmed.json()["outcomes"][0]["outcome"] == "created"
    assert (
        import_client.post(
            f"/api/v1/calendar/imports/{batch['id']}/confirm",
            json=first_confirmation,
        ).json()
        == confirmed.json()
    )

    detail = import_client.get(f"/api/v1/calendar/imports/{batch['id']}").json()
    batch = detail["batch"]
    invalid = next(item for item in detail["candidates"] if item["state"] == "invalid")
    corrected = import_client.put(
        f"/api/v1/calendar/imports/{batch['id']}/candidates/{invalid['id']}",
        json={
            **valid_update(
                invalid,
                batch,
                name="公式已改正",
                start_date="2026-10-06",
                starts_at="11:00:00",
                ends_at="12:00:00",
            ),
            "kind": "fixed",
            "term_id": None,
            "description": "",
            "location": "",
            "busy": True,
            "time_zone": "Asia/Shanghai",
            "end_day_offset": 0,
            "start_period_number": None,
            "end_period_number": None,
            "ambiguous_utc_offset_minutes": None,
            "recurrence": {
                "frequency": "none",
                "interval": 1,
                "weekdays": [],
                "month": None,
                "day_of_month": None,
                "until": None,
                "count": None,
                "week_numbers": [],
            },
        },
    )
    assert corrected.status_code == 200, corrected.text
    assert corrected.json()["candidate"]["state"] == "valid"

    detail = import_client.get(f"/api/v1/calendar/imports/{batch['id']}").json()
    batch = detail["batch"]
    remaining = [item for item in detail["candidates"] if item["state"] in {"valid", "invalid"}]
    final = import_client.post(
        f"/api/v1/calendar/imports/{batch['id']}/confirm",
        json={
            "operation_id": str(uuid4()),
            "expected_batch_version": batch["version"],
            "decisions": [
                {
                    "candidate_id": item["id"],
                    "expected_version": item["version"],
                    "action": "confirm" if item["state"] == "valid" else "skip",
                }
                for item in remaining
            ],
        },
    )
    assert final.status_code == 200, final.text
    assert final.json()["batch"]["state"] == "completed"
    created_outcomes = [item for item in final.json()["outcomes"] if item["outcome"] == "created"]
    assert len(created_outcomes) == 2
    assert any(item["conflicts"]["items"] for item in created_outcomes)

    occurrences = import_client.get(
        "/api/v1/calendar/occurrences",
        params={"start": "2026-10-05", "end": "2026-10-07"},
    ).json()["items"]
    assert {item["name"] for item in occurrences} >= {
        "线性代数",
        "实验课",
        "公式已改正",
    }
    assert all(
        item["source_type"] == "import"
        for item in occurrences
        if item["name"] in {"线性代数", "实验课", "公式已改正"}
    )

    repeated = upload(import_client, "schedule.xlsx", contents)
    assert repeated.status_code == 201, repeated.text
    repeated_detail = import_client.get(
        f"/api/v1/calendar/imports/{repeated.json()['batch']['id']}"
    ).json()
    duplicate = repeated_detail["candidates"][0]
    assert duplicate["state"] == "duplicate"
    assert duplicate["duplicate_series_id"] is not None
    assert "exact_duplicate" in {warning["code"] for warning in duplicate["warnings"]}


def test_ics_recurrence_invalid_rule_update_conflicts_and_uid_deduplication(
    import_client,
    import_database_url,
):
    create_account(
        import_client,
        import_database_url,
        time_zone="America/New_York",
    )
    contents = (
        b"BEGIN:VCALENDAR\r\n"
        b"VERSION:2.0\r\n"
        b"PRODID:-//iTime Test//EN\r\n"
        b"BEGIN:VEVENT\r\n"
        b"UID:weekly@example.com\r\n"
        b"DTSTART;TZID=America/New_York:20261102T090000\r\n"
        b"DTEND;TZID=America/New_York:20261102T100000\r\n"
        b"RRULE:FREQ=WEEKLY;BYDAY=MO,WE;COUNT=4\r\n"
        b"SUMMARY:Weekly seminar\r\n"
        b"END:VEVENT\r\n"
        b"BEGIN:VEVENT\r\n"
        b"UID:unsupported@example.com\r\n"
        b"DTSTART;TZID=America/New_York:20261102T093000\r\n"
        b"DTEND;TZID=America/New_York:20261102T103000\r\n"
        b"RRULE:FREQ=MONTHLY;BYDAY=1MO;COUNT=2\r\n"
        b"SUMMARY:Unsupported event\r\n"
        b"END:VEVENT\r\n"
        b"END:VCALENDAR\r\n"
    )
    created = upload(import_client, "calendar.ics", contents)
    assert created.status_code == 201, created.text
    detail = import_client.get(f"/api/v1/calendar/imports/{created.json()['batch']['id']}").json()
    invalid = next(item for item in detail["candidates"] if item["state"] == "invalid")
    assert invalid["error"]["code"] == "unsupported_ics_recurrence"

    corrected = import_client.put(
        f"/api/v1/calendar/imports/{detail['batch']['id']}/candidates/{invalid['id']}",
        json=valid_update(
            invalid,
            detail["batch"],
            kind="fixed",
            term_id=None,
            name="Corrected event",
            description="",
            location="",
            busy=True,
            time_zone="America/New_York",
            start_date="2026-11-02",
            starts_at="09:30:00",
            ends_at="10:30:00",
            end_day_offset=0,
            start_period_number=None,
            end_period_number=None,
            ambiguous_utc_offset_minutes=None,
            recurrence={
                "frequency": "none",
                "interval": 1,
                "weekdays": [],
                "month": None,
                "day_of_month": None,
                "until": None,
                "count": None,
                "week_numbers": [],
            },
        ),
    )
    assert corrected.status_code == 200, corrected.text
    detail = import_client.get(f"/api/v1/calendar/imports/{detail['batch']['id']}").json()
    candidates = [item for item in detail["candidates"] if item["state"] == "valid"]
    result = import_client.post(
        f"/api/v1/calendar/imports/{detail['batch']['id']}/confirm",
        json=confirm_payload(detail["batch"], candidates),
    )
    assert result.status_code == 200, result.text
    assert result.json()["batch"]["state"] == "completed"
    assert all(item["outcome"] == "created" for item in result.json()["outcomes"])
    assert all(item["conflicts"]["items"] for item in result.json()["outcomes"])

    changed = contents.replace(b"Weekly seminar", b"Weekly seminar changed")
    duplicate_upload = upload(import_client, "changed.ics", changed)
    duplicate_detail = import_client.get(
        f"/api/v1/calendar/imports/{duplicate_upload.json()['batch']['id']}"
    ).json()
    duplicate = next(
        item
        for item in duplicate_detail["candidates"]
        if item["external_uid"] == "weekly@example.com"
    )
    assert duplicate["state"] == "duplicate"
    assert "source_content_changed" in {warning["code"] for warning in duplicate["warnings"]}


def test_import_ownership_expiry_and_invalid_files(
    import_client,
    import_database_url,
):
    first_email, _ = create_account(import_client, import_database_url)
    valid_ics = (
        b"BEGIN:VCALENDAR\r\nVERSION:2.0\r\n"
        b"BEGIN:VEVENT\r\nUID:private@example.com\r\n"
        b"DTSTART:20261005T090000Z\r\nDTEND:20261005T100000Z\r\n"
        b"SUMMARY:Private\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    )
    created = upload(import_client, "private.ics", valid_ics)
    assert created.status_code == 201, created.text
    batch_id = created.json()["batch"]["id"]

    create_account(import_client, import_database_url)
    assert import_client.get(f"/api/v1/calendar/imports/{batch_id}").status_code == 404
    login_account(import_client, first_email)
    with psycopg.connect(
        psycopg_url(import_database_url),
        autocommit=True,
    ) as connection:
        connection.execute(
            "UPDATE calendar_import_batches SET expires_at=%s WHERE id=%s",
            (datetime.now(UTC) - timedelta(seconds=1), batch_id),
        )
    detail = import_client.get(f"/api/v1/calendar/imports/{batch_id}").json()
    assert detail["batch"]["state"] == "expired"
    expired = import_client.post(
        f"/api/v1/calendar/imports/{batch_id}/confirm",
        json=confirm_payload(detail["batch"], [detail["candidates"][0]]),
    )
    assert expired.status_code == 409
    assert expired.json()["error"]["code"] == "calendar_import_expired"

    assert upload(import_client, "broken.xlsx", b"not a zip").json()["error"]["code"] == (
        "invalid_xlsx"
    )
    assert upload(import_client, "calendar.txt", valid_ics).status_code == 415
    assert upload(import_client, "empty.ics", b"").status_code == 413


def test_concurrent_partial_confirm_only_applies_one_batch_version(
    import_client,
    import_live_server,
    import_database_url,
):
    email, _ = create_account(import_client, import_database_url)
    created = upload(
        import_client,
        "concurrent.xlsx",
        xlsx_bytes(
            [
                xlsx_row("并发候选 A", start="2026-12-01"),
                xlsx_row("并发候选 B", start="2026-12-02"),
            ]
        ),
    )
    assert created.status_code == 201, created.text
    detail = import_client.get(f"/api/v1/calendar/imports/{created.json()['batch']['id']}").json()
    first, second = detail["candidates"]
    first_payload = confirm_payload(detail["batch"], [first])
    second_payload = confirm_payload(detail["batch"], [second])
    barrier = Barrier(2)

    def submit(client, payload):
        barrier.wait()
        return client.post(
            f"/api/v1/calendar/imports/{detail['batch']['id']}/confirm",
            json=payload,
        )

    with (
        httpx.Client(
            base_url=import_live_server.origin,
            timeout=15,
            trust_env=False,
        ) as first_client,
        httpx.Client(
            base_url=import_live_server.origin,
            timeout=15,
            trust_env=False,
        ) as second_client,
    ):
        login_account(first_client, email)
        login_account(second_client, email)
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = [
                future.result()
                for future in (
                    pool.submit(submit, first_client, first_payload),
                    pool.submit(submit, second_client, second_payload),
                )
            ]
    assert sorted(response.status_code for response in responses) == [200, 409]
    conflict = next(response for response in responses if response.status_code == 409)
    assert conflict.json()["error"]["code"] == "calendar_import_version_conflict"

    refreshed = import_client.get(f"/api/v1/calendar/imports/{detail['batch']['id']}").json()
    assert refreshed["batch"]["counts"]["confirmed"] == 1
    assert refreshed["batch"]["counts"]["valid"] == 1


def test_confirmation_rolls_back_every_selected_candidate_on_validation_failure(
    import_client,
    import_database_url,
):
    create_account(import_client, import_database_url)
    created = upload(
        import_client,
        "atomic.xlsx",
        xlsx_bytes(
            [
                xlsx_row("事务有效项", start="2027-01-10"),
                ['=CONCAT("invalid")'],
            ]
        ),
    )
    detail = import_client.get(f"/api/v1/calendar/imports/{created.json()['batch']['id']}").json()
    attempted = import_client.post(
        f"/api/v1/calendar/imports/{detail['batch']['id']}/confirm",
        json=confirm_payload(detail["batch"], detail["candidates"]),
    )
    assert attempted.status_code == 409
    assert attempted.json()["error"]["code"] == "invalid_import_candidate"
    unchanged = import_client.get(f"/api/v1/calendar/imports/{detail['batch']['id']}").json()
    assert [item["state"] for item in unchanged["candidates"]] == [
        "valid",
        "invalid",
    ]
    assert (
        import_client.get(
            "/api/v1/calendar/occurrences",
            params={"start": "2027-01-10", "end": "2027-01-11"},
        ).json()["items"]
        == []
    )


def test_file_import_migration_round_trip_and_loss_guard(admin_url):
    with isolated_database(admin_url) as database_url:
        migrate(database_url, "downgrade", "0010_calendar_additions")
        with psycopg.connect(psycopg_url(database_url)) as connection:
            assert (
                connection.execute("SELECT to_regclass('calendar_import_batches')").fetchone()[0]
                is None
            )
            assert (
                connection.execute(
                    """
                SELECT count(*) FROM information_schema.columns
                WHERE table_name='calendar_series' AND column_name='source_id'
                """
                ).fetchone()[0]
                == 0
            )
        migrate(database_url, "upgrade", "head")
        migrate(database_url, "check")

        owner_id = uuid4()
        source_id = uuid4()
        series_id = uuid4()
        with psycopg.connect(
            psycopg_url(database_url),
            autocommit=True,
        ) as connection:
            connection.execute(
                "INSERT INTO users (id, email, password_hash) VALUES (%s, %s, %s)",
                (owner_id, f"migration-{uuid4().hex}@example.com", "hash"),
            )
            connection.execute(
                """
                INSERT INTO calendar_sources (id, owner_id, kind, name)
                VALUES (%s, %s, 'ics_file', 'source.ics')
                """,
                (source_id, owner_id),
            )
            connection.execute(
                """
                INSERT INTO calendar_series (
                    id, owner_id, source_id, kind, name, source_type, time_zone,
                    start_date, starts_at, ends_at, recurrence_frequency,
                    recurrence_weekdays, course_week_numbers
                )
                VALUES (
                    %s, %s, %s, 'fixed', 'Imported', 'import', 'UTC',
                    '2026-10-05', '09:00', '10:00', 'none',
                    '[]'::jsonb, '[]'::jsonb
                )
                """,
                (series_id, owner_id, source_id),
            )
        with pytest.raises(subprocess.CalledProcessError) as error:
            migrate(database_url, "downgrade", "0010_calendar_additions")
        assert "while imported series exist" in error.value.stderr

        with psycopg.connect(
            psycopg_url(database_url),
            autocommit=True,
        ) as connection:
            connection.execute("DELETE FROM calendar_series WHERE id=%s", (series_id,))
        migrate(database_url, "downgrade", "0010_calendar_additions")
        migrate(database_url, "upgrade", "head")
        migrate(database_url, "check")
