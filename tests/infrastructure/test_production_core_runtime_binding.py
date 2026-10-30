"""Source/mocked runtime binding tests; no live client, credentials or DB."""

from __future__ import annotations

import copy
import datetime as dt
import hashlib
import importlib.util
import io
import json
import os
import socket
import ssl
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location(
    "production_core_runtime_binding",
    ROOT / "scripts/production_core_runtime_binding.py",
)
assert spec and spec.loader
binding = importlib.util.module_from_spec(spec)
spec.loader.exec_module(binding)
producer_spec = importlib.util.spec_from_file_location(
    "core_producer_fixtures",
    ROOT / "tests/infrastructure/test_production_core_candidate.py",
)
assert producer_spec and producer_spec.loader
producer_fixtures = importlib.util.module_from_spec(producer_spec)
producer_spec.loader.exec_module(producer_fixtures)

c = binding.candidate
SHA = "a" * 40
TREE = "b" * 40
IMAGE = c.IMAGE + "@sha256:" + "c" * 64
TIME = dt.datetime(2026, 10, 30, 12, tzinfo=dt.UTC)
SECRET = "synthetic-secret-value-must-never-be-hashed"


@pytest.fixture(autouse=True)
def no_external_clients(monkeypatch):
    def refused(*args, **kwargs):
        raise AssertionError("external client forbidden in source tests")

    monkeypatch.setattr(socket, "socket", refused)
    monkeypatch.setattr(socket, "create_connection", refused)
    monkeypatch.setattr(subprocess, "run", refused)
    monkeypatch.setattr(binding, "now", lambda: TIME)


def context() -> dict[str, Any]:
    return dict(source_sha=SHA, source_tree=TREE, run_id=20, run_attempt=1)


configuration = producer_fixtures.configuration


def prepared_record() -> dict[str, Any]:
    value = producer_fixtures.record()
    value["panel"] = c.panel_identity(ROOT / "web/public")
    return value


def association() -> dict[str, Any]:
    return dict(
        run_id=10, run_attempt=1, artifact_id=30, archive_digest="sha256:" + "8" * 64
    )


def revision_name(role: str) -> str:
    return binding.ROLES[role][0] + "--r20a1-" + SHA[:7]


def app_state(role: str) -> dict[str, Any]:
    name, container = binding.ROLES[role]
    revision = revision_name(role)
    template = dict(
        revisionSuffix="r20a1-" + SHA[:7],
        containers=[
            dict(
                name=container,
                image=IMAGE,
                args=[binding.process(role)],
                env=[
                    dict(name="PYTHON_ENV", value="production"),
                    dict(name="DATABASE_URL", secretRef="database-url"),
                    dict(name="SYNTHETIC_SECRET", value=SECRET),
                ],
                resources=dict(cpu=1, memory="2Gi"),
            )
        ],
        scale=dict(minReplicas=1, maxReplicas=1),
    )
    ingress = (
        dict(
            external=True,
            targetPort=80,
            allowInsecure=False,
            traffic=[dict(latestRevision=True, weight=100)],
        )
        if role == "api"
        else None
    )
    app: dict[str, Any] = dict(
        id=binding.PREFIX + "/containerApps/" + name,
        name=name,
        identity=dict(
            type="UserAssigned",
            userAssignedIdentities={
                f"/subscriptions/{binding.SUBSCRIPTION}/resourceGroups/{binding.GROUP}/providers/Microsoft.ManagedIdentity/userAssignedIdentities/synthetic-runtime": dict(
                    clientId="synthetic-public-id", principalId="synthetic-public-id"
                )
            },
        ),
        properties=dict(
            environmentId=binding.ENVIRONMENT,
            provisioningState="Succeeded",
            latestRevisionName=revision,
            latestReadyRevisionName=revision,
            configuration=dict(
                activeRevisionsMode="Single",
                ingress=ingress,
                secrets=[
                    dict(name="database-url", value=SECRET),
                    dict(name="registry-secret", value=SECRET),
                ],
                registries=[
                    dict(
                        server=c.REGISTRY + ".azurecr.io",
                        username="synthetic-private-user",
                        passwordSecretRef="registry-secret",
                    )
                ],
            ),
            template=template,
        ),
    )
    active: dict[str, Any] = dict(
        id=app["id"] + "/revisions/" + revision,
        name=revision,
        properties=dict(
            active=True,
            healthState="Healthy",
            provisioningState="Provisioned",
            runningState="RunningAtMaxScale",
            template=copy.deepcopy(template),
        ),
    )
    old: dict[str, Any] = dict(
        id=app["id"] + "/revisions/" + name + "--old",
        name=name + "--old",
        properties=dict(active=False),
    )
    replica = dict(
        name=revision + "-pod",
        properties=dict(containers=[dict(name=container, ready=True)]),
    )
    return dict(
        app=app,
        active=active,
        revisions=[copy.deepcopy(active), old],
        replicas={revision: [replica], old["name"]: []},
    )


