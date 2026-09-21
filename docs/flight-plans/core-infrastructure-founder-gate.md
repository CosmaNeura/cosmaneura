# Flight plan: founder-gate Core production infrastructure

## Outcome and boundaries

- Problem and measurable outcome: the production application deploy is
  founder-gated, but the production Terraform plan and apply workflows are not.
  After this change, both workflows refuse an original or rerun actor other
  than `JohnClem` before binding the `production` environment.
- In scope: GitHub workflow authorization, regression tests, and the operator
  runbook.
- Explicit non-goals: running a production plan/apply/deploy, changing GitHub
  approval settings, adding an environment reviewer, or changing Terraform.
- Affected surfaces: the Core GitHub Actions control plane and access to
  production state, identity, and secrets. No application or patient data is
  changed by this PR.

## Risk and dependencies

- Tier: Tier 2 because this changes production infrastructure control.
- High-blast-radius decision: founder-only manual self-authorization is the
  approved control while the private repository remains on its current plan.
- This is one standalone containment PR and can remain unmerged safely; until
  it lands, collaborators with workflow-dispatch access remain able to start
  production infrastructure workflows.
- Cross-repo ordering: N/A; no contract changes.

## Current deployed state

- Baseline source: `master` at `9a298e6565f45363a700190feedcd2e1cf6870c8`.
- Current gap: exact-`master`, reviewed-plan, digest, state-identity, and
  concurrency gates exist, but actor and rerun-actor gates do not.
- Configuration drift verified 2026-08-25: repository-scoped Azure identity
  variables duplicate the intended environment variables, and an unused
  generic `OP_SERVICE_ACCOUNT_TOKEN` repository secret remains. This PR does
  not remove credentials before their consumers are inventoried.
- Reconciliation decision: assimilate this source-only control on merge. Do
  not run a production workflow merely to exercise it.
- Previous known-good artifact and data-recovery boundary: N/A; this change
  neither builds an artifact nor mutates infrastructure or data.

## Conditional controls

- Feature flag: N/A; authorization is a security boundary, not reversible
  user-visible behavior.
- Schema/data sequence and PHI-safe rehearsal: N/A; no schema or data change.

## Rollout and promotion

1. Verify both workflows parse and the focused policy tests pass.
2. Obtain independent agent review of the final commit.
3. Merge through the existing PR process. The gate becomes effective from
   `master`; do not dispatch a production plan, apply, or deployment.
- Abort threshold: any path that binds the `production` environment without a
  successful founder-authorization dependency blocks merge.
- Founder production record: N/A; this PR changes source controls only and
  authorizes no production mutation.

## Security and recovery

- No new credential or security acceptance is introduced. No required
  environment reviewer is claimed.
- The current `master` ruleset blocks force pushes but does not require PR-only
  updates, so workflow self-modification remains a current-process residual.
  This PR preserves the approved GitHub review settings.
- Recovery: revert through a reviewed PR. Do not bypass the gate during an
  incident; establish a separately reviewed emergency procedure instead.
- Observation window: confirm the merged workflow graph and branch checks;
  no destructive cleanup follows.

## Completion

- Both production infrastructure jobs depend on a no-permission authorization
  job that validates manual event, exact `master`, original actor, and rerun
  actor.
- Focused tests and independent exact-commit review pass, and the runbook no
  longer claims automatic production deploys or a configured reviewer.
- Follow-up: inventory consumers and separately remove the legacy repository
  Azure variables and generic 1Password secret; revisit PR-only workflow
  protection with the broader approval-process review.
