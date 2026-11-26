# Hosted production checks and Terraform admission

CTO-18 moves production Plan and Apply to GitHub-hosted Ubuntu 24.04 using the
existing Azure OIDC identity and the `production` environment. The workflows
keep their existing dispatch paths, the separate reviewed Plan attestation, the
Apply state and digest comparisons, and the final Apply check of current
`master`. The local launcher remains in the repository.

**Run production checks** is an enabled JohnClem-only dispatch on `master`.
Each dispatch must be a new first attempt. The job uses the existing Release
OIDC identity and `production` environment. It serializes with Plan, Apply and
production deployments in `cosmaneura-production-container-apps`, with
`cancel-in-progress: false`. The runner processes live database inputs inside
Azure. Only fixed marker labels and codes, aggregate non-negative counts,
booleans and the reviewed migration revisions can leave the database session.
The published receipt carries operational provenance such as the GitHub run
and candidate IDs, image and execution name.

## Candidate and receipt

The reviewed input source is `cf67a63129426c558b84559618822a6b59e7cdba`.
On a new workflow head, the carrier builds the bundle from the Git objects at
the **actual dispatch SHA** after verifying both script pins and all 97
migration files against the reviewed manifest. The GitHub run, workflow and
receipt candidate SHA must agree. The receipt separately identifies the
reviewed source of the byte pins. Final-head review must cover this
reconciliation, the SQL and the carrier before any live dispatch.

Plan, Apply, **Release verified candidate** and the retained manual
prepare/promote workflow require the newest production-checks run for the exact
candidate to be a successful JohnClem first attempt on `master`, with both
jobs successful and one unexpired, digest-verified receipt. A pending or failed
newer checks run holds the candidate. A synthetic run, a copied receipt, an old
candidate and a missing or expired artifact all fail the gate. The GitHub
artifact retention requested by the workflow is one day. A receipt is only
created after the strict marker and G2 comparison, zero retry and
single-execution checks, and the unchanged-template check.

The app credential has write privileges. The DB session imposes and checks
read-only operation. Workflows and operators work with selector names and never
retrieve either credential value. The migration job creates one Azure execution
record and console output. The embedded source payloads and the selector name
are readable by principals with execution access.

## Before a live dispatch

- Record repository visibility and the effective readers and retention of
  workflow logs, summaries, artifacts, job execution records, Log Analytics and
  the Terraform backend, including any retained state copies. Confirm the
  production environment approval policy, reviewer eligibility and bypass
  settings. Confirm the applicable ruleset, required CI and Trivy results,
  staging owner and exact-candidate staging evidence.
- Record final-head review of the complete carrier and SQL. Independently verify
  the live federation, backend reachability from hosted x64, effective provider
  and backend permissions, and pinned setup and provider compatibility using
  approved evidence. Hold production until the evidence is recorded.
- Record runtime evidence for the exact traffic-serving image digest
  `877fe96e9b43297be2ac479ef939c5e47476229fc867062ad0fda2bd1a50fac6`.
  At the reviewed source, `.tool-versions` specifies Python 3.13.2,
  `pyproject.toml` requires Python 3.13 or later, and `uv.lock` pins both
  `psycopg` and `psycopg-binary` to 3.2.6. Run the synthetic probe against an
  already-cached copy of the exact OCI image. It checks the executable at
  `/opt/venv/bin/python`, Python 3.13, psycopg 3.2.6 and libpq 17.4 or later
  without network or credential inputs. Record the probe result for the digest.

```bash
python3 ops/production-checks/probe_image.py
```

## Failure and cleanup

The carrier verifies 100% of revision traffic, the exact image on every
positive-weight web revision, the same approved database selector, and a
complete execution listing with only terminal states. Before starting, it
requires manual parallelism and completion count of one, zero replica retry
and a known positive replica timeout. It hashes the names-only template
projection, including those settings, and checks it again after the execution.

The carrier calls start once. On a lost or invalid response, it compares
execution names with the before-start list and matches the unique invocation
marker embedded as whitespace in the compressed script bundle, along with
the exact image, command, arguments and environment names. It stops only the
positively identified execution if it is still nonterminal and checks for
terminal status. A timeout, unknown state or failed status query for an
identified execution also takes the execution-specific stop path. The
five-minute poll budget leaves a separate cleanup interval within the
15-minute Actions job. An unresolved start or stop requires human incident
review. Keep downstream production work held and obtain fresh John approval
before a new dispatch. An Actions rerun cannot start the job.

Completed logs are read through the existing production Log Analytics
workspace, scoped to the execution and container. This is also the readback
path when a completed replica no longer has a live stream. An incomplete,
malformed, duplicate or error marker fails validation. Logs and Azure CLI
diagnostics stay private. The Actions job publishes only the successful
structured receipt.

The Release identity needs existing job/production revision reads,
`Microsoft.App/jobs/start/action`, `Microsoft.App/jobs/executions/read` and
execution-specific `Microsoft.App/jobs/stop/action` on the job, plus access to
query the existing Log Analytics workspace and its console table
(`Microsoft.OperationalInsights/workspaces/query/read` and applicable table
query permission, such as
`Microsoft.OperationalInsights/workspaces/query/ContainerAppConsoleLogs_CL/read`).
The effective stop, execution metadata and log-query permissions are
**Unverified**. Verify them through existing grants and the authorized
rehearsal. Hold if a required grant or response shape cannot be verified.

## Staging rehearsal after push

The staging owner or authorized CI operator runs this after push from the
reviewed checkout, using a pre-existing synthetic staging job, staging digest,
staging credentials and the workspace already receiving that job’s logs. Its
manual template must use one replica, zero retries and a replica timeout above
40 seconds. Follow the existing approval procedure for staging execution.

```bash
python3 ops/production-checks/staging_rehearsal.py \
  --subscription "$STAGING_SUBSCRIPTION" \
  --resource-group "$STAGING_RESOURCE_GROUP" \
  --job "$STAGING_SYNTHETIC_JOB" --container "$STAGING_SYNTHETIC_CONTAINER" \
  --workspace "$STAGING_LOG_WORKSPACE" --image "$STAGING_IMAGE_DIGEST_REF"
```

The first override verifies the roughly 92 KB payload and recomputes the exact
97-file manifest within the container. The script checks template isolation,
reads the finished execution markers and confirms one execution. The second,
deliberately sleeping synthetic invocation exercises the execution-specific
timeout and terminal cleanup with a 40-second rehearsal poll budget. Both
invoke the same carrier logic and recheck template isolation. The output is
staging evidence only and the script emits no production receipt.

## Reviewing hosted Plan and completing Release

The hosted Plan runner processes production state and the ephemeral saved plan.
Plan and Apply suppress raw OpenTofu diagnostics. The renderer emits a
reviewable list of the operation, static source address and number of
instances, including drift. If those fields are insufficient to review a
change, stop and record what still needs reviewed evidence. The attestation
contains digests and state lineage/serial. Raw state, plan JSON, binaries and
diagnostic logs never enter an artifact or cache.

After an admitted Plan, John reviews the summary and dispatches Apply
separately with the successful Plan run ID and the artifact-service digest.
Release still requires the matching staging candidate, successful applicable
Apply, signed callback and hosted-execution attestations, Trivy and the
existing post-deploy health and ready-digest checks. A skipped deployment or
missing positive health evidence is a STOP. Under the reviewed post-deploy
procedure, any drop in the nine access categories or an existing-user
sign-in or redirect failure triggers the established rollback decision.
