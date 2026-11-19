"""Gates for Release verified candidate."""
from __future__ import annotations  # noqa: I001
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
TRIVY = "aquasec/trivy@sha256:ab70a02200597efa04748f210f793936eb647cbcdb0ea69cc30b226d6f5a22c7"
def evidence(candidate="a" * 40, run_id=99):
    attempt = 1
    jobs = {
        "total_count": len(verify.REQUIRED_STAGING_JOBS),
        "jobs": [
            {
                "name": n, "status": "completed", "conclusion": "success",
                "run_id": run_id, "run_attempt": attempt, "head_sha": "b" * 40,
            }
            for n in sorted(verify.REQUIRED_STAGING_JOBS)
        ],
    }
    run = {
        "id": run_id, "name": verify.STAGING_WORKFLOW_NAME, "path": verify.STAGING_WORKFLOW,
        "event": "workflow_dispatch", "head_branch": "master", "head_sha": "b" * 40,
        "status": "completed", "conclusion": "success", "run_attempt": attempt,
        "repository": {"full_name": verify.REPOSITORY},
        "actor": {"login": "JohnClem"}, "triggering_actor": {"login": "JohnClem"},
    }
    arts = {"artifacts": [
        {"id": 1001, "name": f"canonical-staging-release-{candidate}-{run_id}-{attempt}", "expired": False},
        {"id": 1002, "name": f"canonical-staging-publication-{candidate}-{run_id}-{attempt}", "expired": False},
        {"id": 1003, "name": f"canonical-staging-attempt-{run_id}-{attempt}", "expired": False},
    ]}
    return run, arts, jobs
def bundle(payload, name):
    raw = json.dumps(payload, sort_keys=True).encode()
    return {name: raw, "SHA256SUMS": f"{hashlib.sha256(raw).hexdigest()}  {name}\n".encode()}
def files(candidate="a" * 40, digest="sha256:" + "c" * 64, run_id=99, attempt=1):
    rel = {
        "source_sha": candidate, "image": {"digest": digest},
        "release_contract_result": "passed", "smoke": {"healthcheck": {"result": "passed"}},
        "deployment_outcome": "success", "github": {"run_id": run_id, "run_attempt": attempt},
    }
    pub = {
        "source_sha": candidate, "image": {"digest": digest},
        "publication_outcome": "success", "run_id": run_id, "run_attempt": attempt,
    }
    return bundle(rel, "release.json"), bundle(pub, "publication.json")
def test_staging_and_workflow_gates():
    verify.verify_staging_workflow_shards()
    candidate, digest = "a" * 40, "sha256:" + "c" * 64
    run, arts, jobs = evidence(candidate)
    rel, pub = files(candidate, digest)
    with mock.patch.object(verify, "subprocess") as sp:
        sp.call.return_value = 0
        verify.verify_staging_run(
            run, arts, jobs, candidate, digest,
            github_sha="d" * 40, release_files=rel, publication_files=pub,
        )
    bad = copy.deepcopy(run)
    bad["conclusion"] = "failure"
    with pytest.raises(ValueError, match="Staging run provenance"):
        verify.verify_staging_run(bad, arts, jobs, candidate, digest)
    raw = WORKFLOW.read_text()
    wf = yaml.safe_load(raw)
    assert set(wf["jobs"]) == {"gate", "publish-and-deploy"}
    gate, publish = wf["jobs"]["gate"], wf["jobs"]["publish-and-deploy"]
    assert "environment" not in gate
    assert publish["environment"] == "production"
    assert publish["permissions"]["id-token"] == "write"
    assert "severity-exit-code" not in raw and "--exit-code 1" in raw
    assert "ref: ${{ inputs.candidate_sha }}" not in raw
    assert "scripts/release_verified_candidate_ops.py" in raw
    on = wf.get("on", wf.get(True, {}))
    inputs = on["workflow_dispatch"]["inputs"]
    assert inputs["github_hosted_fallback_authorized"]["description"].startswith("Operator attestation:")
    assert inputs["provider_signed_recall_callback_verified"]["description"].startswith("Operator attestation:")
    for job in (gate, publish):
        assert "inputs.github_hosted_fallback_authorized" in job["if"]
        steps = job["steps"]
        req = next(i for i, s in enumerate(steps) if s.get("name") == "Require candidate SHA equals GITHUB_SHA before checkout")
        co = next(i for i, s in enumerate(steps) if isinstance(s.get("uses"), str) and "actions/checkout@" in s["uses"])
        assert req < co and "ref" not in steps[co].get("with", {})
    ps = publish["steps"]
    names = [s.get("name") for s in ps]
    assert names.index("Scan the built production image with Trivy before publish") < next(
        i for i, s in enumerate(ps) if isinstance(s.get("uses"), str) and "docker/login-action@" in s["uses"]
    )
    assert names.index("Reverify current master and admit production context before publish") < next(
        i for i, s in enumerate(ps) if isinstance(s.get("uses"), str) and "docker/login-action@" in s["uses"]
    )
    assert not ONE_TIME.exists()
