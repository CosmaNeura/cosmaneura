# CosmaBot companion API integration

Risk: Tier 2 (human identity, workload admission, and patient-record boundaries).
This adds platform APIs used by the separate CosmaBot app. It deploys no CosmaBot
frontend, recording implementation, or database schema to the platform.

## Receiver-first patch stack

1. Add the saved-record contract and bounded read service. These reuse the
   existing verified human/context and Scribe capture-binding authority.
2. Add bounded record and connection/encounter discovery receivers, plus the
   optional distinct companion workload caller for context/capture protocols.
   Sovereign tools retain their existing separate caller restriction.
3. Add reviewed administrative provider correspondence and appointment
   preview/connect. Existing practice membership and processing access are
   prerequisites. Administrative apply requires its exact reviewed plan.

Each increment stays within the repository's 2,000 changed-line/50-file budget.
All three are needed before normal CosmaBot onboarding can be activated. Keep
the receivers disabled while the stack is incomplete. Current Core/Scribe
source, capture limits, and explicit recording renewal remain in place.

## Activation boundaries

The default values are false for `COMPANION_RECORD_READ_ENABLED`,
`COMPANION_ENCOUNTER_READ_ENABLED`, and `COMPANION_APPOINTMENT_SETUP_ENABLED`.
`AGENT_API_COMPANION_CLIENT_ID` defaults to empty. Source inclusion alone does
not provision an identity, grant an Entra role, change the cohort, or enable I/O.

After the platform owner authorizes the API/security change and the reviewed
stack is merged, the platform's approved staging path must deploy the exact
candidate. Reconcile its live revision, schema, and environment before changing
configuration. A distinct reviewed CosmaBot workload identity needs the existing
`AgentApi.Access` role for the correct tenant/audience and the new explicit caller
allowlist. Preserve the Scribe/Sovereign identities and caller separation.

Human sessions still come from the platform's verified Clerk issuer. Separately
verify the existing doctor, practice membership, platform access, authority
tenant, and Scribe practice correspondence. Use `provision-companion-provider`
to inspect a plan; applying it requires the exact plan hash and reviewed operator
references. The helper cannot grant a practice membership or product access.
Do not invent a mapping or reuse a foreign tenant merely to pass onboarding.

The human reviews an appointment preview before connecting its exact batch.
Discovery creates no patient, appointment, or authority records. There is no
automatic appointment-insert hook in this stack.

## Acceptance and recovery

Local acceptance exercises the full reviewed-provider to appointment-setup to
current capture-binding path with synthetic PostgreSQL rows, including stale
plan refusal, current authorization checks, caller separation, and revocation.
The current receiver boundary/provisioning checks run with repository-wide
Clerk/database cleanup hooks excluded. These local checks are not live staging
acceptance; record exact tested/reviewed commit hashes in each eventual PR.

After authorized deployment, perform one synthetic human-session discovery,
reviewed appointment connection, capture admission, upload, transcript, and
review/save readback through the separate CosmaBot environment. No live recording
is transferred between devices. Saved work may be reopened on another device.

If the integration fails, disable the three new receivers and remove only the
companion caller admission/role granted for this integration. This stops new
use; it does not undo existing correspondence or audit records. Investigate and
explicitly revoke mistaken mappings through the platform's reviewed process.
Do not delete authority history, reset data, or overwrite newer Core/Scribe
deployments as a rollback. Production promotion remains a separate owner action.
