"""Secretless candidate/provenance checks; no real client, loader or deployment."""

import base64
import importlib.util
import io
import json
import socket
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any, TypedDict

import pytest
import yaml

from tests.infrastructure.test_production_saved_image_identity import (
    fixture as saved_fixture,
)
from tests.infrastructure.test_production_saved_image_identity import (
    save as save_fixture,
)

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location(
    "production_core_candidate", ROOT / "scripts/production_core_candidate.py"
)
assert spec and spec.loader
candidate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(candidate)
SHA = "a" * 40
TREE = "b" * 40
IMAGE_DIGEST = "sha256:" + "c" * 64
CONFIG_DIGEST = saved_fixture(engine="classic")[0]["Id"]


class ProducerEvidence(TypedDict):
    run: dict[str, Any]
    workflow: dict[str, Any]
    jobs: dict[str, Any]
    artifact: dict[str, Any]
    archive: bytes


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refused(*args, **kwargs):
        raise AssertionError("network forbidden in source tests")

    monkeypatch.setattr(socket, "socket", refused)
    monkeypatch.setattr(socket, "create_connection", refused)


def configuration():
    config = dict.fromkeys(candidate.PUBLIC_KEYS, "synthetic-public-setting")
    config.update(
        NODE_ENV="production",
        OPENAPI_JSON_PATH="openapi.json",
        VITE_PYTHON_URL="https://app.cosmaneura.com/",
        VITE_INTAKE_CHAT_DOMAIN="chat.cosmaneura.com",
        VITE_SOVEREIGN_PROVIDER_URL="https://sovereign.cosmaneura.com",
        VITE_SOVEREIGN_PLATFORM_AUTHORITY_ENABLED="false",
        VITE_CLERK_PUBLIC_KEY="pk_live_"
        + base64.b64encode(b"clerk.example.invalid$").decode().rstrip("="),
    )
    return config


def context(run=20):
    return dict(source_sha=SHA, source_tree=TREE, run_id=run, run_attempt=1)


def local_image() -> dict[str, Any]:
    return saved_fixture(engine="classic")[0]


def manifest_bytes() -> bytes:
    return saved_fixture()[2]


def retention_bytes(image_digest) -> bytes:
    flags: dict[str, bool] = dict.fromkeys(("writeEnabled", "deleteEnabled"), False)
    flags.update(dict.fromkeys(("readEnabled", "listEnabled"), True))
    return candidate.canonical(dict(digest=image_digest, changeableAttributes=flags))


def runtime_environment() -> dict[str, str]:
    return dict(
        GITHUB_ACTOR="JohnClem",
        GITHUB_TRIGGERING_ACTOR="JohnClem",
        GITHUB_EVENT_NAME="workflow_dispatch",
        GITHUB_REF="refs/heads/master",
        GITHUB_REPOSITORY=candidate.REPOSITORY,
        GITHUB_WORKFLOW_REF=candidate.WORKFLOW_REF,
        GITHUB_SHA=SHA,
        GITHUB_RUN_ID="20",
        GITHUB_RUN_ATTEMPT="1",
    )


def admitted_frontend(tmp_path, monkeypatch, run=10):
    for key, value in runtime_environment().items():
        monkeypatch.setenv(key, str(run) if key == "GITHUB_RUN_ID" else value)
    config_path, admission_path = (
        tmp_path / f"config-{run}.json",
        tmp_path / f"admission-{run}.json",
    )
    config_path.write_bytes(candidate.canonical(configuration()))
    candidate.frontend_admission.prepare(config_path, admission_path, "true", SHA, SHA)
    return json.loads(config_path.read_bytes()), admission_path


def enabled_candidate(tmp_path, monkeypatch):
    config, admission_path = admitted_frontend(tmp_path, monkeypatch)
    image, exported, manifest = saved_fixture(engine="classic")
    image_archive = save_fixture(tmp_path / "enabled-candidate-image.tar", exported)
    saved_identity = candidate.saved_image_identity(image_archive, image)
    value = candidate.candidate_record(
        context(10),
        config,
        manifest,
        image,
        "sha256:" + "f" * 64,
        record()["panel"],
        candidate.digest(manifest),
        saved_identity,
        admission_path,
    )
    return value, config, admission_path


def evidence_for(value):
    data = evidence()
    data["archive"] = archive(value)
    data["artifact"]["digest"] = candidate.digest(data["archive"])
    data["artifact"]["size_in_bytes"] = len(data["archive"])
    return data


def test_enabled_candidate_binds_actual_admission_to_authenticated_producer(
    tmp_path, monkeypatch
):
    value, config, admission = enabled_candidate(tmp_path, monkeypatch)
    assert value["schema_version"] == 2 and value["frontend_authority_enabled"] is True
    assert value["frontend_admission"] == json.loads(admission.read_bytes())
    assert value["public_configuration_digest"] == candidate.digest(
        candidate.canonical(config)
    )
    assert (
        candidate.verify_evidence(context(), 10, 1, 30, **evidence_for(value)) == value
    )


