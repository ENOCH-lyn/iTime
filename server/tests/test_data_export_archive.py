import codecs
import csv
import io
import json
from datetime import UTC, datetime
from hashlib import sha256
from uuid import uuid4
from zipfile import ZipFile

import pytest
from icalendar import Calendar

from itime.data_export.archive import (
    EXPECTED_EXPORT_PATHS,
    ExportBuildError,
    ExportLimits,
    build_export_archive,
)


def sample_snapshot() -> dict:
    snapshot_at = "2026-10-04T08:09:10Z"
    task_id = str(uuid4())
    version_id = str(uuid4())
    return {
        "schema_version": 1,
        "snapshot_at": snapshot_at,
        "account": {
            "profile": {
                "id": str(uuid4()),
                "email": "owner@example.com",
                "created_at": snapshot_at,
            },
            "devices": [],
            "security_events": [],
        },
        "settings": {"time": None, "privacy": None, "memory": None},
        "tasks": {
            "projects": [
                {
                    "id": str(uuid4()),
                    "kind": "project",
                    "name": "=HYPERLINK(\"https://example.com\")",
                    "description": "项目说明",
                    "version": 1,
                    "created_at": snapshot_at,
                    "updated_at": snapshot_at,
                }
            ],
            "tasks": [
                {
                    "id": task_id,
                    "project_id": None,
                    "title": "整理 [资料]",
                    "description": "第一行\n第二行",
                    "estimated_minutes": 30,
                    "estimate_source": "user",
                    "priority": 2,
                    "status": "open",
                    "completed_at": None,
                    "archived_at": None,
                    "deleted_at": None,
                    "purge_after": None,
                    "purged_at": None,
                    "version": 1,
                    "created_at": snapshot_at,
                    "updated_at": snapshot_at,
                }
            ],
            "schedules": [
                {
                    "task_id": task_id,
                    "due_kind": "datetime",
                    "start_date": "2026-10-05",
                    "last_occurrence_date": "2026-10-05",
                    "due_local_time": "10:00:00",
                    "due_at": "2026-10-05T02:00:00Z",
                    "time_zone": "Asia/Shanghai",
                    "recurrence_frequency": "none",
                    "recurrence_interval": 1,
                    "recurrence_weekdays": [],
                }
            ],
            "occurrences": [],
            "labels": [],
            "task_labels": [],
            "steps": [],
            "links": [],
        },
        "calendar": {
            "time_settings": None,
            "terms": [],
            "term_periods": [],
            "sources": [],
            "subscriptions": [],
            "subscription_refreshes": [],
            "subscription_events": [],
            "subscription_overrides": [],
            "subscription_exceptions": [],
            "series": [
                {
                    "id": str(uuid4()),
                    "term_id": None,
                    "source_id": None,
                    "kind": "fixed",
                    "name": "每周例会",
                    "description": "",
                    "location": "会议室",
                    "busy": True,
                    "source_type": "manual",
                    "time_zone": "Asia/Shanghai",
                    "start_date": "2026-10-05",
                    "starts_at": "09:00:00",
                    "ends_at": "10:00:00",
                    "end_day_offset": 0,
                    "recurrence_frequency": "weekly",
                    "recurrence_interval": 1,
                    "recurrence_weekdays": [1],
                    "recurrence_month": None,
                    "recurrence_day_of_month": None,
                    "recurrence_until": "2026-10-26",
                    "recurrence_count": None,
                    "course_week_numbers": [],
                }
            ],
            "import_batches": [],
            "import_candidates": [],
            "import_identities": [],
            "exceptions": [],
            "additions": [],
            "jobs": [],
        },
        "conversations": {
            "conversations": [
                {
                    "id": "conversation-1",
                    "title": "# 计划",
                    "created_at": snapshot_at,
                }
            ],
            "messages": [
                {
                    "id": "message-1",
                    "conversation_id": "conversation-1",
                    "sequence": 1,
                    "role": "user",
                    "status": "complete",
                    "content": "整理 *任务*",
                    "created_at": snapshot_at,
                }
            ],
            "jobs": [],
        },
        "planning": {
            "input_snapshots": [],
            "proposals": [],
            "proposal_changes": [],
            "plans": [{"id": "plan-1", "current_version_id": version_id}],
            "versions": [],
            "blocks": [
                {
                    "id": "block-1",
                    "plan_version_id": version_id,
                    "task_id": task_id,
                    "task_title_snapshot": "整理资料",
                    "starts_at": "2026-10-05T03:00:00Z",
                    "ends_at": "2026-10-05T03:30:00Z",
                    "time_zone": "Asia/Shanghai",
                }
            ],
            "unscheduled_items": [],
            "jobs": [],
        },
        "execution": {
            "focus_sessions": [],
            "focus_events": [],
            "revisions": [],
            "difficulties": [],
        },
        "reviews": {
            "sessions": [],
            "input_snapshots": [],
            "messages": [],
            "reports": [],
            "annotations": [],
            "memory_proposals": [],
            "memories": [
                {
                    "id": "memory-1",
                    "kind": "preference",
                    "title": "专注",
                    "content": "上午工作",
                    "source_type": "manual",
                    "created_at": snapshot_at,
                }
            ],
            "jobs": [],
        },
        "quota": {
            "daily_allowances": [],
            "reservations": [],
            "adjustments": [],
            "ledger": [],
        },
        "media": {"temporary_media": []},
    }


