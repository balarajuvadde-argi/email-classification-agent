from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

POLICY_VERSION = "2026-08-20-v2"

APPROVED_ROOT_DOMAINS: tuple[str, ...] = (
    "zillow.com",
    "trulia.com",
    "streeteasy.com",
    "hotpads.com",
    "outeast.com",
    "realtor.com",
    "move.com",
    "redfin.com",
    "homes.com",
    "craigslist.org",
    "movoto.com",
    "homesnap.com",
    "estately.com",
    "homefinder.com",
    "xome.com",
    "zerodown.com",
)


def _as_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    raise ValueError(f"Invalid boolean value: {value!r}")


def _as_int(value: str | None, default: int, minimum: int = 1) -> int:
    if value is None or not value.strip():
        return default
    parsed = int(value)
    if parsed < minimum:
        raise ValueError(f"Expected integer >= {minimum}, received {parsed}")
    return parsed


def _as_float(value: str | None, default: float, minimum: float, maximum: float) -> float:
    if value is None or not value.strip():
        return default
    parsed = float(value)
    if parsed < minimum or parsed > maximum:
        raise ValueError(f"Expected number in [{minimum}, {maximum}], received {parsed}")
    return parsed


@dataclass(frozen=True, slots=True)
class Settings:
    expected_gmail_address: str | None = None
    gmail_token_file: Path | None = None
    gmail_credentials_json: str | None = None
    gmail_oauth_secret_id: str | None = None
    openai_api_key: str | None = None
    openai_api_key_secret_id: str | None = None
    openai_model: str = "gpt-5-mini"
    aws_region: str | None = None

    wholesale_label: str = "Wholesale"
    processed_label: str = "EmailAgent/Processed/v2"
    gmail_query: str = "in:inbox -in:spam -in:trash"
    approved_root_domains: tuple[str, ...] = field(default=APPROVED_ROOT_DOMAINS)

    dry_run: bool = True
    use_llm: bool = True
    mark_low_confidence_processed: bool = False
    min_llm_wholesale_confidence: float = 0.85
    max_messages_per_run: int = 50
    max_thread_messages: int = 6
    max_body_chars: int = 20_000
    max_thread_context_chars: int = 18_000

    @classmethod
    def from_env(cls) -> Settings:
        token_file_raw = os.getenv("GMAIL_TOKEN_FILE")
        return cls(
            expected_gmail_address=(os.getenv("EXPECTED_GMAIL_ADDRESS") or "").strip() or None,
            gmail_token_file=Path(token_file_raw).expanduser() if token_file_raw else None,
            gmail_credentials_json=(os.getenv("GMAIL_CREDENTIALS_JSON") or "").strip() or None,
            gmail_oauth_secret_id=(os.getenv("GMAIL_OAUTH_SECRET_ID") or "").strip() or None,
            openai_api_key=(os.getenv("OPENAI_API_KEY") or "").strip() or None,
            openai_api_key_secret_id=(os.getenv("OPENAI_API_KEY_SECRET_ID") or "").strip() or None,
            openai_model=(os.getenv("OPENAI_MODEL") or "gpt-5-mini").strip(),
            aws_region=(os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "").strip() or None,
            wholesale_label=(os.getenv("WHOLESALE_LABEL") or "Wholesale").strip(),
            processed_label=(os.getenv("PROCESSED_LABEL") or "EmailAgent/Processed/v2").strip(),
            gmail_query=(os.getenv("GMAIL_QUERY") or "in:inbox -in:spam -in:trash").strip(),
            dry_run=_as_bool(os.getenv("DRY_RUN"), True),
            use_llm=_as_bool(os.getenv("USE_LLM"), True),
            mark_low_confidence_processed=_as_bool(
                os.getenv("MARK_LOW_CONFIDENCE_PROCESSED"), False
            ),
            min_llm_wholesale_confidence=_as_float(
                os.getenv("MIN_LLM_WHOLESALE_CONFIDENCE"), 0.85, 0.0, 1.0
            ),
            max_messages_per_run=_as_int(os.getenv("MAX_MESSAGES_PER_RUN"), 50),
            max_thread_messages=_as_int(os.getenv("MAX_THREAD_MESSAGES"), 6),
            max_body_chars=_as_int(os.getenv("MAX_BODY_CHARS"), 20_000, 1_000),
            max_thread_context_chars=_as_int(
                os.getenv("MAX_THREAD_CONTEXT_CHARS"), 18_000, 1_000
            ),
        )


def secret_json(secret_id: str, region_name: str | None = None) -> dict[str, Any]:
    """Load and parse a JSON secret from AWS Secrets Manager."""
    import boto3

    client = boto3.client("secretsmanager", region_name=region_name)
    response = client.get_secret_value(SecretId=secret_id)
    raw = response.get("SecretString")
    if not raw:
        raise RuntimeError(f"Secret {secret_id!r} does not contain SecretString JSON")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise RuntimeError(f"Secret {secret_id!r} must contain a JSON object")
    return data
