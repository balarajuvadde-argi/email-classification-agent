from __future__ import annotations

import contextvars
import hashlib
import json
import logging
import time
from collections import deque
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

MAX_EVENTS = 2_000
MAX_STRING_CHARS = 500

_EVENT_CACHE: deque[dict[str, Any]] = deque(maxlen=MAX_EVENTS)
_RUN_ID: contextvars.ContextVar[str] = contextvars.ContextVar("run_id", default="")
_USER_ID_HASH: contextvars.ContextVar[str] = contextvars.ContextVar("user_id_hash", default="")


@contextmanager
def event_context(*, run_id: str = "", user_id: str = "") -> Iterator[None]:
    run_token = _RUN_ID.set(run_id)
    user_token = _USER_ID_HASH.set(_hash_user_id(user_id) if user_id else "")
    try:
        yield
    finally:
        _RUN_ID.reset(run_token)
        _USER_ID_HASH.reset(user_token)


def structured_event(logger: logging.Logger, event: str, **fields: Any) -> dict[str, Any]:
    record = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "event": event,
        "run_id": _RUN_ID.get(),
        "user_id_hash": _USER_ID_HASH.get(),
        **_sanitize_mapping(fields),
    }
    _EVENT_CACHE.append(record)
    logger.info(json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return record


def recent_events(
    *,
    limit: int = 200,
    run_id: str | None = None,
    user_id: str | None = None,
) -> list[dict[str, Any]]:
    clean_limit = min(max(limit, 1), MAX_EVENTS)
    user_hash = _hash_user_id(user_id) if user_id else None
    values = []
    for record in reversed(_EVENT_CACHE):
        if run_id and record.get("run_id") != run_id:
            continue
        if user_hash and record.get("user_id_hash") != user_hash:
            continue
        values.append(dict(record))
        if len(values) >= clean_limit:
            break
    return list(reversed(values))


def clear_events_for_tests() -> None:
    _EVENT_CACHE.clear()


def _hash_user_id(user_id: str) -> str:
    return hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:16]


def _sanitize_mapping(fields: dict[str, Any]) -> dict[str, Any]:
    return {str(key): _sanitize_value(value) for key, value in fields.items()}


def _sanitize_value(value: Any) -> Any:
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        compact = " ".join(value.split())
        return compact[:MAX_STRING_CHARS]
    if isinstance(value, dict):
        return {str(key): _sanitize_value(item) for key, item in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [_sanitize_value(item) for item in list(value)[:50]]
    return _sanitize_value(str(value))