@pytest.mark.parametrize(
    "tamper",
    [
        "missing",
        "source",
        "run",
        "actor",
        "workflow",
        "configuration",
        "version",
        "extra",
        "false",
    ],
)
def test_enabled_archive_rejects_unbound_or_malformed_admission(
    tmp_path, monkeypatch, tamper
):
    value, _, _ = enabled_candidate(tmp_path, monkeypatch)
    admission = value["frontend_admission"]
    if tamper == "missing":
        del value["frontend_admission"]
    elif tamper == "source":
        admission["source_sha"] = "1" * 40
    elif tamper == "run":
        admission["manual_workflow_context"]["GITHUB_RUN_ID"] = "11"
    elif tamper == "actor":
        admission["manual_workflow_context"]["GITHUB_ACTOR"] = "Other"
    elif tamper == "workflow":
        admission["manual_workflow_context"]["GITHUB_WORKFLOW_REF"] = "retired@master"
    elif tamper == "configuration":
        admission["public_configuration_sha256"] = "0" * 64
    elif tamper == "version":
        admission["version"] = True
    elif tamper == "extra":
        admission["readiness"] = True
    else:
        value["frontend_authority_enabled"] = False
    with pytest.raises(ValueError):
        candidate.verify_evidence(context(), 10, 1, 30, **evidence_for(value))


@pytest.mark.parametrize("run,source", [(20, SHA), (10, "1" * 40)])
def test_producer_admission_cannot_be_reused_in_another_run_or_source(
    tmp_path, monkeypatch, run, source
):
    value, config, admission = enabled_candidate(tmp_path, monkeypatch)
    monkeypatch.setenv("GITHUB_RUN_ID", str(run))
    monkeypatch.setenv("GITHUB_SHA", source)
    monkeypatch.setattr(
        candidate,
        "command",
        lambda *a, **k: pytest.fail("client called before admission refusal"),
    )
    with pytest.raises(ValueError):
        candidate.promotion_check(value, config, admission)


def test_fresh_promotion_admission_preserves_identical_enabled_config_and_digest(
    tmp_path, monkeypatch
):
    value, config, producer_admission = enabled_candidate(tmp_path, monkeypatch)
    fresh_config, fresh_admission = admitted_frontend(tmp_path, monkeypatch, run=20)
    assert fresh_config == config
    assert fresh_admission.read_bytes() != producer_admission.read_bytes()
    monkeypatch.setattr(candidate, "panel_identity", lambda *a: value["panel"])
    monkeypatch.setattr(candidate, "source_context", lambda: context())
    calls = []

    def read(args, **kwargs):
        calls.append(args)
        if args[:4] == ["docker", "buildx", "imagetools", "inspect"]:
            assert args[-1] == value["image"]
            return manifest_bytes()
        assert args[:4] == ["az", "acr", "repository", "show"]
        return retention_bytes(candidate.digest(manifest_bytes()))

    monkeypatch.setattr(candidate, "command", read)
    assert (
        candidate.promotion_check(value, fresh_config, fresh_admission)
        == value["image"]
    )
    assert len(calls) == 3 and all(
        "push" not in call and "build" not in call for call in calls
    )


@pytest.mark.parametrize(
    "tamper", ["default_dark", "config_changed", "receipt_missing", "record_flag"]
)
def test_enabled_promotion_refuses_changed_choice_or_configuration_before_clients(
    tmp_path, monkeypatch, tamper
):
    value, _, _ = enabled_candidate(tmp_path, monkeypatch)
    config, admission = admitted_frontend(tmp_path, monkeypatch, run=20)
    if tamper == "default_dark":
        config["VITE_SOVEREIGN_PLATFORM_AUTHORITY_ENABLED"] = "false"
    elif tamper == "config_changed":
        config["VITE_POSTHOG_KEY"] = "changed-synthetic-setting"
    elif tamper == "receipt_missing":
        admission.unlink()
    else:
        value["frontend_authority_enabled"] = False
    monkeypatch.setattr(
        candidate,
        "command",
        lambda *a, **k: pytest.fail("client called before refusal"),
    )
    with pytest.raises((ValueError, OSError)):
        candidate.promotion_check(value, config, admission)


def test_unadmitted_enabled_publish_fails_before_image_or_registry_commands(
    tmp_path, monkeypatch
):
    config = configuration()
    config["VITE_SOVEREIGN_PLATFORM_AUTHORITY_ENABLED"] = "true"
    path = tmp_path / "unadmitted.json"
    path.write_bytes(candidate.canonical(config))
    monkeypatch.setattr(candidate, "source_context", lambda: context())
    monkeypatch.setattr(
        candidate,
        "command",
        lambda *a, **k: pytest.fail("unadmitted publish reached a client"),
    )
    with pytest.raises(ValueError):
        candidate.publish(path, tmp_path / "candidate.json")


