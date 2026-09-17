from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any, Callable

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
POLICY_PATH = ROOT / "infra-staging/recovery_image_bootstrap_policy.json"
SCRIPT_PATH = ROOT / "infra-staging/bootstrap_recovery_image.sh"
VALIDATOR_PATH = ROOT / "scripts/validate_staging_recovery_image_bootstrap.py"
WORKFLOW_PATH = ROOT / ".github/workflows/bootstrap_staging_recovery_image.yml"


def _module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "recovery_image_bootstrap", VALIDATOR_PATH
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _canonical(value: Any) -> bytes:
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode()


def _sha(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


# fmt: off
def _attributes(locked: bool) -> dict[str, bool]:
    return {"deleteEnabled": not locked, "listEnabled": True, "readEnabled": True, "writeEnabled": not locked}


def _fixture(state: str = "original") -> tuple[dict, dict, bytes, bytes, bytes]:
    policy = json.loads(POLICY_PATH.read_text())
    registry = policy["registry"]
    config = {
        "architecture": registry["architecture"], "os": registry["os"], "created": registry["config_created"],
        "config": {"Labels": registry["labels"]},
        "rootfs": {"type": "layers", "diff_ids": [f"sha256:{index:064x}" for index in range(1, 13)]},
    }
    config_bytes = _canonical(config)
    registry.update(config_digest=_sha(config_bytes), config_size=len(config_bytes))
    manifest = {
        "schemaVersion": 2, "mediaType": registry["manifest_media_type"],
        "config": {"mediaType": registry["config_media_type"], "size": len(config_bytes), "digest": registry["config_digest"]},
        "layers": [{"mediaType": "application/vnd.docker.image.rootfs.diff.tar.gzip", "size": index, "digest": f"sha256:{index + 20:064x}"} for index in range(12)],
    }
    manifest_bytes = _canonical(manifest)
    registry.update(manifest_digest=_sha(manifest_bytes), manifest_size=len(manifest_bytes))
    policy["confirmation"] = f"ACCEPT {registry['manifest_digest']}"
    anchors = ["unique-build", "unique-push", "unique-deploy"]
    job_log = ("\n".join(anchors) + "\n").encode()
    policy["historical_publication"].update(ordered_unique_log_anchors=anchors, raw_log_sha256=_sha(job_log))
    sha, azure, historical = "f" * 40, policy["azure"], policy["historical_publication"]
    progress = ["original", "tag_locked", "registry_locked", "worker_pinned", "scheduler_pinned", "pinned"].index(state)
    tag_locked, manifest_locked, update = progress >= 1, progress >= 2, "2026-09-05T12:00:00Z"
    observation = {
        "source": {"repository": policy["repository"], "ref": policy["protected_ref"], "ref_protected": True, "event_name": "workflow_dispatch", "actor": policy["founder"], "triggering_actor": policy["founder"], "workflow_sha": sha, "local_head": sha, "current_master": sha, "environment_policy": {"name": "Staging", "can_admins_bypass": False, "protection_rules": [{"type": "branch_policy"}], "deployment_branch_policy": {"protected_branches": False, "custom_branch_policies": True}, "total_count": 1, "branch_policies": [{"name": "master", "type": "branch"}]}},
        "azure": {
            "account": {"subscriptionId": azure["subscription_id"], "tenantId": azure["tenant_id"], "userName": azure["client_id"], "userType": "servicePrincipal"},
            "identity": {"clientId": azure["client_id"], "principalId": azure["principal_id"], "tenantId": azure["tenant_id"], "id": f"/subscriptions/{azure['subscription_id']}/resourceGroups/{azure['resource_group']}/providers/Microsoft.ManagedIdentity/userAssignedIdentities/{azure['identity_name']}"},
            "federated_credentials": [azure["federated_credential"]],
            "enabled_subscriptions": copy.deepcopy(azure["enabled_subscriptions"]),
            "effective_role_assignments": copy.deepcopy(azure["effective_role_assignments"]),
        },
        "historical": {"run": copy.deepcopy(historical["run"]), "job": copy.deepcopy(historical["job"])},
        "registry": {
            "resource": {"id": registry["resource_id"], "name": registry["name"], "loginServer": registry["login_server"], "adminUserEnabled": True, "policies": {"trustPolicy": {"status": "disabled"}}},
            "diagnostic_settings": [],
            "tag_metadata": [{"name": registry["source_tag"], "digest": registry["manifest_digest"], "signed": False, "createdTime": registry["tag_created_time"], "lastUpdateTime": update if tag_locked else registry["tag_last_update_time"], "changeableAttributes": _attributes(tag_locked)}],
            "manifest_metadata": {"digest": registry["manifest_digest"], "createdTime": registry["manifest_created_time"], "lastUpdateTime": update if manifest_locked else registry["manifest_last_update_time"], "mediaType": registry["manifest_media_type"], "configMediaType": registry["config_media_type"], "architecture": registry["architecture"], "os": registry["os"], "tags": registry["expected_tags"], "changeableAttributes": _attributes(manifest_locked)},
        },
        "workloads": [],
    }
    for index, expected in enumerate(policy["workloads"]):
        pinned = progress >= index + 3
        image = f"{registry['login_server']}/{registry['repository']}@{registry['manifest_digest']}" if pinned else f"{registry['login_server']}/{registry['repository']}:{registry['source_tag']}"
        revision = expected["pinned_revision"] if pinned else expected["original_revision"]
        ingress = {"traffic": [{"latestRevision": True, "weight": 100}]} if expected["traffic_weight"] == 100 else None
        template = {"revisionSuffix": revision.rsplit("--", 1)[-1], "containers": [{"name": expected["container_name"], "image": image, "args": ["run"]}], "scale": {"minReplicas": 1}}
        running = "RunningAtMaxScale" if expected["name"].endswith("scheduler") else "Running"
        properties = {"managedEnvironmentId": f"/subscriptions/{azure['subscription_id']}/resourceGroups/{azure['resource_group']}/providers/Microsoft.App/managedEnvironments/cosmaneura-staging-app-environment", "configuration": {"activeRevisionsMode": "Single", "ingress": ingress}, "template": template, "latestRevisionName": revision, "latestReadyRevisionName": revision, "provisioningState": "Succeeded", "runningStatus": running}
        observation["workloads"].append({
            "name": expected["name"],
            "app": {"name": expected["name"], "id": f"/subscriptions/{azure['subscription_id']}/resourceGroups/{azure['resource_group']}/providers/Microsoft.App/containerApps/{expected['name']}", "location": "North Central US", "identity": {"type": "None"}, "tags": {}, "properties": properties},
            "revisions": [{"name": revision, "properties": {"active": True, "healthState": "Healthy", "provisioningState": "Provisioned", "runningState": "Running", "trafficWeight": expected["traffic_weight"], "template": {**copy.deepcopy(template), "revisionSuffix": None}}}],
        })
    return policy, observation, job_log, manifest_bytes, config_bytes
# fmt: on


def _validate(fixture: tuple[dict, dict, bytes, bytes, bytes]) -> dict:
    policy, observation, log, manifest, config = fixture
    return _module().validate_capture(policy, observation, log, manifest, config)


_LIVE_EPHEMERAL_STORAGE = {
    "cosmaneura-staging-worker": "4Gi",
    "cosmaneura-staging-scheduler": "1Gi",
    "cosmaneura-staging-app": "2Gi",
}


def _templates(workload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    app_template = workload["app"]["properties"]["template"]
    revision_template = workload["revisions"][0]["properties"]["template"]
    return app_template, revision_template


def _apply_live_azure_template_projection(
    fixture: tuple[dict, dict, bytes, bytes, bytes],
) -> None:
    """Model the app/revision projection observed in capture run 33971188011."""
    for workload in fixture[1]["workloads"]:
        app_template, revision_template = _templates(workload)
        app_container = app_template["containers"][0]
        revision_container = revision_template["containers"][0]

        # These fields are materialized only by the container-app projection.
        app_container["imageType"] = "ContainerImage"
        common_resources = {"cpu": 0.5, "memory": "1Gi"}
        app_container["resources"] = {
            **common_resources,
            "ephemeralStorage": _LIVE_EPHEMERAL_STORAGE[workload["name"]],
        }
        revision_container["resources"] = copy.deepcopy(common_resources)
        app_template["customMetricsSettings"] = None

        # Azure reports the server defaults on the app and nulls on the
        # active-revision projection.
        app_template["scale"].update({"cooldownPeriod": 300, "pollingInterval": 30})
        revision_template["scale"].update(
            {"cooldownPeriod": None, "pollingInterval": None}
        )

    scheduler = next(
        workload
        for workload in fixture[1]["workloads"]
        if workload["name"] == "cosmaneura-staging-scheduler"
    )
    app_template, revision_template = _templates(scheduler)
    common_rule = {
        "name": "scheduler-trigger",
        "custom": {"type": "cron", "metadata": {"timezone": "UTC"}},
    }
    app_rule = copy.deepcopy(common_rule)
    app_rule["custom"]["auth"] = []
    app_template["scale"]["rules"] = [app_rule]
    revision_template["scale"]["rules"] = [copy.deepcopy(common_rule)]


def _apply_secret_ref_env_value_projection(
    fixture: tuple[dict, dict, bytes, bytes, bytes],
    projected_workloads: set[str] | None = None,
) -> None:
    """Model the secretRef projection observed in pin run 33974824553."""
    for workload in fixture[1]["workloads"]:
        app_template, revision_template = _templates(workload)
        revision_env = [
            {"name": "DATABASE_URL", "secretRef": "database-url"},
            {"name": "LOG_LEVEL", "value": "info"},
            {"name": "SERVICE_TOKEN", "secretRef": "service-token"},
        ]
        app_env = copy.deepcopy(revision_env)
        if projected_workloads is None or workload["name"] in projected_workloads:
            app_env[0]["value"] = ""
            app_env[2]["value"] = ""
        app_template["containers"][0]["env"] = app_env
        revision_template["containers"][0]["env"] = revision_env


@pytest.mark.parametrize(
    "state",
    [
        "original",
        "tag_locked",
        "registry_locked",
        "worker_pinned",
        "scheduler_pinned",
        "pinned",
    ],
)
def test_exact_monotonic_states_validate(state: str) -> None:
    assert _validate(_fixture(state))["observed_state"] == state


@pytest.mark.parametrize("state", ["original", "pinned"])
def test_live_azure_app_and_revision_projection_is_equivalent(state: str) -> None:
    fixture = _fixture(state)
    _apply_live_azure_template_projection(fixture)

    evidence = _validate(fixture)
    assert evidence["observed_state"] == state
    assert all(
        workload["template_sha256"] != workload["revision_template_sha256"]
        for workload in evidence["workloads"]
    )


@pytest.mark.parametrize("state", ["original", "pinned"])
def test_secret_ref_empty_value_app_projection_is_equivalent(state: str) -> None:
    fixture = _fixture(state)
    _apply_secret_ref_env_value_projection(fixture)

    evidence = _validate(fixture)
    assert evidence["observed_state"] == state
    assert all(
        workload["template_sha256"] != workload["revision_template_sha256"]
        for workload in evidence["workloads"]
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "reverse",
        "nonempty",
        "null",
        "name_mismatch",
        "secret_ref_mismatch",
        "reordered",
        "duplicate",
        "length_mismatch",
        "missing_revision_env",
        "literal",
    ],
)
def test_secret_ref_value_projection_drift_is_rejected(mutation: str) -> None:
    fixture = _fixture()
    _apply_secret_ref_env_value_projection(fixture)
    app_template, revision_template = _templates(fixture[1]["workloads"][0])
    app_env = app_template["containers"][0]["env"]
    revision_env = revision_template["containers"][0]["env"]

    if mutation == "reverse":
        app_env[0].pop("value")
        revision_env[0]["value"] = ""
    elif mutation == "nonempty":
        app_env[0]["value"] = "redacted-but-not-empty"
    elif mutation == "null":
        app_env[0]["value"] = None
    elif mutation == "name_mismatch":
        revision_env[0]["name"] = "OTHER_DATABASE_URL"
    elif mutation == "secret_ref_mismatch":
        revision_env[0]["secretRef"] = "other-database-url"
    elif mutation == "reordered":
        revision_env[0], revision_env[2] = revision_env[2], revision_env[0]
    elif mutation == "duplicate":
        app_env.append(copy.deepcopy(app_env[0]))
        revision_env.append(copy.deepcopy(revision_env[0]))
    elif mutation == "length_mismatch":
        revision_env.pop()
    elif mutation == "missing_revision_env":
        revision_template["containers"][0].pop("env")
    else:
        app_env[0] = {"name": "EMPTY_LITERAL", "value": ""}
        revision_env[0] = {"name": "EMPTY_LITERAL"}

    with pytest.raises(ValueError):
        _validate(fixture)


def test_copy_sequence_accepts_only_semantically_stable_template_projection() -> None:
    module = _module()
    stages = (
        ("registry_locked", set()),
        ("worker_pinned", {"cosmaneura-staging-worker"}),
        (
            "scheduler_pinned",
            {"cosmaneura-staging-worker", "cosmaneura-staging-scheduler"},
        ),
        (
            "pinned",
            {
                "cosmaneura-staging-worker",
                "cosmaneura-staging-scheduler",
                "cosmaneura-staging-app",
            },
        ),
    )
    fixtures = []
    evidence = []
    for state, projected_workloads in stages:
        fixture = _fixture(state)
        _apply_live_azure_template_projection(fixture)
        _apply_secret_ref_env_value_projection(fixture, projected_workloads)
        fixtures.append(fixture)
        evidence.append(_validate(fixture))

    baseline = evidence[0]
    for progress, (before, after, fixture) in enumerate(
        zip(evidence[:-1], evidence[1:], fixtures[1:], strict=True), start=3
    ):
        module.validate_transition(fixtures[0][0], before, after, progress)
        target_index = progress - 3
        current = module.validate_workload_transition(
            fixtures[0][0],
            baseline,
            fixture[1]["workloads"][target_index],
        )
        original = baseline["workloads"][target_index]
        assert current["template_sha256"] != original["template_sha256"]
        assert (
            current["template_comparison_sha256"]
            == original["template_comparison_sha256"]
        )

    drift_fixture = _fixture("worker_pinned")
    _apply_live_azure_template_projection(drift_fixture)
    _apply_secret_ref_env_value_projection(drift_fixture, {"cosmaneura-staging-worker"})
    worker_templates = _templates(drift_fixture[1]["workloads"][0])
    for template in worker_templates:
        template["containers"][0]["args"] = ["unexpected"]
    drift = _validate(drift_fixture)
    with pytest.raises(ValueError):
        module.validate_transition(fixtures[0][0], baseline, drift, 3)
    with pytest.raises(ValueError):
        module.validate_workload_transition(
            fixtures[0][0], baseline, drift_fixture[1]["workloads"][0]
        )


@pytest.mark.parametrize(
    ("number", "boolean"),
    [(0, False), (1, True), (0.0, False), (1.0, True)],
)
def test_boolean_cannot_impersonate_number_in_template_comparison(
    number: int | float, boolean: bool
) -> None:
    fixture = _fixture("worker_pinned")
    app_template, revision_template = _templates(fixture[1]["workloads"][0])
    app_template["scale"]["minReplicas"] = number
    revision_template["scale"]["minReplicas"] = boolean

    with pytest.raises(ValueError):
        _validate(fixture)


def test_boolean_numeric_transition_drift_is_rejected() -> None:
    module = _module()
    before_fixture = _fixture("registry_locked")
    after_fixture = _fixture("worker_pinned")
    for fixture, value in ((before_fixture, 0), (after_fixture, False)):
        for template in _templates(fixture[1]["workloads"][0]):
            template["scale"]["minReplicas"] = value

    before = _validate(before_fixture)
    after = _validate(after_fixture)
    with pytest.raises(ValueError):
        module.validate_transition(before_fixture[0], before, after, 3)


def test_integer_float_cpu_equivalence_has_stable_semantic_digest() -> None:
    module = _module()
    before_fixture = _fixture("registry_locked")
    after_fixture = _fixture("worker_pinned")
    before_templates = _templates(before_fixture[1]["workloads"][0])
    after_templates = _templates(after_fixture[1]["workloads"][0])
    before_templates[0]["containers"][0]["resources"] = {"cpu": 1}
    before_templates[1]["containers"][0]["resources"] = {"cpu": 1.0}
    after_templates[0]["containers"][0]["resources"] = {"cpu": 1.0}
    after_templates[1]["containers"][0]["resources"] = {"cpu": 1}

    before = _validate(before_fixture)
    after = _validate(after_fixture)
    module.validate_transition(before_fixture[0], before, after, 3)
    assert (
        before["workloads"][0]["template_sha256"]
        != after["workloads"][0]["template_sha256"]
    )
    assert (
        before["workloads"][0]["template_comparison_sha256"]
        == after["workloads"][0]["template_comparison_sha256"]
    )


def test_reverse_image_type_projection_is_rejected() -> None:
    fixture = _fixture()
    _, revision_template = _templates(fixture[1]["workloads"][0])
    revision_template["containers"][0]["imageType"] = "ContainerImage"

    with pytest.raises(ValueError):
        _validate(fixture)


@pytest.mark.parametrize("workload_name", _LIVE_EPHEMERAL_STORAGE)
def test_reverse_ephemeral_storage_projection_is_rejected(
    workload_name: str,
) -> None:
    fixture = _fixture()
    workload = next(
        item for item in fixture[1]["workloads"] if item["name"] == workload_name
    )
    app_template, revision_template = _templates(workload)
    common_resources = {"cpu": 0.5, "memory": "1Gi"}
    app_template["containers"][0]["resources"] = copy.deepcopy(common_resources)
    revision_template["containers"][0]["resources"] = {
        **common_resources,
        "ephemeralStorage": _LIVE_EPHEMERAL_STORAGE[workload_name],
    }

    with pytest.raises(ValueError):
        _validate(fixture)


def test_reverse_custom_metrics_projection_is_rejected() -> None:
    fixture = _fixture()
    _, revision_template = _templates(fixture[1]["workloads"][0])
    revision_template["customMetricsSettings"] = None

    with pytest.raises(ValueError):
        _validate(fixture)


@pytest.mark.parametrize(
    ("field", "default"),
    [("cooldownPeriod", 300), ("pollingInterval", 30)],
)
def test_reverse_scale_default_projection_is_rejected(field: str, default: int) -> None:
    fixture = _fixture()
    app_template, revision_template = _templates(fixture[1]["workloads"][0])
    app_template["scale"][field] = None
    revision_template["scale"][field] = default

    with pytest.raises(ValueError):
        _validate(fixture)


def test_reverse_custom_scale_auth_projection_is_rejected() -> None:
    fixture = _fixture()
    scheduler = next(
        workload
        for workload in fixture[1]["workloads"]
        if workload["name"] == "cosmaneura-staging-scheduler"
    )
    app_template, revision_template = _templates(scheduler)
    common_rule = {
        "name": "scheduler-trigger",
        "custom": {"type": "cron", "metadata": {"timezone": "UTC"}},
    }
    app_template["scale"]["rules"] = [copy.deepcopy(common_rule)]
    revision_rule = copy.deepcopy(common_rule)
    revision_rule["custom"]["auth"] = []
    revision_template["scale"]["rules"] = [revision_rule]

    with pytest.raises(ValueError):
        _validate(fixture)


@pytest.mark.parametrize("image_type", ["DockerImage", "containerimage", ""])
def test_non_container_image_projection_is_rejected(image_type: str) -> None:
    fixture = _fixture()
    _apply_live_azure_template_projection(fixture)
    app_template, _ = _templates(fixture[1]["workloads"][0])
    app_template["containers"][0]["imageType"] = image_type

    with pytest.raises(ValueError):
        _validate(fixture)


@pytest.mark.parametrize(
    ("field", "value"),
    [("cooldownPeriod", 301), ("pollingInterval", 31)],
)
def test_non_default_app_scale_projection_is_rejected(field: str, value: int) -> None:
    fixture = _fixture()
    _apply_live_azure_template_projection(fixture)
    app_template, _ = _templates(fixture[1]["workloads"][0])
    app_template["scale"][field] = value

    with pytest.raises(ValueError):
        _validate(fixture)


def test_nonempty_custom_scale_auth_projection_is_rejected() -> None:
    fixture = _fixture()
    _apply_live_azure_template_projection(fixture)
    scheduler = next(
        workload
        for workload in fixture[1]["workloads"]
        if workload["name"] == "cosmaneura-staging-scheduler"
    )
    app_template, _ = _templates(scheduler)
    app_template["scale"]["rules"][0]["custom"]["auth"] = [
        {"secretRef": "unexpected", "triggerParameter": "token"}
    ]

    with pytest.raises(ValueError):
        _validate(fixture)


def test_configured_custom_metrics_projection_is_rejected_when_revision_omits_it() -> (
    None
):
    fixture = _fixture()
    _apply_live_azure_template_projection(fixture)
    app_template, _ = _templates(fixture[1]["workloads"][0])
    app_template["customMetricsSettings"] = {"includeInternal": True}

    with pytest.raises(ValueError):
        _validate(fixture)


@pytest.mark.parametrize(
    ("workload_name", "app_value", "revision_value"),
    [
        ("cosmaneura-staging-worker", "5Gi", None),
        ("cosmaneura-staging-scheduler", "1Gi", "2Gi"),
        ("cosmaneura-staging-app", 2, None),
    ],
)
def test_conflicting_or_malformed_ephemeral_storage_is_rejected(
    workload_name: str, app_value: Any, revision_value: Any
) -> None:
    fixture = _fixture()
    _apply_live_azure_template_projection(fixture)
    workload = next(
        item for item in fixture[1]["workloads"] if item["name"] == workload_name
    )
    app_template, revision_template = _templates(workload)
    app_template["containers"][0]["resources"]["ephemeralStorage"] = app_value
    if revision_value is not None:
        revision_template["containers"][0]["resources"]["ephemeralStorage"] = (
            revision_value
        )

    with pytest.raises(ValueError):
        _validate(fixture)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value[1]["source"].__setitem__("current_master", "0" * 40),
        lambda value: value[1]["source"]["environment_policy"]["branch_policies"][
            0
        ].__setitem__("name", "staging"),
        lambda value: value[1]["azure"]["identity"].__setitem__("principalId", "wrong"),
        lambda value: value[1]["azure"]["enabled_subscriptions"].append({}),
        lambda value: value[1]["azure"]["effective_role_assignments"].pop(),
        lambda value: value[1]["workloads"][0]["app"]["properties"].__setitem__(
            "managedEnvironmentId", "wrong"
        ),
        lambda value: value[1]["historical"]["run"].__setitem__(
            "conclusion", "failure"
        ),
    ],
)
def test_wrong_source_identity_or_run_is_rejected(
    mutate: Callable[[tuple[dict, dict, bytes, bytes, bytes]], None],
) -> None:
    fixture = _fixture()
    mutate(fixture)
    with pytest.raises(ValueError):
        _validate(fixture)


