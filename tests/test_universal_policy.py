import pytest
from pydantic import ValidationError

from email_classification_agent.universal_models import ClassificationPolicy


def _policy(**overrides):
    values = {
        "prompt": "Label invoices as Finance and newsletters as News.",
        "labels": ["Finance", "News"],
    }
    values.update(overrides)
    return ClassificationPolicy(**values)


def test_policy_normalizes_labels_and_forces_spam_trash_exclusions() -> None:
    policy = _policy(labels=[" Finance ", "finance", "News"], gmail_query="in:inbox")

    assert policy.labels == ["Finance", "News"]
    assert "-in:spam" in policy.gmail_query
    assert "-in:trash" in policy.gmail_query


@pytest.mark.parametrize("label", ["INBOX", "Trash", "EmailAgent/Processed/abc"])
def test_policy_rejects_system_and_internal_labels(label: str) -> None:
    with pytest.raises(ValidationError):
        _policy(labels=[label])


@pytest.mark.parametrize("query", ["in:spam", "label:trash newer_than:1d"])
def test_policy_rejects_positive_spam_or_trash_queries(query: str) -> None:
    with pytest.raises(ValidationError):
        _policy(gmail_query=query)


def test_policy_revision_changes_when_classification_semantics_change() -> None:
    original = _policy()
    changed = _policy(prompt="Label invoices and receipts as Finance; newsletters as News.")

    assert original.policy_hash != changed.policy_hash
    assert original.processed_label != changed.processed_label


def test_automatic_toggle_does_not_reclassify_previously_processed_mail() -> None:
    manual = _policy(automatic_enabled=False)
    automatic = _policy(automatic_enabled=True)

    assert manual.policy_hash == automatic.policy_hash
