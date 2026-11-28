# Hosted production checks and Terraform admission

**#1019 gates the first production release that contains the reviewed R2 commit.**
Release and Build & Publish require a receipt when the exact master tip contains
`cf67a63129426c558b84559618822a6b59e7cdba` and the running production source revision
does not. Ancestry includes the commit itself and applies to merge commits.

**Run production checks** is a founder-only dispatch on `master` (the single account
named in the workflow's `if:` condition). Each dispatch must be a new first attempt.
It uses the existing Release OIDC identity and the `production` environment, and
shares the production deployment concurrency group with Plan, Apply and
deployments, with `cancel-in-progress: false`.

## Candidate and receipt

The carrier verifies the actual dispatch SHA's two scripts and 97 migration files against
`cf67a63129426c558b84559618822a6b59e7cdba`. The one-day receipt records both SHAs and
execution provenance. Publication requires strict marker and G2 validation, one execution
with zero retries, and an unchanged template. The session imposes and checks read-only
operation despite the app credential's write privileges. Only fixed markers, aggregate
counts, booleans and reviewed revisions leave the session. Job execution readers can see
selector names and embedded source.

## After the first release

Once the running revision contains the reviewed commit, later releases and
revert-commit rollbacks follow the existing release path without a receipt.
Automatic image rollback keeps its existing path and has no receipt dependency.

Before the first successful release, a receipt is required and checks or
admission cannot provide a usable receipt in these cases:

- **(a)** A new master tip contains the reviewed R2 commit, including a
  fix-forward or revert, while the running image predates that commit and
  migrations have run. The pre-migration G2 shape no longer matches.
- **(b)** The same tip's receipt artifact has expired after one day. After
  migrations run, checks cannot produce a replacement.
- **(c)** A later Run production checks dispatch on the same SHA supersedes
  the receipt. Admission reads only the newest run. After migrations run, that
  run cannot produce a replacement.
- **(d)** A new migration file lands on master before the first release. The
  candidate no longer matches the pinned 97-file manifest.
- **(e)** A partial deploy leaves the three apps reporting different revisions.
  The running-revision read fails before admission can verify a receipt.

The only exit in each case is a reviewed PR to master. For (a) to (d) it removes
or repins the receipt step. For (e) it must also remove the running-revision read
from Build & Publish, because that read fails before the receipt step runs.
Release stays refused by its existing schema admission until the three apps
report one revision. No ungated manual path exists.

Dispatch Run production checks on the final merged master tip. From that
dispatch until Release succeeds, make no merges and add no new migrations on
master. Run checks and Release on the same day. Never re-dispatch checks after
Release has started. Plan and Apply retain their exact-tip admission and do
not gate on a receipt because they supply no running revision file.

**Retire.** After the first successful release, a follow-up PR removes the
receipt steps and the running-revision read from Build & Publish.

The transition check requires a full-history checkout. If ancestry cannot be
established, or if the candidate contains R2 and the running SHA file is invalid
or names an unknown commit, admission requires a receipt. Release and Build &
Publish check after login and after the build, or after reauthenticating the
immutable candidate on the promote path. The check still precedes publish and
deploy. A missing first-release receipt therefore stops the run after the build.

The running SHA comes from the `org.opencontainers.image.revision` label on the
three live apps' images, read using Azure Container Apps and Docker. The live
labels are unverified. Release already refuses at schema admission if it
cannot read the revision, and Build & Publish
would refuse at its new read step. The new read mode and its placement in
prepare and promote have only offline verification. Whether production
environment approvals add manual steps is unverified.

## Before a live dispatch

- Record repository visibility and effective readers and retention for logs,
  summaries, artifacts, execution records, Log Analytics and Terraform state and
  copies. Confirm production approval, reviewer eligibility and bypass settings.
- Record final-head review of the carrier, SQL and candidate pins. Confirm the
  ruleset, required CI and Trivy results, staging owner and exact-candidate staging
  evidence. Verify live federation, hosted x64 backend reachability, provider and
  backend permissions, and pinned setup and provider compatibility before dispatch.
- Review existing production grants for job and revision reads,
  `Microsoft.App/jobs/start/action`, `Microsoft.App/jobs/executions/read`,
  execution-scoped `Microsoft.App/jobs/stop/action`, and the existing workspace
  and console table query permissions. The rehearsal tests staging stop rights.
- Record a real staging rehearsal result before the first production checks dispatch.

## Runtime and execution checks

Before preflight, the workflow uses Entra token login to ACR, enables the Docker
containerd image store on the hosted runner (so digests deeper than overlay2's
layer limit can unpack), pulls the pinned digest and probes the cached image
without network or credential inputs. AcrPull remains unverified until run. The
probe checks `/opt/venv/bin/python`, Python 3.13, psycopg 3.2.6 and libpq 17.4 or
later. Record its result for the pinned image digest (`contract.IMAGE` in
`ops/production-checks/contract.py`).

The carrier verifies the image and one approved selector across all positive traffic
weights totaling 100%. It requires a manual job with one replica and completion,
zero retries, a positive replica timeout and terminal prior executions. It hashes
the names-only template before and after. Preflight lists executions, shows the newest
when present and queries a five-minute container count. It fails closed and reports
only preflight booleans.

The carrier starts once, with a 60-second call timeout within the 300-second budget.
After a lost response it matches a new execution by the unique bundle marker, image,
command, arguments and environment names. Lost start responses, timeouts, unknown
states and failed status reads trigger cleanup scoped to the positively identified
execution, with 60 seconds for terminal confirmation. An unresolved execution fails
the run. A GitHub rerun cannot start the job.

An unresolved execution incident blocks later production-checks dispatches. Once a
human verifies and reconciles the execution, record its GitHub run ID and a nonempty
reconciliation result in `ops/production-checks/incident-clearances.json` on
`master`. The file is a JSON object keyed by run ID, with a short text result for
each cleared run. Keep sensitive diagnostics out of the record.

Log Analytics readback polls the exact execution and container for 18 rows ending
in `RUN_DONE=1`, for up to 240 seconds. A transient `azure_command_failed` on that
readback is retried inside the same 240-second budget; a persistent failure still
stops with `azure_command_failed` once the budget is spent. The final execution
list uses the same rule inside its existing 60-second budget. Incomplete, malformed,
duplicate and error markers fail validation. Log and execution queries use bounded
same-host HTTPS pagination. Logs and Azure diagnostics stay private. A final
`azure_command_failed` stop names only the command class (for example `readback`
or `list`) with no ids, names, or CLI diagnostics.

The checks job caps the steps before the carrier at 15 minutes total. Checkout,
admission, package verification, Azure login, ACR login, pull, probe and the final
admission check get 2 + 1 + 1 + 2 + 2 + 4 + 2 + 1 minutes, respectively. The carrier
budget includes 15 + 15 seconds for selector and template reads, 60 + 15 + 60 for
preflight, 15 for the fresh-master lookup, 300 for start and poll, 60 for stop and
confirmation plus up to 18 for the last status read and sleep, 240 for log readback,
60 for the final execution list and 15 for the template recheck. The job budgets
900 + 873 = 1,773 seconds through the carrier, leaving 87 seconds in the 31-minute
job for receipt upload and job overhead.

## Staging rehearsal after push

With staging approval, use a pre-existing synthetic job, staging image and log
workspace. The subscription may be shared. The job must use one replica, zero retries
and a replica timeout above 40 seconds.

```bash
python3 ops/production-checks/probe_image.py --image "$STAGING_IMAGE_DIGEST_REF"
python3 ops/production-checks/staging_rehearsal.py \
  --subscription "$STAGING_SUBSCRIPTION" --resource-group "$STAGING_RESOURCE_GROUP" \
  --job "$STAGING_SYNTHETIC_JOB" --container "$STAGING_SYNTHETIC_CONTAINER" \
  --workspace "$STAGING_LOG_WORKSPACE" --image "$STAGING_IMAGE_DIGEST_REF"
```

The first execution verifies the 92 KB payload, 97-file manifest and exact 18-line burst
readback. The second exercises scoped stop and terminal confirmation after 40 seconds.
Both check template isolation. The `DATABASE_URL` secret-ref override and production
serving-selector check remain unverified by the rehearsal.

## Reviewing hosted Plan and completing Release

Hosted Plan and Apply use Ubuntu 24.04. Plan emits operation, static source address and
count, including drift. Stop if these are insufficient for review. Apply retains digest,
state and final master checks. Artifacts and caches exclude raw state, plans and diagnostics.

The sequence is four merges (#1016, #1017, #1018 and #1019) and four dispatches
(checks, Plan, Apply and Release), plus any required production environment approvals.
John reviews the Plan summary and supplies its run ID and artifact-service digest for
the Apply dispatch. Release requires matching staging evidence, applicable
successful Apply, signed callback and hosted attestations, Trivy, and health and ready-digest
checks. Stop on skipped deploy or missing health evidence. Follow the rollback procedure
on an access-count drop or existing-user sign-in or redirect failure.