def record():
    panel_sha = "e" * 64
    return dict(
        schema_version=1,
        purpose="verified-production-core-candidate",
        repository=candidate.REPOSITORY,
        workflow_ref=candidate.WORKFLOW_REF,
        **context(10),
        image=candidate.IMAGE + "@" + IMAGE_DIGEST,
        image_config_digest=CONFIG_DIGEST,
        public_configuration_digest=candidate.digest(
            candidate.canonical(configuration())
        ),
        frontend_assets_digest="sha256:" + "f" * 64,
        panel=dict(
            sha256=panel_sha,
            upstream_commit="9" * 40,
            integrity="sha256-" + base64.b64encode(bytes.fromhex(panel_sha)).decode(),
        ),
        frontend_authority_enabled=False,
    )


def archive(value, name="candidate.json", second=False):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as stream:
        stream.writestr(name, candidate.canonical(value))
        if second:
            stream.writestr("unrelated.json", b"{}")
    return output.getvalue()


def evidence() -> ProducerEvidence:
    raw = archive(record())
    run = dict(
        id=10,
        run_attempt=1,
        status="completed",
        conclusion="success",
        event="workflow_dispatch",
        head_branch="master",
        head_sha=SHA,
        repository=dict(full_name=candidate.REPOSITORY, id=42),
        actor=dict(login="JohnClem"),
        triggering_actor=dict(login="JohnClem"),
        workflow_id=77,
        path=candidate.WORKFLOW,
    )
    workflow = dict(path=candidate.WORKFLOW, state="active", id=77)
    jobs = [
        dict(
            id=index,
            name=name,
            run_id=10,
            run_attempt=1,
            head_sha=SHA,
            status="completed",
            conclusion="skipped" if name == "🚀 Deploy" else "success",
        )
        for index, name in enumerate(
            sorted(candidate.required_jobs() | {"🚀 Deploy"}), 100
        )
    ]
    artifact = dict(
        id=30,
        expired=False,
        name=f"production-core-candidate-10-1-{SHA}",
        size_in_bytes=len(raw),
        digest=candidate.digest(raw),
        workflow_run=dict(
            id=10,
            repository_id=42,
            head_repository_id=42,
            head_sha=SHA,
            head_branch="master",
        ),
    )
    return ProducerEvidence(
        run=run,
        workflow=workflow,
        jobs=dict(total_count=len(jobs), jobs=jobs),
        artifact=artifact,
        archive=raw,
    )


def verify(value):
    return candidate.verify_evidence(context(), 10, 1, 30, **value)


def test_authenticated_successful_producer_is_admitted():
    assert verify(evidence()) == record()


@pytest.mark.parametrize("oversized", [False, True])
def test_complete_job_metadata_capacity_is_bounded(monkeypatch, oversized):
    data = evidence()
    step = dict(
        name="Acceptance check " + "x" * 64,
        status="completed",
        conclusion="success",
        started_at="2026-10-30T12:00:00Z",
        completed_at="2026-10-30T12:01:00Z",
    )
    for job in data["jobs"]["jobs"]:
        job["steps"] = [dict(number=n, **step) for n in range(20)]
    raw = candidate.canonical(data["jobs"])
    assert candidate.MAX_JSON < len(raw) <= candidate.MAX_METADATA
    monkeypatch.setattr(candidate, "command", lambda *a, **k: raw)
    path = f"repos/{candidate.REPOSITORY}/actions/runs/10/attempts/1/jobs?per_page=100"
    if oversized:
        raw = b" " * (candidate.MAX_METADATA + 1)
        with pytest.raises(candidate.CandidateRefused):
            candidate.api(path)
    else:
        data["jobs"] = candidate.api(path)
        assert verify(data) == record()


@pytest.mark.parametrize(
    "path,value",
    [
        ("run.id", 11),
        ("run.run_attempt", 2),
        ("run.status", "in_progress"),
        ("run.conclusion", "failure"),
        ("run.event", "push"),
        ("run.head_branch", "staging"),
        ("run.head_sha", "1" * 40),
        ("run.repository.full_name", "Other/fork"),
        ("run.actor.login", "Other"),
        ("run.triggering_actor.login", "Other"),
        ("run.workflow_id", 88),
        ("run.path", ".github/workflows/production_release_verified.yml"),
        ("workflow.path", "other.yml"),
        ("workflow.state", "disabled_manually"),
        ("artifact.id", 31),
        ("artifact.expired", True),
        ("artifact.name", "production-core-candidate-10-2-" + SHA),
        ("artifact.size_in_bytes", candidate.MAX_ARCHIVE + 1),
        ("artifact.digest", "sha256:" + "0" * 64),
        ("artifact.workflow_run.id", 11),
        ("artifact.workflow_run.head_sha", "1" * 40),
        ("artifact.workflow_run.head_branch", "staging"),
        ("artifact.workflow_run.repository_id", 43),
        ("artifact.workflow_run.head_repository_id", 43),
        ("jobs.total_count", 100),
    ],
)
def test_wrong_authenticated_producer_metadata_refuses(path, value):
    data = evidence()
    fields = path.split(".")
    cursor: Any = data
    for field in fields[:-1]:
        cursor = cursor[field]
    cursor[fields[-1]] = value
    with pytest.raises(candidate.CandidateRefused):
        verify(data)


