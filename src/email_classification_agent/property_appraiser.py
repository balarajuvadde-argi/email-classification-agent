from __future__ import annotations

import json
import logging
import os
import re
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from typing import Any

from .heuristics import _DIRECTION, _STREET_TYPE, ADDRESS_RE, MONEY_RE
from .models import ParsedEmail
from .structured_events import structured_event

LOGGER = logging.getLogger(__name__)

BASE_URL = "https://apps.miamidadepa.gov/PApublicServiceProxy/PaServicesProxy.ashx"
DEFAULT_EXCLUDED_MUNICIPALITIES = frozenset({"MIAMI GARDENS", "OPA-LOCKA", "OPALOCKA", "NORTH MIAMI"})
DEFAULT_PRICE_TARGET = 275_000.0
DEFAULT_REQUIRE_DOUBLE_LOT = True
DEFAULT_QUALIFYING_LAND_USE_TERMS = ("SINGLE FAMILY", "DUPLEX", "2 UNITS", "TOWNHOUSE")
DEFAULT_PROPERTY_LOOKUP_TIMEOUT_SECONDS = 12.0
DEFAULT_MIAMI_DADE_ZIPS = frozenset(
    [
        "33010", "33012", "33013", "33014", "33015", "33016", "33018", "33030",
        "33031", "33032", "33033", "33034", "33035", "33054", "33055", "33056",
        "33101", "33109", "33122", "33125", "33126", "33127", "33128", "33129",
        "33130", "33131", "33132", "33133", "33134", "33135", "33136", "33137",
        "33138", "33139", "33140", "33141", "33142", "33143", "33144", "33145",
        "33146", "33147", "33149", "33150", "33154", "33155", "33156", "33157",
        "33158", "33160", "33161", "33162", "33165", "33166", "33167", "33168",
        "33169", "33170", "33172", "33173", "33174", "33175", "33176", "33177",
        "33178", "33179", "33180", "33181", "33182", "33183", "33184", "33185",
        "33186", "33187", "33189", "33190", "33193", "33194", "33196", "33231",
    ]
)
ZIP_RE = re.compile(r"(?<!\d)3[0-4]\d{3}(?!\d)")
ASKING_PRICE_MARKER_RE = re.compile(
    r"\b(?:asking(?:\s+price)?|ask|offer(?:ed)?\s+(?:price|at)|list(?:ing)?\s+price|price|only)\s*[:#-]?\s*",
    re.I,
)
CURRENT_PRICE_MARKER_RE = re.compile(
    r"\b(?:now|reduced|reduction|reduced\s+to)\s*[:#-]?\s*",
    re.I,
)


@dataclass(frozen=True, slots=True)
class PropertyRecord:
    address: str
    asking_price: float | None
    folio: str
    municipality: str
    land_use: str
    legal_description: str
    lot_size_sqft: float | None
    lookup_status: str
    qualifies: bool
    is_folio_30: bool = False
    has_double_lot: bool = False
    is_unincorporated: bool = False
    reasons: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return asdict(self) | {"reasons": list(self.reasons), "price_tag": _price_tag(self)}


def _price_tag(record: PropertyRecord) -> str:
    if record.asking_price is None:
        return "Price Missing"
    return "Target Match" if record.asking_price <= _price_target() else "Above Target"


