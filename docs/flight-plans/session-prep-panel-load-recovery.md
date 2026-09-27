# Pinned panel load recovery

Tier 2: the existing provider assistant bootstrap and authority lifecycle.
Authorized scope is a local host-only recovery fix, synthetic checks and a
separate independent review before merge. The vendor asset, integrity digest,
backend authorization, cohort and deployment configuration remain unchanged.

A script request that never completes leaves the shared panel-load promise
pending. The host's 30-second timeout offers Reconnect, but the next attempt
reuses that same stalled promise. Each host now subscribes with its existing
abort signal. Timeout or unmount releases only that host's subscription. When
the last consumer leaves, cancel the pending script, detach its handlers and
clear only its cache entry so an explicit retry starts a fresh pinned request.
Keep an in-flight load while another live host still needs it. Obsolete
callbacks cannot verify a global, resolve a new load or clear its state.

Successful loads retain the exact verified API/mount identity. Unknown or
replaced globals remain refused; removing a pending script does not authorize
an API it might register late. Only the existing explicit host action creates
a fresh authorized session, and old record/turn content never replays.

Tests execute the actual pinned panel after a simulated stalled script: timeout
then Reconnect, navigation to another appointment, old callbacks, overlapping
consumers and an unverified late global. Keep host, expiry/reconnect, terminal
401, completion and asset-integrity regressions passing, plus TypeScript and
scoped lint/format checks. Local tests substitute browser delivery and service
responses; live deployed browser recovery still requires acceptance.

Local validation passed 24 tests across six focused panel suites, full frontend
TypeScript, scoped ESLint/Prettier and diff whitespace checks. Raw output and
hashes are retained outside the source checkout for independent review.

Recovery restores the prior reviewed Core image while retaining schema and
records. No migration, data repair or separate Scribe/Sovereign service change
is required. Independent review must bind the final substantive commit.
