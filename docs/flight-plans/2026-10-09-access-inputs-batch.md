# Combine fresh access inputs for explicitly timed locked Scribe decisions

Tier 2. Continue the owner's all-twelve Scribe repairs and 5–7 second startup
objective. No access rule, pilot cohort, billing/BAA policy, consent, schema,
configuration, session credential or release boundary changes.

Within one access decision already holding actual provider row locks, combine
its current member/practice read and later Doctor/pilot-grant candidate read.
The revoked-grant tombstone query still runs first. Require valid existing row
ownership and an explicit decision timestamp; default-clock calls keep the
original read and clock sampling order. No permission or result is retained
across decisions, transactions or awaits. Invalid, expired or mismatched
ownership uses the original individual reads. Preserve existing ORM identity
behavior, Doctor validation before ambiguous grant refusal, snapshot/email/hash
validation, membership tombstones and all current entitlement/BAA rules.

Qualification compares complete decisions against the current separate-query
path and original rules across grant/replacement/revocation, missing/changed
membership, entitlement, BAA, timing cutoffs, malformed identity, duplicate grants,
loaded identity-map values and proof expiry. Native receipts and exact original
locks must match, and real billing/workforce/pilot writers must serialize and
cause a later opening to deny. Default clock stage is unchanged. Record exact
committed-source broader tests, actual five-stage SQL/deadline counts, types,
lint, format, change budget and implementer review before merge.

Use the existing immutable merged-source staging release path, preserving the
current five-workload configuration, secrets, Core/Scribe schema and recovery.
No migrations. Keep the existing 20 GiB build guard. Desktop build 64 requires
manual Mac unlock for installation and fresh consent-to-Ready measurement.
Only virtual audio is authorized; never press installed Start for physical audio.
Recovery: revert this patch or retain Core 718 staging image
sha256:92bdb2e4c5f1261c0a0a4738aef42da0d57f630f0daff75b1c2ae7909246fa73.
Production remains outside scope. The all-twelve and consistent 5–7 second goals
remain open until installed/deployed acceptance verifies them.
