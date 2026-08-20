from __future__ import annotations

import json
from typing import Any

from .web_config import WebSettings
from .web_runtime import WebRuntime


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    del context
    runtime = WebRuntime(WebSettings.from_env())
    failures: list[dict[str, str]] = []
    processed = 0
    for record in event.get("Records") or []:
        message_id = str(record.get("messageId") or "")
        try:
            payload = json.loads(record["body"])
            runtime.process_run(
                user_id=str(payload["user_id"]),
                run_id=str(payload["run_id"]),
            )
            processed += 1
        except Exception:  # noqa: BLE001 - SQS retries the opaque job
            failures.append({"itemIdentifier": message_id})
    return {"processed": processed, "batchItemFailures": failures}