def install_azure(monkeypatch, states: dict[str, Any] | None = None) -> dict[str, Any]:
    states = states or {role: app_state(role) for role in binding.ROLES}

    def fake(name, args):
        role = next(role for role, names in binding.ROLES.items() if names[0] == name)
        state = states[role]
        if args[0] == "show":
            return copy.deepcopy(state["app"])
        if args[:2] == ["revision", "list"]:
            return copy.deepcopy(state["revisions"])
        if args[:2] == ["revision", "show"]:
            return copy.deepcopy(state["active"])
        assert args[:2] == ["replica", "list"]
        return copy.deepcopy(state["replicas"][args[args.index("--revision") + 1]])

    monkeypatch.setattr(binding, "az", fake)
    return states


def install_observation(monkeypatch) -> None:
    install_azure(monkeypatch)
    monkeypatch.setattr(
        c,
        "command",
        lambda *a, **k: c.canonical(
            dict(id=binding.SUBSCRIPTION, tenantId=binding.TENANT)
        ),
    )
    panel = prepared_record()["panel"]
    monkeypatch.setattr(
        binding,
        "panel_https",
        lambda value: dict(
            origin="https://" + binding.HOST,
            path=f"/vendor/cosmaneura-panel.v1.{panel['sha256']}.js",
            sha256=panel["sha256"],
            bytes=108861,
            transport="system-ca-hostname-https-no-redirect",
        ),
    )


def observation(monkeypatch) -> dict[str, Any]:
    install_observation(monkeypatch)
    return binding.observe(context(), prepared_record())


def runtime_record(monkeypatch) -> dict[str, Any]:
    return binding.binding_record(
        context(), prepared_record(), association(), observation(monkeypatch)
    )


def archive(record: dict[str, Any], name="runtime-binding.json", second=False) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as stream:
        stream.writestr(name, c.canonical(record))
        if second:
            stream.writestr("extra.json", b"{}")
    return output.getvalue()


def promote_evidence(monkeypatch) -> dict[str, Any]:
    value = runtime_record(monkeypatch)
    raw = archive(value)
    producer = producer_fixtures.evidence()
    run = {**producer["run"], "id": 20}
    workflow = producer["workflow"]
    jobs: list[dict[str, Any]] = producer["jobs"]["jobs"]
    for item in jobs:
        item.update(
            run_id=20,
            conclusion="skipped"
            if item["name"] == "Prepare production candidate"
            else "success",
        )
    deploy = next(item for item in jobs if item["name"] == "🚀 Deploy")
    deploy.update(
        started_at=binding.stamp(TIME - dt.timedelta(minutes=2)),
        completed_at=binding.stamp(TIME + dt.timedelta(minutes=1)),
        steps=[
            dict(name=name, number=index, status="completed", conclusion="success")
            for index, name in enumerate(
                (binding.DEPLOY_STEP, binding.CAPTURE_STEP, binding.UPLOAD_STEP), 10
            )
        ],
    )
    artifact = producer["artifact"]
    artifact.update(
        id=40,
        name=f"production-core-runtime-binding-20-1-{SHA}",
        size_in_bytes=len(raw),
        digest=c.digest(raw),
    )
    artifact["workflow_run"]["id"] = 20
    monkeypatch.setattr(
        binding, "prepare", lambda *a: (prepared_record(), association())
    )
    return dict(
        context=context(),
        artifact_id=40,
        run=run,
        workflow=workflow,
        jobs=dict(total_count=len(jobs), jobs=jobs),
        artifact=artifact,
        raw=raw,
    )


