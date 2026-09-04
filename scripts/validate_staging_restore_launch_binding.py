#!/usr/bin/env python3
"""Dormant launch validators; only the platform-name adapter reads process state."""

# fmt: off
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sys
import urllib.parse
import uuid
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import UTC, datetime, timedelta
from typing import Any, Mapping, NoReturn

try:
    from scripts import authenticate_staging_broker_launch_receipt as receipt_auth
    from scripts import validate_staging_restore_runtime_binding as runtime_binding
except ModuleNotFoundError:  # Direct ``python scripts/...`` imports.
    import authenticate_staging_broker_launch_receipt as receipt_auth
    import validate_staging_restore_runtime_binding as runtime_binding
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
LAUNCH_ID_RE = re.compile(r"^launch-[0-9a-f]{16,64}$")
REHEARSAL_ID_RE = re.compile(r"^[0-9a-f]{12}$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
UNSIGNED_RE = re.compile(r"^(?:0|[1-9][0-9]{0,19})$")
UUID_RE = re.compile(r"^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$")
EXECUTION_NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
EXECUTION_NAME_ENV = "CONTAINER_APP_JOB_EXECUTION_NAME"
IMAGE_RE = re.compile(r"^([a-z0-9.-]+/[a-z0-9._/-]+)@(sha256:[0-9a-f]{64})$")
POINTER_ENV_TO_FIELD = {
    "COSMANEURA_RUNTIME_RECORD_URI": "record_uri",
    "COSMANEURA_RUNTIME_RECORD_SHA256": "record_sha256",
    "COSMANEURA_RUNTIME_SIGNATURE_BUNDLE_URI": "signature_bundle_uri",
    "COSMANEURA_RUNTIME_SIGNATURE_BUNDLE_SHA256": "signature_bundle_sha256",
}
JOB_KINDS = ("provision", "cleanup")
JOB_ALLOWED_ROOT = frozenset({"id", "name", "type", "location", "identity", "properties", "systemData"})
JOB_REQUIRED_ROOT = frozenset({"id", "name", "type", "location", "identity", "properties"})
PROPERTY_COMPUTED = frozenset({"provisioningState", "eventStreamEndpoint", "outboundIpAddresses"})
EXECUTION_COMPUTED = frozenset({"status", "startTime", "endTime", "replicaStatuses"})
class LaunchBindingFailure(RuntimeError):
    """Bounded launch-contract refusal."""
def _fail(code: str) -> NoReturn:
    raise LaunchBindingFailure(code)
def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii")
    except (TypeError, ValueError, UnicodeEncodeError, RecursionError):
        raise LaunchBindingFailure("canonical.invalid") from None
def _digest(raw: bytes) -> str:
    return f"sha256:{hashlib.sha256(raw).hexdigest()}"
def _matches(pattern: re.Pattern[str], value: Any) -> bool:
    return type(value) is str and pattern.fullmatch(value) is not None
def _client_request_id(record_sha256: str) -> str:
    return str(uuid.UUID(hex=record_sha256[7:39], version=4))
def _mapping(value: Any, fields: set[str] | frozenset[str], code: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != set(fields):
        _fail(code)
    return value
def _parse(raw: bytes, *, maximum: int, code: str) -> dict[str, Any]:
    if type(raw) is not bytes or not 1 <= len(raw) <= maximum:
        _fail(code)
    def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in values:
            if key in result:
                _fail(code)
            result[key] = value
        return result
    try:
        value = json.loads(raw, object_pairs_hook=pairs, parse_constant=lambda _value: _fail(code), parse_float=lambda raw_float: number if math.isfinite(number := float(raw_float)) else _fail(code))
    except LaunchBindingFailure:
        raise
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise LaunchBindingFailure(code) from None
    if type(value) is not dict:
        _fail(code)
    return value
def _time(value: Any, code: str) -> datetime:
    if type(value) is str:
        try:
            parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        except ValueError:
            raise LaunchBindingFailure(code) from None
        if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") == value:
            return parsed
    _fail(code)
def _exact_time(value: Any, code: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.microsecond:
        _fail(code)
    return value.astimezone(UTC)
def _versioned_blob(value: Any, *, host: str, container: str, rehearsal_id: str, code: str) -> tuple[str, str]:
    if type(value) is not str:
        _fail(code)
    try:
        uri = urllib.parse.urlsplit(value)
        query = urllib.parse.parse_qs(uri.query, strict_parsing=True)
    except ValueError:
        _fail(code)
    if (
        uri.scheme != "https"
        or uri.hostname != host
        or uri.netloc != host
        or not uri.path.startswith(f"/{container}/{rehearsal_id}/")
        or uri.path.endswith("/")
        or "/" in uri.path.removeprefix(f"/{container}/{rehearsal_id}/")
        or uri.fragment
        or set(query) != {"versionid"}
        or len(query["versionid"]) != 1
        or not query["versionid"][0]
    ):
        _fail(code)
    return value, query["versionid"][0]
def _versioned_secret(value: Any, *, host: str, version: Any, code: str) -> None:
    if type(value) is not str or type(version) is not str:
        _fail(code)
    try:
        uri = urllib.parse.urlsplit(value)
    except ValueError:
        _fail(code)
    parts = tuple(part for part in uri.path.split("/") if part)
    if uri.scheme != "https" or uri.hostname != host or uri.netloc != host or uri.query or uri.fragment or len(parts) != 3 or parts[0] != "secrets" or parts[2] != version:
        _fail(code)
def validate_launch_binding_policy(policy: Any) -> dict[str, Any]:
    document = runtime_binding.validate_runtime_binding_policy(policy)
    launch = document["launch_binding"]
    reference = document["external_runtime_record"]["reference"]
    jobs = document["runtime"]["jobs"]
    identities = document["identities"]
    mailbox = launch["mailbox"]
    signing = launch["receipt_signing"]
    signing_role = signing["role_definition"]
    job_projection = launch["canonical_job_projection"]
    start_response = launch["job_start"]["response"]
    execution_observation = launch["execution_observation"]
    if (
        reference["fixed_plain_environment_variables"] != list(POINTER_ENV_TO_FIELD)
        or launch["pointer_environment"]["exact_names"] != list(POINTER_ENV_TO_FIELD)
        or job_projection["excluded_environment_variables"] != list(POINTER_ENV_TO_FIELD)
        or job_projection["exact_manual_trigger_config"] != {"parallelism": 1, "replicaCompletionCount": 1}
        or job_projection["normalized_execution_template_projection"] != {"required_fields": ["containers"], "optional_empty_fields": ["initContainers"], "output_fields": ["containers", "initContainers"], "job_template_required_fields": ["containers"], "job_template_optional_empty_fields": ["initContainers", "volumes"]}
        or launch["job_start"]["query_parameters"] != [{"name": "api-version", "value": "2024-03-01"}]
        or launch["job_start"]["management_origin"] != launch["job_configuration_observation"]["management_origin"]
        or launch["job_start"]["resource_path_suffix"] != "/start"
        or start_response["body_required_fields"] != ["id", "name"]
        or start_response["body_unknown_fields_permitted"] is not False
        or start_response["exact_execution_resource_id_format"] != "{selected_job_resource_id}/executions/{response_json.name}"
        or execution_observation["body_required_fields"] != ["name", "properties"]
        or execution_observation["body_optional_fields"] != ["id", "type", "systemData"]
        or execution_observation["properties_required_fields"] != ["template"]
        or execution_observation["optional_id_and_type_must_be_exact"] is not True
        or execution_observation["execution_template_schema"] != {"required_fields": ["containers"], "optional_fields": ["initContainers"], "unknown_fields_permitted": False, "init_containers": "absent-or-empty-list", "volumes_permitted": False}
        or launch["broker_request_authentication"]["launch_record_claim_bindings"]
        != {
            "workflow_sha": "workflow_sha",
            "run_id": "run_id",
            "run_attempt": "run_attempt",
        }
        or mailbox["linked_vnet_resource_ids"] != [document["runtime"]["container_apps_environment"]["control_vnet_resource_id"]]
        or launch["broker_request_authentication"]["replay_store"]["key_fields"] != ["iss", "jti"]
        or launch["broker_request_authentication"]["replay_store"]["receipt_metadata_fields"] != ["action", "launch_record_sha256"]
        or signing_role["actions"]
        or signing_role["not_actions"]
        or signing_role["data_actions"] != [signing["broker_sign_action"]]
        or signing_role["not_data_actions"]
    ):
        _fail("launch_policy.pointer_binding")
    for action in JOB_KINDS:
        dispatcher = identities["dispatchers"][action]
        broker = identities["brokers"][action]
        job_identity = identities["jobs"][action]
        if (
            dispatcher["broker_request"]["broker_identity_resource_id"] != broker["resource_id"]
            or dispatcher["broker_request"]["endpoint_resource_id"] != broker["endpoint_resource_id"]
            or dispatcher["broker_request"]["oidc_audience_client_id"] != broker["client_id"]
            or dispatcher["broker_request"]["exact_workflow_ref"] != launch["broker_request_authentication"]["exact_workflow_ref"]
            or dispatcher["broker_request"]["job_read_or_start_actions"]
            or dispatcher["broker_request"]["role_assignment_ids"]
            or broker["caller_identity_resource_id"] != dispatcher["resource_id"]
            or broker["federated_credential_ids"]
            or broker["job_control"]["readable_job_resource_ids"] != [jobs[name]["resource_id"] for name in JOB_KINDS]
            or broker["job_control"]["selected_job_resource_id"] != jobs[action]["resource_id"]
            or broker["job_control"]["read_role_assignment_scopes"] != {name: jobs[name]["resource_id"] for name in JOB_KINDS}
            or broker["job_control"]["start_role_assignment_scope"] != jobs[action]["resource_id"]
            or broker["job_control"]["start_request"] != launch["job_start"]
            or broker["job_control"]["actions"]
            != {
                "read": ["Microsoft.App/jobs/read"],
                "start": ["Microsoft.App/jobs/start/action"],
            }
            or broker["other_role_assignment_ids"]
            or mailbox["role_assignment_ids"][f"{action}_broker_create"] != broker["launch_mailbox_create_role_assignment_id"]
            or mailbox["role_assignment_ids"][f"{action}_job_read"] != job_identity["role_assignment_ids"]["launch_mailbox_read"]
            or launch["receipt_signing"]["broker_role_assignment_ids"][action] != broker["launch_receipt_sign_role_assignment_id"]
            or launch["receipt_signing"]["broker_role_assignment_scopes"][action] != launch["receipt_signing"]["key_resource_id"]
            or launch["receipt_signing"]["broker_role_assignment_role_definition_ids"][action] != signing_role["role_definition_id"]
        ):
            _fail(f"launch_policy.{action}_broker_binding")
    return document
_ARM_GET_SEAL = object()
_JOB_OBSERVATION_SEAL = object()
_EXECUTION_OBSERVATION_SEAL = object()
_LAUNCH_RECORD_SEAL = object()
_VERIFIED_OIDC_SEAL = object()
_START_AUTHORIZATION_SEAL = object()
_START_RESPONSE_SEAL = object()
_AUTHENTICATED_RECEIPT_SEAL = object()
_VALIDATED_RECEIPT_SEAL = object()
def _sealed(cls: type[Any], seal: object, values: Mapping[str, Any]) -> Any:
    result = object.__new__(cls)
    for name, value in values.items():
        object.__setattr__(result, name, value)
    object.__setattr__(result, "_seal", seal)
    return result
@dataclass(frozen=True, slots=True, init=False)
class AuthenticatedArmGetResponse:
    resource_id: str
    api_version: str
    status_code: int
    content_type: str
    observed_at: datetime
    body: bytes
    _seal: object = dataclass_field(repr=False, compare=False)
    def __init__(self) -> None:
        _fail("arm_get.unsealed")
    @classmethod
    def _from_reviewed_managed_identity_adapter(
        cls,
        *,
        resource_id: str,
        api_version: str,
        status_code: int,
        content_type: str,
        observed_at: datetime,
        body: bytes,
    ) -> AuthenticatedArmGetResponse:
        if cls is not AuthenticatedArmGetResponse or type(resource_id) is not str or not runtime_binding._azure_resource_id(resource_id) or api_version != "2024-03-01" or status_code != 200 or type(content_type) is not str or content_type.split(";", 1)[0].strip().lower() != "application/json" or type(body) is not bytes:
            _fail("arm_get.invalid")
        instant = _exact_time(observed_at, "arm_get.invalid")
        return _sealed(cls, _ARM_GET_SEAL, {
            "resource_id": resource_id,
            "api_version": api_version,
            "status_code": status_code,
            "content_type": content_type,
            "observed_at": instant,
            "body": bytes(body),
        })
    def _is_authenticated(self) -> bool:
        return type(self) is AuthenticatedArmGetResponse and getattr(self, "_seal", None) is _ARM_GET_SEAL
@dataclass(frozen=True, slots=True, init=False)
class AuthenticatedJobConfigurationObservation:
    job_kind: str
    job_resource_id: str
    observed_at: datetime
    body_sha256: str
    projection_sha256: str
    execution_template_sha256: str
    image_reference: str
    runtime_reference: Mapping[str, str]
    _seal: object = dataclass_field(repr=False, compare=False)
    def __init__(self) -> None:
        _fail("job_observation.unsealed")
    def _is_authenticated(self) -> bool:
        return type(self) is AuthenticatedJobConfigurationObservation and getattr(self, "_seal", None) is _JOB_OBSERVATION_SEAL
def _execution_template_projection(value: Any, *, job_template: bool, code: str) -> dict[str, Any]:
    allowed = {"containers", "initContainers", "volumes"} if job_template else {"containers", "initContainers"}
    if type(value) is not dict or set(value) - allowed or "containers" not in value:
        _fail(code)
    containers, init_containers, volumes = value["containers"], value.get("initContainers", []), value.get("volumes", [])
    if type(containers) is not list or not containers or any(type(item) is not dict for item in containers) or type(init_containers) is not list or init_containers or (job_template and (type(volumes) is not list or volumes)):
        _fail(code)
    return {"containers": containers, "initContainers": []}
def _job_projection(document: dict[str, Any], *, policy: dict[str, Any], job_kind: str) -> tuple[dict[str, Any], dict[str, str], str]:
    job = policy["runtime"]["jobs"][job_kind]
    identity_policy = policy["identities"]["jobs"][job_kind]
    identity_id = identity_policy["resource_id"]
    if set(document) - JOB_ALLOWED_ROOT or not JOB_REQUIRED_ROOT <= set(document):
        _fail("job_observation.closed_world")
    if document["id"] != job["resource_id"] or document["name"] != job["name"] or type(document["type"]) is not str or document["type"].lower() != "microsoft.app/jobs" or document["location"] != policy["runtime"]["container_apps_environment"]["location"]:
        _fail("job_observation.identity")
    identity = document["identity"]
    if type(identity) is not dict or not {"type", "userAssignedIdentities"} <= set(identity) or set(identity) - {"type", "userAssignedIdentities", "principalId", "tenantId"} or identity["type"] != "UserAssigned" or type(identity["userAssignedIdentities"]) is not dict or set(identity["userAssignedIdentities"]) != {identity_id}:
        _fail("job_observation.managed_identity")
    identity_value = identity["userAssignedIdentities"][identity_id]
    if type(identity_value) is not dict or set(identity_value) != {"clientId", "principalId"} or identity_value["clientId"] != identity_policy["client_id"] or identity_value["principalId"] != identity_policy["principal_id"]:
        _fail("job_observation.managed_identity")
    properties = document["properties"]
    required_properties = {
        "environmentId",
        "workloadProfileName",
        "configuration",
        "template",
    }
    if type(properties) is not dict or set(properties) - required_properties - PROPERTY_COMPUTED or not required_properties <= set(properties) or properties["environmentId"] != job["environment_resource_id"] or type(properties["workloadProfileName"]) is not str or not properties["workloadProfileName"]:
        _fail("job_observation.properties")
    configuration = _mapping(properties["configuration"], {"triggerType", "replicaTimeout", "replicaRetryLimit", "manualTriggerConfig", "registries"}, "job_observation.configuration")
    manual = _mapping(configuration["manualTriggerConfig"], {"parallelism", "replicaCompletionCount"}, "job_observation.manual_trigger")
    if configuration["triggerType"] != "Manual" or type(configuration["replicaTimeout"]) is not int or configuration["replicaTimeout"] <= 0 or type(configuration["replicaRetryLimit"]) is not int or configuration["replicaRetryLimit"] != 0 or any(type(manual[name]) is not int or manual[name] != 1 for name in manual):
        _fail("job_observation.trigger")
    registries = configuration["registries"]
    expected_registry = {
        "server": policy["private_registry"]["host"],
        "identity": identity_id,
    }
    if type(registries) is not list or registries != [expected_registry]:
        _fail("job_observation.registry")
    template = properties["template"]
    execution_template = _execution_template_projection(template, job_template=True, code="job_observation.template")
    if len(execution_template["containers"]) != 1:
        _fail("job_observation.template")
    container = _mapping(template["containers"][0], {"name", "image", "command", "args", "env", "resources"}, "job_observation.container")
    if (
        type(container["name"]) is not str
        or not container["name"]
        or type(container["command"]) is not list
        or type(container["args"]) is not list
        or any(type(item) is not str for item in (*container["command"], *container["args"]))
        or type(container["image"]) is not str
        or not _matches(IMAGE_RE, container["image"])
        or not container["image"].startswith(f"{job['image_repository']}@")
    ):
        _fail("job_observation.container")
    resources = _mapping(container["resources"], {"cpu", "memory"}, "job_observation.resources")
    if type(resources["cpu"]) not in {int, float, str} or type(resources["memory"]) is not str or not resources["memory"]:
        _fail("job_observation.resources")
    env = container["env"]
    if type(env) is not list:
        _fail("job_observation.environment")
    values: dict[str, str] = {}
    nonpointer: list[dict[str, str]] = []
    for item in env:
        item = _mapping(item, {"name", "value"}, "job_observation.environment")
        name, value = item["name"], item["value"]
        if type(name) is not str or type(value) is not str or not name or name in values or name == EXECUTION_NAME_ENV:
            _fail("job_observation.environment")
        values[name] = value
        if name not in POINTER_ENV_TO_FIELD:
            nonpointer.append({"name": name, "value": value})
    if not set(POINTER_ENV_TO_FIELD) <= set(values):
        _fail("job_observation.runtime_pointer")
    pointer = {field: values[name] for name, field in POINTER_ENV_TO_FIELD.items()}
    for field, value in pointer.items():
        runtime_binding._pin(
            value,
            "https_uri" if field.endswith("uri") else "digest",
            "job_observation.runtime_pointer",
        )
        if runtime_binding.PLACEHOLDER_RE.fullmatch(value):
            _fail("job_observation.runtime_pointer")
    projected_template = {
        "containers": [
            {
                **{key: container[key] for key in ("name", "image", "command", "args", "resources")},
                "env": sorted(nonpointer, key=lambda item: item["name"]),
            }
        ],
        "initContainers": [],
        "volumes": [],
    }
    projection = {
        "schema_version": 1,
        "id": document["id"],
        "name": document["name"],
        "type": document["type"],
        "location": document["location"],
        "identity": {
            "type": "UserAssigned",
            "userAssignedIdentityResourceIds": [identity_id],
        },
        "properties": {
            "environmentId": properties["environmentId"],
            "workloadProfileName": properties["workloadProfileName"],
            "configuration": configuration,
            "template": projected_template,
        },
    }
    return projection, pointer, _digest(_canonical(execution_template))
def validate_job_configuration_observation(policy: Any, *, job_kind: str, response: AuthenticatedArmGetResponse) -> AuthenticatedJobConfigurationObservation:
    document_policy = validate_launch_binding_policy(policy)
    if job_kind not in JOB_KINDS or type(response) is not AuthenticatedArmGetResponse or not response._is_authenticated() or response.resource_id != document_policy["runtime"]["jobs"][job_kind]["resource_id"]:
        _fail("job_observation.authentication")
    raw_document = _parse(response.body, maximum=document_policy["launch_binding"]["job_configuration_observation"]["maximum_bytes"], code="job_observation.invalid")
    projection, pointer, template_digest = _job_projection(raw_document, policy=document_policy, job_kind=job_kind)
    projection_digest = _digest(_canonical(projection))
    if projection_digest != document_policy["runtime"]["jobs"][job_kind]["configuration_template_sha256"]:
        _fail("job_observation.projection_digest")
    return _sealed(AuthenticatedJobConfigurationObservation, _JOB_OBSERVATION_SEAL, {
        "job_kind": job_kind,
        "job_resource_id": response.resource_id,
        "observed_at": response.observed_at,
        "body_sha256": _digest(response.body),
        "projection_sha256": projection_digest,
        "execution_template_sha256": template_digest,
        "image_reference": raw_document["properties"]["template"]["containers"][0]["image"],
        "runtime_reference": dict(pointer),
    })
@dataclass(frozen=True, slots=True, init=False)
class ValidatedLaunchRecord:
    record_bytes: bytes
    record_sha256: str
    record_uri: str
    action: str
    rehearsal_id: str | None
    created_at: datetime
    expires_at: datetime
    runtime_record_sha256: str
    workflow_sha: str
    run_id: str
    run_attempt: str
    evidence_file_sha256s: tuple[tuple[str, str], ...]
    attestation_bundle_sha256: str | None
    server_scope_hmac_key_sha256: str | None
    trusted_root_sha256: str
    ttl_hours: int | None
    _seal: object = dataclass_field(repr=False, compare=False)
    def __init__(self) -> None:
        _fail("launch_record.unsealed")
    def _is_validated(self) -> bool:
        return type(self) is ValidatedLaunchRecord and getattr(self, "_seal", None) is _LAUNCH_RECORD_SEAL
def validate_launch_record(policy: Any, record_bytes: bytes, *, record_sha256: str, record_uri: str) -> ValidatedLaunchRecord:
    document_policy = validate_launch_binding_policy(policy)
    contract = document_policy["launch_binding"]["launch_record"]
    record = _parse(record_bytes, maximum=contract["maximum_bytes"], code="launch_record.invalid")
    if record_bytes != _canonical(record) or record_sha256 != _digest(record_bytes) or not _matches(DIGEST_RE, record_sha256):
        _fail("launch_record.content_address")
    _mapping(record, set(contract["required_fields"]), "launch_record.closed_world")
    action = record["action"]
    if (
        action not in JOB_KINDS
        or type(record["schema_version"]) is not int
        or record["schema_version"] != 1
        or record["policy_id"] != document_policy["policy_id"]
        or record["policy_binding_sha256"] != runtime_binding._policy_binding_digest(document_policy)
        or record["repository"] != "CosmaNeura/cosmaneura"
        or record["protected_ref"] != "refs/heads/master"
        or type(record["launch_id"]) is not str
        or LAUNCH_ID_RE.fullmatch(record["launch_id"]) is None
        or type(record["workflow_sha"]) is not str
        or SHA_RE.fullmatch(record["workflow_sha"]) is None
        or type(record["run_id"]) is not str
        or UNSIGNED_RE.fullmatch(record["run_id"]) is None
        or type(record["run_attempt"]) is not str
        or UNSIGNED_RE.fullmatch(record["run_attempt"]) is None
        or type(record["runtime_record_sha256"]) is not str
        or DIGEST_RE.fullmatch(record["runtime_record_sha256"]) is None
    ):
        _fail("launch_record.binding")
    created, expires = (
        _time(record["created_at"], "launch_record.time"),
        _time(record["expires_at"], "launch_record.time"),
    )
    if not created < expires <= created + timedelta(hours=24):
        _fail("launch_record.time")
    mailbox = document_policy["launch_binding"]["mailbox"]
    if type(record_uri) is not str:
        _fail("launch_record.uri")
    try:
        uri = urllib.parse.urlsplit(record_uri)
    except ValueError:
        _fail("launch_record.uri")
    expected_path = f"/{mailbox['container_names'][action]}/{record_sha256[7:]}.json"
    if uri.scheme != "https" or uri.netloc != mailbox["account_host"] or uri.path != expected_path or uri.query or uri.fragment:
        _fail("launch_record.uri")
    root = _mapping(
        record["github_trusted_root"],
        set(contract["github_trusted_root_fields"]),
        "launch_record.trusted_root",
    )
    root_policy = document_policy["evidence_ingress"]["private_versioned_secret_source"]["secrets"]["github_trusted_root"]
    if root != {
        "versioned_https_uri": root_policy["versioned_https_uri"],
        "version": root_policy["version"],
        "sha256": root_policy["sha256"],
    }:
        _fail("launch_record.trusted_root")
    payload = record["payload"]
    rehearsal_id: str | None
    evidence_file_sha256s: tuple[tuple[str, str], ...] = ()
    attestation_bundle_sha256: str | None = None
    server_scope_hmac_key_sha256: str | None = None
    ttl_hours: int | None = None
    if action == "provision":
        provision = contract["provision"]
        payload = _mapping(payload, set(provision["payload_fields"]), "launch_record.provision")
        rehearsal_id = payload["rehearsal_id"]
        if type(rehearsal_id) is not str or REHEARSAL_ID_RE.fullmatch(rehearsal_id) is None:
            _fail("launch_record.provision")
        evidence = _mapping(
            payload["evidence_files"],
            set(provision["exact_evidence_file_names"]),
            "launch_record.evidence",
        )
        blob_policy = document_policy["evidence_ingress"]["private_restore_evidence_container"]
        observed_uris: set[str] = set()
        for name, item in evidence.items():
            item = _mapping(item, set(provision["evidence_object_fields"]), "launch_record.evidence")
            uri_value, version = _versioned_blob(item["versioned_https_uri"], host=blob_policy["account_host"], container=blob_policy["container_name"], rehearsal_id=rehearsal_id, code="launch_record.evidence")
            if item["version_id"] != version or not _matches(DIGEST_RE, item["sha256"]) or uri_value in observed_uris or not urllib.parse.urlsplit(uri_value).path.endswith(f"/{name}"):
                _fail("launch_record.evidence")
            observed_uris.add(uri_value)
        attestation = _mapping(
            payload["github_attestation_bundle"],
            set(provision["github_attestation_bundle_fields"]),
            "launch_record.attestation",
        )
        attestation_uri, attestation_version = _versioned_blob(attestation["versioned_https_uri"], host=blob_policy["account_host"], container=blob_policy["container_name"], rehearsal_id=rehearsal_id, code="launch_record.attestation")
        if attestation["version_id"] != attestation_version or not _matches(DIGEST_RE, attestation["sha256"]) or attestation_uri in observed_uris:
            _fail("launch_record.attestation")
        hmac = _mapping(
            payload["server_scope_hmac_key"],
            set(provision["server_scope_hmac_key_fields"]),
            "launch_record.hmac",
        )
        hmac_policy = document_policy["evidence_ingress"]["private_versioned_secret_source"]["secrets"]["server_scope_hmac_key"]
        _versioned_secret(hmac["versioned_https_uri"], host=document_policy["evidence_ingress"]["private_versioned_secret_source"]["vault_host"], version=hmac["version"], code="launch_record.hmac")
        if (
            hmac["versioned_https_uri"] != hmac_policy["versioned_https_uri"]
            or hmac["version"] != hmac_policy["version"]
            or hmac["encoding"] != "base64url-no-padding"
            or not _matches(DIGEST_RE, hmac["decoded_sha256"])
            or type(payload["ttl_hours"]) is not int
            or not 1 <= payload["ttl_hours"] <= 24
            or expires != created + timedelta(hours=payload["ttl_hours"])
        ):
            _fail("launch_record.hmac")
        evidence_file_sha256s = tuple(
            (name, evidence[name]["sha256"])
            for name in provision["exact_evidence_file_names"]
        )
        attestation_bundle_sha256 = attestation["sha256"]
        server_scope_hmac_key_sha256 = hmac["decoded_sha256"]
        ttl_hours = payload["ttl_hours"]
    else:
        cleanup = contract["cleanup"]
        if type(payload) is not dict or payload.get("mode") not in cleanup["allowed_modes"]:
            _fail("launch_record.cleanup")
        expected = cleanup["selected_payload_fields"] if payload["mode"] == "selected_rehearsal" else cleanup["sweep_payload_fields"]
        _mapping(payload, set(expected), "launch_record.cleanup")
        rehearsal_id = payload.get("rehearsal_id")
        if payload["mode"] == "selected_rehearsal":
            if type(rehearsal_id) is not str or REHEARSAL_ID_RE.fullmatch(rehearsal_id) is None:
                _fail("launch_record.cleanup")
        elif rehearsal_id is not None:
            _fail("launch_record.cleanup")
    return _sealed(ValidatedLaunchRecord, _LAUNCH_RECORD_SEAL, {
        "record_bytes": bytes(record_bytes),
        "record_sha256": record_sha256,
        "record_uri": record_uri,
        "action": action,
        "rehearsal_id": rehearsal_id,
        "created_at": created,
        "expires_at": expires,
        "runtime_record_sha256": record["runtime_record_sha256"],
        "workflow_sha": record["workflow_sha"],
        "run_id": record["run_id"],
        "run_attempt": record["run_attempt"],
        "evidence_file_sha256s": evidence_file_sha256s,
        "attestation_bundle_sha256": attestation_bundle_sha256,
        "server_scope_hmac_key_sha256": server_scope_hmac_key_sha256,
        "trusted_root_sha256": root["sha256"],
        "ttl_hours": ttl_hours,
    })
@dataclass(frozen=True, slots=True, init=False)
class BrokerStartAuthorization:
    action: str
    selected_job_resource_id: str
    request_url: str
    launch_record_uri: str
    launch_record_sha256: str
    client_request_id: str
    oidc_jti_sha256: str
    oidc_replay_receipt_sha256: str
    authorized_at: datetime
    launch_expires_at: datetime
    _seal: object = dataclass_field(repr=False, compare=False)
    def __init__(self) -> None:
        _fail("job_start.unsealed")
@dataclass(frozen=True, slots=True, init=False)
class VerifiedOneUseGithubOidc:
    action: str
    launch_record_sha256: str
    oidc_jti_sha256: str
    replay_receipt_sha256: str
    consumed_at: datetime
    token_expires_at: datetime
    _seal: object = dataclass_field(repr=False, compare=False)
    def __init__(self) -> None:
        _fail("broker_authentication.unsealed")
    @classmethod
    def _from_reviewed_signature_nonce_replay_adapter(
        cls,
        *,
        policy: Any,
        action: str,
        launch_record: ValidatedLaunchRecord,
        verified_claim_projection: Mapping[str, Any],
        replay_key_sha256: str,
        replay_receipt_sha256: str,
        consumed_at: datetime,
    ) -> VerifiedOneUseGithubOidc:
        document = validate_launch_binding_policy(policy)
        instant = _exact_time(consumed_at, "broker_authentication.invalid")
        if cls is not VerifiedOneUseGithubOidc or action not in JOB_KINDS or type(launch_record) is not ValidatedLaunchRecord or not launch_record._is_validated() or launch_record.action != action or type(verified_claim_projection) is not dict:
            _fail("broker_authentication.invalid")
        claims = verified_claim_projection
        contract = document["launch_binding"]["broker_request_authentication"]
        request = document["identities"]["dispatchers"][action]["broker_request"]
        now = int(instant.timestamp())
        replay_key = _digest(_canonical({"iss": claims.get("iss"), "jti": claims.get("jti")}))
        if (
            set(claims) != set(contract["allowlisted_claim_projection"])
            or claims.get("iss") != request["exact_issuer"]
            or claims.get("sub") != request["exact_subject"]
            or claims.get("aud") != request["oidc_audience_client_id"]
            or claims.get("repository") != request["exact_repository"]
            or claims.get("ref") != request["exact_ref"]
            or claims.get("environment") != request["exact_environment"]
            or claims.get("workflow_ref") != request["exact_workflow_ref"]
            or claims.get("workflow_sha") != launch_record.workflow_sha
            or claims.get("run_id") != launch_record.run_id
            or claims.get("run_attempt") != launch_record.run_attempt
            or type(claims.get("jti")) is not str
            or not 1 <= len(claims["jti"]) <= 256
            or any(0xD800 <= ord(char) <= 0xDFFF for char in claims["jti"])
            or type(claims.get("iat")) is not int
            or type(claims.get("exp")) is not int
            or not claims["iat"] <= now < claims["exp"] <= claims["iat"] + 600
            or not launch_record.created_at <= instant < launch_record.expires_at
            or replay_key_sha256 != replay_key
            or not _matches(DIGEST_RE, replay_receipt_sha256)
        ):
            _fail("broker_authentication.invalid")
        return _sealed(cls, _VERIFIED_OIDC_SEAL, {
            "action": action,
            "launch_record_sha256": launch_record.record_sha256,
            "oidc_jti_sha256": _digest(claims["jti"].encode("utf-8")),
            "replay_receipt_sha256": replay_receipt_sha256,
            "consumed_at": instant,
            "token_expires_at": datetime.fromtimestamp(claims["exp"], UTC),
        })
def validate_broker_start_request(
    policy: Any,
    *,
    action: str,
    caller: VerifiedOneUseGithubOidc,
    launch_record: ValidatedLaunchRecord,
    observed_at: datetime,
    request_url: str,
    method: str,
    entity: bytes | None,
    query_parameters: tuple[tuple[str, str], ...],
    content_type: str | None,
    attempt_number: int,
    retry_count: int,
    ambiguous_previous_attempt: bool,
) -> BrokerStartAuthorization:
    document = validate_launch_binding_policy(policy)
    if (
        action not in JOB_KINDS
        or type(caller) is not VerifiedOneUseGithubOidc
        or getattr(caller, "_seal", None) is not _VERIFIED_OIDC_SEAL
        or type(launch_record) is not ValidatedLaunchRecord
        or not launch_record._is_validated()
        or caller.action != action
        or caller.launch_record_sha256 != launch_record.record_sha256
        or launch_record.action != action
        or request_url != f"{document['launch_binding']['job_start']['management_origin']}{document['runtime']['jobs'][action]['resource_id']}{document['launch_binding']['job_start']['resource_path_suffix']}?api-version=2024-03-01"
        or method != "POST"
        or (entity is not None and (type(entity) is not bytes or entity != b""))
        or query_parameters != (("api-version", "2024-03-01"),)
        or content_type is not None
        or type(attempt_number) is not int
        or attempt_number != 1
        or type(retry_count) is not int
        or retry_count != 0
        or ambiguous_previous_attempt is not False
    ):
        _fail("job_start.invalid")
    instant = _exact_time(observed_at, "job_start.invalid")
    if not (launch_record.created_at <= caller.consumed_at <= instant < min(launch_record.expires_at, caller.token_expires_at)):
        _fail("job_start.expired")
    return _sealed(BrokerStartAuthorization, _START_AUTHORIZATION_SEAL, {"action": action, "selected_job_resource_id": document["runtime"]["jobs"][action]["resource_id"], "request_url": request_url, "launch_record_uri": launch_record.record_uri, "launch_record_sha256": launch_record.record_sha256, "client_request_id": _client_request_id(launch_record.record_sha256), "oidc_jti_sha256": caller.oidc_jti_sha256, "oidc_replay_receipt_sha256": caller.replay_receipt_sha256, "authorized_at": instant, "launch_expires_at": launch_record.expires_at})
@dataclass(frozen=True, slots=True, init=False)
class AuthenticatedBrokerStartResponse:
    action: str
    selected_job_resource_id: str
    launch_record_uri: str
    launch_record_sha256: str
    execution_resource_id: str
    execution_name: str
    response_body_sha256: str
    client_request_id: str
    provider_request_id: str
    oidc_jti_sha256: str
    oidc_replay_receipt_sha256: str
    observed_at: datetime
    _seal: object = dataclass_field(repr=False, compare=False)
    def __init__(self) -> None:
        _fail("job_start_response.unsealed")
    @classmethod
    def _from_reviewed_broker_transport(
        cls,
        *,
        authorization: BrokerStartAuthorization,
        status_code: int,
        content_type: str,
        response_body: bytes,
        echoed_client_request_id: str,
        provider_request_id: str,
        observed_at: datetime,
    ) -> AuthenticatedBrokerStartResponse:
        if (
            cls is not AuthenticatedBrokerStartResponse
            or type(authorization) is not BrokerStartAuthorization
            or getattr(authorization, "_seal", None) is not _START_AUTHORIZATION_SEAL
            or status_code != 200
            or type(content_type) is not str
            or content_type.split(";", 1)[0].strip().lower() != "application/json"
            or echoed_client_request_id != authorization.client_request_id
            or type(provider_request_id) is not str
            or UUID_RE.fullmatch(provider_request_id) is None
        ):
            _fail("job_start_response.invalid")
        instant = _exact_time(observed_at, "job_start_response.invalid")
        if not authorization.authorized_at <= instant < authorization.launch_expires_at:
            _fail("job_start_response.expired")
        body = _parse(response_body, maximum=1_048_576, code="job_start_response.invalid")
        if (
            set(body) != {"id", "name"}
            or type(body["id"]) is not str
            or len(body["id"]) > 2048
            or type(body["name"]) is not str
            or EXECUTION_NAME_RE.fullmatch(body["name"]) is None
            or body["id"] != f"{authorization.selected_job_resource_id}/executions/{body['name']}"
        ):
            _fail("job_start_response.execution_identity")
        return _sealed(cls, _START_RESPONSE_SEAL, {
            "action": authorization.action,
            "selected_job_resource_id": authorization.selected_job_resource_id,
            "launch_record_uri": authorization.launch_record_uri,
            "launch_record_sha256": authorization.launch_record_sha256,
            "execution_resource_id": body["id"],
            "execution_name": body["name"],
            "response_body_sha256": _digest(response_body),
            "client_request_id": echoed_client_request_id,
            "provider_request_id": provider_request_id,
            "oidc_jti_sha256": authorization.oidc_jti_sha256,
            "oidc_replay_receipt_sha256": authorization.oidc_replay_receipt_sha256,
            "observed_at": instant,
        })
@dataclass(frozen=True, slots=True, init=False)
class AuthenticatedExecutionObservation:
    action: str
    execution_resource_id: str
    observed_at: datetime
    body_sha256: str
    template_sha256: str
    _seal: object = dataclass_field(repr=False, compare=False)
    def __init__(self) -> None:
        _fail("execution_observation.unsealed")
def validate_execution_observation(policy: Any, *, action: str, response: AuthenticatedArmGetResponse) -> AuthenticatedExecutionObservation:
    document = validate_launch_binding_policy(policy)
    if action not in JOB_KINDS or type(response) is not AuthenticatedArmGetResponse or not response._is_authenticated():
        _fail("execution_observation.authentication")
    job_id = document["runtime"]["jobs"][action]["resource_id"]
    if len(response.resource_id) > 2048 or not response.resource_id.startswith(f"{job_id}/executions/") or "/" in response.resource_id.removeprefix(f"{job_id}/executions/") or EXECUTION_NAME_RE.fullmatch(response.resource_id.rsplit("/", 1)[-1]) is None:
        _fail("execution_observation.identity")
    body = _parse(response.body, maximum=document["launch_binding"]["job_configuration_observation"]["maximum_bytes"], code="execution_observation.invalid")
    if set(body) - {"id", "name", "type", "properties", "systemData"} or not {"name", "properties"} <= set(body) or body["name"] != response.resource_id.rsplit("/", 1)[-1] or ("id" in body and body["id"] != response.resource_id) or ("type" in body and body["type"] != "Microsoft.App/jobs/executions"):
        _fail("execution_observation.identity")
    properties = body["properties"]
    if type(properties) is not dict or "template" not in properties or set(properties) - {"template"} - EXECUTION_COMPUTED:
        _fail("execution_observation.closed_world")
    template = _execution_template_projection(properties["template"], job_template=False, code="execution_observation.template")
    return _sealed(AuthenticatedExecutionObservation, _EXECUTION_OBSERVATION_SEAL, {
        "action": action,
        "execution_resource_id": response.resource_id,
        "observed_at": response.observed_at,
        "body_sha256": _digest(response.body),
        "template_sha256": _digest(_canonical(template)),
    })
def _execution_receipt_uris(policy: dict[str, Any], action: str, execution_name: str) -> tuple[str, str]:
    if action not in JOB_KINDS or type(execution_name) is not str or EXECUTION_NAME_RE.fullmatch(execution_name) is None:
        _fail("launch_receipt.route")
    mailbox = policy["launch_binding"]["mailbox"]
    handoff = mailbox["execution_receipt_handoff"]
    base = f"https://{mailbox['account_host']}/{mailbox['container_names'][action]}"
    receipt_path = handoff["receipt_path_template"].format(execution_name=execution_name)
    signature_path = handoff["signature_envelope_path_template"].format(execution_name=execution_name)
    return f"{base}/{receipt_path}", f"{base}/{signature_path}"
def serialize_broker_launch_receipt(
    policy: Any,
    *,
    launch_record: ValidatedLaunchRecord,
    provision_observation: AuthenticatedJobConfigurationObservation,
    cleanup_observation: AuthenticatedJobConfigurationObservation,
    start_response: AuthenticatedBrokerStartResponse,
    execution_observation: AuthenticatedExecutionObservation,
) -> bytes:
    document = validate_launch_binding_policy(policy)
    observations = {"provision": provision_observation, "cleanup": cleanup_observation}
    if (
        type(launch_record) is not ValidatedLaunchRecord
        or not launch_record._is_validated()
        or type(start_response) is not AuthenticatedBrokerStartResponse
        or getattr(start_response, "_seal", None) is not _START_RESPONSE_SEAL
        or type(execution_observation) is not AuthenticatedExecutionObservation
        or getattr(execution_observation, "_seal", None) is not _EXECUTION_OBSERVATION_SEAL
    ):
        _fail("launch_receipt.inputs")
    for action, observation in observations.items():
        if type(observation) is not AuthenticatedJobConfigurationObservation or not observation._is_authenticated() or observation.job_kind != action or observation.job_resource_id != document["runtime"]["jobs"][action]["resource_id"]:
            _fail("launch_receipt.inputs")
    selected = launch_record.action
    runtime_reference = dict(provision_observation.runtime_reference)
    if (
        start_response.action != selected
        or start_response.launch_record_uri != launch_record.record_uri
        or start_response.launch_record_sha256 != launch_record.record_sha256
        or execution_observation.action != selected
        or start_response.selected_job_resource_id != observations[selected].job_resource_id
        or start_response.execution_resource_id != execution_observation.execution_resource_id
        or execution_observation.template_sha256 != observations[selected].execution_template_sha256
        or dict(cleanup_observation.runtime_reference) != runtime_reference
        or launch_record.runtime_record_sha256 != runtime_reference["record_sha256"]
        or not launch_record.created_at <= min(item.observed_at for item in observations.values()) <= max(item.observed_at for item in observations.values()) <= start_response.observed_at <= execution_observation.observed_at < launch_record.expires_at
    ):
        _fail("launch_receipt.binding")
    receipt = {
        "schema_version": 1,
        "launch_record_uri": launch_record.record_uri,
        "launch_record_sha256": launch_record.record_sha256,
        "action": selected,
        "selected_job_resource_id": observations[selected].job_resource_id,
        "runtime_record_uri": runtime_reference["record_uri"],
        "runtime_record_sha256": runtime_reference["record_sha256"],
        "runtime_signature_bundle_uri": runtime_reference["signature_bundle_uri"],
        "runtime_signature_bundle_sha256": runtime_reference["signature_bundle_sha256"],
        "broker_oidc_jti_sha256": start_response.oidc_jti_sha256,
        "broker_oidc_replay_receipt_sha256": start_response.oidc_replay_receipt_sha256,
        "provision_job_body_sha256": provision_observation.body_sha256,
        "provision_job_projection_sha256": provision_observation.projection_sha256,
        "provision_job_execution_template_sha256": provision_observation.execution_template_sha256,
        "provision_job_image_reference": provision_observation.image_reference,
        "cleanup_job_body_sha256": cleanup_observation.body_sha256,
        "cleanup_job_projection_sha256": cleanup_observation.projection_sha256,
        "cleanup_job_execution_template_sha256": cleanup_observation.execution_template_sha256,
        "cleanup_job_image_reference": cleanup_observation.image_reference,
        "start_response_body_sha256": start_response.response_body_sha256,
        "start_client_request_id": start_response.client_request_id,
        "start_provider_request_id": start_response.provider_request_id,
        "execution_resource_id": execution_observation.execution_resource_id,
        "execution_body_sha256": execution_observation.body_sha256,
        "execution_template_sha256": execution_observation.template_sha256,
        "observed_at": execution_observation.observed_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    contract = document["launch_binding"]["launch_receipt"]
    if list(receipt) != contract["required_fields"]:
        _fail("launch_receipt.contract")
    raw = _canonical(receipt)
    if len(raw) > contract["maximum_bytes"]:
        _fail("launch_receipt.maximum_bytes")
    return raw
@dataclass(frozen=True, slots=True, init=False)
class AuthenticatedBrokerLaunchReceipt:
    receipt_bytes: bytes
    receipt_sha256: str
    action: str
    execution_name: str
    launch_record_uri: str
    launch_record_sha256: str
    runtime_reference: Mapping[str, str]
    receipt_uri: str
    signature_envelope_uri: str
    signature_envelope_sha256: str
    observed_at: datetime
    _seal: object = dataclass_field(repr=False, compare=False)
    def __init__(self) -> None:
        _fail("launch_receipt.unsealed")
    def _is_authenticated(self) -> bool:
        return type(self) is AuthenticatedBrokerLaunchReceipt and getattr(self, "_seal", None) is _AUTHENTICATED_RECEIPT_SEAL
    @classmethod
    def _from_reviewed_platform_metadata_mailbox_signature_adapter(
        cls,
        *,
        policy: Any,
        action: str,
        receipt_uri: str,
        signature_envelope_uri: str,
        retrieval_statuses: tuple[int, ...],
        retrieval_elapsed_seconds: int,
        receipt_bytes: bytes,
        signature_envelope_bytes: bytes,
        signature_verification: receipt_auth.VerifiedBrokerLaunchReceiptSignature,
        observed_at: datetime,
    ) -> AuthenticatedBrokerLaunchReceipt:
        document = validate_launch_binding_policy(policy)
        execution_name = os.environ.get(EXECUTION_NAME_ENV)
        if type(execution_name) is not str:
            _fail("launch_receipt.authentication")
        expected_receipt_uri, expected_signature_uri = _execution_receipt_uris(document, action, execution_name)
        signing = document["launch_binding"]["receipt_signing"]
        retrieval = document["launch_binding"]["mailbox"]["execution_receipt_handoff"]["retrieval"]
        contract = document["launch_binding"]["launch_receipt"]
        maximum = contract["maximum_bytes"]
        if (
            cls is not AuthenticatedBrokerLaunchReceipt
            or receipt_uri != expected_receipt_uri
            or signature_envelope_uri != expected_signature_uri
            or type(retrieval_statuses) is not tuple
            or not 1 <= len(retrieval_statuses) <= retrieval["maximum_attempts"]
            or retrieval_statuses[-1] != retrieval["successful_status"]
            or any(status not in retrieval["retryable_statuses_before_success"] for status in retrieval_statuses[:-1])
            or type(retrieval_elapsed_seconds) is not int
            or not 0 <= retrieval_elapsed_seconds <= retrieval["maximum_elapsed_seconds"]
            or type(receipt_bytes) is not bytes
            or not 1 <= len(receipt_bytes) <= maximum
            or type(signature_envelope_bytes) is not bytes
            or not 1 <= len(signature_envelope_bytes) <= signing["signature_envelope"]["maximum_bytes"]
            or type(signature_verification) is not receipt_auth.VerifiedBrokerLaunchReceiptSignature
            or not signature_verification._matches(policy=document, receipt_bytes=receipt_bytes, signature_envelope_bytes=signature_envelope_bytes)
        ):
            _fail("launch_receipt.authentication")
        receipt = _parse(receipt_bytes, maximum=maximum, code="launch_receipt.authentication")
        _mapping(receipt, set(contract["required_fields"]), "launch_receipt.authentication")
        runtime_reference = {field: receipt[f"runtime_{field}"] for field in runtime_binding.RUNTIME_REFERENCE_FIELDS}
        launch_sha = receipt["launch_record_sha256"]
        mailbox = document["launch_binding"]["mailbox"]
        launch_uri = f"https://{mailbox['account_host']}/{mailbox['container_names'][action]}/{str(launch_sha)[7:]}.json"
        if (
            receipt_bytes != _canonical(receipt)
            or type(receipt["schema_version"]) is not int
            or receipt["schema_version"] != 1
            or receipt["action"] != action
            or receipt["selected_job_resource_id"] != document["runtime"]["jobs"][action]["resource_id"]
            or receipt["execution_resource_id"] != f"{document['runtime']['jobs'][action]['resource_id']}/executions/{execution_name}"
            or type(launch_sha) is not str
            or DIGEST_RE.fullmatch(launch_sha) is None
            or receipt["launch_record_uri"] != launch_uri
        ):
            _fail("launch_receipt.authentication")
        for field, value in runtime_reference.items():
            runtime_binding._pin(value, "https_uri" if field.endswith("uri") else "digest", "launch_receipt.authentication")
            if runtime_binding.PLACEHOLDER_RE.fullmatch(value):
                _fail("launch_receipt.authentication")
        return _sealed(cls, _AUTHENTICATED_RECEIPT_SEAL, {
            "receipt_bytes": bytes(receipt_bytes),
            "receipt_sha256": signature_verification.receipt_sha256,
            "action": action,
            "execution_name": execution_name,
            "launch_record_uri": receipt["launch_record_uri"],
            "launch_record_sha256": launch_sha,
            "runtime_reference": runtime_reference,
            "receipt_uri": receipt_uri,
            "signature_envelope_uri": signature_envelope_uri,
            "signature_envelope_sha256": _digest(signature_envelope_bytes),
            "observed_at": _exact_time(observed_at, "launch_receipt.authentication"),
        })

@dataclass(frozen=True, slots=True, init=False)
class ValidatedReceivedLaunchReceipt:
    receipt_sha256: str
    action: str
    launch_record_sha256: str
    runtime_record_sha256: str
    execution_resource_id: str
    execution_template_sha256: str
    observed_at: datetime
    validated_at: datetime
    _seal: object = dataclass_field(repr=False, compare=False)
    def __init__(self) -> None:
        _fail("launch_receipt.unsealed")
    def _is_validated(self) -> bool:
        return type(self) is ValidatedReceivedLaunchReceipt and getattr(self, "_seal", None) is _VALIDATED_RECEIPT_SEAL

def validate_received_launch_receipt(
    policy: Any,
    *,
    authenticated_receipt: AuthenticatedBrokerLaunchReceipt,
    launch_record: ValidatedLaunchRecord,
    runtime_record: runtime_binding.ValidatedExternalRuntimeBinding,
    validation_now: datetime,
) -> ValidatedReceivedLaunchReceipt:
    document = validate_launch_binding_policy(policy)
    if (
        type(authenticated_receipt) is not AuthenticatedBrokerLaunchReceipt
        or getattr(authenticated_receipt, "_seal", None) is not _AUTHENTICATED_RECEIPT_SEAL
        or type(launch_record) is not ValidatedLaunchRecord
        or not launch_record._is_validated()
        or type(runtime_record) is not runtime_binding.ValidatedExternalRuntimeBinding
        or not runtime_record._is_validated()
        or authenticated_receipt.receipt_sha256 != _digest(authenticated_receipt.receipt_bytes)
        or launch_record.record_sha256 != _digest(launch_record.record_bytes)
        or runtime_record.record_sha256 != _digest(runtime_record.record_bytes)
    ):
        _fail("launch_receipt.inputs")
    contract = document["launch_binding"]["launch_receipt"]
    receipt = _parse(authenticated_receipt.receipt_bytes, maximum=contract["maximum_bytes"], code="launch_receipt.invalid")
    _mapping(receipt, set(contract["required_fields"]), "launch_receipt.closed_world")
    selected = launch_record.action
    execution_id = f"{document['runtime']['jobs'][selected]['resource_id']}/executions/{authenticated_receipt.execution_name}"
    digest_fields = (
        "launch_record_sha256",
        "runtime_record_sha256",
        "runtime_signature_bundle_sha256",
        "broker_oidc_jti_sha256",
        "broker_oidc_replay_receipt_sha256",
        "provision_job_body_sha256",
        "provision_job_projection_sha256",
        "provision_job_execution_template_sha256",
        "cleanup_job_body_sha256",
        "cleanup_job_projection_sha256",
        "cleanup_job_execution_template_sha256",
        "start_response_body_sha256",
        "execution_body_sha256",
        "execution_template_sha256",
    )
    if (
        authenticated_receipt.receipt_bytes != _canonical(receipt)
        or type(receipt["schema_version"]) is not int
        or receipt["schema_version"] != 1
        or authenticated_receipt.action != selected
        or receipt["action"] != selected
        or receipt["launch_record_uri"] != launch_record.record_uri
        or receipt["launch_record_sha256"] != launch_record.record_sha256
        or launch_record.runtime_record_sha256 != runtime_record.record_sha256
        or receipt["selected_job_resource_id"] != document["runtime"]["jobs"][selected]["resource_id"]
        or receipt["runtime_record_uri"] != runtime_record.record_uri
        or receipt["runtime_record_sha256"] != runtime_record.record_sha256
        or receipt["runtime_signature_bundle_uri"] != runtime_record.signature_bundle_uri
        or receipt["runtime_signature_bundle_sha256"] != runtime_record.signature_bundle_sha256
        or receipt["provision_job_projection_sha256"] != document["runtime"]["jobs"]["provision"]["configuration_template_sha256"]
        or receipt["cleanup_job_projection_sha256"] != document["runtime"]["jobs"]["cleanup"]["configuration_template_sha256"]
        or receipt["provision_job_image_reference"] != runtime_record.record["provision_job_image_reference"]
        or receipt["cleanup_job_image_reference"] != runtime_record.record["cleanup_job_image_reference"]
        or receipt["execution_template_sha256"] != receipt[f"{selected}_job_execution_template_sha256"]
        or receipt["execution_resource_id"] != execution_id
        or receipt["start_client_request_id"] != _client_request_id(launch_record.record_sha256)
        or type(receipt["start_provider_request_id"]) is not str
        or UUID_RE.fullmatch(receipt["start_provider_request_id"]) is None
        or any(type(receipt[name]) is not str or DIGEST_RE.fullmatch(receipt[name]) is None for name in digest_fields)
    ):
        _fail("launch_receipt.binding")
    receipt_time = _time(receipt["observed_at"], "launch_receipt.time")
    instant = _exact_time(validation_now, "launch_receipt.time")
    if not (launch_record.created_at <= receipt_time <= authenticated_receipt.observed_at <= instant < launch_record.expires_at):
        _fail("launch_receipt.expired")
    return _sealed(ValidatedReceivedLaunchReceipt, _VALIDATED_RECEIPT_SEAL, {
        "receipt_sha256": authenticated_receipt.receipt_sha256,
        "action": selected,
        "launch_record_sha256": launch_record.record_sha256,
        "runtime_record_sha256": runtime_record.record_sha256,
        "execution_resource_id": execution_id,
        "execution_template_sha256": receipt["execution_template_sha256"],
        "observed_at": receipt_time,
        "validated_at": instant,
    })
def main() -> int:
    try:
        policy = validate_launch_binding_policy(runtime_binding.load_policy())
    except (LaunchBindingFailure, runtime_binding.RuntimeBindingFailure) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"{policy['policy_id']} launch binding valid (dormant)")
    return 0
if __name__ == "__main__":
    raise SystemExit(main())
# fmt: on
