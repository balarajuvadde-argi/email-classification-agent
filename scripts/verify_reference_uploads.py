#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from email_classification_agent.classifier import ClassificationPipeline  # noqa: E402
from email_classification_agent.config import POLICY_VERSION, Settings  # noqa: E402
from email_classification_agent.email_parser import parse_eml  # noqa: E402
from email_classification_agent.models import Category  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify supplied .eml files against the requirement-driven policy"
    )
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "verification" / "current-upload-verification.json",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    manifest = json.loads(
        (ROOT / "tests" / "fixtures" / "manifest.json").read_text(encoding="utf-8")
    )
    manifest_by_hash = {item["sha256"]: item for item in manifest}
    pipeline = ClassificationPipeline(Settings(use_llm=False))

    rows: list[dict[str, object]] = []
    for path in args.paths:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        expected_reference = manifest_by_hash.get(digest)
        message = parse_eml(path)
        result = pipeline.classify(message)
        rows.append(
            {
                "file": path.name,
                "sha256": digest,
                "matches_recorded_reference": expected_reference is not None,
                "recorded_reference_name": (
                    expected_reference["original_name"] if expected_reference else None
                ),
                "sender": message.from_header,
                "subject": message.subject,
                "expected": "WHOLESALE",
                "actual": result.category.value,
                "source": result.source,
                "confidence": result.confidence,
                "evidence": list(result.evidence),
                "passed": result.category is Category.WHOLESALE,
            }
        )

    output = {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "policy_version": POLICY_VERSION,
        "file_count": len(rows),
        "all_match_recorded_references": all(
            bool(row["matches_recorded_reference"]) for row in rows
        ),
        "all_classified_wholesale": all(bool(row["passed"]) for row in rows),
        "results": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(output, indent=2, ensure_ascii=False))
    return 0 if output["all_classified_wholesale"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
