#!/usr/bin/env python3
from __future__ import annotations

import ast
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = ROOT / "src" / "email_classification_agent"
CLIENT_PATH = SRC_ROOT / "gmail_client.py"
TEMPLATE_PATH = ROOT / "template.yaml"

FORBIDDEN_METHOD_NAMES = {
    "archive",
    "trash",
    "delete",
    "batch_delete",
    "send",
    "remove_labels",
    "move",
}
FORBIDDEN_GMAIL_CALLS = {"trash", "delete", "batchDelete", "send"}
REFERENCE_SENDER_TOKENS = {
    "jefinancialholdings",
    "homesellersres",
    "pattoninvestmentproperties",
    "stellarholdingsllc",
    "shared1.ccsend.com",
    "quickturnproperties",
}


def _attribute_call_names(tree: ast.AST) -> list[str]:
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            names.append(node.func.attr)
    return names


def main() -> int:
    source = CLIENT_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(CLIENT_PATH))
    class_node = next(
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "GmailClient"
    )
    method_names = {
        node.name for node in class_node.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    call_names = set(_attribute_call_names(class_node))
    all_source = "\n".join(path.read_text(encoding="utf-8") for path in SRC_ROOT.glob("*.py"))
    all_source_folded = all_source.casefold()
    llm_source = (SRC_ROOT / "llm_classifier.py").read_text(encoding="utf-8")
    config_source = (SRC_ROOT / "config.py").read_text(encoding="utf-8")
    template_source = TEMPLATE_PATH.read_text(encoding="utf-8")

    checks = {
        "no_forbidden_wrapper_methods": not bool(method_names & FORBIDDEN_METHOD_NAMES),
        "no_forbidden_gmail_calls": not bool(call_names & FORBIDDEN_GMAIL_CALLS),
        "empty_remove_label_list_present": '"removeLabelIds": []' in source,
        "no_nonempty_remove_label_assignment": "removeLabelIds.append" not in source,
        "inbox_not_removed": 'removeLabelIds": ["INBOX"]' not in source
        and "removeLabelIds': ['INBOX']" not in source,
        "deployment_excludes_test_fixtures": "CodeUri: src/" in template_source,
        "dry_run_default_true": "dry_run: bool = True" in config_source,
        "mailbox_identity_gate_present": "Mailbox safety check failed" in (
            SRC_ROOT / "agent.py"
        ).read_text(encoding="utf-8"),
        "model_storage_disabled": "store=False" in llm_source,
        "prompt_injection_boundary_present": "untrusted data" in llm_source
        and "ignore previous instructions" in llm_source,
        "large_text_body_hydration_is_text_only": 'mime_type not in {"text/plain", "text/html"}' in source
        and "if filename" in source,
        "reference_senders_not_hardcoded": not any(
            token in all_source_folded for token in REFERENCE_SENDER_TOKENS
        ),
        "versioned_processed_label": "EmailAgent/Processed/v2" in config_source
        and "EmailAgent/Processed/v2" in template_source,
        "low_confidence_retry_default": "mark_low_confidence_processed: bool = False"
        in config_source,
    }
    passed = all(checks.values())
    output = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "passed": passed,
        "checks": checks,
        "gmail_client_methods": sorted(method_names),
        "gmail_api_attribute_calls": sorted(call_names),
    }

    verification_dir = ROOT / "verification"
    verification_dir.mkdir(exist_ok=True)
    json_path = verification_dir / "safety-audit.json"
    json_path.write_text(json.dumps(output, indent=2), encoding="utf-8")

    md_lines = [
        "# Write and Policy Safety Audit",
        "",
        f"Generated: {output['generated_at_utc']}",
        "",
        f"Overall result: **{'PASS' if passed else 'FAIL'}**",
        "",
        "| Control | Result |",
        "|---|---|",
    ]
    for name, value in checks.items():
        md_lines.append(f"| {name.replace('_', ' ')} | {'PASS' if value else 'FAIL'} |")
    md_lines.extend(
        [
            "",
            "The Gmail wrapper's only message mutation is label addition. Its request body uses "
            "an empty `removeLabelIds` list, so the `INBOX` label is preserved. Large Gmail "
            "message bodies are hydrated only for unnamed text MIME parts; file attachments are "
            "not fetched by that path.",
            "",
        ]
    )
    md_path = verification_dir / "safety-audit.md"
    md_path.write_text("\n".join(md_lines), encoding="utf-8")

    print(json.dumps({"json": str(json_path), "markdown": str(md_path), **output}, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
