import base64

from email_classification_agent.agent import EmailClassificationAgent
from email_classification_agent.classifier import ClassificationPipeline
from email_classification_agent.config import Settings


def _encoded(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii").rstrip("=")


class _ApprovedGmail:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str]]] = []
        self.message = {
            "id": "z1",
            "threadId": "tz1",
            "labelIds": ["INBOX"],
            "internalDate": "1000",
            "payload": {
                "mimeType": "text/plain",
                "headers": [
                    {"name": "From", "value": "Zillow <alerts@mail.zillow.com>"},
                    {"name": "To", "value": "buyer@example.com"},
                    {"name": "Subject", "value": "New listing: 123 Main Street"},
                ],
                "body": {
                    "data": _encoded(
                        "Asking $450,000. 3 beds, 2 baths, 1,500 sq ft. Schedule a tour."
                    )
                },
            },
        }

    def profile_email(self) -> str:
        return "buyer@example.com"

    def ensure_label(self, name: str, visible: bool, create: bool = True) -> str:
        return {"Wholesale": "W", "EmailAgent/Processed/v2": "P"}[name]

    def list_candidate_message_ids(self, **kwargs):
        return ["z1"]

    def get_message(self, message_id: str):
        return self.message

    def get_thread(self, thread_id: str):
        return {"messages": [self.message]}

    def add_labels(self, message_id: str, label_ids: list[str]) -> None:
        self.calls.append((message_id, list(label_ids)))


def test_approved_platform_gets_processed_label_but_not_wholesale() -> None:
    gmail = _ApprovedGmail()
    settings = Settings(
        expected_gmail_address="buyer@example.com",
        dry_run=False,
        use_llm=False,
    )
    report = EmailClassificationAgent(
        gmail, ClassificationPipeline(settings), settings
    ).run(max_messages=1)

    assert report.kept_in_inbox == 1
    assert report.labeled_wholesale == 0
    assert gmail.calls == [("z1", ["P"])]
