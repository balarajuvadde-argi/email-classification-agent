#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from dataclasses import replace
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


def main() -> int:
    fixture_dir = ROOT / "tests" / "fixtures"
    manifest = json.loads((fixture_dir / "manifest.json").read_text(encoding="utf-8"))
    pipeline = ClassificationPipeline(Settings(use_llm=False))

    rows: list[dict[str, object]] = []
    failures: list[str] = []
    for index, item in enumerate(manifest, start=1):
        message = parse_eml(fixture_dir / item["fixture"])
        exact = pipeline.classify(message)
        generalized = pipeline.classify(
            replace(
                message,
                from_header=f"Unrelated Sender {index} <offer-{index}@random-source.example>",
                sender_header="",
                reply_to_header="",
                subject="",
            )
        )
        approved = pipeline.classify(
            replace(
                message,
                from_header="Approved Platform <alerts@mail.redfin.com>",
                sender_header="",
            )
        )
        exact_pass = exact.category is Category.WHOLESALE
        generalized_pass = generalized.category is Category.WHOLESALE
        approved_pass = (
            approved.category is Category.KEEP_IN_INBOX
            and approved.source == "approved_domain"
        )
        passed = exact_pass and generalized_pass and approved_pass
        if not passed:
            failures.append(str(item["original_name"]))
        rows.append(
            {
                "fixture": item["fixture"],
                "original_name": item["original_name"],
                "sha256": item["sha256"],
                "sender": message.from_header,
                "subject": message.subject,
                "exact": {
                    "expected": "WHOLESALE",
                    "actual": exact.category.value,
                    "confidence": exact.confidence,
                    "source": exact.source,
                    "evidence": list(exact.evidence),
                    "passed": exact_pass,
                },
                "generalized_sender_and_blank_subject": {
                    "expected": "WHOLESALE",
                    "actual": generalized.category.value,
                    "confidence": generalized.confidence,
                    "source": generalized.source,
                    "passed": generalized_pass,
                },
                "approved_domain_override": {
                    "expected": "KEEP_IN_INBOX",
                    "actual": approved.category.value,
                    "confidence": approved.confidence,
                    "source": approved.source,
                    "passed": approved_pass,
                },
                "passed": passed,
            }
        )

    output = {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "policy_version": POLICY_VERSION,
        "sample_count": len(rows),
        "scenario_count": len(rows) * 3,
        "passed_samples": sum(bool(row["passed"]) for row in rows),
        "failed_samples": len(failures),
        "all_passed": not failures,
        "results": rows,
    }
    verification_dir = ROOT / "verification"
    verification_dir.mkdir(exist_ok=True)
    json_path = verification_dir / "sample-verification.json"
    json_path.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")

    md_lines = [
        "# Supplied Email Sample Verification",
        "",
        f"Generated: {output['generated_at_utc']}",
        "",
        f"Policy: `{POLICY_VERSION}`",
        "",
        f"Result: **{output['scenario_count']}/{output['scenario_count']} scenarios passed**"
        if not failures
        else f"Result: **{output['passed_samples']}/{output['sample_count']} samples passed**",
        "",
        "Each sample is tested three ways: exact file, unrelated sender with a blank subject, "
        "and the same content from an approved Redfin subdomain.",
        "",
        "| # | Supplied sample | Exact | Generalized | Approved-domain override |",
        "|---:|---|---|---|---|",
    ]
    for index, row in enumerate(rows, start=1):
        name = str(row["original_name"]).replace("|", "\\|")
        exact = row["exact"]
        generalized = row["generalized_sender_and_blank_subject"]
        approved = row["approved_domain_override"]
        md_lines.append(
            f"| {index} | {name} | {exact['actual']} | {generalized['actual']} | "
            f"{approved['actual']} |"
        )
    md_lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "The reference messages are recognized from property-offer evidence rather than "
            "sender, subject, filename, or delivery-vendor shortcuts. The approved-domain "
            "bypass still takes precedence over identical content.",
            "",
        ]
    )
    md_path = verification_dir / "sample-verification.md"
    md_path.write_text("\n".join(md_lines), encoding="utf-8")

    print(json.dumps({"json": str(json_path), "markdown": str(md_path), **output}, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