def test_wrong_digest_and_tag_timestamp_are_rejected() -> None:
    fixture = _fixture()
    with pytest.raises(ValueError):
        _validate((*fixture[:3], fixture[3] + b"x", fixture[4]))
    fixture = _fixture()
    fixture[1]["registry"]["tag_metadata"][0]["lastUpdateTime"] = "2026-09-03T00:00:00Z"
    with pytest.raises(ValueError):
        _validate(fixture)


def test_ambiguous_or_reordered_log_is_rejected() -> None:
    for log in (
        b"unique-build\nunique-build\nunique-push\nunique-deploy\n",
        b"unique-push\nunique-build\nunique-deploy\n",
    ):
        fixture = _fixture()
        fixture[0]["historical_publication"]["raw_log_sha256"] = _sha(log)
        with pytest.raises(ValueError):
            _validate((*fixture[:2], log, *fixture[3:]))


def test_missing_or_multiarch_manifest_is_rejected() -> None:
    fixture = _fixture()
    with pytest.raises(ValueError):
        _validate((*fixture[:3], b"{}", fixture[4]))
    policy, observation, log, manifest, config = _fixture()
    index = json.loads(manifest)
    index["manifests"] = []
    manifest = _canonical(index)
    digest = _sha(manifest)
    policy["registry"].update(manifest_digest=digest, manifest_size=len(manifest))
    policy["confirmation"] = f"ACCEPT {digest}"
    observation["registry"]["tag_metadata"][0]["digest"] = digest
    observation["registry"]["manifest_metadata"]["digest"] = digest
    with pytest.raises(ValueError):
        _validate((policy, observation, log, manifest, config))


