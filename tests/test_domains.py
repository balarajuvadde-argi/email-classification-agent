from email_classification_agent.config import APPROVED_ROOT_DOMAINS
from email_classification_agent.domains import (
    domain_from_address,
    is_approved_domain,
    matches_root_domain,
)


def test_root_domain_and_subdomains_are_approved() -> None:
    assert is_approved_domain("zillow.com", APPROVED_ROOT_DOMAINS)
    assert is_approved_domain("convo.zillow.com", APPROVED_ROOT_DOMAINS)
    assert is_approved_domain("email.redfin.com", APPROVED_ROOT_DOMAINS)
    assert is_approved_domain("alerts.move.com", APPROVED_ROOT_DOMAINS)


def test_lookalike_domains_are_not_approved() -> None:
    assert not matches_root_domain("zillow.com.evil.example", "zillow.com")
    assert not matches_root_domain("fakezillow.com", "zillow.com")
    assert not is_approved_domain("realtor.com.attacker.test", APPROVED_ROOT_DOMAINS)


def test_sender_domain_extraction() -> None:
    assert domain_from_address('Zillow Alerts <alerts@Convo.Zillow.com>') == "convo.zillow.com"


def test_domain_normalization_handles_case_and_trailing_dot() -> None:
    assert matches_root_domain("MAIL.ZILLOW.COM.", "zillow.com")
    assert is_approved_domain("Email.RedFin.Com", APPROVED_ROOT_DOMAINS)


def test_reply_to_or_display_name_is_not_used_as_approved_origin() -> None:
    # The production pipeline passes only From, falling back to Sender when From is absent.
    assert domain_from_address('Zillow via outside <broker@outside.example>') == "outside.example"
