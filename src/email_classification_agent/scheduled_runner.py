from __future__ import annotations

import logging
import time

from .web_config import WebSettings
from .web_runtime import WebRuntime

LOGGER = logging.getLogger(__name__)


def run_due_users() -> int:
    settings = WebSettings.from_env()
    runtime = WebRuntime(settings)
    now = int(time.time())
    queued = 0
    for scheduled in runtime.store.list_automatic_users(limit=100, due_before=now):
        if scheduled.consent_version != settings.disclosure_version:
            runtime.store.pause_automatic_user(
                scheduled.user_id,
                scheduled.next_run_at,
                scheduled.connection_version,
                scheduled.consent_version,
            )
            continue
        if not runtime.store.claim_automatic_user(
            scheduled.user_id,
            expected_next_run_at=scheduled.next_run_at,
            new_next_run_at=now + settings.scheduler_claim_delay_seconds,
            connection_version=scheduled.connection_version,
        ):
            continue
        user = runtime.store.get_user(scheduled.user_id)
        if user is None:
            continue
        if not runtime.store.consume_quota(
            user.user_id,
            user.connection_version,
            "automatic-runs",
            settings.automatic_runs_per_day,
            86_400,
            now,
        ):
            continue
        try:
            run = runtime.new_run(user, mode="automatic")
            queued += 1
            if runtime.queue is None:
                runtime.process_run(user.user_id, run.run_id)
        except Exception:
            LOGGER.exception("scheduled_run_failed user_id_hash=%s", user.user_id[:8])
    return queued


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    queued = run_due_users()
    LOGGER.info("scheduled_runner_complete queued=%s", queued)


if __name__ == "__main__":
    main()
