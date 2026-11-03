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

1. Merge the dormant account-owned namespace, additive migration and Scribe
   continuity change after independent review and required checks.
2. Merge the active-account policy together with its eligibility diagnostics
   and native namespace cases after independent review and required checks.
3. Merge the own-record dashboard routing and active-account HTTP access change
   after independent review and required checks. Deploy all three in one
   canonical staging candidate, preserving runtime configuration and Scribe apps.
4. Rehearse the actual staging schema through the current single Alembic head
   on a new synthetic local database. Capture fresh staging identity,
   configuration, schema and recovery image evidence before dispatch.
5. Verify John and dev under their own accounts: discovery, an existing owned
   visit, prior notes, answer persistence/export and foreign-record denial.
   Verify view-as explains how to return to the account's own visits.

Recovery: restore the previously verified Core image and its preserved runtime
configuration. Keep the additive namespace column, constraints and new rows;
do not drop or rewrite immutable ownership evidence. Existing Scribe
applications and the provider service are not replaced by this release.

Production activation requires a separate production deployment decision.
Independent review evidence is pinned to the final source commits in the PRs.
