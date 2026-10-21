# Session Prep authority after a lock wait

Tier 2: authentication/session validity. One bounded Core-only deployable fix.

The ordinary provider interaction path checked the bearer and mount-family
deadlines before waiting for context and interaction-lineage locks. It could
then accept a canonical user phase after expiry. Capture already had a check
after its extra locks; that check and its lock order remain intact.

Reuse the same status, token-to-family association and deadline guard after all
interaction authorization locks. Refresh only the same mount-family row before
that final check so a revocation during the wait cannot use cached state. The
token remains locked. Record/practice/owner rules and downstream source fences,
capabilities, expiry durations, rotation, canonical retries and API schemas stay
unchanged. No schema, dependency, configuration or external service change.

Validate real PostgreSQL lineage contention with isolated synthetic providers
and records: token expiry, absolute family expiry and family revocation must
refuse without a canonical user phase; a still-valid blocked write must succeed.
Run the existing focused authority/conversation and capture guards. Tests use
only an explicitly allowed owned local database and add unique rows; no reset,
truncation, live identity, browser or model call is part of this check.

After final substantive push, obtain independent review of that exact commit.
The parent task owns staging release preparation and deployment. Recovery is
the previous immutable Core image; it restores the known expiry weakness and
must not be described as acceptance of expiry isolation. No migration rung or
UI feature flag is applicable to this guard.

This fix does not assert that Clerk logout revokes an existing mount family.
Current exchange verifies Clerk identity; subsequent opaque-bearer rotation
rechecks current Core authority. Live Clerk logout/revocation acceptance is a
separate explicit contract and test, not proved by these local cases.
