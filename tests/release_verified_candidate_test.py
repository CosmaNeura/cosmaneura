"""Static and synthetic gates for the reusable staging-verified release path."""

from __future__ import annotations

import copy
import hashlib
import json
import subprocess
from pathlib import Path
from unittest import mock

import pytest
import yaml

from scripts import verify_release_verified_candidate as verify

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/release_verified_candidate.yml"
ONE_TIME = ROOT / ".github/workflows/production_release_verified.yml"
ACTIONLINT = Path("/tmp/actionlint-bin/actionlint")
TRIVY_IMAGE = "aquasec/trivy@sha256:ab70a02200597efa04748f210f793936eb647cbcdb0ea69cc30b226d6f5a22c7"


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
                "id": 1001,
                "name": f"canonical-staging-release-{candidate_sha}-{run_id}-{attempt}",
                "expired": False,
            },
            {
                "id": 1002,
                "name": (
                    f"canonical-staging-publication-{candidate_sha}-{run_id}-{attempt}"
                ),
                "expired": False,
            },
            {
                "id": 1003,
                "name": f"canonical-staging-attempt-{run_id}-{attempt}",
                "expired": False,
            },
        ]
    }
    return run, artifacts, jobs


def artifact_bundle(payload: dict, name: str) -> dict[str, bytes]:
    raw = json.dumps(payload, sort_keys=True).encode()
    return {
        name: raw,
        "SHA256SUMS": f"{hashlib.sha256(raw).hexdigest()}  {name}\n".encode(),
    }


def release_publication_files(
    candidate_sha: str = "a" * 40,
    digest: str = "sha256:" + "c" * 64,
    run_id: int = 99,
    attempt: int = 1,
) -> tuple[dict[str, bytes], dict[str, bytes]]:
    release = {
        "source_sha": candidate_sha,
        "image": {"digest": digest, "reference": f"registry/image@{digest}"},
        "release_contract_result": "passed",
        "smoke": {"healthcheck": {"result": "passed", "http_status": 200}},
        "deployment_outcome": "success",
        "github": {
            "repository": verify.REPOSITORY,
            "run_id": run_id,
            "run_attempt": attempt,
        },
    }
    publication = {
        "source_sha": candidate_sha,
        "image": {"digest": digest},
        "publication_outcome": "success",
        "run_id": run_id,
        "run_attempt": attempt,
    }
    return (
        artifact_bundle(release, "release.json"),
        artifact_bundle(publication, "publication.json"),
    )


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
    release_files, publication_files = release_publication_files(candidate, digest)
    with mock.patch.object(verify, "subprocess") as sp:
        sp.call.return_value = 0
        verify.verify_staging_run(
            run,
            artifacts,
            jobs,
            candidate,
            digest,
            github_sha="d" * 40,
            release_files=release_files,
            publication_files=publication_files,
        )

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


