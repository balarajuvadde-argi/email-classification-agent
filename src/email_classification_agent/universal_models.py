from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from typing import Annotated, Any

from pydantic import BaseModel, Field, field_validator, model_validator

SYSTEM_GMAIL_LABELS = {
    "all",
    "all mail",
    "category_forums",
    "category_personal",
    "category_promotions",
    "category_social",
    "category_updates",
    "chat",
    "chats",
    "draft",
    "important",
    "inbox",
    "sent",
    "spam",
    "starred",
    "trash",
    "unread",
}

_UNSAFE_QUERY_RE = re.compile(r"(?:^|\s)(?:in|label):(spam|trash)(?:\s|$)", re.I)


class ClassificationPolicy(BaseModel):
    """A tenant-owned policy with server-enforced safety limits."""

    prompt: str = Field(min_length=20, max_length=8_000)
    labels: list[str] = Field(min_length=1, max_length=12)
    gmail_query: str = Field(
        default="in:inbox -in:spam -in:trash",
        min_length=1,
        max_length=500,
    )
    confidence_threshold: float = Field(default=0.85, ge=0.5, le=1.0)
    max_messages_per_run: int = Field(default=10, ge=1, le=10)
    automatic_enabled: bool = False

    @field_validator("prompt", "gmail_query", mode="before")
    @classmethod
    def _strip_text(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value

    @field_validator("labels")
    @classmethod
    def _validate_labels(cls, labels: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for raw in labels:
            label = raw.strip()
            if not label:
                continue
            if len(label) > 100:
                raise ValueError("Each Gmail label must be 100 characters or fewer")
            if label.casefold() in SYSTEM_GMAIL_LABELS:
                raise ValueError(f"System Gmail label cannot be a destination: {label}")
            if label.casefold().startswith("emailagent/processed/"):
                raise ValueError("Destination labels cannot use the processed-label namespace")
            folded = label.casefold()
            if folded not in seen:
                normalized.append(label)
                seen.add(folded)
        if not normalized:
            raise ValueError("At least one destination label is required")
        return normalized

    @model_validator(mode="after")
    def _keep_query_out_of_spam_and_trash(self) -> ClassificationPolicy:
        if _UNSAFE_QUERY_RE.search(self.gmail_query):
            raise ValueError("Spam and Trash cannot be classification sources")
        query = self.gmail_query
        if "-in:spam" not in query.casefold():
            query += " -in:spam"
        if "-in:trash" not in query.casefold():
            query += " -in:trash"
        self.gmail_query = query.strip()
        return self

    @property
    def policy_hash(self) -> str:
        canonical = json.dumps(
            {
                "prompt": self.prompt,
                "labels": self.labels,
                "gmail_query": self.gmail_query,
                "confidence_threshold": self.confidence_threshold,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @property
    def processed_label(self) -> str:
        return f"EmailAgent/Processed/{self.policy_hash[:16]}"

    @property
    def activation_hash(self) -> str:
        value = f"{self.policy_hash}:{self.max_messages_per_run}"
        return hashlib.sha256(value.encode("utf-8")).hexdigest()


class UniversalDecision(BaseModel):
    label: str | None = Field(
        default=None,
        description="One exact allowed Gmail label, or null when no label applies.",
    )
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = Field(min_length=1, max_length=600)
    evidence: list[Annotated[str, Field(max_length=300)]] = Field(
        default_factory=list,
        max_length=8,
    )


@dataclass(slots=True)
class UniversalOutcome:
    message_id: str
    thread_id: str
    subject: str
    sender: str
    proposed_label: str | None
    confidence: float
    action: str
    reason: str
    evidence: tuple[str, ...] = ()
    secondary_label: str | None = None
    property_records: tuple[dict[str, Any], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["evidence"] = list(self.evidence)
        return value


@dataclass(slots=True)
class UniversalReport:
    mailbox: str
    dry_run: bool
    policy_hash: str
    processed_label: str
    scanned: int = 0
    proposed: int = 0
    labeled: int = 0
    kept_unlabeled: int = 0
    low_confidence: int = 0
    failed: int = 0
    outcomes: list[UniversalOutcome] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "mailbox": self.mailbox,
            "dry_run": self.dry_run,
            "policy_hash": self.policy_hash,
            "processed_label": self.processed_label,
            "scanned": self.scanned,
            "proposed": self.proposed,
            "labeled": self.labeled,
            "kept_unlabeled": self.kept_unlabeled,
            "low_confidence": self.low_confidence,
            "failed": self.failed,
            "outcomes": [outcome.as_dict() for outcome in self.outcomes],
        }


@dataclass(frozen=True, slots=True)
class UserRecord:
    user_id: str
    email: str
    encrypted_grant: str
    policy: ClassificationPolicy | None = None
    created_at: int = 0
    updated_at: int = 0
    next_run_at: int = 0
    last_previewed_activation_hash: str = ""
    consent_version: str = ""
    consented_at: int = 0
    connection_version: str = ""
    schedule_paused_for_consent: bool = False


@dataclass(frozen=True, slots=True)
class ScheduledUserRecord:
    """Least-data projection used by the automatic-run dispatcher."""

    user_id: str
    policy_hash: str
    next_run_at: int
    consent_version: str
    connection_version: str


@dataclass(frozen=True, slots=True)
class SessionRecord:
    user_id: str
    csrf_token: str
    expires_at: int
    connection_version: str = ""


@dataclass(frozen=True, slots=True)
class PolicyRevision:
    policy_hash: str
    saved_at: int
    policy_json: str
    connection_version: str = ""

    @property
    def policy(self) -> ClassificationPolicy:
        return ClassificationPolicy.model_validate_json(self.policy_json)


@dataclass(frozen=True, slots=True)
class OAuthStateRecord:
    code_verifier: str
    expires_at: int
    initiating_user_id: str | None = None
    browser_nonce_hash: str = ""
    id_token_nonce: str = ""
    consent_version: str = ""
    consented_at: int = 0
    purpose: str = "connect"
    initiating_connection_version: str = ""


@dataclass(frozen=True, slots=True)
class ActionPlan:
    user_id: str
    policy_hash: str
    mailbox: str
    outcomes: tuple[UniversalOutcome, ...]
    expires_at: int
    connection_version: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "policy_hash": self.policy_hash,
            "mailbox": self.mailbox,
            "outcomes": [outcome.as_dict() for outcome in self.outcomes],
            "expires_at": self.expires_at,
            "connection_version": self.connection_version,
        }


@dataclass(frozen=True, slots=True)
class RunRecord:
    run_id: str
    user_id: str
    mode: str
    status: str
    policy_hash: str
    created_at: int
    updated_at: int
    expires_at: int
    report_json: str = ""
    plan_id: str = ""
    error: str = ""
    lease_expires_at: int = 0
    attempts: int = 0
    connection_version: str = ""
