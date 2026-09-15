# CI cost controls and runner-independent validation

Tier 2: changes CI execution and, separately, the status-provider trust boundary.
User requested this work and then explicitly requested changing GitHub
requirements after enabling an Actions spending stop. Do not merge this PR
without the user's merge instruction. No deployment or infrastructure changes.

## Code change

- PR backend workers: 31 -> 4 (application, integration, infrastructure 1/2).
- Infrastructure files use a stable SHA-256 partition, cover new files
  automatically, and run under their own fixture boundary with pytest-xdist.
  Application/DB/browser fixtures and serial recipes are unchanged.
- Required Backend/Frontend aggregators always report, accept only a validated
  intentional path skip or successful prerequisites, and fail on filter errors.
- Pinned dorny paths-filter uses checked-out Git without a PR-read token.
  Relevant infrastructure/scripts/contracts, build inputs and policy documents
  are included. Ordinary flight plans and README-only changes skip heavy jobs.
- The complete suite remains selected on every master push and manual dispatch,
  independent of paths. Actual execution still requires available compute.
- Build & Publish (manual) becomes dispatch-only; production gates are untouched.
- New uv/download caches are opt-in for secretless checks, respect the existing
  cache-off switch, and do not add caching to protected deployment jobs.
- Every workflow job has a finite timeout. The three excluded production
  workflow files were already bounded and remain byte-for-byte unchanged.
- Collection-only hooks no longer start DB cleanup/migrations or a JS build.
- No Docker layer cache: `just up --fast` pulls images, it does not build them.

## Evidence and cost expectations

GitHub metadata for September 1-15: all 144 runs of the nominally manual
workflow were first attempts: 143 master pushes and one staging dispatch.
There is no workflow_call/workflow_run/repository_dispatch caller. Pushes ran
validation; they did not authorize production deployment.

Successful run 34910531582 had 34 jobs, 370.02 aggregate job-runtime minutes
(386 if each job is rounded up). Its setup steps were usually about a minute,
not ten. The user's earlier period-wide estimate was approximately 160 minutes
per PR push; it is not interchangeable with this recent sample.

| Checks workflow | Before | After |
| --- | ---: | ---: |
| Full/relevant PR | 34 jobs | 9 jobs |
| Backend-only inputs | 34 jobs | 8 jobs |
| Frontend-only inputs | 34 jobs | 4 jobs |
| README/ordinary flight plan only | 34 jobs | 3 jobs |
| Backend test workers | 31 | 4 |

The separate change-budget job adds one to each PR total. Infrastructure-specific
OpenTofu checks are unchanged and additional where applicable. Web changes also
run backend tests because those recipes build/use web assets.

Docs-only work should take about 3-5 rounded runner-minutes instead of 160-386.
For full runs, 20-80 minutes is a target range, not a measured result or an 80%
savings claim. Consolidation amortizes repeated module fixtures, omits unrelated
DB initialization in offline checks, and adds local CPU parallelism. Actual
full-run cost and the 30-minute worker bound must be measured before acceptance.

Actual `pytest --collect-only -q` comparison: 29 old infrastructure selectors
and the two new selectors have identical 3,913-node unions, zero loss or
duplication. This includes 3,824 pre-existing cases and 89 new cost guards.
Application (1,399) and integration (10) selections/recipes are unchanged.
Their IDs were collected with normal conftests and parametrization, sockets
disabled, a collection-only service-version adapter, inert configuration and
empty static-asset directories; this is NOT runtime acceptance of either suite.
No production settings or test-execution workflow loads that adapter.

All 14 workflow/action/filter YAML documents parse. Actionlint passes changed
workflows and all workflow structural checks. Full actionlint also reports 11
pre-existing ShellCheck diagnostics in untouched deploy_staging_candidate.yml
and infra_apply.yml; do not silently change excluded deployment files to hide them.
Local cost guards, existing sharding/host-package guards and static checks are
recorded in PR evidence. No unchanged application/frontend suite is replayed.

## Approved direction: remove the paid-runner dependency, not validation

Current master requires Backend, Frontend and change-budget from GitHub Actions
app 15368 only. The active force-push ruleset also requires change-budget from
that app. An exhausted Actions budget therefore blocks otherwise valid PRs.

Proposed live configuration change (record exact readback separately): keep all
three contexts and strict/up-to-date enforcement, but remove only the Actions
issuer binding in both classic branch protection and the applicable ruleset.
Keep PR-only merges, stale-review dismissal, existing human approval count,
conversation resolution, admin enforcement, no deletion, no force push, ruleset
scope/enforcement and its empty bypass list unchanged. Grant no new credentials.

This permits identities ALREADY authorized to write commit statuses to report
local results. It does not cryptographically attest local execution and is a
deliberate change in reporter trust. Never publish a fabricated green result.
GitHub Actions remains a valid reporter when budget permits; the spending stop
must not be disabled, bypassed or shifted to an unapproved paid service.

Before reporting local success, record the exact clean head, current master/base,
changed scope, commands and exit codes, applicable test results and limitations
in the PR. Reuse earlier passing tests only with verified unchanged relevant
source/dependencies and explicit provenance. Report failure/pending where actual
evidence is missing. Check the source again before publishing; changes invalidate
the result. Enforce the existing 2,000-line/50-file budget from the actual PR diff.

Use the established codex-auto-review endpoint for independent review after the
final substantive source push. Record the exact reviewed head and findings;
review is not a substitute for executing tests or human merge authorization.
Do not make hosted execution a prerequisite for submitting that review.

## Rollout and recovery

Keep work local while spending is stopped. A feature-branch push does not start
these workflows; PR creation may enqueue jobs even for a draft. Do not start
paid runs or publish false statuses to get this change merged. Existing runs
do not adopt new workflow YAML. Other workstreams must incorporate the merged
master commit before their next PR push; no cross-task messages are authorized.

The provider-only rules change, once independently reviewed and applied, affects
all PRs targeting master immediately. The workflow savings apply only after
merge and branch synchronization. No global workflow-disable or budget increase.

Rollback the source through a normal revert PR. Restore app/integration ID 15368
on the same required contexts if reverting the status-provider policy, preserving
every other protection. That reinstates the hosted-check dependency and may block
merges until budget is available. Source/reporter changes are not deployment.
