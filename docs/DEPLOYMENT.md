# Legacy Single-Mailbox Activation Runbook

This document applies only to `template.yaml` and the fixed Wholesale CLI. For the universal
multi-user website, do not create a shared Gmail authorized-user token file; use
[`WEB_DEPLOYMENT.md`](WEB_DEPLOYMENT.md).

## Preflight checklist

- Confirm the exact target Gmail address.
- Confirm the `Wholesale` label name.
- Confirm that labeled messages should be moved out of `INBOX` into their Gmail labels.
- Enable the Gmail API in the Google Cloud project.
- Create an OAuth desktop client and authorize the target mailbox.
- Create an OpenAI API key.
- Install AWS SAM CLI and authenticate to the intended AWS account and region.

## Step 1: Verify code and supplied samples

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
python -m pytest -q
python scripts/verify_samples.py
python scripts/safety_audit.py
```

Expected result: the complete suite passes, all nine supplied examples classify as `WHOLESALE`, sender/subject generalization passes, approved-domain and lookalike tests pass, Gmail large-text-body hydration passes, and the write-safety audit passes.

## Step 2: Authorize the target Gmail mailbox

```bash
python scripts/bootstrap_gmail_oauth.py \
  --client-secret /secure/path/client_secret.json \
  --expected-account maurice@sargigroup.com \
  --output gmail_oauth_secret.json
```

The command exits without writing credentials when the wrong mailbox is authenticated.

## Step 3: Run a local dry-run backfill

Create `.env` from `.env.example`, then run:

```bash
email-classifier backfill
```

Review the JSON report. In dry-run mode, no labels are created or applied.

## Step 4: Apply the local backfill

```bash
email-classifier backfill --live
```

This creates the visible `Wholesale` label and hidden `EmailAgent/Processed/v2` label when absent, then adds labels to messages. It never removes `INBOX`.

## Step 5: Create AWS secrets

Create a Gmail secret whose value is the complete contents of `gmail_oauth_secret.json`.

Create a model-key secret with this shape:

```json
{"api_key":"YOUR_KEY"}
```

Record both secret ARNs.

## Step 6: Deploy the scheduled Lambda in dry-run mode

```bash
sam build
sam deploy --guided
```

Use:

- `ExpectedGmailAddress`: exact target mailbox
- `GmailOAuthSecretArn`: Gmail secret ARN
- `OpenAIApiKeySecretArn`: model-key secret ARN
- `OpenAIModel`: `gpt-5-mini`
- `DryRun`: `true`
- `ScheduleExpression`: `rate(2 minutes)`

The function has reserved concurrency of one to prevent overlapping classifications.

## Step 7: Inspect dry-run logs

Check the Lambda CloudWatch log group for:

- The expected authenticated mailbox.
- Zero authentication or secret-loading errors.
- Proposed `WHOLESALE` decisions only for actual property pitches.
- Approved-platform messages reported as kept in the inbox.
- No failed messages.
- Policy version `2026-08-20-v2` in each run report.

No body content is intentionally logged.

## Step 8: Enable live labeling

Update the stack parameter `DryRun` to `false` and redeploy. The schedule becomes the ongoing new-mail processor.

## Step 9: Spot-check

During the first several days:

- Compare new approved-platform alerts against the main inbox.
- Review every new message under `Wholesale`.
- Record false positives and missed pitches.
- Adjust `MIN_LLM_WHOLESALE_CONFIDENCE` or policy examples only after reviewing evidence.

## Rollback

Set `DryRun=true` or disable the EventBridge schedule. Existing labels remain on messages, and no message has been archived or deleted. Remove labels manually only when desired.
