from __future__ import annotations

import json
import logging
import re
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from typing import Any

from .heuristics import _DIRECTION, _STREET_TYPE, ADDRESS_RE, MONEY_RE
from .models import ParsedEmail

# LOGGER = logging.getLogger(__name__)

BASE_URL = "https://apps.miamidadepa.gov/PApublicServiceProxy/PaServicesProxy.ashx"
EXCLUDED_MUNICIPALITIES = frozenset({"MIAMI GARDENS", "OPA-LOCKA", "OPALOCKA", "NORTH MIAMI"})


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
        return asdict(self) | {"reasons": list(self.reasons)}


class MiamiDadePropertyClient:
    def __init__(self, *, timeout_seconds: float = 12.0) -> None:
        self._timeout_seconds = timeout_seconds

    def lookup_email(self, message: ParsedEmail) -> list[PropertyRecord]:
        candidates = _extract_candidates(message)
        # LOGGER.info(
        #     "Property Appraiser: Extracted %d candidate address(es) from email '%s'",
        #     len(candidates),
        #     message.subject,
        # )
        return [self.lookup(address, price) for address, price in candidates]

    def lookup(self, address: str, asking_price: float | None = None) -> PropertyRecord:
        clean_addr = _clean_address_query(address)
        # LOGGER.info(
        #     "Miami-Dade PA: Querying address '%s' (cleaned: '%s', asking_price=%s)",
        #     address,
        #     clean_addr,
        #     asking_price,
        # )
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
            return _record_from_detail(address, asking_price, folio, match, detail)
        except Exception as exc:
            # LOGGER.warning("Miami-Dade PA lookup exception for '%s': %s", address, exc)
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
        return matches[-1].group(0).strip(" ,.-")
    return cleaned



def _extract_candidates(message: ParsedEmail) -> list[tuple[str, float | None]]:
    text = re.sub(r"\s+", " ", f"{message.subject} {message.body_text}").strip()
    candidates: list[tuple[str, float | None]] = []
    seen_addresses: set[str] = set()

    for match in ADDRESS_RE.finditer(text):
        raw_address = match.group(0).strip(" ,.-")
        clean_address = _clean_address_query(raw_address)
        if clean_address.casefold() in seen_addresses:
            continue

        context = text[match.start() : match.end() + 140]
        price_match = MONEY_RE.search(context)
        asking_price = _money_value(price_match.group(0)) if price_match else None
        
        candidates.append((clean_address, asking_price))
        seen_addresses.add(clean_address.casefold())

    return candidates


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
    is_excluded_muni = municipality in EXCLUDED_MUNICIPALITIES

    # Double lot detection (e.g. LOT 12 AND 13, LOTS 15 & 16, LOTS 17 THROUGH 19, DOUBLE LOT)
    has_double_lot = bool(
        re.search(
            r"\bLOTS?\s+\d+\s*(?:AND|THROUGH|TO|-|&)\s*\d+|\bDOUBLE\s+LOT\b|\b2\s+LOTS?\b|\bMULTIPLE\s+LOTS?\b",
            legal_description,
            re.I,
        )
    )

    is_single_family_or_duplex = (
        "SINGLE FAMILY" in land_use.upper()
        or "DUPLEX" in land_use.upper()
        or "2 UNITS" in land_use.upper()
        or "TOWNHOUSE" in land_use.upper()
    )

    price_under_target = asking_price is not None and asking_price <= 275_000
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
    
    if is_single_family_or_duplex:
        reasons.append(f"qualifying zoning/land use: {land_use}")

    if price_under_target:
        reasons.append(f"asking price ${asking_price:,.0f} is under $275,000 target")
    elif price_specified:
        reasons.append(f"asking price ${asking_price:,.0f} is above $275,000 target")

    # Qualification criteria:
    # 1. Folio must start with 30 (unincorporated county)
    # 2. Municipality must not be excluded (Miami Gardens, Opa-locka, North Miami)
    # 3. Must be Single Family / Duplex
    # 4. Must be under $275k target OR have a double lot (which justifies price flexibility)
    qualifies = (
        is_folio_30
        and not is_excluded_muni
        and is_single_family_or_duplex
        and (price_under_target or has_double_lot or asking_price is None)
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
