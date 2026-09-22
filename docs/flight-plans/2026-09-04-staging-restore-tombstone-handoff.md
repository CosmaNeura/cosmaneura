# Flight plan: Stage the dormant restore tombstone handoff

Status: **source and tests only; deliberately non-executable**.

## Outcome and boundaries

- Give the future provision job create/read authority on its exact v4 cleanup
  container so a partial, failed, expired, or indeterminate restore attempt can
  publish and read back the dominant tombstone before handing deletion to the
  cleanup job.
- Keep ARM resource deletion exclusively in cleanup. Provision can submit a
  restore and persist the cleanup handoff, but it cannot delete the target or
  any related resource.
- Keep all Terraform switches false by default. This change runs no workflow,
  performs no Azure apply, restores or mutates no data, and authorizes no
  staging, production, or PHI operation.

## Risk and dependency order

- **Tier 2:** the change narrows a future security and cleanup boundary.
- The activated Terraform shape has exactly six logical marker assignments:
  two cleanup read grants and four create/read grants. Enabling create grants
  makes provision's one cleanup assignment use create/read instead of
  read-only; the desired plan contains no duplicate. The stable output key is
  `provision_cleanup` in either state.
- Create/read assignments require, in the same reviewed plan, live provider
  proof of conditional create/read success and overwrite/delete denial with
  exactly Blob read plus one create action, action-time proof of the private
  marker account/Blob endpoint/DNS/VNet-link posture, authenticated action-time
  readback of active legal holds, and locked 90-day WORM with both
  protected-append modes disabled.
- The dependency order is marker account/private endpoint/DNS → legal holds and
  locked WORM → authenticated legal-hold readback → exact create/read role
  assignments. A later runtime and dispatcher change must consume the IDs; this
  PR does not.

## Verification, rollout, and recovery

1. Run formatting, static validation, and provider-mocked secretless plans.
   Tests must prove default dormancy, the three-read dormant shape, the exact
   two-read/four-create activated shape, the single logical
   `provision_cleanup` output, and rejection without private-network proof.
2. Review the source-only change independently. Do not run a credentialed plan
   or apply as part of this PR.
3. In a later authorized change, collect provider-fidelity and private-network
   readback, enable legal hold and locked WORM, independently read back the
   active legal-hold tags, review the exact saved plan, then install and read
   back the six assignments.
4. Only later may a reviewed private runtime bind provision's tombstone-only
   handoff and cleanup's deletion authority. Workflow integration and a live
   rehearsal remain separate approvals.

Recovery before any apply is revert/disable in source. After legal hold or
locked WORM is applied, recovery is roll-forward under the existing retention
procedure; the locked policy is irreversible. Removing a create grant does not
remove or weaken an existing tombstone.