def replace(value: dict[str, Any], path: str, change: Any) -> None:
    current: Any = value
    keys = path.split(".")
    for key in keys[:-1]:
        current = current[int(key)] if isinstance(current, list) else current[key]
    if isinstance(current, list):
        current[int(keys[-1])] = change
    else:
        current[keys[-1]] = change


def test_exact_active_traffic_and_all_three_ready_roles(monkeypatch):
    value = observation(monkeypatch)
    assert set(value["workloads"]) == set(binding.ROLES)
    assert binding.validate_observation(value, context(), prepared_record()) == value
    for role in binding.ROLES:
        assert value["workloads"][role]["revision"] == revision_name(role)
        assert value["workloads"][role]["image"] == IMAGE


@pytest.mark.parametrize("role", list(binding.ROLES))
@pytest.mark.parametrize(
    "path,value",
    [
        ("app.name", "another-app"),
        ("app.id", "/foreign"),
        ("app.properties.environmentId", "/staging-environment"),
        ("app.properties.provisioningState", "Failed"),
        ("app.properties.latestRevisionName", "old"),
        ("app.properties.latestReadyRevisionName", "old"),
        ("app.properties.configuration.activeRevisionsMode", "Multiple"),
        ("active.properties.active", False),
        ("active.properties.healthState", "Unhealthy"),
        ("active.properties.provisioningState", "Provisioning"),
        ("active.properties.runningState", "Stopped"),
        ("active.properties.template.containers.0.image", c.IMAGE + ":latest"),
        ("active.properties.template.containers.0.name", "sidecar"),
        ("active.properties.template.containers.0.args", [SECRET]),
        ("active.properties.template.containers.0.command", [SECRET]),
        ("active.properties.template.containers.0.env.0.value", "staging"),
        ("active.properties.template.initContainers", [dict(name="unexpected")]),
        ("active.properties.template.volumes", [dict(name="unexpected")]),
        ("app.properties.configuration.registries.0.server", "staging.azurecr.io"),
        ("app.properties.configuration.registries.0.passwordSecretRef", "missing"),
    ],
)
def test_wrong_workload_source_runtime_or_ownership_refused(
    monkeypatch, role, path, value
):
    states = install_azure(monkeypatch)
    replace(states[role], path, value)
    with pytest.raises(c.CandidateRefused):
        binding.observe_app(role, context(), IMAGE)


@pytest.mark.parametrize(
    "path,value",
    [
        ("external", False),
        ("targetPort", 443),
        ("allowInsecure", True),
        ("traffic.0.weight", 99),
        ("traffic.0.weight", True),
        ("traffic.0.latestRevision", False),
        ("traffic.0.revisionName", "old"),
        ("traffic.0.label", "old-route"),
    ],
)
def test_api_route_cannot_select_old_split_or_insecure_revision(
    monkeypatch, path, value
):
    states = install_azure(monkeypatch)
    replace(states["api"]["app"]["properties"]["configuration"]["ingress"], path, value)
    with pytest.raises(c.CandidateRefused):
        binding.observe_app("api", context(), IMAGE)


def test_explicit_exact_ready_revision_route_is_allowed(monkeypatch):
    states = install_azure(monkeypatch)
    states["api"]["app"]["properties"]["configuration"]["ingress"]["traffic"] = [
        dict(revisionName=revision_name("api"), weight=100)
    ]
    assert binding.observe_app("api", context(), IMAGE)["revision"] == revision_name(
        "api"
    )