class MiamiDadePropertyClient:
    def __init__(self, *, timeout_seconds: float | None = None) -> None:
        self._timeout_seconds = (
            timeout_seconds
            if timeout_seconds is not None
            else _env_float(
                "PROPERTY_LOOKUP_TIMEOUT_SECONDS",
                DEFAULT_PROPERTY_LOOKUP_TIMEOUT_SECONDS,
                minimum=1.0,
            )
        )

    def lookup_email(self, message: ParsedEmail) -> list[PropertyRecord]:
        candidates = _extract_candidates(message)
        structured_event(
            LOGGER,
            "property_candidates_extracted",
            message_id=message.message_id,
            subject=message.subject,
            candidate_count=len(candidates),
            candidates=[{"address": address, "asking_price": price} for address, price in candidates],
        )
        return [self.lookup(address, price) for address, price in candidates]

    def lookup(self, address: str, asking_price: float | None = None) -> PropertyRecord:
        clean_addr = _clean_address_query(address)
        structured_event(
            LOGGER,
            "miami_dade_property_lookup_started",
            address=address,
            cleaned_address=clean_addr,
            asking_price=asking_price,
        )
        try:
            search = self._request(
                {
                    "Operation": "GetAddress",
                    "clientAppName": "PropertySearch",
                    "myUnit": "",
                    "from": "1",
                    "myAddress": clean_addr,
                    "to": "200",
                }
            )
            matches = search.get("MinimumPropertyInfos") or []
            if not matches:
                return _unverified(address, asking_price, "address_not_found")
            match = matches[0]
            folio = str(match.get("Strap") or "").strip()
            if not folio:
                return _unverified(address, asking_price, "folio_missing")
            detail = self._request(
                {
                    "Operation": "GetPropertySearchByFolio",
                    "clientAppName": "PropertySearch",
                    "folioNumber": re.sub(r"[^0-9]", "", folio),
                }
            )
            record = _record_from_detail(address, asking_price, folio, match, detail)
            structured_event(
                LOGGER,
                "miami_dade_property_lookup_completed",
                address=address,
                lookup_status=record.lookup_status,
                qualifies=record.qualifies,
                folio=record.folio,
                municipality=record.municipality,
                asking_price=record.asking_price,
                reasons=record.reasons,
            )
            return record
        except Exception as exc:
            structured_event(
                LOGGER,
                "miami_dade_property_lookup_failed",
                address=address,
                error_type=type(exc).__name__,
            )
            return _unverified(address, asking_price, "lookup_failed")

    def _request(self, params: dict[str, str]) -> dict[str, Any]:
        query = urllib.parse.urlencode(params)
        request = urllib.request.Request(
            f"{BASE_URL}?{query}",
            headers={"User-Agent": "InboxPilot-AcquisitionAgent/1.0"},
        )
        with urllib.request.urlopen(request, timeout=self._timeout_seconds) as response:  # noqa: S310
            value = json.loads(response.read())
        if not isinstance(value, dict) or value.get("Completed") is False:
            raise RuntimeError("Property portal returned an incomplete response")
        return value


def _clean_address_query(raw: str) -> str:
    cleaned = raw.strip(" ,.-:\t\r\n")
    noise_pattern = r"(?:for\s+(?:more\s+)?(?:info|information|details)|call|contact|inquiries|details|phone|tel|email)\s*[:#-]?\s*"
    parts = re.split(noise_pattern, cleaned, flags=re.I)
    if len(parts) > 1:
        cleaned = parts[-1].strip(" ,.-:\t\r\n")

    # Find the street address starting with house number and ending with street type
    # e.g., 11370 SW 224th St, 16225 NE 2nd Ave, 890 E 52nd St
    matches = list(
        re.finditer(
            rf"\b(\d{{1,6}}[A-Z]?)\s+(?:{_DIRECTION}\s+)?[A-Z0-9][A-Z0-9.'-]*(?:\s+[A-Z0-9][A-Z0-9.'-]*){{0,3}}\s+{_STREET_TYPE}\b",
            cleaned,
            re.I,
        )
    )
    if matches:
        candidate = matches[-1].group(0).strip(" ,.-")
        directional_starts = list(
            re.finditer(rf"\b\d{{1,6}}[A-Z]?\s+{_DIRECTION}\s+", candidate, re.I)
        )
        if directional_starts and directional_starts[-1].start() > 0:
            candidate = candidate[directional_starts[-1].start() :]
        return candidate.strip(" ,.-")
    return cleaned



def _extract_candidates(message: ParsedEmail) -> list[tuple[str, float | None]]:
    text = _normalize_email_text(f"{message.subject}\n{message.body_text}")
    candidates: list[tuple[str, float | None]] = []
    seen_addresses: set[str] = set()
    matches = list(ADDRESS_RE.finditer(text))

    for index, match in enumerate(matches):
        raw_address = match.group(0).strip(" ,.-")
        clean_address = _clean_address_query(raw_address)
        dedupe_key = _address_dedupe_key(clean_address)
        if dedupe_key in seen_addresses:
            structured_event(
                LOGGER,
                "property_candidate_duplicate_skipped",
                address=clean_address,
                dedupe_key=dedupe_key,
            )
            continue

        next_start = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        property_block = text[match.start() : min(next_start, match.start() + 1_500)]
        if _has_only_non_target_zip(property_block):
            structured_event(
                LOGGER,
                "property_candidate_non_target_zip_skipped",
                address=clean_address,
                zips=sorted(set(ZIP_RE.findall(property_block))),
            )
            continue
        asking_price = _extract_asking_price(property_block)
        if asking_price is None:
            surrounding_context = text[max(0, match.start() - 120) : min(len(text), match.end() + 220)]
            asking_price = _extract_asking_price(surrounding_context)
        
        candidates.append((clean_address, asking_price))
        seen_addresses.add(dedupe_key)

    return candidates


