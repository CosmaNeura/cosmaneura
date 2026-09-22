#!/usr/bin/env python3
"""Validate the one-time staging recovery-image bootstrap evidence."""

from __future__ import annotations

import argparse
import copy
import hashlib
import io
import json
import math
import re
import stat
import sys
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, NoReturn

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
INTEGER_RE = re.compile(r"^[1-9][0-9]*$")
ANSI_RE = re.compile(rb"\x1b\[[0-?]*[ -/]*[@-~]")
MAX_LOG_BYTES = 2 * 1024 * 1024
MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_RECEIPT_BYTES = 256 * 1024
MAX_CLOCK_SKEW = timedelta(minutes=5)
PROGRESS_STATES = tuple(
    "original tag_locked registry_locked worker_pinned scheduler_pinned pinned".split()
)
EXPECTED_EPHEMERAL_STORAGE = {
    "cosmaneura-staging-app-worker": "4Gi",
    "cosmaneura-staging-app-scheduler": "1Gi",
    "cosmaneura-staging-app-web": "2Gi",
}
_MISSING = object()


class BootstrapEvidenceError(ValueError):
    """Raised when evidence does not satisfy the immutable bootstrap policy."""


def _fail(message: str) -> NoReturn:
    raise BootstrapEvidenceError(message)


def _read_bytes(path: Path, *, maximum: int, label: str) -> bytes:
    try:
        metadata = path.stat()
    except OSError as exc:
        raise BootstrapEvidenceError(f"{label} is unavailable") from exc
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > maximum:
        _fail(f"{label} is not a bounded regular file")
    try:
        value = path.read_bytes()
    except OSError as exc:
        raise BootstrapEvidenceError(f"{label} could not be read") from exc
    if len(value) != metadata.st_size:
        _fail(f"{label} changed while it was read")
    return value


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def _digest(value: Any) -> str:
    return _sha256(_json_bytes(value))


def _semantic_json_view(value: Any) -> list[Any]:
    """Return a type-tagged JSON tree with exact finite numeric equivalence."""
    if value is None:
        return ["null"]
    if type(value) is bool:
        return ["boolean", value]
    if type(value) is int:
        return ["number", str(value), "1"]
    if type(value) is float:
        if not math.isfinite(value):
            _fail("semantic JSON contains a non-finite number")
        numerator, denominator = value.as_integer_ratio()
        return ["number", str(numerator), str(denominator)]
    if type(value) is str:
        return ["string", value]
    if type(value) is list:
        return ["array", [_semantic_json_view(item) for item in value]]
    if type(value) is dict:
        if any(type(key) is not str for key in value):
            _fail("semantic JSON object contains a non-string key")
        return [
            "object",
            [[key, _semantic_json_view(value[key])] for key in sorted(value)],
        ]
    _fail("semantic JSON contains an unsupported value")


def _semantic_json_equal(left: Any, right: Any) -> bool:
    return _semantic_json_view(left) == _semantic_json_view(right)


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail(f"{label} must be an object")
    return value


