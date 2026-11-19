"""Admit a staging-verified master candidate for reusable production release.

Replaces the one-time 27 Oct acceptance pin. Full tests are proven by a
successful Deploy Staging Candidate run for the exact candidate SHA; this
script never attests work it did not inspect.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

REPOSITORY = "CosmaNeura/cosmaneura"
STAGING_WORKFLOW = ".github/workflows/deploy_staging_candidate.yml"
STAGING_WORKFLOW_NAME = "Deploy Staging Candidate"
SHA = re.compile(r"[0-9a-f]{40}\Z")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
RUN_ID = re.compile(r"[1-9][0-9]{0,19}\Z")

# Keep in lockstep with deploy_staging_candidate.yml backend-tests matrix.
STAGING_SHARDS = (
    "application",
    "infrastructure-general",
    "infrastructure-controller",
    "infrastructure-receipt-io",
    "infrastructure-sanitizer-foundation",
    "infrastructure-sanitizer-source-state-authority",
    "infrastructure-sanitizer-source-state-context",
    "infrastructure-sanitizer-source-state-integrity",
    "infrastructure-sanitizer-evidence",
    "infrastructure-sanitizer-evidence-integrity",
    "infrastructure-sanitizer-executor",
    "infrastructure-sanitizer-observer",
    "infrastructure-sanitizer-observer-runtime",
    "infrastructure-sanitizer-observer-output",
    "infrastructure-sanitizer-observer-connection-plan",
    "infrastructure-sanitizer-observer-connection-plan-state",
    "infrastructure-sanitizer-observer-connection-plan-output",
    "infrastructure-sanitizer-observer-connection-plan-contract",
    "infrastructure-sanitizer-bootstrap-binding",
    "infrastructure-sanitizer-bootstrap-binding-digest",
    "infrastructure-sanitizer-bootstrap-binding-ancestry",
    "infrastructure-sanitizer-bootstrap-binding-state",
    "infrastructure-sanitizer-bootstrap-binding-output",
    "infrastructure-sanitizer-bootstrap-binding-duplicate",
    "infrastructure-sanitizer-bootstrap-binding-tamper",
    "infrastructure-sanitizer-bootstrap-binding-failure",
    "infrastructure-sanitizer-observer-integrity",
    "infrastructure-sanitizer-data-contract-executor",
    "infrastructure-sanitizer-source-snapshot",
    "infrastructure-restore",
    "integration",
)

REQUIRED_STAGING_JOBS = {
    "Authorize immutable staging candidate",
    "Candidate backend quality",
    "Candidate Backend",
    "Candidate Frontend",
    "Resolve reviewed public staging build configuration",
    "Build exact candidate without protected credentials",
    "Publish credential-free candidate archive",
    "Deploy exact candidate to persistent staging",
    "Record complete staging workflow attempt",
    *{f"Candidate backend tests ({shard})" for shard in STAGING_SHARDS},
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def output(args: list[str]) -> str:
    return subprocess.check_output(args, text=True).strip()


def api(path: str) -> dict:
    payload = json.loads(output(["gh", "api", path]))
    require(isinstance(payload, dict), "GitHub API response must be an object.")
    return payload


def verify_founder_and_source(candidate_sha: str) -> None:
    require(
        os.environ.get("GITHUB_REPOSITORY") == REPOSITORY, "Wrong release repository."
    )
    require(
        os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
        and os.environ.get("GITHUB_REF") == "refs/heads/master",
        "Release must be manual master.",
    )
    require(
        os.environ.get("GITHUB_ACTOR") == "JohnClem"
        and os.environ.get("GITHUB_TRIGGERING_ACTOR") == "JohnClem",
        "Release requires founder actors.",
    )
    target = os.environ.get("GITHUB_SHA", "")
    require(bool(SHA.fullmatch(candidate_sha)), "Invalid candidate SHA.")
    require(bool(SHA.fullmatch(target)), "Invalid release SHA.")
    require(candidate_sha == target, "Candidate SHA must equal this release SHA.")
    require(
        output(["git", "rev-parse", "HEAD"]) == target,
        "Checkout differs from release SHA.",
    )
    master = output(
        [
            "gh",
            "api",
            f"repos/{REPOSITORY}/git/ref/heads/master",
            "--jq",
            ".object.sha",
        ]
    )
    require(master == target, "Master advanced or candidate is not current master.")


def verify_staging_jobs(jobs_payload: dict) -> None:
    jobs = jobs_payload.get("jobs", [])
    require(
        jobs_payload.get("total_count") == len(REQUIRED_STAGING_JOBS) == len(jobs),
        "Missing or additional staging jobs.",
    )
    require(len({job.get("name") for job in jobs}) == len(jobs), "Duplicate jobs.")
    require(
        {job.get("name") for job in jobs} == REQUIRED_STAGING_JOBS,
        "Staging job names differ from the reviewed Deploy Staging Candidate matrix.",
    )
    for job in jobs:
        require(
            job.get("status") == "completed" and job.get("conclusion") == "success",
            f"Staging job incomplete or unsuccessful: {job.get('name')}.",
        )


def verify_staging_run(
    run: dict, artifacts: dict, jobs_payload: dict, candidate_sha: str, digest: str
) -> None:
    require(
        run.get("name") == STAGING_WORKFLOW_NAME
        and run.get("path") == STAGING_WORKFLOW
        and run.get("event") == "workflow_dispatch"
        and run.get("head_branch") == "master"
        and run.get("status") == "completed"
        and run.get("conclusion") == "success"
        and run.get("repository", {}).get("full_name") == REPOSITORY,
        "Staging run provenance differs.",
    )
    require(
        all(
            run.get(key, {}).get("login") == "JohnClem"
            for key in ("actor", "triggering_actor")
        ),
        "Wrong staging actor.",
    )
    require(bool(SHA.fullmatch(str(run.get("head_sha", "")))), "Invalid staging head.")
    # Control-plane head may differ from the candidate; the release artifact name
    # binds the exact candidate SHA that staging tested and deployed.
    attempt = run.get("run_attempt")
    require(isinstance(attempt, int) and attempt >= 1, "Invalid staging attempt.")
    expected_release = (
        f"canonical-staging-release-{candidate_sha}-{run['id']}-{attempt}"
    )
    expected_publication = (
        f"canonical-staging-publication-{candidate_sha}-{run['id']}-{attempt}"
    )
    names = {item.get("name") for item in artifacts.get("artifacts", [])}
    require(
        expected_release in names and expected_publication in names,
        "Staging artifacts do not bind the candidate SHA.",
    )
    verify_staging_jobs(jobs_payload)
    require(bool(DIGEST.fullmatch(digest)), "Invalid staging image digest.")
    # Digest is required as an explicit input so the founder names the staging
    # digest that was observed; artifact bytes are not trusted as a substitute.
    require(
        any(
            item.get("name") == expected_publication and item.get("expired") is False
            for item in artifacts.get("artifacts", [])
        ),
        "Staging publication artifact expired or missing.",
    )


def verify_staging_workflow_shards() -> None:
    source = Path(".github/workflows/deploy_staging_candidate.yml").read_text()
    shards = re.findall(r"^          - shard: ([a-z0-9-]+)$", source, re.MULTILINE)
    require(
        tuple(shards) == STAGING_SHARDS,
        "Verifier shard list drifted from Deploy Staging Candidate.",
    )


def main() -> None:
    candidate_sha = os.environ["CANDIDATE_SHA"]
    staging_run_id = os.environ["STAGING_RUN_ID"]
    staging_image_digest = os.environ["STAGING_IMAGE_DIGEST"]
    require(bool(RUN_ID.fullmatch(staging_run_id)), "Invalid staging run id.")
    verify_founder_and_source(candidate_sha)
    verify_staging_workflow_shards()
    run = api(f"repos/{REPOSITORY}/actions/runs/{staging_run_id}")
    jobs = api(
        f"repos/{REPOSITORY}/actions/runs/{staging_run_id}/jobs?per_page=100&filter=latest"
    )
    artifacts = api(f"repos/{REPOSITORY}/actions/runs/{staging_run_id}/artifacts")
    verify_staging_run(run, artifacts, jobs, candidate_sha, staging_image_digest)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as stream:
            stream.write(
                f"Staging run {staging_run_id} verified for candidate {candidate_sha}. "
                f"All {len(REQUIRED_STAGING_JOBS)} staging jobs succeeded, including "
                f"full backend shards, frontend, quality, publish and deploy. "
                f"Named staging digest {staging_image_digest}. "
                "Production image digest is pinned separately after the production build.\n"
            )
    print(
        "Staging-verified candidate admitted; full staging tests inspected, not re-attested."
    )


if __name__ == "__main__":
    main()
