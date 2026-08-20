from __future__ import annotations

import logging
from typing import Any

from .agent import EmailClassificationAgent
from .classifier import ClassificationPipeline
from .config import Settings
from .gmail_client import GmailClient


def lambda_handler(event: dict[str, Any] | None, context: Any) -> dict[str, Any]:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    settings = Settings.from_env()
    gmail = GmailClient.from_settings(settings)
    classifier = ClassificationPipeline(settings)
    report = EmailClassificationAgent(gmail, classifier, settings).run()
    return report.as_dict()