@pytest.mark.parametrize(
    "field,value",
    [
        ("conclusion", "skipped"),
        ("status", "queued"),
        ("head_sha", "1" * 40),
        ("run_attempt", 2),
        ("run_id", 11),
        ("name", "Unrelated"),
    ],
)
def test_missing_or_not_successful_acceptance_job_refuses(field, value):
    data = evidence()
    selected = next(
        item
        for item in data["jobs"]["jobs"]
        if item["name"] == "Backend tests (application)"
    )
    selected[field] = value
    with pytest.raises(candidate.CandidateRefused):
        verify(data)


def test_producer_must_not_have_deployed_and_cannot_be_current_run():
    data = evidence()
    next(item for item in data["jobs"]["jobs"] if item["name"] == "🚀 Deploy")[
        "conclusion"
    ] = "success"
    with pytest.raises(candidate.CandidateRefused):
        verify(data)
    with pytest.raises(candidate.CandidateRefused):
        candidate.verify_evidence(context(10), 10, 1, 30, **evidence())


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_sha", "1" * 40),
        ("source_tree", "2" * 40),
        ("run_id", 11),
        ("run_attempt", 2),
        ("schema_version", True),
        ("purpose", "preparation-is-approval"),
        ("frontend_authority_enabled", True),
        ("image", candidate.IMAGE + ":latest"),
        ("image", candidate.IMAGE + ":" + SHA),
        (
            "image",
            "cosmaneurastagingregistry.azurecr.io/cosmaneura/cosmaneura@"
            + IMAGE_DIGEST,
        ),
        (
            "image",
            "cosmaneurasovereignprodregistry.azurecr.io/cosmaneura/sovereign@"
            + IMAGE_DIGEST,
        ),
        ("public_configuration_digest", "invalid"),
        ("repository", "Other/fork"),
        ("workflow_ref", "other-workflow"),
    ],
)
def test_even_self_consistent_archive_cannot_widen_source_or_target(field, value):
    data = evidence()
    changed = record()
    changed[field] = value
    data["archive"] = archive(changed)
    data["artifact"]["digest"] = candidate.digest(data["archive"])
    data["artifact"]["size_in_bytes"] = len(data["archive"])
    with pytest.raises(candidate.CandidateRefused):
        verify(data)


@pytest.mark.parametrize(
    "name,second",
    [("../candidate.json", False), ("candidate.json", True), ("other.json", False)],
)
def test_archive_is_single_closed_document_without_extraction(name, second):
    raw = archive(record(), name, second)
    with pytest.raises(candidate.CandidateRefused):
        candidate.archive_record(raw, candidate.digest(raw))


@pytest.mark.parametrize(
    "raw",
    [
        b'{"x":1,"x":2}',
        b'{"x":NaN}',
        b'{"x":Infinity}',
        b"not-json",
        b" " * (candidate.MAX_JSON + 1),
    ],
)
def test_duplicate_malformed_or_oversized_documents_refuse(raw):
    with pytest.raises(candidate.CandidateRefused):
        candidate.decode(raw)


@pytest.mark.parametrize(
    "field,value",
    [
        ("VITE_SOVEREIGN_PLATFORM_AUTHORITY_ENABLED", "true"),
        ("VITE_SOVEREIGN_PROVIDER_URL", "https://staging-sovereign.cosmaneura.com"),
        ("VITE_PYTHON_URL", "https://staging-app.cosmaneura.com/"),
        ("NODE_ENV", "staging"),
        ("OPENAPI_JSON_PATH", "/tmp/custom"),
        ("VITE_CLERK_PUBLIC_KEY", "pk_test_ignored"),
    ],
)
def test_current_dark_production_configuration_is_preserved(field, value):
    config = configuration()
    config[field] = value
    with pytest.raises((candidate.CandidateRefused, ValueError)):
        candidate.public_configuration(config)


def test_configuration_digest_change_fails_before_registry_access(monkeypatch):
    config = configuration()
    config["VITE_POSTHOG_KEY"] = "changed-synthetic-public-setting"
    monkeypatch.setattr(
        candidate,
        "command",
        lambda *a, **k: pytest.fail("registry accessed before config refusal"),
    )
    with pytest.raises(candidate.CandidateRefused):
        candidate.promotion_check(record(), config)


def test_exact_manifest_bytes_and_one_buildx_final_lf_are_checked():
    raw = manifest_bytes()
    expected = candidate.digest(raw)
    candidate.manifest_identity(raw, expected)
    candidate.manifest_identity(raw + b"\n", expected)
    for altered in (
        raw + b"\n\n",
        raw + b" ",
        raw.replace(b'"schemaVersion":2', b'"schemaVersion":1'),
    ):
        with pytest.raises(candidate.CandidateRefused):
            candidate.manifest_identity(altered, expected)
    with pytest.raises(candidate.CandidateRefused):
        candidate.manifest_identity(raw, expected, dict(Id=IMAGE_DIGEST))