@pytest.mark.parametrize("role", list(binding.ROLES))
@pytest.mark.parametrize(
    "problem",
    [
        "none-active",
        "two-active",
        "missing-active",
        "duplicate-revision",
        "unready",
        "no-replica",
        "extra-container",
        "residual-old-replica",
        "foreign-revision",
        "ambiguous-env",
        "duplicate-env",
    ],
)
def test_active_and_replica_inventories_fail_closed(monkeypatch, role, problem):
    states = install_azure(monkeypatch)
    state = states[role]
    replica = state["replicas"][revision_name(role)][0]
    if problem == "none-active":
        state["revisions"][0]["properties"]["active"] = False
    elif problem == "two-active":
        state["revisions"][1]["properties"]["active"] = True
    elif problem == "missing-active":
        state["revisions"].pop(0)
    elif problem == "duplicate-revision":
        state["revisions"].append(copy.deepcopy(state["revisions"][0]))
    elif problem == "unready":
        replica["properties"]["containers"][0]["ready"] = False
    elif problem == "no-replica":
        state["replicas"][revision_name(role)] = []
    elif problem == "extra-container":
        replica["properties"]["containers"].append(dict(name="sidecar", ready=True))
    elif problem == "residual-old-replica":
        state["replicas"][state["revisions"][1]["name"]] = [copy.deepcopy(replica)]
        if role == "api":
            assert binding.observe_app(role, context(), IMAGE)[
                "revision"
            ] == revision_name(role)
            return
    elif problem == "foreign-revision":
        state["revisions"][1]["id"] = "/foreign"
    else:
        env = state["active"]["properties"]["template"]["containers"][0]["env"]
        if problem == "ambiguous-env":
            env[1]["value"] = SECRET
        else:
            env.append(copy.deepcopy(env[0]))
    with pytest.raises(c.CandidateRefused):
        binding.observe_app(role, context(), IMAGE)


def test_no_secret_or_literal_environment_value_reaches_hash_inputs(monkeypatch):
    install_observation(monkeypatch)
    hashed: list[bytes] = []
    original = binding.canonical

    def checked(value):
        raw = original(value)
        assert SECRET.encode() not in raw and b"synthetic-private-user" not in raw
        hashed.append(raw)
        return raw

    monkeypatch.setattr(binding, "canonical", checked)
    result = binding.observe(context(), prepared_record())
    assert hashed and SECRET not in json.dumps(result)
    assert binding.structure(
        dict(value="low-entropy-one", enabled=True)
    ) == binding.structure(dict(value="low-entropy-two", enabled=False))
    assert "complete-configuration-equality" in binding.UNPROVED


@pytest.mark.parametrize(
    "change", ["image", "route", "reference", "runtime", "replica"]
)
def test_second_sample_refuses_public_runtime_drift(monkeypatch, change):
    install_observation(monkeypatch)
    original = binding.observe_app
    calls = 0

    def changed(role, ctx, image):
        nonlocal calls
        calls += 1
        value = original(role, ctx, image)
        if calls == 4:
            if change == "replica":
                value["ready_replicas"] = ["replaced-within-sample"]
                return value
            key = (
                "image"
                if change == "image"
                else "sanitized_configuration_digest"
                if change in {"route", "reference"}
                else "revision"
            )
            value[key] = "different"
        return value

    monkeypatch.setattr(binding, "observe_app", changed)
    with pytest.raises(c.CandidateRefused):
        binding.observe(context(), prepared_record())


def test_wrong_account_or_long_observation_refused(monkeypatch):
    install_observation(monkeypatch)
    monkeypatch.setattr(
        c,
        "command",
        lambda *a, **k: c.canonical(dict(id="staging", tenantId=binding.TENANT)),
    )
    with pytest.raises(c.CandidateRefused):
        binding.observe(context(), prepared_record())
    monkeypatch.setattr(
        c,
        "command",
        lambda *a, **k: c.canonical(
            dict(id=binding.SUBSCRIPTION, tenantId=binding.TENANT)
        ),
    )
    times = iter((TIME, TIME + dt.timedelta(seconds=binding.MAX_INTERVAL + 1)))
    monkeypatch.setattr(binding, "now", lambda: next(times))
    with pytest.raises(c.CandidateRefused):
        binding.observe(context(), prepared_record())


def test_hard_observation_deadline_interrupts_before_network_and_restores_handler(
    monkeypatch,
):
    previous = binding.signal.getsignal(binding.signal.SIGALRM)
    calls = []

    def timer(kind, seconds):
        assert kind == binding.signal.ITIMER_REAL
        calls.append(seconds)
        if seconds:
            binding.signal.getsignal(binding.signal.SIGALRM)(kind, None)

    monkeypatch.setattr(binding.signal, "setitimer", timer)
    with pytest.raises(c.CandidateRefused):
        binding.observe(context(), prepared_record())
    assert calls == [binding.MAX_INTERVAL, 0]
    assert binding.signal.getsignal(binding.signal.SIGALRM) == previous


