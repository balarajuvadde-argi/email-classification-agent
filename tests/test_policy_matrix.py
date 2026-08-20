import pytest

from email_classification_agent.classifier import ClassificationPipeline
from email_classification_agent.config import Settings
from email_classification_agent.models import Category, ParsedEmail


def _message(subject: str, body: str, from_header: str = "Sender <mail@outside.example>") -> ParsedEmail:
    return ParsedEmail(
        message_id="m",
        thread_id="t",
        label_ids=("INBOX",),
        internal_date_ms=1,
        from_header=from_header,
        sender_header="",
        reply_to_header="",
        to_header="buyer@example.com",
        subject=subject,
        body_text=body,
    )


@pytest.mark.parametrize(
    ("subject", "body"),
    [
        (
            "For sale: 123 Main Street, Miami FL 33147",
            "Asking $425K. 3 beds, 2 baths, single-family home. Reply for details.",
        ),
        (
            "Off-market vacant land opportunity",
            "NE 152nd Terrace, North Miami Beach, FL 33162. Only $149,900. "
            "Vacant land, 5,748 sq ft. Call or text for more information.",
        ),
        (
            "Assignable contract: 987 Oak Road, Tampa FL 33602",
            "Asking $250,000. 2 beds, 1 bath, 1,100 sq ft. $20K assignment fee. "
            "Submit an offer today.",
        ),
        (
            "New disposition list - cash buyers",
            "55 Palm Avenue, Orlando FL 32801. Asking $300,000. ARV $450,000. "
            "3 beds, 2 baths, 1,400 sq ft. Call or text for access.",
        ),
        (
            "Would you buy my house?",
            "I want to sell my house at 55 Palm Avenue, Orlando FL 32801. "
            "Asking $300,000. It is a 3 bed, 2 bath single-family home.",
        ),
        (
            "New brokerage listing: 44 Bay Drive",
            "44 Bay Drive, Miami FL 33133. Offered at $1.2M. 4 beds, 3 baths, "
            "2,600 sq ft. Schedule a showing.",
        ),
        (
            "Off-market deal: 123 Main Street, Miami FL 33147 - $375K",
            "",
        ),
        (
            "Masked-address investment opportunity",
            "6XXX Sunset Drive, South Miami FL 33143. Asking $1.45M. "
            "4 beds, 2 baths, 2,614 sq ft. Fix & flip. Call or text.",
        ),
        (
            "Offering memorandum: 123 Main Street",
            "We are offering 123 Main Street, Miami FL 33147. "
            "Please review the attached offering memorandum and submit an offer.",
        ),
    ],
)
def test_clear_nonapproved_property_pitches_are_wholesale(subject: str, body: str) -> None:
    result = ClassificationPipeline(Settings(use_llm=False)).classify(_message(subject, body))
    assert result.category is Category.WHOLESALE
    assert result.source == "deterministic_high_confidence"
    assert result.confidence >= 0.99


@pytest.mark.parametrize(
    ("subject", "body"),
    [
        (
            "Email classification agent criteria",
            "Create a Wholesale folder. Approved platforms include Zillow and Redfin. "
            "Deal alerts and assignment contracts should be classified by the rule.",
        ),
        (
            "Implementation notes with quoted sample",
            "Meeting notes for the email classifier. Training example: 123 Main Street, "
            "Miami FL 33147, asking $450,000, ARV $650,000, 3 beds, 2 baths, "
            "1,500 sq ft, cash buyers only, submit an offer. This is reference text for "
            "the rule, not a current property offer.",
        ),
        (
            "Market analysis: celebrity home listed for $5M",
            "Real estate news and market analysis about 123 Ocean Drive, Miami FL 33139. "
            "The article reports that the home was listed for sale at $5,000,000. "
            "Read the full article for market commentary.",
        ),
        (
            "Closing package for 123 Main Street",
            "Attorney review of the purchase and sale agreement for 123 Main Street, "
            "Miami FL 33147. Contract price $450,000. The closing package and title "
            "commitment are attached.",
        ),
        (
            "Inspection invoice - 123 Main Street",
            "Invoice and inspection report for 123 Main Street, Miami FL 33147. "
            "Amount due $1,250. This is a vendor invoice, not a property offer.",
        ),
        (
            "Now hiring acquisitions staff",
            "Career opportunity for a wholesale acquisitions role. Apply for the job. "
            "No property is being offered in this email.",
        ),
        (
            "Re: 123 Main Street",
            "Can you send me the inspection report and closing date?",
        ),
    ],
)
def test_non_offer_real_estate_messages_do_not_auto_label_wholesale(
    subject: str, body: str
) -> None:
    result = ClassificationPipeline(Settings(use_llm=False)).classify(_message(subject, body))
    assert result.category is Category.KEEP_IN_INBOX
    assert result.source != "deterministic_high_confidence"


def test_approved_domain_overrides_even_a_clear_property_pitch() -> None:
    message = _message(
        "Off-market deal: 123 Main Street",
        "Asking $450,000. ARV $650,000. 3 beds, 2 baths. Submit an offer.",
        from_header="Platform <alerts@mail.zillow.com>",
    )
    result = ClassificationPipeline(Settings(use_llm=False)).classify(message)
    assert result.category is Category.KEEP_IN_INBOX
    assert result.source == "approved_domain"


def test_approved_brand_or_link_in_body_does_not_override_sender_domain() -> None:
    message = _message(
        "New listing: 123 Main Street",
        "123 Main Street, Miami FL 33147. Asking $450,000. 3 beds, 2 baths, "
        "1,500 sq ft. View photos on Zillow and submit an offer.",
        from_header="Outside Broker <broker@outside.example>",
    )
    result = ClassificationPipeline(Settings(use_llm=False)).classify(message)
    assert result.category is Category.WHOLESALE
