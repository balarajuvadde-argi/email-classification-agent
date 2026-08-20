import hashlib
import json
from dataclasses import replace
from pathlib import Path

from email_classification_agent.classifier import ClassificationPipeline
from email_classification_agent.config import Settings
from email_classification_agent.email_parser import parse_eml
from email_classification_agent.models import Category

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"


def _manifest() -> list[dict[str, str]]:
    return json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))


def test_reference_files_match_recorded_hashes() -> None:
    for item in _manifest():
        digest = hashlib.sha256((FIXTURES / item["fixture"]).read_bytes()).hexdigest()
        assert digest == item["sha256"]


def test_reference_classification_does_not_depend_on_original_sender_or_subject() -> None:
    pipeline = ClassificationPipeline(Settings(use_llm=False))
    for index, item in enumerate(_manifest(), start=1):
        original = parse_eml(FIXTURES / item["fixture"])
        generalized = replace(
            original,
            from_header=f"Unrelated Sender {index} <offer-{index}@random-source.example>",
            sender_header="",
            reply_to_header="",
            subject="",
        )
        result = pipeline.classify(generalized)
        assert result.category is Category.WHOLESALE, item["original_name"]
        assert result.source == "deterministic_high_confidence"


def test_same_reference_content_from_approved_platform_stays_in_inbox() -> None:
    pipeline = ClassificationPipeline(Settings(use_llm=False))
    for item in _manifest():
        original = parse_eml(FIXTURES / item["fixture"])
        approved = replace(
            original,
            from_header="Approved Platform <alerts@mail.redfin.com>",
            sender_header="",
        )
        result = pipeline.classify(approved)
        assert result.category is Category.KEEP_IN_INBOX, item["original_name"]
        assert result.source == "approved_domain"


def test_source_code_contains_no_reference_sender_shortcuts() -> None:
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "src" / "email_classification_agent").glob("*.py")
    ).casefold()
    forbidden_reference_tokens = {
        "jefinancialholdings",
        "homesellersres",
        "pattoninvestmentproperties",
        "stellarholdingsllc",
        "shared1.ccsend.com",
        "quickturnproperties",
    }
    assert forbidden_reference_tokens.isdisjoint(source)
