from __future__ import annotations

import os
import re
from collections.abc import Callable

from .domains import is_approved_domain, origin_domain
from .email_parser import parse_gmail_message
from .gmail_client import GmailClient
from .models import ParsedEmail
from .property_appraiser import MiamiDadePropertyClient
from .universal_classifier import UniversalEmailClassifier
from .universal_models import (
    ActionPlan,
    ClassificationPolicy,
    UniversalDecision,
    UniversalOutcome,
    UniversalReport,
)

# LOGGER = logging.getLogger(__name__)
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
MIAMI_DADE_ZIPS = DEFAULT_MIAMI_DADE_ZIPS
DEFAULT_MIAMI_DADE_LABEL_SUFFIX = "Miami-Dade"
DEFAULT_MIAMI_DADE_IMPORTANT_SUFFIX = "Miami-Dade/Important"
DEFAULT_CANDIDATE_SCAN_WINDOW = 100
DEFAULT_ON_MARKET_ROOT_DOMAINS = (
    "zillow.com",
    "redfin.com",
    "sefmatrixmail.com",
    "onehome.com",
)
DEFAULT_ON_MARKET_LABEL = "Acquisitions/On Market"
DEFAULT_WHOLESALE_LABEL = "Acquisitions/Wholesale"


def _env_list(name: str, default: frozenset[str] | tuple[str, ...]) -> tuple[str, ...]:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return tuple(default)
    values = tuple(item.strip() for item in raw.split(",") if item.strip())
    return values or tuple(default)


def _configured_miami_dade_zips() -> tuple[str, ...]:
    values = _env_list("ACQUISITION_MIAMI_DADE_ZIPS", DEFAULT_MIAMI_DADE_ZIPS)
    invalid = [value for value in values if not re.fullmatch(r"\d{5}", value)]
    if invalid:
        raise ValueError(
            "ACQUISITION_MIAMI_DADE_ZIPS must be a comma-separated list of 5-digit ZIP codes"
        )
    return values


def _clean_label_suffix(value: str, *, name: str) -> str:
    cleaned = value.strip().strip("/")
    if not cleaned:
        raise ValueError(f"{name} must not be empty")
    if any(part.strip() == "" for part in cleaned.split("/")):
        raise ValueError(f"{name} must not contain empty label path segments")
    return cleaned


def _miami_dade_label_suffix() -> str:
    raw = os.getenv("ACQUISITION_MIAMI_DADE_LABEL_SUFFIX") or DEFAULT_MIAMI_DADE_LABEL_SUFFIX
    return _clean_label_suffix(raw, name="ACQUISITION_MIAMI_DADE_LABEL_SUFFIX")


def _important_label_suffix() -> str:
    raw = os.getenv("ACQUISITION_IMPORTANT_LABEL_SUFFIX") or DEFAULT_MIAMI_DADE_IMPORTANT_SUFFIX
    return _clean_label_suffix(raw, name="ACQUISITION_IMPORTANT_LABEL_SUFFIX")


def _candidate_scan_window(message_limit: int) -> int:
    raw = os.getenv("CANDIDATE_SCAN_WINDOW")
    if raw is None or raw.strip() == "":
        configured = DEFAULT_CANDIDATE_SCAN_WINDOW
    else:
        try:
            configured = int(raw.strip())
        except ValueError as exc:
            raise ValueError("CANDIDATE_SCAN_WINDOW must be an integer") from exc
    return min(500, max(message_limit, configured))


def _configured_on_market_domains() -> tuple[str, ...]:
    return _env_list("ON_MARKET_ROOT_DOMAINS", DEFAULT_ON_MARKET_ROOT_DOMAINS)


def _configured_on_market_label() -> str:
    raw = os.getenv("ON_MARKET_LABEL") or DEFAULT_ON_MARKET_LABEL
    return _clean_label_suffix(raw, name="ON_MARKET_LABEL")


def _configured_wholesale_label() -> str:
    raw = os.getenv("WHOLESALE_LABEL") or DEFAULT_WHOLESALE_LABEL
    return _clean_label_suffix(raw, name="WHOLESALE_LABEL")