def test_image_asset_closure_is_real_file_content_and_refuses_links(tmp_path):
    left, right = tmp_path / "left", tmp_path / "right"
    left.mkdir()
    right.mkdir()
    (left / "app.js").write_bytes(b"synthetic executable asset")
    (right / "app.js").write_bytes(b"synthetic executable asset")
    assert candidate.asset_tree(left) == candidate.asset_tree(right)
    (right / "app.js").write_bytes(b"wrong compiled asset")
    assert candidate.asset_tree(left) != candidate.asset_tree(right)
    (right / "escape").symlink_to(left / "app.js")
    with pytest.raises(candidate.CandidateRefused):
        candidate.asset_tree(right)


@pytest.mark.parametrize(
    "field,value", [("Os", "windows"), ("Architecture", "arm64"), ("Id", "invalid")]
)
def test_wrong_local_image_cannot_be_published(field, value):
    image = local_image()
    candidate.image_source(context(), image)
    image[field] = value
    with pytest.raises(candidate.CandidateRefused):
        candidate.image_source(context(), image)


@pytest.mark.parametrize(
    "env",
    [
        ["PYTHON_ENV=staging"],
        ["PYTHON_ENV=production", "PYTHON_ENV=staging"],
        ["PYTHON_ENV=production", "PYTHON_ENV=production"],
        ["PYTHON_ENV=production", None],
    ],
)
def test_runtime_environment_cannot_be_staging_or_ambiguous(env):
    image = local_image()
    image["Config"]["Env"] = env
    with pytest.raises(candidate.CandidateRefused):
        candidate.image_source(context(), image)


@pytest.mark.parametrize("engine", ["classic", "containerd"])
def test_publish_verifies_before_registry_mutation_and_protects_both_objects(
    monkeypatch, tmp_path, engine
):
    config_path = tmp_path / "configuration.json"
    config_path.write_bytes(candidate.canonical(configuration()))
    image, exported, raw = saved_fixture(engine=engine)
    actual_digest = candidate.digest(raw)
    calls = []
    monkeypatch.setattr(candidate, "source_context", lambda: context(10))
    monkeypatch.setattr(
        candidate,
        "compiled_assets",
        lambda *args: (record()["frontend_assets_digest"], record()["panel"]),
    )

    def fake(args, **kwargs):
        calls.append(args)
        if args[:3] == ["docker", "image", "inspect"]:
            return candidate.canonical([image])
        if args[:3] == ["docker", "image", "save"]:
            assert args[-1] == image["Id"]
            save_fixture(Path(args[args.index("--output") + 1]), exported)
            return b""
        if args[:2] == ["docker", "create"]:
            return ("1" * 64).encode()
        if args[:4] == ["docker", "buildx", "imagetools", "inspect"]:
            return (
                raw + b"\n"
                if "--raw" in args
                else candidate.canonical(dict(digest=actual_digest))
            )
        if args[:4] == ["az", "acr", "repository", "show"]:
            return retention_bytes(actual_digest)
        return b""

    monkeypatch.setattr(candidate, "command", fake)
    output = tmp_path / "candidate.json"
    candidate.publish(config_path, output)
    observed = json.loads(output.read_bytes())
    assert observed["image"] == candidate.IMAGE + "@" + actual_digest
    assert observed["image_config_digest"] == CONFIG_DIGEST
    tag = next(args for args in calls if args[:2] == ["docker", "tag"])
    assert tag[2] == image["Id"]
    assert next(
        i for i, args in enumerate(calls) if args[:3] == ["docker", "image", "save"]
    ) < next(i for i, args in enumerate(calls) if args[:2] == ["docker", "create"])
    first_push = next(
        i for i, args in enumerate(calls) if args[:2] == ["docker", "push"]
    )
    assert (
        next(i for i, args in enumerate(calls) if args[:2] == ["docker", "cp"])
        < first_push
    )
    imported = next(args for args in calls if args[:3] == ["az", "acr", "import"])
    assert "--force" not in imported and IMAGE_DIGEST not in imported
    updates = [
        args for args in calls if args[:4] == ["az", "acr", "repository", "update"]
    ]
    assert len(updates) == 2
    assert any(
        candidate.IMAGE_REPOSITORY + "@" + actual_digest in args for args in updates
    )
    assert all(
        "--write-enabled" in args and "--delete-enabled" in args for args in updates
    )
    assert not any(
        args[:2] in (["docker", "start"], ["docker", "exec"], ["docker", "build"])
        for args in calls
    )
    assert output.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("invalid_image", [False, True])
