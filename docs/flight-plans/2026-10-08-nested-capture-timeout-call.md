# One settings call for overlapping Scribe SQL budgets

Risk: Tier 2, capture/record cancellation boundary. The owner authorized fixing
all twelve desktop issues and releasing backend latency fixes. This small
refactor preserves access, consent, token, session, route, schema, data and
environment behavior; it combines only SQL timeout setup for one statement.

The approved backend was deployed at `c41672aaaa705bad77e979408b183c71cea89072`.
Two fresh installed build-59 visits still took 30.023 and 27.393 seconds in
backend preparation. Audio device discovery was about 0.1 seconds. Synthetic
real native routes still execute hundreds of SQL statements; encounter lookups
pay both capture and narrower record timeout setup on each one.

Both guards continue to calculate their current budget and check cancellation.
The first callback sets the minimum statement/lock timeout of both budgets in
one transaction-local parameterized call. The second callback independently
checks both budgets and consumes correspondence tied to this exact SQL execution
context, capture budget, record scope, connection and thread. No timeout,
permission decision or row is cached between statements. Callback ordering is
supported in both directions. Scope exit and nested record guards select the
original independent setter path. Existing flush/commit fences remain intact.

Validation uses only the explicitly owned synthetic PostgreSQL lab on loopback
55937, `core_ux_01_test`, `scribe_ux_lab_owner`, with its task ownership marker
and authority-only pytest bootstrap. Cover both callback orders, both cancelled
budgets, settings failure, wrong thread/connection/context/scope, nested scopes,
decreasing real settings, transaction-local reset, slow SQL cancellation, actual
lock waits, and exact native route DBAPI counts. All authorization statements
and other DBAPI calls must remain identical. Retain existing actual revocation,
scope, owner lock, original-query equivalence and deadline route tests.

Synthetic real native-route DBAPI calls are mount 2443→1936, creator review
1928→1524 and capabilities 2348→1944. Total 6719→5404 (19.57% fewer). Actual
authority statement counts are unchanged. These figures do not prove an
installed startup-time improvement. Test and implementer review the exact final
commit, record honest skipped fixtures and change-budget evidence in its PR,
and merge under the owner's existing local-validation policy.

For the owner-approved staging release, use the committed Actions-off immutable
source/controller and publisher. Select the exact merged PR candidate; qualify
the packaged image and preserve every role's existing configuration, secret
fingerprints and recovery revision. No migration is added. Current Core schema
must remain `56789df3c14c` and Scribe `0024`. Change only the three Core image
references; CAB activation, live payments and production remain unchanged.
Recovery restores retained prior images/revisions; no schema downgrade. Then
measure a fresh fictional installed-app preparation without physical capture.
All twelve remains open unless the startup outcome is acceptable.
