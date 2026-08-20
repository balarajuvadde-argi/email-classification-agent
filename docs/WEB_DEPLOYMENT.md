# Universal Web Application Deployment Runbook

This runbook deploys the multi-user website in `template-web.yaml`. It does not use the
single-mailbox `template.yaml`, Desktop OAuth client, `EXPECTED_GMAIL_ADDRESS`, or a shared
`gmail_oauth_secret.json`.

## Credential decision

| Credential or data | Production location | Scope |
|---|---|---|
| Google Web OAuth client JSON | One AWS Secrets Manager secret per environment | Shared backend configuration; never sent to a browser |
| OpenAI project API key | Separate AWS Secrets Manager secret | Shared backend key with project budget/usage controls |
| User Google refresh grant | KMS-encrypted DynamoDB profile item | One grant per Google `sub`; encryption context includes the user ID |
| Google access token | Memory only | Refreshed as needed; never persisted |
| User prompt, labels, query, threshold | DynamoDB profile item | Tenant-owned configuration |
| Run decision summary | DynamoDB with TTL | Bounded subject/sender/reason/evidence; no intentional full-body storage |
| `.eml` upload | Request memory or transient Lambda upload spool | Not persistently stored by this application; bounded content is sent to OpenAI |

There is no production reason to externalize `gmail_oauth_secret.json`. That file represents
one person's authorized-user grant and is appropriate only for the legacy CLI. In the website,
each person clicks **Connect Gmail** and the backend stores only their encrypted refresh grant.

## Architecture

1. A FastAPI/Mangum web Lambda serves the UI, starts Google OAuth, stores policies, and queues
   preview/apply jobs.
2. Google OAuth uses Authorization Code flow, PKCE, server-side single-use state, an OIDC nonce,
   and a browser-bound HttpOnly nonce cookie. A full connection requests `openid`, `email`, and
   `https://www.googleapis.com/auth/gmail.modify`; returning sign-in requests identity scopes
   only.
3. A FIFO SQS queue serializes work per Google user ID. Workers claim a fenced lease, recheck
   the current connection/policy/consent before every Gmail or model operation, and retry
   transient top-level failures.
4. A DynamoDB GSI exposes only user ID, policy hash, consent version, connection version, and
   next-run time to the dispatcher. It does not project prompts, mailbox addresses, or grants.
5. Gmail mutations can only create/add allow-listed user labels. Every message modify request
   uses `removeLabelIds: []`.

## Prerequisites

- A company-owned domain verified with Google and hosted in Route 53.
- An ACM public certificate for the application hostname in the deployment Region.
- AWS CLI and AWS SAM CLI authenticated to the intended account/Region.
- Docker Desktop or another container runtime for reproducible Linux Lambda builds on Windows.
- A dedicated Google Cloud project for each of development, staging, and production.
- A dedicated OpenAI project with a restricted project API key, usage limit, and budget alert.
- A monitored SNS topic for CloudWatch alarms.
- Real operator, privacy, and support contact details plus company-reviewed Privacy/Terms text.

## 1. Configure Google Cloud

1. Create or select the production Google Cloud project and enable the Gmail API.
2. Configure Google Auth Platform branding with the real application name, home page, Privacy
   Policy, Terms, support email, and company-owned authorized domain.
3. Choose **External** audience for a service intended for accounts outside one Workspace
   organization. Testing mode is only for named test users; do not mistake it for a public
   launch.
4. Create an OAuth client of type **Web application**.
5. Add the exact authorized JavaScript origin:

   ```text
   https://inbox.example.com
   ```

6. Add the exact redirect URI:

   ```text
   https://inbox.example.com/oauth/google/callback
   ```

7. Download the Web client JSON to a temporary secure workstation location. Confirm its top
   level contains `"web"`, not `"installed"`.

For local development, create a separate Web client with
`http://localhost:8000/oauth/google/callback`. Never reuse the production client secret in a
developer checkout.

### Why the earlier `403 org_internal` happened

An OAuth app configured as **Internal** accepts only members of its Google Workspace
organization. An account such as `cto@argifamily.com` cannot authorize an Internal app owned by
another organization. Use an External app for cross-organization users, or intentionally keep
the service organization-only and have the Workspace administrator allow it. Adding a different
email address in code cannot bypass Google's audience policy.