def test_image_or_asset_failure_prevents_publication(
    monkeypatch, tmp_path, invalid_image
):
    config_path = tmp_path / "configuration.json"
    config_path.write_bytes(candidate.canonical(configuration()))
    image = local_image()
    monkeypatch.setattr(candidate, "source_context", lambda: context(10))

    def fake(args, **kwargs):
        assert args[0] == "docker" and args[1] in {"image", "create", "cp", "rm"}
        if invalid_image and args[:3] != ["docker", "image", "inspect"]:
            pytest.fail("malformed image advanced beyond inspection")
        if args[:3] == ["docker", "image", "save"]:
            save_fixture(
                Path(args[args.index("--output") + 1]),
                saved_fixture(engine="classic")[1],
            )
            return b""
        return (
            candidate.canonical([None if invalid_image else image])
            if args[1] == "image"
            else ("1" * 64).encode()
            if args[1] == "create"
            else b""
        )

    monkeypatch.setattr(candidate, "command", fake)
    monkeypatch.setattr(
        candidate, "compiled_assets", lambda *args: candidate.require(False)
    )
    with pytest.raises(candidate.CandidateRefused):
        candidate.publish(config_path, tmp_path / "candidate.json")


@pytest.mark.parametrize(
    "attribute,value",
    [
        ("writeEnabled", True),
        ("deleteEnabled", True),
        ("readEnabled", False),
        ("listEnabled", False),
    ],
)
def test_unprotected_tag_or_manifest_is_never_admitted(monkeypatch, attribute, value):
    attributes = dict(
        writeEnabled=False, deleteEnabled=False, readEnabled=True, listEnabled=True
    )
    attributes[attribute] = value
    monkeypatch.setattr(
        candidate,
        "command",
        lambda *a, **k: candidate.canonical(
            dict(digest=IMAGE_DIGEST, changeableAttributes=attributes)
        ),
    )
    with pytest.raises(candidate.CandidateRefused):
        candidate.retention("candidate", IMAGE_DIGEST)


def test_workflow_promotes_without_any_build_or_publish_and_retains_gates():
    source = (ROOT / candidate.WORKFLOW).read_text()
    workflow = yaml.load(source, Loader=yaml.BaseLoader)
    inputs = workflow["on"]["workflow_dispatch"]["inputs"]
    assert inputs["release_action"]["default"] == "prepare"
    producer, promotion = (
        workflow["jobs"]["production-candidate"],
        workflow["jobs"]["deploy"],
    )
    assert "inputs.release_action == 'prepare'" in producer["if"]
    assert "inputs.release_action == 'promote'" in promotion["if"]
    promoted = json.dumps(promotion)
    assert "just build" not in promoted and "install-nixpacks" not in promoted
    assert "production_core_candidate.py publish" not in promoted
    assert any(
        'bash deploy.sh "$GITHUB_SHA" "$CORE_PRODUCTION_IMAGE_REF"'
        in step.get("run", "")
        for step in promotion["steps"]
    )
    assert any(
        "production_core_candidate.py admit" in step.get("run", "")
        for step in promotion["steps"]
    )
    first_credential = next(
        i
        for i, step in enumerate(promotion["steps"])
        if "OP_SERVICE_ACCOUNT_TOKEN" in step.get("env", {})
    )
    assert (
        next(
            i
            for i, step in enumerate(promotion["steps"])
            if "production_core_candidate.py admit" in step.get("run", "")
        )
        < first_credential
    )
    gates = workflow["jobs"]["release-gates"]
    assert gates["needs"] == ["web", "app", "deployment-scope", "authorize-production"]
    gated = json.dumps(gates)
    for required in (
        "provider_signed_recall_callback_verified",
        "infra_apply_local.yml",
        "merge-base --is-ancestor",
        "production_core_candidate.py",
        "superadmin_capability_config_preflight.py",
        "DURABLE_WEBHOOK_INBOX_ENABLED",
    ):
        assert required in gated
    configured_names = {
        job["name"]
        for key, job in workflow["jobs"].items()
        if key not in {"backend-tests", "deploy"}
    }
    shards = workflow["jobs"]["backend-tests"]["strategy"]["matrix"]["include"]
    configured_names |= {"Backend tests (" + item["shard"] + ")" for item in shards}
    assert configured_names == candidate.required_jobs()


def shell_function(source, name):
    begin = source.index(name + "() {")
    end = source.index("\n}", begin) + 2
    return source[begin:end]


def test_actual_core_app_update_consumes_digest_not_source_tag(tmp_path):
    source = (ROOT / "infra/deploy.sh").read_text()
    function = shell_function(source, "initiate_deployment")
    image = candidate.IMAGE + "@" + IMAGE_DIGEST
    script = (
        'az() { if [[ "$2" == "update" ]]; then printf "%s\\n" "$@"; fi; return 0; }\n'
        + function
        + '\nPRODUCTION_IMAGE_REF="'
        + image
        + '"\nRESOURCE_GROUP=cosmaneura-prod\nREVISION_STRING=synthetic\ninitiate_deployment cosmaneura-prod-worker '
        + SHA
    )
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
        env={"PATH": "/usr/bin:/bin"},
    )
    assert result.returncode == 0
    assert image in result.stdout and candidate.IMAGE + ":" + SHA not in result.stdout


