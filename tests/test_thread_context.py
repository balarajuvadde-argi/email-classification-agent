import base64

from email_classification_agent.agent import EmailClassificationAgent
from email_classification_agent.config import Settings
from email_classification_agent.models import Category, ClassificationResult


def _encoded(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii").rstrip("=")


def _message(message_id: str, internal_date: int, subject: str, body: str) -> dict:
    return {
        "id": message_id,
        "threadId": "thread-1",
        "labelIds": ["INBOX"],
        "internalDate": str(internal_date),
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "From", "value": "Seller <seller@example.com>"},
                {"name": "To", "value": "buyer@example.com"},
                {"name": "Subject", "value": subject},
            ],
            "body": {"data": _encoded(body)},
        },
    }


class _ContextGmail:
    def __init__(self) -> None:
        self.current = _message(
            "current", 1_000, "Re: 123 Main Street", "Yes, it is still available."
        )
        self.prior = _message(
            "prior",
            500,
            "123 Main Street investment opportunity",
            "Asking $350,000. ARV $525,000. Submit an offer.",
        )
        self.same_timestamp_future = _message(
            "same-time-future",
            1_000,
            "Same timestamp but later in thread",
            "This same-timestamp later message must not appear in prior context.",
        )
        self.future = _message(
            "future", 1_500, "Later message", "This must not appear in prior context."
        )
        self.label_calls: list[tuple[str, list[str]]] = []

    def profile_email(self) -> str:
        return "buyer@example.com"

    def ensure_label(self, name: str, visible: bool, create: bool = True) -> str:
        return {"Wholesale": "W", "EmailAgent/Processed/v2": "P"}[name]

    def list_candidate_message_ids(self, **kwargs):
        return ["current"]

    def get_message(self, message_id: str):
        return self.current

    def get_thread(self, thread_id: str):
        return {
            "messages": [
                self.prior,
                self.current,
                self.same_timestamp_future,
                self.future,
            ]
        }

    def add_labels(self, message_id: str, label_ids: list[str]) -> None:
        self.label_calls.append((message_id, list(label_ids)))


class _ContextClassifier:
    def __init__(self) -> None:
        self.context = ""

    def classify(self, message, thread_context: str = "") -> ClassificationResult:
        self.context = thread_context
        return ClassificationResult(
            category=Category.WHOLESALE,
            confidence=0.94,
            reason="Brief reply continues the prior property pitch.",
            source="test_semantic",
        )


def test_agent_passes_only_prior_thread_messages_to_classifier() -> None:
    gmail = _ContextGmail()
    classifier = _ContextClassifier()
    settings = Settings(
        expected_gmail_address="buyer@example.com",
        dry_run=False,
        use_llm=False,
    )
    report = EmailClassificationAgent(gmail, classifier, settings).run(max_messages=1)

    assert report.labeled_wholesale == 1
    assert "123 Main Street investment opportunity" in classifier.context
    assert "Same timestamp but later in thread" not in classifier.context
    assert "Later message" not in classifier.context
    assert gmail.label_calls == [("current", ["W", "P"])]
