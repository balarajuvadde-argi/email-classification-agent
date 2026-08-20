from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class Category(str, Enum):
    WHOLESALE = "WHOLESALE"
    KEEP_IN_INBOX = "KEEP_IN_INBOX"


class LLMDecision(BaseModel):
    category: Literal["WHOLESALE", "KEEP_IN_INBOX"]
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = Field(min_length=1, max_length=600)
    evidence: list[str] = Field(default_factory=list, max_length=8)


@dataclass(slots=True)
class ParsedEmail:
    message_id: str
    thread_id: str
    label_ids: tuple[str, ...]
    internal_date_ms: int
    from_header: str
    sender_header: str
    reply_to_header: str
    to_header: str
    subject: str
    body_text: str
    raw_headers: dict[str, str] = field(default_factory=dict)
    attachment_names: tuple[str, ...] = ()


@dataclass(slots=True)
class HeuristicAssessment:
    property_detail_score: int
    promotion_score: int
    contract_score: int
    negative_score: int
    meta_discussion: bool
    high_confidence_wholesale: bool
    high_confidence_keep: bool
    evidence: tuple[str, ...]
    reason: str


@dataclass(slots=True)
class ClassificationResult:
    category: Category
    confidence: float
    reason: str
    source: str
    evidence: tuple[str, ...] = ()
    should_mark_processed: bool = True


@dataclass(slots=True)
class MessageOutcome:
    message_id: str
    subject: str
    sender: str
    category: str
    source: str
    confidence: float
    action: str
    reason: str


@dataclass(slots=True)
class ProcessingReport:
    mailbox: str
    dry_run: bool
    policy_version: str = ""
    scanned: int = 0
    labeled_wholesale: int = 0
    kept_in_inbox: int = 0
    skipped_already_processed: int = 0
    failed: int = 0
    outcomes: list[MessageOutcome] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "mailbox": self.mailbox,
            "dry_run": self.dry_run,
            "policy_version": self.policy_version,
            "scanned": self.scanned,
            "labeled_wholesale": self.labeled_wholesale,
            "kept_in_inbox": self.kept_in_inbox,
            "skipped_already_processed": self.skipped_already_processed,
            "failed": self.failed,
            "outcomes": [asdict(outcome) for outcome in self.outcomes],
        }