def test_archive_contains_verified_json_csv_ics_and_markdown():
    export_id = uuid4()
    archive = build_export_archive(
        sample_snapshot(),
        export_id,
        ExportLimits(max_uncompressed_bytes=1_000_000, max_archive_bytes=500_000),
    )
    assert archive.sha256 == sha256(archive.content).hexdigest()
    assert archive.byte_size == len(archive.content)
    assert archive.filename == "itime-export-20261004-080910Z.zip"

    with ZipFile(io.BytesIO(archive.content)) as package:
        assert tuple(package.namelist()) == EXPECTED_EXPORT_PATHS
        manifest = json.loads(package.read("manifest.json"))
        exported = json.loads(package.read("itime-data.json"))
        assert exported["tasks"]["tasks"][0]["title"] == "整理 [资料]"
        for path, metadata in manifest["files"].items():
            content = package.read(path)
            assert sha256(content).hexdigest() == metadata["sha256"]
            assert len(content) == metadata["byte_size"]

        project_csv = package.read("csv/projects.csv")
        assert project_csv.startswith(codecs.BOM_UTF8)
        rows = list(csv.DictReader(io.StringIO(project_csv.decode("utf-8-sig"))))
        assert rows[0]["name"].startswith("'=HYPERLINK")

        calendar = Calendar.from_ical(package.read("calendar/itime.ics"))
        summaries = [
            str(component.get("summary"))
            for component in calendar.walk("VEVENT")
        ]
        assert {"整理 [资料]", "每周例会", "整理资料"} <= set(summaries)

        conversations = package.read("markdown/conversations.md").decode()
        assert "\\# 计划" in conversations
        assert "整理 \\*任务\\*" in conversations
        assert "上午工作" in package.read("markdown/memories.md").decode()
        assert "敏感个人信息" in package.read("README.txt").decode()


