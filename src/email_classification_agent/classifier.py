from __future__ import annotations

from .config import Settings, secret_json
from .domains import is_approved_domain, origin_domain
from .heuristics import assess_obvious_wholesale
from .llm_classifier import OpenAIEmailClassifier
from .models import Category, ClassificationResult, ParsedEmail


class ClassificationPipeline:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._llm: OpenAIEmailClassifier | None = None
        api_key = _resolve_openai_api_key(settings)
        if settings.use_llm and api_key:
            self._llm = OpenAIEmailClassifier(api_key=api_key, model=settings.openai_model)

    def classify(self, message: ParsedEmail, thread_context: str = "") -> ClassificationResult:
        domain = origin_domain(message.from_header, message.sender_header)
        if is_approved_domain(domain, self._settings.approved_root_domains):
            return ClassificationResult(
                category=Category.KEEP_IN_INBOX,
                confidence=1.0,
                reason=f"Approved listing-platform root domain: {domain}",
                source="approved_domain",
                evidence=(domain,),
            )

        heuristic = assess_obvious_wholesale(message)
        if heuristic.high_confidence_wholesale:
            return ClassificationResult(
                category=Category.WHOLESALE,
                confidence=0.99,
                reason=heuristic.reason,
                source="deterministic_high_confidence",
                evidence=heuristic.evidence,
            )

        if heuristic.high_confidence_keep:
            return ClassificationResult(
                category=Category.KEEP_IN_INBOX,
                confidence=0.99,
                reason=heuristic.reason,
                source="deterministic_high_confidence_keep",
                evidence=heuristic.evidence,
                should_mark_processed=True,
            )

        if self._llm is None:
            return ClassificationResult(
                category=Category.KEEP_IN_INBOX,
                confidence=0.0,
                reason=(
                    "Semantic classification is required but no OpenAI API key is configured. "
                    f"{heuristic.reason}"
                ),
                source="llm_unavailable",
                evidence=heuristic.evidence,
                should_mark_processed=False,
            )

        decision = self._llm.classify(
            message=message,
            origin_domain=domain,
            thread_context=thread_context,
            max_body_chars=self._settings.max_body_chars,
            max_context_chars=self._settings.max_thread_context_chars,
        )
        category = Category(decision.category)
        evidence = tuple(decision.evidence)
        if (
            category is Category.WHOLESALE
            and decision.confidence < self._settings.min_llm_wholesale_confidence
        ):
            return ClassificationResult(
                category=Category.KEEP_IN_INBOX,
                confidence=decision.confidence,
                reason=(
                    f"Model suggested Wholesale below the "
                    f"{self._settings.min_llm_wholesale_confidence:.0%} action threshold: "
                    f"{decision.reason}"
                ),
                source="llm_low_confidence",
                evidence=evidence,
                should_mark_processed=self._settings.mark_low_confidence_processed,
            )
        return ClassificationResult(
            category=category,
            confidence=decision.confidence,
            reason=decision.reason,
            source="llm_semantic",
            evidence=evidence,
            should_mark_processed=True,
        )


def _resolve_openai_api_key(settings: Settings) -> str | None:
    if settings.openai_api_key:
        return settings.openai_api_key
    if settings.openai_api_key_secret_id:
        data = secret_json(settings.openai_api_key_secret_id, settings.aws_region)
        for key in ("api_key", "OPENAI_API_KEY", "openai_api_key"):
            value = data.get(key)
            if value:
                return str(value)
        raise RuntimeError(
            f"Secret {settings.openai_api_key_secret_id!r} must contain api_key or OPENAI_API_KEY"
        )
    return None