def test_all_migration_readback_and_drain_consumers_share_digest():
    source = (ROOT / "infra/deploy.sh").read_text()
    assert 'PRODUCTION_TAG="$PRODUCTION_IMAGE_REF"' in source
    for name in (
        "initiate_deployment",
        "check_deployment",
        "wait_for_incompatible_worker_replicas_to_drain",
    ):
        body = shell_function(source, name)
        assert '"$PRODUCTION_IMAGE_REF"' in body
        assert "${PRODUCTION_IMAGE}:${git_sha}" not in body
    for name in ("verify_migration_job_template", "run_migrations"):
        body = shell_function(source, name)
        assert '"$PRODUCTION_TAG"' in body
    assert '--image "$PRODUCTION_TAG"' in shell_function(source, "run_migrations")
    assert source.index("production_core_candidate.py") > source.index(
        "superadmin_capability_config_preflight.py"
    )
    assert "unset GH_TOKEN GITHUB_TOKEN" in source


def test_cli_failure_is_value_free(monkeypatch, capsys):
    monkeypatch.setattr(
        sys,
        "argv",
        "candidate admit --producer-run-id 10 --producer-attempt 1 --artifact-id 30".split(),
    )

    def malformed(*args):
        raise KeyError("synthetic-untrusted-content-must-not-appear")

    monkeypatch.setattr(candidate, "admit", malformed)
    assert candidate.main() == 1
    out = capsys.readouterr()
    assert out.out == "" and out.err == "production_core_candidate_refused\n"


@pytest.mark.parametrize(
    "key,value",
    [
        ("GITHUB_ACTOR", "Other"),
        ("GITHUB_TRIGGERING_ACTOR", "Other"),
        ("GITHUB_EVENT_NAME", "push"),
        ("GITHUB_REF", "refs/heads/staging"),
        ("GITHUB_REPOSITORY", "Other/fork"),
        ("GITHUB_WORKFLOW_REF", "legacy-workflow"),
        ("GITHUB_SHA", "short"),
    ],
)
def test_wrong_runtime_context_refuses_before_any_client(monkeypatch, key, value):
    for field, expected in runtime_environment().items():
        monkeypatch.setenv(field, expected)
    monkeypatch.setenv(key, value)
    monkeypatch.setattr(
        candidate,
        "command",
        lambda *a, **k: pytest.fail("client executed before context refusal"),
    )
    with pytest.raises(candidate.CandidateRefused):
        candidate.source_context()


def test_admit_fetches_only_exact_authenticated_api_coordinates(monkeypatch):
    data = evidence()
    prefix = "repos/" + candidate.REPOSITORY + "/actions"
    replies = {
        prefix + "/runs/10": data["run"],
        prefix + "/workflows/build_and_publish_local.yml": data["workflow"],
        prefix + "/runs/10/attempts/1/jobs?per_page=100": data["jobs"],
        prefix + "/artifacts/30": data["artifact"],
    }
    monkeypatch.setattr(candidate, "source_context", lambda: context())
    monkeypatch.setattr(candidate, "api", lambda path: replies[path])

    def fetch(args, **kwargs):
        path = prefix + "/artifacts/30/zip"
        assert args == ["gh", "api", "--hostname", "github.com", path]
        assert kwargs == {"github": True}
        return data["archive"]

    monkeypatch.setattr(candidate, "command", fetch)
    assert candidate.admit(10, 1, 30) == record()


def test_positive_promotion_only_reads_and_keeps_exact_registry_digest(monkeypatch):
    value = record()
    raw = manifest_bytes()
    actual_digest = candidate.digest(raw)
    value["image"] = candidate.IMAGE + "@" + actual_digest
    monkeypatch.setattr(candidate, "panel_identity", lambda *args: value["panel"])
    monkeypatch.setattr(candidate, "source_context", lambda: context())
    calls = []

    def read(args, **kwargs):
        calls.append(args)
        if args[:4] == ["docker", "buildx", "imagetools", "inspect"]:
            assert args[-1] == value["image"]
            return raw + b"\n"
        assert args[:4] == ["az", "acr", "repository", "show"]
        assert args[args.index("--name") + 1] == candidate.REGISTRY
        return retention_bytes(actual_digest)

    monkeypatch.setattr(candidate, "command", read)
    assert candidate.promotion_check(value, configuration()) == value["image"]
    assert len(calls) == 3


def test_immutable_archive_digest_detects_modified_bytes():
    data = evidence()
    with pytest.raises(candidate.CandidateRefused):
        candidate.archive_record(
            data["archive"] + b"tampering", data["artifact"]["digest"]
        )


