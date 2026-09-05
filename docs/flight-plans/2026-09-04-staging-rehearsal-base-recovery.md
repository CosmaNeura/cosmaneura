# Flight plan: fail-closed staging rehearsal base recovery

## Outcome and boundaries

- Problem and measurable outcome: make AzureRM refresh the private marker
  account through management-plane APIs only, prevent incidental replacement,
  and give a human operator a machine-checked path from the stopped serial-7
  apply to an exact additive remainder plan.
- In scope: the AzureRM provider feature, marker-account lifecycle guard,
  incident evidence, recovery runbook, recovery-plan validator, and tests.
- Delivery is a three-PR stack: the provider/lifecycle correction and incident
  record; the non-mutating validator and core tests; then the protected
  apply-runtime handoff, final runbook, and security regressions.
- Explicit non-goals: untainting or otherwise editing state; Azure apply or
  mutation; creating the remaining resources; enabling a runtime, workflow,
  restore, sanitizer, retention control, cutover, Auth, Zoom, or production.
- Affected environment and data class: isolated staging-rehearsal control
  metadata only; no PHI or application data.

## Risk and dependencies

- Tier **2**: this repairs a partially instantiated security-boundary control
  plane and governs a later state mutation and Azure apply.
- No irreversible decision occurs in this PR. `prevent_destroy` is a removable
  source guard, while all Azure legal-hold, WORM, and lock flags remain false.
- Dependency order: source fix and protected merge → human state snapshot/live
  readback → separately approved untaint → fresh saved plan and validation →
  independent plan review → human apply → post-apply readback.
- Cross-repository work: N/A. No sender or receiver is activated.

## Current deployed state

- Core protected `master` was `73256c24f26ebd68a7fb97443af92bdc8cbfe944`
  when the partial state was reconciled.
- Isolated backend lineage
  `76b6712e-5781-adbd-9fb1-cf66db4f4e28`, serial 7, records 28 of the original
  39 planned instances and marks only the marker account tainted.
- Live readback proves the recorded 26 Azure objects exist, the target group is
  empty, and eleven exact resources remain absent. The detailed inventory and
  plan hashes are in
  `docs/evidence/STAGING_REHEARSAL_BASE_PARTIAL_APPLY_2026-09-04.md`.
- Reconciliation decision: retain the correctly configured account and other
  live objects; remove only the false taint through an approved human state
  transition; create only the eleven missing resources.
- Previous known-good artifact and data recovery: no workload, restore, or data
  artifact exists. The boundary is the protected remote state plus ARM
  readback. Never use either pre-interruption saved plan.

## Feature flag / release control

N/A. This is infrastructure safety and incident recovery, not reversible
user-visible behavior. Every runtime and irreversible activation remains off.

## Schema/data sequence

N/A. This PR changes no schema or data and performs no restore or migration.
The later isolated synthetic-only rehearsal remains mandatory.

## Rollout and promotion

1. Merge the source-only PR after exact-head independent review and protected
   CI.
2. A human operator captures protected state and live ARM evidence and obtains
   approval for the one-resource untaint.
3. From the exact current protected-master recovery commit, the operator uses
   the incident-specific validator's create mode. It invokes the only permitted
   plan command with `-refresh=true` and must emit a passing digest-only receipt
   for exactly eleven creates and 28 no-ops.
4. A different reviewer accepts the exact saved plan and current live state.
5. The operator fetches protected master, pulls an immediate pre-apply state,
   and runs digest-bound verify mode with a new path for the protected execution
   directory. It requires the same current source, exact reviewed plan hash,
   lineage and serial 8, 28-instance inventory, and a plan age no greater than
   four hours. It performs a fresh read-only-lockfile initialization and binds
   the preserved provider runtime and exact backend into its receipt.
6. From that same protected execution context, the operator runs the isolated
   GET-only handoff preparer. It proves two identical ARM snapshots, the exact
   26-object/eleven-absence live contract, target emptiness, effective policy
   and role inventories, and unchanged serial-8 remote state. Its owner-only,
   ten-minute receipt binds the source, plan, state, runtime, Azure CLI and
   configuration, TLS trust, machine boot session, and one-use nonce. It always
   reports `apply_authorized=false`.
7. Stop until a separate independently reviewed human-only consumer can
   revalidate and consume that exact receipt immediately before replacing its
   process with the receipt-bound OpenTofu invocation. A human operator then
   applies only the exact reviewed plan and records post-apply readback.
   OpenTofu must also accept its native saved-plan/state binding. Stop before
   any later rung.

Abort on a changed lineage, pre-untaint serial, tainted/deposed instance, failed
check, drift, update, delete, replacement, extra/missing resource, changed
input, wrong provider/version, stale plan, target-group resource, or live
posture mismatch. No production or PHI promotion is part of this plan.

## Security and recovery

- AzureRM 4.73.0's storage data plane is explicitly unavailable. This avoids
  inaccessible Queue/static-site probes; it does not weaken the account's
  private-network or Shared-Key prohibition.
- `prevent_destroy` makes any taint-driven replacement fail closed. Intentional
  decommission requires a separate reviewed source change before destroy.
- `storage_use_azuread` is not a substitute because the operator has no private
  data-plane route. A later AzAPI account migration, if desired to eliminate
  disabled-key material from state, requires its own rehearsed state migration.
- No `-refresh=false`, `-target`, `-replace`, force-unlock, state removal/import,
  unsaved apply, or raw-state/plan publication is permitted.
- The validator strips ambient OpenTofu, Git, and Azure-provider overrides,
  pins the reviewed subscription, backend coordinates, Azure public cloud, default workspace, provider tenant, and OpenTofu 1.12.5; it generates the full-refresh plan itself,
  decodes JSON from the exact
  captured plan bytes, verifies the archive's Terraform source and provider
  lockfile against the clean expected protected-master commit, and binds its
  embedded state to the protected snapshot. Artifacts must be single-link,
  owner-only regular files in a symlink-free owner-only directory.
- OpenTofu 1.12.5 does not emit `applyable` or `complete` in plan JSON. A
  successful detailed-exitcode plan plus `errored=false`, the exact nonempty
  28-no-op/11-create closed world, zero drift/deferred/action invocation, and
  passing checks is the executable completeness proof. If a later OpenTofu
  version emits either field, the validator requires it to be true.
- The handoff preparer uses only bounded, no-proxy, no-redirect HTTPS GETs and
  never emits the Azure token or raw response bodies. It refuses an ambient
  Python/import/transport environment and revalidates the exact runtime after
  the live reads.
- Observation window: read back all 39 expected state instances and exact live
  controls before any private-executor or irreversible-retention plan.

## Completion

- This PR is complete after focused tests, OpenTofu format/validation/mocked
  tests, change-budget check, independent exact-head review, and protected CI.
- Incident recovery is complete only after the later operator evidence proves
  the exact additive apply and readback. This PR alone does not claim it.
- Update the evidence record and README status after operator recovery.
