# Timed retained conversation display

Tier 2: an additive, opt-in patient-record display contract. This outcome has no
database migration, environment flag, identity, authorization or worker change.

## Result

`POST /service/v1/agent/conversation-turns/read-display-timed` accepts only
`schema_version: "1.3.0"` and the existing `mode` / `client_turn_id` selection.
It uses the existing verified workload principal, mount token, own-record scope,
request limits and current source/receipt checks. The response adds UTC
`observed_at` and a required `expires_at` field on each message. User rows have
null expiry. An accepted assistant row has the earliest live deadline of its
already verified user/final phases, provider/history receipts, exact retained
history members and inherited provider source. The read creates no receipt and
does not refresh any deadline.

The old closed 1.0, 1.1 and 1.2 requests and projections remain available without
these fields. A caller cannot supply an observation or expiry. Missing, expired
or invalid receipt evidence cannot make an assistant displayable through 1.3.
No deadline is calculated from `recorded_at + 24 hours`, a browser wall clock or
a newly requested history receipt.

Retained mapped answers preserve their original text, dates and excerpt map
after a source correction and remain marked `superseded`. The same answer stays
excluded from current inference/history. A legacy final with null citation
metadata still uses the unchanged current-source gate and never receives
fabricated excerpt links. Display metadata does not grant source access.

## Release sequence and acceptance

Publish and deploy the backward-compatible Core receiver before a provider panel
opts into 1.3. The separate provider SOURCE9 reader must use server remaining
time conservatively, accounting for request duration and monotonic elapsed time.
It must hide expired answers and re-read the exact turn before a fresh export;
this Core response alone does not erase an already-rendered browser answer.
Neither source nor staging publication is authorized by this private packet.

Prepared normal Core tests exercise real canonical route writes, persisted
receipts, direct and transitive deadlines, source correction, unchanged legacy
projections, ownership and the closed version boundary using existing synthetic
fixtures. They require the ordinary root conftest, migrations, seeds, plugins
and locked Python dependencies with newly owned local PostgreSQL/Redis. Existing
workload authentication and generation fixtures remain synthetic; these tests
do not prove live MSI, vendor, browser timing or paired deployment. Execute only
the reviewed exact source-bound local recipe. Record actual results separately
from this prepared source and retain any failure.

## Recovery

Stop the new provider opt-in and use the prior retained display path when that
client can satisfy its existing display rules. Recover the complete additive
source outcome through a separately reviewed source change, leaving the old
readers and all persisted data intact. No schema reversal, receipt backfill,
deadline renewal or stale-answer salvage is part of recovery.
