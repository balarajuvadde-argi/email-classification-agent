import base64

from email_classification_agent.agent import EmailClassificationAgent
from email_classification_agent.classifier import ClassificationPipeline
from email_classification_agent.config import Settings


def _encoded(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii").rstrip("=")


def _gmail_message() -> dict:
    return {
        "id": "m1",
        "threadId": "t1",
        "labelIds": ["INBOX", "UNREAD"],
        "internalDate": "1000",
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "From", "value": "Deal Desk <deals@investor.example>"},
                {"name": "To", "value": "buyer@example.com"},
                {"name": "Subject", "value": "Hot off-market duplex deal"},
            ],
            "body": {
                "data": _encoded(
                    "123 Main Street, Miami FL 33147. Asking price $375,000. "
                    "ARV $550,000. Duplex, 2 beds, 2 baths, 1,000 sq ft. "
                    "Cash buyers only. Call or text to submit an offer."
                )
            },
        },
    }


class _FakeGmail:
    def __init__(self):
        self.label_calls = []

    def profile_email(self):
        return "buyer@example.com"

    def ensure_label(self, name, visible, create=True):
        return {"Wholesale": "L_WHOLESALE", "EmailAgent/Processed/v2": "L_PROCESSED"}[name]

    def list_candidate_message_ids(self, **kwargs):
        return ["m1"]

    def get_message(self, message_id):
        return _gmail_message()

    def get_thread(self, thread_id):
        return {"messages": [_gmail_message()]}

    def add_labels(self, message_id, label_ids):
        self.label_calls.append((message_id, list(label_ids)))


def test_live_flow_adds_wholesale_and_processed_labels_only() -> None:
    settings = Settings(
        expected_gmail_address="buyer@example.com",
        dry_run=False,
        use_llm=False,
    )
    gmail = _FakeGmail()
    report = EmailClassificationAgent(
        gmail, ClassificationPipeline(settings), settings
    ).run(max_messages=10)

    assert report.failed == 0
    assert report.labeled_wholesale == 1
    assert gmail.label_calls == [("m1", ["L_WHOLESALE", "L_PROCESSED"])]


def test_dry_run_performs_no_message_write() -> None:
    settings = Settings(
        expected_gmail_address="buyer@example.com",
        dry_run=True,
        use_llm=False,
    )
    gmail = _FakeGmail()
    report = EmailClassificationAgent(
        gmail, ClassificationPipeline(settings), settings
    ).run(max_messages=10)

    assert report.labeled_wholesale == 1
    assert gmail.label_calls == []
