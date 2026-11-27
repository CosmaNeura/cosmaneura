# Hosted production checks and Terraform admission

**The R2 production deploy must not proceed until #1019 (receipt gating) is merged.**
Plan, Apply, Release and build-and-publish can run without a receipt at this layer.

**Run production checks** is a JohnClem-only dispatch on `master`. Each dispatch must
be a new first attempt. It uses the existing Release OIDC identity and the
`production` environment, and shares the `cosmaneura-production-container-apps`
concurrency group with Plan, Apply and deployments, with `cancel-in-progress: false`.

## Candidate and receipt

The carrier verifies the actual dispatch SHA's two scripts and 97 migration files against
`cf67a63129426c558b84559618822a6b59e7cdba`. The one-day receipt records both SHAs and
execution provenance. Publication requires strict marker and G2 validation, one execution
with zero retries, and an unchanged template. The session imposes and checks read-only
operation despite the app credential's write privileges. Only fixed markers, aggregate
counts, booleans and reviewed revisions leave the session. Job execution readers can see
selector names and embedded source.

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

Before preflight, the workflow uses Entra token login to ACR, pulls the pinned digest
and probes the cached image without network or credential inputs. AcrPull remains
unverified until run. The probe checks `/opt/venv/bin/python`, Python 3.13,
psycopg 3.2.6 and libpq 17.4 or later. Record its result for digest
`877fe96e9b43297be2ac479ef939c5e47476229fc867062ad0fda2bd1a50fac6`.

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

Log Analytics readback polls the exact execution and container for 18 rows ending
in `RUN_DONE=1`, for up to 240 seconds. Incomplete, malformed,
duplicate and error markers fail validation. Log and execution queries use bounded
same-host HTTPS pagination. Logs and Azure diagnostics stay private.

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

After merging #1016 through #1019, separately dispatch checks, Plan, Apply and Release,
with required environment approvals. John reviews the Plan summary and supplies its run ID
and artifact-service digest for Apply. Release requires matching staging evidence, applicable
successful Apply, signed callback and hosted attestations, Trivy, and health and ready-digest
checks. Stop on skipped deploy or missing health evidence. Follow the rollback procedure
on an access-count drop or existing-user sign-in or redirect failure.