class FakeHTTPS:
    def __init__(self, host, timeout, context):
        assert host == binding.HOST and timeout == 20
        assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
        self.headers: dict[str, str] = {}
        self.status = 200
        self.body = b"synthetic public panel"
        self.closed = False

    def request(self, method, path, headers):
        assert method == "GET" and path.startswith("/vendor/cosmaneura-panel.v1.")
        assert set(headers) == {"Accept", "Accept-Encoding", "Cache-Control"}

    def getresponse(self):
        return self

    def getheader(self, key):
        return self.headers.get(key)

    def read(self, maximum):
        assert maximum == binding.MAX_PANEL + 1
        return self.body[:maximum]

    def close(self):
        self.closed = True


def test_tls_refuses_ambient_trust_store_override(monkeypatch):
    monkeypatch.setenv("SSL_CERT_FILE", "/synthetic-untrusted-ca.pem")
    with pytest.raises(c.CandidateRefused):
        binding.panel_https(prepared_record()["panel"])


@pytest.mark.parametrize(
    "problem",
    [
        None,
        "redirect",
        "location",
        "compressed",
        "oversized-length",
        "oversized-body",
        "wrong-hash",
        "wrong-length",
        "tls-error",
    ],
)
def test_fixed_https_has_verified_tls_and_no_redirect_or_credentials(
    monkeypatch, problem
):
    instances: list[FakeHTTPS] = []

    def connect(*args, **kwargs):
        if problem == "tls-error":
            raise ssl.SSLError(SECRET)
        value = FakeHTTPS(*args, **kwargs)
        if problem == "redirect":
            value.status = 302
        if problem == "location":
            value.headers["Location"] = "https://foreign.invalid/"
        if problem == "compressed":
            value.headers["Content-Encoding"] = "gzip"
        if problem == "oversized-length":
            value.headers["Content-Length"] = str(binding.MAX_PANEL + 1)
        if problem == "oversized-body":
            value.body = b"x" * (binding.MAX_PANEL + 1)
        if problem == "wrong-length":
            value.headers["Content-Length"] = "1"
        instances.append(value)
        return value

    monkeypatch.setattr(binding.http.client, "HTTPSConnection", connect)
    panel = dict(sha256=hashlib.sha256(b"synthetic public panel").hexdigest())
    if problem == "wrong-hash":
        panel["sha256"] = "0" * 64
    if problem is None:
        assert binding.panel_https(panel)["sha256"] == panel["sha256"]
    else:
        with pytest.raises((c.CandidateRefused, ssl.SSLError)):
            binding.panel_https(panel)
    assert all(value.closed for value in instances)


def test_completed_authenticated_promote_and_original_prepare_admitted(monkeypatch):
    evidence = promote_evidence(monkeypatch)
    value = binding.verify_promote(**evidence)
    assert value["deployment_authorized"] is False
    assert value["unproved"] == binding.UNPROVED


@pytest.mark.parametrize(
    "path,value",
    [
        ("run.id", 21),
        ("run.run_attempt", 2),
        ("run.run_attempt", True),
        ("run.status", "in_progress"),
        ("run.conclusion", "failure"),
        ("run.event", "push"),
        ("run.head_branch", "staging"),
        ("run.head_sha", "0" * 40),
        ("run.repository.full_name", "foreign/core"),
        ("run.actor.login", "other"),
        ("run.triggering_actor.login", "other"),
        ("run.workflow_id", 99),
        ("run.path", ".github/workflows/retired.yml"),
        ("workflow.state", "disabled_manually"),
        ("workflow.path", "other.yml"),
        ("artifact.id", 41),
        ("artifact.expired", True),
        ("artifact.name", "self-hashed-plan"),
        ("artifact.size_in_bytes", True),
        ("artifact.digest", "sha256:" + "0" * 64),
        ("artifact.workflow_run.id", 10),
        ("artifact.workflow_run.repository_id", 99),
        ("artifact.workflow_run.head_repository_id", 99),
        ("artifact.workflow_run.head_sha", "0" * 40),
        ("artifact.workflow_run.head_branch", "staging"),
        ("jobs.total_count", 1),
        ("jobs.jobs.0.head_sha", "0" * 40),
        ("jobs.jobs.0.run_id", 10),
        ("jobs.jobs.0.run_attempt", 2),
        ("jobs.jobs.0.conclusion", "failure"),
    ],
)
def test_authenticated_promote_metadata_cannot_be_substituted(monkeypatch, path, value):
    evidence = promote_evidence(monkeypatch)
    replace(evidence, path, value)
    with pytest.raises(c.CandidateRefused):
        binding.verify_promote(**evidence)


