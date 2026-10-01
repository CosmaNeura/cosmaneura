"""Synthetic release transitions exercise real guards; no Azure/GitHub calls."""

import copy
import runpy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
m = runpy.run_path(str(ROOT / "scripts/staging_web_image_only.py"))
guard = runpy.run_path(str(ROOT / "scripts/staging_azure_cli_bound.py"))
f = m["release"].__globals__
WEB = m["WEB"]
NAMES = sorted(m["APPS"])
OLD = "cosmaneurastagingregistry.azurecr.io/cosmaneura/cosmaneura@sha256:" + "a" * 64
NEW = "cosmaneurastagingregistry.azurecr.io/cosmaneura/cosmaneura@sha256:" + "b" * 64


def proof(name):
    return {
        "name": name,
        "revision": name + "--old",
        "ready_revision": name + "--old",
        "image": OLD,
        "ready_image": OLD,
        "identity_sha256": "1" * 64,
        "configuration_sha256": "2" * 64,
        "template_sha256": "3" * 64,
        "raw_template_sha256": "3" * 64,
        "raw_ready_template_sha256": "3" * 64,
        "template_without_image_sha256": "4" * 64,
        "ready_template_without_image_sha256": "4" * 64,
        "effective_runtime_sha256": "5" * 64,
    }


def setup(monkeypatch, tmp_path):
    current = {name: proof(name) for name in NAMES}
    prior = copy.deepcopy(current)
    app = {
        "latestRevisionName": WEB + "--old",
        "latestReadyRevisionName": WEB + "--old",
        "provisioningState": "Succeeded",
        "template": {"containers": [{"image": OLD}]},
    }
    baseline = {"schema": "16a6bf2d1539", "workloads": prior}
    plan = {
        "build_plan": {"path": "unused", "sha256": "6" * 64},
        "intent_path": str(tmp_path / "intent"),
    }
    calls = []
    monkeypatch.setitem(
        f, "prepare", lambda *_args, **_kw: ({}, {"image_reference": NEW}, baseline)
    )
    monkeypatch.setitem(f, "source_verify", lambda *_args: calls.append("source"))
    monkeypatch.setitem(f, "schema", lambda *_args: baseline["schema"])
    monkeypatch.setitem(
        f,
        "observe",
        lambda name, **_kw: (copy.deepcopy(current[name]), copy.deepcopy(app)),
    )

    def az(name, prefix, *args):
        calls.append((name, prefix, args))
        if prefix == ["containerapp", "update"]:
            current[WEB].update(
                image=NEW,
                ready_image=NEW,
                revision=WEB + "--new",
                ready_revision=WEB + "--new",
            )
            app["template"]["containers"][0]["image"] = NEW
        if prefix == ["containerapp", "show"]:
            return {"properties": copy.deepcopy(app)}
        return {}

    monkeypatch.setitem(f, "az", az)
    return plan, baseline, current, calls


def test_apply_writes_intent_before_one_web_mutation_and_preserves_siblings(
    monkeypatch, tmp_path
):
    plan, baseline, current, calls = setup(monkeypatch, tmp_path)
    real = f["az"]

    def checked(name, prefix, *args):
        if prefix == ["containerapp", "update"]:
            assert Path(plan["intent_path"]).is_file()
            assert name == WEB and args == ("--image", NEW)
        return real(name, prefix, *args)

    monkeypatch.setitem(f, "az", checked)
    result = m["release"](plan, "7" * 64, "apply")
    assert result["complete"] and result["worker_scheduler_unchanged"]
    assert Path(plan["intent_path"] + ".result").is_file()
    assert (
        sum(isinstance(c, tuple) and c[1] == ["containerapp", "update"] for c in calls)
        == 1
    )
    for name in m["APPS"] - {WEB}:
        assert current[name] == baseline["workloads"][name]