def test_allowlist_retirement_actionlint(monkeypatch):
    import importlib.util
    import sys
    spec = importlib.util.spec_from_file_location("pcc", ROOT / "scripts/production_core_candidate.py")
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.path.pop(0)
    ref = f"{mod.REPOSITORY}/{mod.RELEASE_VERIFIED_CANDIDATE_WORKFLOW}@refs/heads/master"
    assert ref in mod.ALLOWED_CONTEXT_WORKFLOW_REFS
    for k, v in {
        "GITHUB_ACTOR": "JohnClem", "GITHUB_TRIGGERING_ACTOR": "JohnClem",
        "GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_REF": "refs/heads/master",
        "GITHUB_REPOSITORY": mod.REPOSITORY, "GITHUB_WORKFLOW_REF": ref,
        "GITHUB_SHA": "a" * 40, "GITHUB_RUN_ID": "1", "GITHUB_RUN_ATTEMPT": "1",
    }.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(mod, "command", lambda *a, **k: pytest.fail("refuse before client"))
    with pytest.raises(mod.CandidateRefused):
        mod.source_context()
    monkeypatch.setenv("GITHUB_REPOSITORY", verify.REPOSITORY)
    monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
    monkeypatch.setenv("GITHUB_REF", "refs/heads/master")
    monkeypatch.setenv("GITHUB_ACTOR", "JohnClem")
    monkeypatch.setenv("GITHUB_TRIGGERING_ACTOR", "JohnClem")
    monkeypatch.setenv("GITHUB_SHA", "a" * 40)
    with pytest.raises(ValueError, match="CANDIDATE_SHA must equal"):
        verify.verify_founder_and_source("b" * 40)
    assert not (ROOT / "scripts/verify_one_time_release_reuse.py").exists()
    assert ACTIONLINT.is_file()
    result = subprocess.run(
        [str(ACTIONLINT), "-shellcheck=", str(WORKFLOW)], cwd=ROOT, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stdout + result.stderr
    help_result = subprocess.run(
        ["docker", "run", "--rm", TRIVY, "image", "--help"],
        capture_output=True, text=True, timeout=120,
    )
    assert help_result.returncode == 0
    assert "--severity-exit-code" not in help_result.stdout and "--exit-code" in help_result.stdout
    seen = []
    def fake_api(path):
        seen.append(path)
        if "artifacts" in path:
            return {"artifacts": []}
        if "jobs" in path:
            return {"total_count": 0, "jobs": []}
        return {
            "id": 1, "name": verify.STAGING_WORKFLOW_NAME, "path": verify.STAGING_WORKFLOW,
            "event": "workflow_dispatch", "head_branch": "master", "head_sha": "b" * 40,
            "status": "completed", "conclusion": "success", "run_attempt": 1,
            "repository": {"full_name": verify.REPOSITORY},
            "actor": {"login": "JohnClem"}, "triggering_actor": {"login": "JohnClem"},
        }
    for k, v in {
        "CANDIDATE_SHA": "a" * 40, "STAGING_RUN_ID": "99",
        "STAGING_IMAGE_DIGEST": "sha256:" + "c" * 64, "GITHUB_SHA": "a" * 40,
        "GITHUB_REPOSITORY": verify.REPOSITORY, "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_REF": "refs/heads/master", "GITHUB_ACTOR": "JohnClem",
        "GITHUB_TRIGGERING_ACTOR": "JohnClem",
    }.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(verify, "api", fake_api)
    monkeypatch.setattr(verify, "verify_founder_and_source", lambda *_: None)
    monkeypatch.setattr(verify, "verify_staging_workflow_shards", lambda: None)
    with pytest.raises(ValueError):
        verify.main()
    assert any(p.endswith("/artifacts?per_page=100") for p in seen)
