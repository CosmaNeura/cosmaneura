#!/usr/bin/env python3
"""Authenticate the exact post-teardown bootstrap-identity evidence offline."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import sys
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, NoReturn

try:
    from scripts import staging_offline_plan_runtime as offline
except ModuleNotFoundError:  # Direct `python scripts/...` compatibility.
    import staging_offline_plan_runtime as offline


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = (
    ROOT / "contracts/staging-rehearsal/v1/"
    "restore-sanitizer-bootstrap-identity-teardown-evidence-policy.json"
)
POLICY_SHA256 = "2cdf25f0d4e6758d5ec76ea4eb9179c3310e9d7b71238868d971c5d140ccc6f0"
MAXIMUM_POLICY_BYTES = 128 * 1024
KIND = "sealed-authenticated-staging-sanitizer-bootstrap-identity-teardown-evidence-v1"
EVIDENCE_FILE = "post-apply-verification-v3.json"
REVIEW_REQUEST_FILE = "post-apply-review-request-v3.json"
REVIEW_RESPONSE_FILE = "post-apply-review-response-v3.json"
EVIDENCE_FILES = (EVIDENCE_FILE, REVIEW_REQUEST_FILE, REVIEW_RESPONSE_FILE)
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
HEX_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
UTC_RE = re.compile(r"^20[0-9]{2}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
PROVENANCE_DOMAIN = (
    "cosmaneura:staging:sanitizer-bootstrap-identity-teardown-evidence:v1"
)
CLAIMS = {
    "retrospective_teardown_evidence_authenticated": True,
    "bootstrap_identity_absence_authenticated": True,
    "bootstrap_direct_and_inherited_rbac_absence_authenticated": True,
    "bootstrap_job_attachment_absence_authenticated": True,
    "bootstrap_database_authority_absence_authenticated": True,
    "bootstrap_state_tombstone_authenticated": True,
    "cleanup_target_met": False,
    "cleanup_target_overrun_disclosed": True,
    "current_live_state_authenticated": False,
    "bootstrap_identity_creation_authority": False,
    "bootstrap_identity_attachment_authority": False,
    "provider_mutation_authority": False,
    "database_mutation_authority": False,
    "workflow_integrated": False,
    "executable": False,
}
BASE_PUBLIC_FIELDS = (
    "kind",
    "teardown_binding_sha256",
    "policy_sha256",
    "source_commit",
    "applied_plan_sha256",
    "review_response_id",
    "rehearsal_id",
    "authenticated_at",
    "evidence_completed_at",
    "cleanup_overrun_seconds",
)
PUBLIC_FIELDS = (*BASE_PUBLIC_FIELDS, *CLAIMS)
PRIVATE_FIELDS = ("_target", "_evidence_digests", "_proof_sha256", "_seal")
_SEAL = object()


class BootstrapIdentityTeardownEvidenceFailure(RuntimeError):
    """Bounded refusal without emitting raw identity or provider evidence."""


def _fail(code: str) -> NoReturn:
    raise BootstrapIdentityTeardownEvidenceFailure(code) from None


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


def _mapping(value: Any, fields: set[str], code: str) -> Mapping[str, Any]:
    if type(value) is not dict or set(value) != fields:
        _fail(code)
    return value


def _integer(value: Any, expected: int, code: str) -> None:
    if type(value) is not int or value != expected:
        _fail(code)


def _parse_utc(value: Any, code: str) -> datetime:
    if type(value) is not str or UTC_RE.fullmatch(value) is None:
        _fail(code)
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        _fail(code)
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        _fail(code)
    return parsed


def _trusted_now(clock: Callable[[], datetime]) -> datetime:
    try:
        value = clock()
    except Exception:
        _fail("bootstrap_identity_teardown_evidence.clock_invalid")
    if (
        type(value) is not datetime
        or value.tzinfo is not UTC
        or value.utcoffset() != timedelta(0)
        or value.microsecond
        or value.fold
    ):
        _fail("bootstrap_identity_teardown_evidence.clock_invalid")
    return value


def _utc_now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _validate_policy(value: Any) -> dict[str, Any]:
    code = "bootstrap_identity_teardown_evidence.policy_invalid"
    document = _mapping(
        value,
        {
            "schema_version",
            "policy_id",
            "closed_world",
            "source",
            "target",
            "evidence",
            "cleanup",
            "state",
            "authority",
            "independent_review",
            "claims",
            "output",
            "status",
        },
        code,
    )
    source = _mapping(
        document["source"],
        {
            "repository",
            "protected_ref",
            "source_commit",
            "applied_plan_sha256",
            "decoded_plan_sha256",
            "plan_review_request_sha256",
            "plan_review_response_id",
            "plan_review_response_sha256",
            "plan_review_verdict",
        },
        code,
    )
    target = _mapping(
        document["target"],
        {
            "subscription_id",
            "tenant_id",
            "resource_group",
            "identity_name",
            "identity_resource_id",
            "client_id",
            "principal_id",
            "rehearsal_id",
        },
        code,
    )
    evidence = _mapping(
        document["evidence"],
        {
            "acceptance_mode",
            "caller_selects_directory_not_content",
            "regular_owner_only_files_required",
            "symlinks_or_hardlinks_permitted",
            "exact_directory_entries_required",
            "maximum_file_bytes",
            "files",
        },
        code,
    )
    cleanup = _mapping(
        document["cleanup"],
        {
            "cleanup_not_after",
            "evidence_started_at",
            "evidence_completed_at",
            "started_overrun_seconds",
            "completed_overrun_seconds",
            "cleanup_target_met",
            "cleanup_target_overrun_disclosed",
            "overrun_is_not_current_authority_proof",
        },
        code,
    )
    state = _mapping(
        document["state"],
        {
            "backend_key",
            "opentofu_version",
            "lineage",
            "pre_apply_serial",
            "post_apply_serial",
            "exact_resource_addresses",
            "exact_output_keys",
            "exact_passing_check_addresses",
            "teardown_evidence_sha256",
        },
        code,
    )
    authority = _mapping(
        document["authority"],
        {
            "identity_count",
            "direct_or_inherited_role_assignment_count",
            "subscription_container_apps_job_count",
            "bootstrap_identity_job_attachment_count",
            "subscription_postgres_server_count",
            "entra_enabled_postgres_server_count",
            "entra_enabled_postgres_server_id",
            "bootstrap_identity_postgres_administrator_count",
        },
        code,
    )
    review = _mapping(
        document["independent_review"],
        {
            "model",
            "request_file",
            "request_sha256",
            "response_file",
            "response_id",
            "response_sha256",
            "verdict",
        },
        code,
    )
    output = _mapping(
        document["output"],
        {
            "kind",
            "public_fields",
            "private_fields",
            "raw_identity_or_provider_values_public",
            "copy_pickle_json_serialization_or_mutation_permitted",
        },
        code,
    )
    expected_status = {
        "authenticator_implemented": True,
        "retrospective_teardown_authenticated": True,
        "current_live_state_authenticated": False,
        "bootstrap_identity_recreation_authorized": False,
        "bootstrap_job_attachment_implemented": False,
        "bootstrap_control_plane_adapter_implemented": False,
        "bootstrap_database_provisioner_implemented": False,
        "provider_calls_permitted": False,
        "database_calls_permitted": False,
        "workflow_integrated": False,
        "executable": False,
        "activation_ready": False,
        "public_activation_failure": (
            "activation.sanitizer_bootstrap_job_attachment_unavailable"
        ),
    }
    files = evidence["files"]
    deadline = _parse_utc(cleanup["cleanup_not_after"], code)
    started = _parse_utc(cleanup["evidence_started_at"], code)
    completed = _parse_utc(cleanup["evidence_completed_at"], code)
    if (
        document["schema_version"] != 1
        or document["policy_id"]
        != "cosmaneura-staging-sanitizer-bootstrap-identity-teardown-evidence-v1"
        or document["closed_world"] is not True
        or source["repository"] != "CosmaNeura/cosmaneura"
        or source["protected_ref"] != "refs/heads/master"
        or SHA_RE.fullmatch(source["source_commit"]) is None
        or any(
            DIGEST_RE.fullmatch(source[name]) is None
            for name in (
                "applied_plan_sha256",
                "decoded_plan_sha256",
                "plan_review_request_sha256",
                "plan_review_response_sha256",
            )
        )
        or not str(source["plan_review_response_id"]).startswith("resp_")
        or source["plan_review_verdict"] != "APPROVED FOR FOUNDER APPLY"
        or any(
            UUID_RE.fullmatch(target[name]) is None
            for name in ("subscription_id", "tenant_id", "client_id", "principal_id")
        )
        or target["identity_name"] != "cosmaneura-staging-restore-bootstrap-job"
        or target["identity_resource_id"].casefold()
        != (
            f"/subscriptions/{target['subscription_id']}/resourceGroups/"
            f"{target['resource_group']}/providers/Microsoft.ManagedIdentity/"
            f"userAssignedIdentities/{target['identity_name']}"
        ).casefold()
        or target["rehearsal_id"] != "21506aaeec3c"
        or evidence["acceptance_mode"]
        != "exact-hash-pinned-independent-post-apply-review"
        or evidence["caller_selects_directory_not_content"] is not True
        or evidence["regular_owner_only_files_required"] is not True
        or evidence["symlinks_or_hardlinks_permitted"] is not False
        or evidence["exact_directory_entries_required"] is not True
        or type(evidence["maximum_file_bytes"]) is not int
        or not 1 <= evidence["maximum_file_bytes"] <= offline.MAX_FILE_BYTES
        or type(files) is not dict
        or set(files) != set(EVIDENCE_FILES)
        or any(DIGEST_RE.fullmatch(item) is None for item in files.values())
        or cleanup["cleanup_target_met"] is not False
        or cleanup["cleanup_target_overrun_disclosed"] is not True
        or cleanup["overrun_is_not_current_authority_proof"] is not True
        or started - deadline != timedelta(seconds=cleanup["started_overrun_seconds"])
        or completed - deadline
        != timedelta(seconds=cleanup["completed_overrun_seconds"])
        or not deadline < started <= completed
        or state["backend_key"] != "staging-sanitizer-bootstrap-identity.tfstate"
        or state["opentofu_version"] != "1.12.5"
        or UUID_RE.fullmatch(state["lineage"]) is None
        or state["pre_apply_serial"] != 2
        or state["post_apply_serial"] != 3
        or state["exact_resource_addresses"]
        != ["managed.terraform_data.activation_guard"]
        or state["exact_output_keys"]
        != ["sanitizer_bootstrap_identity_teardown", "state_boundary"]
        or state["exact_passing_check_addresses"]
        != [
            "terraform_data.activation_guard",
            "var.sanitizer_bootstrap_rehearsal_id",
            "var.sanitizer_bootstrap_lifetime_hours",
            "var.sanitizer_bootstrap_teardown_rehearsal_id",
            "var.sanitizer_bootstrap_teardown_evidence_sha256",
        ]
        or DIGEST_RE.fullmatch(state["teardown_evidence_sha256"]) is None
        or authority
        != {
            "identity_count": 0,
            "direct_or_inherited_role_assignment_count": 0,
            "subscription_container_apps_job_count": 6,
            "bootstrap_identity_job_attachment_count": 0,
            "subscription_postgres_server_count": 5,
            "entra_enabled_postgres_server_count": 1,
            "entra_enabled_postgres_server_id": authority[
                "entra_enabled_postgres_server_id"
            ],
            "bootstrap_identity_postgres_administrator_count": 0,
        }
        or not str(authority["entra_enabled_postgres_server_id"])
        .casefold()
        .startswith(f"/subscriptions/{target['subscription_id']}/".casefold())
        or review["model"] != "codex-auto-review"
        or review["request_file"] != REVIEW_REQUEST_FILE
        or review["response_file"] != REVIEW_RESPONSE_FILE
        or review["request_sha256"] != files[REVIEW_REQUEST_FILE]
        or review["response_sha256"] != files[REVIEW_RESPONSE_FILE]
        or not str(review["response_id"]).startswith("resp_")
        or review["verdict"] != "POST-APPLY VERIFIED"
        or document["claims"] != CLAIMS
        or output["kind"] != KIND
        or output["public_fields"] != list(PUBLIC_FIELDS)
        or output["private_fields"] != list(PRIVATE_FIELDS)
        or output["raw_identity_or_provider_values_public"] is not False
        or output["copy_pickle_json_serialization_or_mutation_permitted"] is not False
        or document["status"] != expected_status
    ):
        _fail(code)
    for key in (
        "started_overrun_seconds",
        "completed_overrun_seconds",
    ):
        if type(cleanup[key]) is not int or cleanup[key] <= 0:
            _fail(code)
    return dict(document)


def _read_policy() -> tuple[dict[str, Any], bytes]:
    try:
        raw = offline.read_checkout_file(
            POLICY_PATH,
            label="bootstrap identity teardown evidence policy",
            maximum_bytes=MAXIMUM_POLICY_BYTES,
        )
        if not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), POLICY_SHA256):
            _fail("bootstrap_identity_teardown_evidence.policy_invalid")
        parsed = offline.load_json_bytes(
            raw, label="bootstrap identity teardown evidence policy"
        )
        return _validate_policy(parsed), raw
    except BootstrapIdentityTeardownEvidenceFailure:
        raise
    except Exception:
        _fail("bootstrap_identity_teardown_evidence.policy_invalid")


def load_bootstrap_identity_teardown_evidence_policy() -> dict[str, Any]:
    """Load the exact checked-in retrospective teardown policy."""

    policy, _raw = _read_policy()
    return policy


def _read_evidence_files(
    directory: Path, policy: Mapping[str, Any]
) -> dict[str, bytes]:
    code = "bootstrap_identity_teardown_evidence.directory_invalid"
    if not isinstance(directory, Path) or not directory.is_absolute():
        _fail(code)
    descriptor = -1
    try:
        descriptor = offline.open_private_directory(
            directory, label="bootstrap identity teardown evidence directory"
        )
        entries = set(os.listdir(descriptor))
    except Exception:
        _fail(code)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if entries != set(EVIDENCE_FILES):
        _fail(code)
    result: dict[str, bytes] = {}
    for name in EVIDENCE_FILES:
        try:
            raw = offline.read_private_file(
                directory / name,
                label=name,
                maximum_bytes=policy["evidence"]["maximum_file_bytes"],
            )
        except Exception:
            _fail("bootstrap_identity_teardown_evidence.file_invalid")
        expected = policy["evidence"]["files"][name]
        if not hmac.compare_digest(_digest(raw), expected):
            _fail("bootstrap_identity_teardown_evidence.file_invalid")
        result[name] = raw
    return result


def _serialized_contains(value: Any, needles: tuple[str, ...]) -> bool:
    serialized = _canonical(value).decode("ascii").casefold()
    return any(needle.casefold() in serialized for needle in needles)


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
    if (
        state["version"] != 4
        or state["terraform_version"] != expected["opentofu_version"]
        or type(state["serial"]) is not int
        or state["serial"] != expected["post_apply_serial"]
        or state["lineage"] != expected["lineage"]
        or type(resources) is not list
        or [
            f"{item.get('mode')}.{item.get('type')}.{item.get('name')}"
            for item in resources
        ]
        != expected["exact_resource_addresses"]
        or resources[0].get("provider") != 'provider["terraform.io/builtin/terraform"]'
        or type(resources[0].get("instances")) is not list
        or len(resources[0]["instances"]) != 1
        or type(outputs) is not dict
        or list(outputs) != expected["exact_output_keys"]
        or type(checks) is not list
        or [item.get("config_addr") for item in checks]
        != expected["exact_passing_check_addresses"]
        or any(item.get("status") != "pass" for item in checks)
        or any(
            type(item.get("objects")) is not list
            or len(item["objects"]) != 1
            or item["objects"][0].get("status") != "pass"
            for item in checks
        )
    ):
        _fail(code)
    instance = resources[0]["instances"][0]
    if (
        type(instance) is not dict
        or set(instance)
        != {"index_key", "schema_version", "attributes", "sensitive_attributes"}
        or instance["index_key"] != 0
        or instance["schema_version"] != 0
        or instance["sensitive_attributes"] != []
        or type(instance["attributes"]) is not dict
        or set(instance["attributes"]) != {"id", "input", "output", "triggers_replace"}
        or UUID_RE.fullmatch(instance["attributes"]["id"]) is None
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
    attributes = instance["attributes"]
    if (
        type(attributes["input"]) is not dict
        or attributes["input"].get("value") != guard
        or type(attributes["output"]) is not dict
        or attributes["output"].get("value") != guard
        or type(attributes["triggers_replace"]) is not dict
        or attributes["triggers_replace"].get("value")
        != {
            "cleanup_not_after": cleanup["cleanup_not_after"],
            "lifetime_hours": 6,
            "rehearsal_id": target["rehearsal_id"],
        }
    ):
        _fail(code)
    teardown = outputs["sanitizer_bootstrap_identity_teardown"].get("value")
    boundary = outputs["state_boundary"].get("value")
    if teardown != {
        "apply_authorized": False,
        "cleanup_not_after": cleanup["cleanup_not_after"],
        "founder_apply_required": True,
        "identity_resource_id": target["identity_resource_id"],
        "mode": "teardown-only",
        "rehearsal_id": target["rehearsal_id"],
        "teardown_evidence_sha256": expected["teardown_evidence_sha256"],
    } or boundary != {
        "container": "cosmaneura-tfstate",
        "key": expected["backend_key"],
        "resource_group": "cosmaneura-tfstate",
        "storage_account": "cosmaneuratfstate",
    }:
        _fail(code)


def _validate_evidence(value: Any, policy: Mapping[str, Any]) -> None:
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
    evidence_hashes = prior["evidence_hashes"]
    if (
        envelope["schema_version"] != 3
        or envelope["policy_id"]
        != "cosmaneura-staging-sanitizer-bootstrap-identity-post-apply-v3"
        or envelope["pass"] is not True
        or HEX_DIGEST_RE.fullmatch(envelope["prior_envelope_sha256"]) is None
        or prior["schema_version"] != 2
        or prior["policy_id"]
        != "cosmaneura-staging-sanitizer-bootstrap-identity-post-apply-v2"
        or prior["target"] != observed_target
        or prior["source_commit"] != source["source_commit"]
        or f"sha256:{prior['applied_plan_sha256']}" != source["applied_plan_sha256"]
        or prior["cleanup_not_after"] != cleanup["cleanup_not_after"]
        or type(prior["capture_script"]) is not str
        or hashlib.sha256(prior["capture_script"].encode()).hexdigest()
        != prior["capture_script_sha256"]
        or HEX_DIGEST_RE.fullmatch(prior["capture_script_sha256"]) is None
        or prior["query_contract"] != expected_queries
        or type(evidence_hashes) is not dict
        or set(evidence_hashes)
        != {
            "v1_verification",
            "identities",
            "role_assignments",
            "jobs",
            "postgres_admins",
            "isolated_state",
        }
        or any(
            HEX_DIGEST_RE.fullmatch(item) is None for item in evidence_hashes.values()
        )
        or verification["schema_version"] != 1
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
        or account
        != {
            "environmentName": "AzureCloud",
            "id": target["subscription_id"],
            "isDefault": True,
            "state": "Enabled",
            "tenantId": target["tenant_id"],
        }
        or raw["identities"] != []
        or raw["role_assignments"] != []
        or verification["identity_count"] != authority["identity_count"]
        or verification["role_assignment_count"]
        != authority["direct_or_inherited_role_assignment_count"]
        or type(jobs) is not list
        or len(jobs) != authority["subscription_container_apps_job_count"]
        or verification["job_count"] != len(jobs)
        or verification["job_attachment_matches"]
        != authority["bootstrap_identity_job_attachment_count"]
        or _serialized_contains(jobs, needles)
        or type(admins) is not list
        or verification["postgres_admin_matches"]
        != authority["bootstrap_identity_postgres_administrator_count"]
        or _serialized_contains(admins, needles)
        or inherited["command"] != expected_rbac_command
        or HEX_DIGEST_RE.fullmatch(inherited["evidence_sha256"]) is None
        or inherited["assignments"] != []
        or inherited["count"] != authority["direct_or_inherited_role_assignment_count"]
        or not completed <= rbac_started <= rbac_completed
        or type(servers) is not list
        or HEX_DIGEST_RE.fullmatch(postgres["evidence_sha256"]) is None
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
        or type(verification["state_serial"]) is not int
        or verification["state_serial"] != state_policy["post_apply_serial"]
        or verification["state_outputs"] != state_policy["exact_output_keys"]
        or [
            f"{item.get('mode')}.{item.get('type')}.{item.get('name')}"
            for item in verification["state_resources"]
        ]
        != state_policy["exact_resource_addresses"]
    ):
        _fail(code)
    _integer(inherited["count"], 0, code)
    _integer(postgres["count"], authority["subscription_postgres_server_count"], code)
    _validate_state(raw["isolated_state"], policy)


def _validate_review_response(value: Any, policy: Mapping[str, Any]) -> None:
    code = "bootstrap_identity_teardown_evidence.review_invalid"
    if type(value) is not dict:
        _fail(code)
    output = value.get("output")
    if type(output) is not list:
        _fail(code)
    texts: list[str] = []
    for item in output:
        if type(item) is not dict or item.get("type") != "message":
            continue
        content = item.get("content")
        if type(content) is not list:
            _fail(code)
        for block in content:
            if type(block) is dict and block.get("type") == "output_text":
                text = block.get("text")
                if type(text) is not str:
                    _fail(code)
                texts.append(text)
    review = policy["independent_review"]
    if (
        value.get("id") != review["response_id"]
        or value.get("model") != review["model"]
        or value.get("status") != "completed"
        or value.get("error") is not None
        or value.get("incomplete_details") is not None
        or texts != [review["verdict"]]
    ):
        _fail(code)


def _binding_material(
    policy: Mapping[str, Any], evidence_digests: Mapping[str, str]
) -> dict[str, Any]:
    return {
        "policy_sha256": f"sha256:{POLICY_SHA256}",
        "source_commit": policy["source"]["source_commit"],
        "applied_plan_sha256": policy["source"]["applied_plan_sha256"],
        "review_response_id": policy["independent_review"]["response_id"],
        "rehearsal_id": policy["target"]["rehearsal_id"],
        "evidence_completed_at": policy["cleanup"]["evidence_completed_at"],
        "cleanup_overrun_seconds": policy["cleanup"]["completed_overrun_seconds"],
        "target": dict(policy["target"]),
        "evidence_digests": dict(evidence_digests),
        "claims": dict(CLAIMS),
    }


class AuthenticatedBootstrapIdentityTeardownEvidence:
    """Immutable process-local proof of the reviewed teardown checkpoint."""

    __slots__ = (*PUBLIC_FIELDS, *PRIVATE_FIELDS)

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        _fail("bootstrap_identity_teardown_evidence.output_invalid")

    def __setattr__(self, name: str, value: object) -> NoReturn:
        del name, value
        _fail("bootstrap_identity_teardown_evidence.output_invalid")

    def __delattr__(self, name: str) -> NoReturn:
        del name
        _fail("bootstrap_identity_teardown_evidence.output_invalid")

    def __repr__(self) -> str:
        return "<AuthenticatedBootstrapIdentityTeardownEvidence sealed>"

    def __copy__(self) -> NoReturn:
        _fail("bootstrap_identity_teardown_evidence.output_invalid")

    def __deepcopy__(self, memo: object) -> NoReturn:
        del memo
        _fail("bootstrap_identity_teardown_evidence.output_invalid")

    def __reduce__(self) -> NoReturn:
        _fail("bootstrap_identity_teardown_evidence.output_invalid")

    def __reduce_ex__(self, protocol: object) -> NoReturn:
        del protocol
        _fail("bootstrap_identity_teardown_evidence.output_invalid")

    def _is_authenticated(self) -> bool:
        try:
            policy = load_bootstrap_identity_teardown_evidence_policy()
            target = dict(self._target)
            evidence_digests = dict(self._evidence_digests)
            material = _binding_material(policy, evidence_digests)
            public = {
                "kind": KIND,
                "teardown_binding_sha256": _digest(_canonical(material)),
                "policy_sha256": f"sha256:{POLICY_SHA256}",
                "source_commit": policy["source"]["source_commit"],
                "applied_plan_sha256": policy["source"]["applied_plan_sha256"],
                "review_response_id": policy["independent_review"]["response_id"],
                "rehearsal_id": policy["target"]["rehearsal_id"],
                "authenticated_at": self.authenticated_at,
                "evidence_completed_at": _parse_utc(
                    policy["cleanup"]["evidence_completed_at"],
                    "bootstrap_identity_teardown_evidence.policy_invalid",
                ),
                "cleanup_overrun_seconds": policy["cleanup"][
                    "completed_overrun_seconds"
                ],
                **CLAIMS,
            }
            actual = {name: getattr(self, name) for name in PUBLIC_FIELDS}
            return (
                type(self) is AuthenticatedBootstrapIdentityTeardownEvidence
                and self._seal is _SEAL
                and tuple(public) == PUBLIC_FIELDS
                and target == policy["target"]
                and self._evidence_digests == tuple(policy["evidence"]["files"].items())
                and all(
                    type(actual[name]) is type(expected) and actual[name] == expected
                    for name, expected in public.items()
                )
                and _verify_provenance(self)
            )
        except Exception:
            return False


def _validated_candidate(
    *,
    policy: Mapping[str, Any],
    evidence_directory: Path,
    clock: Callable[[], datetime],
) -> AuthenticatedBootstrapIdentityTeardownEvidence:
    document = _validate_policy(policy)
    before = _trusted_now(clock)
    completed = _parse_utc(
        document["cleanup"]["evidence_completed_at"],
        "bootstrap_identity_teardown_evidence.policy_invalid",
    )
    if before < completed:
        _fail("bootstrap_identity_teardown_evidence.evidence_from_future")
    raw = _read_evidence_files(evidence_directory, document)
    try:
        parsed = {
            name: offline.load_json_bytes(value, label=name)
            for name, value in raw.items()
        }
    except Exception:
        _fail("bootstrap_identity_teardown_evidence.file_invalid")
    _validate_evidence(parsed[EVIDENCE_FILE], document)
    request = parsed[REVIEW_REQUEST_FILE]
    if request.get("model") != document["independent_review"]["model"]:
        _fail("bootstrap_identity_teardown_evidence.review_invalid")
    _validate_review_response(parsed[REVIEW_RESPONSE_FILE], document)
    after = _trusted_now(clock)
    if after < before:
        _fail("bootstrap_identity_teardown_evidence.clock_regressed")
    evidence_digests = document["evidence"]["files"]
    material = _binding_material(document, evidence_digests)
    value = object.__new__(AuthenticatedBootstrapIdentityTeardownEvidence)
    values = {
        "kind": KIND,
        "teardown_binding_sha256": _digest(_canonical(material)),
        "policy_sha256": f"sha256:{POLICY_SHA256}",
        "source_commit": document["source"]["source_commit"],
        "applied_plan_sha256": document["source"]["applied_plan_sha256"],
        "review_response_id": document["independent_review"]["response_id"],
        "rehearsal_id": document["target"]["rehearsal_id"],
        "authenticated_at": after,
        "evidence_completed_at": completed,
        "cleanup_overrun_seconds": document["cleanup"]["completed_overrun_seconds"],
        **CLAIMS,
        "_target": tuple(document["target"].items()),
        "_evidence_digests": tuple(evidence_digests.items()),
        "_proof_sha256": "",
        "_seal": _SEAL,
    }
    if tuple(values) != (*PUBLIC_FIELDS, *PRIVATE_FIELDS):
        _fail("bootstrap_identity_teardown_evidence.output_invalid")
    for name, item in values.items():
        object.__setattr__(value, name, item)
    return value


def _build_provenance() -> tuple[Callable[..., Any], Callable[..., bool]]:
    key = os.urandom(32)
    issued: dict[tuple[int, int], object] = {}

    def proof(value: AuthenticatedBootstrapIdentityTeardownEvidence) -> str:
        material = _canonical(
            {
                "domain": PROVENANCE_DOMAIN,
                "pid": os.getpid(),
                "object_identity": id(value),
                "teardown_binding_sha256": value.teardown_binding_sha256,
                "authenticated_at": value.authenticated_at.strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                ),
                "seal_identity": id(_SEAL),
            }
        )
        return hmac.new(key, material, hashlib.sha256).hexdigest()

    def verify(value: AuthenticatedBootstrapIdentityTeardownEvidence) -> bool:
        return issued.get((os.getpid(), id(value))) is value and hmac.compare_digest(
            value._proof_sha256, proof(value)
        )

    def authenticate(
        *, evidence_directory: Path
    ) -> AuthenticatedBootstrapIdentityTeardownEvidence:
        policy, _raw = _read_policy()
        value = _validated_candidate(
            policy=policy,
            evidence_directory=evidence_directory,
            clock=_utc_now,
        )
        object.__setattr__(value, "_proof_sha256", proof(value))
        issued[os.getpid(), id(value)] = value
        if not value._is_authenticated():
            issued.pop((os.getpid(), id(value)), None)
            _fail("bootstrap_identity_teardown_evidence.output_invalid")
        return value

    return authenticate, verify


_authenticate, _verify_provenance = _build_provenance()


def authenticate_bootstrap_identity_teardown_evidence(
    *, evidence_directory: Path
) -> AuthenticatedBootstrapIdentityTeardownEvidence:
    """Authenticate the exact reviewed evidence without live authority."""

    try:
        return _authenticate(evidence_directory=evidence_directory)
    except BootstrapIdentityTeardownEvidenceFailure:
        raise
    except Exception:
        _fail("bootstrap_identity_teardown_evidence.evidence_invalid")


def _public_projection(
    value: AuthenticatedBootstrapIdentityTeardownEvidence,
) -> dict[str, Any]:
    if not value._is_authenticated():
        _fail("bootstrap_identity_teardown_evidence.output_invalid")
    return {
        name: (
            getattr(value, name).strftime("%Y-%m-%dT%H:%M:%SZ")
            if type(getattr(value, name)) is datetime
            else getattr(value, name)
        )
        for name in PUBLIC_FIELDS
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence_directory", type=Path)
    args = parser.parse_args(argv)
    try:
        result = authenticate_bootstrap_identity_teardown_evidence(
            evidence_directory=args.evidence_directory
        )
        sys.stdout.buffer.write(_canonical(_public_projection(result)) + b"\n")
    except BootstrapIdentityTeardownEvidenceFailure as error:
        print(str(error), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