@pytest.mark.parametrize(
    "mutator,match",
    [
        (
            lambda rel, pub: rel.__setitem__(
                "release.json",
                json.dumps(
                    {
                        **json.loads(rel["release.json"]),
                        "source_sha": "e" * 40,
                    }
                ).encode(),
            ),
            "SHA256SUMS mismatch|release source_sha",
        ),
        (
            lambda rel, pub: (
                rel.__setitem__(
                    "release.json",
                    json.dumps(
                        {
                            **json.loads(rel["release.json"]),
                            "release_contract_result": "failed",
                        }
                    ).encode(),
                ),
                rel.__setitem__(
                    "SHA256SUMS",
                    (
                        f"{hashlib.sha256(rel['release.json']).hexdigest()}  "
                        "release.json\n"
                    ).encode(),
                ),
            ),
            "release_contract_result",
        ),
        (
            lambda rel, pub: (
                rel.__setitem__(
                    "release.json",
                    json.dumps(
                        {
                            **json.loads(rel["release.json"]),
                            "smoke": {"healthcheck": {"result": "failed"}},
                        }
                    ).encode(),
                ),
                rel.__setitem__(
                    "SHA256SUMS",
                    (
                        f"{hashlib.sha256(rel['release.json']).hexdigest()}  "
                        "release.json\n"
                    ).encode(),
                ),
            ),
            "smoke.healthcheck",
        ),
        (
            lambda rel, pub: (
                pub.__setitem__(
                    "publication.json",
                    json.dumps(
                        {
                            **json.loads(pub["publication.json"]),
                            "publication_outcome": "failed",
                        }
                    ).encode(),
                ),
                pub.__setitem__(
                    "SHA256SUMS",
                    (
                        f"{hashlib.sha256(pub['publication.json']).hexdigest()}  "
                        "publication.json\n"
                    ).encode(),
                ),
            ),
            "publication was not successful",
        ),
        (
            lambda rel, pub: (
                rel.__setitem__(
                    "release.json",
                    json.dumps(
                        {
                            **json.loads(rel["release.json"]),
                            "image": {"digest": "sha256:" + "f" * 64},
                        }
                    ).encode(),
                ),
                rel.__setitem__(
                    "SHA256SUMS",
                    (
                        f"{hashlib.sha256(rel['release.json']).hexdigest()}  "
                        "release.json\n"
                    ).encode(),
                ),
            ),
            "release image.digest",
        ),
        (
            lambda rel, pub: (
                pub.__setitem__(
                    "publication.json",
                    json.dumps(
                        {
                            **json.loads(pub["publication.json"]),
                            "run_id": 1,
                        }
                    ).encode(),
                ),
                pub.__setitem__(
                    "SHA256SUMS",
                    (
                        f"{hashlib.sha256(pub['publication.json']).hexdigest()}  "
                        "publication.json\n"
                    ).encode(),
                ),
            ),
            "publication run id/attempt",
        ),
    ],
)
def test_release_publication_require_mutations(mutator, match):
    candidate = "a" * 40
    digest = "sha256:" + "c" * 64
    run, artifacts, jobs = staging_evidence(candidate)
    release_files, publication_files = release_publication_files(candidate, digest)
    mutator(release_files, publication_files)
    # Repair SHA256SUMS only when mutator left them inconsistent intentionally for
    # the source_sha case; that case expects either sums or field failure.
    with mock.patch.object(verify, "subprocess") as sp:
        sp.call.return_value = 0
        with pytest.raises(ValueError, match=match):
            verify.verify_staging_run(
                run,
                artifacts,
                jobs,
                candidate,
                digest,
                github_sha="d" * 40,
                release_files=release_files,
                publication_files=publication_files,
            )