def _label_allowed(label: str, allowed_labels: dict[str, str]) -> str | None:
    configured = allowed_labels.get(label.casefold())
    if configured:
        return configured
    leaf = label.rsplit("/", 1)[-1].casefold()
    for allowed in allowed_labels.values():
        if allowed.rsplit("/", 1)[-1].casefold() == leaf:
            return allowed
    return None


def _is_wholesale_primary(primary_label: str | None) -> bool:
    if not primary_label:
        return False
    folded = primary_label.casefold()
    configured = _configured_wholesale_label().casefold()
    return folded in {"wholesale", "wholesaler", configured} or folded.endswith("/wholesale")


def _on_market_label(allowed_labels: dict[str, str]) -> str | None:
    return _label_allowed(_configured_on_market_label(), allowed_labels)


def _on_market_origin(message: ParsedEmail) -> str:
    return origin_domain(message.from_header, message.sender_header)


def _is_on_market_sender(message: ParsedEmail) -> bool:
    return is_approved_domain(_on_market_origin(message), _configured_on_market_domains())


def _looks_like_on_market_listing(message: ParsedEmail) -> bool:
    text = f"{message.subject}\n{message.body_text}".casefold()
    if not text.strip():
        return False

    listing_cues = (
        "new listing",
        "listing for sale",
        "new or updated",
        "listed",
        "listings already in the search",
        "properties that match the search criteria",
        "saved search",
        "instant update",
        "price cut",
        "for sale",
        "view all properties",
        "see latest search results",
        "mls #",
        "onehome",
        "zillow",
        "redfin",
    )
    source_cue = any(cue in text for cue in listing_cues)
    price_cue = bool(re.search(r"\$\s?\d[\d,]*(?:\.\d+)?\s?(?:k|m)?\b", text, re.I))
    address_cue = bool(
        re.search(
            r"\b\d{2,6}\s+(?:n|s|e|w|ne|nw|se|sw)?\.?\s*"
            r"[a-z0-9 .'-]{2,40}\s+"
            r"(?:st|street|ave|avenue|rd|road|dr|drive|ter|terrace|pl|place|ct|court|"
            r"ln|lane|blvd|boulevard|way|cir|circle|pkwy|parkway)\b",
            text,
            re.I,
        )
    )
    property_detail_cue = bool(
        re.search(r"\b\d+\s*(?:bd|bed|beds|bedroom|ba|bath|baths|sqft|sq\.?\s*ft)\b", text, re.I)
    )
    mls_cue = bool(re.search(r"\bmls\s*#?\s*[a-z]?\d{5,}\b", text, re.I))

    return (source_cue and (price_cue or address_cue or property_detail_cue or mls_cue)) or (
        mls_cue and address_cue
    )


def _deterministic_on_market_decision(
    message: ParsedEmail,
    allowed_labels: dict[str, str],
) -> UniversalDecision | None:
    label = _on_market_label(allowed_labels)
    if not label or not _is_on_market_sender(message) or not _looks_like_on_market_listing(message):
        return None
    domain = _on_market_origin(message) or "an approved on-market listing source"
    return UniversalDecision(
        label=label,
        confidence=0.99,
        reason=(
            f"The email comes from {domain}, an approved on-market listing source, "
            "and contains active listing/search-result evidence such as listing, price, "
            "address, property details, or MLS information."
        ),
        evidence=[domain, "on-market listing evidence"],
    )


def _miami_dade_label(primary_label: str | None, message: ParsedEmail) -> str | None:
    if not _is_wholesale_primary(primary_label):
        return None
    current_text = f"{message.subject} {message.body_text}"
    if any(re.search(rf"(?<!\d){zip_code}(?!\d)", current_text) for zip_code in _configured_miami_dade_zips()):
        return f"{primary_label}/{_miami_dade_label_suffix()}"
    return None


def _secondary_label_chain(primary_label: str | None, secondary_label: str | None) -> tuple[str, ...]:
    if not primary_label or not secondary_label:
        return ()
    important_label = f"{primary_label}/{_important_label_suffix()}"
    miami_dade_label = f"{primary_label}/{_miami_dade_label_suffix()}"
    if secondary_label.casefold() == important_label.casefold():
        return (miami_dade_label, important_label)
    return (secondary_label,)


class ProviderClassificationError(RuntimeError):
    """Signals a model/provider failure that the queue worker may retry."""


