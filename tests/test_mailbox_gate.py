import pytest

from email_classification_agent.agent import EmailClassificationAgent
from email_classification_agent.classifier import ClassificationPipeline
from email_classification_agent.config import Settings


class _WrongMailboxGmail:
    def __init__(self) -> None:
        self.label_checked = False

    def profile_email(self) -> str:
        return "wrong@example.com"

    def ensure_label(self, *args, **kwargs):
        self.label_checked = True
        raise AssertionError("Labels must not be inspected after mailbox mismatch")


def test_wrong_authenticated_mailbox_stops_before_label_access() -> None:
    gmail = _WrongMailboxGmail()
    settings = Settings(
        expected_gmail_address="target@example.com",
        dry_run=False,
        use_llm=False,
    )
    with pytest.raises(RuntimeError, match="Mailbox safety check failed"):
        EmailClassificationAgent(
            gmail, ClassificationPipeline(settings), settings
        ).run(max_messages=1)
    assert not gmail.label_checked
