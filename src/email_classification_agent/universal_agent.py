from __future__ import annotations

import logging
import re
from collections.abc import Callable

from .email_parser import parse_gmail_message
from .gmail_client import GmailClient
from .models import ParsedEmail
from .universal_classifier import UniversalEmailClassifier
from .universal_models import (
    ActionPlan,
    ClassificationPolicy,
    UniversalOutcome,
    UniversalReport,
)

LOGGER = logging.getLogger(__name__)
MIAMI_DADE_ZIPS = frozenset(
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


def _miami_dade_label(primary_label: str | None, message: ParsedEmail) -> str | None:
    if not primary_label or primary_label.casefold() not in {"wholesale", "wholesaler"}:
        return None
    current_text = f"{message.subject} {message.body_text}"
    if any(re.search(rf"(?<!\d){zip_code}(?!\d)", current_text) for zip_code in MIAMI_DADE_ZIPS):
        return f"{primary_label}/Miami-Dade"
    return None


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
                if secondary_label and secondary_label.casefold() != (
                    f"{proposed_label}/miami-dade"
                ).casefold():
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
                if secondary_label:
                    secondary_id = label_ids.get(secondary_label)
                    if secondary_id is None:
                        secondary_id = self._gmail.ensure_label(
                            secondary_label,
                            visible=True,
                            create=True,
                        )
                        label_ids[secondary_label] = secondary_id
                    ids.insert(0, secondary_id)
                self._operation_guard()
                self._gmail.add_labels(outcome.message_id, ids)
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
            except Exception as exc:  # noqa: BLE001 - isolate each Gmail message
                LOGGER.error(
                    "Failed to apply labels to Gmail message %s (%s)",
                    outcome.message_id,
                    type(exc).__name__,
                )
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
        for label in policy.labels:
            self._operation_guard()
            label_ids[label] = self._gmail.ensure_label(
                label,
                visible=True,
                create=not dry_run,
            )
        query = f'{policy.gmail_query} -label:"{policy.processed_label}"'
        self._operation_guard()
        message_ids = self._gmail.list_message_ids(query, policy.max_messages_per_run)
        for message_id in message_ids:
            report.scanned += 1
            try:
                self._operation_guard()
                resource = self._gmail.get_message(message_id)
                message = parse_gmail_message(resource)
                thread_context = self._thread_context(message)
                self._operation_guard()
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

                should_process = (
                    decision.label is None
                    or proposed_label is not None
                    or (not dry_run and action == "marked_processed_below_threshold")
                )
                if not dry_run and should_process:
                    ids = [processed_id]
                    if proposed_label:
                        ids.insert(0, label_ids[proposed_label])
                    if secondary_label:
                        secondary_id = label_ids.get(secondary_label)
                        if secondary_id is None:
                            secondary_id = self._gmail.ensure_label(
                                secondary_label,
                                visible=True,
                                create=True,
                            )
                            label_ids[secondary_label] = secondary_id
                        ids.insert(0, secondary_id)
                    self._operation_guard()
                    self._gmail.add_labels(message.message_id, ids)
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
                        secondary_label=_miami_dade_label(proposed_label, message),
                    )
                )
            except ProviderClassificationError:
                raise
            except Exception as exc:  # noqa: BLE001 - isolate each Gmail message
                LOGGER.error(
                    "Failed to classify Gmail message %s (%s)",
                    message_id,
                    type(exc).__name__,
                )
                report.failed += 1
                report.outcomes.append(
                    UniversalOutcome(
                        message_id=message_id,
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
            LOGGER.warning("Could not load thread %s", current.thread_id, exc_info=True)
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
