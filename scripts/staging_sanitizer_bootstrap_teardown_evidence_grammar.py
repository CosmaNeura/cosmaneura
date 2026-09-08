#!/usr/bin/env python3
"""Closed-world grammar for reviewed bootstrap-identity teardown evidence."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import stat
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, NoReturn

try:
    from scripts import staging_offline_plan_runtime as offline
except ModuleNotFoundError:  # Direct `python scripts/...` compatibility.
    import staging_offline_plan_runtime as offline


EVIDENCE_FILE = "post-apply-verification-v3.json"
STATE_EVIDENCE_FILE = "post-apply-isolated.tfstate"
REVIEW_REQUEST_FILE = "post-apply-review-request-v4.json"
REVIEW_RESPONSE_FILE = "post-apply-review-response-v4.json"
EVIDENCE_FILES = (
    EVIDENCE_FILE,
    STATE_EVIDENCE_FILE,
    REVIEW_REQUEST_FILE,
    REVIEW_RESPONSE_FILE,
)
REVIEW_BINDING_DOMAIN = (
    "cosmaneura:staging:sanitizer-bootstrap-identity-post-apply-review:v4"
)
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
HEX_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
RESPONSE_FIELDS = {
    "background",
    "completed_at",
    "content_filters",
    "created_at",
    "error",
    "frequency_penalty",
    "id",
    "incomplete_details",
    "instructions",
    "max_output_tokens",
    "max_tool_calls",
    "metadata",
    "model",
    "moderation",
    "object",
    "output",
    "parallel_tool_calls",
    "presence_penalty",
    "previous_response_id",
    "prompt_cache_key",
    "prompt_cache_retention",
    "reasoning",
    "safety_identifier",
    "service_tier",
    "status",
    "store",
    "temperature",
    "text",
    "tool_choice",
    "tool_usage",
    "tools",
    "top_logprobs",
    "top_p",
    "truncation",
    "usage",
    "user",
}


class EvidenceGrammarFailure(RuntimeError):
    """Bounded refusal for invalid teardown evidence grammar."""


def _fail(code: str) -> NoReturn:
    raise EvidenceGrammarFailure(code) from None


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeEncodeError, RecursionError):
        _fail("bootstrap_identity_teardown_evidence.json_invalid")


def _digest(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def _pretty_file_digest(value: Any) -> str:
    raw = (json.dumps(value, ensure_ascii=True, indent=2) + "\n").encode("ascii")
    return hashlib.sha256(raw).hexdigest()


def _mapping(value: Any, fields: set[str], code: str) -> Mapping[str, Any]:
    if type(value) is not dict or set(value) != fields:
        _fail(code)
    return value


def _integer(value: Any, expected: int, code: str) -> None:
    if type(value) is not int or value != expected:
        _fail(code)


def _same_typed(value: Any, expected: Any) -> bool:
    if type(value) is not type(expected):
        return False
    if type(expected) is dict:
        return set(value) == set(expected) and all(
            _same_typed(value[name], item) for name, item in expected.items()
        )
    if type(expected) is list:
        return len(value) == len(expected) and all(
            _same_typed(item, expected[index]) for index, item in enumerate(value)
        )
    return value == expected


def _exact_typed(value: Any, expected: Any, code: str) -> None:
    if not _same_typed(value, expected):
        _fail(code)


def _stat_identity(value: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _matches(pattern: re.Pattern[str], value: Any) -> bool:
    return type(value) is str and pattern.fullmatch(value) is not None


def _parse_utc(value: Any, code: str) -> datetime:
    if type(value) is not str:
        _fail(code)
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        _fail(code)
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        _fail(code)
    return parsed


def _read_evidence_files(
    directory: Path, policy: Mapping[str, Any]
) -> dict[str, bytes]:
    code = "bootstrap_identity_teardown_evidence.directory_invalid"
    if not isinstance(directory, Path) or not directory.is_absolute():
        _fail(code)
    descriptor = -1
    open_files: list[int] = []
    try:
        descriptor = offline.open_private_directory(
            directory, label="bootstrap identity teardown evidence directory"
        )
        directory_before = os.fstat(descriptor)
        entries = set(os.listdir(descriptor))
        if entries != set(EVIDENCE_FILES):
            _fail(code)
        result: dict[str, bytes] = {}
        metadata: dict[str, os.stat_result] = {}
        flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
        )
        maximum_bytes = policy["evidence"]["maximum_file_bytes"]
        for name in EVIDENCE_FILES:
            file_descriptor = os.open(name, flags, dir_fd=descriptor)
            open_files.append(file_descriptor)
            before = os.fstat(file_descriptor)
            linked = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_uid != os.geteuid()
                or before.st_nlink != 1
                or stat.S_IMODE(before.st_mode) != 0o400
                or not 0 < before.st_size <= maximum_bytes
                or _stat_identity(before) != _stat_identity(linked)
            ):
                _fail("bootstrap_identity_teardown_evidence.file_invalid")
            value = bytearray()
            while len(value) < before.st_size:
                chunk = os.read(
                    file_descriptor,
                    min(1024 * 1024, before.st_size - len(value)),
                )
                if not chunk:
                    break
                value.extend(chunk)
            after = os.fstat(file_descriptor)
            if len(value) != before.st_size or _stat_identity(before) != _stat_identity(
                after
            ):
                _fail("bootstrap_identity_teardown_evidence.file_invalid")
            result[name] = bytes(value)
            metadata[name] = after
        if set(os.listdir(descriptor)) != set(EVIDENCE_FILES):
            _fail(code)
        for name, before in metadata.items():
            linked = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            if _stat_identity(before) != _stat_identity(linked):
                _fail("bootstrap_identity_teardown_evidence.file_invalid")
        directory_after = os.fstat(descriptor)
        linked_directory = os.stat(directory, follow_symlinks=False)
        if (
            _stat_identity(directory_before) != _stat_identity(directory_after)
            or _stat_identity(directory_after) != _stat_identity(linked_directory)
            or not stat.S_ISDIR(linked_directory.st_mode)
        ):
            _fail(code)
    except EvidenceGrammarFailure:
        raise
    except Exception:
        _fail(code)
    finally:
        for file_descriptor in open_files:
            try:
                os.close(file_descriptor)
            except OSError:
                pass
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
    for name, raw in result.items():
        if not hmac.compare_digest(_digest(raw), policy["evidence"]["files"][name]):
            _fail("bootstrap_identity_teardown_evidence.file_invalid")
    return result


def _serialized_contains(value: Any, needles: tuple[str, ...]) -> bool:
    serialized = _canonical(value).decode("ascii").casefold()
    return any(needle.casefold() in serialized for needle in needles)


def _resource_segments(
    value: Any,
    *,
    subscription_id: str,
    provider: str,
    resource_type: str,
    code: str,
) -> list[str]:
    if type(value) is not str or not value.isascii() or value.strip() != value:
        _fail(code)
    parts = value.split("/")
    if (
        len(parts) != 9
        or parts[0] != ""
        or parts[1].lower() != "subscriptions"
        or parts[2].lower() != subscription_id.lower()
        or parts[3].lower() != "resourcegroups"
        or not parts[4]
        or parts[5].lower() != "providers"
        or parts[6].lower() != provider.lower()
        or parts[7].lower() != resource_type.lower()
        or not parts[8]
    ):
        _fail(code)
    return parts


def _validate_job_inventory(jobs: Any, *, subscription_id: str, code: str) -> None:
    if type(jobs) is not list:
        _fail(code)
    job_ids: list[str] = []
    for value in jobs:
        job = _mapping(value, {"id", "identity"}, code)
        if type(job["id"]) is not str:
            _fail(code)
        job_ids.append(job["id"])
        _resource_segments(
            job["id"],
            subscription_id=subscription_id,
            provider="Microsoft.App",
            resource_type="jobs",
            code=code,
        )
        identity = job["identity"]
        if type(identity) is not dict:
            _fail(code)
        identity_type = identity.get("type")
        if type(identity_type) is not str or identity_type not in {
            "None",
            "UserAssigned",
        }:
            _fail(code)
        if identity_type == "None":
            if set(identity) != {"type"}:
                _fail(code)
            continue
        identity = _mapping(identity, {"type", "userAssignedIdentities"}, code)
        assignments = identity["userAssignedIdentities"]
        if type(assignments) is not dict or not assignments:
            _fail(code)
        for identity_id, record in assignments.items():
            _resource_segments(
                identity_id,
                subscription_id=subscription_id,
                provider="Microsoft.ManagedIdentity",
                resource_type="userAssignedIdentities",
                code=code,
            )
            identifiers = _mapping(record, {"clientId", "principalId"}, code)
            if any(
                type(identifiers[name]) is not str
                or not _matches(UUID_RE, identifiers[name])
                for name in ("clientId", "principalId")
            ):
                _fail(code)
    if len(set(map(str.lower, job_ids))) != len(job_ids) or job_ids != sorted(
        job_ids, key=str.lower
    ):
        _fail(code)


def _validate_postgres_inventory(
    servers: Any,
    admins: Any,
    *,
    subscription_id: str,
    tenant_id: str,
    enabled_server_id: str,
    code: str,
) -> None:
    if type(servers) is not list or type(admins) is not list:
        _fail(code)
    server_ids: list[str] = []
    for value in servers:
        server = _mapping(value, {"authConfig", "id", "state"}, code)
        _resource_segments(
            server["id"],
            subscription_id=subscription_id,
            provider="Microsoft.DBforPostgreSQL",
            resource_type="flexibleServers",
            code=code,
        )
        server_ids.append(server["id"])
        auth = _mapping(
            server["authConfig"],
            {"activeDirectoryAuth", "passwordAuth", "tenantId"},
            code,
        )
        if server["state"] != "Ready":
            _fail(code)
        if server["id"].lower() == enabled_server_id.lower():
            if auth != {
                "activeDirectoryAuth": "Enabled",
                "passwordAuth": "Disabled",
                "tenantId": tenant_id,
            }:
                _fail(code)
        elif auth != {
            "activeDirectoryAuth": "Disabled",
            "passwordAuth": "Enabled",
            "tenantId": None,
        }:
            _fail(code)
    if len(set(map(str.lower, server_ids))) != len(server_ids) or server_ids != sorted(
        server_ids, key=str.lower
    ):
        _fail(code)
    administrator_ids: list[str] = []
    prefix = f"{enabled_server_id}/administrators/"
    for value in admins:
        admin = _mapping(
            value,
            {"id", "name", "principalName", "principalType", "tenantId"},
            code,
        )
        if (
            type(admin["id"]) is not str
            or not admin["id"].isascii()
            or not admin["id"].lower().startswith(prefix.lower())
            or not _matches(UUID_RE, admin["id"][len(prefix) :])
            or type(admin["name"]) is not str
            or not admin["name"]
            or admin["principalName"] != admin["name"]
            or admin["principalType"] != "ServicePrincipal"
            or admin["tenantId"] != tenant_id
        ):
            _fail(code)
        administrator_ids.append(admin["id"])
    if len(set(map(str.lower, administrator_ids))) != len(
        administrator_ids
    ) or administrator_ids != sorted(administrator_ids, key=str.lower):
        _fail(code)


def _validate_state(value: Any, policy: Mapping[str, Any]) -> None:
    code = "bootstrap_identity_teardown_evidence.state_invalid"
    state = _mapping(
        value,
        {
            "version",
            "terraform_version",
            "serial",
            "lineage",
            "outputs",
            "resources",
            "check_results",
        },
        code,
    )
    expected = policy["state"]
    resources = state["resources"]
    outputs = state["outputs"]
    checks = state["check_results"]
    target = policy["target"]
    cleanup = policy["cleanup"]
    _integer(state["version"], 4, code)
    _integer(state["serial"], expected["post_apply_serial"], code)
    if (
        state["terraform_version"] != expected["opentofu_version"]
        or state["lineage"] != expected["lineage"]
        or type(resources) is not list
        or len(resources) != 1
        or type(outputs) is not dict
        or list(outputs) != expected["exact_output_keys"]
        or type(checks) is not list
        or len(checks) != len(expected["exact_passing_check_addresses"])
    ):
        _fail(code)
    resource = _mapping(
        resources[0], {"mode", "type", "name", "provider", "instances"}, code
    )
    if (
        f"{resource['mode']}.{resource['type']}.{resource['name']}"
        != expected["exact_resource_addresses"][0]
        or resource["provider"] != 'provider["terraform.io/builtin/terraform"]'
        or type(resource["instances"]) is not list
        or len(resource["instances"]) != 1
    ):
        _fail(code)
    instance = _mapping(
        resource["instances"][0],
        {"index_key", "schema_version", "attributes", "sensitive_attributes"},
        code,
    )
    _integer(instance["index_key"], 0, code)
    _integer(instance["schema_version"], 0, code)
    attributes = _mapping(
        instance["attributes"], {"id", "input", "output", "triggers_replace"}, code
    )
    if (
        instance["sensitive_attributes"] != []
        or type(attributes["id"]) is not str
        or not _matches(UUID_RE, attributes["id"])
    ):
        _fail(code)
    guard = {
        "cleanup_not_after": cleanup["cleanup_not_after"],
        "identity_resource_id": target["identity_resource_id"],
        "mode": "teardown-only",
        "rehearsal_id": target["rehearsal_id"],
        "teardown_authorized": True,
        "teardown_evidence_sha256": expected["teardown_evidence_sha256"],
    }
    guard_type = [
        "object",
        {
            "cleanup_not_after": "string",
            "identity_resource_id": "string",
            "mode": "string",
            "rehearsal_id": "string",
            "teardown_authorized": "bool",
            "teardown_evidence_sha256": "string",
        },
    ]
    trigger_type = [
        "object",
        {
            "cleanup_not_after": "string",
            "lifetime_hours": "number",
            "rehearsal_id": "string",
        },
    ]
    input_value = _mapping(attributes["input"], {"value", "type"}, code)
    output_value = _mapping(attributes["output"], {"value", "type"}, code)
    triggers = _mapping(attributes["triggers_replace"], {"value", "type"}, code)
    if (
        not _same_typed(input_value["value"], guard)
        or not _same_typed(input_value["type"], guard_type)
        or not _same_typed(output_value["value"], guard)
        or not _same_typed(output_value["type"], guard_type)
        or not _same_typed(
            triggers["value"],
            {
                "cleanup_not_after": cleanup["cleanup_not_after"],
                "lifetime_hours": 6,
                "rehearsal_id": target["rehearsal_id"],
            },
        )
        or not _same_typed(triggers["type"], trigger_type)
    ):
        _fail(code)
    teardown_record = _mapping(
        outputs["sanitizer_bootstrap_identity_teardown"], {"value", "type"}, code
    )
    boundary_record = _mapping(outputs["state_boundary"], {"value", "type"}, code)
    expected_teardown = {
        "apply_authorized": False,
        "cleanup_not_after": cleanup["cleanup_not_after"],
        "founder_apply_required": True,
        "identity_resource_id": target["identity_resource_id"],
        "mode": "teardown-only",
        "rehearsal_id": target["rehearsal_id"],
        "teardown_evidence_sha256": expected["teardown_evidence_sha256"],
    }
    expected_boundary = {
        "container": "cosmaneura-tfstate",
        "key": expected["backend_key"],
        "resource_group": "cosmaneura-tfstate",
        "storage_account": "cosmaneuratfstate",
    }
    if (
        not _same_typed(teardown_record["value"], expected_teardown)
        or not _same_typed(
            teardown_record["type"],
            [
                "object",
                {
                    "apply_authorized": "bool",
                    "cleanup_not_after": "string",
                    "founder_apply_required": "bool",
                    "identity_resource_id": "string",
                    "mode": "string",
                    "rehearsal_id": "string",
                    "teardown_evidence_sha256": "string",
                },
            ],
        )
        or not _same_typed(boundary_record["value"], expected_boundary)
        or not _same_typed(
            boundary_record["type"],
            [
                "object",
                {
                    "container": "string",
                    "key": "string",
                    "resource_group": "string",
                    "storage_account": "string",
                },
            ],
        )
    ):
        _fail(code)
    expected_checks = expected["exact_passing_check_addresses"]
    for index, item in enumerate(checks):
        check = _mapping(
            item, {"object_kind", "config_addr", "status", "objects"}, code
        )
        address = expected_checks[index]
        expected_kind = "resource" if address.startswith("terraform_data.") else "var"
        expected_object_address = (
            f"{address}[0]" if expected_kind == "resource" else address
        )
        if (
            check["object_kind"] != expected_kind
            or check["config_addr"] != address
            or check["status"] != "pass"
            or type(check["objects"]) is not list
            or len(check["objects"]) != 1
        ):
            _fail(code)
        check_object = _mapping(check["objects"][0], {"object_addr", "status"}, code)
        if (
            check_object["object_addr"] != expected_object_address
            or check_object["status"] != "pass"
        ):
            _fail(code)


def _validate_evidence(
    value: Any, policy: Mapping[str, Any], raw_files: Mapping[str, bytes]
) -> None:
    code = "bootstrap_identity_teardown_evidence.evidence_invalid"
    envelope = _mapping(
        value,
        {
            "schema_version",
            "policy_id",
            "prior_envelope_sha256",
            "prior_envelope",
            "remediation",
            "pass",
        },
        code,
    )
    prior = _mapping(
        envelope["prior_envelope"],
        {
            "schema_version",
            "policy_id",
            "target",
            "source_commit",
            "applied_plan_sha256",
            "cleanup_not_after",
            "capture_script_sha256",
            "capture_script",
            "query_contract",
            "evidence_hashes",
            "verification",
            "raw_evidence",
        },
        code,
    )
    verification = _mapping(
        prior["verification"],
        {
            "schema_version",
            "policy_id",
            "source_commit",
            "applied_plan_sha256",
            "cleanup_not_after",
            "evidence_started_at",
            "evidence_completed_at",
            "account",
            "identity_count",
            "role_assignment_count",
            "job_count",
            "job_attachment_matches",
            "postgres_server_count",
            "postgres_admin_matches",
            "state_lineage",
            "state_serial",
            "state_outputs",
            "state_resources",
            "pass",
        },
        code,
    )
    raw = _mapping(
        prior["raw_evidence"],
        {"identities", "role_assignments", "jobs", "postgres_admins", "isolated_state"},
        code,
    )
    remediation = _mapping(
        envelope["remediation"],
        {"inherited_rbac", "postgres_server_inventory"},
        code,
    )
    inherited = _mapping(
        remediation["inherited_rbac"],
        {
            "command",
            "started_at",
            "completed_at",
            "evidence_sha256",
            "assignments",
            "count",
        },
        code,
    )
    postgres = _mapping(
        remediation["postgres_server_inventory"],
        {
            "query",
            "evidence_sha256",
            "servers",
            "count",
            "entra_enabled_count",
            "entra_enabled_server_ids",
        },
        code,
    )
    target = policy["target"]
    cleanup = policy["cleanup"]
    state_policy = policy["state"]
    authority = policy["authority"]
    source = policy["source"]
    observed_target = {
        name: target[name]
        for name in (
            "subscription_id",
            "tenant_id",
            "resource_group",
            "identity_name",
            "identity_resource_id",
            "client_id",
            "principal_id",
        )
    }
    needles = (
        target["identity_name"],
        target["client_id"],
        target["principal_id"],
        target["identity_resource_id"],
    )
    started = _parse_utc(verification["evidence_started_at"], code)
    completed = _parse_utc(verification["evidence_completed_at"], code)
    deadline = _parse_utc(cleanup["cleanup_not_after"], code)
    rbac_started = _parse_utc(inherited["started_at"], code)
    rbac_completed = _parse_utc(inherited["completed_at"], code)
    jobs = raw["jobs"]
    admins = raw["postgres_admins"]
    servers = postgres["servers"]
    account = verification["account"]
    expected_rbac_command = (
        "az role assignment list --include-inherited --scope /subscriptions/"
        f"{target['subscription_id']} --subscription {target['subscription_id']} "
        f"--query [exact principalId {target['principal_id']}]"
    )
    enabled = [
        item
        for item in servers
        if type(item) is dict
        and type(item.get("authConfig")) is dict
        and item["authConfig"].get("activeDirectoryAuth") == "Enabled"
    ]
    expected_queries = {
        "identity": (
            "az identity list scoped to exact subscription and resource group, "
            "filtered by exact identity name"
        ),
        "roles": (
            "az role assignment list --all scoped to exact subscription, filtered "
            "by exact immutable principalId"
        ),
        "jobs": (
            "az containerapp job list scoped to exact subscription; verifier searches "
            "serialized identity blocks for exact client ID, principal ID, and identity name"
        ),
        "postgres": (
            "az postgres flexible-server list scoped to exact subscription plus exact "
            "Entra-admin list for the only Entra-enabled server; verifier searches for "
            "exact client ID, principal ID, and identity name"
        ),
        "state": (
            "OpenTofu state pull using the exact isolated backend execution directory"
        ),
    }
    evidence_hashes = _mapping(
        prior["evidence_hashes"],
        {
            "v1_verification",
            "identities",
            "role_assignments",
            "jobs",
            "postgres_admins",
            "isolated_state",
        },
        code,
    )
    if any(not _matches(HEX_DIGEST_RE, item) for item in evidence_hashes.values()):
        _fail(code)
    _integer(envelope["schema_version"], 3, code)
    _integer(prior["schema_version"], 2, code)
    _integer(verification["schema_version"], 1, code)
    for name, expected_count in (
        ("identity_count", authority["identity_count"]),
        (
            "role_assignment_count",
            authority["direct_or_inherited_role_assignment_count"],
        ),
        ("job_count", authority["subscription_container_apps_job_count"]),
        (
            "job_attachment_matches",
            authority["bootstrap_identity_job_attachment_count"],
        ),
        ("postgres_server_count", authority["subscription_postgres_server_count"]),
        (
            "postgres_admin_matches",
            authority["bootstrap_identity_postgres_administrator_count"],
        ),
        ("state_serial", state_policy["post_apply_serial"]),
    ):
        _integer(verification[name], expected_count, code)
    _integer(inherited["count"], 0, code)
    _integer(postgres["count"], authority["subscription_postgres_server_count"], code)
    _integer(
        postgres["entra_enabled_count"],
        authority["entra_enabled_postgres_server_count"],
        code,
    )
    expected_account = {
        "environmentName": "AzureCloud",
        "id": target["subscription_id"],
        "isDefault": True,
        "state": "Enabled",
        "tenantId": target["tenant_id"],
    }
    _mapping(account, set(expected_account), code)
    _exact_typed(account, expected_account, code)
    if type(verification["state_resources"]) is not list:
        _fail(code)
    for item in verification["state_resources"]:
        record = _mapping(item, {"mode", "type", "name", "provider"}, code)
        if any(type(record[name]) is not str for name in record):
            _fail(code)
    _validate_job_inventory(jobs, subscription_id=target["subscription_id"], code=code)
    _validate_postgres_inventory(
        servers,
        admins,
        subscription_id=target["subscription_id"],
        tenant_id=target["tenant_id"],
        enabled_server_id=authority["entra_enabled_postgres_server_id"],
        code=code,
    )
    try:
        state_from_file = offline.load_json_bytes(
            raw_files[STATE_EVIDENCE_FILE], label=STATE_EVIDENCE_FILE
        )
    except Exception:
        _fail(code)
    _validate_state(raw["isolated_state"], policy)
    expected_verification_resources = [
        {name: resource[name] for name in ("mode", "type", "name", "provider")}
        for resource in raw["isolated_state"]["resources"]
    ]
    component_values = {
        "v1_verification": verification,
        "identities": raw["identities"],
        "role_assignments": raw["role_assignments"],
        "jobs": jobs,
        "postgres_admins": admins,
    }
    component_hashes_valid = all(
        hmac.compare_digest(_pretty_file_digest(item), evidence_hashes.get(name, ""))
        for name, item in component_values.items()
    )
    if (
        envelope["policy_id"]
        != "cosmaneura-staging-sanitizer-bootstrap-identity-post-apply-v3"
        or envelope["pass"] is not True
        or not _matches(HEX_DIGEST_RE, envelope["prior_envelope_sha256"])
        or envelope["prior_envelope_sha256"] != _pretty_file_digest(prior)
        or prior["policy_id"]
        != "cosmaneura-staging-sanitizer-bootstrap-identity-post-apply-v2"
        or prior["target"] != observed_target
        or prior["source_commit"] != source["source_commit"]
        or f"sha256:{prior['applied_plan_sha256']}" != source["applied_plan_sha256"]
        or prior["cleanup_not_after"] != cleanup["cleanup_not_after"]
        or type(prior["capture_script"]) is not str
        or hashlib.sha256(prior["capture_script"].encode()).hexdigest()
        != prior["capture_script_sha256"]
        or not _matches(HEX_DIGEST_RE, prior["capture_script_sha256"])
        or prior["query_contract"] != expected_queries
        or not component_hashes_valid
        or not hmac.compare_digest(
            hashlib.sha256(raw_files[STATE_EVIDENCE_FILE]).hexdigest(),
            evidence_hashes["isolated_state"],
        )
        or not _same_typed(state_from_file, raw["isolated_state"])
        or verification["policy_id"]
        != "cosmaneura-staging-sanitizer-bootstrap-identity-post-apply-v1"
        or verification["source_commit"] != source["source_commit"]
        or f"sha256:{verification['applied_plan_sha256']}"
        != source["applied_plan_sha256"]
        or verification["cleanup_not_after"] != cleanup["cleanup_not_after"]
        or started.strftime("%Y-%m-%dT%H:%M:%SZ") != cleanup["evidence_started_at"]
        or completed.strftime("%Y-%m-%dT%H:%M:%SZ") != cleanup["evidence_completed_at"]
        or started - deadline != timedelta(seconds=cleanup["started_overrun_seconds"])
        or completed - deadline
        != timedelta(seconds=cleanup["completed_overrun_seconds"])
        or verification["pass"] is not True
        or raw["identities"] != []
        or raw["role_assignments"] != []
        or verification["identity_count"] != authority["identity_count"]
        or verification["role_assignment_count"]
        != authority["direct_or_inherited_role_assignment_count"]
        or len(jobs) != authority["subscription_container_apps_job_count"]
        or verification["job_count"] != len(jobs)
        or verification["job_attachment_matches"]
        != authority["bootstrap_identity_job_attachment_count"]
        or _serialized_contains(jobs, needles)
        or verification["postgres_admin_matches"]
        != authority["bootstrap_identity_postgres_administrator_count"]
        or _serialized_contains(admins, needles)
        or inherited["command"] != expected_rbac_command
        or inherited["evidence_sha256"] != _pretty_file_digest(inherited["assignments"])
        or inherited["assignments"] != []
        or inherited["count"] != authority["direct_or_inherited_role_assignment_count"]
        or not completed <= rbac_started <= rbac_completed
        or postgres["evidence_sha256"] != _pretty_file_digest(servers)
        or postgres["query"]
        != (
            "az postgres flexible-server list scoped to exact subscription "
            f"{target['subscription_id']}"
        )
        or len(servers) != authority["subscription_postgres_server_count"]
        or postgres["count"] != len(servers)
        or postgres["entra_enabled_count"]
        != authority["entra_enabled_postgres_server_count"]
        or len(enabled) != authority["entra_enabled_postgres_server_count"]
        or postgres["entra_enabled_server_ids"]
        != [authority["entra_enabled_postgres_server_id"]]
        or [item["id"] for item in enabled]
        != [authority["entra_enabled_postgres_server_id"]]
        or verification["postgres_server_count"] != len(servers)
        or verification["state_lineage"] != state_policy["lineage"]
        or verification["state_serial"] != state_policy["post_apply_serial"]
        or verification["state_outputs"] != state_policy["exact_output_keys"]
        or not _same_typed(
            verification["state_resources"], expected_verification_resources
        )
    ):
        _fail(code)


def _validate_review_response(
    value: Any,
    policy: Mapping[str, Any],
    *,
    request_binding: Mapping[str, str],
    review_input_length: int,
) -> None:
    code = "bootstrap_identity_teardown_evidence.review_invalid"
    response = _mapping(value, RESPONSE_FIELDS, code)
    review = policy["independent_review"]
    for name in ("created_at", "completed_at"):
        if type(response[name]) is not int or response[name] <= 0:
            _fail(code)
    if response["completed_at"] < response["created_at"]:
        _fail(code)
    metadata = _mapping(
        response["metadata"],
        {"binding_id", "evidence_sha256", "request_input_sha256"},
        code,
    )
    if not _same_typed(metadata, request_binding):
        _fail(code)
    content_filters = response["content_filters"]
    if type(content_filters) is not list or len(content_filters) != 1:
        _fail(code)
    content_filter = _mapping(
        content_filters[0],
        {
            "blocked",
            "source_type",
            "content_filter_raw",
            "content_filter_results",
            "content_filter_offsets",
        },
        code,
    )
    offsets = _mapping(
        content_filter["content_filter_offsets"],
        {"start_offset", "end_offset", "check_offset"},
        code,
    )
    _integer(offsets["start_offset"], 0, code)
    _integer(offsets["check_offset"], 0, code)
    _integer(offsets["end_offset"], review_input_length, code)
    if not _same_typed(
        {
            name: content_filter[name]
            for name in content_filter
            if name != "content_filter_offsets"
        },
        {
            "blocked": False,
            "source_type": "prompt",
            "content_filter_raw": [],
            "content_filter_results": {},
        },
    ):
        _fail(code)
    reasoning = _mapping(
        response["reasoning"], {"context", "effort", "mode", "summary"}, code
    )
    text = _mapping(response["text"], {"format", "verbosity"}, code)
    text_format = _mapping(text["format"], {"type"}, code)
    tool_usage = _mapping(response["tool_usage"], {"image_gen", "web_search"}, code)
    image_usage = _mapping(
        tool_usage["image_gen"],
        {
            "input_tokens",
            "input_tokens_details",
            "output_tokens",
            "output_tokens_details",
            "total_tokens",
        },
        code,
    )
    web_usage = _mapping(tool_usage["web_search"], {"num_requests"}, code)
    for name in ("input_tokens", "output_tokens", "total_tokens"):
        _integer(image_usage[name], 0, code)
    for name in ("input_tokens_details", "output_tokens_details"):
        details = _mapping(image_usage[name], {"image_tokens", "text_tokens"}, code)
        _integer(details["image_tokens"], 0, code)
        _integer(details["text_tokens"], 0, code)
    _integer(web_usage["num_requests"], 0, code)
    usage = _mapping(
        response["usage"],
        {
            "input_tokens",
            "input_tokens_details",
            "output_tokens",
            "output_tokens_details",
            "total_tokens",
        },
        code,
    )
    input_details = _mapping(
        usage["input_tokens_details"], {"cache_write_tokens", "cached_tokens"}, code
    )
    output_details = _mapping(
        usage["output_tokens_details"], {"reasoning_tokens"}, code
    )
    for item in (
        usage["input_tokens"],
        input_details["cache_write_tokens"],
        input_details["cached_tokens"],
        usage["output_tokens"],
        output_details["reasoning_tokens"],
        usage["total_tokens"],
    ):
        if type(item) is not int or item < 0:
            _fail(code)
    if usage["total_tokens"] != usage["input_tokens"] + usage["output_tokens"]:
        _fail(code)
    if (
        input_details["cache_write_tokens"] + input_details["cached_tokens"]
        > usage["input_tokens"]
        or usage["output_tokens"] > review["maximum_output_tokens"]
        or output_details["reasoning_tokens"] > usage["output_tokens"]
    ):
        _fail(code)
    if (
        response["id"] != review["response_id"]
        or response["model"] != review["model"]
        or response["status"] != "completed"
        or response["error"] is not None
        or response["incomplete_details"] is not None
        or response["background"] is not False
        or response["instructions"] is not None
        or type(response["max_output_tokens"]) is not int
        or response["max_output_tokens"] != review["maximum_output_tokens"]
        or response["max_tool_calls"] is not None
        or response["moderation"] is not None
        or response["object"] != "response"
        or response["parallel_tool_calls"] is not True
        or type(response["frequency_penalty"]) is not float
        or type(response["presence_penalty"]) is not float
        or type(response["temperature"]) is not float
        or type(response["top_p"]) is not float
        or response["previous_response_id"] is not None
        or response["prompt_cache_key"] is not None
        or response["prompt_cache_retention"] != "24h"
        or reasoning
        != {
            "context": "all_turns",
            "effort": "high",
            "mode": "standard",
            "summary": None,
        }
        or response["safety_identifier"] is not None
        or response["service_tier"] != "default"
        or response["store"] is not True
        or text_format != {"type": "text"}
        or text["verbosity"] != "medium"
        or response["tool_choice"] != "auto"
        or response["tools"] != []
        or type(response["top_logprobs"]) is not int
        or response["top_logprobs"] != 0
        or response["truncation"] != "disabled"
        or response["user"] is not None
    ):
        _fail(code)
    output = response["output"]
    if type(output) is not list or len(output) < 2:
        _fail(code)
    for item in output[:-1]:
        reasoning_item = _mapping(
            item, {"id", "type", "content", "encrypted_content", "summary"}, code
        )
        if (
            type(reasoning_item["id"]) is not str
            or not reasoning_item["id"].startswith("rs_")
            or reasoning_item["type"] != "reasoning"
            or reasoning_item["content"] != []
            or type(reasoning_item["encrypted_content"]) is not str
            or not reasoning_item["encrypted_content"]
            or reasoning_item["summary"] != []
        ):
            _fail(code)
    message = _mapping(
        output[-1], {"id", "type", "status", "content", "phase", "role"}, code
    )
    content = message["content"]
    if type(content) is not list or len(content) != 1:
        _fail(code)
    block = _mapping(content[0], {"type", "annotations", "logprobs", "text"}, code)
    if (
        type(message["id"]) is not str
        or not message["id"].startswith("msg_")
        or message["type"] != "message"
        or message["status"] != "completed"
        or message["phase"] != "final_answer"
        or message["role"] != "assistant"
        or block["type"] != "output_text"
        or block["annotations"] != []
        or block["logprobs"] != []
        or block["text"] != review["verdict"]
    ):
        _fail(code)


def _validate_review_request(
    value: Any, evidence: Mapping[str, Any], policy: Mapping[str, Any]
) -> tuple[dict[str, str], int]:
    code = "bootstrap_identity_teardown_evidence.review_invalid"
    request = _mapping(
        value, {"model", "reasoning", "metadata", "max_output_tokens", "input"}, code
    )
    reasoning = _mapping(request["reasoning"], {"effort"}, code)
    metadata = _mapping(
        request["metadata"],
        {"binding_id", "evidence_sha256", "request_input_sha256"},
        code,
    )
    evidence_text = json.dumps(evidence, ensure_ascii=True, separators=(",", ":"))
    review_input = request["input"]
    review = policy["independent_review"]
    expected_metadata = {
        "binding_id": review["binding_id"],
        "evidence_sha256": policy["evidence"]["files"][EVIDENCE_FILE],
        "request_input_sha256": review["request_input_sha256"],
    }
    if type(request["model"]) is not str or type(review_input) is not str:
        _fail(code)
    calculated_input_sha256 = _digest(review_input.encode("utf-8"))
    calculated_binding = expected_review_binding(
        evidence_sha256=expected_metadata["evidence_sha256"],
        request_input_sha256=calculated_input_sha256,
        maximum_output_tokens=review["maximum_output_tokens"],
        model=request["model"],
    )
    if (
        request["model"] != review["model"]
        or reasoning["effort"] != "high"
        or type(request["max_output_tokens"]) is not int
        or request["max_output_tokens"] != review["maximum_output_tokens"]
        or type(review_input) is not str
        or not review_input.endswith(f"\n\nEVIDENCE V3\n{evidence_text}")
        or review_input.count(evidence_text) != 1
        or calculated_input_sha256 != review["request_input_sha256"]
        or not hmac.compare_digest(review["binding_id"], calculated_binding)
        or not _same_typed(metadata, expected_metadata)
    ):
        _fail(code)
    return dict(metadata), len(review_input)


def expected_review_binding(
    *,
    evidence_sha256: str,
    request_input_sha256: str,
    maximum_output_tokens: int,
    model: str,
) -> str:
    return _digest(
        _canonical(
            {
                "domain": REVIEW_BINDING_DOMAIN,
                "evidence_sha256": evidence_sha256,
                "maximum_output_tokens": maximum_output_tokens,
                "model": model,
                "reasoning_effort": "high",
                "request_input_sha256": request_input_sha256,
            }
        )
    )


def validate_evidence_directory(
    directory: Path, policy: Mapping[str, Any]
) -> dict[str, bytes]:
    """Read and validate the exact closed-world evidence and review bundle."""

    raw = _read_evidence_files(directory, policy)
    try:
        parsed = {
            name: offline.load_json_bytes(value, label=name)
            for name, value in raw.items()
        }
    except Exception:
        _fail("bootstrap_identity_teardown_evidence.file_invalid")
    _validate_evidence(parsed[EVIDENCE_FILE], policy, raw)
    request_binding, review_input_length = _validate_review_request(
        parsed[REVIEW_REQUEST_FILE], parsed[EVIDENCE_FILE], policy
    )
    _validate_review_response(
        parsed[REVIEW_RESPONSE_FILE],
        policy,
        request_binding=request_binding,
        review_input_length=review_input_length,
    )
    return raw
