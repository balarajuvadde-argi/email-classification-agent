from __future__ import annotations

import logging

from .classifier import ClassificationPipeline
from .config import POLICY_VERSION, Settings
from .email_parser import parse_gmail_message
from .gmail_client import GmailClient
from .models import Category, MessageOutcome, ParsedEmail, ProcessingReport

LOGGER = logging.getLogger(__name__)


class EmailClassificationAgent:
    def __init__(
        self,
        gmail: GmailClient,
        classifier: ClassificationPipeline,
        settings: Settings,
    ) -> None:
        self._gmail = gmail
        self._classifier = classifier
        self._settings = settings

    def run(self, max_messages: int | None = None) -> ProcessingReport:
        mailbox = self._gmail.profile_email()
        if self._settings.expected_gmail_address:
            expected = self._settings.expected_gmail_address.casefold()
            if mailbox.casefold() != expected:
                raise RuntimeError(
                    f"Mailbox safety check failed: authenticated as {mailbox!r}, expected {expected!r}"
                )

        create_labels = not self._settings.dry_run
        wholesale_label_id = self._gmail.ensure_label(
            self._settings.wholesale_label, visible=True, create=create_labels
        )
        processed_label_id = self._gmail.ensure_label(
            self._settings.processed_label, visible=False, create=create_labels
        )

        if max_messages is None:
            effective_limit: int | None = self._settings.max_messages_per_run
        elif max_messages <= 0:
            effective_limit = None
        else:
            effective_limit = max_messages
        candidate_ids = self._gmail.list_candidate_message_ids(
            base_query=self._settings.gmail_query,
            processed_label_name=self._settings.processed_label,
            max_results=effective_limit,
        )
        report = ProcessingReport(
            mailbox=mailbox,
            dry_run=self._settings.dry_run,
            policy_version=POLICY_VERSION,
        )

        for message_id in candidate_ids:
            report.scanned += 1
            try:
                resource = self._gmail.get_message(message_id)
                message = parse_gmail_message(resource)
                thread_context = self._thread_context(message)
                result = self._classifier.classify(message, thread_context)

                labels_to_add: list[str] = []
                if result.category is Category.WHOLESALE:
                    labels_to_add.append(wholesale_label_id)
                    report.labeled_wholesale += 1
                    action = "would_add_Wholesale" if self._settings.dry_run else "added_Wholesale"
                else:
                    report.kept_in_inbox += 1
                    action = "kept_in_inbox"

                if result.should_mark_processed:
                    labels_to_add.append(processed_label_id)
                else:
                    action += "; left_unprocessed_for_retry"

                if not self._settings.dry_run:
                    self._gmail.add_labels(message.message_id, labels_to_add)

                report.outcomes.append(
                    MessageOutcome(
                        message_id=message.message_id,
                        subject=message.subject,
                        sender=message.from_header,
                        category=result.category.value,
                        source=result.source,
                        confidence=result.confidence,
                        action=action,
                        reason=result.reason,
                    )
                )
            except Exception as exc:  # noqa: BLE001 - isolate per-message failures
                report.failed += 1
                LOGGER.exception("Failed to classify Gmail message %s", message_id)
                report.outcomes.append(
                    MessageOutcome(
                        message_id=message_id,
                        subject="",
                        sender="",
                        category="ERROR",
                        source="exception",
                        confidence=0.0,
                        action="no_write",
                        reason=str(exc),
                    )
                )
        return report

    def _thread_context(self, current: ParsedEmail) -> str:
        if not current.thread_id:
            return ""
        try:
            thread = self._gmail.get_thread(current.thread_id)
        except Exception:  # noqa: BLE001 - current message can still be classified
            LOGGER.warning(
                "Could not load thread context for %s; classifying current message only",
                current.message_id,
                exc_info=True,
            )
            return ""
        indexed = [
            (index, parse_gmail_message(item))
            for index, item in enumerate(thread.get("messages") or [])
        ]
        # Gmail normally returns a thread in chronological order, but sort defensively by
        # internal date and retain the API order as a stable tie-breaker. Selecting by the
        # current message's position prevents a later message with the same timestamp (or a
        # missing timestamp) from leaking into prior context.
        indexed.sort(key=lambda pair: (pair[1].internal_date_ms, pair[0]))
        current_position = next(
            (
                position
                for position, (_, item) in enumerate(indexed)
                if item.message_id == current.message_id
            ),
            None,
        )
        if current_position is not None:
            prior = [item for _, item in indexed[:current_position]]
        elif current.internal_date_ms:
            prior = [
                item
                for _, item in indexed
                if item.message_id != current.message_id
                and item.internal_date_ms < current.internal_date_ms
            ]
        else:
            prior = []
        prior = prior[-self._settings.max_thread_messages :]
        chunks: list[str] = []
        for item in prior:
            body = item.body_text[:3_000]
            attachments = ", ".join(item.attachment_names[:10]) or "[none]"
            chunks.append(
                f"From: {item.from_header}\nSubject: {item.subject}\n"
                f"Attachments: {attachments}\nBody: {body or '[empty]'}"
            )
        return "\n\n--- PRIOR MESSAGE ---\n\n".join(chunks)
