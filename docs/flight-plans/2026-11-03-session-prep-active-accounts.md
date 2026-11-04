# Session Prep for every active account

Risk: Tier 2 (account authorization and provider-record namespace).

An active authenticated account receives Session Prep without a pilot grant,
practice membership, onboarding, BAA snapshot or billing entitlement. Deleted
accounts and effective account cancellation remain unavailable. Every record
exchange still requires exact actor, Doctor, patient, binding, tenant and live
membership provenance. Read-only view-as does not issue Session Prep authority.

Accounts without a practice receive a private, immutable Doctor-owned provider
namespace when opening an owned visit. Existing practice namespaces and record
bindings are retained. Joining a practice preserves that private namespace and
Scribe's existing access rules; no practice, legal attestation or billing rows
are fabricated.

Release sequence:

1. Merge the account namespace, additive migration, active-account policy and
   matching eligibility diagnostics after independent review and required checks.
2. Merge the own-record dashboard routing, HTTP access and native account tests
   after independent review and required checks. Deploy both in one canonical
   staging candidate, preserving runtime configuration and Scribe apps.
3. Rehearse the actual staging schema through the current single Alembic head
   on a new synthetic local database. Capture fresh staging identity,
   configuration, schema and recovery image evidence before dispatch.
4. Verify John and dev under their own accounts: discovery, an existing owned
   visit, prior notes, answer persistence/export and foreign-record denial.
   Verify view-as explains how to return to the account's own visits.

Recovery: close Session Prep with its rollout flag and retain additive schema
and immutable ownership evidence. After private namespaces are issued, use
only account-namespace-compatible Core images; never restore older readers.
No automatic image rollback runs; Scribe apps/provider service stay in place.

Production activation requires a separate production deployment decision.
Independent review evidence is pinned to the final source commits in the PRs.
