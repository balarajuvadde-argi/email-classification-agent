# Email Classification Agent

**Package version:** 2.0.0  
**Policy version:** `2026-08-20-v2`

A requirement-driven Gmail agent for existing and new inbox mail. It keeps approved listing-platform mail in the main inbox and labels non-approved property-sales messages as **Wholesale**.

## Controlling rule

The agent adds `Wholesale` when both conditions are true:

1. The current email is an actual real-estate property pitch or directly continues one. This includes offers, conventional listings, deal alerts, off-market opportunities, buyer-list/disposition blasts, assignment or assignable contracts, JV pitches, cold property outreach, land/lot offers, recurring inventory, and brief sales follow-ups.
2. The current message's origin domain is not one of the approved roots.

Approved roots and true subdomains remain in the main inbox:

- `zillow.com`
- `trulia.com`
- `streeteasy.com`
- `hotpads.com`
- `outeast.com`
- `realtor.com`
- `move.com`
- `redfin.com`
- `homes.com`
- `craigslist.org`
- `movoto.com`
- `homesnap.com`
- `estately.com`
- `homefinder.com`
- `xome.com`
- `zerodown.com`

The root-domain check is boundary-safe: `mail.zillow.com` is approved; `fakezillow.com` and `zillow.com.attacker.test` are not. Links, logos, display names, quoted text, and `Reply-To` do not create an approved bypass.

## Folder behavior

This release has only two outcomes:

- **Wholesale:** add `Wholesale` and preserve `INBOX`.
- **Main inbox:** do not add `Wholesale`.

Earlier meeting discussion mentioned additional categories, but the latest written instruction is more specific: approved platforms stay in the main inbox, and all other actual property-sales messages go to `Wholesale`. Therefore this package does not move mail into `On Market`, `Off Market`, or `News`.

## Why the supplied examples pass

The nine supplied `.eml` files are regression examples, not sender-specific rules. The code contains no special case for their senders, delivery vendors, subjects, or filenames.

They classify as `WHOLESALE` because they contain requirement-level evidence such as property locations, asking prices, shorthand prices, ARV/comps, property details, off-market/wholesale or disposition wording, showing/access instructions, cash terms, calls to action, or contract-control language.

Generalization tests replace each original sender with an unrelated random domain and remove the subject; all nine still classify `WHOLESALE`. The same nine bodies rewritten to an approved `mail.redfin.com` sender all remain in the inbox.

## Classification flow

1. Verify that the authenticated Gmail address exactly matches `EXPECTED_GMAIL_ADDRESS`.
2. Read unprocessed messages currently in `INBOX`, excluding Spam and Trash.
3. Hydrate large unnamed text MIME parts that Gmail returns via `attachmentId`.
4. Parse plain text, or sanitized HTML when no plain-text body exists.
5. Apply the approved-root-domain bypass.
6. Detect unmistakable non-approved property offers, including concise listing and offering-memorandum messages, with conservative deterministic evidence.
7. Keep obvious internal rule/setup/training messages in the main inbox.
8. Use a structured semantic model for borderline messages and brief thread replies.
9. Require the configured Wholesale confidence threshold, default `0.85`.
10. Add labels only; never remove `INBOX`.

## False-positive controls

The agent distinguishes property pitches from:

- internal implementation, meeting, rule, prompt, or training correspondence;
- news, editorial articles, market reports, and commentary;
- title, legal, closing, mortgage, insurance, appraisal, inspection, permitting, invoice, receipt, and vendor messages;
- recruiting messages mentioning acquisitions or wholesaling;
- ordinary client correspondence that merely references a property.

Current-message content is the unit of action. Only earlier messages from the same thread are provided as context. Attached `.eml` training examples are not merged into the current body. Email content is treated as untrusted data, and the semantic prompt explicitly rejects instructions embedded in an email.

## Message preservation and safety

The Gmail wrapper only adds labels. It has no archive, move, trash, delete, send, or label-removal method. Every write uses:

```json
{
  "addLabelIds": ["..."],
  "removeLabelIds": []
}
```

The visible destination label is `Wholesale`. The hidden processed marker is versioned as `EmailAgent/Processed/v2`, allowing this policy revision to re-evaluate messages previously handled by an older policy marker.

## Local setup

### 1. Create Google OAuth credentials

Enable the Gmail API in a Google Cloud project, configure the OAuth consent screen for the target account, create a Desktop OAuth client, and download its client-secret JSON.

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
python scripts/bootstrap_gmail_oauth.py \
  --client-secret C:\Users\Myfin\Downloads\email-classification-agent-v2\email-classification-agent-v2\client_secret.json \
  --expected-account maurice@sargigroup.com \
  --output gmail_oauth_secret.json
```

python scripts/bootstrap_gmail_oauth.py --client-secret C:\Users\Myfin\Downloads\email-classification-agent-v2\email-classification-agent-v2\client_secret.json --expected-account maurice@sargigroup.com --output gmail_oauth_secret.json

The bootstrap script verifies the authenticated mailbox before writing the token.

### 2. Configure the environment

```bash
cp .env.example .env
```

Set the target mailbox, Gmail OAuth source, and model key. Keep `DRY_RUN=true` initially.

### 3. Verify the package

```bash
python -m pytest -q
python scripts/verify_samples.py
python scripts/safety_audit.py
python -m compileall -q src scripts tests
```

To verify exported reference files directly:

```bash
python scripts/verify_reference_uploads.py path/to/example1.eml path/to/example2.eml
```

### 4. Preview existing inbox mail

```bash
email-classifier backfill
```

This performs no message writes.

### 5. Apply the existing-inbox backfill

```bash
email-classifier backfill --live
```

### 6. Process a scheduled-size batch

```bash
email-classifier run --live
```

## AWS deployment

The included AWS SAM template deploys a Python Lambda function with one concurrent execution and an EventBridge schedule. The default deployment is dry-run.

Create two Secrets Manager secrets:

- Gmail authorized-user OAuth JSON
- OpenAI key JSON: `{"api_key":"..."}`

Then:

```bash
sam build
sam deploy --guided
```

Start with `DryRun=true`, inspect CloudWatch classification output, and only then update to `DryRun=false`.

## CLI reference

```bash
email-classifier classify-eml path/to/message.eml
email-classifier run
email-classifier run --live
email-classifier backfill
email-classifier backfill --live
```

## Key files

- `src/email_classification_agent/` — classifier, parser, Gmail client, agent, and model integration
- `tests/` — policy, reference-generalization, parsing, Gmail-body, thread, domain, safety, and deployment tests
- `tests/fixtures/` — checksummed copies of the nine supplied reference emails
- `docs/POLICY.md` — exact decision policy
- `docs/REQUIREMENT_TRACEABILITY.md` — source precedence and requirement-to-test mapping
- `docs/DEPLOYMENT.md` — activation runbook
- `docs/SECURITY.md` — privacy and write-safety controls
- `verification/` — generated evidence
