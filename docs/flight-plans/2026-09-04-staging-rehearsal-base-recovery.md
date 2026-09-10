# Flight plan: fail-closed staging rehearsal base recovery

## Outcome and boundaries

- Problem and measurable outcome: make AzureRM refresh the private marker
  account through management-plane APIs only, prevent incidental replacement,
  and give a human operator a machine-checked path from the stopped serial-7
  apply to an exact additive remainder plan.
- In scope: the AzureRM provider feature, marker-account lifecycle guard,
  incident evidence, recovery runbook, recovery-plan validator, and tests.
- Delivery is a four-PR stack: the provider/lifecycle correction and incident
  record; the non-mutating validator and core tests; the protected GET-only
  apply-runtime handoff; then its human-only one-use consumer and regressions.
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
3. From the exact current protected-master recovery commit, the operator first
   uses the incident-specific state-refresh validator and human-only consumer.
   They permit only the ten closed-world provider-state normalizations observed
   after untaint, one non-secret output creation, and zero Azure resource
   actions. The one-use apply advances state from serial 8 to serial 9 without
   changing Azure resources. Raw state, raw plan JSON, and hydrated storage
   credentials remain owner-only and are never review payloads.
4. After exact serial-9 state and live readback are captured, the operator uses
   the base-recovery validator's create mode. It invokes the only permitted
   full-refresh plan command and must emit a passing digest-only receipt for
   exactly eleven creates, 28 no-ops, and zero drift.
5. A different reviewer accepts the exact saved plan and current live state.
6. The operator fetches protected master, pulls an immediate pre-apply state,
   and runs digest-bound verify mode with a new path for the protected execution
   directory. It requires the same current source, exact reviewed plan hash,
   lineage and serial 9, 28-instance inventory, and a plan age no greater than
   four hours. It performs a fresh read-only-lockfile initialization and binds
   the preserved provider runtime and exact backend into its receipt.
7. From that same protected execution context, the operator runs the isolated
   GET-only handoff preparer. It proves two identical ARM snapshots, the exact
   26-object/eleven-absence live contract, target emptiness, effective policy
   and role inventories, and unchanged serial-9 remote state. Its owner-only,
   ten-minute receipt binds the source, plan, state, runtime, Azure CLI and
   configuration, TLS trust, machine boot session, and one-use nonce. It always
   reports `apply_authorized=false`.
8. After the consumer is independently reviewed and merged, discard every
   receipt made before its merge and prepare a new plan, execution directory,
   and handoff from the exact current protected `master`. Supply the consumer
   the independently recorded source, plan, handoff-receipt, and Azure-user
   principal digests. It requires a non-root human at one foreground controlling
   terminal to enter the full commit, both artifact digests, the exact
   statement `APPLY STAGING-REHEARSAL SERIAL 9: NO-OP 28, CREATE 11, UPDATE 0,
   DELETE 0, REPLACE 0`, and a fresh random challenge. Only after the two initial
   identical GET-only ARM snapshots and second complete local validation does
   that 120-second action window begin. The same deadline covers all five
   entries, another complete GET-only ARM snapshot, final remote-state,
   source/master, provider, plan-age, terminal, and expiry checks, and the
   one-use claim. Immediately after the entries, the reader clears the earlier
   ARM response-date and byte counters for the final snapshot while retaining
   the exact already-running action deadline; it cannot create a fresh or
   longer window.
9. The consumer atomically creates the owner-only one-use claim and directly
   opens and revalidates the plan descriptor-relatively, keeps that exact
   owner-only descriptor across `execve`, and replaces itself with OpenTofu
   1.12.5 using `/dev/fd/<n>` rather than the mutable plan pathname. It offers no
   yes flag, alternate binary, extra Terraform argument, environment override,
   or automation interface. OpenTofu stdout and stderr go only to an
   identity-bound owner-only `state-refresh-apply.log` in the execution
   directory; treat it as sensitive local evidence and never publish or submit
   it for review. Never run the consumer from CI or an agent. If anything fails
   after the claim is created—including `execve` or OpenTofu itself—do not delete
   or overwrite the claim; create and review a new plan, directory, and handoff.
   After a successful human apply, record post-apply state and ARM readback, then
   stop before any later rung.

Create and revalidate the base-recovery plan through the inline bootstrap below.
Take `REVIEWED_LAUNCHER_SHA256` from the independent review of the exact merged
source; do not derive an expected digest from the mutable checkout at execution
time. The system shell copies the launcher into a fresh private directory and
verifies that copy before executing any repository code. Direct invocation of
the repository launcher is unsupported and refuses.

The authenticated launcher separately copies the validator, manifest, and
complete Apple Command Line Tools Python 3.9.6 runtime into new private inodes.
It removes ACLs and write permissions, then verifies all 2,006 entries, relative
paths, file contents, link targets, link counts, modes, owners, and zero ACLs.
Each enumeration command must succeed before its output is parsed. Python,
its framework, and all imports execute from that verified copy. A previously
opened writable descriptor to a source file cannot alter the copied inode.
The validator is then read into memory, digest checked, and compiled from those
same bytes. The receipt binds the snapshot policy and normalized tree hashes;
the private runtime is removed when the invocation ends.

