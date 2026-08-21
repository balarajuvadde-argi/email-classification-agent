from __future__ import annotations

import json
import logging
import secrets
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from contextlib import suppress
from dataclasses import replace
from typing import Any, Protocol

from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2 import id_token as google_id_token

from .email_parser import parse_eml_bytes
from .gmail_client import GMAIL_MODIFY_SCOPE, GmailClient
from .google_oauth import GoogleOAuthManager
from .multitenant_store import (
    DynamoDbMultiTenantStore,
    InMemoryMultiTenantStore,
    MultiTenantStore,
)
from .token_security import (
    FernetTokenCipher,
    KmsTokenCipher,
    TokenCipher,
    credentials_from_grant,
    credentials_to_grant,
)
from .universal_agent import UniversalClassificationAgent
from .universal_classifier import UniversalEmailClassifier
from .universal_models import ActionPlan, RunRecord, UniversalOutcome, UserRecord
from .web_config import WebSettings, load_google_client_config, load_openai_api_key

LOGGER = logging.getLogger(__name__)


class JobQueue(Protocol):
    def send(self, user_id: str, run_id: str) -> None: ...


class SqsJobQueue:
    def __init__(self, queue_url: str, *, region_name: str | None = None, client: Any = None) -> None:
        if client is None:
            import boto3

            client = boto3.client("sqs", region_name=region_name)
        self._client = client
        self._queue_url = queue_url

    def send(self, user_id: str, run_id: str) -> None:
        body = json.dumps({"user_id": user_id, "run_id": run_id}, separators=(",", ":"))
        kwargs: dict[str, Any] = {"QueueUrl": self._queue_url, "MessageBody": body}
        if self._queue_url.endswith(".fifo"):
            kwargs["MessageGroupId"] = user_id
            kwargs["MessageDeduplicationId"] = f"{user_id}:{run_id}"
        self._client.send_message(**kwargs)


IdentityVerifier = Callable[[str, str], dict[str, Any]]


def verify_google_identity(raw_id_token: str, client_id: str) -> dict[str, Any]:
    return google_id_token.verify_oauth2_token(
        raw_id_token,
        GoogleAuthRequest(),
        audience=client_id,
    )


