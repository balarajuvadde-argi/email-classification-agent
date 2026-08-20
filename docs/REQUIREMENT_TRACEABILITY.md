# Requirement Traceability

**Policy version:** `2026-08-20-v2`

## Source precedence

1. **Latest written inbox instruction** is controlling: approved platform roots stay in the main inbox; every other actual real-estate property pitch goes to `Wholesale`; nothing is deleted or archived.
2. **Meeting notes** provide business context and terminology. Earlier brainstorming about `Acquisitions/On Market/Off Market/News` does not override the later, explicit instruction to leave approved-platform mail in the main inbox and create only the `Wholesale` routing rule in this release.
3. **Nine supplied `.eml` files** are positive reference examples. They are regression fixtures, not a sender/domain blacklist and not templates that the classifier must exactly match.

## Decision matrix

| Current inbound email | Sender domain | Result |
|---|---|---|
| Property offer, listing, deal alert, off-market opportunity, disposition/wholesale blast, assignment/JV pitch, land offer, or property-sales follow-up | Approved root or true subdomain | Keep in main inbox |
| Same property-sales content | Any non-approved domain | Add `Wholesale`; preserve `INBOX` |
| Internal rule/setup/training/meeting correspondence | Any domain | Keep in main inbox |
| News/editorial/market commentary without an actionable property pitch | Any domain | Keep in main inbox |
| Title, legal, closing, lending, insurance, inspection, permitting, invoice, receipt, or vendor message without an actual property pitch | Any domain | Keep in main inbox |
| Ambiguous message | Non-approved domain | Keep in main inbox; use semantic review or leave eligible for retry |

## Requirement-to-control map

| Requirement | Implementation | Verification |
|---|---|---|
| Match approved platforms by root domain, including subdomains | Boundary-safe `domain == root` or `domain.endswith('.' + root)` | All 16 roots, subdomains, case/trailing-dot, and lookalike tests |
| Do not approve lookalikes | Exact DNS-label boundary check | `fakezillow.com` and `zillow.com.attacker.test` rejected |
| Detect property pitches by meaning, not sender shortcut | Deterministic evidence engine plus structured semantic fallback | Reference generalization replaces original sender and subject; all nine still classify `WHOLESALE` |
| Approved platform always stays in inbox | Approved-domain bypass runs before content classification | Same nine reference bodies rewritten to `mail.redfin.com`; all stay in inbox |
| Catch simple and complex property offers | Address/parcel, price, asking/ARV, property details, sale/off-market/wholesale, CTA, contract evidence | Synthetic listing, owner outreach, land, assignment, disposition, brokerage, masked-address, subject-only, and offering-memorandum tests |
| Avoid internal setup false positives | Meta/configuration guard and attachment isolation | Internal setup, meeting/training text, and attached `.eml` tests |
| Avoid news/transaction/vendor false positives | Negative-context guard plus semantic fallback | News article, closing package, inspection invoice, recruiting, and ordinary inquiry tests |
| Read large Gmail bodies | Hydrate unnamed `text/plain`/`text/html` parts returned through Gmail `attachmentId` | Large-body hydration tests; binary and `.eml` attachments are not fetched |
| Use prior conversation safely | Only earlier messages in the same thread are passed as context | Future-message exclusion, same-timestamp ordering, and brief-follow-up context test |
| Do not archive/delete | Only add labels; `removeLabelIds` is always empty | Write-safety unit and static audits |
| Do not operate on the wrong mailbox | Exact authenticated-address gate before label access | Wrong-mailbox test |
| Process existing and new inbox mail | Backfill command plus scheduled batch | CLI and deployment-template tests |
| Treat email content as untrusted | Semantic prompt explicitly rejects prompt instructions in emails | Prompt-security test |

## Reference-sample policy

The implementation contains no special case for Jonathan Espinosa, HomeSellers, Patton Investment Properties, Quick Turn Properties, Constant Contact, Mailchimp, or any other reference sender. The nine files pass because their content contains actionable property-offer evidence and their origin domains are not on the approved list.
