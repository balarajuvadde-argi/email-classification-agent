from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import replace
from pathlib import Path

from dotenv import load_dotenv

from .agent import EmailClassificationAgent
from .classifier import ClassificationPipeline
from .config import Settings
from .email_parser import parse_eml
from .gmail_client import GmailClient


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Thread-aware Gmail Wholesale classifier")
    parser.add_argument("--env-file", default=".env", help="Optional dotenv file")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose debug logging")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run", help="Process a scheduled-size batch")
    run.add_argument("--limit", type=int, default=None)
    run.add_argument("--live", action="store_true", help="Apply labels; default is dry-run")
    run.add_argument("--verbose", action="store_true", help="Enable verbose debug logging")

    backfill = subparsers.add_parser("backfill", help="Process all unprocessed inbox messages")
    backfill.add_argument("--limit", type=int, default=0, help="0 means all")
    backfill.add_argument("--live", action="store_true", help="Apply labels; default is dry-run")
    backfill.add_argument("--verbose", action="store_true", help="Enable verbose debug logging")

    classify = subparsers.add_parser("classify-eml", help="Classify a local .eml file")
    classify.add_argument("path", type=Path)
    classify.add_argument("--verbose", action="store_true", help="Enable verbose debug logging")
    return parser


def main() -> int:
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")

    args = _parser().parse_args()
    load_dotenv(args.env_file, override=False)
    verbose = getattr(args, "verbose", False)
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    settings = Settings.from_env()

    if args.command == "classify-eml":
        message = parse_eml(args.path)
        result = ClassificationPipeline(settings).classify(message)
        payload = {
            "file": str(args.path),
            "category": result.category.value,
            "confidence": result.confidence,
            "source": result.source,
            "reason": result.reason,
            "evidence": result.evidence,
        }
        if os.getenv("PROPERTY_LOOKUP_ENABLED", "false").strip().casefold() == "true":
            from .property_appraiser import MiamiDadePropertyClient

            records = MiamiDadePropertyClient().lookup_email(message)
            payload["property_lookup"] = [r.as_dict() for r in records]

        print(
            json.dumps(
                payload,
                indent=2,
                ensure_ascii=False,
            )
        )
        return 0

    settings = replace(settings, dry_run=not args.live)
    gmail = GmailClient.from_settings(settings)
    classifier = ClassificationPipeline(settings)
    agent = EmailClassificationAgent(gmail, classifier, settings)
    report = agent.run(max_messages=args.limit)
    print(json.dumps(report.as_dict(), indent=2, ensure_ascii=False))
    return 0 if report.failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
