import codecs
import csv
import io
import json
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
