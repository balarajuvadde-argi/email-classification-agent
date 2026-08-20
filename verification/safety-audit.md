# Write and Policy Safety Audit

Generated: 2026-08-20T17:09:40.556446+00:00

Overall result: **PASS**

| Control | Result |
|---|---|
| no forbidden wrapper methods | PASS |
| no forbidden gmail calls | PASS |
| empty remove label list present | PASS |
| no nonempty remove label assignment | PASS |
| inbox not removed | PASS |
| deployment excludes test fixtures | PASS |
| web deployment excludes test fixtures | PASS |
| dry run default true | PASS |
| mailbox identity gate present | PASS |
| model storage disabled | PASS |
| universal model storage disabled | PASS |
| prompt injection boundary present | PASS |
| universal prompt injection boundary present | PASS |
| large text body hydration is text only | PASS |
| reference senders not hardcoded | PASS |
| versioned processed label | PASS |
| low confidence retry default | PASS |
| universal user label allowlist | PASS |
| universal add label only boundary | PASS |
| universal low confidence contract disclosed | PASS |
| per user grant excludes access and client secret | PASS |
| web kms context and least index projection | PASS |
| web custom domain only | PASS |
| web has no per user token file | PASS |
| web legal templates have no placeholders | PASS |

The Gmail wrapper's only message mutation is label addition. Its request body uses an empty `removeLabelIds` list, so the `INBOX` label is preserved. Large Gmail message bodies are hydrated only for unnamed text MIME parts; file attachments are not fetched by that path. The web path additionally enforces tenant-scoped encrypted grants, a server-side destination-label allow-list, and a custom-domain-only API.
