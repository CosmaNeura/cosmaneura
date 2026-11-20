# fmt: off
"""Gates for Release verified candidate."""
from __future__ import annotations  # noqa: I001
import copy
import hashlib
import http.client
import json
import subprocess
import urllib.error
from pathlib import Path
from unittest import mock
import pytest
import yaml
from scripts import release_verified_candidate_ops as ops
from scripts import verify_release_verified_candidate as verify
ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/release_verified_candidate.yml"
ONE_TIME = ROOT / ".github/workflows/production_release_verified.yml"
ACTIONLINT = Path("/tmp/actionlint-bin/actionlint")
EXPECTED = "cosmaneuraprodregistry.azurecr.io/cosmaneura/cosmaneura@sha256:" + "c" * 64
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
def baseline_payload(schema="aaaaaaaaaaaa"):
    return {
        "resource_group": ops.RG, "schema_revision": schema,
        "migration_policy": ops.POLICY, "healthcheck_url": ops.HEALTH,
        "apps": {
            n: {"image": f"old@{n}", "latestReadyRevisionName": f"{n}-r1"}
            for n in ops.APPS
        },
    }
def test_staging_and_workflow_gates():
    verify.verify_staging_workflow_shards()
    candidate, digest = "a" * 40, "sha256:" + "c" * 64
    run, arts, jobs = evidence(candidate)
    rel, pub = files(candidate, digest)
    with mock.patch.object(verify, "subprocess") as sp:
        sp.call.return_value = 0
        verify.verify_staging_run(
            run, arts, jobs, candidate, digest, github_sha="d" * 40,
            release_files=rel, publication_files=pub,
        )
    bad = copy.deepcopy(run)
    bad["conclusion"] = "failure"
    with pytest.raises(ValueError, match="Staging provenance"):
        verify.verify_staging_run(bad, arts, jobs, candidate, digest)
    raw = WORKFLOW.read_text()
    wf = yaml.safe_load(raw)
    assert set(wf["jobs"]) == {"gate", "publish-and-deploy"}
    gate, publish = wf["jobs"]["gate"], wf["jobs"]["publish-and-deploy"]
    assert "environment" not in gate and publish["environment"] == "production"
    assert publish["permissions"]["id-token"] == "write"
    assert "severity-exit-code" not in raw and "--exit-code 1" in raw
    assert "ref: ${{ inputs.candidate_sha }}" not in raw
    assert "GITHUB_WORKSPACE/.release-artifacts" not in raw
    assert "$RUNNER_TEMP/release-artifacts" in raw
    assert "${{ runner.temp }}/release-artifacts" in raw
    assert not ONE_TIME.exists()
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
    login = next(i for i, s in enumerate(ps) if isinstance(s.get("uses"), str) and "docker/login-action@" in s["uses"])
    assert names.index("Scan the built production image with Trivy before publish") < login
    assert names.index("Reverify current master and admit production context before publish") < login
def test_h10_artifacts_outside_checkout_keep_context_clean(tmp_path, monkeypatch):
    assert "GITHUB_WORKSPACE/.release-artifacts" not in WORKFLOW.read_text()
    assert "$RUNNER_TEMP/release-artifacts" in WORKFLOW.read_text()
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    subprocess.check_call(["git", "init", "-q"], cwd=checkout)
    outside = tmp_path / "runner-temp" / "release-artifacts"
    outside.mkdir(parents=True)
    (outside / "scanned-production-image.tar").write_bytes(b"img")
    monkeypatch.chdir(checkout)
    assert subprocess.check_output(
        ["git", "ls-files", "--others", "--exclude-standard"], cwd=checkout
    ) == b""
    (checkout / ".release-artifacts").mkdir()
    (checkout / ".release-artifacts" / "leak").write_text("x")
    assert b".release-artifacts" in subprocess.check_output(
        ["git", "ls-files", "--others", "--exclude-standard"], cwd=checkout
    )
