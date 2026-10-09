# API-only CAB generation runtime flags

Tier 2: billing runtime configuration. This follows the dormant async and
continuation implementations in PRs #693 and #694 and adds no schema or identity.

The existing application reads `CORE_CAB_ASYNC_ENABLED` and
`CORE_CAB_CONTINUATION_ENABLED`, but its release templates and API projection
omitted them. Include both as public API-only flags, default false. Validate
closed Boolean strings; async requires the complete existing bridge/authorizer,
and continuation also requires async. The effective staging configuration
fingerprint includes either change. Workers, scheduler and migration jobs receive
neither flag or source policy.

Merge this plumbing dormant. Build the selected immutable Core artifact with its
public endpoint configuration recorded; a build pointing to existing staging is
not a separately routed synthetic provider beta. Reconcile actual target schema
and configuration, accept the compatible CAB receiver, configure the dedicated
two-way identities and exact synthetic note admissions, then enable the chosen
capabilities in a separately reviewed target activation. Keep Session Prep and
Sovereign independent. Source and local proofs do not authorize hosted activation.

Recovery disables continuation before or together with async; a full channel
shutdown also disables bridge and authorizer and revokes source admissions.
Preserve action/continuation evidence and additive schema. No migration rung or
new restore rehearsal applies to this configuration-only change; retain the
actual migration evidence for PRs #693/#694. Validate the existing secretless
runtime wiring checks plus the new flag/dependency/API fingerprint cases.
