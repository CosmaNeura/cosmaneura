"""Admit a staging-verified master candidate for reusable production release.

Full tests are proven by a successful Deploy Staging Candidate run for the
exact candidate SHA. Staging release/publication artifacts (SHA256SUMS) bind
source_sha, image digest, contract, smoke, and publication success. This
script never attests work it did not inspect.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import subprocess
import zipfile
from pathlib import Path
from typing import Any

REPOSITORY = "CosmaNeura/cosmaneura"
STAGING_WORKFLOW = ".github/workflows/deploy_staging_candidate.yml"
STAGING_WORKFLOW_NAME = "Deploy Staging Candidate"
SHA = re.compile(r"[0-9a-f]{40}\Z")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
RUN_ID = re.compile(r"[1-9][0-9]{0,19}\Z")

STAGING_CONTROL_PATHS = (
    ".github/workflows/deploy_staging_candidate.yml",
    "scripts/verify_staging_candidate.py",
    "scripts/staging_release_contract.py",
    "infra-staging",
)

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


def api(path: str) -> dict[str, Any]:
    payload = json.loads(output(["gh", "api", path]))
    require(isinstance(payload, dict), "GitHub API response must be an object.")
    return payload


def require_candidate_sha(candidate_sha: str, github_sha: str) -> None:
    require(bool(SHA.fullmatch(candidate_sha)), "Invalid candidate SHA.")
    require(bool(SHA.fullmatch(github_sha)), "Invalid release SHA.")
    require(
        candidate_sha == github_sha,
        "CANDIDATE_SHA must equal GITHUB_SHA.",
    )


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
    require_candidate_sha(candidate_sha, target)
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


def verify_ancestry(candidate_sha: str, run_head_sha: str, github_sha: str) -> None:
    """Require candidate <= run.head_sha <= GITHUB_SHA by ancestry."""
    require(bool(SHA.fullmatch(run_head_sha)), "Invalid staging head.")
    require(
        subprocess.call(
            ["git", "merge-base", "--is-ancestor", candidate_sha, run_head_sha]
        )
        == 0,
        "Candidate is not an ancestor of the staging run head.",
    )
    require(
        subprocess.call(
            ["git", "merge-base", "--is-ancestor", run_head_sha, github_sha]
        )
        == 0,
        "Staging run head is not an ancestor of this release SHA.",
    )
    diff = subprocess.call(
        [
            "git",
            "diff",
            "--quiet",
            candidate_sha,
            github_sha,
            "--",
            *STAGING_CONTROL_PATHS,
        ]
    )
    require(
        diff == 0,
        "Staging control plane changed between candidate and this release SHA.",
    )


def verify_staging_jobs(jobs_payload: dict[str, Any]) -> None:
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


def artifact_by_name(artifacts: dict[str, Any], name: str) -> dict[str, Any]:
    matches = [
        item
        for item in artifacts.get("artifacts", [])
        if item.get("name") == name and item.get("expired") is False
    ]
    require(len(matches) == 1, f"Staging artifact missing or ambiguous: {name}.")
    item = matches[0]
    require(isinstance(item.get("id"), int) and item["id"] > 0, "Invalid artifact id.")
    return item


def download_artifact_files(artifact_id: int) -> dict[str, bytes]:
    raw = subprocess.check_output(
        [
            "gh",
            "api",
            f"repos/{REPOSITORY}/actions/artifacts/{artifact_id}/zip",
        ]
    )
    with zipfile.ZipFile(io.BytesIO(raw)) as bundle:
        files: dict[str, bytes] = {}
        for entry in bundle.infolist():
            require(
                not entry.is_dir() and entry.filename not in files,
                "Malformed staging artifact zip.",
            )
            files[entry.filename] = bundle.read(entry)
    return files


def verify_sha256sums(files: dict[str, bytes]) -> None:
    require("SHA256SUMS" in files, "Staging artifact missing SHA256SUMS.")
    lines = files["SHA256SUMS"].decode("ascii").splitlines()
    expected = {
        f"{hashlib.sha256(files[name]).hexdigest()}  {name}"
        for name in files
        if name != "SHA256SUMS"
    }
    require(
        set(lines) == expected and len(lines) == len(expected), "SHA256SUMS mismatch."
    )


def load_json(files: dict[str, bytes], name: str) -> dict[str, Any]:
    require(name in files, f"Missing {name}.")
    payload = json.loads(files[name])
    require(isinstance(payload, dict), f"{name} must be an object.")
    return payload


def verify_release_and_publication(
    *,
    release_files: dict[str, bytes],
    publication_files: dict[str, bytes],
    candidate_sha: str,
    digest: str,
    run_id: int,
    attempt: int,
) -> None:
    verify_sha256sums(release_files)
    verify_sha256sums(publication_files)
    release = load_json(release_files, "release.json")
    publication = load_json(publication_files, "publication.json")

    require(release.get("source_sha") == candidate_sha, "release source_sha mismatch.")
    require(
        publication.get("source_sha") == candidate_sha,
        "publication source_sha mismatch.",
    )
    require(
        isinstance(release.get("image"), dict)
        and release["image"].get("digest") == digest,
        "release image.digest does not match staging_image_digest.",
    )
    require(
        isinstance(publication.get("image"), dict)
        and publication["image"].get("digest") == digest,
        "publication image.digest does not match staging_image_digest.",
    )
    require(
        release.get("release_contract_result") == "passed",
        "release_contract_result did not pass.",
    )
    smoke = release.get("smoke")
    require(isinstance(smoke, dict), "release smoke missing.")
    health = smoke.get("healthcheck")
    require(
        isinstance(health, dict) and health.get("result") == "passed",
        "smoke.healthcheck did not pass.",
    )
    require(
        publication.get("publication_outcome") == "success",
        "publication was not successful.",
    )
    require(
        release.get("deployment_outcome") == "success",
        "staging deployment_outcome was not success.",
    )
    github = release.get("github")
    require(isinstance(github, dict), "release github provenance missing.")
    require(
        github.get("run_id") == run_id and github.get("run_attempt") == attempt,
        "release github run id/attempt mismatch.",
    )
    require(
        publication.get("run_id") == run_id
        and publication.get("run_attempt") == attempt,
        "publication run id/attempt mismatch.",
    )


def verify_staging_run(
    run: dict[str, Any],
    artifacts: dict[str, Any],
    jobs_payload: dict[str, Any],
    candidate_sha: str,
    digest: str,
    *,
    github_sha: str | None = None,
    release_files: dict[str, bytes] | None = None,
    publication_files: dict[str, bytes] | None = None,
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
    attempt = run.get("run_attempt")
    require(isinstance(attempt, int) and attempt >= 1, "Invalid staging attempt.")
    require(isinstance(run.get("id"), int) and run["id"] > 0, "Invalid staging run id.")
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
    require(
        any(
            item.get("name") == expected_publication and item.get("expired") is False
            for item in artifacts.get("artifacts", [])
        ),
        "Staging publication artifact expired or missing.",
    )
    if github_sha is not None:
        verify_ancestry(candidate_sha, str(run["head_sha"]), github_sha)
    if release_files is not None and publication_files is not None:
        verify_release_and_publication(
            release_files=release_files,
            publication_files=publication_files,
            candidate_sha=candidate_sha,
            digest=digest,
            run_id=int(run["id"]),
            attempt=int(attempt),
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
    artifacts = api(
        f"repos/{REPOSITORY}/actions/runs/{staging_run_id}/artifacts?per_page=100"
    )
    release_meta = artifact_by_name(
        artifacts,
        f"canonical-staging-release-{candidate_sha}-{run['id']}-{run['run_attempt']}",
    )
    publication_meta = artifact_by_name(
        artifacts,
        (
            f"canonical-staging-publication-{candidate_sha}-"
            f"{run['id']}-{run['run_attempt']}"
        ),
    )
    release_files = download_artifact_files(int(release_meta["id"]))
    publication_files = download_artifact_files(int(publication_meta["id"]))
    verify_staging_run(
        run,
        artifacts,
        jobs,
        candidate_sha,
        staging_image_digest,
        github_sha=os.environ["GITHUB_SHA"],
        release_files=release_files,
        publication_files=publication_files,
    )
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as stream:
            stream.write(
                f"Staging run {staging_run_id} verified for candidate {candidate_sha}. "
                f"All {len(REQUIRED_STAGING_JOBS)} staging jobs succeeded. "
                f"Release/publication SHA256SUMS bind source_sha, digest "
                f"{staging_image_digest}, contract, smoke, and publication success "
                f"for run {run['id']} attempt {run['run_attempt']}. "
                "Production image digest is pinned separately after the production build.\n"
            )
    print(
        "Staging-verified candidate admitted; release/publication artifacts inspected."
    )


if __name__ == "__main__":
    main()
