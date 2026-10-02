from itime.calendar.subscription_parser import parse_subscription_ics


def calendar(*events: str) -> bytes:
    return (
        "BEGIN:VCALENDAR\r\n"
        "VERSION:2.0\r\n"
        "PRODID:-//iTime Test//EN\r\n"
        + "".join(events)
        + "END:VCALENDAR\r\n"
    ).encode()


def event(*lines: str) -> str:
    return "BEGIN:VEVENT\r\n" + "\r\n".join(lines) + "\r\nEND:VEVENT\r\n"


def test_parses_unbounded_rule_dates_and_all_day_values():
    snapshot = parse_subscription_ics(
        calendar(
            event(
                "UID:weekly-class",
                "SUMMARY:Weekly class",
                "DTSTART;TZID=America/New_York:20261001T090000",
                "DTEND;TZID=America/New_York:20261001T100000",
                "RRULE:FREQ=WEEKLY;BYDAY=TH",
                "RDATE;TZID=America/New_York:20261002T090000",
                "EXDATE;TZID=America/New_York:20261008T090000",
            ),
            event(
                "UID:holiday",
                "SUMMARY:Holiday",
                "DTSTART;VALUE=DATE:20261003",
                "DTEND;VALUE=DATE:20261005",
            ),
        ),
        default_time_zone="Asia/Shanghai",
    )

    assert snapshot.absence_complete
    assert snapshot.item_errors == []
    weekly = snapshot.items[0].baseline
    assert weekly is not None
    assert weekly["rrule"] == "FREQ=WEEKLY;BYDAY=TH"
    assert weekly["start"]["semantic"] == "iana"
    assert weekly["start"]["time_zone"] == "America/New_York"
    assert len(weekly["rdates"]) == len(weekly["exdates"]) == 1
    all_day = snapshot.items[1].baseline
    assert all_day is not None
    assert all_day["start"]["kind"] == "date"
    assert all_day["duration"] == {"kind": "days", "value": 2}


def test_utc_recurrence_id_matches_iana_occurrence_identity():
    snapshot = parse_subscription_ics(
        calendar(
            event(
                "UID:class",
                "DTSTART;TZID=America/New_York:20261001T090000",
                "DTEND;TZID=America/New_York:20261001T100000",
                "RRULE:FREQ=WEEKLY",
            ),
            event(
                "UID:class",
                "RECURRENCE-ID:20261008T130000Z",
                "DTSTART;TZID=America/New_York:20261008T110000",
                "DTEND;TZID=America/New_York:20261008T120000",
            ),
        ),
        default_time_zone="UTC",
    )

    detached = snapshot.items[1]
    assert detached.recurrence_id == "INSTANT:2026-10-08T13:00:00Z"


def test_cancelled_event_without_dtstart_remains_actionable():
    snapshot = parse_subscription_ics(
        calendar(
            event(
                "UID:class",
                "RECURRENCE-ID:20261008T130000Z",
                "STATUS:CANCELLED",
            )
        ),
        default_time_zone="UTC",
    )

    assert snapshot.items[0].baseline == {
        "uid": "class",
        "recurrence_id": "INSTANT:2026-10-08T13:00:00Z",
        "status": "cancelled",
    }


def test_conflicting_duplicate_identity_is_an_item_error():
    snapshot = parse_subscription_ics(
        calendar(
            event(
                "UID:same",
                "SUMMARY:First",
                "DTSTART:20261001T090000Z",
                "DTEND:20261001T100000Z",
            ),
            event(
                "UID:same",
                "SUMMARY:Second",
                "DTSTART:20261001T090000Z",
                "DTEND:20261001T100000Z",
            ),
        ),
        default_time_zone="UTC",
    )

    assert len(snapshot.items) == 1
    assert snapshot.items[0].baseline is None
    assert snapshot.items[0].error_code == "calendar_subscription_identity_conflict"


def test_unknown_uid_taints_absence_but_known_invalid_event_keeps_identity():
    snapshot = parse_subscription_ics(
        calendar(
            event(
                "SUMMARY:No uid",
                "DTSTART:20261001T090000Z",
                "DTEND:20261001T100000Z",
            ),
            event(
                "UID:known",
                "DTSTART;TZID=Unknown/Zone:20261001T090000",
                "DTEND;TZID=Unknown/Zone:20261001T100000",
            ),
        ),
        default_time_zone="UTC",
    )

    assert not snapshot.absence_complete
    assert snapshot.items[0].uid == "known"
    assert snapshot.items[0].baseline is None
    assert snapshot.items[0].error_code == "calendar_subscription_timezone_unsupported"
