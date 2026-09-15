# Actions-off operating policy and bounded manual workflows

Tier 2: CI/deployment workflow controls and the status-provider trust boundary.
The user explicitly requested Actions-off operation and runner-independent PR
validation. The user then chose to defer the inactive test-shard optimization
and return to staging sooner. Do not merge without the user's merge instruction.
This change neither deploys nor authorizes infrastructure or database mutation.

## Narrow source change

- Remove the `master`/`staging` push trigger from Build & Publish (manual).
  Keep manual dispatch, founder/ref/environment gates and deployment steps.
- Cap change-budget at five minutes; restore authorization at five minutes and
  restore cleanup at fifteen minutes. The disabled provision job stays at one.
- Add regression coverage for dispatch-only registration and finite job limits.
- Record the independently reviewed, user-authorized GitHub operating policy.

PR checks, all test recipes/selectors, application and integration conftests,
common setup, dependencies and lockfile are identical to protected-master base
`16ad15538b218f9e5bfd9cb4a5970cbfaef90a82`. Existing test cases are retained;
only obsolete push-trigger assertions are changed, with timeout coverage added.
The excluded `infra_apply.yml`, `infra_production_plan.yml` and
`deploy_staging_candidate.yml` remain byte-for-byte unchanged.

Review 38 of the former head `b0075b4254050a1f2e95b0953805f522dd54952f`
returned CODE FAIL / POLICY PASS. Its P1 required full execution of both new
infrastructure shards, or separately reviewed deferral. This PR removes that
execution change; it does not waive the finding or claim the shards passed.
The complete optimization is preserved on local branch
`codex/ci-cost-optimization-deferred-20260915` at that former head. Path filters,
cache changes, pytest-xdist, collection-hook changes and shard regrouping are
all deferred. Future adoption requires exact review and full runtime validation.

## Live Actions-off policy

Repository Actions is disabled (`enabled: false`), read back through GitHub's
API. This already prevents new hosted workflow runs independently of PR merge.
It remains off until explicit user authorization after platform stabilization
and major feature rollout, not merely until a spending-limit reset.

Before disabling, no workflow was running and the latest five scheduled restore
cleanup runs were skipped. The cleanup-enable variable was absent from repository
variables; organization-variable access was denied and no extra scope requested.
This is NOT proof that all Azure resources have been cleaned up. Any cleanup
need must be separately inspected and handled under existing human-only gates.

GitHub remains the source/PR host. Existing running Azure applications are not
changed. Actions-based CI, deployments, attestations and scheduled cleanup cannot
be relied on while Actions is off. No alternate paid runner, budget increase,
deployment mechanism or new permission is authorized by this correction.

## Required checks without an Actions-only issuer

The approved live configuration removes only the Actions issuer binding
(`app_id`/`integration_id` 15368) from classic master protection and its active
required-status-check ruleset. Backend, Frontend and change-budget remain
required, strict and up to date. Before/after API evidence verifies unchanged
PR review settings, stale-review dismissal, admin enforcement, conversation
resolution, no force push/deletion, ruleset scope and empty bypass list.

Already-authorized status writers may now publish genuine local results. No
token, app, role or scope is added. This intentionally broadens trusted reporter
identity; it does not cryptographically attest local execution. Never fabricate
success, overwrite a genuine failure with unrelated evidence, or count agent
review as test execution. Same-name check-run/status collisions must be checked;
an existing pending or failing Actions check may still prevent a merge.

For every local report, record the clean exact head, current protected base,
changed scope, commands, exit codes, results and limitations in durable PR
evidence. Use proportionate affected-scope tests. Reuse a previous passing test
only after proving relevant source/dependencies unchanged and linking its exact
run/head. Report pending or failure if evidence is missing. Recheck source before
publishing statuses. Compute change-budget from the actual PR diff, retaining
the existing 2,000-changed-line and 50-file thresholds.

Use `codex-auto-review` for independent review after the final substantive push.
Record exact source, findings and dispositions. Review is neither test execution
nor GitHub approval, merge authorization or deployment authorization.

## Verification, rollout and recovery

Run the changed dispatch/timeout regression tests, affected existing workflow
guards and actionlint on changed workflows. Prove deferred execution/configuration
files match the protected base. Do not rerun unchanged passing full suites.
The final PR evidence records the exact commands, results and reuse provenance.

Historical metadata showed 143 master-push runs and one staging dispatch of the
nominally manual workflow during September 1-15. Removing its push trigger avoids
that duplicate validation if Actions is later re-enabled. No measured 80-percent
runtime-efficiency improvement is claimed; the optimization is deferred.

The live settings apply to other PRs immediately. Source changes reach other
branches through normal incorporation of merged master; no cross-task messages
or automatic rewriting of other worktrees are authorized.

Recover source through a normal revert PR. Restoring issuer ID 15368 on the same
required contexts restores the Actions-only dependency and may block merges
while Actions is disabled; preserve every other protection. Re-enabling Actions
requires explicit user instruction and prior review of source, cost controls and
deployment/cleanup dependencies. A source revert does not re-enable Actions.
