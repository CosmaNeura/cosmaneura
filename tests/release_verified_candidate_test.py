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
OPS_SRC = (ROOT / "scripts/release_verified_candidate_ops.py").read_text()
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
def baseline_payload(*, running="aaaaaaaaaaaa", candidate="aaaaaaaaaaaa", migrate=None):
    migrate = running != candidate if migrate is None else migrate
    return {
        "resource_group": ops.RG,
        "running_source_sha": "b" * 40,
        "candidate_sha": "a" * 40,
        "schema_head_running": running,
        "schema_head_candidate": candidate,
        "migration_in_release": migrate,
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
    assert "admit-schema" in raw and "PRODUCTION_SCHEMA_REVISION" not in raw + OPS_SRC
    assert not ONE_TIME.exists()
    names = [s.get("name") for s in publish["steps"]]
    assert names.index("Admit code-derived schema heads before publish") < names.index(
        "Publish scanned image with equivalent Core admission checks"
    )
def test_h10_artifacts_outside_checkout_keep_context_clean(tmp_path, monkeypatch):
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
def test_allowlist_retirement_actionlint():
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
    result = subprocess.run(
        [str(ACTIONLINT), "-shellcheck=", str(WORKFLOW)], cwd=ROOT,
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
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
def test_equal_heads_rollback_runs_and_reports_all_failures(tmp_path, monkeypatch):
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps(baseline_payload()) + "\n")
    seen = []
    monkeypatch.setattr(ops, "app_image", lambda name: f"live@{name}")
    def fail_all(args, **_k):
        seen.append(args[args.index("-n") + 1])
        raise subprocess.CalledProcessError(1, args)
    monkeypatch.setattr(ops.subprocess, "check_call", fail_all)
    with pytest.raises(SystemExit) as raised:
        ops.rollback(baseline)
    assert raised.value.code == 1
    assert seen == list(ops.APPS)
def test_different_heads_rollback_refuses_and_leaves_image(tmp_path, monkeypatch):
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps(baseline_payload(running="aaaaaaaaaaaa", candidate="bbbbbbbbbbbb")) + "\n")
    calls = []
    monkeypatch.setattr(ops.subprocess, "check_call", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(ops, "app_image", lambda *_: pytest.fail("must not read live image"))
    with pytest.raises(SystemExit) as raised:
        ops.rollback(baseline)
    assert raised.value.code == 1
    assert calls == []
def test_equal_heads_skips_swap_when_already_at_baseline(tmp_path, monkeypatch):
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps(baseline_payload()) + "\n")
    monkeypatch.setattr(ops, "app_image", lambda name: f"old@{name}")
    calls = []
    monkeypatch.setattr(ops.subprocess, "check_call", lambda *a, **k: calls.append(a))
    ops.rollback(baseline)
    assert calls == []
def test_schema_refusal_before_publish_leaves_no_locked_tag(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTAINER_REGISTRY_HOST", ops.HOST)
    monkeypatch.setenv("SCANNED_IMAGE_ARCHIVE_SHA256", "a" * 64)
    monkeypatch.setenv("GITHUB_RUN_ID", "1")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    locked = []
    monkeypatch.setattr(ops, "admit_schema_before_publish", lambda *a, **k: ops.refuse("schema boom"))
    monkeypatch.setattr(ops.subprocess, "check_call", lambda *a, **k: locked.append(a))
    monkeypatch.setattr(ops, "run", lambda *a, **k: pytest.fail("no registry run"))
    cfg = tmp_path / "cfg.json"
    cfg.write_text("{}")
    archive = tmp_path / "img.tar"
    archive.write_bytes(b"x")
    with pytest.raises(SystemExit):
        ops.publish(cfg, "a" * 40, archive, schema_output=tmp_path / "schema.json")
    assert locked == []
def test_production_schema_revision_env_is_not_read(monkeypatch):
    monkeypatch.setenv("PRODUCTION_SCHEMA_REVISION", "cccccccccccc")
    assert "PRODUCTION_SCHEMA_REVISION" not in OPS_SRC
    monkeypatch.setattr(ops, "schema_head", lambda sha: "aaaaaaaaaaaa")
    evidence = ops.schema_evidence("b" * 40, "a" * 40)
    assert evidence["migration_in_release"] is False
