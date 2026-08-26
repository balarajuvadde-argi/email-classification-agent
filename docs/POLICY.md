# Wholesale Inbox Classification Policy

**Policy version:** `2026-08-20-v2`

## Objective

Keep approved listing-platform mail in the main inbox. Add the visible `Wholesale` label to every non-approved inbound email that is an actual property-sales opportunity or directly continues a property-sales conversation.

This release has two destinations only:

- `Wholesale` and moved out of `INBOX`
- Main inbox only

It does not create or move mail into `On Market`, `Off Market`, or `News`, because the latest written instruction says approved platform messages must remain in the main inbox and defines only the `Wholesale` routing action.

## Decision order

### 1. Approved sender-domain bypass

Derive the origin domain from the current message's `From` header, falling back to `Sender` only when `From` has no usable address. Normalize case, a trailing dot, and internationalized domain names.

The sender is approved only when the domain equals an approved root or ends with `.` plus that root. This accepts true subdomains and rejects suffix/lookalike domains.

Approved roots:

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

Examples:

- Approved: `zillow.com`, `mail.zillow.com`, `convo.zillow.com`
- Not approved: `fakezillow.com`, `zillow.com.attacker.test`, `zillow-example.com`

The `Reply-To` domain, links, logos, display name, quoted headers, and brand names in the body do not create an approved-domain bypass.

### 2. Wholesale decision for non-approved senders

Classify the current email as `WHOLESALE` when it does any of the following:

- Offers, lists, advertises, or pitches a house, multifamily property, commercial property, parcel, lot, or vacant land.
- Sends a deal alert, buyer-list blast, inventory list, dispositions list, or recurring property opportunity.
- Advertises an off-market property, fix-and-flip, buy-and-hold, value-add, rental, or development opportunity.
- Offers an assignment, assignable contract, equitable interest, contract-controlled property, or JV opportunity.
- Cold-emails a specific property or asks the recipient to request access, schedule a showing, call for details, or submit an offer.
- Sends a conventional brokerage listing from a sender outside the approved root-domain list.
- Continues an earlier property-pitch thread with availability, price, access, diligence, showing, contract, assignment, or offer information.

Strong evidence includes:

- property address, masked address, cross-street, parcel/APN/folio, city/state/ZIP;
- asking price, shorthand price such as `$425K`, ARV, comps, escrow, assignment fee;
- beds/baths, square footage, lot size, acreage, property type, zoning, occupancy, condition;
- `for sale`, `new listing`, `off-market`, `wholesale`, `disposition`, `deal alert`, `inventory`, `cash buyers`;
- calls to request information, obtain access, schedule a showing, or submit an offer;
- assignment, equitable-interest, contract-control, or JV language tied to a concrete property.

### 3. Keep in main inbox

Keep the current email in the main inbox when it is not an actual property pitch, including:

- internal discussion about the agent, labels, folders, rules, prompts, approved domains, implementation, or testing;
- meeting notes and emails carrying reference/training `.eml` attachments;
- real-estate news, editorial content, market reports, or articles that discuss a property but are not trying to sell it to the recipient;
- financing, title, insurance, legal, permitting, closing, appraisal, inspection, accounting, property-management, invoice, receipt, or vendor correspondence without an actual property offer;
- recruiting or employment messages that mention acquisitions or wholesaling but do not offer a property;
- ordinary client correspondence that merely mentions real estate;
- ambiguous messages without enough evidence to act safely.

## Current-message and thread rule

The current inbound email is the unit of action. Earlier messages in the same Gmail thread are evidence only.

- A brief reply such as “Yes, it is still available” can be `Wholesale` when an earlier message clearly pitched a property.
- A later message is never included as prior context.
- An unrelated current message does not inherit the category of an old thread.
- Quoted or attached reference emails do not automatically turn an internal setup email into a property offer.

## Deterministic and semantic stages

1. Approved-domain bypass.
2. Conservative deterministic detection for unmistakable offers.
3. High-confidence internal setup/training keep decision.
4. Structured semantic classification for borderline messages.
5. `Wholesale` model decisions must meet the configured confidence threshold, default `0.85`.
6. Below-threshold or unavailable semantic decisions remain in the inbox and are left eligible for retry by default.

Email and thread text are untrusted input. The semantic instructions explicitly prohibit following prompt-like instructions contained inside an email.

## Message parsing

- Prefer the plain-text MIME body; fall back to sanitized HTML text.
- Ignore scripts, styles, hidden head content, binary attachments, and attached `.eml` reference messages.
- Record attachment filenames for semantic context without treating filenames alone as decisive evidence.
- When Gmail returns a large unnamed `text/plain` or `text/html` body via `attachmentId`, retrieve that text body before classification.
- Never fetch binary/file attachments as part of the body-hydration step.

## Gmail action

For `WHOLESALE`:

- add `Wholesale`;
- add the hidden policy-version marker `EmailAgent/Processed/v2` after successful handling;
- remove `INBOX` so Gmail shows the message under `Wholesale` instead of the Inbox.

For `KEEP_IN_INBOX`:

- do not add `Wholesale`;
- add the hidden processed marker only when the decision is complete and safe to mark processed.

The Gmail wrapper has no delete, trash, send, forward, or destination-label removal operation. Its message mutation can add labels and remove only `INBOX` from messages that received a destination label.