@pytest.mark.parametrize(
    "problem",
    [
        "prepare-ran",
        "missing-job",
        "duplicate-job",
        "deploy-failed",
        "capture-skipped",
        "upload-failed",
        "step-order",
        "duplicate-step",
        "outside-job",
    ],
)
def test_only_successfully_deployed_recorded_and_uploaded_promote_is_trusted(
    monkeypatch, problem
):
    evidence = promote_evidence(monkeypatch)
    jobs = evidence["jobs"]["jobs"]
    deploy = next(item for item in jobs if item["name"] == "🚀 Deploy")
    if problem == "prepare-ran":
        next(item for item in jobs if item["name"] == "Prepare production candidate")[
            "conclusion"
        ] = "success"
    elif problem == "missing-job":
        jobs.pop()
    elif problem == "duplicate-job":
        jobs[-1] = copy.deepcopy(jobs[0])
    elif problem == "deploy-failed":
        deploy["conclusion"] = "failure"
    elif problem == "capture-skipped":
        deploy["steps"][1]["conclusion"] = "skipped"
    elif problem == "upload-failed":
        deploy["steps"][2]["conclusion"] = "failure"
    elif problem == "step-order":
        deploy["steps"][0]["number"] = 99
    elif problem == "duplicate-step":
        deploy["steps"].append(copy.deepcopy(deploy["steps"][1]))
    else:
        deploy["completed_at"] = binding.stamp(TIME - dt.timedelta(seconds=1))
    with pytest.raises(c.CandidateRefused):
        binding.verify_promote(**evidence)


@pytest.mark.parametrize(
    "path,value",
    [
        ("source_sha", "0" * 40),
        ("source_tree", "0" * 40),
        ("schema_version", True),
        ("purpose", "beta-ready"),
        ("repository", "foreign/core"),
        ("workflow_ref", "other"),
        ("run_id", 10),
        ("run_attempt", 2),
        ("run_attempt", True),
        ("contract_digest", "sha256:" + "0" * 64),
        ("deployment_authorized", True),
        ("unproved", []),
        ("prepare.artifact_id", 99),
        ("prepare.archive_digest", "sha256:" + "0" * 64),
        ("candidate.image", c.IMAGE + ":latest"),
        ("observation.subscription", "staging"),
        ("observation.tenant", "foreign"),
        ("observation.environment", "/staging"),
        ("observation.workloads.api.image", c.IMAGE + ":latest"),
        ("observation.workloads.api.revision", "old"),
        ("observation.workloads.worker.ready_replicas", []),
        ("observation.workloads.scheduler.ready_replicas", ["one", "two"]),
        ("observation.panel_https.origin", "https://staging-app.cosmaneura.com"),
        ("observation.panel_https.sha256", "0" * 64),
        ("observation.panel_https.bytes", True),
        ("observation.panel_https.transport", "unverified-tls"),
        ("observation.completed_at", "2026-10-30T11:00:00Z"),
        ("observation.started_at", "2026-10-30T11:00:00Z"),
        ("observation.completed_at", "2026-10-30T12:01:00Z"),
    ],
)
def test_self_hashed_receipt_does_not_override_provenance_or_live_gates(
    monkeypatch, path, value
):
    evidence = promote_evidence(monkeypatch)
    record = binding.unpack(evidence["raw"], evidence["artifact"]["digest"])
    replace(record, path, value)
    evidence["raw"] = archive(record)
    evidence["artifact"]["digest"] = c.digest(evidence["raw"])
    evidence["artifact"]["size_in_bytes"] = len(evidence["raw"])
    with pytest.raises(c.CandidateRefused):
        binding.verify_promote(**evidence)