def test_archive_composes_subscription_occurrence_exceptions():
    snapshot = sample_snapshot()
    source_id = str(uuid4())
    master_id = str(uuid4())
    snapshot["calendar"]["subscription_events"] = [
        {
            "id": master_id,
            "source_id": source_id,
            "uid": "course",
            "recurrence_id": "",
            "baseline": {
                "name": "来源课程",
                "description": "来源说明",
                "location": "来源教室",
                "busy": True,
                "status": "confirmed",
                "start": {
                    "kind": "datetime",
                    "semantic": "utc",
                    "local": "2026-10-05T09:00:00",
                    "time_zone": "Asia/Shanghai",
                    "utc": "2026-10-05T01:00:00Z",
                },
                "duration": {"kind": "seconds", "value": 3600},
                "rrule": "FREQ=DAILY;COUNT=4",
                "rdates": [],
                "exdates": [],
            },
            "parse_status": "valid",
            "source_status": "active",
        }
    ]
    snapshot["calendar"]["subscription_overrides"] = [
        {
            "event_id": master_id,
            "values": {"location": "本地系列地点"},
            "source_values": {"location": "来源教室"},
        }
    ]

    def exception(action: str, day: int, **changes):
        values = {
            "id": str(uuid4()),
            "master_event_id": master_id,
            "source_occurrence_key": (
                f"INSTANT:2026-10-{day:02d}T01:00:00Z"
            ),
            "original_start_at": f"2026-10-{day:02d}T01:00:00Z",
            "original_end_at": f"2026-10-{day:02d}T02:00:00Z",
            "original_all_day": False,
            "action": action,
            "moved_all_day": None,
            "moved_start_date": None,
            "moved_end_date": None,
            "moved_starts_at": None,
            "moved_ends_at": None,
            "moved_time_zone": None,
            "moved_ambiguous_offset_minutes": None,
            "moved_start_at": None,
            "moved_end_at": None,
            "override_name": None,
            "override_description": None,
            "override_location": None,
            "override_busy": None,
        }
        values.update(changes)
        return values

    snapshot["calendar"]["subscription_exceptions"] = [
        exception("cancelled", 6),
        exception(
            "moved",
            7,
            moved_all_day=False,
            moved_start_date="2026-10-08",
            moved_end_date="2026-10-08",
            moved_starts_at="15:00:00",
            moved_ends_at="16:00:00",
            moved_time_zone="Asia/Shanghai",
            moved_start_at="2026-10-08T07:00:00Z",
            moved_end_at="2026-10-08T08:00:00Z",
        ),
        exception(
            "overridden",
            8,
            override_name="本地单次名称",
            override_description="本地单次说明",
        ),
    ]
    archive = build_export_archive(
        snapshot,
        uuid4(),
        ExportLimits(
            max_uncompressed_bytes=1_000_000,
            max_archive_bytes=500_000,
        ),
    )
    with ZipFile(io.BytesIO(archive.content)) as package:
        rows = list(
            csv.DictReader(
                io.StringIO(
                    package.read("csv/calendar-events.csv").decode("utf-8-sig")
                )
            )
        )
        exception_rows = [
            row for row in rows if row["kind"] == "subscription_exception"
        ]
        assert len(exception_rows) == 3
        assert {row["status"] for row in exception_rows} == {
            "cancelled",
            "moved",
            "overridden",
        }

        exported = Calendar.from_ical(package.read("calendar/itime.ics"))
        events = [
            item
            for item in exported.walk("VEVENT")
            if str(item.get("x-itime-kind")) == "calendar_subscription"
        ]
        assert len(events) == 4
        detached = {
            item.decoded("recurrence-id").astimezone(UTC): item
            for item in events
            if item.get("recurrence-id") is not None
        }
        cancelled = detached[datetime(2026, 10, 6, 1, tzinfo=UTC)]
        assert str(cancelled.get("status")) == "CANCELLED"
        moved = detached[datetime(2026, 10, 7, 1, tzinfo=UTC)]
        assert moved.decoded("dtstart").astimezone(UTC) == datetime(
            2026,
            10,
            8,
            7,
            tzinfo=UTC,
        )
        overridden = detached[datetime(2026, 10, 8, 1, tzinfo=UTC)]
        assert str(overridden.get("summary")) == "本地单次名称"
        assert str(overridden.get("description")) == "本地单次说明"
        assert str(overridden.get("location")) == "本地系列地点"


def test_archive_is_stable_and_rejects_size_limits():
    snapshot = sample_snapshot()
    export_id = uuid4()
    limits = ExportLimits(
        max_uncompressed_bytes=1_000_000,
        max_archive_bytes=500_000,
    )
    first = build_export_archive(snapshot, export_id, limits)
    second = build_export_archive(snapshot, export_id, limits)
    assert first.content == second.content

    with pytest.raises(ExportBuildError) as uncompressed:
        build_export_archive(
            snapshot,
            export_id,
            ExportLimits(max_uncompressed_bytes=100, max_archive_bytes=500_000),
        )
    assert uncompressed.value.code == "data_export_too_large"

    with pytest.raises(ExportBuildError) as compressed:
        build_export_archive(
            snapshot,
            export_id,
            ExportLimits(max_uncompressed_bytes=1_000_000, max_archive_bytes=100),
        )
    assert compressed.value.code == "data_export_too_large"
