from __future__ import annotations

import secrets
import time
from dataclasses import replace
from typing import Any

from .multitenant_store import DynamoDbMultiTenantStore
from .universal_models import RunRecord
from .web_config import WebSettings
from .web_runtime import SqsJobQueue


def lambda_handler(event: dict[str, Any] | None, context: Any) -> dict[str, Any]:
    del event, context
    settings = WebSettings.from_env()
    if not settings.table_name or not settings.queue_url:
        raise RuntimeError("Dispatcher requires DynamoDB and SQS configuration")
    store = DynamoDbMultiTenantStore(settings.table_name, region_name=settings.aws_region)
    queue = SqsJobQueue(settings.queue_url, region_name=settings.aws_region)
    queued = 0
    now = int(time.time())
    for user in store.list_automatic_users(limit=100, due_before=now):
        if user.consent_version != settings.disclosure_version:
            store.pause_automatic_user(
                user.user_id,
                user.next_run_at,
                user.connection_version,
                user.consent_version,
            )
            continue
        if not store.claim_automatic_user(
            user.user_id,
            expected_next_run_at=user.next_run_at,
            new_next_run_at=now + settings.automatic_interval_seconds,
            connection_version=user.connection_version,
        ):
            continue
        run = RunRecord(
            run_id=f"{now:010d}-{secrets.token_hex(8)}",
            user_id=user.user_id,
            mode="automatic",
            status="queued",
            policy_hash=user.policy_hash,
            created_at=now,
            updated_at=now,
            expires_at=now + settings.run_ttl_seconds,
            connection_version=user.connection_version,
        )
        if not store.acquire_active_run(
            user.user_id,
            run.run_id,
            now,
            min(run.expires_at, now + 1_800),
            run.connection_version,
        ):
            continue
        if not store.consume_quota(
            user.user_id,
            user.connection_version,
            "automatic-runs",
            settings.automatic_runs_per_day,
            86_400,
            now,
        ):
            store.release_active_run(user.user_id, run.run_id)
            continue
        stored = False
        try:
            store.put_run(run)
            stored = True
            queue.send(user.user_id, run.run_id)
        except Exception:
            try:
                if stored:
                    store.update_run(
                        replace(
                            run,
                            status="failed",
                            updated_at=int(time.time()),
                            error="The automatic run could not be queued.",
                        )
                    )
            finally:
                store.release_active_run(user.user_id, run.run_id)
            raise
        queued += 1
    return {"queued": queued}
