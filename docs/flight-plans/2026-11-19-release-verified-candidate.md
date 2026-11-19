# Reusable release verified candidate

Tier 1. Deployment/release control. No production dispatch in this change.

## Outcome

Add `Release verified candidate` so current master can be promoted after a
successful `Deploy Staging Candidate`. Retires the one-time 27 Oct
`production_release_verified.yml` pin (4ff93182 + four control files +
`local_assertion_repair_verified`), which cannot release current master.

Build & Publish remains a second production path; a follow-up ticket gates or
folds it. This change does not rewrite Build & Publish.

## Non-goals

- Do not dispatch production, Terraform Plan/Apply, or Azure mutation.
- Do not weaken Build & Publish, Trivy, required checks, or the master ruleset.
- Do not change environment settings, secrets, or Azure from this PR.

## Gates retained

| Gate | Enforcement |
| --- | --- |
| Full tests | `scripts/verify_release_verified_candidate.py` requires every Deploy Staging Candidate job succeeded and release/publication SHA256SUMS bind source_sha, digest, contract, smoke, and publication success |
| Trivy scan | `build-and-scan` scans the built production image before any registry login; CRITICAL/HIGH fail the job |
| Candidate image digest pinning | `release_verified_candidate_publish.py` publishes the scanned archive, resolves `@digest` (not a mutable tag), refuses an existing tag, and ACR-locks |
| Production Plan / Apply clicks | `environment: production` on `publish-and-deploy` plus verified `terraform_apply_run_id` ancestry/config gate |
| Founder / master / operator attestations / dark inbox | Same actor/ref locks; `github_hosted_fallback_authorized` and Recall callback inputs are operator attestations (not automated evidence hashes) |

## Runtime recovery

Before deploy, record app/worker/scheduler digests and revisions. After deploy,
check `app.cosmaneura.com/healthcheck` and each ready-revision digest. On
failure, roll back to the recorded digests and upload evidence. Migration
policy: image rollback restores prior digests/revisions only; Alembic
migrations applied by `deploy.sh` are forward-only.

## Live-user effect of this PR

None. Merging only adds a manual workflow and retires the unusable one-time
path. No deploy runs from the PR.