@pytest.mark.parametrize(
    "field",
    [
        "image",
        "identity_sha256",
        "effective_runtime_sha256",
        "configuration_sha256",
        "revision",
    ],
)
def test_changed_sibling_refuses_before_intent_or_mutation(
    monkeypatch, tmp_path, field
):
    plan, _, current, calls = setup(monkeypatch, tmp_path)
    current["cosmaneura-staging-worker"][field] = "changed"
    with pytest.raises(ValueError):
        m["release"](plan, "7" * 64, "apply")
    assert not Path(plan["intent_path"]).exists()
    assert not any(
        isinstance(c, tuple) and c[1] == ["containerapp", "update"] for c in calls
    )


def test_failed_image_call_retains_intent_and_never_retries(monkeypatch, tmp_path):
    plan, _, _, calls = setup(monkeypatch, tmp_path)

    def failed(name, prefix, *args):
        calls.append((name, prefix, args))
        if prefix == ["containerapp", "update"]:
            raise OSError()
        return {}

    monkeypatch.setitem(f, "az", failed)
    with pytest.raises(OSError):
        m["release"](plan, "7" * 64, "apply")
    assert Path(plan["intent_path"]).is_file()
    assert not Path(plan["intent_path"] + ".result").exists()
    with pytest.raises(FileExistsError):
        m["release"](plan, "7" * 64, "apply")
    assert (
        sum(isinstance(c, tuple) and c[1] == ["containerapp", "update"] for c in calls)
        == 1
    )


def test_post_update_runtime_drift_does_not_write_success(monkeypatch, tmp_path):
    plan, _, current, _ = setup(monkeypatch, tmp_path)
    real = f["az"]

    def drift(name, prefix, *args):
        value = real(name, prefix, *args)
        if prefix == ["containerapp", "update"]:
            current[WEB]["effective_runtime_sha256"] = "changed"
        return value

    monkeypatch.setitem(f, "az", drift)
    with pytest.raises(ValueError):
        m["release"](plan, "7" * 64, "apply")
    assert (
        Path(plan["intent_path"]).exists()
        and not Path(plan["intent_path"] + ".result").exists()
    )


def test_schema_and_source_failure_prevent_intent(monkeypatch, tmp_path):
    plan, _, _, _ = setup(monkeypatch, tmp_path)
    monkeypatch.setitem(f, "schema", lambda *_args: "different")
    with pytest.raises(ValueError):
        m["release"](plan, "7" * 64, "apply")
    assert not Path(plan["intent_path"]).exists()
    monkeypatch.setitem(
        f, "source_verify", lambda *_args: (_ for _ in ()).throw(ValueError())
    )
    with pytest.raises(ValueError):
        m["release"](plan, "7" * 64, "apply")
    assert not Path(plan["intent_path"]).exists()


def test_template_comparison_excludes_only_azure_computed_fields_and_image():
    a = {
        "revisionSuffix": "old",
        "scale": {"minReplicas": 0},
        "containers": [
            {
                "image": OLD,
                "imageType": "ContainerImage",
                "resources": {"cpu": 1, "ephemeralStorage": "1Gi"},
                "env": [{"name": "KEY", "secretRef": "key", "value": "placeholder"}],
            }
        ],
    }
    b = copy.deepcopy(a)
    b["revisionSuffix"] = "new"
    b["containers"][0]["image"] = NEW
    del b["containers"][0]["imageType"]
    del b["containers"][0]["resources"]["ephemeralStorage"]
    del b["containers"][0]["env"][0]["value"]
    assert m["template_projection"](a, without_image=True) == m["template_projection"](
        b, without_image=True
    )
    b["scale"]["minReplicas"] = 1
    assert m["template_projection"](a, without_image=True) != m["template_projection"](
        b, without_image=True
    )