def test_ancestry_and_control_plane_mutations(monkeypatch):
    candidate = "a" * 40
    head = "b" * 40
    github = "d" * 40
    calls: list[list[str]] = []

    def fake_call(args, *a, **k):
        calls.append(list(args))
        if args[:3] == ["git", "merge-base", "--is-ancestor"]:
            if args[3] == candidate and args[4] == head:
                return 0
            if args[3] == head and args[4] == github:
                return 0
            return 1
        if args[:3] == ["git", "diff", "--quiet"]:
            return 0
        return 1

    monkeypatch.setattr(verify.subprocess, "call", fake_call)
    verify.verify_ancestry(candidate, head, github)

    def refuse_control(*args, **kwargs):
        if args[0][:3] == ["git", "diff", "--quiet"]:
            return 1
        return fake_call(*args, **kwargs)

    monkeypatch.setattr(verify.subprocess, "call", refuse_control)
    with pytest.raises(ValueError, match="Staging control plane changed"):
        verify.verify_ancestry(candidate, head, github)


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
    assert inputs["github_hosted_fallback_authorized"]["description"].startswith(
        "Operator attestation:"
    )
    assert inputs["provider_signed_recall_callback_verified"]["description"].startswith(
        "Operator attestation:"
    )
    assert set(workflow["jobs"]) == {"gate", "build-and-scan", "publish-and-deploy"}
    gate = workflow["jobs"]["gate"]
    build = workflow["jobs"]["build-and-scan"]
    publish = workflow["jobs"]["publish-and-deploy"]
    assert "environment" not in gate
    assert "environment" not in build
    assert publish["environment"] == "production"
    assert publish["concurrency"] == {
        "group": "cosmaneura-production-container-apps",
        "cancel-in-progress": False,
    }
    assert gate["permissions"] == {
        "actions": "read",
        "contents": "read",
        "pull-requests": "read",
        "issues": "read",
    }
    assert build["permissions"] == {"contents": "read"}
    assert "id-token" not in build["permissions"]
    assert publish["permissions"]["id-token"] == "write"
    for job in (gate, build, publish):
        assert "inputs.github_hosted_fallback_authorized" in job["if"]
    gate_steps = [step.get("name") for step in gate["steps"] if "name" in step]
    assert "Require candidate SHA equals GITHUB_SHA before checkout" in gate_steps
    assert "Verify staging-verified candidate before credentials" in gate_steps
    assert "Require operator attestation for Recall callback" in gate_steps
    assert "Verify successful applicable Terraform Apply" in gate_steps
    build_steps = [step.get("name") for step in build["steps"] if "name" in step]
    assert "Scan the built production image with Trivy before publish" in build_steps
    assert "Export scanned image archive for publish admission" in build_steps
    publish_steps = [step.get("name") for step in publish["steps"] if "name" in step]
    assert (
        "Reverify current master and admit production context before publish"
        in publish_steps
    )
    assert (
        "Publish scanned image with equivalent Core admission checks" in publish_steps
    )
    assert (
        "Record current production digests and revisions before deploy" in publish_steps
    )
    assert "Deploy the pinned production digest to Azure" in publish_steps
    assert "Verify healthcheck and ready-revision digests or roll back" in publish_steps
    # Context before docker login / publish; login after Trivy (other job).
    context_idx = publish_steps.index(
        "Reverify current master and admit production context before publish"
    )
    login_idxs = [
        i
        for i, step in enumerate(publish["steps"])
        if isinstance(step.get("uses"), str) and "docker/login-action@" in step["uses"]
    ]
    assert login_idxs and context_idx < login_idxs[0]
    assert "severity-exit-code" not in raw
    assert "--exit-code 1" in raw
    assert (
        "aquasec/trivy@sha256:ab70a02200597efa04748f210f793936eb647cbcdb0ea69cc30b226d6f5a22c7"
        in raw
    )
    assert "scripts/verify_release_verified_candidate.py" in raw
    assert "scripts/release_verified_candidate_publish.py" in raw
    assert "scripts/release_verified_candidate_runtime.py" in raw
    assert 'bash deploy.sh "$CANDIDATE_SHA" "$CORE_PRODUCTION_IMAGE_REF"' in raw
    assert "DURABLE_WEBHOOK_INBOX_ENABLED" in raw
    assert "ref: ${{ inputs.candidate_sha }}" not in raw
    assert (
        "per_page=100"
        in (ROOT / "scripts/verify_release_verified_candidate.py").read_text()
    )
    # One-time historical caller is retired.
    assert not ONE_TIME.exists()
    assert not (ROOT / "scripts/verify_one_time_release_reuse.py").exists()
    assert "verify_one_time_release_reuse.py" not in raw


