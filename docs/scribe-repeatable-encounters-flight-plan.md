# Repeatable provider-created Scribe encounters

Owner request: 2026-09-29. Tier 2: patient creation and current account authority.
Staging deployment and idle desktop/mobile updates are authorized; production is excluded.

Core owns the existing Patient and Appointment records and provider authority.
The new human-authenticated encounter endpoint reuses the current companion
provider resolver, its Practice-before-Doctor locks, the support-view prohibition,
and provider-record correspondence. Existing patients are selected by exact ID.
Creating a patient is explicit; names and birth dates are never identity keys.

An additive Core receipt binds an account-scoped creation UUID to the exact
request digest, patient, appointment and Scribe workspace UUID. The receipt and
source records commit in one transaction. Retries return the same result; reuse
with different input conflicts. No consent, recording session, audio or keys are
created by this operation. Consent and recording retain the existing pathway.

The native receiver remains a read-only consumer of the existing OS registry.
Core exposes its new receipt-backed encounter list and workspace lookup through
the verified human session. The receiver compares that result to its existing
reviewed account mapping before and after I/O. Legacy registry workspaces keep
their original IDs and recovery scopes. No cross-account support-view capability
authorizes create, record or save.

Release sequence:
1. Reconcile actual staging account records, current source/images/config/schema.
2. Rehearse additive migration; deploy merged Core receiver with creation disabled.
3. Deploy compatible Scribe receiver and enable the bounded staging workflow.
4. Update idle desktop and mobile with their existing identities and preserved local data.
   Both use the same patient/setup UI and encrypted retained creation intent.
5. Run three fresh synthetic encounters, duplicate/lost-response retries, account
   scoping, saved-note reopening and existing recovery regression checks.

Rollback: disable new encounter creation and restore the prior immutable images
and desktop bundle. Retain the additive receipt table and every created encounter;
do not drop records or change encryption/recovery identifiers. The old catalog
and capture endpoints stay compatible throughout rollout. No unrelated feature
flag (including companion Session Prep) is enabled by this release.

Evidence must identify exact tested/reviewed SHAs, schema before/after, images,
desktop/mobile builds and whether each test used automation, simulated audio, a physical
microphone or a real two-party Zoom meeting. Human sign-in/agreements cannot be
fabricated. John/Shawn invitations or messages are outside this authorization.

Release control owner: John. Initial cohort: existing reviewed staging Scribe provider mappings.
Review the temporary creation enablement controls by 2026-10-30; no production activation.
