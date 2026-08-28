from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .config import secret_json
from .policy_templates import ALL_ACQUISITIONS_PROMPT
from .schedule import parse_local_times

DEFAULT_CLASSIFICATION_PROMPT = ALL_ACQUISITIONS_PROMPT


@dataclass(frozen=True, slots=True)
class WebSettings:
    environment: str
    app_base_url: str
    table_name: str
    kms_key_id: str | None
    local_token_encryption_key: str | None
    google_oauth_secret_id: str | None
    google_oauth_client_config_json: str | None
    google_oauth_client_file: str | None
    openai_api_key_secret_id: str | None
    openai_api_key: str | None
    openai_model: str
    queue_url: str | None
    aws_region: str | None
    database_url: str | None = None
    cookie_name: str = "email_agent_session"
    session_ttl_seconds: int = 86_400
    oauth_state_ttl_seconds: int = 600
    plan_ttl_seconds: int = 900
    run_ttl_seconds: int = 86_400
    automatic_interval_seconds: int = 43_200
    worker_lease_seconds: int = 330
    manual_runs_per_day: int = 50
    eml_previews_per_day: int = 20
    automatic_runs_per_day: int = 2
    service_name: str = "Inbox Pilot"
    operator_name: str = "Local development operator"
    privacy_contact_email: str = "privacy@localhost.invalid"
    support_email: str = "support@localhost.invalid"
    ai_provider_name: str = "OpenAI"
    ai_provider_data_policy_url: str = (
        "https://developers.openai.com/api/docs/guides/your-data"
    )
    privacy_effective_date: str = "2026-08-20"
    privacy_notice_version: str = "2026-08-20.1"
    backup_recovery_days: int = 7
    mvp_mode: bool = False
    property_lookup_enabled: bool = False
    default_classification_prompt: str = DEFAULT_CLASSIFICATION_PROMPT
    default_classification_labels: tuple[str, ...] = (
        "Acquisitions/On Market",
        "Acquisitions/Off Market",
        "Acquisitions/Wholesale",
        "News",
    )
    default_gmail_query: str = "in:inbox"
    default_confidence_threshold: float = 0.85
    default_max_messages_per_run: int = 10
    default_automatic_enabled: bool = True
    scheduler_claim_delay_seconds: int = 3_600
    automatic_schedule_timezone: str = "America/New_York"
    automatic_schedule_local_times: tuple[str, ...] = ("10:00", "17:00")
    automatic_schedule_window_seconds: int = 900

    @property
    def production(self) -> bool:
        return self.environment.casefold() in {"staging", "production"}

    @property
    def secure_transport(self) -> bool:
        return urlsplit(self.app_base_url).scheme.casefold() == "https"

    @property
    def oauth_redirect_uri(self) -> str:
        return f"{self.app_base_url.rstrip('/')}/oauth/google/callback"

    @property
    def disclosure_version(self) -> str:
        value = "|".join(
            (
                self.operator_name,
                self.ai_provider_name,
                self.ai_provider_data_policy_url,
                self.privacy_effective_date,
                self.privacy_notice_version,
            )
        )
        return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]

    @classmethod
    def from_env(cls) -> WebSettings:
        settings = cls(
            environment=(os.getenv("APP_ENV") or "development").strip(),
            app_base_url=(os.getenv("APP_BASE_URL") or "http://localhost:8000").strip(),
            table_name=(os.getenv("MULTITENANT_TABLE_NAME") or "").strip(),
            kms_key_id=(os.getenv("TOKEN_KMS_KEY_ID") or "").strip() or None,
            local_token_encryption_key=(
                os.getenv("LOCAL_TOKEN_ENCRYPTION_KEY") or ""
            ).strip()
            or None,
            google_oauth_secret_id=(os.getenv("GOOGLE_OAUTH_CLIENT_SECRET_ID") or "").strip()
            or None,
            google_oauth_client_config_json=(
                os.getenv("GOOGLE_OAUTH_CLIENT_CONFIG_JSON") or ""
            ).strip()
            or None,
            google_oauth_client_file=(os.getenv("GOOGLE_OAUTH_CLIENT_FILE") or "").strip()
            or None,
            openai_api_key_secret_id=(os.getenv("OPENAI_API_KEY_SECRET_ID") or "").strip()
            or None,
            openai_api_key=(os.getenv("OPENAI_API_KEY") or "").strip() or None,
            openai_model=(os.getenv("OPENAI_MODEL") or "gpt-5-mini").strip(),
            queue_url=(os.getenv("CLASSIFICATION_QUEUE_URL") or "").strip() or None,
            aws_region=(os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "").strip()
            or None,
            database_url=(os.getenv("DATABASE_URL") or "").strip() or None,
            cookie_name=(os.getenv("SESSION_COOKIE_NAME") or "email_agent_session").strip(),
            session_ttl_seconds=int(os.getenv("SESSION_TTL_SECONDS") or "86400"),
            oauth_state_ttl_seconds=int(os.getenv("OAUTH_STATE_TTL_SECONDS") or "600"),
            plan_ttl_seconds=int(os.getenv("ACTION_PLAN_TTL_SECONDS") or "900"),
            run_ttl_seconds=int(os.getenv("RUN_TTL_SECONDS") or "86400"),
            automatic_interval_seconds=int(
                os.getenv("AUTOMATIC_INTERVAL_SECONDS") or "43200"
            ),
            worker_lease_seconds=int(os.getenv("WORKER_LEASE_SECONDS") or "330"),
            manual_runs_per_day=int(os.getenv("MANUAL_RUNS_PER_DAY") or "50"),
            eml_previews_per_day=int(os.getenv("EML_PREVIEWS_PER_DAY") or "20"),
            automatic_runs_per_day=int(
                os.getenv("AUTOMATIC_RUNS_PER_DAY") or "2"
            ),
            service_name=(os.getenv("SERVICE_NAME") or "Inbox Pilot").strip(),
            operator_name=(
                os.getenv("OPERATOR_NAME") or "Local development operator"
            ).strip(),
            privacy_contact_email=(
                os.getenv("PRIVACY_CONTACT_EMAIL") or "privacy@localhost.invalid"
            ).strip(),
            support_email=(
                os.getenv("SUPPORT_EMAIL") or "support@localhost.invalid"
            ).strip(),
            ai_provider_name=(os.getenv("AI_PROVIDER_NAME") or "OpenAI").strip(),
            ai_provider_data_policy_url=(
                os.getenv("AI_PROVIDER_DATA_POLICY_URL")
                or "https://developers.openai.com/api/docs/guides/your-data"
            ).strip(),
            privacy_effective_date=(
                os.getenv("PRIVACY_EFFECTIVE_DATE") or "2026-08-20"
            ).strip(),
            privacy_notice_version=(
                os.getenv("PRIVACY_NOTICE_VERSION") or "2026-08-20.1"
            ).strip(),
            backup_recovery_days=int(os.getenv("BACKUP_RECOVERY_DAYS") or "7"),
            mvp_mode=(os.getenv("MVP_MODE") or "false").strip().casefold() == "true",
            property_lookup_enabled=(
                os.getenv("PROPERTY_LOOKUP_ENABLED") or "false"
            ).strip().casefold() == "true",
            default_classification_prompt=(
                os.getenv("DEFAULT_CLASSIFICATION_PROMPT") or DEFAULT_CLASSIFICATION_PROMPT
            ).strip(),
            default_classification_labels=tuple(
                label.strip()
                for label in (
                    os.getenv("DEFAULT_CLASSIFICATION_LABELS")
                    or "Acquisitions/On Market,Acquisitions/Off Market,Acquisitions/Wholesale,News"
                ).split(",")
                if label.strip()
            ),
            default_gmail_query=(os.getenv("DEFAULT_GMAIL_QUERY") or "in:inbox").strip(),
            default_confidence_threshold=float(
                os.getenv("DEFAULT_CONFIDENCE_THRESHOLD") or "0.85"
            ),
            default_max_messages_per_run=int(
                os.getenv("DEFAULT_MAX_MESSAGES_PER_RUN") or "10"
            ),
            default_automatic_enabled=(
                os.getenv("DEFAULT_AUTOMATIC_ENABLED") or "true"
            ).strip().casefold() == "true",
            scheduler_claim_delay_seconds=int(
                os.getenv("SCHEDULER_CLAIM_DELAY_SECONDS") or "3600"
            ),
            automatic_schedule_timezone=(
                os.getenv("AUTOMATIC_SCHEDULE_TIMEZONE") or "America/New_York"
            ).strip(),
            automatic_schedule_local_times=tuple(
                value.strip()
                for value in (
                    os.getenv("AUTOMATIC_SCHEDULE_LOCAL_TIMES") or "10:00,17:00"
                ).split(",")
                if value.strip()
            ),
            automatic_schedule_window_seconds=int(
                os.getenv("AUTOMATIC_SCHEDULE_WINDOW_SECONDS") or "900"
            ),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        environment = self.environment.casefold()
        if environment not in {"development", "test", "staging", "production"}:
            raise ValueError("APP_ENV must be development, test, staging, or production")
        parsed = urlsplit(self.app_base_url)
        loopback = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("APP_BASE_URL cannot contain credentials, a query, or a fragment")
        if parsed.path not in {"", "/"}:
            raise ValueError("APP_BASE_URL cannot contain a path")
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("APP_BASE_URL must be an absolute HTTP(S) URL")
        if parsed.scheme != "https" and not loopback:
            raise ValueError("APP_BASE_URL must use HTTPS outside an exact loopback host")
        if not loopback and environment not in {"staging", "production"}:
            raise ValueError("A public APP_BASE_URL requires APP_ENV=staging or production")
        if self.production and not self.mvp_mode:
            if not self.secure_transport:
                raise ValueError("Production APP_BASE_URL must use HTTPS")
            if not self.table_name:
                raise ValueError("MULTITENANT_TABLE_NAME is required in production")
            if not self.kms_key_id:
                raise ValueError("TOKEN_KMS_KEY_ID is required in production")
            if self.local_token_encryption_key:
                raise ValueError("LOCAL_TOKEN_ENCRYPTION_KEY is forbidden in production")
            if not self.google_oauth_secret_id:
                raise ValueError("GOOGLE_OAUTH_CLIENT_SECRET_ID is required in production")
            if self.google_oauth_client_config_json:
                raise ValueError("Inline Google OAuth configuration is forbidden in production")
            if self.google_oauth_client_file:
                raise ValueError("Google OAuth client files are forbidden in production")
            if not self.openai_api_key_secret_id:
                raise ValueError("OPENAI_API_KEY_SECRET_ID is required in production")
            if self.openai_api_key:
                raise ValueError("Inline OPENAI_API_KEY is forbidden in production")
            if not self.queue_url:
                raise ValueError("CLASSIFICATION_QUEUE_URL is required in production")
            contacts = (self.privacy_contact_email, self.support_email)
            if any(
                "@" not in value
                or value.casefold().endswith(("@example.com", ".invalid"))
                for value in contacts
            ):
                raise ValueError("Production privacy and support email addresses are required")
            if not self.operator_name or self.operator_name == "Local development operator":
                raise ValueError("OPERATOR_NAME is required in production")
            if not self.privacy_notice_version:
                raise ValueError("PRIVACY_NOTICE_VERSION is required in production")
            if not self.ai_provider_name:
                raise ValueError("AI_PROVIDER_NAME is required in production")
            if self.ai_provider_name != "OpenAI":
                raise ValueError("This implementation requires AI_PROVIDER_NAME=OpenAI")
            provider_url = urlsplit(self.ai_provider_data_policy_url)
            if (
                provider_url.scheme != "https"
                or provider_url.hostname not in {"openai.com", "developers.openai.com"}
            ):
                raise ValueError("AI provider policy URL must be an official OpenAI URL")
        if self.session_ttl_seconds < 300:
            raise ValueError("SESSION_TTL_SECONDS must be at least 300")
        if not (120 <= self.oauth_state_ttl_seconds <= 1_800):
            raise ValueError("OAUTH_STATE_TTL_SECONDS must be between 120 and 1800")
        if not (60 <= self.automatic_interval_seconds <= 86_400):
            raise ValueError("AUTOMATIC_INTERVAL_SECONDS must be between 60 and 86400")
        if not (60 <= self.worker_lease_seconds <= 3_600):
            raise ValueError("WORKER_LEASE_SECONDS must be between 60 and 3600")
        if not (1 <= self.manual_runs_per_day <= 1_000):
            raise ValueError("MANUAL_RUNS_PER_DAY must be between 1 and 1000")
        if not (1 <= self.eml_previews_per_day <= 1_000):
            raise ValueError("EML_PREVIEWS_PER_DAY must be between 1 and 1000")
        if not (1 <= self.automatic_runs_per_day <= 1_440):
            raise ValueError("AUTOMATIC_RUNS_PER_DAY must be between 1 and 1440")
        minimum_automatic_budget = (86_400 + self.automatic_interval_seconds - 1) // (
            self.automatic_interval_seconds
        )
        if self.automatic_runs_per_day < minimum_automatic_budget:
            raise ValueError(
                "AUTOMATIC_RUNS_PER_DAY must cover the configured automatic interval"
            )
        if self.plan_ttl_seconds < 60 or self.run_ttl_seconds < 300:
            raise ValueError("Plan and run TTLs are too short")
        if not (1 <= self.backup_recovery_days <= 35):
            raise ValueError("BACKUP_RECOVERY_DAYS must be between 1 and 35")
        if self.plan_ttl_seconds > min(
            self.session_ttl_seconds,
            self.run_ttl_seconds,
        ):
            raise ValueError("ACTION_PLAN_TTL_SECONDS cannot outlive sessions or runs")
        if not (1 <= self.default_max_messages_per_run <= 100):
            raise ValueError("DEFAULT_MAX_MESSAGES_PER_RUN must be between 1 and 100")
        if not (0.5 <= self.default_confidence_threshold <= 1.0):
            raise ValueError("DEFAULT_CONFIDENCE_THRESHOLD must be between 0.5 and 1.0")
        if not self.default_classification_prompt:
            raise ValueError("DEFAULT_CLASSIFICATION_PROMPT must not be empty")
        if not self.default_classification_labels:
            raise ValueError("DEFAULT_CLASSIFICATION_LABELS must not be empty")
        if not (60 <= self.scheduler_claim_delay_seconds <= 86_400):
            raise ValueError("SCHEDULER_CLAIM_DELAY_SECONDS must be between 60 and 86400")
        try:
            ZoneInfo(self.automatic_schedule_timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("AUTOMATIC_SCHEDULE_TIMEZONE must be a valid IANA timezone") from exc
        parse_local_times(",".join(self.automatic_schedule_local_times))
        if not (60 <= self.automatic_schedule_window_seconds <= 3_600):
            raise ValueError(
                "AUTOMATIC_SCHEDULE_WINDOW_SECONDS must be between 60 and 3600"
            )


@lru_cache(maxsize=8)
def load_google_client_config(settings: WebSettings) -> dict[str, Any]:
    if settings.google_oauth_client_config_json:
        config_value = settings.google_oauth_client_config_json
        config_path = Path(config_value)
        if config_path.is_file():
            value = json.loads(config_path.read_text(encoding="utf-8"))
        else:
            value = json.loads(config_value)
    elif settings.google_oauth_client_file:
        value = json.loads(Path(settings.google_oauth_client_file).read_text(encoding="utf-8"))
    elif settings.google_oauth_secret_id:
        value = secret_json(settings.google_oauth_secret_id, settings.aws_region)
    else:
        raise RuntimeError(
            "Set GOOGLE_OAUTH_CLIENT_CONFIG_JSON for local development or "
            "GOOGLE_OAUTH_CLIENT_SECRET_ID in production"
        )
    if "client_config" in value and isinstance(value["client_config"], dict):
        value = value["client_config"]
    if not isinstance(value.get("web"), dict):
        raise RuntimeError("Google OAuth configuration must contain a Web application client")
    return value


@lru_cache(maxsize=8)
def load_openai_api_key(settings: WebSettings) -> str:
    if settings.openai_api_key:
        return settings.openai_api_key
    if settings.openai_api_key_secret_id:
        data = secret_json(settings.openai_api_key_secret_id, settings.aws_region)
        for key in ("api_key", "OPENAI_API_KEY", "openai_api_key"):
            if data.get(key):
                return str(data[key])
        raise RuntimeError("OpenAI secret must contain api_key")
    raise RuntimeError("No server-side OpenAI API key is configured")
