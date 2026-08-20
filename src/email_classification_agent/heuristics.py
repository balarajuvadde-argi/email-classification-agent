from __future__ import annotations

import re

from .models import HeuristicAssessment, ParsedEmail

# The deterministic stage is intentionally conservative. It catches unmistakable
# property offers and leaves borderline messages to the semantic classifier.
MAX_HEURISTIC_BODY_CHARS = 120_000

MONEY_RE = re.compile(
    r"(?<![\w$])\$\s*(?:"
    r"\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?"
    r"|\d{5,}(?:\.\d{1,2})?"
    r"|\d+(?:\.\d+)?\s*[KMB]"
    r")\b",
    re.I,
)

_DIRECTION = r"(?:N|S|E|W|NE|NW|SE|SW)\.?"
_STREET_TYPE = (
    r"(?:ST(?:REET)?|AVE(?:NUE)?|RD|ROAD|BLVD|BOULEVARD|DR|DRIVE|CT|COURT|"
    r"TER(?:RACE)?|LN|LANE|WAY|PL|PLACE|PKWY|PARKWAY|HWY|HIGHWAY|CIR(?:CLE)?|"
    r"TRL|TRAIL|PLZ|PLAZA|CV|COVE)"
)
_NUMBER_OR_MASK = r"(?:\d{1,6}[A-Z]?|[Xx]{2,6}|\d{1,3}[Xx]{1,4})"
ADDRESS_RE = re.compile(
    rf"\b{_NUMBER_OR_MASK}\s+(?:{_DIRECTION}\s+)?"
    rf"[A-Z0-9][A-Z0-9.'-]*(?:\s+[A-Z0-9][A-Z0-9.'-]*){{0,6}}\s+{_STREET_TYPE}\b",
    re.I,
)

# Vacant-land messages sometimes identify only a cross-street or a street without a
# building number, e.g. "NE 152nd Terrace, North Miami Beach, FL 33162".
STREET_LOCATION_RE = re.compile(
    rf"\b{_DIRECTION}\s+\d{{1,4}}(?:ST|ND|RD|TH)?\s+{_STREET_TYPE}\s*,?\s*"
    r"[A-Z][A-Z .'-]{1,40},?\s+(?:[A-Z]{2}|FLORIDA)\s+\d{5}\b",
    re.I,
)

STATE_CODES = (
    "AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|"
    "MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|"
    "WA|WV|WI|WY|DC"
)
STATE_ZIP_RE = re.compile(rf"\b(?:{STATE_CODES}|FLORIDA)\s+\d{{5}}(?:-\d{{4}})?\b", re.I)
PARCEL_RE = re.compile(
    r"\b(?:APN|parcel(?: id| number| no\.?| #)?|folio(?: number| no\.?| #)?|tax id)"
    r"\s*[:#-]?\s*[A-Z0-9][A-Z0-9-]{5,}\b",
    re.I,
)

PROPERTY_DETAIL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("street address", ADDRESS_RE),
    ("street/location", STREET_LOCATION_RE),
    ("parcel identifier", PARCEL_RE),
    ("price", MONEY_RE),
    ("asking price", re.compile(r"\basking(?: price)?\s*:?\s*(?:\$|\d)", re.I)),
    ("after-repair value/comps", re.compile(r"\bARV(?: value)?\b|after[- ]repair value|\bcomps?\s*[:$]", re.I)),
    (
        "bed/bath count",
        re.compile(
            r"\b(?:beds?|bedrooms?|br)\s*[:/-]?\s*\d"
            r"|\b\d+\s*(?:beds?|bedrooms?|br)\b"
            r"|\b(?:baths?|bathrooms?|ba)\s*[:/-]?\s*\d"
            r"|\b\d+\s*(?:baths?|bathrooms?|ba)\b",
            re.I,
        ),
    ),
    ("square footage/lot", re.compile(r"\b(?:sq\.?\s*ft|sqft|square feet|lot size|lot sqft|acre(?:s|age)?)\b", re.I)),
    (
        "property type",
        re.compile(
            r"\b(?:single[- ]family|SFR|SFH|duplex|triplex|fourplex|quadplex|"
            r"multifamily|multi[- ]family|townhouse|townhome|condo|minium|"
            r"vacant land|land|residential lot|commercial property|mobile home)\b",
            re.I,
        ),
    ),
    ("zoning/development", re.compile(r"\b(?:zoning|zoned|redevelopment|development opportunity|buildable|density|units? permitted)\b", re.I)),
    ("showing/access", re.compile(r"\b(?:showing instructions|lockbox|by appointment|24[- ]hour notice|text me for access|schedule (?:a )?(?:tour|showing))\b", re.I)),
    (
        "condition/exit",
        re.compile(
            r"\b(?:fix\s*(?:&|and)\s*flip|full rehab|light rehab|cosmetic rehab|"
            r"value[- ]add|buy\s*(?:&|and)\s*hold|vacant at clos(?:e|ing)|"
            r"tenant occupied|needs work|cash flow(?:ing)?)\b",
            re.I,
        ),
    ),
    ("state/zip", STATE_ZIP_RE),
)

PROMOTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "explicit sale/offer",
        re.compile(
            r"\b(?:for sale|offered at|listed for sale|property offer|selling (?:my|this|the) "
            r"(?:property|home|house|lot|land)|want to sell (?:my|this|the) "
            r"(?:property|home|house|lot|land))\b"
            r"|\basking(?: price)?\s*:?\s*\$"
            r"|\bonly\s*\$",
            re.I,
        ),
    ),
    (
        "deal promotion",
        re.compile(
            r"\b(?:new deal|hot deal|deal alert|amazing deals?|incredible deals?|"
            r"available propert(?:y|ies)|priced to move|inventory|just landed|"
            r"new listing|featured listing|investment opportunity|offering memorandum|"
            r"investment memorandum|offering package|we are offering|now offering)\b",
            re.I,
        ),
    ),
    ("investment pitch", re.compile(r"\b(?:strong upside|great opportunity|don't miss out|best priced|value[- ]add opportunity)\b", re.I)),
    ("off-market pitch", re.compile(r"\boff[- ]market\b", re.I)),
    (
        "wholesale/disposition wording",
        re.compile(
            r"\b(?:wholesale(?:r|s| deals?| inventory| opportunity| deal flow)?|"
            r"dispo(?:sition)?(?: list| blast| inventory)?|buyers? list)\b",
            re.I,
        ),
    ),
    (
        "cash-buyer terms",
        re.compile(
            r"\b(?:cash buyers?|all deals are cash|cash or private money|private money only|"
            r"buyer assumes|zero day inspection|non[- ]refundable escrow)\b",
            re.I,
        ),
    ),
    (
        "call to action",
        re.compile(
            r"\b(?:call or text|text (?:me|us|to RSVP)|contact (?:me|us)|get more info|"
            r"more pictures|submit (?:an )?offer|name your price|RSVP|request access|"
            r"schedule (?:a )?(?:tour|showing)|reply for (?:details|access))\b",
            re.I,
        ),
    ),
    ("deal economics", re.compile(r"\b(?:escrow|comps?|estimated repairs?|rental income|cap rate|gross income|NOI|assignment fee)\b", re.I)),
    ("brokerage listing", re.compile(r"\b(?:co[- ]broke|MLS participant|licensed (?:real estate )?brokerage|broker listing)\b", re.I)),
)

CONTRACT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("assignment", re.compile(r"\bassign(?:ment|able|or|ee)?\b", re.I)),
    ("disposition", re.compile(r"\b(?:dispo|dispositions?)\b", re.I)),
    ("joint venture", re.compile(r"\b(?:JV|joint venture)\b", re.I)),
    ("equitable interest", re.compile(r"\bequitable interest\b", re.I)),
    ("contract control", re.compile(r"\bcontractually controlled\b|\bunder contract with (?:the )?current owner\b", re.I)),
    ("purchase agreement", re.compile(r"\bpurchase and sale agreement\b", re.I)),
    ("not owner of record", re.compile(r"\bmay not be the owner of record\b", re.I)),
)

META_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bemail classification agent\b", re.I),
    re.compile(r"\b(?:email|acquisitions?) classifier\b", re.I),
    re.compile(r"\bacquisitions agent\s*-\s*(?:wholesale|on market)\s*-\s*(?:typical email received|basic proposed criteria|real estate platforms)\b", re.I),
    re.compile(r"\bapproved (?:listing )?platforms?\b", re.I),
    re.compile(r"\bcreate (?:a )?(?:folder|label|rule)\b", re.I),
    re.compile(r"\b(?:set up|configure|implement) (?:the |this |a )?(?:email |inbox )?(?:rule|filter|agent|classifier)\b", re.I),
    re.compile(r"\bsort(?:ing)? (?:my |the )?emails?\b", re.I),
    re.compile(r"\bmeeting notes?\b", re.I),
    re.compile(r"\b(?:training|reference|test) (?:emails?|examples?|fixtures?)\b", re.I),
    re.compile(r"\bprompt(?:ing)? (?:the )?(?:agent|classifier|AI)\b", re.I),
)

NEGATIVE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "news/editorial",
        re.compile(
            r"\b(?:housing market report|market commentary|market analysis|market forecast|"
            r"industry news|real estate news|editorial|press release|median (?:sale )?price|"
            r"market trends?|read the full article)\b",
            re.I,
        ),
    ),
    (
        "closing/legal transaction",
        re.compile(
            r"\b(?:closing disclosure|settlement statement|title commitment|title policy|"
            r"wire instructions|closing package|escrow statement|legal notice|attorney review|"
            r"recording confirmation|deed recorded)\b",
            re.I,
        ),
    ),
    (
        "service/vendor message",
        re.compile(
            r"\b(?:mortgage rate update|loan approval|pre[- ]approval|insurance quote|"
            r"inspection report|appraisal report|repair estimate|permit status|invoice|receipt|"
            r"property management statement|tenant ledger|vendor proposal)\b",
            re.I,
        ),
    ),
    (
        "recruiting/career",
        re.compile(r"\b(?:now hiring|job opening|career opportunity|apply for (?:the |this )?(?:job|position)|commission for licensed realtors)\b", re.I),
    ),
)