def test_allowlist_retirement_actionlint(monkeypatch):
    import importlib.util
    import sys
    import urllib.request
    if not ACTIONLINT.is_file():
        ACTIONLINT.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(
            "https://github.com/rhysd/actionlint/releases/download/v1.7.7/actionlint_1.7.7_linux_amd64.tar.gz",
            ACTIONLINT.parent / "actionlint.tgz",
        )
        subprocess.check_call([
            "tar", "-xzf", str(ACTIONLINT.parent / "actionlint.tgz"),
            "-C", str(ACTIONLINT.parent), "actionlint",
        ])
        ACTIONLINT.chmod(0o755)
    assert ACTIONLINT.is_file(), "actionlint required at /tmp/actionlint-bin/actionlint"
    spec = importlib.util.spec_from_file_location("pcc", ROOT / "scripts/production_core_candidate.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.path.pop(0)
    ref = f"{mod.REPOSITORY}/{mod.RELEASE_VERIFIED_CANDIDATE_WORKFLOW}@refs/heads/master"
    assert ref in mod.ALLOWED_CONTEXT_WORKFLOW_REFS
    env = {
        "GITHUB_ACTOR": "JohnClem", "GITHUB_TRIGGERING_ACTOR": "JohnClem",
        "GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_REF": "refs/heads/master",
        "GITHUB_REPOSITORY": mod.REPOSITORY, "GITHUB_WORKFLOW_REF": ref,
        "GITHUB_SHA": "a" * 40, "GITHUB_RUN_ID": "1", "GITHUB_RUN_ATTEMPT": "1",
    }
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(mod, "command", lambda *a, **k: pytest.fail("refuse before client"))
    with pytest.raises(mod.CandidateRefused):
        mod.source_context()
    monkeypatch.setenv("GITHUB_REPOSITORY", verify.REPOSITORY)
    monkeypatch.setenv("GITHUB_SHA", "a" * 40)
    with pytest.raises(ValueError, match="CANDIDATE_SHA must equal"):
        verify.verify_founder_and_source("b" * 40)
    assert not (ROOT / "scripts/verify_one_time_release_reuse.py").exists()
    result = subprocess.run(
        [str(ACTIONLINT), "-shellcheck=", str(WORKFLOW)], cwd=ROOT,
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
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
@pytest.mark.parametrize(
    "exc",
    [
        urllib.error.URLError("boom"),
        TimeoutError("timeout"),
        http.client.RemoteDisconnected("remote"),
        http.client.BadStatusLine("bad"),
        ConnectionError("conn"),
        KeyError("properties"),
        subprocess.CalledProcessError(1, ["az"]),
    ],
)
def test_n1_verify_ready_exceptions_route_to_fail_and_rollback(tmp_path, monkeypatch, exc):
    baseline = tmp_path / "baseline.json"
    evidence = tmp_path / "evidence.json"
    baseline.write_text(json.dumps(baseline_payload()) + "\n")
    monkeypatch.setenv("PRODUCTION_SCHEMA_REVISION", "aaaaaaaaaaaa")
    rolled = []
    monkeypatch.setattr(ops, "rollback", lambda path: rolled.append(path))
    def boom(*_a, **_k):
        raise exc
    ok = mock.MagicMock(
        __enter__=lambda s: s, __exit__=lambda *a: False, status=200,
        getcode=lambda: 200, read=lambda n=256: b"ok",
    )
    if isinstance(exc, (KeyError, subprocess.CalledProcessError)):
        monkeypatch.setattr(ops.urllib.request, "urlopen", lambda *a, **k: ok)
        monkeypatch.setattr(ops, "az_json", boom)
    else:
        monkeypatch.setattr(ops.urllib.request, "urlopen", boom)
    with pytest.raises(SystemExit):
        ops.verify_ready(EXPECTED, baseline, evidence)
    assert rolled == [baseline]
    assert evidence.is_file()
def test_n2_migrated_schema_rollback_refuses(tmp_path, monkeypatch):
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps(baseline_payload("aaaaaaaaaaaa")) + "\n")
    monkeypatch.setenv("PRODUCTION_SCHEMA_REVISION", "bbbbbbbbbbbb")
    calls = []
    monkeypatch.setattr(ops.subprocess, "check_call", lambda *a, **k: calls.append(a))
    with pytest.raises(SystemExit) as raised:
        ops.rollback(baseline)
    assert raised.value.code == 1
    assert calls == []
def test_n2_partial_rollback_reports_all_apps(tmp_path, monkeypatch):
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps(baseline_payload("aaaaaaaaaaaa")) + "\n")
    monkeypatch.setenv("PRODUCTION_SCHEMA_REVISION", "aaaaaaaaaaaa")
    seen = []
    def fail_all(args, **_k):
        seen.append(args[args.index("-n") + 1])
        raise subprocess.CalledProcessError(1, args)
    monkeypatch.setattr(ops.subprocess, "check_call", fail_all)
    with pytest.raises(SystemExit) as raised:
        ops.rollback(baseline)
    assert raised.value.code == 1
    assert seen == list(ops.APPS)
