# Email Classification Agent Verification Report

**Date:** August 20, 2026  
**Package version:** 2.0.0  
**Policy version:** `2026-08-20-v2`

## Overall result

**Requirement logic, reference coverage, parser hardening, and write-safety controls: PASS**  
**Production activation in the intended Gmail mailbox: NOT PERFORMED IN THIS REVISION**

The updated package implements the latest written rule as the controlling policy:

- Approved root domains and true subdomains remain in the main inbox.
- Actual property-sales messages from every other origin receive `Wholesale`.
- Non-offer, internal, news, transactional, vendor, recruiting, and ambiguous messages remain in the main inbox.
- `INBOX` is never removed; no message is archived, deleted, trashed, or sent.

## Current uploaded references

The nine `.eml` files supplied in the current request were checked directly.

- Files checked: **9**
- Byte-for-byte matches to the recorded regression fixtures: **9/9**
- Classified `WHOLESALE`: **9/9**
- Decision source: **deterministic high confidence**
- Confidence: **0.99 for every file**

## Generalization verification

Each of the nine reference messages was tested in three scenarios:

1. Exact supplied content and original sender.
2. Original sender replaced by an unrelated random non-approved domain and subject removed.
3. Identical content rewritten to an approved `mail.redfin.com` sender.

Result: **27/27 scenarios passed**.

This demonstrates that the reference files are positive examples rather than sender, subject, filename, or delivery-vendor shortcuts. The same property content remains protected by the approved-domain bypass when it truly comes from an approved root.

## Automated test results

- Python compilation: **PASS**
- Python package wheel build: **PASS**
- Pytest: **53 passed**
- Approved roots and true subdomains: **PASS**
- Case and trailing-dot domain normalization: **PASS**
- Lookalike/suffix domains: **REJECTED**
- Simple listing, owner outreach, land, assignment, disposition, brokerage, masked-address, subject-only, and offering-memorandum cases: **PASS**
- Internal rule/setup/training messages: **kept in inbox**
- Attached `.eml` training sample isolation: **PASS**
- News/editorial property article: **kept in inbox**
- Closing/legal transaction: **kept in inbox**
- Inspection/vendor invoice: **kept in inbox**
- Recruiting message mentioning wholesaling: **kept in inbox**
- Large Gmail text body returned via `attachmentId`: **hydrated and classified**
- Binary/file attachment during body hydration: **not fetched**
- HTML style/script/head noise: **excluded from classification text**
- Prior-thread context: **only earlier messages included, including same-timestamp ordering protection**
- Wrong-mailbox guard: **stops before label access**
- Dry run: **no message write**
- Live mock flow: **adds only Wholesale and processed labels**

## Production gaps corrected in version 2

### Large Gmail text bodies

Gmail may return an unnamed `text/plain` or `text/html` MIME body through a separate body `attachmentId`. Version 1 parsed only inline `body.data`, which could leave a large message with an empty body. Version 2 hydrates only those unnamed text body parts before classification and does not fetch PDF, image, spreadsheet, document, or attached `.eml` files through that path.

### Reference-overfitting protection

The source code is audited to ensure that reference sender names and domains are not hardcoded. Regression tests replace both sender and subject while retaining the body evidence.

### False-positive boundaries

The deterministic stage now separates actionable property offers from:

- internal rule/configuration/training correspondence;
- news and market commentary;
- title, legal, closing, mortgage, insurance, appraisal, inspection, permitting, invoice, receipt, and vendor messages;
- recruiting messages;
- ordinary property-related conversation without an offer.

Mixed or ambiguous cases are delegated to structured semantic classification rather than being auto-labeled.

### Prompt-injection boundary

The semantic instructions treat current email text, quoted thread content, and attachment filenames as untrusted data. Instructions embedded in an email cannot change the classification policy.

### Versioned reprocessing

The hidden processed marker is now `EmailAgent/Processed/v2`. This permits a v2 backfill to re-evaluate messages that might have been marked by an older policy without removing the old hidden marker.

## Write and policy safety audit

Every audit control passed:

- no archive, move, trash, delete, send, or label-removal wrapper method;
- no destructive Gmail API call;
- every message modification uses `removeLabelIds: []`;
- dry-run default is enabled;
- exact mailbox identity gate is present;
- structured model storage is disabled;
- prompt-injection boundary is present;
- large-body hydration is text-only and filename-guarded;
- reference senders are not hardcoded;
- versioned processed label is configured;
- below-threshold semantic decisions remain eligible for retry by default;
- test fixtures are excluded from the Lambda deployment artifact.

## Deployment status and limitation

No live backfill or scheduled production deployment was performed during this revision. Activating the rule still requires OAuth authorization for the intended Gmail mailbox, the production model key, and access to the target AWS account. The deployment must start in dry-run mode and be reviewed before live labeling is enabled.

No classifier can truthfully guarantee zero errors on every future natural-language email. This package reduces that risk by combining deterministic approved-domain precedence, conservative high-confidence rules, semantic fallback, a high action threshold, fail-safe inbox behavior, versioned reprocessing, and explicit review evidence.

## Evidence files

- `pytest.txt`
- `compileall.txt`
- `package-build.txt`
- `final-qa-summary.json`
- `cli-smoke.json`
- `sample-verification.json` and `.md`
- `current-upload-verification.json`
- `safety-audit.json` and `.md`
- `docs/REQUIREMENT_TRACEABILITY.md`