def test_bound_command_accepts_only_web_image_change_and_exact_schema_probe():
    ending = [
        "--subscription",
        m["SUBSCRIPTION"],
        "--only-show-errors",
        "--output",
        "json",
    ]
    suffix = ["--name", WEB, "--resource-group", m["GROUP"]]
    update = ["containerapp", "update", *suffix, "--image", NEW, *ending]
    validate = guard["validate_runtime_operation"]
    validate(update, "web-image-only")
    for changed in [
        update + ["--set-env-vars", "X=true"],
        [v.replace(WEB, "cosmaneura-staging-worker") for v in update],
        [
            v.replace(NEW, OLD.replace("cosmaneurastagingregistry", "other"))
            for v in update
        ],
    ]:
        with pytest.raises(guard["BoundAzureRefused"]):
            validate(changed, "web-image-only")
    probe = [
        "containerapp",
        "exec",
        *suffix,
        "--revision",
        WEB + "--old",
        "--replica",
        WEB + "--old-abc",
        "--container",
        "cosmaneura-staging-app-web",
        "--command",
        guard["WEB_SCHEMA_PROBE_COMMAND"],
        *ending,
    ]
    validate(probe, "web-image-only")
    with pytest.raises(guard["BoundAzureRefused"]):
        validate(
            [v.replace(guard["WEB_SCHEMA_PROBE_COMMAND"], "arbitrary") for v in probe],
            "web-image-only",
        )


def test_recovery_requires_exact_apply_intent_and_no_runtime_drift(
    monkeypatch, tmp_path
):
    plan, _, current, _ = setup(monkeypatch, tmp_path)
    intent = {
        "operation": "web-image-only",
        "plan_sha256": "7" * 64,
        "mutation_may_start": True,
        "automatic_retry_allowed": False,
    }
    current[WEB]["effective_runtime_sha256"] = "changed"
    with pytest.raises(ValueError):
        m["release"](plan, "7" * 64, "recover", intent)
    assert not Path(plan["intent_path"] + ".recovery").exists()
    intent["plan_sha256"] = "wrong"
    with pytest.raises(ValueError):
        m["release"](plan, "7" * 64, "recover", intent)


def test_recovery_keeps_siblings_and_restores_only_bound_web_image(
    monkeypatch, tmp_path
):
    plan, baseline, current, calls = setup(monkeypatch, tmp_path)
    intent = {
        "operation": "web-image-only",
        "plan_sha256": "7" * 64,
        "mutation_may_start": True,
        "automatic_retry_allowed": False,
    }
    template = {"containers": [{"image": OLD}]}
    template_hash = m["digest"](m["template_projection"](template, without_image=True))
    baseline["workloads"][WEB]["raw_ready_template_sha256"] = m["digest"](template)
    baseline["workloads"][WEB]["ready_template_without_image_sha256"] = template_hash
    current[WEB]["ready_template_without_image_sha256"] = template_hash
    # Failed candidate is latest while the old revision still serves traffic.
    current[WEB].update(
        image=NEW, ready_image=OLD, revision=WEB + "--new", ready_revision=WEB + "--old"
    )

    def az(name, prefix, *args):
        calls.append((name, prefix, args))
        if prefix == ["containerapp", "revision", "show"]:
            return {
                "properties": {"provisioningState": "Provisioned", "template": template}
            }
        if prefix == ["containerapp", "update"]:
            assert name == WEB and args == ("--image", OLD)
            current[WEB].update(
                image=OLD,
                ready_image=OLD,
                revision=WEB + "--recovered",
                ready_revision=WEB + "--recovered",
            )
        if prefix == ["containerapp", "show"]:
            return {
                "properties": {
                    "latestRevisionName": WEB + "--recovered",
                    "latestReadyRevisionName": WEB + "--recovered",
                    "provisioningState": "Succeeded",
                    "template": template,
                }
            }
        return {}

    monkeypatch.setitem(f, "az", az)
    result = m["release"](plan, "7" * 64, "recover", intent)
    assert result["complete"] and result["worker_scheduler_unchanged"]
    assert result["web"]["image"] == OLD
    assert Path(plan["intent_path"] + ".recovery.result").is_file()
    assert (
        sum(isinstance(c, tuple) and c[1] == ["containerapp", "update"] for c in calls)
        == 1
    )