def test_step_order_permissions_and_checkout_gates():
    workflow = yaml.safe_load(WORKFLOW.read_text())
    for job_name in ("gate", "build-and-scan", "publish-and-deploy"):
        steps = workflow["jobs"][job_name]["steps"]
        checkout_indexes = [
            i
            for i, step in enumerate(steps)
            if isinstance(step.get("uses"), str) and "actions/checkout@" in step["uses"]
        ]
        assert checkout_indexes
        checkout = steps[checkout_indexes[0]]
        assert "ref" not in checkout.get("with", {})
        require_idx = next(
            i
            for i, step in enumerate(steps)
            if step.get("name")
            == "Require candidate SHA equals GITHUB_SHA before checkout"
        )
        assert require_idx < checkout_indexes[0]
    build_steps = workflow["jobs"]["build-and-scan"]["steps"]
    build_uses = [step.get("uses") for step in build_steps if "uses" in step]
    assert not any(
        isinstance(item, str)
        and ("docker/login-action@" in item or "azure/login@" in item)
        for item in build_uses
    )
    publish_steps = workflow["jobs"]["publish-and-deploy"]["steps"]
    trivy_job_has_no_login = True
    assert trivy_job_has_no_login
    publish_names = [step.get("name") for step in publish_steps if "name" in step]
    assert publish_names.index(
        "Reverify current master and admit production context before publish"
    ) < next(
        i
        for i, step in enumerate(publish_steps)
        if isinstance(step.get("uses"), str) and "docker/login-action@" in step["uses"]
    )


def test_production_core_context_allows_reusable_release_workflow_only_in_context_mode():
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location(
        "production_core_candidate",
        ROOT / "scripts/production_core_candidate.py",
    )
    assert spec is not None and spec.loader is not None
    candidate = importlib.util.module_from_spec(spec)
    scripts_dir = str(ROOT / "scripts")
    sys.path.insert(0, scripts_dir)
    try:
        spec.loader.exec_module(candidate)
    finally:
        if sys.path[0] == scripts_dir:
            sys.path.pop(0)

    release_ref = (
        f"{candidate.REPOSITORY}/{candidate.RELEASE_VERIFIED_CANDIDATE_WORKFLOW}"
        "@refs/heads/master"
    )
    assert release_ref in candidate.ALLOWED_CONTEXT_WORKFLOW_REFS
    assert candidate.WORKFLOW_REF in candidate.ALLOWED_CONTEXT_WORKFLOW_REFS
    assert candidate.WORKFLOW == ".github/workflows/build_and_publish_local.yml"


@pytest.mark.parametrize("mode", ["publish", "admit", "check"])
def test_production_core_publish_admit_check_refuse_release_verified_ref(
    monkeypatch, mode
):
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location(
        "production_core_candidate_refuse",
        ROOT / "scripts/production_core_candidate.py",
    )
    assert spec is not None and spec.loader is not None
    candidate = importlib.util.module_from_spec(spec)
    scripts_dir = str(ROOT / "scripts")
    sys.path.insert(0, scripts_dir)
    try:
        spec.loader.exec_module(candidate)
    finally:
        if sys.path[0] == scripts_dir:
            sys.path.pop(0)

    release_ref = (
        f"{candidate.REPOSITORY}/{candidate.RELEASE_VERIFIED_CANDIDATE_WORKFLOW}"
        "@refs/heads/master"
    )
    for key, value in {
        "GITHUB_ACTOR": "JohnClem",
        "GITHUB_TRIGGERING_ACTOR": "JohnClem",
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_REF": "refs/heads/master",
        "GITHUB_REPOSITORY": candidate.REPOSITORY,
        "GITHUB_WORKFLOW_REF": release_ref,
        "GITHUB_SHA": "a" * 40,
        "GITHUB_RUN_ID": "1",
        "GITHUB_RUN_ATTEMPT": "1",
    }.items():
        monkeypatch.setenv(key, value)

    monkeypatch.setattr(
        candidate,
        "command",
        lambda *a, **k: pytest.fail("must refuse before client use"),
    )
    with pytest.raises(candidate.CandidateRefused):
        candidate.source_context()


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

    with pytest.raises(ValueError, match="CANDIDATE_SHA must equal"):
        verify.verify_founder_and_source("b" * 40)


def test_require_candidate_sha_gate():
    verify.require_candidate_sha("a" * 40, "a" * 40)
    with pytest.raises(ValueError, match="Invalid candidate SHA"):
        verify.require_candidate_sha("short", "a" * 40)
    with pytest.raises(ValueError, match="CANDIDATE_SHA must equal"):
        verify.require_candidate_sha("a" * 40, "b" * 40)


