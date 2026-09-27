# Companion staging configuration

Tier 2: this extends the staging deployment configuration for a distinct API
caller. Merging it does not deploy Core, grant an Entra role or grant access to
any clinical record. Production configuration is unchanged.

The reviewed release plan may include all four public runtime fields:

- `AGENT_API_COMPANION_CLIENT_ID`
- `COMPANION_ENCOUNTER_READ_ENABLED`
- `COMPANION_RECORD_READ_ENABLED`
- `COMPANION_APPOINTMENT_SETUP_ENABLED`

Omission retains historical release-plan compatibility and does not admit a
caller from inherited environment values. The group must be complete; flags are
literal `true`/`false`, and the caller must be a nonzero UUID distinct from the
Scribe receiver. The existing application startup check also rejects a caller
shared with Sovereign. An empty caller is accepted only with all flags false.
These values participate in each workload's exact configuration attestation.

## Activation sequence

1. Locally validate and review the exact source commit, then merge the PR.
2. Prepare the selected immutable Core candidate and read the current workload,
   identity and configuration baseline. Carry forward the current Scribe and
   Sovereign values. Record the four-field delta and the distinct caller's exact
   API application-role assignment for owner approval.
3. With activation authority, deploy the selected Core image and reviewed
   configuration before enabling the CosmaBot connector. Verify all three Core
   workloads and the unchanged existing integrations. There are no migrations
   in this change.
4. Verify the bounded companion connection with the shared human identity and
   existing provider/record authority. Entra admission alone grants no clinical
   record access.

Subsequent configuration-preserving releases must carry the active group in
their reviewed runtime inputs. Omitting it withdraws companion admission; the
existing exact-configuration preservation check refuses that change when the
live workload is still enabled.

Recovery: before activation, the existing live revision is unaffected. After
activation, withdraw the distinct caller using an empty ID and three false
flags, or use the retained per-workload image/revision recovery plan. Remove the
separate role assignment if withdrawing the integration. Preserve the shared
identity, existing Scribe/Sovereign callers and all stored workspace data.
