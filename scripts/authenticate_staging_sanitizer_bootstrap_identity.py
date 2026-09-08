#!/usr/bin/env python3
"""Authenticate the hash-pinned bootstrap-identity evidence checkpoint.

This offline-only module reads a closed set of owner-only evidence files. It
has no network, credential, provider, database, workflow, or mutation surface.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import stat
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, NoReturn, SupportsIndex

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = (
    ROOT
    / "contracts/staging-rehearsal/v1/restore-sanitizer-bootstrap-identity-evidence-policy.json"
)
POLICY_SHA256 = "8c6dd344d9eae0e682098402f48bb5f816787b3f4f9686b5744672c2ebe420ed"
MAXIMUM_POLICY_BYTES = 64 * 1024
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
UTC_RE = re.compile(r"^20[0-9]{2}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
KIND = "sealed-authenticated-staging-sanitizer-bootstrap-identity-evidence-v1"
RECEIPT_FILE = "post-apply-evidence-receipt.json"
STATE_FILE = "post-apply-final.tfstate"
NORMALIZED_STATE_FILE = "post-apply-normalized.tfstate"
IDENTITY_FILE = "post-apply-identity.json"
FEDERATED_FILE = "post-apply-federated-credentials.json"
ROLES_FILE = "post-apply-role-assignments.json"
INVENTORY_FILE = "job-inventory.json"
JOB_FILES = (
    "job-identity-pilot-migrate.json",
    "job-identity-pilot-worker.json",
    "job-identity-prod-migrate.json",
    "job-identity-sched-dev-migrate.json",
    "job-identity-scribe-dev-smoke.json",
    "job-identity-staging-migrate.json",
)
EVIDENCE_FILES = (
    RECEIPT_FILE,
    STATE_FILE,
    NORMALIZED_STATE_FILE,
    IDENTITY_FILE,
    FEDERATED_FILE,
    ROLES_FILE,
    INVENTORY_FILE,
    *JOB_FILES,
)
CLAIMS = {
    "retrospective_evidence_authenticated": True,
    "bootstrap_identity_selected": True,
    "bootstrap_identity_exact_ids_pinned": True,
    "bootstrap_identity_current_live_state_authenticated": False,
    "bootstrap_identity_bound_to_single_use_job": False,
    "bootstrap_authority_authenticated": False,
    "database_connection_authority": False,
    "database_mutation_authority": False,
    "provider_mutation_authority": False,
    "workflow_integrated": False,
    "executable": False,
}
PUBLIC_FIELDS = (
    "kind",
    "identity_binding_sha256",
    "policy_sha256",
    "foundation_source_commit",
    "post_apply_receipt_sha256",
    "rehearsal_id",
    "authenticated_at",
    "expires_at",
    *CLAIMS,
)
PRIVATE_FIELDS = ("_identity", "_evidence_digests", "_proof_sha256", "_seal")
PROVENANCE_DOMAIN = "cosmaneura:staging:sanitizer-bootstrap-identity-evidence:v1"
_SEAL = object()


class BootstrapIdentityEvidenceFailure(RuntimeError):
    """Bounded refusal without emitting identity or provider response content."""


def _fail(code: str) -> NoReturn:
    raise BootstrapIdentityEvidenceFailure(code) from None


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
        _fail("bootstrap_identity_evidence.json_invalid")


def _digest(raw: bytes) -> str:
    return f"sha256:{hashlib.sha256(raw).hexdigest()}"


def _parse_json(raw: bytes, code: str) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for name, value in items:
            if name in result:
                _fail(code)
            result[name] = value
        return result

    try:
        return json.loads(
            raw,
            object_pairs_hook=pairs,
            parse_constant=lambda _value: _fail(code),
        )
    except BootstrapIdentityEvidenceFailure:
        raise
    except (UnicodeDecodeError, ValueError, RecursionError):
        _fail(code)


def _mapping(value: Any, fields: set[str], code: str) -> Mapping[str, Any]:
    if type(value) is not dict or set(value) != fields:
        _fail(code)
    return value


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
        if (
            type(value) is not datetime
            or value.tzinfo is None
            or value.utcoffset() != timedelta(0)
            or value.microsecond != 0
        ):
            _fail("bootstrap_identity_evidence.clock_invalid")
        return value.astimezone(UTC)
    except BootstrapIdentityEvidenceFailure:
        raise
    except Exception:
        _fail("bootstrap_identity_evidence.clock_invalid")


def _utc_now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _lower_resource_id(value: Any, code: str) -> str:
    if type(value) is not str or not value.startswith("/subscriptions/"):
        _fail(code)
    if value.endswith("/") or "//" in value or any(char in value for char in "%?#"):
        _fail(code)
    return value.lower()


def _validate_policy(policy: Any) -> dict[str, Any]:
    document = _mapping(
        policy,
        {
            "schema_version",
            "policy_id",
            "closed_world",
            "foundation",
            "evidence",
            "state",
            "identity",
            "job_inventory",
            "claims",
            "output",
            "status",
        },
        "bootstrap_identity_evidence.policy_invalid",
    )
    foundation = _mapping(
        document["foundation"],
        {
            "repository",
            "protected_ref",
            "source_commit",
            "saved_plan_sha256",
            "rehearsal_id",
            "cleanup_not_after",
        },
        "bootstrap_identity_evidence.policy_invalid",
    )
    evidence = _mapping(
        document["evidence"],
        {
            "acceptance_mode",
            "caller_selects_directory_not_content",
            "regular_owner_only_files_required",
            "symlinks_permitted",
            "maximum_file_bytes",
            "files",
        },
        "bootstrap_identity_evidence.policy_invalid",
    )
    state_policy = _mapping(
        document["state"],
        {
            "backend_key",
            "opentofu_version",
            "lineage",
            "pre_apply_serial",
            "post_apply_serial",
            "exact_resource_addresses",
            "exact_output_keys",
            "required_passing_checks",
        },
        "bootstrap_identity_evidence.policy_invalid",
    )
    identity = _mapping(
        document["identity"],
        {
            "name",
            "resource_id",
            "client_id",
            "principal_id",
            "tenant_id",
            "resource_group",
            "location",
            "type",
            "isolation_scope",
            "assignment_restrictions",
            "tags",
        },
        "bootstrap_identity_evidence.policy_invalid",
    )
    inventory = _mapping(
        document["job_inventory"],
        {"exact_jobs", "bootstrap_attachment_count"},
        "bootstrap_identity_evidence.policy_invalid",
    )
    output = _mapping(
        document["output"],
        {
            "kind",
            "public_fields",
            "private_fields",
            "copy_pickle_json_serialization_or_mutation_permitted",
            "raw_evidence_or_exact_identity_values_publicly_exposed",
        },
        "bootstrap_identity_evidence.policy_invalid",
    )
    status_policy = _mapping(
        document["status"],
        {
            "offline_authenticator_implemented",
            "live_provider_transport_implemented",
            "binding_plan_integration_implemented",
            "job_attachment_implemented",
            "teardown_authenticator_implemented",
            "activation_ready",
            "activation_blockers",
        },
        "bootstrap_identity_evidence.policy_invalid",
    )
    files = evidence["files"]
    jobs = inventory["exact_jobs"]
    tags = identity["tags"]
    if (
        document["schema_version"] != 1
        or document["policy_id"]
        != "cosmaneura-staging-sanitizer-bootstrap-identity-evidence-v1"
        or document["closed_world"] is not True
        or foundation["repository"] != "CosmaNeura/cosmaneura"
        or foundation["protected_ref"] != "refs/heads/master"
        or type(foundation["source_commit"]) is not str
        or SHA_RE.fullmatch(foundation["source_commit"]) is None
        or type(foundation["saved_plan_sha256"]) is not str
        or DIGEST_RE.fullmatch(foundation["saved_plan_sha256"]) is None
        or not re.fullmatch(r"[0-9a-f]{12}", str(foundation["rehearsal_id"]))
        or _parse_utc(
            foundation["cleanup_not_after"],
            "bootstrap_identity_evidence.policy_invalid",
        )
        <= datetime(2026, 1, 1, tzinfo=UTC)
        or evidence["acceptance_mode"]
        != "reviewed-retrospective-read-only-provider-evidence"
        or evidence["caller_selects_directory_not_content"] is not True
        or evidence["regular_owner_only_files_required"] is not True
        or evidence["symlinks_permitted"] is not False
        or type(evidence["maximum_file_bytes"]) is not int
        or not 1 <= evidence["maximum_file_bytes"] <= 1024 * 1024
        or type(files) is not dict
        or tuple(files) != EVIDENCE_FILES
        or any(
            type(value) is not str or DIGEST_RE.fullmatch(value) is None
            for value in files.values()
        )
        or state_policy["backend_key"] != "staging-sanitizer-bootstrap-identity.tfstate"
        or state_policy["opentofu_version"] != "1.12.5"
        or UUID_RE.fullmatch(str(state_policy["lineage"])) is None
        or type(state_policy["pre_apply_serial"]) is not int
        or type(state_policy["post_apply_serial"]) is not int
        or state_policy["post_apply_serial"] != state_policy["pre_apply_serial"] + 1
        or state_policy["exact_resource_addresses"]
        != [
            "data.azurerm_resource_group.control",
            "managed.azurerm_user_assigned_identity.sanitizer_bootstrap",
            "managed.terraform_data.activation_guard",
        ]
        or state_policy["exact_output_keys"]
        != ["sanitizer_bootstrap_identity_foundation", "state_boundary"]
        or state_policy["required_passing_checks"]
        != [
            "azurerm_user_assigned_identity.sanitizer_bootstrap",
            "terraform_data.activation_guard",
            "var.sanitizer_bootstrap_lifetime_hours",
            "var.sanitizer_bootstrap_rehearsal_id",
        ]
        or identity["name"] != "cosmaneura-staging-restore-bootstrap-job"
        or identity["resource_group"] != "cosmaneura-staging-rehearsal-control"
        or identity["location"] != "eastus"
        or identity["type"] != "Microsoft.ManagedIdentity/userAssignedIdentities"
        or identity["isolation_scope"] != "None"
        or identity["assignment_restrictions"] != {"providers": []}
        or any(
            UUID_RE.fullmatch(str(identity[name])) is None
            for name in ("client_id", "principal_id", "tenant_id")
        )
        or _lower_resource_id(
            identity["resource_id"], "bootstrap_identity_evidence.policy_invalid"
        )
        != (
            f"/subscriptions/bab2a709-1fcb-4c87-a9fc-802f7477ec13/"
            f"resourcegroups/{identity['resource_group']}/providers/"
            f"microsoft.managedidentity/userassignedidentities/{identity['name']}"
        ).lower()
        or tags
        != {
            "cleanup-not-after": foundation["cleanup_not_after"],
            "data-classification": "non-phi-control-metadata",
            "environment": "staging",
            "lifecycle": "ephemeral",
            "managed-by": "opentofu",
            "purpose": "staging-restore-sanitizer-bootstrap",
            "rehearsal-id": foundation["rehearsal_id"],
        }
        or type(jobs) is not dict
        or tuple(jobs) != JOB_FILES
        or len(set(jobs.values())) != len(JOB_FILES)
        or any(
            _lower_resource_id(
                value, "bootstrap_identity_evidence.policy_invalid"
            ).count("/providers/microsoft.app/jobs/")
            != 1
            for value in jobs.values()
        )
        or inventory["bootstrap_attachment_count"] != 0
        or document["claims"] != CLAIMS
        or output["kind"] != KIND
        or output["public_fields"] != list(PUBLIC_FIELDS)
        or output["private_fields"] != list(PRIVATE_FIELDS)
        or output["copy_pickle_json_serialization_or_mutation_permitted"] is not False
        or output["raw_evidence_or_exact_identity_values_publicly_exposed"] is not False
        or status_policy
        != {
            "offline_authenticator_implemented": True,
            "live_provider_transport_implemented": False,
            "binding_plan_integration_implemented": True,
            "job_attachment_implemented": False,
            "teardown_authenticator_implemented": False,
            "activation_ready": False,
            "activation_blockers": [
                "fresh_live_provider_reauthentication_unimplemented",
                "bootstrap_job_attachment_unimplemented",
                "bootstrap_teardown_authenticator_unimplemented",
            ],
        }
    ):
        _fail("bootstrap_identity_evidence.policy_invalid")
    return dict(document)


def _read_policy() -> tuple[dict[str, Any], bytes]:
    try:
        raw = POLICY_PATH.read_bytes()
    except OSError:
        _fail("bootstrap_identity_evidence.policy_invalid")
    if not 1 <= len(raw) <= MAXIMUM_POLICY_BYTES or not hmac.compare_digest(
        hashlib.sha256(raw).hexdigest(), POLICY_SHA256
    ):
        _fail("bootstrap_identity_evidence.policy_invalid")
    return _validate_policy(
        _parse_json(raw, "bootstrap_identity_evidence.policy_invalid")
    ), raw


def load_bootstrap_identity_evidence_policy() -> dict[str, Any]:
    """Load and validate the exact checked-in evidence policy."""

    policy, _raw = _read_policy()
    return policy


def _read_exact_evidence(root: Path, name: str, expected: str, maximum: int) -> bytes:
    path = root / name
    descriptor = -1
    try:
        before = path.lstat()
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid not in {0, os.geteuid()}
            or stat.S_IMODE(before.st_mode) & 0o077
            or before.st_nlink != 1
            or not 1 <= before.st_size <= maximum
        ):
            _fail("bootstrap_identity_evidence.file_invalid")
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        after = os.fstat(descriptor)
        raw = os.read(descriptor, maximum + 1)
        if (
            (before.st_dev, before.st_ino, before.st_size)
            != (after.st_dev, after.st_ino, after.st_size)
            or not stat.S_ISREG(after.st_mode)
            or after.st_uid not in {0, os.geteuid()}
            or stat.S_IMODE(after.st_mode) & 0o077
            or after.st_nlink != 1
            or len(raw) != before.st_size
            or os.read(descriptor, 1)
            or not hmac.compare_digest(_digest(raw), expected)
        ):
            _fail("bootstrap_identity_evidence.file_invalid")
        return raw
    except BootstrapIdentityEvidenceFailure:
        raise
    except OSError:
        _fail("bootstrap_identity_evidence.file_invalid")
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _validate_receipt(
    receipt: Any, policy: Mapping[str, Any], evidence_digests: Mapping[str, str]
) -> None:
    value = _mapping(
        receipt,
        {
            "schema_version",
            "created_at_utc",
            "scope",
            "result",
            "source",
            "operation",
            "isolated_state",
            "bootstrap_identity",
            "negative_authority_evidence",
            "container_apps_jobs_checked",
            "evidence_files",
            "safety",
        },
        "bootstrap_identity_evidence.receipt_invalid",
    )
    foundation = policy["foundation"]
    identity = policy["identity"]
    state_policy = policy["state"]
    source = _mapping(
        value["source"],
        {
            "repository",
            "protected_master_sha",
            "remote_master_readback_sha",
            "saved_plan_sha256",
            "decoded_plan_sha256",
            "validator_receipt_sha256",
            "independent_plan_review_response_id",
            "independent_plan_review_verdict",
        },
        "bootstrap_identity_evidence.receipt_invalid",
    )
    operation = _mapping(
        value["operation"],
        {
            "operator",
            "apply_reported_by_user",
            "apply_executed_by_agent",
            "plan_status",
            "spent_plan_path",
            "reapply_authorized",
            "create_count",
            "azure_create_count",
            "update_count",
            "destroy_or_replace_count",
        },
        "bootstrap_identity_evidence.receipt_invalid",
    )
    state = _mapping(
        value["isolated_state"],
        {
            "backend_key",
            "opentofu_version",
            "lineage",
            "pre_apply_serial",
            "post_apply_serial",
            "post_apply_resource_addresses",
            "output_keys",
            "check_count",
            "all_checks_pass",
            "raw_state_sha256",
            "normalized_state_sha256",
        },
        "bootstrap_identity_evidence.receipt_invalid",
    )
    observed = _mapping(
        value["bootstrap_identity"],
        {
            "name",
            "resource_id",
            "resource_id_comparison",
            "client_id",
            "principal_id",
            "tenant_id",
            "location",
            "rehearsal_id",
            "lifecycle",
            "cleanup_not_after",
            "destruction_protected",
            "live_readback_matches_state",
            "required_tags_match",
        },
        "bootstrap_identity_evidence.receipt_invalid",
    )
    negative = _mapping(
        value["negative_authority_evidence"],
        {
            "federated_credential_count",
            "direct_or_inherited_role_assignment_count",
            "subscription_container_apps_job_count",
            "bootstrap_identity_job_attachment_count",
            "database_authority_bound",
            "provider_authority_bound",
            "single_use_job_bound",
            "workflow_binding_implemented",
            "exact_ids_authenticated",
        },
        "bootstrap_identity_evidence.receipt_invalid",
    )
    safety = _mapping(
        value["safety"],
        {
            "no_second_apply",
            "no_destroy_or_teardown_executed",
            "no_federation_created",
            "no_role_assignment_created",
            "no_job_attachment_created",
            "cleanup_deadline_is_hard_gate",
        },
        "bootstrap_identity_evidence.receipt_invalid",
    )
    expected_file_hashes = {
        name: digest.removeprefix("sha256:")
        for name, digest in evidence_digests.items()
        if name != RECEIPT_FILE
    }
    checked_jobs = value["container_apps_jobs_checked"]
    if (
        value["schema_version"]
        != "cosmaneura.staging-sanitizer-bootstrap-post-apply-evidence.v1"
        or value["scope"] != "non-phi staging control metadata"
        or value["result"] != "pass"
        or _parse_utc(
            value["created_at_utc"], "bootstrap_identity_evidence.receipt_invalid"
        )
        >= _parse_utc(
            foundation["cleanup_not_after"],
            "bootstrap_identity_evidence.receipt_invalid",
        )
        or source["repository"] != foundation["repository"]
        or source["protected_master_sha"] != foundation["source_commit"]
        or source["remote_master_readback_sha"] != foundation["source_commit"]
        or f"sha256:{source['saved_plan_sha256']}" != foundation["saved_plan_sha256"]
        or DIGEST_RE.fullmatch(f"sha256:{source['decoded_plan_sha256']}") is None
        or DIGEST_RE.fullmatch(f"sha256:{source['validator_receipt_sha256']}") is None
        or not str(source["independent_plan_review_response_id"]).startswith("resp_")
        or source["independent_plan_review_verdict"] != "No actionable findings."
        or operation
        != {
            "operator": "founder",
            "apply_reported_by_user": True,
            "apply_executed_by_agent": False,
            "plan_status": "spent_and_quarantined",
            "spent_plan_path": "/private/tmp/cosmaneura-bootstrap-live-s54/APPLIED-DO-NOT-REAPPLY-bootstrap.tfplan",
            "reapply_authorized": False,
            "create_count": 2,
            "azure_create_count": 1,
            "update_count": 0,
            "destroy_or_replace_count": 0,
        }
        or state["backend_key"] != state_policy["backend_key"]
        or state["opentofu_version"] != state_policy["opentofu_version"]
        or state["lineage"] != state_policy["lineage"]
        or state["pre_apply_serial"] != state_policy["pre_apply_serial"]
        or state["post_apply_serial"] != state_policy["post_apply_serial"]
        or state["post_apply_resource_addresses"]
        != state_policy["exact_resource_addresses"]
        or state["output_keys"] != state_policy["exact_output_keys"]
        or state["check_count"] != len(state_policy["required_passing_checks"])
        or state["all_checks_pass"] is not True
        or state["raw_state_sha256"]
        != evidence_digests[STATE_FILE].removeprefix("sha256:")
        or state["normalized_state_sha256"]
        != evidence_digests[NORMALIZED_STATE_FILE].removeprefix("sha256:")
        or observed["name"] != identity["name"]
        or _lower_resource_id(
            observed["resource_id"],
            "bootstrap_identity_evidence.receipt_invalid",
        )
        != _lower_resource_id(
            identity["resource_id"], "bootstrap_identity_evidence.receipt_invalid"
        )
        or observed["resource_id_comparison"]
        != "Azure resource IDs compared case-insensitively"
        or observed["client_id"] != identity["client_id"]
        or observed["principal_id"] != identity["principal_id"]
        or observed["tenant_id"] != identity["tenant_id"]
        or observed["location"] != identity["location"]
        or observed["rehearsal_id"] != foundation["rehearsal_id"]
        or observed["lifecycle"] != "ephemeral"
        or observed["cleanup_not_after"] != foundation["cleanup_not_after"]
        or observed["destruction_protected"] is not True
        or observed["live_readback_matches_state"] is not True
        or observed["required_tags_match"] is not True
        or negative
        != {
            "federated_credential_count": 0,
            "direct_or_inherited_role_assignment_count": 0,
            "subscription_container_apps_job_count": len(JOB_FILES),
            "bootstrap_identity_job_attachment_count": 0,
            "database_authority_bound": False,
            "provider_authority_bound": False,
            "single_use_job_bound": False,
            "workflow_binding_implemented": False,
            "exact_ids_authenticated": False,
        }
        or type(checked_jobs) is not list
        or len(checked_jobs) != len(JOB_FILES)
        or any(type(item) is not str for item in checked_jobs)
        or len(set(checked_jobs)) != len(JOB_FILES)
        or {
            _lower_resource_id(item, "bootstrap_identity_evidence.receipt_invalid")
            for item in checked_jobs
        }
        != {
            _lower_resource_id(item, "bootstrap_identity_evidence.receipt_invalid")
            for item in policy["job_inventory"]["exact_jobs"].values()
        }
        or value["evidence_files"] != expected_file_hashes
        or safety
        != {
            "no_second_apply": True,
            "no_destroy_or_teardown_executed": True,
            "no_federation_created": True,
            "no_role_assignment_created": True,
            "no_job_attachment_created": True,
            "cleanup_deadline_is_hard_gate": True,
        }
    ):
        _fail("bootstrap_identity_evidence.receipt_invalid")


def _state_address(resource: Any) -> str:
    if type(resource) is not dict:
        _fail("bootstrap_identity_evidence.state_invalid")
    try:
        return f"{resource['mode']}.{resource['type']}.{resource['name']}"
    except (KeyError, TypeError):
        _fail("bootstrap_identity_evidence.state_invalid")


def _validate_state(state: Any, policy: Mapping[str, Any]) -> None:
    value = _mapping(
        state,
        {
            "version",
            "terraform_version",
            "serial",
            "lineage",
            "outputs",
            "resources",
            "check_results",
        },
        "bootstrap_identity_evidence.state_invalid",
    )
    state_policy = policy["state"]
    identity = policy["identity"]
    foundation = policy["foundation"]
    resources = value["resources"]
    outputs = value["outputs"]
    if (
        value["version"] != 4
        or value["terraform_version"] != state_policy["opentofu_version"]
        or value["lineage"] != state_policy["lineage"]
        or value["serial"] != state_policy["post_apply_serial"]
        or type(resources) is not list
        or [_state_address(item) for item in resources]
        != state_policy["exact_resource_addresses"]
        or any(
            type(item.get("instances")) is not list or len(item["instances"]) != 1
            for item in resources
        )
        or any(type(item["instances"][0]) is not dict for item in resources)
        or type(outputs) is not dict
        or list(outputs) != state_policy["exact_output_keys"]
        or any(type(item) is not dict for item in outputs.values())
    ):
        _fail("bootstrap_identity_evidence.state_invalid")
    identity_state = outputs["sanitizer_bootstrap_identity_foundation"].get("value")
    state_boundary = outputs["state_boundary"].get("value")
    if (
        type(identity_state) is not dict
        or identity_state.get("name") != identity["name"]
        or _lower_resource_id(
            identity_state.get("resource_id"),
            "bootstrap_identity_evidence.state_invalid",
        )
        != _lower_resource_id(
            identity["resource_id"], "bootstrap_identity_evidence.state_invalid"
        )
        or identity_state.get("client_id") != identity["client_id"]
        or identity_state.get("principal_id") != identity["principal_id"]
        or identity_state.get("tenant_id") != identity["tenant_id"]
        or identity_state.get("rehearsal_id") != foundation["rehearsal_id"]
        or identity_state.get("cleanup_not_after") != foundation["cleanup_not_after"]
        or identity_state.get("lifecycle") != "ephemeral"
        or identity_state.get("destruction_protected") is not True
        or identity_state.get("federated_credential_ids") != []
        or identity_state.get("role_assignment_ids") != []
        or identity_state.get("attached_job_resource_ids") != []
        or any(
            identity_state.get(name) is not False
            for name in (
                "database_authority_bound",
                "exact_ids_authenticated",
                "provider_authority_bound",
                "single_use_job_bound",
                "workflow_binding_implemented",
            )
        )
        or state_boundary
        != {
            "container": "cosmaneura-tfstate",
            "key": state_policy["backend_key"],
            "resource_group": "cosmaneura-tfstate",
            "storage_account": "cosmaneuratfstate",
        }
    ):
        _fail("bootstrap_identity_evidence.state_invalid")
    managed = resources[1]["instances"][0].get("attributes", {})
    guard = resources[2]["instances"][0].get("attributes", {})
    guard_value = {
        "cleanup_not_after": foundation["cleanup_not_after"],
        "enabled": True,
        "exact_plan_approved": True,
        "lifetime_hours": 6,
        "rehearsal_id": foundation["rehearsal_id"],
    }
    if (
        managed.get("name") != identity["name"]
        or _lower_resource_id(
            managed.get("id"), "bootstrap_identity_evidence.state_invalid"
        )
        != _lower_resource_id(
            identity["resource_id"], "bootstrap_identity_evidence.state_invalid"
        )
        or managed.get("client_id") != identity["client_id"]
        or managed.get("principal_id") != identity["principal_id"]
        or managed.get("tenant_id") != identity["tenant_id"]
        or managed.get("resource_group_name") != identity["resource_group"]
        or managed.get("location") != identity["location"]
        or managed.get("isolation_scope") != ""
        or managed.get("tags") != identity["tags"]
        or guard.get("input", {}).get("value") != guard_value
        or guard.get("output", {}).get("value") != guard_value
    ):
        _fail("bootstrap_identity_evidence.state_invalid")
    checks = value["check_results"]
    if (
        type(checks) is not list
        or any(type(item) is not dict for item in checks)
        or sorted(item.get("config_addr") for item in checks)
        != sorted(state_policy["required_passing_checks"])
        or any(item.get("status") != "pass" for item in checks)
    ):
        _fail("bootstrap_identity_evidence.state_invalid")


def _validate_live_identity(value: Any, policy: Mapping[str, Any]) -> None:
    identity = policy["identity"]
    observed = _mapping(
        value,
        {
            "assignmentRestrictions",
            "clientId",
            "id",
            "isolationScope",
            "location",
            "name",
            "principalId",
            "resourceGroup",
            "tags",
            "tenantId",
            "type",
        },
        "bootstrap_identity_evidence.identity_invalid",
    )
    if (
        observed["assignmentRestrictions"] != identity["assignment_restrictions"]
        or observed["clientId"] != identity["client_id"]
        or _lower_resource_id(
            observed["id"], "bootstrap_identity_evidence.identity_invalid"
        )
        != _lower_resource_id(
            identity["resource_id"], "bootstrap_identity_evidence.identity_invalid"
        )
        or observed["isolationScope"] != identity["isolation_scope"]
        or observed["location"] != identity["location"]
        or observed["name"] != identity["name"]
        or observed["principalId"] != identity["principal_id"]
        or observed["resourceGroup"] != identity["resource_group"]
        or observed["tags"] != identity["tags"]
        or observed["tenantId"] != identity["tenant_id"]
        or observed["type"] != identity["type"]
    ):
        _fail("bootstrap_identity_evidence.identity_invalid")


def _validate_negative_authority(
    documents: Mapping[str, Any], policy: Mapping[str, Any]
) -> None:
    if documents[FEDERATED_FILE] != []:
        _fail("bootstrap_identity_evidence.federation_present")
    if documents[ROLES_FILE] != []:
        _fail("bootstrap_identity_evidence.role_assignment_present")
    jobs = policy["job_inventory"]["exact_jobs"]
    inventory = documents[INVENTORY_FILE]
    if (
        type(inventory) is not list
        or len(inventory) != len(JOB_FILES)
        or any(type(item) is not str for item in inventory)
        or len(set(inventory)) != len(JOB_FILES)
        or {
            _lower_resource_id(item, "bootstrap_identity_evidence.job_invalid")
            for item in inventory
        }
        != {
            _lower_resource_id(item, "bootstrap_identity_evidence.job_invalid")
            for item in jobs.values()
        }
    ):
        _fail("bootstrap_identity_evidence.job_invalid")
    bootstrap_id = _lower_resource_id(
        policy["identity"]["resource_id"], "bootstrap_identity_evidence.job_invalid"
    )
    for name, expected_id in jobs.items():
        job = _mapping(
            documents[name],
            {"id", "identity"},
            "bootstrap_identity_evidence.job_invalid",
        )
        identity = job["identity"]
        if (
            _lower_resource_id(job["id"], "bootstrap_identity_evidence.job_invalid")
            != _lower_resource_id(
                expected_id, "bootstrap_identity_evidence.job_invalid"
            )
            or type(identity) is not dict
            or set(identity)
            != {
                "principalId",
                "tenantId",
                "type",
                "userAssignedIdentities",
            }
        ):
            _fail("bootstrap_identity_evidence.job_invalid")
        attached = identity["userAssignedIdentities"]
        if attached is not None and (
            type(attached) is not dict
            or bootstrap_id
            in {
                _lower_resource_id(item, "bootstrap_identity_evidence.job_invalid")
                for item in attached
            }
        ):
            _fail("bootstrap_identity_evidence.bootstrap_attachment_present")


def _binding_material(
    policy: Mapping[str, Any], evidence_digests: Mapping[str, str]
) -> dict[str, Any]:
    identity = policy["identity"]
    return {
        "policy_sha256": f"sha256:{POLICY_SHA256}",
        "foundation_source_commit": policy["foundation"]["source_commit"],
        "post_apply_receipt_sha256": evidence_digests[RECEIPT_FILE],
        "rehearsal_id": policy["foundation"]["rehearsal_id"],
        "state_lineage": policy["state"]["lineage"],
        "state_serial": policy["state"]["post_apply_serial"],
        "identity": {
            "resource_id": identity["resource_id"].lower(),
            "client_id": identity["client_id"],
            "principal_id": identity["principal_id"],
            "tenant_id": identity["tenant_id"],
        },
        "evidence_digests": dict(evidence_digests),
    }


@dataclass(frozen=True, slots=True, init=False, repr=False)
class AuthenticatedBootstrapIdentityEvidence:
    """Process-local evidence binding; it grants no live authority."""

    kind: str
    identity_binding_sha256: str
    policy_sha256: str
    foundation_source_commit: str
    post_apply_receipt_sha256: str
    rehearsal_id: str
    authenticated_at: datetime
    expires_at: datetime
    retrospective_evidence_authenticated: bool
    bootstrap_identity_selected: bool
    bootstrap_identity_exact_ids_pinned: bool
    bootstrap_identity_current_live_state_authenticated: bool
    bootstrap_identity_bound_to_single_use_job: bool
    bootstrap_authority_authenticated: bool
    database_connection_authority: bool
    database_mutation_authority: bool
    provider_mutation_authority: bool
    workflow_integrated: bool
    executable: bool
    _identity: tuple[tuple[str, str], ...] = dataclass_field(repr=False)
    _evidence_digests: tuple[tuple[str, str], ...] = dataclass_field(repr=False)
    _proof_sha256: str = dataclass_field(repr=False)
    _seal: object = dataclass_field(repr=False, compare=False)

    def __init__(self) -> None:
        _fail("bootstrap_identity_evidence.output_invalid")

    def __copy__(self) -> NoReturn:
        _fail("bootstrap_identity_evidence.output_invalid")

    def __deepcopy__(self, _memo: Any) -> NoReturn:
        _fail("bootstrap_identity_evidence.output_invalid")

    def __reduce_ex__(self, _protocol: SupportsIndex) -> NoReturn:
        _fail("bootstrap_identity_evidence.output_invalid")

    def _is_authenticated(self) -> bool:
        try:
            policy = load_bootstrap_identity_evidence_policy()
            expected_expiry = _parse_utc(
                policy["foundation"]["cleanup_not_after"],
                "bootstrap_identity_evidence.policy_invalid",
            )
            identity = dict(self._identity)
            evidence_digests = dict(self._evidence_digests)
            expected_material = _binding_material(policy, evidence_digests)
            if (
                type(self) is not AuthenticatedBootstrapIdentityEvidence
                or self._seal is not _SEAL
                or self.kind != KIND
                or self.policy_sha256 != f"sha256:{POLICY_SHA256}"
                or type(self._identity) is not tuple
                or type(self._evidence_digests) is not tuple
                or self._evidence_digests != tuple(policy["evidence"]["files"].items())
                or not _verify_provenance(self)
                or identity != expected_material["identity"]
                or self.foundation_source_commit
                != expected_material["foundation_source_commit"]
                or self.post_apply_receipt_sha256
                != expected_material["post_apply_receipt_sha256"]
                or self.rehearsal_id != expected_material["rehearsal_id"]
                or type(self.authenticated_at) is not datetime
                or self.authenticated_at.tzinfo is None
                or self.authenticated_at.utcoffset() != timedelta(0)
                or self.authenticated_at.microsecond != 0
                or type(self.expires_at) is not datetime
                or self.expires_at != expected_expiry
                or self.authenticated_at >= self.expires_at
                or any(
                    getattr(self, name) is not expected
                    for name, expected in CLAIMS.items()
                )
            ):
                return False
            if self.identity_binding_sha256 != _digest(_canonical(expected_material)):
                return False
            checked = _trusted_now(_utc_now)
            if checked < self.authenticated_at or checked >= self.expires_at:
                return False
            return True
        except Exception:
            return False


def _validated_candidate(
    *,
    policy: Mapping[str, Any],
    evidence_directory: Path,
    clock: Callable[[], datetime],
) -> tuple[AuthenticatedBootstrapIdentityEvidence, datetime]:
    document = _validate_policy(policy)
    if not isinstance(evidence_directory, Path) or not evidence_directory.is_absolute():
        _fail("bootstrap_identity_evidence.directory_invalid")
    try:
        directory_state = evidence_directory.lstat()
    except OSError:
        _fail("bootstrap_identity_evidence.directory_invalid")
    if (
        not stat.S_ISDIR(directory_state.st_mode)
        or directory_state.st_uid not in {0, os.geteuid()}
        or stat.S_IMODE(directory_state.st_mode) & 0o077
    ):
        _fail("bootstrap_identity_evidence.directory_invalid")
    before = _trusted_now(clock)
    expires_at = _parse_utc(
        document["foundation"]["cleanup_not_after"],
        "bootstrap_identity_evidence.policy_invalid",
    )
    if before >= expires_at:
        _fail("bootstrap_identity_evidence.expired")
    evidence_digests = document["evidence"]["files"]
    raw = {
        name: _read_exact_evidence(
            evidence_directory,
            name,
            evidence_digests[name],
            document["evidence"]["maximum_file_bytes"],
        )
        for name in EVIDENCE_FILES
    }
    parsed = {
        name: _parse_json(value, "bootstrap_identity_evidence.file_invalid")
        for name, value in raw.items()
    }
    _validate_receipt(parsed[RECEIPT_FILE], document, evidence_digests)
    _validate_state(parsed[STATE_FILE], document)
    _validate_state(parsed[NORMALIZED_STATE_FILE], document)
    normalized_raw_state = dict(parsed[STATE_FILE])
    normalized_raw_state["check_results"] = sorted(
        normalized_raw_state["check_results"], key=lambda item: item["config_addr"]
    )
    if _canonical(normalized_raw_state) != _canonical(parsed[NORMALIZED_STATE_FILE]):
        _fail("bootstrap_identity_evidence.state_invalid")
    _validate_live_identity(parsed[IDENTITY_FILE], document)
    _validate_negative_authority(parsed, document)
    after = _trusted_now(clock)
    if after < before:
        _fail("bootstrap_identity_evidence.clock_regressed")
    if after >= expires_at:
        _fail("bootstrap_identity_evidence.expired")
    material = _binding_material(document, evidence_digests)
    result = object.__new__(AuthenticatedBootstrapIdentityEvidence)
    values = {
        "kind": KIND,
        "identity_binding_sha256": _digest(_canonical(material)),
        "policy_sha256": f"sha256:{POLICY_SHA256}",
        "foundation_source_commit": document["foundation"]["source_commit"],
        "post_apply_receipt_sha256": evidence_digests[RECEIPT_FILE],
        "rehearsal_id": document["foundation"]["rehearsal_id"],
        "authenticated_at": after,
        "expires_at": expires_at,
        **CLAIMS,
        "_identity": tuple(material["identity"].items()),
        "_evidence_digests": tuple(evidence_digests.items()),
        "_proof_sha256": "",
        "_seal": _SEAL,
    }
    for name, value in values.items():
        object.__setattr__(result, name, value)
    return result, after


def _build_provenance() -> tuple[Callable[..., Any], Callable[..., bool]]:
    key = os.urandom(32)
    issued: dict[tuple[int, int], object] = {}

    def proof(value: AuthenticatedBootstrapIdentityEvidence) -> str:
        material = _canonical(
            {
                "domain": PROVENANCE_DOMAIN,
                "object_identity": id(value),
                "identity_binding_sha256": value.identity_binding_sha256,
                "authenticated_at": value.authenticated_at.strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                ),
                "expires_at": value.expires_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
        )
        return hmac.new(key, material, hashlib.sha256).hexdigest()

    def verify(value: AuthenticatedBootstrapIdentityEvidence) -> bool:
        return issued.get((os.getpid(), id(value))) is value and hmac.compare_digest(
            value._proof_sha256, proof(value)
        )

    def authenticate(
        *, evidence_directory: Path
    ) -> AuthenticatedBootstrapIdentityEvidence:
        policy, _raw = _read_policy()
        value, _authenticated_at = _validated_candidate(
            policy=policy, evidence_directory=evidence_directory, clock=_utc_now
        )
        object.__setattr__(value, "_proof_sha256", proof(value))
        issued[os.getpid(), id(value)] = value
        if not value._is_authenticated():
            issued.pop((os.getpid(), id(value)), None)
            _fail("bootstrap_identity_evidence.output_invalid")
        return value

    return authenticate, verify


_authenticate_exact_evidence, _verify_provenance = _build_provenance()


def authenticate_bootstrap_identity_evidence(
    *, evidence_directory: Path
) -> AuthenticatedBootstrapIdentityEvidence:
    """Authenticate the exact checked-in evidence set before its hard expiry."""

    return _authenticate_exact_evidence(evidence_directory=evidence_directory)


def _public_projection(value: AuthenticatedBootstrapIdentityEvidence) -> dict[str, Any]:
    if not value._is_authenticated():
        _fail("bootstrap_identity_evidence.output_invalid")
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
        result = authenticate_bootstrap_identity_evidence(
            evidence_directory=args.evidence_directory
        )
        sys.stdout.buffer.write(_canonical(_public_projection(result)) + b"\n")
    except BootstrapIdentityEvidenceFailure as error:
        print(str(error), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
