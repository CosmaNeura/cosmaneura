# Initial recording window, receiver first

Tier 2. A configured native recording preparation currently calls creator
review, key binding, lease issue and initial reservations separately. Replace
that sequence with Scribe's additive creator recording-window receiver.

The native journal and closed outer operation remain unchanged. Derive each
binding/lease/reservation key using the ordinary component namespace. Scribe
recovers prior individual commits and atomically commits the missing pieces.
Core checks current configured admission before dispatch and after synchronous
validation of every nested receipt. Preserve cancellation/deadline fences.
This preparation does not start a source or grant upload authority.

Deploy Scribe PR 293 before this Core consumer. For recovery, restore the
preceding Core image first; retain the additive Scribe route until the consumer
is recovered. No schema, dependencies, runtime configuration, identities,
permissions, cohorts or billing changes. Production is outside this release.

Validation uses owned synthetic PostgreSQL and virtual audio only. Record exact
source, receipts, current-access revocation, deadlines, replay behavior, real
SQL and lock order, HTTP route/body/headers and applicable adjacent checks.
Measure fresh installed preparations after deployment; local query reductions
are not a measured startup speedup. The 5–7 second goal remains open until
repeat installed measurements establish it.
