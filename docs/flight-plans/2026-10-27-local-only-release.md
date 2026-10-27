# Local-only acceptance and production execution

Risk: Tier 2. Outcome: stop all GitHub-hosted compute, including production
plan/apply/build/deploy, while preserving release authorization and evidence.

## Source and environment reconciliation

The seven reviewed CoachCoe PRs remain merged. Validation repair #843 was
independently reviewed at 9b1a4c6e7136f3fcd9181987308e20e13314d95b and merged.
Its full local backend acceptance passed 4,555 tests (1,626 existing skips);
independent frontend acceptance passed 839 tests and all quality checks.
Hosted CI was cancelled at the owner's request and is not described as passing.
The owner explicitly requires zero hosted jobs, including release.

Keep production data, selected database and credentials. Replace the three
core workload images only through the existing founder manual promotion.
Keep durable inbox, watchdog, sovereign authority and Stripe launch flags dark.
Preserve additive migration structures on recovery. Previously recorded
production image/schema/configuration drift remains the release baseline;
refresh it immediately before execution if production has changed.

## Execution controls

All tracked workflows explicitly require self-hosted Linux X64 runners. The
three production workflows have new local-only registration paths; the old
registrations remain permanently disabled so rerunning a historical hosted
snapshot cannot incur charges. The old files are removed; Git and disabled workflow registrations retain the
audit history.
Only the three existing production workflows use `cosmaneura-production-local`.
The other workflows use a separate validation label; do not register that label
on a machine with production-capable Docker access. PR validation workflows
remain disabled; agents run acceptance locally before one final push.

`scripts/local_release_runner.py` accepts only an active founder manual run at
exact current master. Its root-owned pre-job hook runs before checkout and admits only those exact
master workflow paths, founder actors, manual events and the pinned target SHA, run ID and attempt.
Run-specific runner labels prevent another queued release consuming the worker.
Rejection kills the exact disposable container before an always()/failure() step
can execute; API failure also terminates listener/worker processes in its private
PID namespace. Cleanup validates the exact network and attached-project owners
before deleting test resources.
The target is stored in a root-owned0444 file before the runner starts; job
environment values cannot replace it. Registration is repository-scoped and
requires no expansion of the existing GitHub token scope. Each checksum-verified
official runner accepts one job, then exits. JIT credentials arrive through
stdin and the runner's consumed input environment variable. No operator home,
GitHub credential store or production configuration is mounted. The Docker
socket is privileged access to the local Docker daemon: never admit PR or
unmerged-branch code to this runner.

Backend jobs use private per-runner Compose projects and internal networks,
with no published service ports. Cleanup removes only that project's resources.
Local CI disables secret discovery through 1Password while retaining the normal
synthetic test configuration and existing assertions. Protected production jobs
remain separate, bind credentials at their existing steps, and retain the
founder/rerun actor, exact-master, Terraform attestation/apply, ancestry, callback,
current-master rechecks and production concurrency controls.

## Repository settings and security

Repository Actions stays disabled until source review, runner admission and
security runner settings are verified. Disable all legacy/nonproduction workflow
registrations before enabling only the three local manual production workflows.
Do not rerun historical hosted snapshots.

Preserve secret scanning, push protection, dependency graph, Dependabot alerts
and security updates. GitHub supports labeled self-hosted Dependabot runners;
configure that supported option before Actions is re-enabled. Default CodeQL is
currently not configured and automatic dependency submission is disabled.
Generated workflow names in the historical inventory do not prove active
scanning. Do not disable security features to get around the hosted-compute ban.
If a security runner setting is unavailable, leave Actions disabled and resolve
the exact control with the owner before release.

## Acceptance and promotion sequence

1. Finish local application, frontend and full standalone infrastructure acceptance.
2. Smoke-test the runner image and isolated service lifecycle locally. Verify no
   published ports and cleanup with another project's resources untouched.
3. Independently review the final pushed control commit; record findings in its PR.
4. Merge only after review. Verify the merged source and all runner labels.
5. Verify immutable runner admission and security runner settings. Use the existing
   repository-scoped credential; do not expand organization token permissions.
6. Start local ephemeral workers for a fresh Production Plan at merged master.
   Review its immutable attestation, then run Terraform Apply with that run ID
   and digest. Use neither stale preliminary-plan evidence nor fabricated run IDs.
7. Replay the owner-confirmed synthetic Recall `recording.done` event immediately
   before release; require a fresh real provider-signed HTTP200 receipt.
8. Dispatch the existing Build & Publish manual workflow. All jobs execute locally;
   its full acceptance prerequisites and production gates remain mandatory.
9. Verify running revision/image digest, schema and availability; refresh the
   reconciliation evidence. Remove ephemeral registrations and owned test services.

## Recovery

Keep Actions disabled if runner/security isolation cannot be established. A
reviewed forward revert can restore workflow source, but never re-enable hosted
execution without the owner's new authorization. Preserve production data and
additive schema structures; recover workloads using the previously verified
image digest through the existing manual controls. Runner interruption removes
only uniquely owned local resources and its own JIT registration.
