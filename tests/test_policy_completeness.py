from email_classification_agent.config import APPROVED_ROOT_DOMAINS
from email_classification_agent.domains import is_approved_domain


def test_every_configured_root_and_subdomain_is_approved() -> None:
    assert len(APPROVED_ROOT_DOMAINS) == 16
    for root in APPROVED_ROOT_DOMAINS:
        assert is_approved_domain(root, APPROVED_ROOT_DOMAINS)
        assert is_approved_domain(f"mail.{root}", APPROVED_ROOT_DOMAINS)


def test_every_configured_root_rejects_suffix_lookalike() -> None:
    for root in APPROVED_ROOT_DOMAINS:
        assert not is_approved_domain(f"{root}.attacker.test", APPROVED_ROOT_DOMAINS)
        assert not is_approved_domain(f"fake{root}", APPROVED_ROOT_DOMAINS)