def test_sidecar_unhealthy_and_workload_disagreement_are_rejected() -> None:
    for mutation in ("sidecar", "unhealthy", "disagreement"):
        fixture = _fixture()
        workload = fixture[1]["workloads"][0]
        if mutation == "sidecar":
            workload["app"]["properties"]["template"]["containers"].append({})
        elif mutation == "unhealthy":
            workload["revisions"][0]["properties"]["healthState"] = "Degraded"
        else:
            pinned = _fixture("pinned")[1]["workloads"][0]
            fixture[1]["workloads"][0] = pinned
        with pytest.raises(ValueError):
            _validate(fixture)


def test_out_of_order_or_third_state_is_not_resumable() -> None:
    manifest_first = _fixture()
    manifest_first[1]["registry"]["manifest_metadata"]["changeableAttributes"] = (
        _attributes(True)
    )
    out_of_order = _fixture("registry_locked")
    out_of_order[1]["workloads"][1] = _fixture("scheduler_pinned")[1]["workloads"][1]
    for fixture in (manifest_first, out_of_order):
        with pytest.raises(ValueError):
            _validate(fixture)


def test_lock_readback_and_configuration_drift_fail_closed() -> None:
    module = _module()
    policy, observation, *_ = _fixture()
    with pytest.raises(ValueError):
        module.validate_lock(policy, "tag", observation["registry"]["tag_metadata"])
    before = _validate(_fixture())
    after = _validate(_fixture("pinned"))
    module.validate_transition(policy, _validate(_fixture("worker_pinned")), after)
    bad_suffix = _fixture("pinned")
    bad_suffix[1]["workloads"][0]["app"]["properties"]["template"]["revisionSuffix"] = (
        "wrong"
    )
    with pytest.raises(ValueError):
        _validate(bad_suffix)
    bad_revision_shape = _fixture("pinned")
    bad_revision_shape[1]["workloads"][0]["revisions"][0]["properties"]["template"][
        "revisionSuffix"
    ] = "unexpected"
    with pytest.raises(ValueError):
        _validate(bad_revision_shape)
    middle, later = (
        _validate(_fixture("worker_pinned")),
        _validate(_fixture("scheduler_pinned")),
    )
    later["registry"]["tag_last_update_time"] = "2026-09-05T13:00:00Z"
    with pytest.raises(ValueError):
        module.validate_transition(policy, middle, later, 4)
    after["workloads"][1]["configuration_sha256"] = "sha256:" + "0" * 64
    with pytest.raises(ValueError):
        module.validate_transition(policy, before, after)


