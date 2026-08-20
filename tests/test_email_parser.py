import base64
from email.message import EmailMessage
from pathlib import Path

from email_classification_agent.classifier import ClassificationPipeline
from email_classification_agent.config import Settings
from email_classification_agent.email_parser import html_to_text, parse_eml, parse_gmail_message
from email_classification_agent.models import Category


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def test_html_to_text_ignores_css_script_and_hidden_head_content() -> None:
    html = """
    <html><head><style>.x { content: 'Hot deal $999,999'; }</style>
    <script>window.fake = '123 Main Street';</script></head>
    <body><h1>Invoice received</h1><p>No property offer.</p>
    <img alt="inspection logo" src="x.png"></body></html>
    """
    text = html_to_text(html)
    assert "Hot deal" not in text
    assert "123 Main Street" not in text
    assert "Invoice received" in text
    assert "inspection logo" in text


def test_attached_reference_eml_is_not_merged_into_current_body(tmp_path: Path) -> None:
    attached = EmailMessage()
    attached["From"] = "Deal Desk <deals@outside.example>"
    attached["To"] = "buyer@example.com"
    attached["Subject"] = "Hot off-market deal"
    attached.set_content(
        "123 Main Street, Miami FL 33147. Asking $450,000. ARV $650,000. "
        "3 beds, 2 baths. Submit an offer."
    )

    parent = EmailMessage()
    parent["From"] = "Project Team <team@example.com>"
    parent["To"] = "buyer@example.com"
    parent["Subject"] = "Email classification agent - reference email"
    parent.set_content(
        "Meeting notes: the attached training example is provided only to configure "
        "the email classification rule."
    )
    parent.add_attachment(
        attached.as_bytes(),
        maintype="message",
        subtype="rfc822",
        filename="wholesale-reference.eml",
    )

    path = tmp_path / "parent.eml"
    path.write_bytes(parent.as_bytes())
    parsed = parse_eml(path)

    assert "123 Main Street" not in parsed.body_text
    assert parsed.attachment_names == ("wholesale-reference.eml",)
    result = ClassificationPipeline(Settings(use_llm=False)).classify(parsed)
    assert result.category is Category.KEEP_IN_INBOX
    assert result.source == "deterministic_high_confidence_keep"


def test_parse_gmail_raw_message_preserves_metadata_and_complete_body() -> None:
    email = EmailMessage()
    email["From"] = "Seller <seller@outside.example>"
    email["To"] = "buyer@example.com"
    email["Subject"] = "For sale: 123 Main Street"
    email.set_content("Asking $450,000. 3 beds, 2 baths. Submit an offer.")

    parsed = parse_gmail_message(
        {
            "id": "m1",
            "threadId": "t1",
            "labelIds": ["INBOX", "UNREAD"],
            "internalDate": "1234",
            "raw": _b64url(email.as_bytes()),
        }
    )
    assert parsed.message_id == "m1"
    assert parsed.thread_id == "t1"
    assert parsed.internal_date_ms == 1234
    assert parsed.subject == "For sale: 123 Main Street"
    assert "Asking $450,000" in parsed.body_text
