# Flight plan: verify staging restore dispatcher federation through ARM

## Outcome and boundaries

- Problem and measurable outcome: replace the dormant restore controller's
  incorrect Microsoft Graph application/SP lookups with exact Azure Resource
  Manager reads of the UAMI resources Terraform actually creates.
- In scope: the legacy controller's identity preflight, provider-mocked tests,
  and documentation of the corrected authority boundary.
- Explicit non-goals: changing policy pins or activation bits, applying
  Terraform, changing Azure or GitHub configuration, dispatching a workflow,
  restoring or sanitizing data, staging cutover, or production promotion.
- Affected users, services, environments, and data classes: future operators
  of the isolated non-PHI restore rehearsal; no environment or data changes.

## Risk and dependencies

- Tier and reason: **Tier 2** because this is an identity/federation preflight
  on a future restore security boundary, although the workflow remains dormant.
- Irreversible or high-blast-radius decisions: none. Both operations are exact
  GETs and every activation gate remains closed.
- Dependency-ordered stack: trusted evidence/authenticators → dormant trusted
  executor → private runtime and provider-fidelity evidence → dispatcher OIDC
  proof/policy pinning → separately authorized live rehearsal.
- Cross-repo contract/version and receiver-before-sender order: N/A; this
  correction is repository-local and activates no sender.

## Current deployed state

- Protected `master` `b8e8ee06850c7215f002443c8dd97e33e6679918`
  creates two dispatcher UAMIs under
  `cosmaneura-staging-rehearsal-control`, each with one ARM child federated
  credential. The controller instead queried Graph application objects.
- Read-only Azure reconciliation on 2026-09-05 confirmed both exact UAMI
  parents in `eastus` and exactly one expected `github-staging-restore-*`
  credential each. The old Graph FIC command cannot resolve the provision UAMI
  client ID. No Azure or GitHub mutation was performed.
- Difference from `master`: code/tests/docs only. The envelope policy remains
  placeholder-blocked, `trusted_evidence_binding_implemented` remains `false`,
  and workflow provision remains a credentialless failing tombstone.
- Reconciliation decision: preserve the live UAMI model and correct the reader;
  do not add Graph permissions to disguise the identity-model mismatch.

## Feature flag / release control (reversible user-visible behavior only)

- N/A: this is a dormant security-boundary correction, not user-visible
  behavior. All activation controls remain false.

## Schema/data sequence (schema/data work only)

- N/A: no schema, migration, data, restore, or sanitizer operation occurs.

## Rollout and promotion

- Hold points: focused restore-envelope tests → full relevant infrastructure
  tests and static checks → final-commit independent review → protected PR
  checks → merge dormant → hold.
- Health signals and abort thresholds: reject a parent with any mismatched ID,
  name, type, location, tenant, client, or principal; reject a missing,
  malformed, paginated, additional, or mismatched credential child. Any Graph
  call, policy/activation change, mutation command, or cleanup dependency on
  later FIC/RBAC/deny drift is an abort.
- Canary journey: no live canary or workflow dispatch is permitted by this
  slice. Provider-mocked tests exercise success and every mismatch boundary.
- Founder-only manual production/PHI promotion: out of scope and prohibited.

## Security and recovery

- Parent and collection URLs are constructed from the pinned ARM origin,
  subscription, exact control resource group, policy-validated environment,
  and managed-identity API `2023-01-31`; callers cannot supply an endpoint.
- Provision requires the sole exact federation child before deny-policy checks
  or mutation. Cleanup continues to verify the exact authenticated UAMI parent
  and target group but intentionally does not block safe deletion on later
  FIC, RBAC, or deny-policy drift.
- The existing control-reader role already includes the two required UAMI and
  FIC read actions. No Graph permission, Terraform, or policy change is needed.
- Recovery: revert this repository-only correction. No live side effect or data
  recovery action exists for this slice.

## Completion

- Evidence required: tests prove exact parent and child bindings, no
  pagination/additional credential, no Graph command, and cleanup drift
  tolerance; the full focused suite and static checks pass; change budget is
  met; and an independent agent review is recorded against the final commit.
- Follow-up: before activation, prove the exact ARM reads from both dispatcher
  OIDC sessions, pin every remaining identity/role/deny value and policy hash,
  complete the private runtime/provider-fidelity gates, and obtain separate
  live-rehearsal authorization.