def _array(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        _fail(f"{label} must be an array")
    return value


def _sorted_objects(value: Any, label: str) -> list[dict[str, Any]]:
    return sorted(
        (_object(item, label) for item in _array(value, label)), key=_json_bytes
    )


def _normalized_roles(value: Any, label: str) -> list[dict[str, Any]]:
    roles = _sorted_objects(value, label)
    keys = {"principalId", "principalType", "roleDefinitionId", "scope"}
    if any(set(role) != keys for role in roles):
        _fail(f"{label} contains unexpected fields")
    return sorted(
        [
            {
                **role,
                "principalId": str(role["principalId"]).casefold(),
                "roleDefinitionId": str(role["roleDefinitionId"]).casefold(),
                "scope": str(role["scope"]).casefold(),
            }
            for role in roles
        ],
        key=_json_bytes,
    )


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        _fail(f"{label} must be a non-empty string")
    return value


def _integer(value: Any, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        _fail(f"{label} must be a positive integer")
    return value


def _time(value: Any, label: str) -> datetime:
    text = _string(value, label)
    if not text.endswith("Z"):
        _fail(f"{label} must be UTC")
    try:
        parsed = datetime.fromisoformat(f"{text[:-1]}+00:00")
    except ValueError as exc:
        raise BootstrapEvidenceError(f"{label} is not an ISO-8601 time") from exc
    if parsed.tzinfo != UTC:
        _fail(f"{label} must be UTC")
    return parsed


def _json_from_bytes(value: bytes, label: str) -> Any:
    try:
        return json.loads(value)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BootstrapEvidenceError(f"{label} is not JSON") from exc


def load_json(path: Path, *, label: str, maximum: int = MAX_JSON_BYTES) -> Any:
    return _json_from_bytes(_read_bytes(path, maximum=maximum, label=label), label)


def load_policy(path: Path) -> dict[str, Any]:
    policy = _object(load_json(path, label="bootstrap policy"), "bootstrap policy")
    validate_policy(policy)
    return policy


def validate_policy(policy: dict[str, Any]) -> None:
    if policy.get("schema_version") != 1:
        _fail("unsupported policy schema")
    if policy.get("policy_id") != "cosmaneura-staging-recovery-image-bootstrap-v1":
        _fail("unexpected policy id")
    if policy.get("repository") != "CosmaNeura/cosmaneura":
        _fail("unexpected repository")
    if policy.get("protected_ref") != "refs/heads/master":
        _fail("unexpected protected ref")
    if policy.get("workflow_path") != (
        ".github/workflows/bootstrap_staging_recovery_image.yml"
    ):
        _fail("unexpected workflow path")
    if policy.get("founder") != "JohnClem":
        _fail("unexpected founder")
    ttl = _integer(policy.get("capture_ttl_seconds"), "capture ttl")
    if ttl > 3600:
        _fail("capture ttl is not short-lived")
    registry = _object(policy.get("registry"), "registry policy")
    historical = _object(
        policy.get("historical_publication"), "historical publication policy"
    )
    source_sha = _string(historical.get("source_sha"), "historical source sha")
    manifest_digest = _string(registry.get("manifest_digest"), "manifest digest")
    config_digest = _string(registry.get("config_digest"), "config digest")
    if not SHA_RE.fullmatch(source_sha):
        _fail("historical source sha is malformed")
    if not DIGEST_RE.fullmatch(manifest_digest) or not DIGEST_RE.fullmatch(
        config_digest
    ):
        _fail("registry digest is malformed")
    labels = _object(registry.get("labels"), "image labels")
    if labels.get("org.opencontainers.image.revision") != source_sha:
        _fail("image revision label is not bound to historical source")
    run = _object(historical.get("run"), "historical run")
    job = _object(historical.get("job"), "historical job")
    if run.get("id") != job.get("run_id") or run.get("run_attempt") != job.get(
        "run_attempt"
    ):
        _fail("historical run and job disagree")
    if run.get("head_sha") != source_sha or job.get("head_sha") != source_sha:
        _fail("historical metadata is not source-bound")
    if registry.get("source_tag") != source_sha:
        _fail("source tag is not source-bound")
    workloads = _array(policy.get("workloads"), "workload policy")
    if [item.get("name") for item in workloads if isinstance(item, dict)] != [
        "cosmaneura-staging-worker",
        "cosmaneura-staging-scheduler",
        "cosmaneura-staging-app",
    ]:
        _fail("workload order or membership is not exact")
    if len({_object(item, "workload").get("name") for item in workloads}) != 3:
        _fail("workloads are not unique")
    if manifest_digest not in _string(policy.get("confirmation"), "confirmation"):
        _fail("confirmation is not digest-bound")
    limitations = _object(registry.get("known_limitations"), "known limitations")
    if limitations != {
        "image_signed": False,
        "repository_event_diagnostics_present": False,
        "registry_admin_user_enabled": True,
        "original_tag_mutable": True,
        "original_manifest_mutable": True,
        "requires_typed_human_acceptance": True,
    }:
        _fail("retrospective evidence limitations are not explicit")
    azure = _object(policy.get("azure"), "azure policy")
    if azure.get("enabled_subscriptions") != [
        {
            "subscriptionId": azure.get("subscription_id"),
            "tenantId": azure.get("tenant_id"),
            "state": "Enabled",
        }
    ]:
        _fail("enabled subscription policy is not closed-world")
    roles = _normalized_roles(
        azure.get("effective_role_assignments"), "role assignment policy"
    )
    if len(roles) != 3 or any(
        role.get("principalId") != str(azure.get("principal_id")).casefold()
        or role.get("principalType") != "ServicePrincipal"
        for role in roles
    ):
        _fail("effective role-assignment policy is not exact")
    subscription = f"/subscriptions/{azure.get('subscription_id')}"
    resource_group = f"{subscription}/resourceGroups/{azure.get('resource_group')}"
    actual_roles = {
        (
            str(role.get("roleDefinitionId", "")).casefold(),
            str(role.get("scope", "")).casefold(),
        )
        for role in roles
    }
    expected_roles = {
        (
            f"{subscription}/providers/Microsoft.Authorization/roleDefinitions/8311e382-0749-4cb8-b61a-304f252e45ec".casefold(),
            str(registry["resource_id"]).casefold(),
        ),
        (
            f"{subscription}/providers/Microsoft.Authorization/roleDefinitions/b24988ac-6180-42a0-ab88-20f7382dd24c".casefold(),
            resource_group.casefold(),
        ),
        (
            f"{subscription}/providers/Microsoft.Authorization/roleDefinitions/b24988ac-6180-42a0-ab88-20f7382dd24c".casefold(),
            f"{resource_group}/providers/Microsoft.App/managedEnvironments/cosmaneura-staging-app-environment".casefold(),
        ),
    }
    if actual_roles != expected_roles:
        _fail("effective role assignments are not the exact least-privilege set")


def policy_sha256(policy: dict[str, Any]) -> str:
    validate_policy(policy)
    return _digest(policy)


def _exact_subset(
    observed: dict[str, Any], expected: dict[str, Any], label: str
) -> None:
    for key, value in expected.items():
        if observed.get(key) != value:
            _fail(f"{label}.{key} does not match policy")


def _validate_source(
    policy: dict[str, Any], observation: dict[str, Any]
) -> dict[str, Any]:
    source = _object(observation.get("source"), "source observation")
    expected = {
        "repository": policy["repository"],
        "ref": policy["protected_ref"],
        "ref_protected": True,
        "event_name": "workflow_dispatch",
        "actor": policy["founder"],
        "triggering_actor": policy["founder"],
    }
    _exact_subset(source, expected, "source")
    workflow_sha = _string(source.get("workflow_sha"), "workflow sha")
    if not SHA_RE.fullmatch(workflow_sha):
        _fail("workflow sha is malformed")
    if source.get("local_head") != workflow_sha:
        _fail("local checkout is not the workflow sha")
    if source.get("current_master") != workflow_sha:
        _fail("workflow sha is not current master")
    environment = _object(source.get("environment_policy"), "environment policy")
    if environment != {
        "name": "Staging",
        "can_admins_bypass": False,
        "protection_rules": [{"type": "branch_policy"}],
        "deployment_branch_policy": {
            "protected_branches": False,
            "custom_branch_policies": True,
        },
        "total_count": 1,
        "branch_policies": [{"name": "master", "type": "branch"}],
    }:
        _fail("staging environment is not closed-world on master")
    return {
        **expected,
        "workflow_sha": workflow_sha,
        "environment_policy_sha256": _digest(environment),
    }


def _validate_azure(
    policy: dict[str, Any], observation: dict[str, Any]
) -> dict[str, Any]:
    expected = _object(policy.get("azure"), "azure policy")
    azure = _object(observation.get("azure"), "azure observation")
    account = _object(azure.get("account"), "azure account")
    identity = _object(azure.get("identity"), "azure identity")
    _exact_subset(
        account,
        {
            "subscriptionId": expected["subscription_id"],
            "tenantId": expected["tenant_id"],
            "userName": expected["client_id"],
            "userType": "servicePrincipal",
        },
        "azure account",
    )
    _exact_subset(
        identity,
        {
            "clientId": expected["client_id"],
            "principalId": expected["principal_id"],
            "tenantId": expected["tenant_id"],
        },
        "azure identity",
    )
    expected_id = (
        f"/subscriptions/{expected['subscription_id']}/resourceGroups/"
        f"{expected['resource_group']}/providers/Microsoft.ManagedIdentity/"
        f"userAssignedIdentities/{expected['identity_name']}"
    )
    if str(identity.get("id", "")).casefold() != expected_id.casefold():
        _fail("azure identity resource id does not match policy")
    credentials = _array(azure.get("federated_credentials"), "federated credentials")
    if len(credentials) != 1:
        _fail("azure identity must have exactly one federated credential")
    if credentials[0] != expected["federated_credential"]:
        _fail("azure federated credential does not match the staging environment")
    subscriptions = _sorted_objects(
        azure.get("enabled_subscriptions"), "enabled subscriptions"
    )
    assignments = _normalized_roles(
        azure.get("effective_role_assignments"), "effective role assignments"
    )
    if subscriptions != _sorted_objects(
        expected["enabled_subscriptions"], "enabled subscription policy"
    ):
        _fail("enabled subscription inventory does not match policy")
    if assignments != _normalized_roles(
        expected["effective_role_assignments"], "role assignment policy"
    ):
        _fail("effective role assignments do not match policy")
    return {
        "client_id": expected["client_id"],
        "principal_id": expected["principal_id"],
        "tenant_id": expected["tenant_id"],
        "subscription_id": expected["subscription_id"],
        "identity_resource_id": expected_id,
        "federated_credential_sha256": _digest(credentials[0]),
        "enabled_subscriptions_sha256": _digest(subscriptions),
        "effective_role_assignments_sha256": _digest(assignments),
    }


def _validate_historical(
    policy: dict[str, Any], observation: dict[str, Any], job_log: bytes
) -> dict[str, Any]:
    if len(job_log) > MAX_LOG_BYTES:
        _fail("historical job log exceeds the bound")
    expected = _object(
        policy.get("historical_publication"), "historical publication policy"
    )
    historical = _object(observation.get("historical"), "historical observation")
    run = _object(historical.get("run"), "historical run observation")
    job = _object(historical.get("job"), "historical job observation")
    _exact_subset(run, _object(expected.get("run"), "historical run policy"), "run")
    _exact_subset(job, _object(expected.get("job"), "historical job policy"), "job")
    actual_log_digest = _sha256(job_log)
    if actual_log_digest != expected.get("raw_log_sha256"):
        _fail("historical raw job log digest does not match policy")
    clean_log = ANSI_RE.sub(b"", job_log).decode("utf-8", errors="strict")
    positions: list[int] = []
    for anchor in _array(expected.get("ordered_unique_log_anchors"), "log anchors"):
        text = _string(anchor, "log anchor")
        if clean_log.count(text) != 1:
            _fail("historical job log evidence is missing or ambiguous")
        positions.append(clean_log.index(text))
    if positions != sorted(positions) or len(set(positions)) != len(positions):
        _fail("historical job log evidence is reordered")
    return {
        "source_sha": expected["source_sha"],
        "run_id": expected["run"]["id"],
        "run_attempt": expected["run"]["run_attempt"],
        "job_id": expected["job"]["id"],
        "raw_log_sha256": actual_log_digest,
        "ordered_anchor_count": len(positions),
    }


def _attributes(value: Any, label: str) -> dict[str, bool]:
    attributes = _object(value, label)
    expected_keys = {"deleteEnabled", "listEnabled", "readEnabled", "writeEnabled"}
    if set(attributes) != expected_keys or any(
        not isinstance(attributes[key], bool) for key in expected_keys
    ):
        _fail(f"{label} is malformed")
    return {key: attributes[key] for key in sorted(expected_keys)}


def _locked(attributes: dict[str, bool]) -> bool:
    return attributes == {
        "deleteEnabled": False,
        "listEnabled": True,
        "readEnabled": True,
        "writeEnabled": False,
    }


def _mutable(attributes: dict[str, bool]) -> bool:
    return attributes == {
        "deleteEnabled": True,
        "listEnabled": True,
        "readEnabled": True,
        "writeEnabled": True,
    }


def _validate_raw_image(
    policy: dict[str, Any], manifest_bytes: bytes, config_bytes: bytes
) -> None:
    registry = _object(policy.get("registry"), "registry policy")
    if len(manifest_bytes) != registry.get("manifest_size"):
        _fail("raw image manifest size does not match policy")
    if _sha256(manifest_bytes) != registry.get("manifest_digest"):
        _fail("raw image manifest digest does not match policy")
    manifest = _object(
        _json_from_bytes(manifest_bytes, "raw image manifest"), "manifest"
    )
    if manifest.get("schemaVersion") != 2:
        _fail("image manifest schema is not v2")
    if manifest.get("mediaType") != registry.get("manifest_media_type"):
        _fail("image manifest is missing or multi-architecture")
    if "manifests" in manifest:
        _fail("multi-architecture image indexes are not accepted")
    config_descriptor = _object(manifest.get("config"), "manifest config descriptor")
    if config_descriptor != {
        "mediaType": registry["config_media_type"],
        "size": registry["config_size"],
        "digest": registry["config_digest"],
    }:
        _fail("manifest config descriptor does not match policy")
    layers = _array(manifest.get("layers"), "manifest layers")
    if len(layers) != registry.get("layer_count"):
        _fail("manifest layer count does not match policy")
    if any(
        not isinstance(item, dict)
        or not DIGEST_RE.fullmatch(str(item.get("digest", "")))
        for item in layers
    ):
        _fail("manifest layer descriptor is malformed")
    if len(config_bytes) != registry.get("config_size"):
        _fail("raw image config size does not match policy")
    if _sha256(config_bytes) != registry.get("config_digest"):
        _fail("raw image config digest does not match policy")
    config = _object(_json_from_bytes(config_bytes, "raw image config"), "image config")
    if config.get("architecture") != registry.get("architecture"):
        _fail("image architecture does not match policy")
    if config.get("os") != registry.get("os"):
        _fail("image operating system does not match policy")
    if config.get("created") != registry.get("config_created"):
        _fail("image creation time does not match policy")
    config_section = _object(config.get("config"), "image runtime config")
    if config_section.get("Labels") != registry.get("labels"):
        _fail("image source/run labels do not match policy")
    rootfs = _object(config.get("rootfs"), "image rootfs")
    if rootfs.get("type") != "layers" or len(
        _array(rootfs.get("diff_ids"), "image diff ids")
    ) != registry.get("layer_count"):
        _fail("image rootfs does not match the manifest")


def _registry_state(
    policy: dict[str, Any], registry_observation: dict[str, Any]
) -> tuple[str, dict[str, Any]]:
    expected = _object(policy.get("registry"), "registry policy")
    resource = _object(registry_observation.get("resource"), "registry resource")
    if (
        str(resource.get("id", "")).casefold()
        != str(expected["resource_id"]).casefold()
    ):
        _fail("registry resource id does not match policy")
    _exact_subset(
        resource,
        {
            "name": expected["name"],
            "loginServer": expected["login_server"],
            "adminUserEnabled": True,
        },
        "registry resource",
    )
    policies = _object(resource.get("policies"), "registry policies")
    if _object(policies.get("trustPolicy"), "registry trust policy").get("status") != (
        "disabled"
    ):
        _fail("registry trust-policy state differs from the accepted limitation")
    diagnostics = _array(
        registry_observation.get("diagnostic_settings"), "registry diagnostics"
    )
    if diagnostics:
        _fail("registry diagnostic settings changed; refresh human evidence review")
    tags = _array(registry_observation.get("tag_metadata"), "source tag metadata")
    if len(tags) != 1:
        _fail("source tag is missing or ambiguous")
    tag = _object(tags[0], "source tag")
    _exact_subset(
        tag,
        {
            "name": expected["source_tag"],
            "digest": expected["manifest_digest"],
            "signed": False,
            "createdTime": expected["tag_created_time"],
        },
        "source tag",
    )
    manifest = _object(
        registry_observation.get("manifest_metadata"), "manifest metadata"
    )
    _exact_subset(
        manifest,
        {
            "digest": expected["manifest_digest"],
            "createdTime": expected["manifest_created_time"],
            "mediaType": expected["manifest_media_type"],
            "configMediaType": expected["config_media_type"],
            "architecture": expected["architecture"],
            "os": expected["os"],
        },
        "manifest metadata",
    )
    if sorted(_array(manifest.get("tags"), "manifest tags")) != sorted(
        expected["expected_tags"]
    ):
        _fail("manifest tag set drifted")
    tag_attributes = _attributes(tag.get("changeableAttributes"), "tag attributes")
    manifest_attributes = _attributes(
        manifest.get("changeableAttributes"), "manifest attributes"
    )
    if _mutable(tag_attributes):
        if tag.get("lastUpdateTime") != expected["tag_last_update_time"]:
            _fail("source tag update time drifted")
        tag_state = "mutable"
    elif _locked(tag_attributes):
        if _time(tag.get("lastUpdateTime"), "tag update time") < _time(
            expected["tag_created_time"], "expected tag creation time"
        ):
            _fail("locked tag update time predates creation")
        tag_state = "locked"
    else:
        tag_state = "third"
    if _mutable(manifest_attributes):
        if manifest.get("lastUpdateTime") != expected["manifest_last_update_time"]:
            _fail("manifest update time drifted")
        manifest_state = "mutable"
    elif _locked(manifest_attributes):
        if _time(manifest.get("lastUpdateTime"), "manifest update time") < _time(
            expected["manifest_created_time"], "expected manifest creation time"
        ):
            _fail("locked manifest update time predates creation")
        manifest_state = "locked"
    else:
        manifest_state = "third"
    state = {
        ("mutable", "mutable"): "original",
        ("locked", "mutable"): "tag_locked",
        ("locked", "locked"): "locked",
    }.get((tag_state, manifest_state), "third")
    return state, {
        "state": state,
        "manifest_digest": expected["manifest_digest"],
        "config_digest": expected["config_digest"],
        "source_tag": expected["source_tag"],
        "tag_created_time": tag["createdTime"],
        "tag_last_update_time": tag["lastUpdateTime"],
        "manifest_created_time": manifest["createdTime"],
        "manifest_last_update_time": manifest["lastUpdateTime"],
        "tag_attributes": tag_attributes,
        "manifest_attributes": manifest_attributes,
        "limitations": expected["known_limitations"],
    }


def validate_lock(policy: dict[str, Any], kind: str, metadata: Any) -> None:
    registry = _object(policy.get("registry"), "registry policy")
    if kind == "tag":
        values = _array(metadata, "tag lock readback")
        if len(values) != 1:
            _fail("tag lock readback is missing or ambiguous")
        value = _object(values[0], "tag lock readback")
        _exact_subset(
            value,
            {
                "name": registry["source_tag"],
                "digest": registry["manifest_digest"],
                "signed": False,
                "createdTime": registry["tag_created_time"],
            },
            "tag lock readback",
        )
    elif kind == "manifest":
        value = _object(metadata, "manifest lock readback")
        _exact_subset(
            value,
            {
                "digest": registry["manifest_digest"],
                "createdTime": registry["manifest_created_time"],
            },
            "manifest lock readback",
        )
    else:
        _fail("unknown lock kind")
    if not _locked(_attributes(value.get("changeableAttributes"), "lock attributes")):
        _fail(f"{kind} lock readback is not immutable")


def _normalized_template(
    template: Any, expected_container: str, image: str, revision_suffix: str | None
) -> Any:
    normalized = copy.deepcopy(_object(template, "workload template"))
    if revision_suffix is None:
        if normalized.get("revisionSuffix") is not None:
            _fail("revision-list template unexpectedly exposes a suffix")
        normalized["revisionSuffix"] = "<bootstrap-suffix>"
    else:
        if normalized.get("revisionSuffix") != revision_suffix:
            _fail("workload revision suffix does not match policy")
        normalized["revisionSuffix"] = "<bootstrap-suffix>"
    containers = _array(normalized.get("containers"), "workload containers")
    if len(containers) != 1:
        _fail("workload has a missing container or sidecars")
    container = _object(containers[0], "workload container")
    if container.get("name") != expected_container:
        _fail("workload container name does not match policy")
    if container.get("image") != image:
        _fail("workload image does not match its expected state")
    container["image"] = "<bootstrap-image>"
    return normalized


def _template_comparison_views(
    app_template: dict[str, Any],
    revision_template: dict[str, Any],
    expected_container: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Normalize only documented app/revision response-projection differences."""
    app_view = copy.deepcopy(app_template)
    revision_view = copy.deepcopy(revision_template)
    # These are directional: the app-resource endpoint materializes the field
    # and the active-revision endpoint omits it. Mirror-image shapes remain drift.
    if (
        app_view.get("customMetricsSettings", _MISSING) is None
        and "customMetricsSettings" not in revision_view
    ):
        app_view.pop("customMetricsSettings")

    app_container = _object(
        _array(app_view.get("containers"), "workload containers")[0],
        "workload container",
    )
    revision_container = _object(
        _array(revision_view.get("containers"), "workload containers")[0],
        "workload container",
    )
    if (
        app_container.get("imageType", _MISSING) == "ContainerImage"
        and "imageType" not in revision_container
    ):
        app_container.pop("imageType")

    app_env = app_container.get("env")
    revision_env = revision_container.get("env")
    if (
        isinstance(app_env, list)
        and isinstance(revision_env, list)
        and len(app_env) == len(revision_env)
        and all(isinstance(item, dict) for item in (*app_env, *revision_env))
    ):
        app_names = [item.get("name", _MISSING) for item in app_env]
        revision_names = [item.get("name", _MISSING) for item in revision_env]
        names_are_unique_strings = (
            all(isinstance(name, str) and name for name in app_names)
            and all(isinstance(name, str) and name for name in revision_names)
            and len(set(app_names)) == len(app_names)
            and len(set(revision_names)) == len(revision_names)
        )
        # Azure's app-resource projection materializes value="" for secretRef
        # entries while the active-revision projection omits value. Require the
        # same unique, ordered names before normalizing individual exact
        # name+secretRef pairs; ambiguous or reordered lists remain drift.
        if names_are_unique_strings and app_names == revision_names:
            for app_entry, revision_entry in zip(app_env, revision_env, strict=True):
                secret_ref = app_entry.get("secretRef", _MISSING)
                if (
                    isinstance(secret_ref, str)
                    and secret_ref
                    and revision_entry.get("secretRef", _MISSING) == secret_ref
                    and app_entry.get("value", _MISSING) == ""
                    and "value" not in revision_entry
                ):
                    app_entry.pop("value")

    app_resources = app_container.get("resources")
    revision_resources = revision_container.get("resources")
    if isinstance(app_resources, dict) and isinstance(revision_resources, dict):
        app_ephemeral = app_resources.get("ephemeralStorage", _MISSING)
        if (
            app_ephemeral == EXPECTED_EPHEMERAL_STORAGE.get(expected_container)
            and "ephemeralStorage" not in revision_resources
        ):
            app_resources.pop("ephemeralStorage")

    app_scale = app_view.get("scale")
    revision_scale = revision_view.get("scale")
    if isinstance(app_scale, dict) and isinstance(revision_scale, dict):
        for field, default in (
            ("cooldownPeriod", 300),
            ("pollingInterval", 30),
        ):
            if (
                app_scale.get(field, _MISSING) == default
                and field in revision_scale
                and revision_scale[field] is None
            ):
                app_scale[field] = None

        app_rules = app_scale.get("rules")
        revision_rules = revision_scale.get("rules")
        if (
            isinstance(app_rules, list)
            and isinstance(revision_rules, list)
            and len(app_rules) == len(revision_rules)
        ):
            for app_rule, revision_rule in zip(app_rules, revision_rules, strict=True):
                if not isinstance(app_rule, dict) or not isinstance(
                    revision_rule, dict
                ):
                    continue
                app_custom = app_rule.get("custom")
                revision_custom = revision_rule.get("custom")
                if not isinstance(app_custom, dict) or not isinstance(
                    revision_custom, dict
                ):
                    continue
                if app_custom.get("auth", _MISSING) == [] and (
                    "auth" not in revision_custom
                ):
                    app_custom.pop("auth")

    return app_view, revision_view


def _validate_workload(
    policy: dict[str, Any], workload_observation: Any, state: str
) -> dict[str, Any]:
    workload = _object(workload_observation, "workload observation")
    name = _string(workload.get("name"), "workload name")
    policies = {
        item["name"]: _object(item, "workload policy")
        for item in _array(policy.get("workloads"), "workload policy")
    }
    if name not in policies:
        _fail("unapproved workload was observed")
    expected = policies[name]
    registry = _object(policy.get("registry"), "registry policy")
    if state == "original":
        image = (
            f"{registry['login_server']}/{registry['repository']}:"
            f"{registry['source_tag']}"
        )
        revision_name = expected["original_revision"]
    elif state == "pinned":
        image = (
            f"{registry['login_server']}/{registry['repository']}@"
            f"{registry['manifest_digest']}"
        )
        revision_name = expected["pinned_revision"]
    else:
        _fail("workload state must be original or pinned")
    revision_suffix = revision_name.rsplit("--", 1)[-1]
    app = _object(workload.get("app"), "container app")
    expected_id = (
        f"/subscriptions/{policy['azure']['subscription_id']}/resourceGroups/"
        f"{policy['azure']['resource_group']}/providers/Microsoft.App/"
        f"containerApps/{name}"
    )
    if app.get("name") != name or str(app.get("id", "")).casefold() != (
        expected_id.casefold()
    ):
        _fail("container app identity does not match policy")
    properties = _object(app.get("properties"), "container app properties")
    expected_environment_id = (
        f"{expected_id.rsplit('/providers/', 1)[0]}/providers/Microsoft.App/"
        "managedEnvironments/cosmaneura-staging-app-environment"
    )
    if str(properties.get("managedEnvironmentId", "")).casefold() != (
        expected_environment_id.casefold()
    ):
        _fail("container app environment does not match policy")
    configuration = _object(properties.get("configuration"), "app configuration")
    if configuration.get("activeRevisionsMode") != "Single":
        _fail("container app is not in Single revision mode")
    if (
        properties.get("latestRevisionName") != revision_name
        or properties.get("latestReadyRevisionName") != revision_name
    ):
        _fail("container app latest/ready revision does not match policy")
    if properties.get("provisioningState") != "Succeeded" or not str(
        properties.get("runningStatus", "")
    ).startswith("Running"):
        _fail("container app is not healthy and provisioned")
    app_template = _normalized_template(
        properties.get("template"), expected["container_name"], image, revision_suffix
    )
    revisions = _array(workload.get("revisions"), "container app revisions")
    active = [
        _object(revision, "container app revision")
        for revision in revisions
        if isinstance(revision, dict)
        and revision.get("properties", {}).get("active") is True
    ]
    if len(active) != 1 or active[0].get("name") != revision_name:
        _fail("container app must have exactly one expected active revision")
    revision_properties = _object(active[0].get("properties"), "revision properties")
    if (
        revision_properties.get("healthState") != "Healthy"
        or revision_properties.get("provisioningState") != "Provisioned"
    ):
        _fail("active revision is not healthy and provisioned")
    if not str(revision_properties.get("runningState", "")).startswith("Running"):
        _fail("active revision is not running")
    if revision_properties.get("trafficWeight") != expected["traffic_weight"]:
        _fail("active revision traffic differs from policy")
    revision_template = _normalized_template(
        revision_properties.get("template"),
        expected["container_name"],
        image,
        None,
    )
    app_comparison, revision_comparison = _template_comparison_views(
        app_template, revision_template, expected["container_name"]
    )
    if not _semantic_json_equal(app_comparison, revision_comparison):
        _fail("app and active-revision templates disagree after normalization")
    ingress = configuration.get("ingress")
    if expected["traffic_weight"] == 100:
        ingress_object = _object(ingress, "api ingress")
        if ingress_object.get("traffic") != [{"latestRevision": True, "weight": 100}]:
            _fail("api traffic is not 100 percent on the latest revision")
    elif ingress is not None:
        _fail("non-api workload unexpectedly exposes ingress")
    control = {
        "id": app["id"],
        "location": app.get("location"),
        "identity": app.get("identity"),
        "tags": app.get("tags"),
        "environment_id": properties.get("managedEnvironmentId"),
    }
    return {
        "name": name,
        "state": state,
        "revision": revision_name,
        "image": image,
        "template_sha256": _digest(app_template),
        "revision_template_sha256": _digest(revision_template),
        "template_comparison_sha256": _digest(_semantic_json_view(app_comparison)),
        "configuration_sha256": _digest(configuration),
        "control_sha256": _digest(control),
        "active_revision_count": 1,
        "health": "Healthy",
        "provisioning": "Provisioned",
        "traffic_weight": expected["traffic_weight"],
    }


def _observed_workload_state(policy: dict[str, Any], workload_observation: Any) -> str:
    workload = _object(workload_observation, "workload observation")
    name = _string(workload.get("name"), "workload name")
    expected = next(
        (item for item in policy["workloads"] if item.get("name") == name), None
    )
    if expected is None:
        _fail("unapproved workload was observed")
    revision = _object(
        _object(workload.get("app"), "container app").get("properties"),
        "container app properties",
    ).get("latestRevisionName")
    if revision == expected["original_revision"]:
        return "original"
    if revision == expected["pinned_revision"]:
        return "pinned"
    _fail("workload revision is neither exact original nor exact pinned")


def validate_capture(
    policy: dict[str, Any],
    observation: dict[str, Any],
    job_log: bytes,
    manifest_bytes: bytes,
    config_bytes: bytes,
) -> dict[str, Any]:
    validate_policy(policy)
    source = _validate_source(policy, observation)
    azure = _validate_azure(policy, observation)
    historical = _validate_historical(policy, observation, job_log)
    _validate_raw_image(policy, manifest_bytes, config_bytes)
    registry_state, registry = _registry_state(
        policy, _object(observation.get("registry"), "registry observation")
    )
    if registry_state not in {"original", "tag_locked", "locked"}:
        _fail("registry lock state is not a reachable bootstrap state")
    observed_workloads = _array(observation.get("workloads"), "workloads")
    if [item.get("name") for item in observed_workloads if isinstance(item, dict)] != [
        item["name"] for item in policy["workloads"]
    ]:
        _fail("workload observation order or membership is not exact")
    workloads = [
        _validate_workload(policy, workload, _observed_workload_state(policy, workload))
        for workload in observed_workloads
    ]
    progress = {
        ("original", ("original", "original", "original")): 0,
        ("tag_locked", ("original", "original", "original")): 1,
        ("locked", ("original", "original", "original")): 2,
        ("locked", ("pinned", "original", "original")): 3,
        ("locked", ("pinned", "pinned", "original")): 4,
        ("locked", ("pinned", "pinned", "pinned")): 5,
    }.get((registry_state, tuple(item["state"] for item in workloads)))
    if progress is None:
        _fail("registry/workload state is not a monotonic reachable prefix")
    result = {
        "schema_version": 1,
        "policy_id": policy["policy_id"],
        "policy_sha256": policy_sha256(policy),
        "observed_state": PROGRESS_STATES[progress],
        "progress_index": progress,
        "source": source,
        "azure": azure,
        "historical_publication": historical,
        "registry": registry,
        "workloads": workloads,
    }
    result["evidence_sha256"] = _digest(result)
    return result


def validate_transition(
    policy: dict[str, Any],
    before: dict[str, Any],
    after: dict[str, Any],
    expected_progress: int = 5,
) -> None:
    validate_policy(policy)
    if before.get("policy_sha256") != policy_sha256(policy) or after.get(
        "policy_sha256"
    ) != policy_sha256(policy):
        _fail("transition evidence is not policy-bound")
    for evidence in (before, after):
        state = evidence.get("observed_state")
        if state not in PROGRESS_STATES or evidence.get(
            "progress_index"
        ) != PROGRESS_STATES.index(state):
            _fail("transition progress index is inconsistent")
    if (
        expected_progress not in range(len(PROGRESS_STATES))
        or after["progress_index"] != expected_progress
        or before["progress_index"] > after["progress_index"]
    ):
        _fail("transition is not the exact expected monotonic progress")
    before_registry = _object(before.get("registry"), "before registry")
    after_registry = _object(after.get("registry"), "after registry")
    if before["progress_index"] >= 1 and before_registry.get(
        "tag_last_update_time"
    ) != after_registry.get("tag_last_update_time"):
        _fail("locked tag changed after verified completion")
    if before["progress_index"] >= 2 and before_registry.get(
        "manifest_last_update_time"
    ) != after_registry.get("manifest_last_update_time"):
        _fail("locked manifest changed after verified completion")
    for key in ("source", "azure", "historical_publication"):
        if before.get(key) != after.get(key):
            _fail(f"{key} drifted during bootstrap")
    before_workloads = _array(before.get("workloads"), "before workloads")
    after_workloads = _array(after.get("workloads"), "after workloads")
    if len(before_workloads) != 3 or len(after_workloads) != 3:
        _fail("transition workload set is incomplete")
    for earlier, later in zip(before_workloads, after_workloads, strict=True):
        left = _object(earlier, "before workload")
        right = _object(later, "after workload")
        if left.get("name") != right.get("name"):
            _fail("transition workload order changed")
        for key in (
            "template_comparison_sha256",
            "configuration_sha256",
            "control_sha256",
            "traffic_weight",
        ):
            if left.get(key) != right.get(key):
                _fail(f"workload {left.get('name')} changed beyond image and suffix")


def validate_workload_transition(
    policy: dict[str, Any],
    baseline: dict[str, Any],
    workload: dict[str, Any],
    state: str = "pinned",
) -> dict[str, Any]:
    current = _validate_workload(policy, workload, state)
    candidates = [
        item
        for item in _array(baseline.get("workloads"), "baseline workloads")
        if isinstance(item, dict) and item.get("name") == current["name"]
    ]
    if len(candidates) != 1:
        _fail("baseline workload is missing or ambiguous")
    earlier = candidates[0]
    for key in (
        "template_comparison_sha256",
        "configuration_sha256",
        "control_sha256",
        "traffic_weight",
    ):
        if earlier.get(key) != current.get(key):
            _fail("revision copy changed more than image and suffix")
    return current


def validate_capture_receipt(
    policy: dict[str, Any],
    receipt_bytes: bytes,
    capture_run: dict[str, Any],
    expected_run_id: str,
    expected_sha256: str,
    current_sha: str,
    now: datetime,
) -> dict[str, Any]:
    if not INTEGER_RE.fullmatch(expected_run_id):
        _fail("capture run id is malformed")
    if not DIGEST_RE.fullmatch(expected_sha256):
        _fail("capture receipt digest is malformed")
    if _sha256(receipt_bytes) != expected_sha256:
        _fail("capture receipt digest does not match")
    receipt = _object(
        _json_from_bytes(receipt_bytes, "capture receipt"), "capture receipt"
    )
    if receipt_bytes != _json_bytes(receipt) + b"\n":
        _fail("capture receipt is not canonical JSON")
    if receipt.get("schema_version") != 1 or receipt.get("policy_id") != policy.get(
        "policy_id"
    ):
        _fail("capture receipt contract does not match policy")
    if receipt.get("policy_sha256") != policy_sha256(policy):
        _fail("capture receipt policy digest does not match")
    if receipt.get("mode") != "capture" or receipt.get("status") != "captured":
        _fail("receipt is not a successful capture")
    operation = _object(receipt.get("operation"), "capture operation")
    if str(operation.get("run_id")) != expected_run_id:
        _fail("capture receipt run id does not match")
    _exact_subset(
        operation,
        {
            "repository": policy["repository"],
            "ref": policy["protected_ref"],
            "workflow_path": policy["workflow_path"],
            "source_sha": current_sha,
            "actor": policy["founder"],
            "triggering_actor": policy["founder"],
        },
        "capture operation",
    )
    generated = _time(receipt.get("generated_at"), "receipt generated time")
    expires = _time(receipt.get("expires_at"), "receipt expiry")
    ttl = timedelta(seconds=policy["capture_ttl_seconds"])
    if expires - generated != ttl:
        _fail("capture receipt expiry is not policy-bound")
    if now < generated - MAX_CLOCK_SKEW or now > expires:
        _fail("capture receipt is not currently valid")
    evidence = _object(receipt.get("evidence"), "capture evidence")
    expected_evidence_digest = evidence.get("evidence_sha256")
    unhashed = dict(evidence)
    unhashed.pop("evidence_sha256", None)
    if expected_evidence_digest != _digest(unhashed):
        _fail("capture evidence digest does not match")
    if evidence.get("policy_sha256") != policy_sha256(policy):
        _fail("capture evidence is not policy-bound")
    source = _object(evidence.get("source"), "capture evidence source")
    if source.get("workflow_sha") != current_sha:
        _fail("capture evidence is not current master")
    if evidence.get("observed_state") not in PROGRESS_STATES:
        _fail("capture evidence state is not resumable")
    expected_run = {
        "id": int(expected_run_id),
        "run_attempt": operation.get("run_attempt"),
        "event": "workflow_dispatch",
        "status": "completed",
        "conclusion": "success",
        "head_branch": "master",
        "head_sha": current_sha,
        "actor": policy["founder"],
        "triggering_actor": policy["founder"],
        "path": policy["workflow_path"],
    }
    _exact_subset(capture_run, expected_run, "capture workflow run")
    return receipt


def extract_receipt(archive_path: Path, output_path: Path) -> None:
    archive = _read_bytes(
        archive_path, maximum=MAX_RECEIPT_BYTES, label="receipt archive"
    )
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
            members = bundle.infolist()
            if len(members) != 1:
                _fail("receipt artifact must contain exactly one file")
            member = members[0]
            mode = member.external_attr >> 16
            if member.filename != "staging-recovery-image-bootstrap-receipt.json":
                _fail("receipt artifact member name is not exact")
            if (
                member.is_dir()
                or stat.S_ISLNK(mode)
                or member.file_size > MAX_RECEIPT_BYTES
            ):
                _fail("receipt artifact member is unsafe")
            value = bundle.read(member)
    except zipfile.BadZipFile as exc:
        raise BootstrapEvidenceError("receipt artifact is not a ZIP file") from exc
    if len(archive) == 0 or len(value) == 0:
        _fail("receipt artifact is empty")
    output_path.write_bytes(value)


def emit_receipt(
    policy: dict[str, Any],
    *,
    mode: str,
    status_value: str,
    phase: str,
    source_sha: str,
    run_id: str,
    run_attempt: str,
    actor: str,
    triggering_actor: str,
    evidence: dict[str, Any] | None,
    capture_run_id: str | None,
    capture_sha256: str | None,
    journal: dict[str, Any],
    output: Path,
    now: datetime,
) -> None:
    if mode not in {"capture", "pin"} or status_value not in {
        "failed",
        "captured",
        "complete",
    }:
        _fail("terminal receipt mode or status is invalid")
    if (
        not SHA_RE.fullmatch(source_sha)
        or not INTEGER_RE.fullmatch(run_id)
        or not INTEGER_RE.fullmatch(run_attempt)
    ):
        _fail("terminal operation identity is malformed")
    if actor != policy["founder"] or triggering_actor != policy["founder"]:
        _fail("terminal receipt actors do not match policy")
    generated = now.astimezone(UTC).isoformat().replace("+00:00", "Z")
    receipt: dict[str, Any] = {
        "schema_version": 1,
        "policy_id": policy["policy_id"],
        "policy_sha256": policy_sha256(policy),
        "mode": mode,
        "status": status_value,
        "phase": phase,
        "generated_at": generated,
        "expires_at": None,
        "operation": {
            "repository": policy["repository"],
            "ref": policy["protected_ref"],
            "workflow_path": policy["workflow_path"],
            "source_sha": source_sha,
            "run_id": int(run_id),
            "run_attempt": int(run_attempt),
            "actor": actor,
            "triggering_actor": triggering_actor,
        },
        "capture_binding": (
            {"run_id": int(capture_run_id), "sha256": capture_sha256}
            if capture_run_id and capture_sha256
            else None
        ),
        "evidence": evidence,
        "mutation_journal": journal,
        "eligibility": {
            "eligible_for_recovery_use": False,
            "requires_independent_review": status_value == "complete",
            "reason": (
                "complete_receipt_requires_independent_exact-receipt review"
                if status_value == "complete"
                else "bootstrap_not_complete"
            ),
        },
    }
    if status_value == "captured":
        expires = now.astimezone(UTC) + timedelta(seconds=policy["capture_ttl_seconds"])
        receipt["expires_at"] = expires.isoformat().replace("+00:00", "Z")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(_json_bytes(receipt) + b"\n")


def _write_json(path: Path, value: Any) -> None:
    path.write_bytes(_json_bytes(value) + b"\n")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    def files(name: str, *paths: str, policy: bool = True) -> argparse.ArgumentParser:
        command = subparsers.add_parser(name)
        if policy:
            command.add_argument("--policy", type=Path, required=True)
        for path in paths:
            command.add_argument(
                f"--{path.replace('_', '-')}", type=Path, required=True
            )
        return command

    files("validate-policy")
    files("validate-capture", "observation", "job_log", "manifest", "config", "output")
    transition = files("validate-transition", "before", "after")
    transition.add_argument(
        "--expected-progress", type=int, choices=range(6), default=5
    )
    lock = files("validate-lock", "metadata")
    lock.add_argument("--kind", choices=("tag", "manifest"), required=True)
    workload = files("validate-workload", "baseline", "workload")
    workload.add_argument("--state", choices=("original", "pinned"), default="pinned")
    receipt = files("validate-receipt", "receipt", "capture_run")
    for argument in ("expected-run-id", "expected-sha256", "current-sha"):
        receipt.add_argument(f"--{argument}", required=True)
    receipt.add_argument("--now")
    files("extract-receipt", "archive", "output", policy=False)
    emit = files("emit-receipt", "journal", "output")
    for argument in (
        "mode",
        "status",
        "phase",
        "source-sha",
        "run-id",
        "run-attempt",
        "actor",
        "triggering-actor",
    ):
        emit.add_argument(f"--{argument}", required=True)
    emit.add_argument("--evidence", type=Path)
    emit.add_argument("--capture-run-id")
    emit.add_argument("--capture-sha256")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "extract-receipt":
            extract_receipt(args.archive, args.output)
            return 0
        policy = load_policy(args.policy)
        if args.command == "validate-policy":
            print(policy_sha256(policy))
        elif args.command == "validate-capture":
            result = validate_capture(
                policy,
                _object(
                    load_json(args.observation, label="observation"), "observation"
                ),
                _read_bytes(args.job_log, maximum=MAX_LOG_BYTES, label="job log"),
                _read_bytes(args.manifest, maximum=MAX_JSON_BYTES, label="manifest"),
                _read_bytes(args.config, maximum=MAX_JSON_BYTES, label="config"),
            )
            _write_json(args.output, result)
        elif args.command == "validate-transition":
            validate_transition(
                policy,
                _object(load_json(args.before, label="before evidence"), "before"),
                _object(load_json(args.after, label="after evidence"), "after"),
                args.expected_progress,
            )
        elif args.command == "validate-lock":
            validate_lock(
                policy, args.kind, load_json(args.metadata, label="lock readback")
            )
        elif args.command == "validate-workload":
            validate_workload_transition(
                policy,
                _object(load_json(args.baseline, label="baseline"), "baseline"),
                _object(load_json(args.workload, label="workload"), "workload"),
                args.state,
            )
        elif args.command == "validate-receipt":
            now = _time(args.now, "current time") if args.now else datetime.now(UTC)
            validate_capture_receipt(
                policy,
                _read_bytes(
                    args.receipt, maximum=MAX_RECEIPT_BYTES, label="capture receipt"
                ),
                _object(
                    load_json(args.capture_run, label="capture run"), "capture run"
                ),
                args.expected_run_id,
                args.expected_sha256,
                args.current_sha,
                now,
            )
        elif args.command == "emit-receipt":
            evidence = (
                _object(load_json(args.evidence, label="evidence"), "evidence")
                if args.evidence
                else None
            )
            journal = _object(load_json(args.journal, label="journal"), "journal")
            emit_receipt(
                policy,
                mode=args.mode,
                status_value=args.status,
                phase=args.phase,
                source_sha=args.source_sha,
                run_id=args.run_id,
                run_attempt=args.run_attempt,
                actor=args.actor,
                triggering_actor=args.triggering_actor,
                evidence=evidence,
                capture_run_id=args.capture_run_id,
                capture_sha256=args.capture_sha256,
                journal=journal,
                output=args.output,
                now=datetime.now(UTC),
            )
        else:  # pragma: no cover - argparse owns command choices
            _fail("unsupported command")
    except BootstrapEvidenceError as exc:
        print(f"staging recovery image bootstrap refused: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