# fmt: off
def test_capture_receipt_is_digest_bound_and_expires(tmp_path: Path) -> None:
    module = _module()
    policy, observation, *_ = fixture = _fixture()
    evidence = _validate(fixture)
    now = datetime(2026, 9, 5, 12, tzinfo=UTC)
    output = tmp_path / "receipt.json"
    journal = {"registry": {}, "workloads": {}}
    module.emit_receipt(policy, mode="capture", status_value="captured", phase="capture_validated", source_sha=observation["source"]["workflow_sha"], run_id="900", run_attempt="1", actor="JohnClem", triggering_actor="JohnClem", evidence=evidence, capture_run_id=None, capture_sha256=None, journal=journal, output=output, now=now)
    value = output.read_bytes()
    digest = _sha(value)
    run = {"id": 900, "run_attempt": 1, "event": "workflow_dispatch", "status": "completed", "conclusion": "success", "head_branch": "master", "head_sha": observation["source"]["workflow_sha"], "actor": "JohnClem", "triggering_actor": "JohnClem", "path": policy["workflow_path"]}
    module.validate_capture_receipt(policy, value, run, "900", digest, observation["source"]["workflow_sha"], now)
    with pytest.raises(ValueError):
        module.validate_capture_receipt(policy, value + b" ", run, "900", digest, observation["source"]["workflow_sha"], now)
    with pytest.raises(ValueError):
        module.validate_capture_receipt(policy, value, run, "900", digest, observation["source"]["workflow_sha"], now + timedelta(hours=1))