def test_second_source_read_drift_prevents_mutation(monkeypatch, tmp_path):
    plan, _, current, _ = setup(monkeypatch, tmp_path)
    count = []

    def source(*_args):
        count.append(True)
        if len(count) == 2:
            current[WEB]["identity_sha256"] = "changed"

    monkeypatch.setitem(f, "source_verify", source)
    with pytest.raises(ValueError):
        m["release"](plan, "7" * 64, "apply")
    assert not Path(plan["intent_path"]).exists()


def test_source_expiry_during_second_check_prevents_intent(monkeypatch, tmp_path):
    plan, _, _, _ = setup(monkeypatch, tmp_path)
    calls = []

    def expired(*_args):
        calls.append(True)
        if len(calls) == 2:
            raise ValueError("expired")

    monkeypatch.setitem(f, "source_verify", expired)
    with pytest.raises(ValueError):
        m["release"](plan, "7" * 64, "apply")
    assert len(calls) == 2 and not Path(plan["intent_path"]).exists()


def test_observe_azure_projection_keeps_complete_app_and_retained_hashes(monkeypatch):
    app_template = {
        "revisionSuffix": "old",
        "scale": {"cooldownPeriod": 300},
        "containers": [
            {
                "name": "cosmaneura-staging-app-web",
                "image": OLD,
                "imageType": "ContainerImage",
                "resources": {"cpu": 1, "ephemeralStorage": "1Gi"},
                "env": [{"name": "KEY", "secretRef": "key", "value": "placeholder"}],
            }
        ],
    }
    ready_template = copy.deepcopy(app_template)
    ready_template["revisionSuffix"] = None
    ready_template["scale"]["cooldownPeriod"] = None
    del ready_template["containers"][0]["imageType"]
    del ready_template["containers"][0]["resources"]["ephemeralStorage"]
    del ready_template["containers"][0]["env"][0]["value"]

    def az(_name, prefix, *_args):
        if prefix == ["containerapp", "show"]:
            return {
                "identity": {"type": "UserAssigned"},
                "properties": {
                    "configuration": {"activeRevisionsMode": "Single"},
                    "provisioningState": "Succeeded",
                    "latestRevisionName": WEB + "--old",
                    "latestReadyRevisionName": WEB + "--old",
                    "template": app_template,
                },
            }
        if prefix == ["containerapp", "revision", "show"]:
            return {
                "properties": {
                    "healthState": "Healthy",
                    "provisioningState": "Provisioned",
                    "template": ready_template,
                }
            }
        return [{"name": "key", "value": "synthetic-secret-not-emitted"}]

    monkeypatch.setitem(f, "az", az)
    result, _ = m["observe"](WEB)
    assert (
        result["template_without_image_sha256"]
        != result["ready_template_without_image_sha256"]
    )
    assert "synthetic-secret-not-emitted" not in str(result)
    assert result["effective_runtime_sha256"] == m["digest"](
        {
            "environment": {"KEY": "synthetic-secret-not-emitted"},
            "args": [],
            "command": [],
        }
    )


def test_exec_transport_keeps_token_on_stdin_and_independent_tty(monkeypatch):
    token = b"fictional-token"
    monkeypatch.setitem(f, "command", lambda *_args, **_kwargs: token)
    captured = []

    def run(argv, **kwargs):
        captured.append((argv, kwargs))
        return SimpleNamespace(
            returncode=0, stdout=b"STAGING_SCHEMA_REVISION=16a6bf2d1539\n", stderr=b""
        )

    monkeypatch.setattr(f["subprocess"], "run", run)
    raw = m["az"](
        WEB,
        ["containerapp", "exec"],
        "--revision",
        WEB + "--old",
        "--replica",
        WEB + "--old-a",
        "--container",
        "cosmaneura-staging-app-web",
        "--command",
        guard["WEB_SCHEMA_PROBE_COMMAND"],
    )
    assert b"STAGING_SCHEMA_REVISION=" in raw
    argv, kwargs = captured[0]
    assert kwargs["input"] == token and len(kwargs["pass_fds"]) == 1
    assert "3<&0 0<&" in argv[2] and "preexec_fn" not in kwargs


