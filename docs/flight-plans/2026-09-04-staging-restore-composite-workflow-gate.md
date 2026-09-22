# Flight plan: fail closed at the staging restore workflow boundary

## Outcome and boundaries

- Problem and measurable outcome: remove the legacy workflow route that could pass caller-supplied digest claims directly to `staging_restore_envelope.py provision`; selecting `provision` must reach only a credentialless failing tombstone.
- In scope: the protected-master rehearsal workflow, its focused static tests, and documentation of the future trusted coordinator boundary.
- Explicit non-goals: enabling evidence policies, provisioning infrastructure, dispatching the workflow, restoring or sanitizing data, staging cutover, or production promotion.
- Affected users, services, environments, and data classes: operators of the dormant staging restore control plane; no runtime user, environment, or data is changed by this PR.

## Risk and dependencies

- Tier and reason: **Tier 2** because the edited workflow is a future restore/security boundary, even though this change only removes authority.
- Irreversible or high-blast-radius decisions: none executed. The provision job has `permissions: {}`, no environment, checkout, OIDC, Azure login, or mutation command, and fails within one minute.
- Dependency-ordered PR stack; explain how each PR can be held safely: manifest snapshot → server authentication → provider observation → GitHub attestation → composite binding → this workflow gate. Every layer remains dormant and may be held indefinitely.
- Cross-repo contract/version and receiver-before-sender order: N/A; all contracts are in this repository and no sender is activated.

## Current deployed state

- Live SHA/digest/configuration/schema evidence by affected environment: no live workflow dispatch or Azure mutation was performed. Base `master` commit `b0b7e32a9b15311e2c960e1ebe7987320b939f41` still exposed the legacy direct provision workflow route; this PR removes that route in repository state only.
- Difference from `master` and deployed-only behavior: `provision` remains a visible manual choice solely to return an explicit failure; manual cleanup and gated hourly sweep retain their existing controller, identity, attested receipt, and artifact path.
- Reconciliation decision: replace the unsafe provision route; retain cleanup/sweep so existing restore sets cannot be stranded.
- Previous known-good artifact and data-recovery boundary: N/A for provision because no restore is created. Cleanup continues to use exact ownership/name/expiry/network checks and fails closed on ambiguous sets.

## Feature flag / release control (reversible user-visible behavior only)

- N/A: this is a fail-closed security/workflow boundary, not reversible user-visible behavior.

## Schema/data sequence (schema/data work only)

- N/A: no schema or data change and no migration/rehearsal is executed.

## Rollout and promotion

- Ordered environments/cohorts and hold points: focused workflow tests → independent agent review of the final commit → protected PR checks → merge. Hold after merge; do not dispatch `provision` as a live rehearsal.
- Health signals and measurable abort thresholds: static tests must prove the tombstone has no credentials or mutation path and cleanup/sweep remains reachable. Any provision checkout, environment, OIDC permission, Azure login, controller provision call, or safe-submit call is an abort.
- Canary journey: N/A; the only provision outcome is deterministic failure.
- Founder-only manual production/PHI promotion record, or current control gap: no production/PHI promotion is in scope. A later restore activation remains a separate founder-authorized decision.

## Security and recovery

- Open security acceptances, scope, expiry, and promotion impact: none accepted here. Opaque seals are in-process capabilities and cannot cross GitHub step/job output or artifact serialization; reconstructed objects fail exact-type/module-seal checks.
- Activation blockers: composition records `restore_evidence_composition_policy_disabled`, `upstream_policies_disabled`, `trusted_composite_verifier_unimplemented`, `workflow_integration_unimplemented`, `trusted_executor_integration_unimplemented`, and `restore_activation_unimplemented`; its readiness and four activation flags remain false. All five safe-submit activation flags remain false; verifier/root digests and storage/network/RBAC/WORM/fidelity `CONFIGURE_EXACT_...` pins remain unset; no single-process trusted coordinator/private-runner binding exists; live non-PHI provider-fidelity evidence and restore authorization remain absent.
- Recovery move: revert the workflow-only change only if the trusted replacement route is already reviewed; otherwise keep provision disabled. Cleanup/sweep is the recovery path for pre-existing restore sets and is intentionally preserved.
- Observation window before irreversible cleanup: N/A; this PR creates nothing and changes no retention boundary.

## Completion

- Evidence required to call the outcome complete: workflow diff and focused tests prove legacy inputs/command/credentials are absent from provision, tombstone failure is unconditional, cleanup/sweep controls are preserved, change budget passes, and final independent review is recorded against the reviewed commit.
- Follow-up cleanup PRs and owners: a separate Tier-2 PR must implement one trusted process that authenticates receipts, mints and composes opaque seals, rechecks freshness, and invokes safe-submit directly; activation and live rehearsal remain separately reviewed and authorized.
- Documentation and contract records to update: these two staging runbooks and this flight plan. No policy activation bit changes in this PR.
