from __future__ import annotations

from email.utils import parseaddr


def normalize_domain(domain: str) -> str:
    normalized = domain.strip().strip(".").lower()
    if not normalized:
        return ""
    try:
        return normalized.encode("idna").decode("ascii")
    except UnicodeError:
        return normalized


def domain_from_address(header_value: str) -> str:
    _, address = parseaddr(header_value or "")
    if "@" not in address:
        return ""
    return normalize_domain(address.rsplit("@", 1)[1])


def origin_domain(from_header: str, sender_header: str = "") -> str:
    return domain_from_address(from_header) or domain_from_address(sender_header)


def matches_root_domain(domain: str, root_domain: str) -> bool:
    domain = normalize_domain(domain)
    root = normalize_domain(root_domain)
    return bool(domain and root and (domain == root or domain.endswith("." + root)))


def is_approved_domain(domain: str, approved_roots: tuple[str, ...]) -> bool:
    return any(matches_root_domain(domain, root) for root in approved_roots)
