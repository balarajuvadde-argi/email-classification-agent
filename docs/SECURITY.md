# Legacy Single-Mailbox Security and Privacy Controls

This document describes the fixed Wholesale CLI. The multi-user web security model and
production controls are documented in [`WEB_DEPLOYMENT.md`](WEB_DEPLOYMENT.md).

## Gmail authorization

The agent uses the Gmail `gmail.modify` OAuth scope because Gmail requires it to read messages and create or apply labels. That OAuth grant is broader than the application's intended behavior, so the code narrows its effective capability:

- `GmailClient` exposes a single message write: `add_labels`.
- Message modifications can remove `INBOX` only when a destination label is applied.
- The wrapper has no trash, delete, send, forward, or destination-label removal method.
- Tests assert the exact Gmail modification body and assert that destructive method names are absent.
- `EXPECTED_GMAIL_ADDRESS` is mandatory in production and stops execution when OAuth is connected to the wrong mailbox.
- `DRY_RUN=true` is the default.

## Data minimization

- Only messages in the current inbox are considered by default.
- Spam and Trash are excluded.
- Binary/file attachments and attached `.eml` reference messages are not downloaded or parsed by the body hydrator.
- When Gmail stores a large unnamed `text/plain` or `text/html` MIME body behind an `attachmentId`, that text body is retrieved so classification does not operate on an empty message.
- Attachment filenames may be provided as weak semantic context, but filenames alone never determine the label.
- Body and prior-thread context are length-limited.
- The classifier does not log email bodies.
- Structured model requests use `store=False`.
- The semantic policy treats email, quoted thread content, and attachment filenames as untrusted data and rejects prompt-like instructions embedded in messages.

## Secrets

For AWS deployment, store the Gmail OAuth authorized-user JSON and model API key in AWS Secrets Manager. The Lambda execution role is granted `secretsmanager:GetSecretValue` only for the two supplied secret ARNs.

Do not commit `.env`, Google client secrets, refresh-token files, or API keys. The repository `.gitignore` excludes the expected local secret files.

## Sender-domain boundary

Approval is based on an exact root-domain boundary. A sender domain is approved only when it equals the root or is a true subdomain. This rejects strings such as:

- `zillow.com.attacker.test`
- `fakezillow.com`
- `redfin.com.example.org`

The policy does not trust a brand name or link in the message body as proof of approved origin.

## Deployment isolation

The SAM `CodeUri` is `src/`, so test fixtures and supplied sample emails are not included in the Lambda deployment artifact.

## Operational recommendations

- Start with a dry-run backfill and review every proposed action.
- Keep CloudWatch log retention limited to an operationally necessary period.
- Rotate the model key and revoke the Gmail OAuth grant when decommissioning.
- Review the `Wholesale` folder during the first several days and adjust the policy threshold based on false positives or false negatives.

## Policy-version isolation

The hidden processed marker is `EmailAgent/Processed/v2`. A policy revision uses a new marker so older decisions can be re-evaluated without removing or modifying the previous hidden label.