class UniversalClassificationAgent:
    """Prompt-driven, add-label-only Gmail classifier for one authenticated user."""

    def __init__(
        self,
        gmail: GmailClient,
        classifier: UniversalEmailClassifier,
        *,
        expected_email: str,
        max_thread_messages: int = 6,
        max_body_chars: int = 20_000,
        max_context_chars: int = 18_000,
        operation_guard: Callable[[], None] | None = None,
        property_client: MiamiDadePropertyClient | None = None,
    ) -> None:
        if not expected_email.strip():
            raise ValueError("expected_email is mandatory for tenant isolation")
        self._gmail = gmail
        self._classifier = classifier
        self._expected_email = expected_email.strip().casefold()
        self._max_thread_messages = max_thread_messages
        self._max_body_chars = max_body_chars
        self._max_context_chars = max_context_chars
        self._operation_guard = operation_guard or (lambda: None)
        self._property_client = property_client

    def preview(self, policy: ClassificationPolicy) -> UniversalReport:
        return self._run(policy, dry_run=True)

    def run_automatic(self, policy: ClassificationPolicy) -> UniversalReport:
        if not policy.automatic_enabled:
            raise RuntimeError("Automatic classification is disabled for this user")
        return self._run(policy, dry_run=False)

    def apply_plan(self, policy: ClassificationPolicy, plan: ActionPlan) -> UniversalReport:
        self._operation_guard()
        mailbox = self._verified_mailbox()
        if plan.mailbox.casefold() != mailbox.casefold():
            raise RuntimeError("Action plan mailbox does not match the authenticated Gmail account")
        if plan.policy_hash != policy.policy_hash:
            raise RuntimeError("Classification policy changed after preview; create a new preview")

        report = UniversalReport(
            mailbox=mailbox,
            dry_run=False,
            policy_hash=policy.policy_hash,
            processed_label=policy.processed_label,
        )
        self._operation_guard()
        processed_id = self._gmail.ensure_label(policy.processed_label, visible=False, create=True)
        label_ids: dict[str, str] = {}
        allowed_labels = {label.casefold(): label for label in policy.labels}
        for outcome in plan.outcomes:
            report.scanned += 1
            if outcome.action == "left_unprocessed_below_threshold":
                report.low_confidence += 1
                report.outcomes.append(outcome)
                continue
            if outcome.action == "no_write":
                report.failed += 1
                report.outcomes.append(outcome)
                continue
            proposed_label = outcome.proposed_label
            secondary_label = outcome.secondary_label
            if proposed_label:
                exact_label = allowed_labels.get(proposed_label.casefold())
                actionable = outcome.action == f"would_add:{proposed_label}"
                if (
                    exact_label is None
                    or not actionable
                    or outcome.confidence < policy.confidence_threshold
                ):
                    report.failed += 1
                    report.outcomes.append(
                        UniversalOutcome(
                            message_id=outcome.message_id,
                            thread_id=outcome.thread_id,
                            subject=outcome.subject,
                            sender=outcome.sender,
                            proposed_label=None,
                            confidence=0.0,
                            action="no_write",
                            reason="Preview action failed server-side validation.",
                        )
                    )
                    continue
                proposed_label = exact_label
                valid_secondary_labels = {
                    f"{proposed_label}/{_miami_dade_label_suffix()}".casefold(),
                    f"{proposed_label}/{_important_label_suffix()}".casefold(),
                }
                if secondary_label and secondary_label.casefold() not in valid_secondary_labels:
                    secondary_label = None
            elif outcome.action != "would_mark_processed_without_destination":
                report.failed += 1
                report.outcomes.append(
                    UniversalOutcome(
                        message_id=outcome.message_id,
                        thread_id=outcome.thread_id,
                        subject=outcome.subject,
                        sender=outcome.sender,
                        proposed_label=None,
                        confidence=0.0,
                        action="no_write",
                        reason="Preview action failed server-side validation.",
                    )
                )
                continue
            try:
                ids = [processed_id]
                if proposed_label:
                    self._operation_guard()
                    label_id = label_ids.get(proposed_label)
                    if label_id is None:
                        label_id = self._gmail.ensure_label(
                            proposed_label,
                            visible=True,
                            create=True,
                        )
                        label_ids[proposed_label] = label_id
                    ids.insert(0, label_id)
                for label_name in _secondary_label_chain(proposed_label, secondary_label):
                    secondary_id = label_ids.get(label_name)
                    if secondary_id is None:
                        secondary_id = self._gmail.ensure_label(
                            label_name,
                            visible=True,
                            create=True,
                        )
                        label_ids[label_name] = secondary_id
                    ids.insert(0, secondary_id)
                self._operation_guard()
                self._gmail.add_labels(
                    outcome.message_id,
                    ids,
                    remove_inbox=bool(proposed_label),
                )
                if proposed_label:
                    report.labeled += 1
                else:
                    report.kept_unlabeled += 1
                outcome.action = (
                    f"added:{proposed_label}"
                    if proposed_label
                    else "marked_processed_without_destination"
                )
                report.outcomes.append(outcome)
            except Exception:  # noqa: BLE001 - isolate each Gmail message
                # LOGGER.error(
                #     "Failed to apply labels to Gmail message %s (%s)",
                #     outcome.message_id,
                #     type(exc).__name__,
                # )
                report.failed += 1
                report.outcomes.append(
                    UniversalOutcome(
                        message_id=outcome.message_id,
                        thread_id=outcome.thread_id,
                        subject=outcome.subject,
                        sender=outcome.sender,
                        proposed_label=proposed_label,
                        confidence=outcome.confidence,
                        action="no_write",
                        reason="Gmail did not accept the label action; no labels were removed.",
                    )
                )
        return report

    def _run(self, policy: ClassificationPolicy, *, dry_run: bool) -> UniversalReport:
        self._operation_guard()
        mailbox = self._verified_mailbox()
        report = UniversalReport(
            mailbox=mailbox,
            dry_run=dry_run,
            policy_hash=policy.policy_hash,
            processed_label=policy.processed_label,
        )
        self._operation_guard()
        processed_id = self._gmail.ensure_label(
            policy.processed_label,
            visible=False,
            create=not dry_run,
        )
        label_ids: dict[str, str] = {}
        allowed_labels = {label.casefold(): label for label in policy.labels}
        for label in policy.labels:
            self._operation_guard()
            label_ids[label] = self._gmail.ensure_label(
                label,
                visible=True,
                create=not dry_run,
            )
        query = (
            policy.gmail_query
            if dry_run
            else f'{policy.gmail_query} -label:"{policy.processed_label}"'
        )
        self._operation_guard()
        message_limit = policy.max_messages_per_run
        candidate_limit = _candidate_scan_window(message_limit)
        message_ids = self._gmail.list_message_ids(query, candidate_limit)
        messages = self._ordered_messages(message_ids)[:message_limit]
        for message in messages:
            report.scanned += 1
            try:
                thread_context = self._thread_context(message)
                self._operation_guard()
                decision = _deterministic_on_market_decision(message, allowed_labels)
                if decision is None:
                    try:
                        decision = self._classifier.classify(
                            message,
                            policy,
                            thread_context=thread_context,
                            max_body_chars=self._max_body_chars,
                            max_context_chars=self._max_context_chars,
                        )
                    except Exception as exc:  # noqa: BLE001 - worker owns provider retries
                        raise ProviderClassificationError from exc
                proposed_label = decision.label
                if proposed_label:
                    proposed_label = allowed_labels.get(proposed_label.casefold(), proposed_label)
                if proposed_label and decision.confidence < policy.confidence_threshold:
                    report.low_confidence += 1
                    action = (
                        "left_unprocessed_below_threshold"
                        if dry_run
                        else "marked_processed_below_threshold"
                    )
                    proposed_label = None
                elif proposed_label:
                    report.proposed += 1
                    action = f"would_add:{proposed_label}" if dry_run else f"added:{proposed_label}"
                else:
                    if dry_run:
                        report.kept_unlabeled += 1
                    action = (
                        "would_mark_processed_without_destination"
                        if dry_run
                        else "marked_processed_without_destination"
                    )
                secondary_label = _miami_dade_label(proposed_label, message)
                property_records = (
                    tuple(record.as_dict() for record in self._property_client.lookup_email(message))
                    if (
                        self._property_client
                        and proposed_label
                        and _is_wholesale_primary(proposed_label)
                        and secondary_label
                    )
                    else ()
                )
                has_qualified = any(record.get("qualifies") for record in property_records)

                if has_qualified:
                    secondary_label = f"{proposed_label}/{_important_label_suffix()}"

                should_process = (
                    decision.label is None
                    or proposed_label is not None
                    or (not dry_run and action == "marked_processed_below_threshold")
                )
                if not dry_run and should_process:
                    ids = [processed_id]
                    if proposed_label:
                        ids.insert(0, label_ids[proposed_label])
                    for label_name in _secondary_label_chain(proposed_label, secondary_label):
                        secondary_id = label_ids.get(label_name)
                        if secondary_id is None:
                            secondary_id = self._gmail.ensure_label(
                                label_name,
                                visible=True,
                                create=True,
                            )
                            label_ids[label_name] = secondary_id
                        ids.insert(0, secondary_id)
                    self._operation_guard()
                    self._gmail.add_labels(
                        message.message_id,
                        ids,
                        remove_inbox=bool(proposed_label),
                    )
                    if proposed_label:
                        report.labeled += 1
                    else:
                        report.kept_unlabeled += 1

                report.outcomes.append(
                    UniversalOutcome(
                        message_id=message.message_id,
                        thread_id=message.thread_id,
                        subject=message.subject[:500],
                        sender=message.from_header[:500],
                        proposed_label=proposed_label,
                        confidence=decision.confidence,
                        action=action,
                        reason=decision.reason,
                        evidence=tuple(decision.evidence),
                        secondary_label=secondary_label,
                        property_records=property_records,
                    )
                )
            except ProviderClassificationError:
                raise
            except Exception:  # noqa: BLE001 - isolate each Gmail message
                # LOGGER.error(
                #     "Failed to classify Gmail message %s (%s)",
                #     message.message_id,
                #     type(exc).__name__,
                # )
                report.failed += 1
                report.outcomes.append(
                    UniversalOutcome(
                        message_id=message.message_id,
                        thread_id="",
                        subject="",
                        sender="",
                        proposed_label=None,
                        confidence=0.0,
                        action="no_write",
                        reason=(
                            "Classification failed for this message; no Gmail action was taken."
                        ),
                    )
                )
        return report

    def _ordered_messages(self, message_ids: list[str]) -> list[ParsedEmail]:
        indexed: list[tuple[int, int, ParsedEmail]] = []
        for index, message_id in enumerate(message_ids):
            self._operation_guard()
            resource = self._gmail.get_message(message_id)
            message = parse_gmail_message(resource)
            indexed.append((index, message.internal_date_ms, message))
        indexed.sort(key=lambda item: (-item[1], item[0], item[2].message_id))
        return [message for _, _, message in indexed]

    def _verified_mailbox(self) -> str:
        mailbox = self._gmail.profile_email().strip().casefold()
        if mailbox != self._expected_email:
            raise RuntimeError(
                f"Tenant mailbox check failed: authenticated as {mailbox!r}, "
                f"expected {self._expected_email!r}"
            )
        return mailbox

    def _thread_context(self, current: ParsedEmail) -> str:
        if not current.thread_id:
            return ""
        try:
            self._operation_guard()
            resource = self._gmail.get_thread(current.thread_id)
        except Exception:  # noqa: BLE001 - current message remains classifiable
            # LOGGER.warning("Could not load thread %s", current.thread_id, exc_info=True)
            return ""
        indexed = [
            (index, parse_gmail_message(item))
            for index, item in enumerate(resource.get("messages") or [])
        ]
        indexed.sort(key=lambda pair: (pair[1].internal_date_ms, pair[0]))
        position = next(
            (
                position
                for position, (_, item) in enumerate(indexed)
                if item.message_id == current.message_id
            ),
            None,
        )
        if position is None:
            prior = [
                item
                for _, item in indexed
                if item.internal_date_ms < current.internal_date_ms
            ]
        else:
            prior = [item for _, item in indexed[:position]]
        chunks: list[str] = []
        for item in prior[-self._max_thread_messages :]:
            chunks.append(
                f"From: {item.from_header[:1_000]}\nSubject: {item.subject[:2_000]}\n"
                f"Body: {item.body_text[:3_000] or '[empty]'}"
            )
        return "\n\n--- PRIOR MESSAGE ---\n\n".join(chunks)
