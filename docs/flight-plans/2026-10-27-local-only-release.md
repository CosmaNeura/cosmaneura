# Local-only acceptance and production execution

The PR-validation compute policy below was superseded on 2026-10-28 by owner
authorization to use standard GitHub-hosted Ubuntu runners for `Pull Request
Checks`, `Change budget`, and secretless `Terraform Plan` in this public repo.
Workflow enablement is separate. Production controls in this plan still apply.

Risk: Tier 2. Outcome: keep acceptance and PR validation local, with an explicit
owner-authorized hosted release fallback after local execution failure. Preserve
release authorization, validation prerequisites and evidence.

## Source and environment reconciliation

The seven reviewed CoachCoe PRs remain merged. Validation repair #843 was
independently reviewed at 9b1a4c6e7136f3fcd9181987308e20e13314d95b and merged.
Its full local backend acceptance passed 4,555 tests (1,626 existing skips);
independent frontend acceptance passed 839 tests and all quality checks.
Hosted CI was cancelled at the owner's request and is not described as passing.
The owner initially required zero hosted jobs, including release. After local
plan/apply succeeded, the owner authorized hosted execution if the next local
release step failed. The Docker image-pull readiness check failed on the shared
daemon DNS on 2026-10-27, so the final manual release uses that fallback.

Keep production data, selected database and credentials. Replace the three
core workload images only through the existing founder manual promotion.
Keep durable inbox, watchdog, sovereign authority and Stripe launch flags dark.
Preserve additive migration structures on recovery. Previously recorded
production image/schema/configuration drift remains the release baseline;
refresh it immediately before execution if production has changed.

## Execution controls

Plan/apply and disabled validation workflows require self-hosted Linux runners.
The final Build & Publish fallback uses hosted Ubuntu only when every job
receives explicit founder opt-in on exact master; its input defaults false.
Local production uses native ARM64 on the operator's Apple Silicon host; disabled validation
registrations retain their separate X64 label. The .NET runner cannot execute
reliably under QEMU, as confirmed by the first local connection attempt and
official actions/runner issue 4778. Native runner 2.338.2 and yq are checksum-pinned;
the same Nixpacks and 1Password versions select pinned ARM64 packages. Production
image recipes retain their explicit linux/amd64 target. The
three production workflows have new registration paths; the old
registrations remain permanently disabled so rerunning a historical hosted
snapshot cannot incur charges. The old files are removed; Git and disabled
workflow registrations retain the audit history.
Local production plan/apply use `cosmaneura-production-local`.
The other workflows use a separate validation label; do not register that label
on a machine with production-capable Docker access. PR validation workflows
remain disabled; agents run acceptance locally before one final push.

`scripts/local_release_runner.py` accepts only an active founder manual run at
exact current master. Its root-owned pre-job hook runs before checkout and admits
only those exact master workflow paths, founder actors, manual events and the
pinned target SHA, run ID and attempt.
Run-specific runner labels prevent another queued release consuming the worker.
Rejection kills the exact disposable container before an always()/failure() step
can execute; API failure also terminates listener/worker processes in its private
PID namespace. Cleanup validates the exact network and attached-project owners
before deleting test resources.
The target is stored in a root-owned mode-0444 file before the runner starts; job
environment values cannot replace it. Registration is repository-scoped and
requires no expansion of the existing GitHub token scope. Each checksum-verified
official runner accepts one job, then exits. Docker's init process reaps adopted
test descendants in the private process namespace. JIT credentials arrive through
stdin and the runner's consumed input environment variable. No operator home,
GitHub credential store or production configuration is mounted. The Docker
socket is privileged access to the local Docker daemon: never admit PR or
unmerged-branch code to this runner.

Local backend jobs use private per-runner Compose projects and internal networks,
with no published service ports. Cleanup removes only that project's resources.
Runner-only loopback forwarders preserve the database replay's existing local
endpoint guard; they never bind a host interface. Cleanup verifies each
forwarder's UID, session and exact command before terminating its process group.
Subnet claims rescan after a concurrent overlap and fail on unrelated errors.
The runner includes checksum-pinned yq for the existing deployment tests.
Local CI disables secret discovery through 1Password while retaining the normal
synthetic test configuration and existing assertions. Protected production jobs
remain separate, bind credentials at their existing steps, and retain the
founder/rerun actor, exact-master, Terraform attestation/apply, ancestry, callback,
current-master rechecks and production concurrency controls.

## Repository settings and security

Actions is enabled only for the three reviewed manual production registrations.
Source review, local admission and security controls were verified before activation.
The hosted Build & Publish fallback requires a false-by-default opt-in and both
founder actors on exact master before any job starts. Its matrix and backend
aggregate checks remain mandatory; test services stay on disposable hosted VMs. Disable all legacy/nonproduction workflow
registrations before enabling only the three reviewed manual production workflows.
Do not rerun historical hosted snapshots.

Preserve secret scanning, push protection, dependency records and existing
alerts. Generated CodeQL, Dependabot Updates and Dependency Graph jobs require
separate compute controls: recent CodeQL runs succeeded even though the default
setup API now reports not-configured while Actions is disabled. That API response
does not establish inactivity. GitHub supports labeled self-hosted Dependabot
runners; verify supported local routing before enabling an automated job.
Automatic approval review rejected pausing the three generated registrations
without explicit owner acceptance of the scan/update/submission gap. The owner
then approved that exact temporary pause on 2026-10-27. GitHub refuses to disable
dynamic registrations directly, so their underlying controls are paused instead:
CodeQL default setup and Code Quality are not-configured, automatic security
fixes are disabled, automatic dependency submission is saved as Disabled, and
the version-update schedule is retained at `.github/dependabot.paused.yml`.
GitHub reads only the canonical `.github/dependabot.yml` filename. Preserve all
alerts and dependency records. Restore that filename and enable automated jobs
only after their isolated local runner selection is verified; the preserved
ecosystem/grouping policy is unchanged. Do not weaken secret scanning or push
protection. The owner-authorized hosted fallback leaves these generated-job controls paused.
Restore automated jobs only after their compute routing is verified.

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
8. Dispatch Build & Publish manually with `github_hosted_fallback_authorized=true`
   only for the owner-authorized fallback. Its full acceptance prerequisites and
   production gates remain mandatory; automatic PR workflows stay disabled.
9. Verify running revision/image digest, schema and availability; refresh the
   reconciliation evidence. Remove ephemeral registrations and owned test services.

## Recovery

Keep Actions disabled if runner/security isolation cannot be established. A
reviewed forward revert can restore workflow source, but never re-enable hosted
execution beyond the explicit manual release fallback without the owner's new authorization. Preserve production data and
additive schema structures; recover workloads using the previously verified
image digest through the existing manual controls. Runner interruption removes
only uniquely owned local resources and its own JIT registration.
