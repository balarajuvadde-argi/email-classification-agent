from email_classification_agent.models import MessageOutcome, ProcessingReport


def test_report_is_json_serializable_shape() -> None:
    report = ProcessingReport(
        mailbox="a@example.com", dry_run=True, policy_version="2026-08-20-v2"
    )
    report.outcomes.append(
        MessageOutcome(
            message_id="m1",
            subject="s",
            sender="f",
            category="KEEP_IN_INBOX",
            source="test",
            confidence=1.0,
            action="kept_in_inbox",
            reason="test",
        )
    )
    data = report.as_dict()
    assert data["policy_version"] == "2026-08-20-v2"
    assert data["outcomes"][0]["message_id"] == "m1"
