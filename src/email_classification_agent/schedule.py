from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo


def parse_local_times(value: str) -> tuple[time, ...]:
    times: list[time] = []
    for raw_part in value.split(","):
        part = raw_part.strip()
        if not part:
            continue
        pieces = part.split(":")
        if len(pieces) != 2:
            raise ValueError("Scheduled run times must use HH:MM format")
        try:
            hour = int(pieces[0])
            minute = int(pieces[1])
        except ValueError as exc:
            raise ValueError("Scheduled run times must use HH:MM format") from exc
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError("Scheduled run times must be valid 24-hour local times")
        times.append(time(hour=hour, minute=minute))
    if not times:
        raise ValueError("At least one scheduled run time is required")
    return tuple(sorted(set(times)))


def _local_datetime(timestamp: int, timezone_name: str) -> datetime:
    return datetime.fromtimestamp(timestamp, tz=UTC).astimezone(ZoneInfo(timezone_name))


def next_scheduled_timestamp(
    timestamp: int,
    *,
    timezone_name: str,
    local_times: tuple[time, ...],
) -> int:
    local_now = _local_datetime(timestamp, timezone_name)
    for day_offset in range(2):
        candidate_date = local_now.date() + timedelta(days=day_offset)
        for local_run_time in local_times:
            candidate = datetime.combine(
                candidate_date,
                local_run_time,
                tzinfo=ZoneInfo(timezone_name),
            )
            if candidate > local_now:
                return int(candidate.astimezone(UTC).timestamp())
    raise RuntimeError("Unable to calculate next scheduled run")


def is_in_scheduled_window(
    timestamp: int,
    *,
    timezone_name: str,
    local_times: tuple[time, ...],
    window_seconds: int,
) -> bool:
    local_now = _local_datetime(timestamp, timezone_name)
    window = timedelta(seconds=window_seconds)
    for local_run_time in local_times:
        window_start = datetime.combine(
            local_now.date(),
            local_run_time,
            tzinfo=ZoneInfo(timezone_name),
        )
        if window_start <= local_now < window_start + window:
            return True
    return False