@pytest.mark.parametrize(
    "problem",
    [
        "wrong-name",
        "duplicate",
        "oversized",
        "wrong-digest",
        "duplicate-json-key",
        "extra-field",
    ],
)
def test_archive_is_bounded_closed_and_immutable(monkeypatch, problem):
    value = runtime_record(monkeypatch)
    raw = archive(
        value,
        name="../runtime-binding.json"
        if problem == "wrong-name"
        else "runtime-binding.json",
        second=problem == "duplicate",
    )
    if problem == "oversized":
        raw = b"x" * (c.MAX_ARCHIVE + 1)
    if problem == "duplicate-json-key":
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as stream:
            stream.writestr("runtime-binding.json", b'{"purpose":1,"purpose":2}')
        raw = output.getvalue()
    if problem == "extra-field":
        value["schema_verified"] = True
        evidence = promote_evidence(monkeypatch)
        evidence["raw"] = archive(value)
        evidence["artifact"]["digest"] = c.digest(evidence["raw"])
        with pytest.raises(c.CandidateRefused):
            binding.verify_promote(**evidence)
    else:
        with pytest.raises(c.CandidateRefused):
            binding.unpack(
                raw,
                "sha256:" + "0" * 64 if problem == "wrong-digest" else c.digest(raw),
            )


def test_original_prepare_is_reauthenticated_not_trusted_from_receipt(monkeypatch):
    evidence = promote_evidence(monkeypatch)

    def refused(*args):
        raise c.CandidateRefused("original producer refused")

    monkeypatch.setattr(binding, "prepare", refused)
    with pytest.raises(c.CandidateRefused):
        binding.verify_promote(**evidence)


def test_admission_download_uses_fixed_authenticated_github_artifact_id(monkeypatch):
    evidence = promote_evidence(monkeypatch)
    prefix = f"repos/{c.REPOSITORY}/actions"
    metadata = {
        f"{prefix}/runs/20": evidence["run"],
        f"{prefix}/workflows/build_and_publish_local.yml": evidence["workflow"],
        f"{prefix}/runs/20/attempts/1/jobs?per_page=100": evidence["jobs"],
        f"{prefix}/artifacts/40": evidence["artifact"],
    }
    monkeypatch.setattr(
        binding, "local_source", lambda: dict(source_sha=SHA, source_tree=TREE)
    )
    monkeypatch.setattr(c, "api", lambda path: metadata.pop(path))

    def download(args, *, github=False):
        assert github is True
        assert args == [
            "gh",
            "api",
            "--hostname",
            "github.com",
            f"{prefix}/artifacts/40/zip",
        ]
        return evidence["raw"]

    monkeypatch.setattr(c, "command", download)
    assert binding.admit(20, 1, 40)["deployment_authorized"] is False
    assert not metadata


@pytest.mark.parametrize(
    "problem",
    [
        None,
        "worker-restart",
        "scheduler-restart",
        "api-scale",
        "runtime-drift",
        "source-drift",
    ],
)
def test_admission_reobserves_runtime_and_rechecks_current_master(monkeypatch, problem):
    value = runtime_record(monkeypatch)
    monkeypatch.setattr(binding, "admit", lambda *args: value)
    monkeypatch.setattr(
        binding,
        "local_source",
        lambda: dict(
            source_sha="0" * 40 if problem == "source-drift" else SHA, source_tree=TREE
        ),
    )
    fresh = copy.deepcopy(value["observation"])
    if problem == "runtime-drift":
        fresh["workloads"]["worker"]["sanitized_template_digest"] = "sha256:" + "0" * 64
    elif problem in {"worker-restart", "scheduler-restart"}:
        fresh["workloads"][problem.split("-")[0]]["ready_replicas"] = [
            "replacement-ready-pod"
        ]
    elif problem == "api-scale":
        fresh["workloads"]["api"]["ready_replicas"].append("another-ready-api-pod")
    monkeypatch.setattr(binding, "observe", lambda *args: fresh)
    if problem in {None, "worker-restart", "scheduler-restart", "api-scale"}:
        result = binding.check(20, 1, 40)
        assert (
            result["deployment_authorized"] is False and result["observation"] == fresh
        )
    else:
        with pytest.raises(c.CandidateRefused):
            binding.check(20, 1, 40)