### Google public-launch gate

`gmail.modify` is a Restricted Gmail scope. Because this service reads/transmits Gmail data on a
server, production publication requires Google's restricted-scope verification and security
assessment process. A Workspace administrator can still block the app. Do not advertise a public
launch until Google shows the production project as published/verified and all assessment
requirements are satisfied.

## 2. Create backend secrets

Create the Google secret from the downloaded Web client JSON without placing its content on the
command line:

```powershell
aws secretsmanager create-secret `
  --name inbox-pilot/production/google-web-oauth `
  --secret-string file://C:/secure/web-client.production.json
```

Create a separate temporary JSON file for the OpenAI project key with this shape:

```json
{"api_key":"YOUR_PROJECT_KEY"}
```

Then create the secret:

```powershell
aws secretsmanager create-secret `
  --name inbox-pilot/production/openai `
  --secret-string file://C:/secure/openai.production.json
```

Securely delete the temporary plaintext files according to company policy after verifying the
secrets. Record both secret ARNs, not their values.

The included IAM policy assumes these two Secrets Manager secrets use the AWS-managed
`aws/secretsmanager` KMS key. If the secrets use customer-managed KMS keys, add narrowly scoped
`kms:Decrypt` permissions for those exact key ARNs with a `kms:ViaService` condition for
Secrets Manager in the deployment Region. Do not grant a wildcard KMS key.

## 3. Validate before deployment