def test_read_bound_rejects_duplicates_symlinks_and_changed_hash(tmp_path):
    path = tmp_path / "duplicate"
    path.write_text('{"x":1,"x":2}')
    with pytest.raises(ValueError):
        m["read_bound"](str(path), m["sha"](path.read_bytes()))
    path.write_text('{"x":1}')
    link = tmp_path / "link"
    link.symlink_to(path)
    with pytest.raises(ValueError):
        m["read_bound"](str(link), m["sha"](path.read_bytes()))
    with pytest.raises(ValueError):
        m["read_bound"](str(path), "0" * 64)


@pytest.mark.parametrize(
    "changed", ["helper", "expired", "migration", "baseline", "publication"]
)
def test_prepare_rejects_unbound_or_stale_execution_inputs(
    monkeypatch, tmp_path, changed
):
    now = datetime.now(UTC)
    build = {
        "release": {
            "migration_revisions": [],
            "schema_before": "16a6bf2d1539",
            "schema_after": "16a6bf2d1539",
            "recovery": {n: {"revision": n + "--old", "image": OLD} for n in NAMES},
        }
    }
    baseline = {
        "version": 1,
        "build_plan_sha256": "6" * 64,
        "schema": "16a6bf2d1539",
        "observed_at": now.isoformat(),
        "workloads": {n: proof(n) for n in NAMES},
    }
    publication = {"valid": True}
    values = {"build": build, "baseline": baseline, "publication": publication}
    plan = {
        "version": 1,
        "action": "staging_core_web_image_only",
        "actor": "delegated_assistant",
        "created_at": now.isoformat(),
        "expires_at": (now + timedelta(hours=1)).isoformat(),
        "build_plan": {"path": "build", "sha256": "6" * 64},
        "publication": {"path": "publication", "sha256": "7" * 64},
        "baseline": {"path": "baseline", "sha256": "8" * 64},
        "helper_sha256s": {
            n: m["sha"]((ROOT / "scripts" / n).read_bytes()) for n in m["HELPERS"]
        },
        "intent_path": str(tmp_path / "intent"),
    }
    monkeypatch.setitem(f, "bound", lambda spec: values[spec["path"]])
    monkeypatch.setitem(f, "verify_checkout", lambda _build: None)

    def deploy_inputs(_build, _hash, pub):
        m["require"](pub["valid"])

    monkeypatch.setattr(
        f["runpy"],
        "run_path",
        lambda _path: {
            "deployment_inputs": deploy_inputs,
            "resolve_publication": lambda _build, _hash, pub: pub,
        },
    )
    m["prepare"](plan)
    if changed == "helper":
        plan["helper_sha256s"]["staging_web_image_only.py"] = "0" * 64
    elif changed == "expired":
        plan.update(
            created_at=(now - timedelta(hours=1)).isoformat(),
            expires_at=(now - timedelta(seconds=1)).isoformat(),
        )
    elif changed == "migration":
        build["release"]["migration_revisions"] = ["16a6bf2d1539"]
    elif changed == "baseline":
        baseline["build_plan_sha256"] = "changed"
    else:
        publication["valid"] = False
    with pytest.raises(ValueError):
        m["prepare"](plan)


def test_cli_rejects_extra_arguments_before_any_read_or_azure(monkeypatch):
    monkeypatch.setattr(f["sys"], "flags", SimpleNamespace(isolated=True, no_site=True))
    monkeypatch.setattr(f["resource"], "setrlimit", lambda *_args: None)
    reads = []
    monkeypatch.setitem(f, "read_bound", lambda *_args: reads.append(True))
    with pytest.raises(ValueError):
        m["main"](["apply", "plan", "6" * 64, "unexpected"])
    assert reads == []