@pytest.mark.parametrize("remote_sha", [SHA, "1" * 40])
def test_current_source_is_rechecked_through_normal_authenticated_path(
    monkeypatch, remote_sha
):
    for key, value in runtime_environment().items():
        monkeypatch.setenv(key, value)

    def git(args, **kwargs):
        assert args[:4] == ["git", "--no-replace-objects", "-c", "core.fsmonitor=false"]
        return (
            (TREE if "HEAD^{tree}" in args else SHA).encode()
            if "rev-parse" in args
            else b""
        )

    monkeypatch.setattr(candidate, "command", git)

    def read(path):
        assert path == "repos/" + candidate.REPOSITORY + "/git/ref/heads/master"
        return dict(object=dict(sha=remote_sha))

    monkeypatch.setattr(candidate, "api", read)
    if remote_sha == SHA:
        assert candidate.source_context() == context()
    else:
        with pytest.raises(candidate.CandidateRefused):
            candidate.source_context()


def test_digest_guard_refuses_missing_tags_wrong_repository_before_loader():
    source = (ROOT / "infra/deploy.sh").read_text()
    guard = source[source.index("if [[ $# -ne 2") : source.index("SCRIPT_DIR=")]
    image = candidate.IMAGE + "@" + IMAGE_DIGEST
    for arguments in (
        [SHA],
        [SHA, candidate.IMAGE + ":latest"],
        [SHA, "other.azurecr.io/repo@" + IMAGE_DIGEST],
        ["short", image],
        [SHA, image],
    ):
        result = subprocess.run(
            ["bash", "-c", guard + "\nprintf after_guard", "guard", *arguments],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            env={"PATH": "/usr/bin:/bin"},
        )
        assert (result.returncode == 0) is (arguments == [SHA, image])
        assert ("after_guard" in result.stdout) is (arguments == [SHA, image])


def test_historical_source_reuse_caller_is_already_blocked_on_master():
    verifier = (ROOT / "scripts/verify_one_time_release_reuse.py").read_text()
    assert 'SOURCE_BASE_SHA = "4ff9318298e332f67b18117ccbabfd3764f569d7"' in verifier
    changes = subprocess.run(
        [
            "git",
            "--no-replace-objects",
            "diff",
            "--name-status",
            "--no-renames",
            "4ff9318298e332f67b18117ccbabfd3764f569d7",
            "5033ea3aedb827e509f70b8f2c47a08c1a430c3b",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    ).stdout.splitlines()
    allowed = {
        "A\t" + path
        for path in (
            ".github/workflows/production_release_verified.yml",
            "scripts/verify_one_time_release_reuse.py",
            "tests/one_time_release_reuse_test.py",
            "docs/flight-plans/2026-10-27-verified-production-release.md",
        )
    }
    assert set(changes) != allowed
    assert 'set(changes) == {"A\\t" + p for p in NEW_CONTROL_FILES}' in verifier
    historical = (
        ROOT / ".github/workflows/production_release_verified.yml"
    ).read_text()
    assert historical.index(
        "scripts/verify_one_time_release_reuse.py"
    ) < historical.index("uses: azure/login@")


def test_client_children_receive_only_their_normal_capabilities(monkeypatch):
    for key in (
        "OP_SERVICE_ACCOUNT_TOKEN",
        "RECALL_WEBHOOK_SECRET",
        "CONTAINER_REGISTRY_PASSWORD",
        "GH_ENTERPRISE_TOKEN",
        "DATABASE_URL",
    ):
        monkeypatch.setenv(key, "synthetic-secret-never-forward")
    monkeypatch.setenv("GH_TOKEN", "synthetic-github-capability")

    def run(args, **kwargs):
        environment = kwargs["env"]
        assert "synthetic-secret-never-forward" not in environment.values()
        assert ("GH_TOKEN" in environment) is (args[0] == "gh")
        kwargs["stdout"].write(b"{}")
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr(candidate.subprocess, "run", run)
    for args, github in (
        (["docker", "image", "inspect", "synthetic"], False),
        (["gh", "api", "--hostname", "github.com", "synthetic"], True),
    ):
        assert candidate.command(args, github=github) == b"{}"


def test_saved_config_failure_prevents_source_probe_and_registry_mutations(
    monkeypatch, tmp_path
):
    image, entries, _raw = saved_fixture()
    catalogue = json.loads(entries["manifest.json"])
    entries[catalogue[0]["Config"]] = b"{}"
    config_path = tmp_path / "configuration.json"
    config_path.write_bytes(candidate.canonical(configuration()))
    monkeypatch.setattr(candidate, "source_context", lambda: context(10))
    calls = []

    def command(args, **kwargs):
        calls.append(args)
        if args[:3] == ["docker", "image", "inspect"]:
            return candidate.canonical([image])
        if args[:3] == ["docker", "image", "save"]:
            save_fixture(Path(args[args.index("--output") + 1]), entries)
            return b""
        pytest.fail("invalid saved config reached source/container/registry operation")

    monkeypatch.setattr(candidate, "command", command)
    output = tmp_path / "candidate.json"
    with pytest.raises(
        candidate.CandidateRefused, match="^production_core_candidate_refused$"
    ):
        candidate.publish(config_path, output)
    assert len(calls) == 2 and not output.exists()
