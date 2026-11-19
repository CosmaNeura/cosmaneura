# Reusable release verified candidate

Tier 1. Deployment/release control. No production dispatch in this change.

## Outcome

Add `Release verified candidate` so current master can be promoted after a
successful `Deploy Staging Candidate`, without reusing the one-time 27 Oct
`Release verified production` pin (4ff93182 + four control files +
`local_assertion_repair_verified`).

## Non-goals

- Do not dispatch production, Terraform Plan/Apply, or Azure mutation.
- Do not weaken Build & Publish, Trivy, required checks, or the master ruleset.
- Do not rewrite the frozen one-time workflow bytes.

## Gates retained

| Gate | Enforcement |
| --- | --- |
| Full tests | `scripts/verify_release_verified_candidate.py` requires every Deploy Staging Candidate job (all backend shards, quality, frontend, publish, deploy) succeeded for the candidate SHA |
| Trivy scan | Workflow step scans the built production image before publish; CRITICAL/HIGH fail the job |
| Candidate image digest pinning | Publish resolves and re-reads an immutable `sha256:` digest; `deploy.sh` receives SHA + digest ref |
| Production Plan / Apply clicks | `environment: production` approval plus verified `terraform_apply_run_id` ancestry/config gate |
| Founder / master / callback / dark inbox | Same actor, ref, callback, and durable-inbox locks as the 27 Oct path |

## Live-user effect of this PR

None. Merging only adds a manual workflow. No deploy runs from the PR.
