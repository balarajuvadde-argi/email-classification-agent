from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

_DATE_QUERY_TOKEN_RE = re.compile(
    r'(?<!\S)(?:after|before|newer|older):(?:"[^"]+"|\S+)|'
    r'(?<!\S)(?:newer_than|older_than):(?:"[^"]+"|\S+)',
    re.IGNORECASE,
)


def gmail_date(value: datetime) -> str:
    """Return Gmail's slash-separated date operator format without zero padding."""
    return f"{value.year}/{value.month}/{value.day}"


def previous_day_window(
    *,
    now: int | None = None,
    timezone_name: str = "America/New_York",
    lookback_days: int = 1,
) -> tuple[str, str]:
    if lookback_days < 1:
        raise ValueError("lookback_days must be at least 1")
    current = (
        datetime.fromtimestamp(now, tz=UTC)
        if now is not None
        else datetime.now(UTC)
    ).astimezone(ZoneInfo(timezone_name))
    end_date = current.date() - timedelta(days=lookback_days - 1)
    start_date = end_date - timedelta(days=1)
    start = datetime.combine(start_date, datetime.min.time(), tzinfo=current.tzinfo)
    end = datetime.combine(end_date, datetime.min.time(), tzinfo=current.tzinfo)
    return gmail_date(start), gmail_date(end)


def strip_date_query_tokens(query: str) -> str:
    return " ".join(_DATE_QUERY_TOKEN_RE.sub(" ", query).split())


def previous_day_query(
    base_query: str,
    *,
    now: int | None = None,
    timezone_name: str = "America/New_York",
    lookback_days: int = 1,
) -> str:
    start, end = previous_day_window(
        now=now,
        timezone_name=timezone_name,
        lookback_days=lookback_days,
    )
    clean_query = strip_date_query_tokens(base_query)
    return f"{clean_query} after:{start} before:{end}".strip()


def automatic_query(
    base_query: str,
    *,
    mode: str,
    now: int | None = None,
    timezone_name: str = "America/New_York",
    lookback_days: int = 1,
) -> str:
    if mode.casefold() == "default":
        return base_query
    if mode.casefold() == "previous_day":
        return previous_day_query(
            base_query,
            now=now,
            timezone_name=timezone_name,
            lookback_days=lookback_days,
        )
    raise ValueError("Unsupported AUTOMATIC_SCAN_MODE")
