# Universal Email Classification Agent

**Package version:** 3.0.0

This repository now contains two deliberately separate products:

1. **Universal multi-user web application** — each user connects their own Gmail account,
   supplies their own prompt, allowed labels, Gmail search query, confidence threshold, and
   run size, then previews or schedules classification. Deploy this with
   [`template-web.yaml`](template-web.yaml) and follow
   [`docs/WEB_DEPLOYMENT.md`](docs/WEB_DEPLOYMENT.md).
2. **Legacy single-mailbox Wholesale CLI** — the original Maurice/Wholesale policy remains
   available for local and scheduled use through `email-classifier` and `template.yaml`.

Do not mix their credentials. The web application does **not** use, upload, or distribute a
`gmail_oauth_secret.json` file.

## Universal web behavior

Every connected user controls:

- a plain-language classification policy;
- an allow-list of 1–12 Gmail user labels;
- the Gmail Inbox search query;
- a confidence threshold and a maximum of 10 messages per run;
- preview, reviewed apply, and optional scheduled operation;
- a bounded `.eml` preview for testing a single email.

When the allow-list contains `Wholesaler` or `Wholesale`, the web classifier also applies
server-controlled Miami-Dade child labels. General Miami-Dade wholesale emails can receive
`<primary>/Miami-Dade`; verified acquisition targets receive
`<primary>/Miami-Dade/Important`. This secondary routing is determined during preview and
carried into the reviewed apply plan.

The server-controlled safety boundary is not externalized. It permits creation and addition
of allow-listed **user** labels, and removes `INBOX` only from messages that receive a
destination label so Gmail shows them under the chosen label instead of the Inbox. It has no
delete, trash, send, forward, or destination-label removal action. Email and thread content
are untrusted model input and cannot change this action boundary.

## Web credential model

- Create one Google OAuth **Web application** client per environment. Keep its complete JSON
  in AWS Secrets Manager; it is a server credential, not a download for end users.
- Store the OpenAI API key in a separate Secrets Manager secret.
- Each user authorizes through the website. Only that user's refresh grant is retained,
  encrypted with AWS KMS using a user-specific encryption context in DynamoDB.
- Access tokens, the shared Google client secret, full email bodies, and uploaded `.eml` files
  are not persistently stored by the application.
- Returning users use identity-only Google sign-in; the restricted Gmail scope is requested
  when connecting or reconnecting Gmail, not on every session renewal.

The earlier `client_secret.json` and `gmail_oauth_secret.json` workflow belongs only to the
legacy single-mailbox CLI. Never place either file in a public image, browser bundle, source
repository, Lambda environment variable, or downloadable web asset.

The web application is multi-user and does not restrict connections to the legacy mailbox.
Any Google account may connect its own mailbox after its owner completes Google OAuth consent.
Do not access a client's mailbox without that client's explicit authorization; do not ask for
their Google password or OAuth token.

The acquisition workflow can verify wholesale property addresses through the Miami-Dade
Property Appraiser public search service. Its initial qualification rule is intentionally
strict: the portal record must have a folio beginning with `30`, be a qualifying residential
record, not be in an excluded municipality, have an email asking price at or below the
configured target, and show double-lot/multiple-lot support when that requirement is enabled.
Verification results are shown in the run report; an incomplete portal lookup never qualifies
or writes the `<primary>/Miami-Dade/Important` label.

When `PROPERTY_LOOKUP_ENABLED=true`, the worker searches the Miami-Dade Property Appraiser
public service for each extracted property address in a wholesale email. It checks the folio
prefix, excluded municipality, land use, email asking price, and full legal description. The
`<primary>/Miami-Dade/Important` label is created only when the website record is verified
and the configured acquisition criteria pass; failed lookups remain unverified and are never
treated as important targets.

The acquisition values are deployment configuration, not code constants. Change them through
environment variables or SAM parameters:

- `ACQUISITION_MIAMI_DADE_ZIPS`
- `ACQUISITION_MIAMI_DADE_LABEL_SUFFIX`
- `ACQUISITION_IMPORTANT_LABEL_SUFFIX`
- `ACQUISITION_PRICE_TARGET`
- `ACQUISITION_REQUIRE_DOUBLE_LOT`
- `ACQUISITION_EXCLUDED_MUNICIPALITIES`
- `ACQUISITION_QUALIFYING_LAND_USE_TERMS`
- `PROPERTY_LOOKUP_TIMEOUT_SECONDS`
- `CANDIDATE_SCAN_WINDOW`

## Web quick start

For a local UI smoke test, copy `.env.web.example` to `.env.web`, provide a local Google Web
OAuth client and development-only encryption key, then run:

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
email-classifier-web
```

Open `http://localhost:8000`. The exact local redirect URI is
`http://localhost:8000/oauth/google/callback`.

For AWS, Google production verification, the custom domain, secrets, deployment parameters,
rollback, retention, and launch gates, use the complete
[web deployment runbook](docs/WEB_DEPLOYMENT.md). The SAM stack provisions the custom-domain
API, DynamoDB, KMS, FIFO queues, worker, scheduler, bounded quotas, TTLs, logs, and alarms.

For a temporary free-tier demonstration, use the Render blueprint in [`render.yaml`](render.yaml)
and follow [`docs/MVP_DEPLOYMENT.md`](docs/MVP_DEPLOYMENT.md). This profile is intentionally
non-durable: the free Render instance can sleep or restart, clearing local users, OAuth grants,
policies, and history. It is suitable for an MVP demonstration only, not production data.

## Current launch status

The code and deployment template are ready for a controlled staging deployment. It is not
automatically a verified public Google OAuth app: the operator must supply a company-owned
domain, ACM certificate, Route 53 zone, real legal/support contacts, AWS/OpenAI secrets, pass
`sam validate --lint` and a disposable-stack test, publish the Google External consent screen,
complete restricted-scope verification/security assessment, and obtain company legal approval
for the Privacy Policy and Terms. The current scheduler/template is sized as a controlled pilot;
capacity and provider quotas must be raised and load-tested before an unrestricted launch.

---

# Legacy single-mailbox Wholesale agent

**Legacy policy version:** `2026-08-20-v2`

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

- **Wholesale:** add `Wholesale` and move the message out of `INBOX`.
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
10. Add destination labels and remove `INBOX` only for messages that receive a destination label.

## False-positive controls

The agent distinguishes property pitches from:

- internal implementation, meeting, rule, prompt, or training correspondence;
- news, editorial articles, market reports, and commentary;
- title, legal, closing, mortgage, insurance, appraisal, inspection, permitting, invoice, receipt, and vendor messages;
- recruiting messages mentioning acquisitions or wholesaling;
- ordinary client correspondence that merely references a property.

Current-message content is the unit of action. Only earlier messages from the same thread are provided as context. Attached `.eml` training examples are not merged into the current body. Email content is treated as untrusted data, and the semantic prompt explicitly rejects instructions embedded in an email.

## Message preservation and safety

The Gmail wrapper can add labels and remove `INBOX` from messages that received a destination label. It has no trash, delete, send, forward, or destination-label removal method. A move-to-label write uses:

```json
{
  "addLabelIds": ["..."],
  "removeLabelIds": ["INBOX"]
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
