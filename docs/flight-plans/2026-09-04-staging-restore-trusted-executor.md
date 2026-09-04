# Flight plan: dormant single-process staging restore executor

## Outcome and boundaries

- Problem and measurable outcome: define and implement a fail-closed coordinator boundary that can authenticate the original staging evidence inputs, keep every opaque seal in one Python process, perform the final freshness join, derive the only permitted restore request, and call safe-submit directly.
- In scope: the closed-world executor policy, a public four-path entry point, a dependency-injected private seam, focused unit tests, and documentation. TTL is derived from a validated provision launch rather than accepted from the caller. This policy/documentation slice does not itself add executable adapters.
- Explicit non-goals: enabling any policy, editing the protected workflow, provisioning a runner or Azure resource, dispatching a workflow, restoring or sanitizing data, staging cutover, or production promotion.
- Affected users, services, environments, and data classes: future operators of the isolated staging restore rehearsal; no user, service, environment, or data is changed while the boundary is dormant.

## Risk and dependencies

- Tier and reason: **Tier 2** because this is the last coordinator boundary before a future restore side effect.
- Irreversible or high-blast-radius decisions: none in this slice. Every executor, adapter, runner, workflow, and restore flag is false, all runtime identities and image/network pins are placeholders, and the public entry point must refuse before reading authority inputs or invoking an adapter.
- Dependency-ordered PR stack: evidence manifest → server evidence/authentication → provider observation → GitHub attestation → evidence composition → workflow tombstone → trusted executor contract. Each layer remains disabled and can be held indefinitely.
- Cross-repo contract/version and receiver-before-sender order: N/A; all pinned policies and consumers are in this repository. A future workflow sender must follow reviewed executor/runtime activation, never precede it.

## Current deployed state

- Live SHA/digest/configuration/schema evidence by affected environment: no live command, dispatch, or Azure mutation is part of this change. The workflow provision path remains the credentialless one-minute tombstone merged in PR #281.
- Difference from `master` and deployed-only behavior: repository state gains only a dormant coordinator contract, implementation seam, tests, and documentation; deployed behavior is unchanged.
- Reconciliation decision: retain the cleanup/sweep path and replace no live provision route because none exists. The future executor must be the only provision authority.
- Previous known-good artifact and data-recovery boundary: no restore is submitted by this slice. Existing cleanup controls remain the recovery boundary for any previously created isolated restore set.

## Feature flag / release control (reversible user-visible behavior only)

- N/A: no user-visible behavior is released. `activation_ready` and every executor activation flag remain false; changing them requires a separate reviewed activation change.

## Schema/data sequence (schema/data work only)

- N/A: there is no schema or data change and no rehearsal is executed.

## Rollout and promotion

- Ordered hold points: validate exact policy hashes and closed-world shape → exercise the dependency seam with fakes → independent review → protected PR checks → merge dormant → separately provision and attest runtime/adapters → separately authorize one non-PHI live rehearsal.
- Health signals and abort thresholds: any policy/hash mismatch, repeated or unbounded input read, seal serialization, clock regression, caller-supplied restore/identity field, more than one provider/verifier/submit invocation, side effect before safe-submit, or output containing raw evidence is an immediate abort.
- Canary journey: the only permitted pre-activation journey is deterministic refusal before authority inputs or adapters. A live canary requires a later founder-authorized staging rehearsal.
- Founder-only manual production/PHI promotion record, or current control gap: production/PHI promotion is out of scope and remains prohibited. Founder authorization is required after staging evidence review.

## Security and recovery

- The public boundary accepts only absolute paths for the exact six-file bundle, one HMAC key, one offline attestation bundle, and one independently delivered GitHub-only root. Before those reads, the private seam validates an authenticated provision receipt, both receipt-derived runtime references, the launch record, the external runtime record, and their cross-binding. The launch record—not the caller—supplies the bounded TTL and expected digests. Files are opened once with no-follow and identity checks. The sealed snapshot retains exact result, manifest, and server-scope bytes; accepted digests bind the other claim files without reopening them. The exact key, attestation bundle, and root bytes remain until submit returns.
- A single process must pass the same validator-minted snapshot and server binding through server authentication, one Azure GET, one offline verifier invocation, evidence composition, deterministic request derivation, and one direct `SafeRestoreSubmitter.submit` call. Serialized reconstruction and job/step handoffs are forbidden.
- One shared timezone-aware, whole-second, non-regressing clock source is required. Composition owns one final freshness read after all receipt/digest checks; safe-submit performs its pinned immediate freshness and cleanup checks with the same source.
- Recovery move: keep the executor and workflow disabled. If a future adapter becomes ambiguous, safe-submit must reconcile or enter cleanup-only behavior under its pinned policy; the coordinator must not retry a second submit call.
- Observation window before irreversible cleanup: N/A here. Future isolated resources retain the envelope's six-hour default and 24-hour maximum TTL, plus the independent cleanup sweep.

## Completion

- Evidence required to call this dormant slice complete: exact raw SHA-256 pins for all eight dependent policies, JSON and diff checks, focused unit tests proving refusal and one-process/read/submit invariants, change-budget compliance, and independent review against the final commit.
- Follow-up owners: infrastructure must supply the exact private job, image, identity, subnet, network, evidence-delivery, verifier/root, marker-store, RBAC, WORM, and provider-fidelity evidence. Workflow integration and restore activation remain separate Tier-2 changes.
- Documentation and contract records to update: this flight plan, the server-scope evidence runbook, the synthetic rehearsal runbook, and `restore-trusted-executor-policy.json`.