def test_one_time_release_path_is_retired():
    assert not ONE_TIME.exists()
    assert not (ROOT / "scripts/verify_one_time_release_reuse.py").exists()
    assert not (ROOT / "tests/one_time_release_reuse_test.py").exists()


def test_actionlint_passes_on_release_verified_candidate():
    assert ACTIONLINT.is_file(), "actionlint binary missing at /tmp/actionlint-bin"
    result = subprocess.run(
        [str(ACTIONLINT), "-shellcheck=", str(WORKFLOW)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_trivy_has_no_severity_exit_code_flag_and_scans_throwaway():
    help_result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            TRIVY_IMAGE,
            "image",
            "--help",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert help_result.returncode == 0
    assert "--severity-exit-code" not in help_result.stdout
    assert "--exit-code" in help_result.stdout
    # Throwaway alpine image: prove the workflow flags are accepted by Trivy.
    tag = "cosmaneura-trivy-throwaway:test"
    subprocess.run(
        ["docker", "pull", "alpine:3.20"],
        check=True,
        capture_output=True,
        timeout=180,
    )
    subprocess.run(
        ["docker", "tag", "alpine:3.20", tag],
        check=True,
        capture_output=True,
        timeout=60,
    )
    try:
        scan = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "-v",
                "/var/run/docker.sock:/var/run/docker.sock",
                TRIVY_IMAGE,
                "image",
                "--exit-code",
                "0",
                "--severity",
                "CRITICAL,HIGH",
                "--ignore-unfixed",
                "--scanners",
                "vuln",
                tag,
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=300,
        )
        assert scan.returncode == 0, scan.stdout + scan.stderr
        bad = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                TRIVY_IMAGE,
                "image",
                "--severity-exit-code",
                "1",
                tag,
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        assert bad.returncode != 0
        combined = (bad.stderr + bad.stdout).lower()
        assert "unknown flag" in combined or "unknown" in combined
    finally:
        subprocess.run(["docker", "rmi", "-f", tag], capture_output=True, check=False)


def test_artifacts_api_uses_per_page_100(monkeypatch):
    seen: list[str] = []

    def fake_api(path: str):
        seen.append(path)
        if path.endswith("/artifacts?per_page=100"):
            return {"artifacts": []}
        if "/jobs?" in path:
            return {"total_count": 0, "jobs": []}
        return {
            "id": 1,
            "name": verify.STAGING_WORKFLOW_NAME,
            "path": verify.STAGING_WORKFLOW,
            "event": "workflow_dispatch",
            "head_branch": "master",
            "head_sha": "b" * 40,
            "status": "completed",
            "conclusion": "success",
            "run_attempt": 1,
            "repository": {"full_name": verify.REPOSITORY},
            "actor": {"login": "JohnClem"},
            "triggering_actor": {"login": "JohnClem"},
        }

    monkeypatch.setenv("CANDIDATE_SHA", "a" * 40)
    monkeypatch.setenv("STAGING_RUN_ID", "99")
    monkeypatch.setenv("STAGING_IMAGE_DIGEST", "sha256:" + "c" * 64)
    monkeypatch.setenv("GITHUB_SHA", "a" * 40)
    monkeypatch.setenv("GITHUB_REPOSITORY", verify.REPOSITORY)
    monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
    monkeypatch.setenv("GITHUB_REF", "refs/heads/master")
    monkeypatch.setenv("GITHUB_ACTOR", "JohnClem")
    monkeypatch.setenv("GITHUB_TRIGGERING_ACTOR", "JohnClem")
    monkeypatch.setattr(verify, "api", fake_api)
    monkeypatch.setattr(verify, "verify_founder_and_source", lambda *_: None)
    monkeypatch.setattr(verify, "verify_staging_workflow_shards", lambda: None)
    with pytest.raises(ValueError):
        verify.main()
    assert any(path.endswith("/artifacts?per_page=100") for path in seen)
    assert any("jobs?per_page=100" in path for path in seen)