def _normalize_email_text(value: str) -> str:
    value = value.replace("\ufffd", "\n").replace("\xa0", " ")
    value = re.sub(r"[\r\t]+", " ", value)
    value = re.sub(r" {2,}", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def _has_only_non_target_zip(context: str) -> bool:
    zips = set(ZIP_RE.findall(context))
    return bool(zips) and zips.isdisjoint(_configured_miami_dade_zips())


def _configured_miami_dade_zips() -> tuple[str, ...]:
    values = _env_list("ACQUISITION_MIAMI_DADE_ZIPS", DEFAULT_MIAMI_DADE_ZIPS)
    invalid = [value for value in values if not re.fullmatch(r"\d{5}", value)]
    if invalid:
        raise ValueError(
            "ACQUISITION_MIAMI_DADE_ZIPS must be a comma-separated list of 5-digit ZIP codes"
        )
    return values


def _extract_asking_price(context: str) -> float | None:
    current_prices = _prices_after_markers(CURRENT_PRICE_MARKER_RE, context)
    if current_prices:
        return current_prices[-1]
    asking_prices = _prices_after_markers(ASKING_PRICE_MARKER_RE, context)
    if asking_prices:
        return asking_prices[-1]
    if re.search(r"\bper\s+door\b", context[:160], re.I):
        context = context[160:]
    price_match = MONEY_RE.search(context[:300])
    return _money_value(price_match.group(0)) if price_match else None


def _prices_after_markers(pattern: re.Pattern[str], context: str) -> list[float]:
    prices: list[float] = []
    for marker in pattern.finditer(context):
        nearby = context[marker.end() : marker.end() + 90]
        price_match = MONEY_RE.search(nearby)
        if price_match:
            value = _money_value(price_match.group(0))
            if value is not None:
                prices.append(value)
    return prices


def _address_dedupe_key(address: str) -> str:
    value = re.sub(r"[^\w\s]", " ", address.upper())
    value = re.sub(r"\b(\d+)(?:ST|ND|RD|TH)\b", r"\1", value)
    replacements = {
        "STREET": "ST",
        "AVENUE": "AVE",
        "ROAD": "RD",
        "TERRACE": "TER",
        "DRIVE": "DR",
        "COURT": "CT",
        "PLACE": "PL",
        "LANE": "LN",
        "BOULEVARD": "BLVD",
        "CIRCLE": "CIR",
        "PARKWAY": "PKWY",
    }
    parts = [replacements.get(part, part) for part in value.split()]
    return " ".join(parts)


def _money_value(value: str) -> float | None:
    cleaned = value.replace("$", "").replace(",", "").strip()
    suffix = cleaned[-1:].casefold()
    multiplier = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}.get(suffix, 1)
    if suffix in {"k", "m", "b"}:
        cleaned = cleaned[:-1].strip()
    try:
        return float(cleaned) * multiplier
    except ValueError:
        return None


def _env_list(name: str, default: tuple[str, ...] | frozenset[str]) -> tuple[str, ...]:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return tuple(default)
    values = tuple(item.strip() for item in raw.split(",") if item.strip())
    return values or tuple(default)


def _env_float(name: str, default: float, *, minimum: float | None = None) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = float(raw.replace(",", "").strip())
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be at least {minimum:g}")
    return value


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    value = raw.strip().casefold()
    if value in {"1", "true", "yes", "y", "on"}:
        return True
    if value in {"0", "false", "no", "n", "off"}:
        return False
    raise ValueError(f"{name} must be true or false")


def _excluded_municipalities() -> frozenset[str]:
    return frozenset(
        item.upper() for item in _env_list("ACQUISITION_EXCLUDED_MUNICIPALITIES", DEFAULT_EXCLUDED_MUNICIPALITIES)
    )


def _qualifying_land_use_terms() -> tuple[str, ...]:
    return tuple(
        item.upper()
        for item in _env_list("ACQUISITION_QUALIFYING_LAND_USE_TERMS", DEFAULT_QUALIFYING_LAND_USE_TERMS)
    )


def _price_target() -> float:
    return _env_float("ACQUISITION_PRICE_TARGET", DEFAULT_PRICE_TARGET, minimum=0)


def _require_double_lot() -> bool:
    return _env_bool("ACQUISITION_REQUIRE_DOUBLE_LOT", DEFAULT_REQUIRE_DOUBLE_LOT)


