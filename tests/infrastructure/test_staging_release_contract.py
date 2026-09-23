"""Verify candidate publication, exact live recovery, and staging-only config."""

import copy
import hashlib
import runpy
import tempfile
from pathlib import Path

import pytest

m = runpy.run_path(
    str(Path(__file__).resolve().parents[2] / "scripts/staging_release_contract.py")
)


def runtime():
    result = {key: "true" for key in m["FLAGS"]}
    result.update(
        SCRIBE_SERVICE_BASE_URL="https://cosmaneura-staging-scribe-api.internal.politesmoke-7989878f.northcentralus.azurecontainerapps.io",
        SCRIBE_SERVICE_SCOPE="api://57264fe1-6a4a-4946-8412-e0be18805977/.default",
        SCRIBE_MANAGED_IDENTITY_CLIENT_ID="11111111-1111-1111-1111-111111111111",
        AGENT_API_SCRIBE_RECEIVER_CLIENT_ID="22222222-2222-2222-2222-222222222222",
        SCRIBE_SERVICE_TIMEOUT_SECONDS="60",
        SCRIBE_CAPTURE_UPLOAD_ORIGINS="https://syntheticstaging.blob.core.windows.net",
        SESSION_PREP_PROVIDER_CLERK_SUBJECTS="user_syntheticreview",
    )
    return result


def fixtures():
    recovery = {
        name: {"image": m["IMAGE"] + "@sha256:" + "a" * 64, "revision": name + "--old"}
        for name in m["APPS"]
    }
    recovery["cosmaneura-staging-app"]["image"] = (
        m["REGISTRY"] + "/scribe-beta/core@sha256:" + "b" * 64
    )
    plan = {
        "version": 2,
        "candidate_sha": "c" * 40,
        "candidate_pr": 500,
        "controller_sha": "d" * 40,
        "release": {"runtime_public": runtime(), "recovery": recovery},
    }
    published = {
        "schema_version": 1,
        "complete": True,
        "outcome": "success",
        "phase": "complete",
        "publication_started": True,
        "production_operation": False,
        "candidate_sha": "c" * 40,
        "controller_sha": "d" * 40,
        "build_plan_sha256": "e" * 64,
        "image_digest": "sha256:" + "f" * 64,
        "image_reference": m["IMAGE"] + "@sha256:" + "f" * 64,
    }
    return plan, published


def test_publication_is_bound_to_reviewed_plan_candidate_controller_and_registry():
    plan, published = fixtures()
    assert (
        m["deployment_inputs"](plan, "e" * 64, published)["image_digest"]
        == published["image_digest"]
    )
    for key, value in [
        ("candidate_sha", "0" * 40),
        ("controller_sha", "0" * 40),
        ("build_plan_sha256", "0" * 64),
        ("complete", False),
        ("production_operation", True),
        ("image_reference", "elsewhere@sha256:" + "f" * 64),
    ]:
        changed = dict(published, **{key: value})
        with pytest.raises(ValueError):
            m["deployment_inputs"](plan, "e" * 64, changed)


@pytest.mark.parametrize(
    "key,value",
    [
        ("SCRIBE_SERVICE_BASE_URL", "https://production.example"),
        ("SCRIBE_SERVICE_TIMEOUT_SECONDS", "999"),
        ("SCRIBE_WORKFLOW_ENABLED", "false"),
        ("SESSION_PREP_PROVIDER_CLERK_SUBJECTS", "*"),
        ("DATABASE_URL", "secret"),
        ("SCRIBE_CAPTURE_UPLOAD_ORIGINS", "https://account.blob.core.windows.net/path"),
    ],
)
def test_runtime_rejects_unbounded_or_wrong_target_values(key, value):
    candidate = runtime()
    candidate[key] = value
    with pytest.raises(ValueError):
        m["validate_runtime"](candidate)


def test_recovery_accepts_split_baseline_but_rejects_image_revision_or_workload_drift():
    plan, _ = fixtures()
    recovery = plan["release"]["recovery"]
    rows = [
        {
            "name": name,
            "readyRevisionImage": v["image"],
            "latestReadyRevision": v["revision"],
        }
        for name, v in recovery.items()
    ]
    m["verify_recovery"](recovery, rows)
    for field, value in [
        ("readyRevisionImage", m["IMAGE"] + "@sha256:" + "0" * 64),
        ("latestReadyRevision", "changed"),
    ]:
        changed = copy.deepcopy(rows)
        changed[0][field] = value
        with pytest.raises(ValueError):
            m["verify_recovery"](recovery, changed)
    with pytest.raises(ValueError):
        m["verify_recovery"](recovery, rows[1:])
    with pytest.raises(ValueError):
        m["verify_recovery"](recovery, [rows[0]] * 3)


def test_bound_input_refuses_tampered_or_writable_shared_content():
    temporary = tempfile.TemporaryDirectory(dir="/private/tmp")
    path = Path(temporary.name).resolve() / "receipt.json"
    raw = b'{"complete":true}\n'
    path.write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    assert m["bound_json"](str(path), digest)["complete"] is True
    path.write_bytes(b'{"complete":false}\n')
    with pytest.raises(ValueError):
        m["bound_json"](str(path), digest)
    path.write_bytes(raw)
    path.chmod(0o666)
    with pytest.raises(ValueError):
        m["bound_json"](str(path), digest)
