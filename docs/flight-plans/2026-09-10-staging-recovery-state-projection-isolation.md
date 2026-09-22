# Flight plan: isolate temporary recovery state projection

## Outcome and risk

Tier **2**. Repair a deterministic pre-confirmation recovery refusal without
weakening the human consumer's identity checks or granting mutation authority.
This supplements `2026-09-04-staging-rehearsal-base-recovery.md`; its source,
plan, principal, runtime, human-action and live-state requirements still apply.

At protected master `96872cdb75bbe66df80706404e5450583f383ba4`, an exact
read-only replay of the consumer prefix refused at `local_binding_equality`
with `binding.changed`. The only differences were execution-directory mtime
and ctime: decoding state created and removed its own temporary file inside
that bound directory. The human attempt reached none of the five prompts;
its one-use claim and apply log were absent. No Azure apply occurred.

## Change and validation

- Put transient state-decoder input beside the execution directory, in its
  separately verified owner-only, symlink-free parent, not inside it.
- Preserve mode-0600 single-link file checks, exact bytes, reopened descriptor
  identity, cleanup, and all execution-directory identity/timestamp checks.
- Update the authenticated launcher and dependent validator/handoff pins.
- Exercise repeated real state projection through local binding validation;
  unchanged inputs must compare equal, while a real execution-folder change
  must still compare unequal. Test insecure-parent refusal and failure cleanup.
- Retain real-launcher tests in isolated worktrees by relocating the fixed
  canonical source prefix only in the test fixture. Production paths and
  launcher authentication remain unchanged.
- Run the recovery plan/handoff/consumer and state-refresh plan/consumer
  suites, Ruff, Pyright, shell syntax, diff checks and the change-budget gate.
  Obtain independent exact-final-head review and required protected CI.

No schema, application, billing, Auth, Zoom, network or Azure resource change
is included. A feature flag and migration rung are not applicable. No raw
state, saved-plan bytes, provider output, credentials or PHI are review data.

## Rollout and exact recovery

1. Merge this source-only repair through normal protected PR controls.
2. Update the clean canonical checkout to the exact merge commit. Discard all
   earlier receipts as authority; preserve their files as historical evidence.
3. Capture fresh protected state and produce a new full-refresh saved plan.
   Require serial 9, the expected lineage, 28 no-ops, exactly eleven creates,
   and no update/delete/replace/drift. All receipts remain non-authorizing.
4. Prove the complete non-applying prefix, including the two local validations
   around live GETs, before asking for another human attempt. Independently
   review the new exact plan and digest-only handoff evidence. Never reuse
   expired receipts or delete a one-use claim.
5. Only the human performs the existing foreground-terminal ceremony; the
   agent never invokes the consumer or Azure mutation. Read back all 39
   expected instances after success before advancing the rehearsal milestone.

On any changed binding or refusal, stop without retrying the same handoff;
preserve evidence and diagnose read-only. Reverting this source commit via a
new reviewed PR restores the previous fail-closed behavior, not successful
recovery. It needs no resource rollback because this PR creates none. The
user's temporary Terminal App Management grant should be revoked after
verified recovery or if the operator session is paused. No application,
staging cutover or production promotion is authorized by this repair.