The version-3 candidate receipt remains non-authoritative
(`launcher_execution_authenticated=false`). The preparer and final consumer
each independently authenticate the launcher and execute its exact bytes from
a closed-writer anonymous pipe before promoting that fact to `true`.

```sh
/usr/bin/env -i /bin/sh -c '
set -eu
umask 077
recovery_source_path=$1
recovery_launcher_digest=$2
shift 2
case "$recovery_launcher_digest" in
  ""|*[!0-9a-f]*) exit 2 ;;
esac
[ "${#recovery_launcher_digest}" -eq 64 ] || exit 2
recovery_bootstrap_dir=$(/usr/bin/mktemp -d /private/tmp/cosmaneura-launcher.XXXXXXXX)
cleanup() { /bin/rm -rf -- "$recovery_bootstrap_dir"; }
trap cleanup EXIT
trap "exit 130" HUP INT TERM
/bin/chmod -N "$recovery_bootstrap_dir"
/bin/chmod 0700 "$recovery_bootstrap_dir"
/bin/cp -X "$recovery_source_path" "$recovery_bootstrap_dir/launcher.sh"
/bin/chmod -N "$recovery_bootstrap_dir/launcher.sh"
/bin/chmod 0500 "$recovery_bootstrap_dir/launcher.sh"
recovery_observed=$(/usr/bin/shasum -a 256 "$recovery_bootstrap_dir/launcher.sh")
if [ "$recovery_observed" != "$recovery_launcher_digest  $recovery_bootstrap_dir/launcher.sh" ]; then
  echo recovery_bootstrap_refused >&2
  exit 2
fi
/bin/sh "$recovery_bootstrap_dir/launcher.sh" \
  --authenticated-launcher-sha256 "$recovery_launcher_digest" "$@"
' recovery-bootstrap \
  /ABSOLUTE/PROTECTED_MASTER/scripts/run_staging_rehearsal_base_recovery_plan.sh \
  REVIEWED_LAUNCHER_SHA256 \
  --mode create \
  --saved-plan /ABSOLUTE/OWNER_ONLY/recovery.tfplan \
  --state-json /ABSOLUTE/OWNER_ONLY/serial-9.tfstate \
  --expected-source-commit SOURCE_COMMIT_40_LOWERCASE_HEX
```

For digest-bound revalidation, use the same bootstrap with `--mode verify` and
add `--execution-dir /ABSOLUTE/OWNER_ONLY/NEW_EXECUTION_DIRECTORY` and
`--expected-saved-plan-sha256 REVIEWED_SAVED_PLAN_SHA256`.

The approved human runs the consumer directly from the exact protected-master
checkout with an empty ambient environment. Replace the placeholders below with
absolute owner-only artifact paths and independently recorded lowercase
digests. `AZURE_PRINCIPAL_SHA256` is the SHA-256 of the approved AzureCloud user
principal name; never put the principal name or any secret in this command,
receipt, logs, or review:

```sh
/usr/bin/env -i PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin \
  /opt/homebrew/bin/python3 -IS \
  /ABSOLUTE/PROTECTED_MASTER/scripts/consume_staging_rehearsal_base_recovery_handoff.py \
  --saved-plan /ABSOLUTE/OWNER_ONLY/recovery.tfplan \
  --state-json /ABSOLUTE/OWNER_ONLY/serial-9.tfstate \
  --execution-dir /ABSOLUTE/OWNER_ONLY/execution \
  --receipt /ABSOLUTE/OWNER_ONLY/handoff.json \
  --expected-source-commit SOURCE_COMMIT_40_LOWERCASE_HEX \
  --expected-saved-plan-sha256 SAVED_PLAN_SHA256 \
  --expected-receipt-sha256 HANDOFF_RECEIPT_FILE_SHA256 \
  --expected-azure-principal-sha256 AZURE_PRINCIPAL_SHA256
```

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
  Python/import/transport environment, accepts a base receipt only after it has
  independently authenticated and executed an immutable, anonymous snapshot
  of the exact launcher bytes, and revalidates the exact runtime after the live
  reads.
- The human-only consumer also refuses ambient Python, dynamic-loader, proxy,
  OpenTofu, Azure, and Git overrides. Its final `execve` environment is a closed
  allowlist, and its immutable claim remains after an execution failure so the
  same handoff cannot be retried ambiguously. Terminal presence is a strong
  local ceremony, not cryptographic proof of a particular person; the external
  principal digest and independent review remain required.
- Observation window: read back all 39 expected state instances and exact live
  controls before any private-executor or irreversible-retention plan.

## Completion

- The consumer PR is complete after focused tests, static checks, change-budget
  verification, independent exact-head review, and protected CI.
- Incident recovery is complete only after the later operator evidence proves
  the exact additive apply and readback. This PR alone does not claim it.
- Update the evidence record and README status after operator recovery.
