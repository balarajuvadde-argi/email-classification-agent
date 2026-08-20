# Supplied Email Sample Verification

Generated: 2026-08-20T12:42:18.288658+00:00

Policy: `2026-08-20-v2`

Result: **27/27 scenarios passed**

Each sample is tested three ways: exact file, unrelated sender with a blank subject, and the same content from an approved Redfin subdomain.

| # | Supplied sample | Exact | Generalized | Approved-domain override |
|---:|---|---|---|---|
| 1 | Monday Deal Alert! Check Out These 9 Hot Deals!🎯📢.eml | WHOLESALE | WHOLESALE | KEEP_IN_INBOX |
| 2 | Tuesday Finale 🎬 13 Amazing Deals To Close Out The Night!🌙.eml | WHOLESALE | WHOLESALE | KEEP_IN_INBOX |
| 3 | 📍 East of I-95: Affordable Miami Lot Near Golden Glades 💰.eml | WHOLESALE | WHOLESALE | KEEP_IN_INBOX |
| 4 | 🔥 New 3/3 Townhouse Deal \| Only $279,999.eml | WHOLESALE | WHOLESALE | KEEP_IN_INBOX |
| 5 | 🔥Monday’s Looking Good! 11 Incredible Deals Just Landed! 💰.eml | WHOLESALE | WHOLESALE | KEEP_IN_INBOX |
| 6 | 🚨 $374K Duplex + $279K 3/3 Townhouse.eml | WHOLESALE | WHOLESALE | KEEP_IN_INBOX |
| 7 | 🚨 NEW Hollywood Duplex \| $374K Buy → $550K ARV.eml | WHOLESALE | WHOLESALE | KEEP_IN_INBOX |
| 8 | 🚨 NEW Miami Deal \| $539K Buy → $850K ARV.eml | WHOLESALE | WHOLESALE | KEEP_IN_INBOX |
| 9 | 🚨 New Miami-Dade Wholesale Deals – Priced to Move FAST.eml | WHOLESALE | WHOLESALE | KEEP_IN_INBOX |

## Interpretation

The reference messages are recognized from property-offer evidence rather than sender, subject, filename, or delivery-vendor shortcuts. The approved-domain bypass still takes precedence over identical content.
