# fmt: off
from __future__ import annotations  # noqa: I001
import hashlib
import io
import json
import os
import re
import subprocess
import zipfile
from pathlib import Path
from typing import Any
import yaml
REPOSITORY = "CosmaNeura/cosmaneura"
STAGING_WORKFLOW = ".github/workflows/deploy_staging_candidate.yml"
STAGING_WORKFLOW_NAME = "Deploy Staging Candidate"
SHA = re.compile(r"[0-9a-f]{40}\Z")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
RUN_ID = re.compile(r"[1-9][0-9]{0,19}\Z")
STAGING_CONTROL_PATHS = (".github/workflows/deploy_staging_candidate.yml", "scripts/verify_staging_candidate.py", "scripts/staging_release_contract.py", "infra-staging")
def _staging_shards() -> list[str]:
    data = yaml.safe_load(Path(".github/workflows/deploy_staging_candidate.yml").read_text()) or {}
    include = ((((data.get("jobs") or {}).get("backend-tests") or {}).get("strategy") or {}).get("matrix") or {}).get("include") or []
    return [str(x["shard"]) for x in include if isinstance(x, dict) and x.get("shard")]
STAGING_SHARDS = tuple(_staging_shards())
REQUIRED_STAGING_JOBS = {
    "Authorize immutable staging candidate", "Candidate backend quality", "Candidate Backend",
    "Candidate Frontend", "Resolve reviewed public staging build configuration",
    "Build exact candidate without protected credentials", "Publish credential-free candidate archive",
    "Deploy exact candidate to persistent staging", "Record complete staging workflow attempt",
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
def verify_founder_and_source(candidate_sha: str) -> None:
    require(os.environ.get("GITHUB_REPOSITORY") == REPOSITORY, "Wrong release repository.")
    require(os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch" and os.environ.get("GITHUB_REF") == "refs/heads/master", "Release must be manual master.")
    require(os.environ.get("GITHUB_ACTOR") == "JohnClem" and os.environ.get("GITHUB_TRIGGERING_ACTOR") == "JohnClem", "Release requires founder actors.")
    target = os.environ.get("GITHUB_SHA", "")
    require(bool(SHA.fullmatch(candidate_sha)) and bool(SHA.fullmatch(target)) and candidate_sha == target, "CANDIDATE_SHA must equal GITHUB_SHA.")
    require(output(["git", "rev-parse", "HEAD"]) == target, "Checkout differs from release SHA.")
    require(output(["gh", "api", f"repos/{REPOSITORY}/git/ref/heads/master", "--jq", ".object.sha"]) == target, "Master advanced or candidate is not current master.")
def verify_ancestry(candidate_sha: str, run_head_sha: str, github_sha: str) -> None:
    require(bool(SHA.fullmatch(run_head_sha)), "Invalid staging head.")
    require(subprocess.call(["git", "merge-base", "--is-ancestor", candidate_sha, run_head_sha]) == 0, "Candidate is not an ancestor of the staging run head.")
    require(subprocess.call(["git", "merge-base", "--is-ancestor", run_head_sha, github_sha]) == 0, "Staging run head is not an ancestor of this release SHA.")
    require(subprocess.call(["git", "diff", "--quiet", f"{candidate_sha}..{run_head_sha}", "--", *STAGING_CONTROL_PATHS]) == 0, "Staging control paths drifted after the candidate.")
def verify_staging_jobs(jobs_payload: dict[str, Any]) -> None:
    jobs = jobs_payload.get("jobs", [])
    require(jobs_payload.get("total_count") == len(REQUIRED_STAGING_JOBS) == len(jobs), "Staging jobs count mismatch.")
    require(len({job.get("name") for job in jobs}) == len(jobs), "Duplicate staging jobs.")
    require({job.get("name") for job in jobs} == REQUIRED_STAGING_JOBS, "Staging job names differ.")
    for job in jobs:
        require(job.get("status") == "completed" and job.get("conclusion") == "success", f"Staging job incomplete or unsuccessful: {job.get('name')}.")
def artifact_by_name(artifacts: dict[str, Any], name: str) -> dict[str, Any]:
    matches = [item for item in artifacts.get("artifacts", []) if item.get("name") == name and item.get("expired") is False]
    require(len(matches) == 1, f"Missing artifact: {name}.")
    item = matches[0]
    require(isinstance(item.get("id"), int) and item["id"] > 0, "Bad artifact id.")
    return item
def download_artifact_files(artifact_id: int) -> dict[str, bytes]:
    raw = subprocess.check_output(["gh", "api", f"repos/{REPOSITORY}/actions/artifacts/{artifact_id}/zip"])
    with zipfile.ZipFile(io.BytesIO(raw)) as bundle:
        files: dict[str, bytes] = {}
        for entry in bundle.infolist():
            require(not entry.is_dir() and entry.filename not in files, "Malformed staging artifact zip.")
            files[entry.filename] = bundle.read(entry)
    return files
def verify_sha256sums(files: dict[str, bytes]) -> None:
    require("SHA256SUMS" in files, "Staging artifact missing SHA256SUMS.")
    lines = files["SHA256SUMS"].decode("ascii").splitlines()
    expected = {f"{hashlib.sha256(files[name]).hexdigest()}  {name}" for name in files if name != "SHA256SUMS"}
    require(set(lines) == expected and len(lines) == len(expected), "SHA256SUMS mismatch.")
def load_json(files: dict[str, bytes], name: str) -> dict[str, Any]:
    require(name in files, f"Missing {name}.")
    payload = json.loads(files[name])
    require(isinstance(payload, dict), f"{name} must be an object.")
    return payload
def verify_release_and_publication(*, release_files, publication_files, candidate_sha, digest, run_id, attempt):
    verify_sha256sums(release_files)
    verify_sha256sums(publication_files)
    release, publication = load_json(release_files, "release.json"), load_json(publication_files, "publication.json")
    require(release.get("source_sha") == candidate_sha and publication.get("source_sha") == candidate_sha, "source_sha mismatch.")
    require(isinstance(release.get("image"), dict) and release["image"].get("digest") == digest, "release digest mismatch.")
    require(isinstance(publication.get("image"), dict) and publication["image"].get("digest") == digest, "publication digest mismatch.")
    require(release.get("release_contract_result") == "passed", "release_contract failed.")
    smoke = release.get("smoke")
    require(isinstance(smoke, dict), "smoke missing.")
    assert isinstance(smoke, dict)
    health = smoke.get("healthcheck")
    require(isinstance(health, dict) and health.get("result") == "passed", "healthcheck failed.")
    require(publication.get("publication_outcome") == "success" and release.get("deployment_outcome") == "success", "staging outcomes failed.")
    github = release.get("github")
    require(isinstance(github, dict), "github provenance missing.")
    assert isinstance(github, dict)
    require(github.get("run_id") == run_id and github.get("run_attempt") == attempt, "github run mismatch.")
    require(publication.get("run_id") == run_id and publication.get("run_attempt") == attempt, "publication run mismatch.")
def verify_staging_run(run, artifacts, jobs_payload, candidate_sha, digest, *, github_sha=None, release_files=None, publication_files=None):
    require(
        run.get("name") == STAGING_WORKFLOW_NAME and run.get("path") == STAGING_WORKFLOW
        and run.get("event") == "workflow_dispatch" and run.get("head_branch") == "master"
        and run.get("status") == "completed" and run.get("conclusion") == "success"
        and run.get("repository", {}).get("full_name") == REPOSITORY, "Staging provenance differs.")
    require(all(run.get(k, {}).get("login") == "JohnClem" for k in ("actor", "triggering_actor")), "Wrong staging actor.")
    require(bool(SHA.fullmatch(str(run.get("head_sha", "")))), "Invalid staging head.")
    attempt = run.get("run_attempt")
    require(isinstance(attempt, int) and attempt >= 1 and isinstance(run.get("id"), int) and run["id"] > 0, "Invalid staging run.")
    assert isinstance(attempt, int)
    names = {item.get("name") for item in artifacts.get("artifacts", [])}
    require(f"canonical-staging-release-{candidate_sha}-{run['id']}-{attempt}" in names and f"canonical-staging-publication-{candidate_sha}-{run['id']}-{attempt}" in names, "Staging artifacts missing.")
    verify_staging_jobs(jobs_payload)
    require(bool(DIGEST.fullmatch(digest)), "Invalid staging digest.")
    if github_sha is not None:
        verify_ancestry(candidate_sha, str(run["head_sha"]), github_sha)
    if release_files is not None and publication_files is not None:
        verify_release_and_publication(release_files=release_files, publication_files=publication_files, candidate_sha=candidate_sha, digest=digest, run_id=int(run["id"]), attempt=int(attempt))
def verify_staging_workflow_shards() -> None:
    shards = re.findall(r"^          - shard: ([a-z0-9-]+)$", Path(".github/workflows/deploy_staging_candidate.yml").read_text(), re.MULTILINE)
    require(tuple(shards) == tuple(STAGING_SHARDS), "Shard list drifted.")
def main() -> None:
    candidate_sha, staging_run_id, staging_image_digest = os.environ["CANDIDATE_SHA"], os.environ["STAGING_RUN_ID"], os.environ["STAGING_IMAGE_DIGEST"]
    require(bool(RUN_ID.fullmatch(staging_run_id)), "Invalid staging run id.")
    verify_founder_and_source(candidate_sha)
    verify_staging_workflow_shards()
    run = api(f"repos/{REPOSITORY}/actions/runs/{staging_run_id}")
    jobs = api(f"repos/{REPOSITORY}/actions/runs/{staging_run_id}/jobs?per_page=100&filter=latest")
    artifacts = api(f"repos/{REPOSITORY}/actions/runs/{staging_run_id}/artifacts?per_page=100")
    release_meta = artifact_by_name(artifacts, f"canonical-staging-release-{candidate_sha}-{run['id']}-{run['run_attempt']}")
    publication_meta = artifact_by_name(artifacts, f"canonical-staging-publication-{candidate_sha}-{run['id']}-{run['run_attempt']}")
    verify_staging_run(run, artifacts, jobs, candidate_sha, staging_image_digest, github_sha=os.environ["GITHUB_SHA"], release_files=download_artifact_files(int(release_meta["id"])), publication_files=download_artifact_files(int(publication_meta["id"])))
    print("Candidate admitted.")
if __name__ == "__main__":
    main()
