# Native request phase timing

Tier 2: this is a numeric observer on the existing patient/session capture path.
It changes no authorization, consent, encryption, schema, endpoint, SQL, timeout,
credential lifetime, grant lifetime or recording behavior.

The immediate Record target is not established. Actual baseline and restored
image trials have substantial admission/preparation and source-decision time;
the existing inclusive `downstream` metric cannot separate Azure SDK token work
from HTTP or transaction work. A saved staging console ledger proves numeric
emission exists, but has no timestamp/trial association. Earlier fixed KQL
queries returned zero matching rows; the SQL discriminator remains unconfirmed.

## Source and deployed reconciliation

This branch starts at reviewed Core `548d6d29f9f06ae675a6ef9b2a22483dc5625203`,
tree `1f8934f84abb23a685edab6b444dcaa85137d4c4`, identical to merged PR766
`fd3f007b4742ead03933afa4984bd169d26d9fd7`. The saved merged-source proof is
`/private/tmp/core-scribe-pool-release-20261017/merged-source-proof.json`, SHA256
`c84809335c968aaf1f282665a239c3ef65c4627b3b13db6df2086936a01e7038`.

Root subsequently restored the web workload to image digest `602239c2deadc78f887b151b404d15deaed8e9ca3c20be030489a16730ba97fc`,
source `6881aacdec032a220ee6ea69f275eca33c3bb424`. The restored image also had a
slow immediate-start trial; neither a transport-pool defect nor a performance
improvement is established. Any founder-owned release must record current
workload/image/configuration and explicitly reconcile that drift. Merging this
observer does not silently re-enable the pool or authorize an image activation.

## Closed observations

The existing exact `PYTHON_ENV=staging` and
`SCRIBE_NATIVE_REQUEST_TIMING=true` gate controls all observations. Each native
POST owns a fresh numeric ledger. There is no process-wide event allowance, so
home/catalog activity cannot exhaust another request's timing budget. Native
helper/runtime log budgets are separate and unchanged.

The added phases are:

- `workflow_token`: every existing, fresh per-call SDK `get_token` operation.
- `workflow_http`: the existing HTTP stream through response collection and its
  actual owned close. It excludes token acquisition, and includes transport
  queue/socket/server/body/close time without claiming to separate those parts.
- `capture_transaction`: the existing admission database context, including
  session acquisition, authority checks, SQL, commit/rollback and context exit.
  It is not a measure of isolated SQL wait.

Phase totals overlap. `workflow_token` and `workflow_http` are children of
existing downstream/consent/session observations. Transaction time overlaps
operation/policy/SQL observations and can contain domain work. SQL cursor
timings continue to exclude pool acquisition, pre-ping, raw deadline SETs and
commit. Do not add these columns together or attribute an unmeasured residual.

`x-cosmaneura-native-request-timing` is an optional, at-most-4096-byte ASCII JSON
header on the same gated native response. It contains exactly the existing nine
ledger fields, fixed phase names, finite bounded milliseconds and integer
counts, a closed request kind, and the fixed event name. It contains no patient,
actor, tenant, identifier, URL, token, SQL, exception value or request metadata.
It adds no CORS exposure, body/status/model/operation-ID or client contract.

The header copies completed observations at `http.response.start`; it does not
close the ledger. The existing stdout row still closes after the full request.
An in-flight or canceled phase may not yet appear in the header. A zero count
does not establish that work was never attempted. Failed serialization or an
existing same-name header leaves the original response unchanged. The caller
can associate the header with that exact response without exporting identities.

The deadline worker still clears inherited context and binds only the original
numeric ledger. Observation does not cross live response/socket/session or
credential objects between loops, alter cancellation, or publish abandoned
worker state. Every original credential call, fresh claim and worker/session
ownership rule remains authoritative.

## Validation and recovery

Use the isolated authority-kernel fixture and existing dependencies only.
Focused cases exercise token failure, HTTP failure, response-close joining,
cancellation/deadline ordering, concurrent request isolation, inclusive
transaction acquisition/commit/rollback, disabled gates and malformed/bounded
header serialization. Existing native receiver, capture client, deadline,
preparation and transport regressions must pass; PostgreSQL-only skips remain
explicit and are not claimed as live database validation.

Independent review is required after the last substantive push. Root owns the
reviewed merged-source release, image qualification, staged reconciliation and
passive exact-response measurement. There are no new dependencies, migrations,
grants, configuration changes or agent-issued cloud actions. Recovery is the
recorded prior immutable image/template, with schema left unchanged; disabling
the existing gate only suppresses observations and is not an image rollback.

No saved test or source review establishes real network speed or the under-five
second physical target. The next trial must retain the actual tap clock bounds,
fresh native journal evidence and the exact gated response timing, with
hierarchical fields reported without double counting.
