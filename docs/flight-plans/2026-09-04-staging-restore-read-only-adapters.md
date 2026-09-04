# Flight plan: dormant read-only staging restore adapters

## Outcome and boundaries

- Problem and measurable outcome: provide the two missing read-only runtime
  adapters needed by the trusted executor: one exact, bounded Azure Resource
  Manager GET using the job's managed identity, and one bounded invocation of
  the digest-pinned GitHub CLI offline attestation verifier.
- In scope: adapter implementations, dependency injection for unit tests, strict
  target/input validation, bounded response/process handling, tests, and docs.
- Explicit non-goals: binding adapters into the public executor, changing an
  activation flag or policy pin, provisioning a private runner, granting an
  identity, enabling network access, invoking Azure, dispatching a workflow,
  restoring or sanitizing data, staging cutover, or production promotion.
- Affected users, services, environments, and data classes: future operators of
  the isolated non-PHI rehearsal; no environment or data is changed.

## Risk and dependencies

- Tier and reason: **Tier 2** because the adapters cross managed-identity and
  signed-provenance boundaries before a future restore authorization is minted.
- Irreversible/high-blast-radius decisions: none. The adapter permits one GET of
  the pinned server; the verifier receives one exact three-file offline bundle;
  the public executor and every policy remain disabled.
- Dependency-ordered PR stack: trusted evidence/authenticators → dormant
  executor → read-only adapters → safe-submit/identity hardening → private
  runtime binding → separately authorized live rehearsal.
- Cross-repo contract/version and receiver-before-sender order: N/A; no sender or
  workflow is activated here.

## Current deployed state

- Live evidence: none collected or changed. `master` at `6c2c7240` contains the
  dormant executor; provision remains a credentialless failing tombstone.
- Difference from `master` and deployed-only behavior: repository state gains
  reusable adapter code and tests only. No runtime calls the adapters.
- Reconciliation decision: retain the disabled workflow and all existing
  cleanup controls. Do not reconcile or mutate live Azure state in this PR.
- Previous known-good/recovery boundary: no artifact is published and no restore
  request is submitted.

## Feature flag / release control (reversible user-visible behavior only)

- N/A: this is a dormant security boundary, not user-visible behavior. Every
  activation bit remains false.

## Schema/data sequence (schema/data work only)

- N/A: no schema, data, migration, restore, or sanitizer operation occurs.

## Rollout and promotion

- Ordered hold points: focused adapter tests → full staging restore suite →
  lint/type checks → independent review of the final commit → protected PR
  checks → merge dormant. Hold after merge.
- Health signals and abort thresholds: any redirect, unexpected endpoint,
  caller-selected resource, second ARM request, oversized or malformed body,
  inherited credential-bearing environment, unpinned verifier or executable
  mutation during its snapshot, unexpected verifier arguments/files, timeout,
  or output over the configured bound is an immediate refusal.
- Canary journey: no live canary is permitted by this slice. Tests use local
  fakes and isolated temporary files only.
- Founder-only manual production/PHI promotion record, or current control gap:
  production and PHI promotion remain out of scope and prohibited.

## Security and recovery

- The ARM adapter reads the Container Apps managed-identity endpoint only from
  a local or link-local address, sends its secret header only to that endpoint,
  requests a token for the exact ARM audience and one caller-supplied lowercase
  client ID, and binds the response to that ID. A later integration slice must
  bind that argument to the runtime policy pin. It refuses redirects, requires
  a single-threaded POSIX executor for its hard whole-exchange deadline, loads
  only the fixed immutable-image CA bundle with TLS key logging disabled, and
  performs one bounded raw-byte GET of the exact source server.
- The offline verifier accepts only the fixed argument/file vocabulary emitted
  by the authenticator, copies an absolute regular root/current-owned Linux
  binary with no group/world write access into a digest-checked sealed
  in-memory file and executes those exact bytes, uses an isolated working
  directory and scrubbed environment, refuses process
  timeouts/output overflow, and never enables a network-backed bundle or trust
  source.
- Network isolation is still a required runtime control; process flags and a
  scrubbed environment are defense in depth, not a substitute for the future
  private-runner egress policy and PID/cgroup containment.
- Recovery move: keep the executor and policies disabled or revert this
  code-only PR. There is no live side effect to undo.

## Completion

- Evidence required to call this slice complete: tests prove exact target,
  audience, identity, redirect, request-count, response-size, file-set,
  executable identity/digest, environment, timeout, and output bounds; the
  full staging restore suite and static checks pass; change budget is met; and
  an independent agent review is recorded against the final commit.
- Follow-up owners: a separate Tier-2 change must split dispatcher and job
  identities, replace the self-referential runtime-image pin with an external
  immutable runtime binding, harden safe-submit token/redirect behavior, and
  provision and attest the private runner before workflow integration.
- Documentation and contract records to update: this flight plan and the
  staging server-scope/synthetic-rehearsal runbooks. No policy hash changes in
  this PR.
