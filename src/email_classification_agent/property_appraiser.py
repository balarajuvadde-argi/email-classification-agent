from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from typing import Any

from .heuristics import ADDRESS_RE, MONEY_RE
from .models import ParsedEmail

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
    reasons: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return asdict(self) | {"reasons": list(self.reasons)}


class MiamiDadePropertyClient:
    def __init__(self, *, timeout_seconds: float = 12.0) -> None:
        self._timeout_seconds = timeout_seconds

    def lookup_email(self, message: ParsedEmail) -> list[PropertyRecord]:
        candidates = _extract_candidates(message)
        return [self.lookup(address, price) for address, price in candidates]

    def lookup(self, address: str, asking_price: float | None = None) -> PropertyRecord:
        try:
            search = self._request(
                {
                    "Operation": "GetAddress",
                    "clientAppName": "PropertySearch",
                    "myUnit": "",
                    "from": "1",
                    "myAddress": address,
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
        except Exception:
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


def _extract_candidates(message: ParsedEmail) -> list[tuple[str, float | None]]:
    text = re.sub(r"\s+", " ", f"{message.subject} {message.body_text}").strip()
    candidates: list[tuple[str, float | None]] = []
    for match in ADDRESS_RE.finditer(text):
        address = match.group(0).strip(" ,.-")
        context = text[match.start() : match.end() + 140]
        price_match = MONEY_RE.search(context)
        asking_price = _money_value(price_match.group(0)) if price_match else None
        candidate = (address, asking_price)
        if candidate not in candidates:
            candidates.append(candidate)
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
    reasons: list[str] = []
    if folio.startswith("30-"):
        reasons.append("folio starts with 30")
    else:
        reasons.append("folio does not start with 30")
    if municipality in EXCLUDED_MUNICIPALITIES:
        reasons.append(f"excluded municipality: {municipality}")
    if asking_price is not None and asking_price <= 275_000:
        reasons.append("asking price is at or below $275,000")
    elif asking_price is not None:
        reasons.append("asking price is above $275,000")
    is_single_family = "SINGLE FAMILY" in land_use.upper()
    has_multiple_lots = bool(re.search(r"\bLOT\s+\d+\s+(?:AND|THROUGH|TO|-|&)\s*\d+", legal_description, re.I))
    qualifies = (
        folio.startswith("30-")
        and municipality not in EXCLUDED_MUNICIPALITIES
        and is_single_family
        and (asking_price is None or asking_price <= 275_000)
        and has_multiple_lots
    )
    if has_multiple_lots:
        reasons.append("legal description indicates multiple lots")
    if is_single_family:
        reasons.append("single-family land use")
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
        reasons=tuple(reasons),
    )


def _number(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None