From PowerShell in the repository:

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python -m pytest -q
ruff check src tests scripts
python -m compileall -q src scripts tests
python scripts/safety_audit.py
sam validate --lint --template-file template-web.yaml
sam build --use-container --template-file template-web.yaml
```

`sam build --use-container` is important on Windows because Lambda runs Linux and dependencies
such as `cryptography` include platform-specific wheels. CI should install from the reviewed
deployment lock, generate an SBOM, and run dependency/license/vulnerability scanning.

## 4. Deploy the custom-domain stack

Run:

```powershell
sam deploy --guided
```

Supply:

- `CustomDomainName`: for example `inbox.example.com`
- `CertificateArn`: ACM certificate ARN for that hostname in this Region
- `HostedZoneId`: Route 53 public hosted-zone ID
- `GoogleOAuthClientSecretArn`: Google Web client secret ARN
- `OpenAIApiKeySecretArn`: OpenAI secret ARN
- `OpenAIModel`: reviewed model name
- `ServiceName`, `OperatorName`, `PrivacyContactEmail`, `SupportEmail`
- `PrivacyEffectiveDate`: `YYYY-MM-DD`
- `PrivacyNoticeVersion`: increment for every material disclosure/provider/retention change
- `BackupRecoveryDays`: 1–35; default 7
- `AlarmNotificationTopicArn`: monitored SNS topic ARN
- `ScheduleExpression`: dispatcher cadence, default five minutes
- `RunRetentionSeconds`: default 24 hours

The stack maps the HTTP API to the Route 53 hostname, requires TLS 1.2, and disables the default
`execute-api` endpoint. The user-facing URL and OAuth cookie/callback origin therefore cannot
diverge.

After deployment, compare the stack's `OAuthCallbackUrl` output byte-for-byte with the Google
Web client redirect URI.

## 5. Staging acceptance

Use a separate staging hostname/project/secrets and verify:

1. A new user can accept the notice and connect the intended Gmail account.
2. A different Google account receives its own tenant record and cannot see the first user's
   runs, plans, prompt, or labels.
3. A prompt and label allow-list save correctly; Gmail system labels are rejected.
4. `.eml` preview produces no Gmail write.
5. Gmail preview produces no label creation or message modification.
6. Reviewed apply adds only the expected destination and hidden processed labels while retaining
   `INBOX`.
7. Automatic mode cannot be enabled until a successful, non-empty preview of the exact policy
   and batch size.
8. Disabling automation before a queued job writes prevents Gmail mutation.
9. Disconnect invalidates the current connection/session generation before token revocation;
   old sessions, plans, runs, and delayed callbacks cannot act on a reconnected account.
10. A privacy-notice version change pauses automation and presents the re-acceptance UI.
11. SQS retry, terminal-failure, dispatcher, queue-age, and DLQ alarms reach the monitored topic.

Inspect CloudWatch logs to ensure message bodies, refresh tokens, client secrets, and OpenAI keys
are absent. Access logs contain route/status metadata only.

## 6. Production rollout

Start as a controlled pilot:

1. Keep scheduled classification off for each user.
2. Have users save a narrow Gmail query and run a preview.
3. Review every decision and apply the one-time plan.
4. Enable scheduled mode only after the exact-policy preview gate is satisfied.
5. Monitor false positives, OpenAI usage, Gmail quota, queue age, worker duration, and alarms.

Automatic below-threshold decisions receive no destination label but do receive a hidden
policy-specific processed marker. This prevents the newest ambiguous messages from consuming
model calls forever and starving older mail. Users should inspect preview results before enabling
automation.

The default scheduler query handles 100 due tenants per dispatcher invocation and the worker has
maximum concurrency 10. This is a multi-user pilot configuration, not an unlimited public-scale
claim. Before open registration, load-test and capacity-plan Gmail/OpenAI quotas, shard/paginate
the scheduling index, move static assets to an edge/CDN path, add WAF/edge and per-account abuse
controls, and add a global spend circuit breaker.

## Retention and deletion

- OAuth state: 10 minutes by default.
- Reviewed preview plan: 15 minutes before Apply; queued Apply receives run-lifetime TTL.
- Browser session: 24 hours.
- Run summaries: 24 hours by default.
- Daily quota counters: bounded TTL; no email content.
- DynamoDB point-in-time recovery: configured by `BackupRecoveryDays`, default 7.
- OpenAI: requests set `store=false`; unless the OpenAI project is approved/configured for
  Modified Abuse Monitoring or Zero Data Retention, abuse-monitoring logs may contain inputs and
  outputs for up to 30 days. Confirm the actual OpenAI project controls before publishing the
  Privacy Policy.

Disconnect deletes the live credential/profile first, making operation guards fail, then attempts
Google revocation and version-conditional cleanup. Short-lived records missed by an eventually
consistent owner index are inaccessible to a new connection generation and expire by TTL.
Disaster-recovery restores must not serve traffic until the operator re-applies previously
completed deletion requests from the approved external privacy/deletion audit process. Establish
and test that process before production; the application table is not itself the deletion ledger.

The table and KMS key use CloudFormation `Retain` policies to prevent accidental cryptographic
data loss during a stack operation. Decommissioning therefore requires an operator-approved
runbook: disable the API/scheduler, revoke or expire grants, export required deletion audit data,
delete retained table data/backups, then schedule KMS-key deletion only after the retention and
legal requirements are satisfied.

## Rotation

- **OpenAI key:** create a replacement project key, update the secret value, cold-start/test the
  functions, then revoke the old key.
- **Google client secret on the same client ID:** update the Secrets Manager value and test a
  refresh/reconnect before invalidating the old secret.
- **New Google client ID:** existing refresh grants may no longer be usable; plan a user
  reconnect campaign.
- **KMS key:** use controlled re-encryption/migration. Do not replace or delete the retained key
  while encrypted grants or PITR backups still depend on it.

## Rollback and incident controls

- Disable the EventBridge Scheduler schedule to stop new automatic jobs.
- Disable the API custom-domain mapping or apply WAF blocking for an incident.
- Set reserved worker concurrency to zero only as a deliberate queue pause; monitor queue age.
- Revoke the OpenAI key to stop model processing immediately.
- Revoke the Google OAuth client or individual grants when Gmail access must stop.
- Preserve the FIFO queue/DLQ and logs for the approved investigation window; do not log email
  bodies to troubleshoot.

Application rollback does not remove labels already added. The product has no label-removal
operation; users can remove unwanted labels directly in Gmail.

## Required public-release approvals

The stack is not a substitute for organizational approval. Before public use, require:

- Google External Published/Verified status and restricted-scope security assessment;
- company-owned verified domain and exact production redirect URI;
- company legal approval of Privacy Policy, Terms, retention, deletion, subprocessors, and
  Google API Limited Use wording;
- confirmed OpenAI project retention controls and a truthful disclosure;
- production SAM validation/build and a disposable-stack integration test;
- reproducible dependency lock/SBOM/scanning;
- threat model, incident response, an external deletion-request ledger plus backup-restore
  deletion replay, key rotation, and
  decommission exercises;
- load, quota, cost, and abuse testing sized for the intended user population.
