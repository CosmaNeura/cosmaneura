# Capture final-row refresh

Tier 2: current provider and clinical authority. No schema or external contract change.

The final capture authorization currently refreshes Doctor, PracticeMember and
Practice with three queries after the original Practice → Doctor, view-as,
token, context and provider-link locks. Join those three refreshes only when
private evidence identifies the exact doctor, practice, subject, Session and
still-active lock-taking transaction. Refresh every returned row and retain
the member lock at its existing stage. The practice join uses the already
locked owner ID, never a membership-selected replacement practice. Missing or
invalid evidence keeps the original three-query path and refusal order.

All current authority, membership, consent, deadline and post-wait checks stay
in place. Commit, rollback, savepoint transition, expired/detached rows and
scope exit cannot reuse the evidence. No decision or transaction crosses
downstream I/O. The seven durable admission transactions, journal-only receipt
acceptance and subsequent authorization/publication remain unchanged. Native,
Clerk, consent controls, recording windows and source permissions are untouched.

The corrected source checks passed 82 cases with 75 explicit PostgreSQL skips;
lint, formatting and the byte-identical runtime type checks passed. The fresh
owned PostgreSQL 16.9 gate then passed all 48 selected cases with zero failures,
skips or collection errors and confirmed resource cleanup. Six new cases cover
actual native receipt/query parity, transaction commit/rollback/savepoint
invalidation, and member-lock revocation versus rollback. Forty-two retained
cases cover current authority, foreign ownership, concurrent resolution, actual
lock waits and the real billing/membership/pilot writers. The retained packaged
union also passed 463 cases with 47 explicit PostgreSQL skips and confirmed
cleanup; those skips are not counted as PostgreSQL passes.

The inert gate copied only the two runtime files over the previously qualified
Core image. Its actual loaded module hashes match source freeze V2, manifest
`e9f3abc958036a03ddd4fbbcc88e195a7ac8caf1951bc94155c9851ef062f05b`.
The closed receipt is
`/private/tmp/scribe-capture-final-row-owned-pg-20261019/run/receipt.json`, SHA-256
`90b8ec0c87db78ba2a6b87d6b55372c9a175448a67bee9168ce2f998b63240e7`;
its PostgreSQL result SHA-256 is
`382cae9deb5fbd4d3bebde8c4cb5cfdf9551c7e413c81293ff1fb64d37f73f45`.
The candidate remains source-only and nonpromotable. This gate does not establish
full server lifespan, TLS, OpenAPI generation or physical latency. Independent
review after the final substantive push and exact merged-source immutable
qualification still precede a Root-owned rollout.

Recover by restoring the previously qualified Core image; no data reversal,
migration, native rebuild or auth policy change applies. The observed Mac 91 b
request has seven capture transaction observations totaling 470.45 ms and two
workflow HTTP observations totaling 295.55 ms, nested in a 1873.203 ms Core
operation. Individual refresh-query costs are unmeasured. Neither two fewer
queries nor those overlapping totals establish a physical saving or an
under-five result; new immediate recording trials are required.