def _match_labels(text: str, patterns: tuple[tuple[str, re.Pattern[str]], ...]) -> list[str]:
    return [label for label, pattern in patterns if pattern.search(text)]


def _prefixed(prefix: str, labels: list[str]) -> list[str]:
    return [f"{prefix}: {label}" for label in labels]


def assess_obvious_wholesale(message: ParsedEmail) -> HeuristicAssessment:
    body = message.body_text[:MAX_HEURISTIC_BODY_CHARS]
    text = f"{message.subject}\n{body}"

    property_labels = _match_labels(text, PROPERTY_DETAIL_PATTERNS)
    promotion_labels = _match_labels(text, PROMOTION_PATTERNS)
    contract_labels = _match_labels(text, CONTRACT_PATTERNS)
    negative_labels = _match_labels(text, NEGATIVE_PATTERNS)
    meta_discussion = any(pattern.search(text) for pattern in META_PATTERNS)

    property_set = set(property_labels)
    promotion_set = set(promotion_labels)

    has_location = bool(
        property_set
        & {"street address", "street/location", "parcel identifier", "state/zip"}
    )
    has_economics = bool(
        property_set & {"price", "asking price", "after-repair value/comps"}
    )
    has_description = bool(
        property_set
        & {
            "bed/bath count",
            "square footage/lot",
            "property type",
            "zoning/development",
            "condition/exit",
        }
    )
    explicit_offer = bool(
        promotion_set
        & {
            "explicit sale/offer",
            "deal promotion",
            "off-market pitch",
            "wholesale/disposition wording",
            "brokerage listing",
        }
    )
    actionable = bool(
        promotion_set & {"call to action", "cash-buyer terms"}
        or "showing/access" in property_set
    )

    dense_promotional_block = len(property_labels) >= 4 and len(promotion_labels) >= 2
    direct_specific_offer = has_location and has_economics and explicit_offer
    location_actionable_offer = has_location and explicit_offer and actionable
    descriptive_offer = (
        has_economics
        and has_description
        and explicit_offer
        and (actionable or len(promotion_labels) >= 2)
    )
    contract_offer = (
        len(contract_labels) >= 1
        and has_location
        and (has_economics or has_description)
    )

    high_confidence_wholesale = (
        dense_promotional_block
        or direct_specific_offer
        or location_actionable_offer
        or descriptive_offer
        or contract_offer
    )

    # Internal implementation/training correspondence is never auto-labeled by the
    # deterministic stage, even when it quotes rule vocabulary. The semantic stage may
    # still inspect a genuinely unusual case.
    if meta_discussion:
        high_confidence_wholesale = False

    # News, closing, vendor, and recruiting language can share real-estate vocabulary.
    # Require an unmistakably actionable offer before overriding those negative cues.
    strong_actionable_offer = (
        explicit_offer
        and actionable
        and has_economics
        and (has_location or has_description)
    ) or (contract_offer and (explicit_offer or actionable))
    if negative_labels and not strong_actionable_offer:
        high_confidence_wholesale = False

    high_confidence_keep = meta_discussion and len(property_labels) < 3

    if high_confidence_wholesale:
        reason = (
            "Non-approved sender with an actionable property offer supported by "
            f"{len(property_labels)} property-detail signal(s), "
            f"{len(promotion_labels)} offer/promotion signal(s), and "
            f"{len(contract_labels)} contract signal(s)."
        )
    elif high_confidence_keep:
        reason = "Internal rule, implementation, meeting, or training correspondence; no current property offer."
    elif negative_labels:
        reason = "Real-estate-related context is present, but the message also has non-offer/news/transactional signals and needs semantic review."
    else:
        reason = "The deterministic evidence is not strong enough for a safe automatic Wholesale action."

    evidence = tuple(
        (
            _prefixed("property", property_labels)
            + _prefixed("offer", promotion_labels)
            + _prefixed("contract", contract_labels)
            + _prefixed("negative", negative_labels)
        )[:14]
    )
    return HeuristicAssessment(
        property_detail_score=len(property_labels),
        promotion_score=len(promotion_labels),
        contract_score=len(contract_labels),
        negative_score=len(negative_labels),
        meta_discussion=meta_discussion,
        high_confidence_wholesale=high_confidence_wholesale,
        high_confidence_keep=high_confidence_keep,
        evidence=evidence,
        reason=reason,
    )
