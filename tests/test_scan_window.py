from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from email_classification_agent.scan_window import (
    automatic_query,
    previous_day_query,
    previous_day_window,
    strip_date_query_tokens,
)


def _timestamp(value: datetime) -> int:
    return int(value.timestamp())


def test_previous_day_window_uses_miami_calendar_day() -> None:
    now = _timestamp(datetime(2026, 9, 1, 10, 0, tzinfo=ZoneInfo("America/New_York")))

    assert previous_day_window(now=now, timezone_name="America/New_York") == (
        "2026/8/31",
        "2026/9/1",
    )


def test_previous_day_window_is_dst_safe() -> None:
    now = _timestamp(datetime(2026, 11, 2, 10, 0, tzinfo=ZoneInfo("America/New_York")))

    assert previous_day_window(now=now, timezone_name="America/New_York") == (
        "2026/11/1",
        "2026/11/2",
    )


def test_previous_day_query_replaces_existing_date_filters() -> None:
    query = previous_day_query(
        'in:inbox newer_than:30d after:2026/1/1 before:"2026/2/1" -in:spam',
        now=_timestamp(datetime(2026, 9, 1, 10, 0, tzinfo=ZoneInfo("America/New_York"))),
        timezone_name="America/New_York",
    )

    assert query == "in:inbox -in:spam after:2026/8/31 before:2026/9/1"


def test_automatic_query_default_mode_keeps_saved_query() -> None:
    assert automatic_query("in:inbox -in:spam", mode="default") == "in:inbox -in:spam"


def test_strip_date_query_tokens_leaves_non_date_terms() -> None:
    assert (
        strip_date_query_tokens("in:inbox label:Client older_than:7d subject:deal")
        == "in:inbox label:Client subject:deal"
    )
