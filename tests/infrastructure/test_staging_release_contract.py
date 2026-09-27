"""Verify candidate publication, exact live recovery, and staging-only config."""

import copy
import hashlib
import json
import runpy
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

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


def companion_runtime():
    return runtime() | {
        "AGENT_API_COMPANION_CLIENT_ID": "33333333-3333-3333-3333-333333333333",
        "COMPANION_ENCOUNTER_READ_ENABLED": "true",
        "COMPANION_RECORD_READ_ENABLED": "true",
        "COMPANION_APPOINTMENT_SETUP_ENABLED": "true",
    }


def test_runtime_accepts_legacy_companion_and_explicit_withdrawal_without_mutation():
    withdrawn = runtime() | {key: "false" for key in m["COMPANION_FLAGS"]}
    withdrawn["AGENT_API_COMPANION_CLIENT_ID"] = ""
    for candidate in (runtime(), companion_runtime(), withdrawn):
        before = dict(candidate)
        m["validate_runtime"](candidate)
        assert candidate == before


@pytest.mark.parametrize(
    "key,value",
    [
        ("AGENT_API_COMPANION_CLIENT_ID", ""),
        ("AGENT_API_COMPANION_CLIENT_ID", "not-a-client-id"),
        ("AGENT_API_COMPANION_CLIENT_ID", "00000000-0000-0000-0000-000000000000"),
        ("AGENT_API_COMPANION_CLIENT_ID", "22222222-2222-2222-2222-222222222222"),
        ("COMPANION_ENCOUNTER_READ_ENABLED", "yes"),
        ("COMPANION_RECORD_READ_ENABLED", True),
        ("COMPANION_APPOINTMENT_SETUP_ENABLED", "1"),
    ],
)
def test_runtime_rejects_invalid_companion_admission(key, value):
    candidate = companion_runtime()
    candidate[key] = value
    with pytest.raises(ValueError):
        m["validate_runtime"](candidate)


@pytest.mark.parametrize("key", sorted(m["COMPANION_FIELDS"]))
def test_runtime_rejects_partial_companion_groups(key):
    candidate = companion_runtime()
    del candidate[key]
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


def reuse_fixture(directory):
    original, published = fixtures()
    original.update(
        created_at="2026-09-25T10:00:00+00:00",
        expires_at="2026-09-25T12:00:00+00:00",
        source_archive_sha256="1" * 64,
        github_user_sha256="2" * 64,
        engine={"id_sha256": "3" * 64},
        **{
            key: {"path": "/private/tmp/old", "sha256": "4" * 64}
            for key in ("frontend", "frontend_origin", "rehearsal", "native_archive")
        },
    )
    published["recorded_at"] = "2026-09-25T11:00:00Z"
    fresh = copy.deepcopy(original)
    fresh.update(
        controller_sha="5" * 40,
        created_at="2026-09-26T10:00:00+00:00",
        expires_at="2026-09-26T12:00:00+00:00",
    )

    def save(name, value):
        path = directory / name
        raw = json.dumps(value).encode()
        path.write_bytes(raw)
        return {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest()}

    old_ref = save("original.json", original)
    published["build_plan_sha256"] = old_ref["sha256"]
    pub_ref = save("published.json", published)
    envelope: dict[str, Any] = dict(
        schema_version=2,
        action="reuse_published_staging_image",
        plan_sha256="6" * 64,
        original_plan=old_ref,
        original_publication=pub_ref,
    )
    return fresh, envelope, published, save


def test_reuse_preserves_original_publication_and_accepts_reviewed_new_controller():
    with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
        plan, envelope, published, _ = reuse_fixture(Path(directory).resolve())
        result = m["deployment_inputs"](plan, "6" * 64, envelope)
        assert result["image_digest"] == published["image_digest"]
        assert m["resolve_publication"](plan, "6" * 64, envelope) == published
        # The new plan may move evidence without altering its authenticated bytes.
        plan["frontend"]["path"] = "/private/tmp/fresh"
        m["deployment_inputs"](plan, "6" * 64, envelope)


def test_cli_emits_an_explicit_reuse_binding_and_consumes_it_without_cloud_calls():
    with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
        plan, envelope, published, save = reuse_fixture(Path(directory).resolve())
        fresh = save("fresh.json", plan)
        command = [sys.executable, m["__file__"]]
        raw = subprocess.check_output(
            command
            + [
                "--reuse-publication",
                fresh["path"],
                fresh["sha256"],
                envelope["original_plan"]["path"],
                envelope["original_plan"]["sha256"],
                envelope["original_publication"]["path"],
                envelope["original_publication"]["sha256"],
            ]
        )
        binding = json.loads(raw)
        assert binding["action"] == "reuse_published_staging_image"
        assert binding["plan_sha256"] == fresh["sha256"]
        saved_binding = save("reuse.json", binding)
        resolved = json.loads(
            subprocess.check_output(
                command
                + [
                    "--deployment-inputs",
                    fresh["path"],
                    fresh["sha256"],
                    saved_binding["path"],
                    saved_binding["sha256"],
                ]
            )
        )
        assert resolved["image_digest"] == published["image_digest"]


@pytest.mark.parametrize(
    "key",
    [
        "candidate_sha",
        "candidate_pr",
        "source_archive_sha256",
        "github_user_sha256",
        "engine",
        "release",
        "frontend",
        "frontend_origin",
        "rehearsal",
        "native_archive",
    ],
)
def test_reuse_refuses_changed_build_or_release_inputs(key):
    with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
        plan, envelope, _, _ = reuse_fixture(Path(directory).resolve())
        if key in ("frontend", "frontend_origin", "rehearsal", "native_archive"):
            plan[key]["sha256"] = "0" * 64
        elif key == "release":
            plan[key]["recovery"]["cosmaneura-staging-app"]["revision"] += "-changed"
        else:
            plan[key] = "changed"
        with pytest.raises(ValueError):
            m["deployment_inputs"](plan, "6" * 64, envelope)


@pytest.mark.parametrize(
    "change",
    [
        "fresh_hash",
        "tamper",
        "chain",
        "extra",
        "late_publication",
        "wrong_original_plan",
        "failed",
    ],
)
def test_reuse_refuses_unbound_tampered_or_unpublished_evidence(change):
    with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
        plan, envelope, published, save = reuse_fixture(Path(directory).resolve())
        if change == "fresh_hash":
            envelope["plan_sha256"] = "0" * 64
        elif change == "tamper":
            Path(envelope["original_publication"]["path"]).write_text("{}")
        elif change == "extra":
            envelope["skip_verification"] = True
        else:
            if change == "chain":
                published = envelope
            elif change == "late_publication":
                published["recorded_at"] = "2026-09-25T12:00:00Z"
            elif change == "wrong_original_plan":
                published["build_plan_sha256"] = "0" * 64
            elif change == "failed":
                published["complete"] = False
            envelope["original_publication"] = save("altered.json", published)
        with pytest.raises(ValueError):
            m["deployment_inputs"](plan, "6" * 64, envelope)
