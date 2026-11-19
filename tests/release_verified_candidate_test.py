"""Static and synthetic gates for the reusable staging-verified release path."""

from __future__ import annotations

import copy
from pathlib import Path
from unittest import mock

import pytest
import yaml

from scripts import verify_release_verified_candidate as verify

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/release_verified_candidate.yml"
ONE_TIME = ROOT / ".github/workflows/production_release_verified.yml"


def staging_evidence(candidate_sha: str = "a" * 40, run_id: int = 99) -> tuple:
    attempt = 1
    jobs = {
        "total_count": len(verify.REQUIRED_STAGING_JOBS),
        "jobs": [
            {
                "name": name,
                "status": "completed",
                "conclusion": "success",
                "run_id": run_id,
                "run_attempt": attempt,
                "head_sha": "b" * 40,
            }
            for name in sorted(verify.REQUIRED_STAGING_JOBS)
        ],
    }
    run = {
        "id": run_id,
        "name": verify.STAGING_WORKFLOW_NAME,
        "path": verify.STAGING_WORKFLOW,
        "event": "workflow_dispatch",
        "head_branch": "master",
        "head_sha": "b" * 40,
        "status": "completed",
        "conclusion": "success",
        "run_attempt": attempt,
        "repository": {"full_name": verify.REPOSITORY},
        "actor": {"login": "JohnClem"},
        "triggering_actor": {"login": "JohnClem"},
    }
    artifacts = {
        "artifacts": [
            {
                "name": f"canonical-staging-release-{candidate_sha}-{run_id}-{attempt}",
                "expired": False,
            },
            {
                "name": (
                    f"canonical-staging-publication-{candidate_sha}-{run_id}-{attempt}"
                ),
                "expired": False,
            },
            {
                "name": f"canonical-staging-attempt-{run_id}-{attempt}",
                "expired": False,
            },
        ]
    }
    return run, artifacts, jobs


def test_required_staging_jobs_match_deploy_staging_candidate_matrix():
    verify.verify_staging_workflow_shards()
    source = (ROOT / ".github/workflows/deploy_staging_candidate.yml").read_text()
    for shard in verify.STAGING_SHARDS:
        assert f"- shard: {shard}" in source
    for name in (
        "Authorize immutable staging candidate",
        "Candidate backend quality",
        "Candidate Frontend",
        "Candidate Backend",
        "Publish credential-free candidate archive",
        "Deploy exact candidate to persistent staging",
    ):
        assert name in verify.REQUIRED_STAGING_JOBS


def test_staging_run_acceptance_and_mutations():
    candidate = "a" * 40
    digest = "sha256:" + "c" * 64
    run, artifacts, jobs = staging_evidence(candidate)
    verify.verify_staging_run(run, artifacts, jobs, candidate, digest)

    bad_run = copy.deepcopy(run)
    bad_run["conclusion"] = "failure"
    with pytest.raises(ValueError, match="Staging run provenance"):
        verify.verify_staging_run(bad_run, artifacts, jobs, candidate, digest)

    bad_artifacts = copy.deepcopy(artifacts)
    bad_artifacts["artifacts"] = bad_artifacts["artifacts"][:1]
    with pytest.raises(ValueError, match="Staging artifacts"):
        verify.verify_staging_run(run, bad_artifacts, jobs, candidate, digest)

    bad_jobs = copy.deepcopy(jobs)
    bad_jobs["jobs"][0]["conclusion"] = "failure"
    with pytest.raises(ValueError, match="incomplete or unsuccessful"):
        verify.verify_staging_run(run, artifacts, bad_jobs, candidate, digest)

    with pytest.raises(ValueError, match="Invalid staging image digest"):
        verify.verify_staging_run(run, artifacts, jobs, candidate, "sha256:dead")


def test_reusable_workflow_keeps_founder_apply_digest_and_trivy_gates():
    raw = WORKFLOW.read_text()
    workflow = yaml.safe_load(raw)
    assert workflow["name"] == "Release verified candidate"
    on = workflow.get("on", workflow.get(True, {}))
    assert set(on) == {"workflow_dispatch"}
    inputs = on["workflow_dispatch"]["inputs"]
    assert "local_assertion_repair_verified" not in inputs
    for key in (
        "github_hosted_fallback_authorized",
        "candidate_sha",
        "staging_run_id",
        "staging_image_digest",
        "terraform_apply_run_id",
        "provider_signed_recall_callback_verified",
    ):
        assert key in inputs
    deploy = workflow["jobs"]["deploy"]
    assert deploy["environment"] == "production"
    assert deploy["concurrency"] == {
        "group": "cosmaneura-production-container-apps",
        "cancel-in-progress": False,
    }
    assert "inputs.github_hosted_fallback_authorized" in deploy["if"]
    step_names = [step.get("name") for step in deploy["steps"] if "name" in step]
    assert "Verify staging-verified candidate before credentials" in step_names
    assert "Verify successful applicable Terraform Apply" in step_names
    assert "Scan the built production image with Trivy before publish" in step_names
    assert "Pin the published production image digest" in step_names
    assert "Deploy the pinned production digest to Azure" in step_names
    assert (
        "aquasec/trivy@sha256:ab70a02200597efa04748f210f793936eb647cbcdb0ea69cc30b226d6f5a22c7"
        in raw
    )
    assert "scripts/verify_release_verified_candidate.py" in raw
    assert 'bash deploy.sh "$CANDIDATE_SHA" "$CORE_PRODUCTION_IMAGE_REF"' in raw
    assert "DURABLE_WEBHOOK_INBOX_ENABLED" in raw
    # One-time historical caller remains frozen and separate.
    assert ONE_TIME.read_bytes()
    assert "verify_one_time_release_reuse.py" in ONE_TIME.read_text()
    assert "verify_one_time_release_reuse.py" not in raw


def test_production_core_context_allows_reusable_release_workflow():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "production_core_candidate",
        ROOT / "scripts/production_core_candidate.py",
    )
    assert spec is not None and spec.loader is not None
    candidate = importlib.util.module_from_spec(spec)
    # Sibling script imports resolve from scripts/.
    import sys

    scripts_dir = str(ROOT / "scripts")
    sys.path.insert(0, scripts_dir)
    try:
        spec.loader.exec_module(candidate)
    finally:
        if sys.path[0] == scripts_dir:
            sys.path.pop(0)

    assert (
        f"{candidate.REPOSITORY}/{candidate.RELEASE_VERIFIED_CANDIDATE_WORKFLOW}"
        "@refs/heads/master" in candidate.ALLOWED_CONTEXT_WORKFLOW_REFS
    )
    assert candidate.WORKFLOW_REF in candidate.ALLOWED_CONTEXT_WORKFLOW_REFS
    # Admit/publish stay pinned to Build & Publish producer path.
    assert candidate.WORKFLOW == ".github/workflows/build_and_publish_local.yml"


def test_founder_source_gate_refuses_drift(monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", verify.REPOSITORY)
    monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
    monkeypatch.setenv("GITHUB_REF", "refs/heads/master")
    monkeypatch.setenv("GITHUB_ACTOR", "JohnClem")
    monkeypatch.setenv("GITHUB_TRIGGERING_ACTOR", "JohnClem")
    monkeypatch.setenv("GITHUB_SHA", "a" * 40)

    with mock.patch.object(verify, "output", side_effect=["a" * 40, "b" * 40]):
        with pytest.raises(ValueError, match="Master advanced"):
            verify.verify_founder_and_source("a" * 40)

    with pytest.raises(ValueError, match="Candidate SHA must equal"):
        verify.verify_founder_and_source("b" * 40)