def test_workflow_and_shell_are_manual_bounded_and_do_not_weaken_deploy() -> None:
    workflow = yaml.safe_load(WORKFLOW_PATH.read_text())
    trigger = workflow.get("on", workflow.get(True))
    assert set(trigger) == {"workflow_dispatch"}
    inputs = trigger["workflow_dispatch"]["inputs"]
    assert set(inputs) == {"mode", "capture_run_id", "capture_receipt_sha256", "typed_confirmation"}
    job = workflow["jobs"]["bootstrap"]
    assert job["environment"] == "staging"
    assert job["permissions"]["deployments"] == "read"
    assert workflow["concurrency"] == {"group": "cosmaneura-staging-container-apps", "cancel-in-progress": False}
    source = SCRIPT_PATH.read_text()
    assert source.count("az acr repository update") == 2
    assert source.count("az containerapp revision copy") == 1
    assert '--from-revision "$from_revision"' in source
    assert 'refresh_token=$refresh_token' not in source
    assert 'Authorization: Bearer $access_token' not in source
    assert "--data-urlencode 'refresh_token@-'" in source
    assert "az account list --refresh --all" in source
    assert 'az monitor diagnostic-settings list --resource "$registry_id" -o json' in source
    assert 'az monitor diagnostic-settings list --resource "$registry_id" --query value' not in source
    assert "--include-inherited --all" not in source
    assert '--scope "$scope" --include-groups --include-inherited --subscription' in source
    assert 'for scope in "$RESOURCE_GROUP_ID" "$REGISTRY_RESOURCE_ID" "$MANAGED_ENVIRONMENT_ID"' in source
    assert source.count('$RESOURCE_GROUP_ID/providers/Microsoft.App/containerApps/cosmaneura-staging-') == 3
    assert "unique_by([(.principalId|ascii_downcase),(.roleDefinitionId|ascii_downcase),(.scope|ascii_downcase)])" in source
    assert "/environments/Staging/deployment-branch-policies?per_page=100" in source
    assert 'revalidate_progress "$((next_progress - 1))"' in source
    assert source.count("validate_capture_authorization") == 6
    assert "for workload in cosmaneura-staging-worker cosmaneura-staging-scheduler cosmaneura-staging-app" in source
    for forbidden in ("docker build", "docker push", "az acr import", "az acr repository delete", "az containerapp update", "az containerapp secret", "az containerapp ingress", "revision deactivate", "alembic upgrade"):
        assert forbidden not in source
    deploy = (ROOT / "infra-staging/deploy.sh").read_bytes()
    assert hashlib.sha256(deploy).hexdigest() == "3d2af53878e7c8ea373daafb024b01dddd97d5a06fc4f1a7c7ab195086353f93"
    assert 'STAGING_IMAGE_REFERENCE="$STAGING_IMAGE@$STAGING_IMAGE_DIGEST"' in deploy.decode()
# fmt: on
