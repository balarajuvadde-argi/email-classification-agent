from email_classification_agent.classifier import ClassificationPipeline
from email_classification_agent.config import Settings
from email_classification_agent.heuristics import assess_obvious_wholesale
from email_classification_agent.models import Category, ParsedEmail


def _message(from_header: str, subject: str, body: str) -> ParsedEmail:
    return ParsedEmail(
        message_id="m1",
        thread_id="t1",
        label_ids=("INBOX",),
        internal_date_ms=1,
        from_header=from_header,
        sender_header="",
        reply_to_header="",
        to_header="user@example.com",
        subject=subject,
        body_text=body,
    )


def test_approved_platform_wins_even_with_property_details() -> None:
    message = _message(
        "Zillow <alerts@mail.zillow.com>",
        "New listing: 123 Main St",
        "Asking $450,000. 3 beds, 2 baths, 1,500 sq ft. Schedule a tour.",
    )
    result = ClassificationPipeline(Settings(use_llm=False)).classify(message)
    assert result.category is Category.KEEP_IN_INBOX
    assert result.source == "approved_domain"


def test_lookalike_domain_does_not_bypass_rule() -> None:
    message = _message(
        "Fake Zillow <deals@zillow.com.attacker.test>",
        "Hot off-market deal: 123 Main Street",
        (
            "Asking price $450,000. ARV $650,000. 3 beds, 2 baths, 1,500 sq ft. "
            "Cash buyers only. Call or text to submit an offer."
        ),
    )
    result = ClassificationPipeline(Settings(use_llm=False)).classify(message)
    assert result.category is Category.WHOLESALE


def test_internal_rule_discussion_is_not_obvious_wholesale() -> None:
    message = _message(
        "Maurice <maurice@example.com>",
        "Email classification agent criteria",
        (
            "Please create a folder called Wholesale. The rule should catch deal alerts, "
            "assignment contracts, and emails saying properties are for sale. Approved "
            "platforms include Zillow and Redfin."
        ),
    )
    assessment = assess_obvious_wholesale(message)
    assert assessment.meta_discussion
    assert not assessment.high_confidence_wholesale
    result = ClassificationPipeline(Settings(use_llm=False)).classify(message)
    assert result.category is Category.KEEP_IN_INBOX
    assert result.source == "deterministic_high_confidence_keep"
    assert result.should_mark_processed


def test_real_estate_news_without_offer_is_not_obvious_wholesale() -> None:
    message = _message(
        "Publisher <news@marketjournal.example>",
        "Miami housing prices rise in July",
        "A market report discusses median prices, permits, and inventory trends. No property is offered for sale.",
    )
    assert not assess_obvious_wholesale(message).high_confidence_wholesale