class WebRuntime:
    def __init__(
        self,
        settings: WebSettings,
        *,
        store: MultiTenantStore | None = None,
        cipher: TokenCipher | None = None,
        oauth: GoogleOAuthManager | None = None,
        queue: JobQueue | None = None,
        identity_verifier: IdentityVerifier = verify_google_identity,
        gmail_factory: Callable[[Any], GmailClient] = GmailClient.from_credentials,
        classifier_factory: Callable[[], UniversalEmailClassifier] | None = None,
    ) -> None:
        self.settings = settings
        self.client_config = load_google_client_config(settings)
        self.store = store or self._build_store(settings)
        self.cipher = cipher or self._build_cipher(settings)
        self.oauth = oauth or GoogleOAuthManager(
            self.client_config,
            settings.oauth_redirect_uri,
        )
        self.queue = queue or (
            SqsJobQueue(settings.queue_url, region_name=settings.aws_region)
            if settings.queue_url
            else None
        )
        self._identity_verifier = identity_verifier
        self._gmail_factory = gmail_factory
        self._classifier_factory = classifier_factory or self._default_classifier

    @staticmethod
    def _build_store(settings: WebSettings) -> MultiTenantStore:
        if settings.mvp_mode:
            return InMemoryMultiTenantStore()
        if settings.table_name:
            return DynamoDbMultiTenantStore(
                settings.table_name,
                region_name=settings.aws_region,
            )
        if settings.production:
            raise RuntimeError("Production requires DynamoDB")
        return InMemoryMultiTenantStore()

    @staticmethod
    def _build_cipher(settings: WebSettings) -> TokenCipher:
        if settings.mvp_mode and settings.local_token_encryption_key:
            return FernetTokenCipher(settings.local_token_encryption_key)
        if settings.kms_key_id:
            return KmsTokenCipher(settings.kms_key_id, region_name=settings.aws_region)
        if settings.local_token_encryption_key:
            return FernetTokenCipher(settings.local_token_encryption_key)
        raise RuntimeError(
            "Configure TOKEN_KMS_KEY_ID, or LOCAL_TOKEN_ENCRYPTION_KEY for local development"
        )

    def _default_classifier(self) -> UniversalEmailClassifier:
        return UniversalEmailClassifier(
            api_key=load_openai_api_key(self.settings),
            model=self.settings.openai_model,
        )

    def _classification_failure_message(self, exc: Exception) -> str:
        status_code = getattr(exc, "status_code", None)
        if status_code == 404:
            LOGGER.error(
                "OpenAI model unavailable model=%s status=%s",
                self.settings.openai_model,
                status_code,
            )
            return (
                "The configured AI model is unavailable. Update OPENAI_MODEL to a supported "
                "model, then run the preview again."
            )
        LOGGER.error(
            "Classification provider failure provider=OpenAI model=%s error_type=%s",
            self.settings.openai_model,
            type(exc).__name__,
        )
        return (
            "The AI classification service could not complete this run. Check the OpenAI "
            "configuration and try again."
        )

    def google_client_id(self) -> str:
        return str(self.client_config["web"]["client_id"])

    def _verified_identity(
        self,
        credentials: Any,
        *,
        expected_user_id: str | None = None,
        expected_nonce: str | None = None,
    ) -> tuple[str, str]:
        raw_id_token = credentials.id_token
        if not isinstance(raw_id_token, str) or not raw_id_token:
            raise RuntimeError("Google did not return an identity token")
        claims = self._identity_verifier(raw_id_token, self.google_client_id())
        if claims.get("email_verified") is not True:
            raise RuntimeError("Google account email is not verified")
        user_id = str(claims.get("sub") or "").strip()
        claimed_email = str(claims.get("email") or "").strip().casefold()
        if not user_id or not claimed_email:
            raise RuntimeError("Google identity token is missing sub or email")
        if expected_nonce and not secrets.compare_digest(
            str(claims.get("nonce") or ""),
            expected_nonce,
        ):
            raise RuntimeError("Google identity token nonce does not match this OAuth request")
        if expected_user_id and not secrets.compare_digest(user_id, expected_user_id):
            raise RuntimeError("Authorized Google account does not match the signed-in user")
        return user_id, claimed_email

    def authorize_user(
        self,
        credentials: Any,
        *,
        expected_user_id: str | None = None,
        expected_connection_version: str | None = None,
        expected_nonce: str | None = None,
        consent_version: str = "",
        consented_at: int = 0,
    ) -> UserRecord:
        user_id, claimed_email = self._verified_identity(
            credentials,
            expected_user_id=expected_user_id,
            expected_nonce=expected_nonce,
        )
        granted_scopes = set(credentials.scopes or [])
        if GMAIL_MODIFY_SCOPE not in granted_scopes:
            raise RuntimeError("The required Gmail label permission was not granted")
        allowed_scopes = {
            "openid",
            "email",
            "https://www.googleapis.com/auth/userinfo.email",
            GMAIL_MODIFY_SCOPE,
        }
        if granted_scopes - allowed_scopes:
            raise RuntimeError("Google returned an unexpected OAuth scope")

        gmail = self._gmail_factory(credentials)
        mailbox = gmail.profile_email().strip().casefold()
        if mailbox != claimed_email:
            raise RuntimeError("Google identity and Gmail profile refer to different accounts")

        now = int(time.time())
        encrypted = self.cipher.encrypt(user_id, credentials_to_grant(credentials))
        connection_version = secrets.token_urlsafe(24)
        return self.store.upsert_user_connection(
            user_id,
            mailbox,
            encrypted,
            now,
            consent_version,
            consented_at,
            connection_version,
            expected_connection_version,
        )

    def authenticate_existing_user(
        self,
        credentials: Any,
        *,
        expected_nonce: str,
    ) -> UserRecord:
        user_id, claimed_email = self._verified_identity(
            credentials,
            expected_nonce=expected_nonce,
        )
        user = self.store.get_user(user_id)
        if user is None or user.email != claimed_email:
            raise RuntimeError("No existing Gmail connection was found for this account")
        return user

    def accept_current_notice(self, user: UserRecord) -> UserRecord:
        current = self.store.get_user(user.user_id)
        if current is None or current.connection_version != user.connection_version:
            raise RuntimeError("Gmail connection changed before consent update")
        automatic_enabled = bool(current.policy and current.policy.automatic_enabled)
        policy_json = current.policy.model_dump_json() if current.policy else ""
        return self.store.update_consent(
            current.user_id,
            current.connection_version,
            self.settings.disclosure_version,
            int(time.time()),
            automatic_enabled,
            policy_json,
        )

    def new_run(self, user: UserRecord, *, mode: str) -> RunRecord:
        self._require_current_consent(user)
        if mode not in {"preview", "automatic"}:
            raise ValueError("Unsupported run mode")
        if user.policy is None:
            raise RuntimeError("Save a classification policy before starting a run")
        if mode == "automatic" and not user.policy.automatic_enabled:
            raise RuntimeError("Automatic classification is disabled")
        now = int(time.time())
        run = RunRecord(
            run_id=f"{now:010d}-{secrets.token_hex(8)}",
            user_id=user.user_id,
            mode=mode,
            status="queued",
            policy_hash=user.policy.policy_hash,
            created_at=now,
            updated_at=now,
            expires_at=now + self.settings.run_ttl_seconds,
            connection_version=user.connection_version,
        )
        return self._store_and_enqueue(run)

    def new_apply_run(self, user: UserRecord, plan_id: str) -> RunRecord:
        self._require_current_consent(user)
        if user.policy is None:
            raise RuntimeError("Classification policy no longer exists")
        plan = self.store.get_plan(plan_id)
        if plan is None:
            raise RuntimeError("Preview plan expired or was already applied")
        if plan.user_id != user.user_id or plan.policy_hash != user.policy.policy_hash:
            raise RuntimeError("Preview plan does not match this user and policy")
        if plan.connection_version != user.connection_version:
            raise RuntimeError("Preview plan belongs to an earlier Gmail connection")
        now = int(time.time())
        job_plan_id = secrets.token_urlsafe(32)
        self.store.put_plan(
            job_plan_id,
            replace(plan, expires_at=now + self.settings.run_ttl_seconds),
        )
        run = RunRecord(
            run_id=f"{now:010d}-{secrets.token_hex(8)}",
            user_id=user.user_id,
            mode="apply",
            status="queued",
            policy_hash=user.policy.policy_hash,
            created_at=now,
            updated_at=now,
            expires_at=now + self.settings.run_ttl_seconds,
            plan_id=job_plan_id,
            connection_version=user.connection_version,
        )
        try:
            queued = self._store_and_enqueue(run)
        except Exception:
            self.store.consume_plan(job_plan_id)
            raise
        with suppress(Exception):
            self.store.consume_plan(plan_id)
        return queued

    def _store_and_enqueue(self, run: RunRecord) -> RunRecord:
        now = int(time.time())
        if not self.store.acquire_active_run(
            run.user_id,
            run.run_id,
            now,
            min(run.expires_at, now + 1_800),
            run.connection_version,
        ):
            raise RuntimeError("Another classification run is already active for this mailbox")
        stored = False
        try:
            if run.mode in {"preview", "apply"} and not self.store.consume_quota(
                run.user_id,
                run.connection_version,
                "manual-runs",
                self.settings.manual_runs_per_day,
                86_400,
                now,
            ):
                raise RuntimeError("The daily manual-run limit has been reached")
            self.store.put_run(run)
            stored = True
            if self.queue:
                self.queue.send(run.user_id, run.run_id)
        except Exception:
            try:
                if stored:
                    self.store.update_run(
                        replace(
                            run,
                            status="failed",
                            updated_at=int(time.time()),
                            error="The run could not be queued.",
                        )
                    )
            finally:
                self.store.release_active_run(run.user_id, run.run_id)
            raise
        return run

    def process_run(self, user_id: str, run_id: str) -> RunRecord | None:
        now = int(time.time())
        running = self.store.claim_run(
            user_id,
            run_id,
            now,
            now + self.settings.worker_lease_seconds,
        )
        if running is None:
            return None
        user = self.store.get_user(user_id)
        if user is None:
            self.store.release_active_run(user_id, run_id)
            return None
        if user.policy is None:
            return self._fail_run(running, "Gmail connection or policy no longer exists")
        if running.connection_version != user.connection_version:
            return self._fail_run(running, "Gmail connection changed before the queued run started")
        if user.consent_version != self.settings.disclosure_version:
            return self._fail_run(running, "The current privacy notice must be accepted again")
        if user.policy.policy_hash != running.policy_hash:
            return self._fail_run(running, "Policy changed before the queued run started")
        if running.mode == "automatic" and not user.policy.automatic_enabled:
            return self._fail_run(running, "Automatic classification was disabled")

        try:
            self._assert_current_connection(
                user,
                require_automatic=running.mode == "automatic",
            )
            credentials = credentials_from_grant(
                self.cipher.decrypt(user.user_id, user.encrypted_grant),
                self.client_config,
            )
            gmail = self._gmail_factory(credentials)

            def operation_guard() -> None:
                self._assert_current_connection(
                    user,
                    require_automatic=running.mode == "automatic",
                )

            agent = UniversalClassificationAgent(
                gmail,
                self._classifier_factory(),
                expected_email=user.email,
                operation_guard=operation_guard,
            )
            consumed_plan_id = ""
            if running.mode == "preview":
                report = agent.preview(user.policy)
                operation_guard()
                plan_id = secrets.token_urlsafe(32)
                self.store.put_plan(
                    plan_id,
                    ActionPlan(
                        user_id=user.user_id,
                        policy_hash=user.policy.policy_hash,
                        mailbox=user.email,
                        outcomes=tuple(report.outcomes),
                        expires_at=int(time.time()) + self.settings.plan_ttl_seconds,
                        connection_version=user.connection_version,
                    ),
                )
                preview_activation_hash = (
                    user.policy.activation_hash
                    if report.failed == 0 and report.scanned > 0
                    else ""
                )
            elif running.mode == "automatic":
                report = agent.run_automatic(user.policy)
                plan_id = ""
            elif running.mode == "apply":
                plan = self.store.get_plan(running.plan_id)
                if plan is None:
                    raise RuntimeError("Preview plan expired or was already applied")
                if plan.user_id != user.user_id:
                    raise RuntimeError("Preview plan belongs to another user")
                if plan.connection_version != user.connection_version:
                    raise RuntimeError("Preview plan belongs to an earlier Gmail connection")
                report = agent.apply_plan(user.policy, plan)
                plan_id = ""
                consumed_plan_id = running.plan_id
            else:
                raise RuntimeError("Unsupported queued run mode")
            completed = replace(
                running,
                status="completed" if report.failed == 0 else "completed_with_errors",
                updated_at=int(time.time()),
                report_json=json.dumps(report.as_dict(), ensure_ascii=False),
                plan_id=plan_id,
                error="",
                lease_expires_at=0,
            )
            if not self.store.update_run(completed):
                return None
            if running.mode == "preview" and preview_activation_hash:
                with suppress(Exception):
                    self.store.mark_policy_previewed(
                        user.user_id,
                        preview_activation_hash,
                        user.connection_version,
                    )
            if consumed_plan_id:
                with suppress(Exception):
                    self.store.consume_plan(consumed_plan_id)
            with suppress(Exception):
                self.store.release_active_run(user_id, run_id)
            return completed
        except Exception as exc:  # noqa: BLE001 - worker records a safe failure
            LOGGER.error(
                "Classification run %s failed (%s)",
                running.run_id,
                type(exc).__name__,
            )
            message = self._classification_failure_message(exc)
            if self.queue is not None and running.attempts < 3:
                retry = replace(
                    running,
                    status="retry",
                    updated_at=int(time.time()),
                    error=message,
                )
                self.store.update_run(retry)
                raise RuntimeError("Classification job should be retried") from None
            LOGGER.error("Terminal classification failure for run %s", running.run_id)
            return self._fail_run(running, message)

    def _assert_current_connection(
        self,
        user: UserRecord,
        require_automatic: bool,
    ) -> None:
        current = self.store.get_user(user.user_id)
        if (
            current is None
            or current.policy is None
            or user.policy is None
            or current.consent_version != self.settings.disclosure_version
            or current.policy.policy_hash != user.policy.policy_hash
            or current.email != user.email
            or current.connection_version != user.connection_version
            or not secrets.compare_digest(
                current.encrypted_grant,
                user.encrypted_grant,
            )
            or (require_automatic and not current.policy.automatic_enabled)
        ):
            raise RuntimeError("Gmail connection or policy changed before the write")

    def apply_preview(self, user: UserRecord, plan_id: str) -> dict[str, Any]:
        if user.policy is None:
            raise RuntimeError("Classification policy no longer exists")
        plan = self.store.get_plan(plan_id)
        if plan is None:
            raise RuntimeError("Preview plan expired or was already applied")
        if plan.user_id != user.user_id:
            raise RuntimeError("Preview plan belongs to another user")
        if plan.connection_version != user.connection_version:
            raise RuntimeError("Preview plan belongs to an earlier Gmail connection")
        self._assert_current_connection(user, require_automatic=False)
        credentials = credentials_from_grant(
            self.cipher.decrypt(user.user_id, user.encrypted_grant),
            self.client_config,
        )
        gmail = self._gmail_factory(credentials)
        agent = UniversalClassificationAgent(
            gmail,
            self._classifier_factory(),
            expected_email=user.email,
            operation_guard=lambda: self._assert_current_connection(
                user,
                require_automatic=False,
            ),
        )
        report = agent.apply_plan(user.policy, plan).as_dict()
        self.store.consume_plan(plan_id)
        return report

    def classify_uploaded_eml(self, user: UserRecord, data: bytes) -> dict[str, Any]:
        self._require_current_consent(user)
        if user.policy is None:
            raise RuntimeError("Save a classification policy first")
        if not data or len(data) > 2_000_000:
            raise ValueError("The .eml upload must be between 1 byte and 2 MB")
        self._assert_current_connection(user, require_automatic=False)
        if not self.store.consume_quota(
            user.user_id,
            user.connection_version,
            "eml-previews",
            self.settings.eml_previews_per_day,
            86_400,
            int(time.time()),
        ):
            raise RuntimeError("The daily .eml preview limit has been reached")
        message = parse_eml_bytes(data)
        decision = self._classifier_factory().classify(message, user.policy)
        label = decision.label
        if label and decision.confidence < user.policy.confidence_threshold:
            label = None
        now = int(time.time())
        run_id = f"{now:010d}-{secrets.token_hex(8)}"
        outcome = UniversalOutcome(
            message_id="uploaded-eml",
            thread_id="uploaded-eml",
            subject=message.subject[:500],
            sender=message.from_header[:500],
            proposed_label=label,
            confidence=decision.confidence,
            action="proposed" if label else "kept_unlabeled",
            reason=decision.reason,
            evidence=tuple(decision.evidence),
        )
        report = {
            "mailbox": user.email,
            "dry_run": True,
            "policy_hash": user.policy.policy_hash,
            "processed_label": user.policy.processed_label,
            "scanned": 1,
            "proposed": int(bool(label)),
            "labeled": 0,
            "kept_unlabeled": int(not label),
            "low_confidence": int(bool(decision.label and not label)),
            "failed": 0,
            "outcomes": [outcome.as_dict()],
        }
        self.store.put_run(
            RunRecord(
                run_id=run_id,
                user_id=user.user_id,
                mode="eml_test",
                status="completed",
                policy_hash=user.policy.policy_hash,
                created_at=now,
                updated_at=now,
                expires_at=now + self.settings.run_ttl_seconds,
                report_json=json.dumps(report, separators=(",", ":")),
                connection_version=user.connection_version,
            )
        )
        return {
            "run_id": run_id,
            "subject": message.subject[:500],
            "sender": message.from_header[:500],
            "label": label,
            "model_label": decision.label,
            "confidence": decision.confidence,
            "threshold": user.policy.confidence_threshold,
            "reason": decision.reason,
            "evidence": decision.evidence,
        }

    def _require_current_consent(self, user: UserRecord) -> None:
        if user.consent_version != self.settings.disclosure_version:
            raise RuntimeError("The current privacy notice must be accepted again")

    def disconnect(self, user: UserRecord) -> bool:
        revoked = False
        token: str | None = None
        try:
            credentials = credentials_from_grant(
                self.cipher.decrypt(user.user_id, user.encrypted_grant),
                self.client_config,
            )
            token = credentials.refresh_token or credentials.token
        except Exception:  # noqa: BLE001 - local deletion must proceed after decrypt failure
            token = None

        # Remove the live profile before a network call so every operation guard fails
        # immediately while Google revocation is attempted.
        self.store.delete_user(user.user_id, user.connection_version)
        if token:
            try:
                request = urllib.request.Request(
                    "https://oauth2.googleapis.com/revoke",
                    data=urllib.parse.urlencode({"token": token}).encode("ascii"),
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    method="POST",
                )
                with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
                    revoked = 200 <= response.status < 300
            except Exception:  # noqa: BLE001 - local deletion already completed
                revoked = False
        return revoked

    def _fail_run(self, run: RunRecord, error: str) -> RunRecord:
        failed = replace(
            run,
            status="failed",
            updated_at=int(time.time()),
            error=error,
        )
        self.store.update_run(failed)
        with suppress(Exception):
            self.store.release_active_run(run.user_id, run.run_id)
        return failed
