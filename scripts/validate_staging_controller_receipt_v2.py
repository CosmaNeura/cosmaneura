#!/usr/bin/env python3
"""Strictly validate controller-receipt-v2 content without authenticating it.

This module has no network, credential, source-reader, producer, or activation
path. Its sealed result proves only that this validator accepted exact bytes; it
does not prove where those bytes came from or that provider claims are true.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, NoReturn

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "contracts/staging-rehearsal/v1/controller-receipt-v2-policy.json"
POLICY_SHA256 = "ba57d516cbc9e44b7256c9f30409a1316567ad7822056bddc0ab668660cfbb73"
MAXIMUM_POLICY_BYTES = 128 * 1024

DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
GIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
REHEARSAL_RE = re.compile(r"^[0-9a-f]{12}$")
RUN_RE = re.compile(r"^[1-9][0-9]{0,19}$")
MANIFEST_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{11,63}$")
REVISION_RE = re.compile(r"^[0-9a-f]{12}$")
UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
UTC_RE = re.compile(r"^20[0-9]{2}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
EXECUTION_NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
ROLE_RE = re.compile(r"^[a-z][a-z0-9_]{2,62}$")
FQDN_RE = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$"
)
IMAGE_RE = re.compile(r"^[a-z0-9.-]+\.azurecr\.io/[a-z0-9._/-]+@sha256:[0-9a-f]{64}$")
PLACEHOLDER_RE = re.compile(r"^CONFIGURE_EXACT_[A-Z0-9_]+$")
AZURE_SEGMENT_RE = re.compile(r"^[A-Za-z0-9._()-]+$")

TOP_LEVEL_FIELDS = (
    "schema_version",
    "kind",
    "policy_id",
    "policy_binding_sha256",
    "repository",
    "policy_pins",
    "identity",
    "authorization",
    "launch",
    "source",
    "target",
    "restore_observed_at",
    "created_at",
    "expires_at",
    "cleanup_not_after",
    "isolation",
    "pass",
)
POLICY_PIN_FIELDS = (
    "safe_submit_policy_id",
    "safe_submit_policy_sha256",
    "restore_envelope_policy_id",
    "restore_envelope_policy_sha256",
)
IDENTITY_FIELDS = (
    "tenant_id",
    "subscription_id",
    "provision_job_resource_id",
    "provision_job_uami_resource_id",
    "provision_job_uami_client_id",
    "provision_job_uami_principal_id",
)
AUTHORIZATION_FIELDS = (
    "rehearsal_id",
    "authorization_id",
    "evidence_binding_sha256",
    "intent_sha256",
    "submission_sha256",
    "restore_request_sha256",
)
LAUNCH_FIELDS = (
    "launch_record_sha256",
    "broker_launch_receipt_sha256",
    "runtime_record_sha256",
    "runtime_signature_bundle_sha256",
    "execution_resource_id",
    "execution_name",
    "launch_expires_at",
    "provision_job_configuration_template_sha256",
    "provision_job_image_reference",
)
SOURCE_FIELDS = (
    "workflow_sha",
    "run_id",
    "run_attempt",
    "source_sha",
    "candidate_sha",
    "source_server_resource_id",
    "source_database_name",
    "approved_restore_time",
)
RUNTIME_FIELDS = (
    "target_fqdn",
    "approved_database_role",
    "target_postgres_server_id",
    "target_vnet_id",
    "target_subnet_id",
    "target_private_endpoint_id",
    "target_network_interface_id",
    "target_private_dns_zone_id",
    "target_private_dns_link_id",
    "target_private_dns_zone_group_id",
    "executor_private_endpoint_id",
    "executor_network_interface_id",
    "executor_private_dns_zone_id",
    "executor_private_dns_link_id",
    "executor_private_dns_zone_group_id",
)
TARGET_FIELDS = (*RUNTIME_FIELDS, "resource_set_sha256")
ISOLATION_FIELDS = (
    "public_access",
    "firewall_rule_count",
    "vnet_peering_count",
    "public_ip_count",
    "unknown_resource_count",
    "blob_copied",
    "redis_copied",
)
INTENT_FIELDS = (
    "schema_version",
    "policy_id",
    "envelope_policy_id",
    "envelope_policy_sha256",
    "authorization_id",
    "evidence_binding",
    "rehearsal_id",
    "source_server_resource_id",
    "provision_client_id",
    "provision_principal_id",
    "target_server_resource_id",
    "target_server_name",
    "location",
    "restore_time",
    "created_at",
    "cleanup_not_after",
    "repository",
    "workflow_sha",
    "run_id",
    "run_attempt",
    "required_tags",
)
EVIDENCE_FIELDS = (
    "schema_version",
    "binding_policy_id",
    "evidence_manifest_policy_id",
    "evidence_manifest_policy_sha256",
    "manifest_id",
    "manifest_file_sha256",
    "bundle_index_sha256",
    "evidence_set_sha256",
    "source_sha",
    "candidate_sha",
    "source_server_resource_id",
    "source_database_name",
    "approved_restore_time",
    "observed_at",
    "expires_at",
    "candidate_approval_pr_number",
    "candidate_protected_master_sha",
    "candidate_schema_revision",
    "candidate_receipt_sha256",
    "inventory_receipt_sha256",
    "quiescence_receipt_sha256",
    "server_scope_receipt_sha256",
    "repository",
    "protected_ref",
    "workflow_path",
    "workflow_sha",
    "run_id",
    "run_attempt",
    "event_name",
    "signer_issuer",
    "signer_subject",
    "attestation_receipt_sha256",
    "provider_receipt_sha256",
    "server_scope_authentication_receipt_sha256",
)
SUBMISSION_FIELDS = (
    "schema_version",
    "kind",
    "policy_id",
    "rehearsal_id",
    "intent_sha256",
    "target_server_resource_id",
    "restore_request_sha256",
    "authorized_at",
)
DYNAMIC_SOURCE_FIELDS = (
    "account_resource_id",
    "account_host",
    "private_endpoint_resource_id",
    "writer_identity_resource_id",
    "reader_identity_resource_ids",
    "writer_role_assignment_id",
    "reader_role_assignment_ids",
    "create_role_definition_id",
    "create_role_permissions_sha256",
    "read_role_definition_id",
    "read_role_permissions_sha256",
    "controller_immutability_policy_resource_id",
    "immutability_configuration_evidence_sha256",
    "container_resource_ids",
)


class ControllerReceiptV2Failure(RuntimeError):
    """A bounded refusal that never includes receipt or policy contents."""


def _fail(code: str) -> NoReturn:
    raise ControllerReceiptV2Failure(code)


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeEncodeError, RecursionError):
        _fail("controller_receipt.content_invalid")


def _digest(raw: bytes) -> str:
    return f"sha256:{hashlib.sha256(raw).hexdigest()}"


def _parse_canonical(raw: Any, maximum: int) -> dict[str, Any]:
    if type(raw) is not bytes or not 1 <= len(raw) <= maximum:
        _fail("controller_receipt.content_invalid")

    def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in values:
            if key in result:
                _fail("controller_receipt.content_invalid")
            result[key] = value
        return result

    try:
        value = json.loads(
            raw,
            object_pairs_hook=pairs,
            parse_constant=lambda _value: _fail("controller_receipt.content_invalid"),
            parse_float=lambda text: (
                number
                if math.isfinite(number := float(text))
                else _fail("controller_receipt.content_invalid")
            ),
        )
    except ControllerReceiptV2Failure:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError):
        _fail("controller_receipt.content_invalid")
    if type(value) is not dict or raw != _canonical(value):
        _fail("controller_receipt.content_invalid")
    return value


def _object(value: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    if type(value) is not dict or set(value) != set(fields):
        _fail("controller_receipt.content_invalid")
    return value


def _utc(value: Any) -> datetime:
    if type(value) is not str or UTC_RE.fullmatch(value) is None:
        _fail("controller_receipt.content_invalid")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        _fail("controller_receipt.content_invalid")
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        _fail("controller_receipt.content_invalid")
    return parsed


def _trusted_now(clock: Any) -> datetime:
    if not callable(clock):
        _fail("controller_receipt.clock_invalid")
    try:
        value = clock()
    except Exception:
        raise ControllerReceiptV2Failure("controller_receipt.clock_invalid") from None
    if (
        type(value) is not datetime
        or value.tzinfo is None
        or value.utcoffset() != timedelta(0)
        or value.microsecond != 0
    ):
        _fail("controller_receipt.clock_invalid")
    return value.astimezone(UTC)


def _azure_id(value: Any) -> bool:
    if type(value) is not str or len(value) > 2_048 or value.endswith("/"):
        return False
    if any(token in value for token in ("//", "%", "?", "#")):
        return False
    parts = value.split("/")
    if (
        len(parts) < 4
        or parts[:2] != ["", "subscriptions"]
        or UUID_RE.fullmatch(parts[2]) is None
    ):
        return False
    index = 3
    if parts[index].casefold() == "resourcegroups":
        if len(parts) < 5 or AZURE_SEGMENT_RE.fullmatch(parts[4]) is None:
            return False
        index = 5
        if index == len(parts):
            return True
    provider = (
        parts[index + 1 :]
        if index < len(parts) and parts[index].casefold() == "providers"
        else []
    )
    return (
        len(provider) >= 3
        and len(provider) % 2 == 1
        and all(AZURE_SEGMENT_RE.fullmatch(part) for part in provider)
    )


def _resource_type(value: str) -> str:
    parts = value.split("/")
    try:
        provider = parts[parts.index("providers") + 1 :]
    except ValueError:
        provider = []
    return "/".join((provider[0], *provider[1::2])).casefold() if provider else ""


def _resource_group(value: str) -> str:
    return "/".join(value.split("/")[:5])


def _resource_group_id(value: Any) -> bool:
    return _azure_id(value) and len(value.split("/")) == 5


def _parent_id(value: str) -> str:
    return value.rsplit("/", 2)[0]


def _configured_or(value: Any, predicate: Callable[[Any], bool]) -> bool:
    return type(value) is str and (
        PLACEHOLDER_RE.fullmatch(value) is not None or predicate(value)
    )


def _policy_binding_digest(policy: Mapping[str, Any]) -> str:
    return _digest(
        _canonical({key: value for key, value in policy.items() if key != "status"})
    )


def _read_pinned_policy() -> dict[str, Any]:
    descriptor = -1
    try:
        before = POLICY_PATH.lstat()
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid not in {0, os.geteuid()}
            or stat.S_IMODE(before.st_mode) & 0o022
            or not 1 <= before.st_size <= MAXIMUM_POLICY_BYTES
        ):
            _fail("controller_receipt.policy_invalid")
        descriptor = os.open(
            POLICY_PATH,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        after = os.fstat(descriptor)
        raw = os.read(descriptor, MAXIMUM_POLICY_BYTES + 1)
        if (
            (before.st_dev, before.st_ino, before.st_size)
            != (after.st_dev, after.st_ino, after.st_size)
            or len(raw) != before.st_size
            or os.read(descriptor, 1)
        ):
            _fail("controller_receipt.policy_invalid")
    except ControllerReceiptV2Failure:
        raise
    except OSError:
        raise ControllerReceiptV2Failure("controller_receipt.policy_invalid") from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if hashlib.sha256(raw).hexdigest() != POLICY_SHA256:
        _fail("controller_receipt.policy_invalid")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError):
        _fail("controller_receipt.policy_invalid")
    if type(value) is not dict:
        _fail("controller_receipt.policy_invalid")
    return value


def _validate_policy_against_template(
    policy: Any,
    template: Mapping[str, Any],
    *,
    executable: bool,
) -> dict[str, Any]:
    if type(policy) is not dict or type(executable) is not bool:
        _fail("controller_receipt.policy_invalid")
    try:
        document = json.loads(_canonical(policy))
        normalized = json.loads(_canonical(document))
    except ControllerReceiptV2Failure:
        raise ControllerReceiptV2Failure("controller_receipt.policy_invalid") from None
    source = document.get("record_chain_source")
    fixed = document.get("fixed_bindings")
    if type(source) is not dict or type(fixed) is not dict:
        _fail("controller_receipt.policy_invalid")
    try:
        for name in DYNAMIC_SOURCE_FIELDS:
            normalized["record_chain_source"][name] = template["record_chain_source"][
                name
            ]
        for name in (
            "executor_resource_group_id",
            "source_server_resource_id",
            "target_resource_group_id",
            "approved_database_role",
            "tenant_id",
            "subscription_id",
            "provision_job_resource_id",
            "provision_job_uami_client_id",
            "provision_job_uami_principal_id",
        ):
            normalized["fixed_bindings"][name] = template["fixed_bindings"][name]
    except (KeyError, TypeError):
        _fail("controller_receipt.policy_invalid")
    if _canonical(normalized) != _canonical(template):
        _fail("controller_receipt.policy_invalid")
    if executable:
        _fail("controller_receipt.policy_disabled")

    resource_names = (
        "account_resource_id",
        "private_endpoint_resource_id",
        "writer_identity_resource_id",
        "writer_role_assignment_id",
        "create_role_definition_id",
        "read_role_definition_id",
        "controller_immutability_policy_resource_id",
    )
    digest_names = (
        "create_role_permissions_sha256",
        "read_role_permissions_sha256",
        "immutability_configuration_evidence_sha256",
    )
    readers = source.get("reader_identity_resource_ids")
    reader_roles = source.get("reader_role_assignment_ids")
    containers = source.get("container_resource_ids")
    if (
        any(not _configured_or(source.get(name), _azure_id) for name in resource_names)
        or any(
            not _configured_or(
                source.get(name), lambda item: DIGEST_RE.fullmatch(item) is not None
            )
            for name in digest_names
        )
        or not _configured_or(
            source.get("account_host"),
            lambda item: re.fullmatch(r"[a-z0-9]{3,24}\.blob\.core\.windows\.net", item)
            is not None,
        )
        or type(readers) is not dict
        or set(readers) != {"provision", "cleanup"}
        or any(not _configured_or(item, _azure_id) for item in readers.values())
        or readers["provision"] != source.get("writer_identity_resource_id")
        or type(reader_roles) is not dict
        or set(reader_roles) != {"provision", "cleanup"}
        or any(not _configured_or(item, _azure_id) for item in reader_roles.values())
        or type(containers) is not dict
        or set(containers) != {"intent", "submission", "controller_receipt"}
        or any(not _configured_or(item, _azure_id) for item in containers.values())
        or not _configured_or(
            fixed.get("executor_resource_group_id"), _resource_group_id
        )
        or not _configured_or(
            fixed.get("source_server_resource_id"),
            lambda item: _azure_id(item)
            and _resource_type(item) == "microsoft.dbforpostgresql/flexibleservers",
        )
        or not _configured_or(fixed.get("target_resource_group_id"), _resource_group_id)
        or not _configured_or(
            fixed.get("approved_database_role"),
            lambda item: ROLE_RE.fullmatch(item) is not None,
        )
        or any(
            not _configured_or(
                fixed.get(name), lambda item: UUID_RE.fullmatch(item) is not None
            )
            for name in (
                "tenant_id",
                "subscription_id",
                "provision_job_uami_client_id",
                "provision_job_uami_principal_id",
            )
        )
        or not _configured_or(
            fixed.get("provision_job_resource_id"),
            lambda item: _azure_id(item)
            and _resource_type(item) == "microsoft.app/jobs",
        )
    ):
        _fail("controller_receipt.policy_invalid")
    if not any(
        PLACEHOLDER_RE.fullmatch(str(item))
        for item in (source["account_resource_id"], *containers.values())
    ):
        expected = {
            name: f"{source['account_resource_id']}/blobServices/default/containers/{container}"
            for name, container in source["container_names"].items()
        }
        if containers != expected:
            _fail("controller_receipt.policy_invalid")
    return document


def validate_controller_receipt_v2_policy(
    policy: Any,
    *,
    executable: bool = False,
) -> dict[str, Any]:
    """Validate configured pins against the exact digest-pinned dormant template."""

    return _validate_policy_against_template(
        policy,
        _read_pinned_policy(),
        executable=executable,
    )


def load_controller_receipt_v2_policy() -> dict[str, Any]:
    """Load only the repository-pinned policy path; never a caller path."""

    template = _read_pinned_policy()
    return _validate_policy_against_template(template, template, executable=False)


_VALIDATED_CONTENT_SEAL = object()


@dataclass(frozen=True, slots=True, init=False)
class ValidatedControllerReceiptV2Content:
    """Exact accepted content. The seal is validator provenance, not source auth."""

    intent_bytes: bytes = dataclass_field(repr=False)
    submission_bytes: bytes = dataclass_field(repr=False)
    receipt_bytes: bytes = dataclass_field(repr=False)
    intent_sha256: str
    submission_sha256: str
    receipt_sha256: str
    policy_binding_sha256: str
    target_fqdn: str
    approved_database_role: str
    target_postgres_server_id: str
    target_vnet_id: str
    target_subnet_id: str
    target_private_endpoint_id: str
    target_network_interface_id: str
    target_private_dns_zone_id: str
    target_private_dns_link_id: str
    target_private_dns_zone_group_id: str
    executor_private_endpoint_id: str
    executor_network_interface_id: str
    executor_private_dns_zone_id: str
    executor_private_dns_link_id: str
    executor_private_dns_zone_group_id: str
    expires_at: str
    cleanup_not_after: str
    provenance_authenticated: bool
    _policy_bytes: bytes = dataclass_field(repr=False)
    _seal: object = dataclass_field(repr=False, compare=False)

    def __init__(self) -> None:
        _fail("controller_receipt.content_invalid")


def _validate_evidence(evidence: Any) -> tuple[datetime, datetime]:
    evidence = _object(evidence, EVIDENCE_FIELDS)
    digest_fields = tuple(name for name in EVIDENCE_FIELDS if name.endswith("_sha256"))
    git_fields = (
        "source_sha",
        "candidate_sha",
        "candidate_protected_master_sha",
        "workflow_sha",
    )
    if (
        type(evidence["schema_version"]) is not int
        or evidence["schema_version"] != 1
        or evidence["binding_policy_id"]
        != "cosmaneura-staging-restore-evidence-binding-v1"
        or evidence["evidence_manifest_policy_id"]
        != "cosmaneura-staging-evidence-manifest-v2"
        or evidence["evidence_manifest_policy_sha256"]
        != "sha256:8b1cd30a90782d5596b44415e0abb9e934c78aeaca9bc7960e226d1feee8df53"
        or type(evidence["manifest_id"]) is not str
        or MANIFEST_ID_RE.fullmatch(evidence["manifest_id"]) is None
        or any(
            type(evidence[name]) is not str
            or DIGEST_RE.fullmatch(evidence[name]) is None
            for name in digest_fields
        )
        or any(
            type(evidence[name]) is not str
            or GIT_SHA_RE.fullmatch(evidence[name]) is None
            for name in git_fields
        )
        or evidence["candidate_protected_master_sha"] != evidence["workflow_sha"]
        or not _azure_id(evidence["source_server_resource_id"])
        or evidence["source_database_name"] != "staging"
        or evidence["repository"] != "CosmaNeura/cosmaneura"
        or evidence["protected_ref"] != "refs/heads/master"
        or evidence["workflow_path"]
        != ".github/workflows/staging_restore_rehearsal.yml"
        or evidence["event_name"] != "workflow_dispatch"
        or evidence["signer_issuer"] != "https://token.actions.githubusercontent.com"
        or evidence["signer_subject"]
        != "repo:CosmaNeura/cosmaneura:environment:staging-restore-provision"
        or type(evidence["run_id"]) is not str
        or RUN_RE.fullmatch(evidence["run_id"]) is None
        or type(evidence["run_attempt"]) is not str
        or RUN_RE.fullmatch(evidence["run_attempt"]) is None
        or type(evidence["candidate_schema_revision"]) is not str
        or REVISION_RE.fullmatch(evidence["candidate_schema_revision"]) is None
        or type(evidence["candidate_approval_pr_number"]) is not int
        or evidence["candidate_approval_pr_number"] <= 0
    ):
        _fail("controller_receipt.content_invalid")
    observed = _utc(evidence["observed_at"])
    expires = _utc(evidence["expires_at"])
    if _utc(evidence["approved_restore_time"]) != observed or not (
        observed < expires <= observed + timedelta(seconds=900)
    ):
        _fail("controller_receipt.content_invalid")
    return observed, expires


def _restore_request_sha256(intent: Mapping[str, Any], intent_sha256: str) -> str:
    body = {
        "location": intent["location"],
        "tags": {**intent["required_tags"], "restore-intent-sha256": intent_sha256[7:]},
        "properties": {
            "createMode": "PointInTimeRestore",
            "sourceServerResourceId": intent["source_server_resource_id"],
            "pointInTimeUTC": intent["restore_time"],
            "network": {"publicNetworkAccess": "Disabled"},
        },
    }
    return _digest(_canonical(body))


def _validate_controller_receipt_v2_content_at(
    policy: Mapping[str, Any],
    *,
    intent_bytes: bytes,
    submission_bytes: bytes,
    receipt_bytes: bytes,
    now: datetime,
) -> ValidatedControllerReceiptV2Content:
    maximum = policy["receipt"]["maximum_bytes"]
    intent = _parse_canonical(intent_bytes, maximum)
    submission = _parse_canonical(submission_bytes, maximum)
    receipt = _parse_canonical(receipt_bytes, maximum)
    _object(intent, INTENT_FIELDS)
    _object(submission, SUBMISSION_FIELDS)
    _object(receipt, TOP_LEVEL_FIELDS)
    evidence = _object(intent["evidence_binding"], EVIDENCE_FIELDS)
    evidence_observed, evidence_expires = _validate_evidence(evidence)
    safe = policy["upstream_policies"]["safe_submit"]
    intent_sha256 = _digest(intent_bytes)
    submission_sha256 = _digest(submission_bytes)
    evidence_sha256 = _digest(_canonical(evidence))
    authorization_id = _digest(
        _canonical(
            {
                "schema_version": 1,
                "safe_submit_policy_id": safe["policy_id"],
                "evidence_binding": evidence,
            }
        )
    )
    rehearsal_id = authorization_id[7:19]
    intent_created = _utc(intent["created_at"])
    intent_cleanup = _utc(intent["cleanup_not_after"])
    intent_restore = _utc(intent["restore_time"])
    if (
        type(intent["schema_version"]) is not int
        or intent["schema_version"] != 4
        or intent["policy_id"] != safe["policy_id"]
        or intent["envelope_policy_id"]
        != policy["upstream_policies"]["restore_envelope"]["policy_id"]
        or intent["envelope_policy_sha256"]
        != policy["upstream_policies"]["restore_envelope"]["sha256"]
        or intent["authorization_id"] != authorization_id
        or intent["rehearsal_id"] != rehearsal_id
        or intent["source_server_resource_id"] != evidence["source_server_resource_id"]
        or intent["repository"] != evidence["repository"]
        or intent["workflow_sha"] != evidence["workflow_sha"]
        or intent["run_id"] != evidence["run_id"]
        or intent["run_attempt"] != evidence["run_attempt"]
        or UUID_RE.fullmatch(str(intent["provision_client_id"])) is None
        or UUID_RE.fullmatch(str(intent["provision_principal_id"])) is None
        or intent_restore != _utc(evidence["approved_restore_time"])
        or intent_created != evidence_observed
        or not intent_created < evidence_expires
        or intent_cleanup <= intent_created
        or intent_cleanup
        > intent_created
        + timedelta(
            seconds=policy["freshness"]["maximum_intent_cleanup_lifetime_seconds"]
        )
        or intent["location"] != policy["fixed_bindings"]["restore_location"]
    ):
        _fail("controller_receipt.content_invalid")
    expected_tags = {
        "environment": "staging-restore-rehearsal",
        "data-classification": "restricted-restored-copy",
        "managed-by": "staging-restore-envelope-v1",
        "expires-epoch": str(int(intent_cleanup.timestamp())),
        "rehearsal-id": rehearsal_id,
    }
    expected_target_name = f"cnr-{rehearsal_id}-e{int(intent_cleanup.timestamp())}-pg"
    if (
        intent["required_tags"] != expected_tags
        or intent["target_server_name"] != expected_target_name
        or not _azure_id(intent["target_server_resource_id"])
        or intent["target_server_resource_id"].split("/")[-1] != expected_target_name
    ):
        _fail("controller_receipt.content_invalid")
    restore_request_sha256 = _restore_request_sha256(intent, intent_sha256)
    expected_submission = {
        "schema_version": 4,
        "kind": "restore-submission-authorized",
        "policy_id": safe["policy_id"],
        "rehearsal_id": rehearsal_id,
        "intent_sha256": intent_sha256,
        "target_server_resource_id": intent["target_server_resource_id"],
        "restore_request_sha256": restore_request_sha256,
        "authorized_at": intent["created_at"],
    }
    if _canonical(submission) != _canonical(expected_submission):
        _fail("controller_receipt.content_invalid")

    sections = {
        "policy_pins": POLICY_PIN_FIELDS,
        "identity": IDENTITY_FIELDS,
        "authorization": AUTHORIZATION_FIELDS,
        "launch": LAUNCH_FIELDS,
        "source": SOURCE_FIELDS,
        "target": TARGET_FIELDS,
        "isolation": ISOLATION_FIELDS,
    }
    values = {name: _object(receipt[name], fields) for name, fields in sections.items()}
    identity, launch = values["identity"], values["launch"]
    source, target, isolation = values["source"], values["target"], values["isolation"]
    upstream = policy["upstream_policies"]
    expected_sections = {
        "policy_pins": {
            "safe_submit_policy_id": upstream["safe_submit"]["policy_id"],
            "safe_submit_policy_sha256": upstream["safe_submit"]["sha256"],
            "restore_envelope_policy_id": upstream["restore_envelope"]["policy_id"],
            "restore_envelope_policy_sha256": upstream["restore_envelope"]["sha256"],
        },
        "authorization": {
            "rehearsal_id": rehearsal_id,
            "authorization_id": authorization_id,
            "evidence_binding_sha256": evidence_sha256,
            "intent_sha256": intent_sha256,
            "submission_sha256": submission_sha256,
            "restore_request_sha256": restore_request_sha256,
        },
        "source": {name: evidence[name] for name in SOURCE_FIELDS},
    }
    if any(values[name] != expected for name, expected in expected_sections.items()):
        _fail("controller_receipt.content_invalid")
    digest_launch_fields = (
        "launch_record_sha256",
        "broker_launch_receipt_sha256",
        "runtime_record_sha256",
        "runtime_signature_bundle_sha256",
        "provision_job_configuration_template_sha256",
    )
    identity_ids = ("provision_job_resource_id", "provision_job_uami_resource_id")
    subscription = identity["subscription_id"]
    configured_writer = policy["record_chain_source"]["writer_identity_resource_id"]
    configured_identity = policy["fixed_bindings"]
    if (
        any(
            UUID_RE.fullmatch(str(identity[name])) is None
            for name in (
                "tenant_id",
                "subscription_id",
                "provision_job_uami_client_id",
                "provision_job_uami_principal_id",
            )
        )
        or any(not _azure_id(identity[name]) for name in identity_ids)
        or any(identity[name].split("/")[2] != subscription for name in identity_ids)
        or _resource_type(identity["provision_job_resource_id"]) != "microsoft.app/jobs"
        or _resource_type(identity["provision_job_uami_resource_id"])
        != "microsoft.managedidentity/userassignedidentities"
        or (
            PLACEHOLDER_RE.fullmatch(configured_writer) is None
            and identity["provision_job_uami_resource_id"] != configured_writer
        )
        or any(
            PLACEHOLDER_RE.fullmatch(configured_identity[name]) is None
            and identity[name] != configured_identity[name]
            for name in (
                "tenant_id",
                "subscription_id",
                "provision_job_resource_id",
                "provision_job_uami_client_id",
                "provision_job_uami_principal_id",
            )
        )
        or identity["provision_job_uami_client_id"] != intent["provision_client_id"]
        or identity["provision_job_uami_principal_id"]
        != intent["provision_principal_id"]
        or any(
            DIGEST_RE.fullmatch(str(launch[name])) is None
            for name in digest_launch_fields
        )
        or type(launch["execution_name"]) is not str
        or EXECUTION_NAME_RE.fullmatch(launch["execution_name"]) is None
        or launch["execution_resource_id"]
        != f"{identity['provision_job_resource_id']}/executions/{launch['execution_name']}"
        or IMAGE_RE.fullmatch(str(launch["provision_job_image_reference"])) is None
        or _utc(launch["launch_expires_at"]) <= intent_created
        or source["source_database_name"]
        != policy["fixed_bindings"]["source_database_name"]
        or (
            PLACEHOLDER_RE.fullmatch(configured_identity["source_server_resource_id"])
            is None
            and source["source_server_resource_id"]
            != configured_identity["source_server_resource_id"]
        )
        or source["source_server_resource_id"].split("/")[2] != subscription
        or source["approved_restore_time"] != intent["restore_time"]
        or not all(
            GIT_SHA_RE.fullmatch(str(source[name]))
            for name in ("workflow_sha", "source_sha", "candidate_sha")
        )
        or not all(
            RUN_RE.fullmatch(str(source[name])) for name in ("run_id", "run_attempt")
        )
    ):
        _fail("controller_receipt.content_invalid")

    runtime_ids = [target[name] for name in RUNTIME_FIELDS if name.endswith("_id")]
    expected_types = {
        "target_postgres_server_id": "microsoft.dbforpostgresql/flexibleservers",
        "target_vnet_id": "microsoft.network/virtualnetworks",
        "target_subnet_id": "microsoft.network/virtualnetworks/subnets",
        "target_private_endpoint_id": "microsoft.network/privateendpoints",
        "target_network_interface_id": "microsoft.network/networkinterfaces",
        "target_private_dns_zone_id": "microsoft.network/privatednszones",
        "target_private_dns_link_id": "microsoft.network/privatednszones/virtualnetworklinks",
        "target_private_dns_zone_group_id": "microsoft.network/privateendpoints/privatednszonegroups",
        "executor_private_endpoint_id": "microsoft.network/privateendpoints",
        "executor_network_interface_id": "microsoft.network/networkinterfaces",
        "executor_private_dns_zone_id": "microsoft.network/privatednszones",
        "executor_private_dns_link_id": "microsoft.network/privatednszones/virtualnetworklinks",
        "executor_private_dns_zone_group_id": "microsoft.network/privateendpoints/privatednszonegroups",
    }
    configured_group = policy["fixed_bindings"]["target_resource_group_id"]
    configured_executor_group = policy["fixed_bindings"]["executor_resource_group_id"]
    configured_role = policy["fixed_bindings"]["approved_database_role"]
    if (
        receipt["schema_version"] != 2
        or type(receipt["schema_version"]) is not int
        or receipt["kind"] != "staging-provision-controller-receipt"
        or receipt["policy_id"] != policy["policy_id"]
        or receipt["policy_binding_sha256"] != _policy_binding_digest(policy)
        or receipt["repository"] != policy["fixed_bindings"]["repository"]
        or target["target_postgres_server_id"] != intent["target_server_resource_id"]
        or target["target_fqdn"]
        != f"{expected_target_name}{policy['fixed_bindings']['target_postgres_fqdn_suffix']}"
        or FQDN_RE.fullmatch(str(target["target_fqdn"])) is None
        or ROLE_RE.fullmatch(str(target["approved_database_role"])) is None
        or (
            PLACEHOLDER_RE.fullmatch(configured_role) is None
            and target["approved_database_role"] != configured_role
        )
        or any(
            not _azure_id(item) or item.split("/")[2] != subscription
            for item in runtime_ids
        )
        or any(
            _resource_type(target[name]) != expected
            for name, expected in expected_types.items()
        )
        or (
            PLACEHOLDER_RE.fullmatch(configured_group) is None
            and any(
                _resource_group(target[name]).casefold() != configured_group.casefold()
                for name in RUNTIME_FIELDS
                if name.startswith("target_") and name.endswith("_id")
            )
        )
        or (
            PLACEHOLDER_RE.fullmatch(configured_executor_group) is None
            and any(
                _resource_group(target[name]).casefold()
                != configured_executor_group.casefold()
                for name in RUNTIME_FIELDS
                if name.startswith("executor_") and name.endswith("_id")
            )
        )
        or _parent_id(target["target_subnet_id"]).casefold()
        != target["target_vnet_id"].casefold()
        or _parent_id(target["target_private_dns_link_id"]).casefold()
        != target["target_private_dns_zone_id"].casefold()
        or _parent_id(target["target_private_dns_zone_group_id"]).casefold()
        != target["target_private_endpoint_id"].casefold()
        or _parent_id(target["executor_private_dns_link_id"]).casefold()
        != target["executor_private_dns_zone_id"].casefold()
        or _parent_id(target["executor_private_dns_zone_group_id"]).casefold()
        != target["executor_private_endpoint_id"].casefold()
        or target["resource_set_sha256"]
        != _digest(_canonical({name: target[name] for name in RUNTIME_FIELDS}))
        or _canonical(isolation) != _canonical(policy["fixed_bindings"]["isolation"])
        or receipt["pass"] is not True
    ):
        _fail("controller_receipt.content_invalid")

    restored = _utc(receipt["restore_observed_at"])
    created = _utc(receipt["created_at"])
    expires = _utc(receipt["expires_at"])
    cleanup = _utc(receipt["cleanup_not_after"])
    freshness = policy["freshness"]
    if cleanup != intent_cleanup or not (
        intent_created
        <= restored
        <= created
        <= now + timedelta(seconds=freshness["maximum_clock_skew_seconds"])
    ):
        _fail("controller_receipt.future")
    if not (
        created
        < expires
        <= created + timedelta(seconds=freshness["maximum_receipt_lifetime_seconds"])
        and expires <= cleanup
    ):
        _fail("controller_receipt.expired")

    result = object.__new__(ValidatedControllerReceiptV2Content)
    output = {
        "intent_bytes": bytes(intent_bytes),
        "submission_bytes": bytes(submission_bytes),
        "receipt_bytes": bytes(receipt_bytes),
        "intent_sha256": intent_sha256,
        "submission_sha256": submission_sha256,
        "receipt_sha256": _digest(receipt_bytes),
        "policy_binding_sha256": _policy_binding_digest(policy),
        **{name: target[name] for name in RUNTIME_FIELDS},
        "expires_at": receipt["expires_at"],
        "cleanup_not_after": receipt["cleanup_not_after"],
        "provenance_authenticated": False,
        "_policy_bytes": _canonical(policy),
    }
    for name, value in output.items():
        object.__setattr__(result, name, value)
    object.__setattr__(result, "_seal", _VALIDATED_CONTENT_SEAL)
    return result


def validate_controller_receipt_v2_content(
    policy: Any,
    *,
    intent_bytes: bytes,
    submission_bytes: bytes,
    receipt_bytes: bytes,
    clock: Callable[[], datetime],
) -> ValidatedControllerReceiptV2Content:
    """Validate canonical bytes and exact bindings, never their provenance."""

    document = validate_controller_receipt_v2_policy(policy, executable=False)
    now = _trusted_now(clock)
    return _validate_controller_receipt_v2_content_at(
        document,
        intent_bytes=intent_bytes,
        submission_bytes=submission_bytes,
        receipt_bytes=receipt_bytes,
        now=now,
    )


def _revalidate_controller_receipt_v2_content(
    receipt: ValidatedControllerReceiptV2Content,
    *,
    now: datetime,
) -> ValidatedControllerReceiptV2Content:
    if (
        type(receipt) is not ValidatedControllerReceiptV2Content
        or receipt._seal is not _VALIDATED_CONTENT_SEAL
        or receipt.provenance_authenticated is not False
    ):
        _fail("controller_receipt.content_invalid")
    policy = _parse_canonical(receipt._policy_bytes, MAXIMUM_POLICY_BYTES)
    return _validate_controller_receipt_v2_content_at(
        validate_controller_receipt_v2_policy(policy),
        intent_bytes=receipt.intent_bytes,
        submission_bytes=receipt.submission_bytes,
        receipt_bytes=receipt.receipt_bytes,
        now=now,
    )


def require_fresh_controller_receipt_v2_content(
    receipt: ValidatedControllerReceiptV2Content,
    *,
    clock: Callable[[], datetime],
) -> ValidatedControllerReceiptV2Content:
    """Check provision freshness; this does not authenticate the receipt."""

    now = _trusted_now(clock)
    validated = _revalidate_controller_receipt_v2_content(receipt, now=now)
    if now >= min(_utc(validated.expires_at), _utc(validated.cleanup_not_after)):
        _fail("controller_receipt.expired")
    return validated