@pytest.mark.parametrize(
    "problem", [None, "bad-config", "source-moved", "existing-output", "symlink-output"]
)
def test_record_is_after_admission_private_exclusive_and_configuration_bound(
    monkeypatch, tmp_path, problem
):
    install_observation(monkeypatch)
    ctx = context()
    calls = 0

    def source():
        nonlocal calls
        calls += 1
        return {
            **ctx,
            "source_sha": "0" * 40 if problem == "source-moved" and calls == 2 else SHA,
        }

    monkeypatch.setattr(c, "source_context", source)
    monkeypatch.setattr(
        binding, "prepare", lambda *args: (prepared_record(), association())
    )
    config = configuration()
    if problem == "bad-config":
        config["VITE_POSTHOG_KEY"] = "changed"
    config_file = tmp_path / "public.json"
    config_file.write_bytes(c.canonical(config))
    output = tmp_path / "runtime-binding.json"
    target = tmp_path / "target"
    if problem == "existing-output":
        output.write_text("owned old evidence")
    if problem == "symlink-output":
        target.write_text("preserved")
        output.symlink_to(target)
    if problem is None:
        binding.record(10, 1, 30, config_file, output)
        assert os.stat(output).st_mode & 0o777 == 0o600
        assert (
            c.read_json(output)["purpose"] == "sampled-production-core-runtime-binding"
        )
    else:
        with pytest.raises((c.CandidateRefused, OSError)):
            binding.record(10, 1, 30, config_file, output)
        if problem == "symlink-output":
            assert target.read_text() == "preserved"


def test_cli_errors_are_value_free(monkeypatch, capsys):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(ROOT / binding.SCRIPT),
            "check",
            "--run-id",
            "20",
            "--run-attempt",
            "1",
            "--artifact-id",
            "40",
        ],
    )

    def refused(*args):
        raise OSError(SECRET)

    monkeypatch.setattr(binding, "check", refused)
    assert binding.main() == 1
    assert capsys.readouterr().err == "production_core_runtime_binding_refused\n"


@pytest.mark.parametrize(
    "arguments", [["update"], ["secret", "list"], ["revision", "activate"], ["exec"]]
)
def test_runtime_helper_admits_no_mutation_secret_request_or_exec(arguments):
    with pytest.raises(c.CandidateRefused):
        binding.az(binding.ROLES["api"][0], arguments)


def test_canonical_capture_is_success_only_after_deploy_before_cleanup():
    workflow = yaml.safe_load((ROOT / c.WORKFLOW).read_text())
    deploy = workflow["jobs"]["deploy"]
    steps = deploy["steps"]
    names = [item.get("name") for item in steps]
    assert (
        names.index(binding.DEPLOY_STEP)
        < names.index(binding.CAPTURE_STEP)
        < names.index(binding.UPLOAD_STEP)
        < names.index("Remove resolved production frontend configuration")
    )
    capture = steps[names.index(binding.CAPTURE_STEP)]
    upload = steps[names.index(binding.UPLOAD_STEP)]
    assert "if" not in capture and "if" not in upload
    assert " record " in capture["run"] and "--configuration" in capture["run"]
    assert set(capture["env"]) == {
        "GH_TOKEN",
        "PRODUCER_RUN_ID",
        "PRODUCER_ATTEMPT",
        "ARTIFACT_ID",
    }
    assert (
        upload["with"]["overwrite"] is False
        and upload["with"]["if-no-files-found"] == "error"
    )
    gate = next(
        item
        for item in workflow["jobs"]["release-gates"]["steps"]
        if item.get("name") == "Verify successful applicable Terraform Apply"
    )
    assert binding.SCRIPT in gate["run"]
    assert "inputs.release_action == 'promote'" in deploy["if"]
    assert (
        "github.actor == 'JohnClem'" in deploy["if"]
        and "github.triggering_actor == 'JohnClem'" in deploy["if"]
    )
    assert "just build" not in "\n".join(item.get("run", "") for item in steps)
