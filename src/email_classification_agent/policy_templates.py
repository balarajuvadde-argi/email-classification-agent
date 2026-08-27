from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PolicyTemplate:
    template_id: str
    title: str
    badge: str
    description: str
    labels: tuple[str, ...]
    prompt: str


ALL_ACQUISITIONS_PROMPT = (
    "Classify inbound emails using these rules:\n\n"
    "1. Label public listing-platform property emails as Acquisitions/On Market.\n"
    "This includes Zillow, Redfin, MLS/Matrix, OneHome, and similar official listing "
    "alerts or saved-search emails that contain active listings, new listings, price "
    "changes, MLS numbers, property addresses, or for-sale search results.\n\n"
    "2. Label relationship-based off-market property opportunities as "
    "Acquisitions/Off Market.\n"
    "This includes direct seller, broker, referral, or private relationship opportunities "
    "where a property may be available outside public listing platforms, but the email is "
    "not clearly a wholesale/disposition/assignment blast.\n\n"
    "3. Label non-platform real estate sales opportunity emails as "
    "Acquisitions/Wholesale.\n"
    "This includes property offers, off-market deal blasts, assignment contracts, "
    "disposition lists, deal alerts, JV/property pitches, land or lot offers, and emails "
    "advertising one or more properties for sale when they do not come from Zillow, "
    "Redfin, MLS/Matrix, OneHome, or another official on-market listing source.\n\n"
    "4. Label news-related emails as News.\n"
    "News includes newsletters, article digests, headlines, economic updates, political "
    "updates, business news, market commentary, industry updates, magazine emails, "
    "publisher emails, and editorial content.\n\n"
    "5. If an email mentions real estate only as news or commentary, label it News, not "
    "an Acquisitions label.\n\n"
    "6. Do not label ordinary transactional emails, meeting invites, account/security "
    "emails, invoices, software notifications, personal emails, or ambiguous emails.\n\n"
    "7. If the email does not clearly match one allowed label, leave it unlabeled."
)


ON_MARKET_PROMPT = (
    "Classify inbound emails using this rule:\n\n"
    "Label public listing-platform property emails as Acquisitions/On Market.\n"
    "This includes Zillow, Redfin, MLS/Matrix, OneHome, and similar official listing "
    "alerts or saved-search emails that contain active listings, new listings, price "
    "changes, MLS numbers, property addresses, or for-sale search results.\n\n"
    "Do not label wholesale blasts, direct off-market outreach, newsletters, loan-only "
    "promotions, meeting invites, account/security emails, invoices, software "
    "notifications, personal emails, or ambiguous emails."
)


OFF_MARKET_PROMPT = (
    "Classify inbound emails using this rule:\n\n"
    "Label relationship-based off-market property opportunities as Acquisitions/Off Market.\n"
    "This includes direct seller, broker, referral, or private relationship opportunities "
    "where a property may be available outside public listing platforms.\n\n"
    "Do not label Zillow, Redfin, MLS/Matrix, OneHome, or other public listing-platform "
    "alerts. Do not label clear wholesale/disposition/assignment blasts, newsletters, "
    "meeting invites, account/security emails, invoices, software notifications, personal "
    "emails, or ambiguous emails."
)


WHOLESALE_PROMPT = (
    "Classify inbound emails using this rule:\n\n"
    "Label non-platform real estate sales opportunity emails as Acquisitions/Wholesale.\n"
    "This includes property offers, off-market deal blasts, assignment contracts, "
    "disposition lists, deal alerts, JV/property pitches, land or lot offers, and emails "
    "advertising one or more properties for sale when they do not come from Zillow, "
    "Redfin, MLS/Matrix, OneHome, or another official on-market listing source.\n\n"
    "Do not label public listing-platform alerts, news/commentary, meeting invites, "
    "account/security emails, invoices, software notifications, personal emails, or "
    "ambiguous emails."
)


NEWS_PROMPT = (
    "Classify inbound emails using this rule:\n\n"
    "Label news-related emails as News.\n"
    "News includes newsletters, article digests, headlines, economic updates, political "
    "updates, business news, market commentary, industry updates, magazine emails, "
    "publisher emails, and editorial content.\n\n"
    "If an email mentions real estate only as news or commentary, label it News. Do not "
    "label property offers, listing alerts, deal pitches, meeting invites, account/security "
    "emails, invoices, software notifications, personal emails, or ambiguous emails."
)


POLICY_TEMPLATES: tuple[PolicyTemplate, ...] = (
    PolicyTemplate(
        template_id="all_acquisitions_news",
        title="All acquisitions + news",
        badge="Recommended",
        description="Sort On-Market, Off-Market, Wholesale, and News in one daily policy.",
        labels=(
            "Acquisitions/On Market",
            "Acquisitions/Off Market",
            "Acquisitions/Wholesale",
            "News",
        ),
        prompt=ALL_ACQUISITIONS_PROMPT,
    ),
    PolicyTemplate(
        template_id="on_market_only",
        title="On-Market only",
        badge="Zillow / Redfin / MLS",
        description="Capture public listing-platform alerts and saved-search results only.",
        labels=("Acquisitions/On Market",),
        prompt=ON_MARKET_PROMPT,
    ),
    PolicyTemplate(
        template_id="off_market_only",
        title="Off-Market only",
        badge="Private opportunities",
        description="Capture direct, broker, referral, or relationship-based property leads.",
        labels=("Acquisitions/Off Market",),
        prompt=OFF_MARKET_PROMPT,
    ),
    PolicyTemplate(
        template_id="wholesale_only",
        title="Wholesale only",
        badge="Deal blasts",
        description="Capture non-platform deal alerts, assignments, dispositions, and JV pitches.",
        labels=("Acquisitions/Wholesale",),
        prompt=WHOLESALE_PROMPT,
    ),
    PolicyTemplate(
        template_id="news_only",
        title="News only",
        badge="Editorial",
        description="Capture newsletters, article digests, market commentary, and headlines.",
        labels=("News",),
        prompt=NEWS_PROMPT,
    ),
)


def get_policy_template(template_id: str) -> PolicyTemplate | None:
    for template in POLICY_TEMPLATES:
        if template.template_id == template_id:
            return template
    return None