def _unverified(address: str, asking_price: float | None, status: str) -> PropertyRecord:
    # LOGGER.info("Miami-Dade PA: Address '%s' unverified (status=%s)", address, status)
    return PropertyRecord(
        address=address,
        asking_price=asking_price,
        folio="",
        municipality="",
        land_use="",
        legal_description="",
        lot_size_sqft=None,
        lookup_status=status,
        qualifies=False,
        is_folio_30=False,
        has_double_lot=False,
        is_unincorporated=False,
        reasons=("Property record could not be verified on the Miami-Dade portal.",),
    )


def _record_from_detail(
    address: str,
    asking_price: float | None,
    folio: str,
    search_match: dict[str, Any],
    detail: dict[str, Any],
) -> PropertyRecord:
    property_info = detail.get("PropertyInfo") or {}
    legal = detail.get("LegalDescription") or {}
    municipality = str(search_match.get("Municipality") or "").strip().upper()
    land_use = str(property_info.get("DORDescription") or "").strip()
    legal_description = str(legal.get("Description") or "").replace("|", " ").strip()
    lot_size = _number(property_info.get("LotSize"))

    is_folio_30 = folio.startswith("30-")
    is_unincorporated = is_folio_30 or "UNINCORPORATED" in municipality
    is_excluded_muni = municipality in _excluded_municipalities()

    # Double lot detection from the official legal description only. Avoid false
    # positives like "LOT 1 BLK 2 LOT SIZE 50 X 110", where the second number is
    # a block number and not another lot.
    has_double_lot = bool(
        re.search(
            r"\bLOTS?\s+\d+[A-Z]?\s*(?:AND|THROUGH|THRU|TO|-|&)\s*\d+[A-Z]?\b",
            legal_description,
            re.I,
        )
    )

    land_use_upper = land_use.upper()
    is_single_family_or_duplex = any(
        term in land_use_upper for term in _qualifying_land_use_terms()
    )

    price_target = _price_target()
    require_double_lot = _require_double_lot()
    target_display = f"${price_target:,.0f}"
    price_under_target = asking_price is not None and asking_price <= price_target
    price_specified = asking_price is not None

    reasons: list[str] = []
    if is_folio_30:
        reasons.append("folio starts with 30 (unincorporated Miami-Dade)")
    else:
        prefix = folio[:2] if len(folio) >= 2 else "unknown"
        reasons.append(f"folio {folio} does not start with 30 (prefix: {prefix}, muni: {municipality or 'unknown'})")

    if is_excluded_muni:
        reasons.append(f"excluded municipality: {municipality}")

    if has_double_lot:
        reasons.append("legal description indicates double/multiple lots")
    else:
        reasons.append("legal description does not show multiple lot numbers")
    
    if is_single_family_or_duplex:
        reasons.append(f"qualifying zoning/land use: {land_use}")

    if price_under_target:
        reasons.append(f"asking price ${asking_price:,.0f} is at or below {target_display} target")
    elif price_specified:
        reasons.append(f"asking price ${asking_price:,.0f} is above {target_display} target")
    else:
        reasons.append("asking price was not found in the email")

    # Qualification criteria:
    # 1. Folio must start with 30 (unincorporated county)
    # 2. Municipality must not be excluded (Miami Gardens, Opa-locka, North Miami)
    # 3. Must be Single Family / Duplex
    # 4. Official legal description must show more than one lot
    # 5. Email asking price must be at or below the $275k target
    qualifies = (
        is_folio_30
        and not is_excluded_muni
        and is_single_family_or_duplex
        and (has_double_lot or not require_double_lot)
        and price_under_target
    )

    # LOGGER.info(
    #     "Miami-Dade PA: Result for '%s' -> Folio: %s (Folio30: %s), Muni: %s, LandUse: %s, DoubleLot: %s, Qualifies: %s, Reasons: %s",
    #     address,
    #     folio,
    #     is_folio_30,
    #     municipality,
    #     land_use,
    #     has_double_lot,
    #     qualifies,
    #     reasons,
    # )

    return PropertyRecord(
        address=address,
        asking_price=asking_price,
        folio=folio,
        municipality=municipality,
        land_use=land_use,
        legal_description=legal_description,
        lot_size_sqft=lot_size,
        lookup_status="verified",
        qualifies=qualifies,
        is_folio_30=is_folio_30,
        has_double_lot=has_double_lot,
        is_unincorporated=is_unincorporated,
        reasons=tuple(reasons),
    )


def _number(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None
