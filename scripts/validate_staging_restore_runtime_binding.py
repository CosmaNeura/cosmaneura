#!/usr/bin/env python3
"""Validate the closed-world staging restore runtime policy and external record.

The module performs no network, credential, signature, or Azure operation. Its
sealed objects prevent accidental API misuse, not hostile same-process forgery.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import math
import os
import re
import stat
import sys
import urllib.parse
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NoReturn

# This security contract is intentionally compact to stay inside the repository's
# hard change budget; keep the declarative tables and checks formatter-stable.
# fmt: off

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "contracts/staging-rehearsal/v1/restore-runtime-binding-policy.json"
CANONICAL_POLICY_SHA256 = "124b93fc3394fcebe177b30a2c61b22040c8a7c15e1c5673da26d454209fc21a"
MAXIMUM_POLICY_BYTES = 262_144

PLACEHOLDER_RE = re.compile(r"^CONFIGURE_EXACT_[A-Z0-9_]+$")
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
AZURE_GUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
AZURE_GUID_RE = re.compile(rf"^{AZURE_GUID}$")
AZURE_RESOURCE_SEGMENT_RE = re.compile(r"^[A-Za-z0-9._()-]+$")
VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
HOST_RE = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$"
)
RECORD_ID_RE = re.compile(r"^runtime-[0-9a-f]{16,64}$")
UTC_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
JWK_COORDINATE_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")
KEY_VAULT_SECRET_NAME_RE = re.compile(r"^[A-Za-z0-9-]{1,127}$")
KEY_VAULT_SECRET_VERSION_RE = re.compile(r"^[0-9a-f]{32}$")

ACTIONS = ("Microsoft.App/jobs/read", "Microsoft.App/jobs/start/action")
RUNTIME_REFERENCE_FIELDS = ("record_uri", "record_sha256", "signature_bundle_uri", "signature_bundle_sha256")
ACTIVATION = (
    ("runtime_record_published", "external_runtime_record_unpublished"),
    ("runtime_record_signature_verified", "external_runtime_record_signature_unverified"),
    ("private_runtime_record_ingress_bound", "private_runtime_record_ingress_unbound"),
    ("runtime_record_publication_authority_bound", "runtime_record_publication_authority_unbound"),
    ("private_registry_binding_implemented", "private_registry_binding_unimplemented"),
    ("broker_oidc_authentication_bound", "broker_oidc_authentication_unbound"),
    ("broker_exact_job_observation_bound", "broker_exact_job_observation_unbound"),
    ("broker_job_control_bound", "broker_job_control_unbound"),
    ("job_start_override_prevention_proven", "job_start_override_prevention_unproven"),
    ("job_start_no_retry_semantics_proven", "job_start_no_retry_semantics_unproven"),
    ("immutable_launch_record_mailbox_bound", "immutable_launch_record_mailbox_unbound"),
    ("launch_receipt_binding_implemented", "launch_receipt_binding_unimplemented"),
    ("job_identity_attachments_bound", "job_identity_attachments_unbound"),
    ("private_evidence_ingress_bound", "private_evidence_ingress_unbound"),
    ("private_secret_ingress_bound", "private_secret_ingress_unbound"),
    ("private_marker_store_binding_implemented", "private_marker_store_binding_unimplemented"),
    ("explicit_egress_bound", "explicit_egress_unbound"),
    ("offline_verifier_containment_proven", "offline_verifier_containment_unproven"),
    ("target_private_endpoint_binding_implemented", "target_private_endpoint_binding_unimplemented"),
    ("authenticated_controller_receipt_bound", "authenticated_controller_receipt_unbound"),
    ("dominant_tombstone_retention_bound", "dominant_cleanup_tombstone_retention_unbound"),
    ("cleanup_binding_implemented", "cleanup_binding_unimplemented"),
    ("workflow_integrated", "workflow_integration_unimplemented"),
    ("restore_activation_authorized", "restore_activation_unimplemented"),
)
IMPLEMENTATION_PATHS = {
    ("launch_binding", "mailbox", "implemented"),
    ("launch_binding", "broker_request_authentication", "replay_store", "implemented"),
    ("launch_binding", "receipt_signing", "implemented"),
    ("launch_binding", "mailbox", "execution_receipt_handoff", "execution_name_source_adapter_implemented"),
    ("launch_binding", "mailbox", "execution_receipt_handoff", "platform_source_validation", "adapter_implemented"),
}
RUNTIME_RECORD_FIELDS = (
    "schema_version",
    "record_id",
    "policy_id",
    "policy_binding_sha256",
    "repository",
    "protected_ref",
    "image_repository",
    "image_digest",
    "provision_job_image_reference",
    "cleanup_job_image_reference",
    "image_published_at",
    "environment_resource_id",
    "provision_job_resource_id",
    "provision_job_configuration_template_sha256",
    "provision_job_identity_resource_id",
    "cleanup_job_resource_id",
    "cleanup_job_configuration_template_sha256",
    "cleanup_job_identity_resource_id",
    "network_policy_sha256",
    "created_at",
)
TARGET_CLEANUP_FIELDS = (
    "target_postgres_server_id",
    "target_vnet_id",
    "target_subnet_id",
    "target_private_endpoint_id",
    "target_network_interface_id",
    "target_private_dns_zone_id",
    "target_private_dns_link_id",
    "target_private_dns_zone_group_id",
)
EXECUTOR_CLEANUP_FIELDS = (
    "executor_private_endpoint_id",
    "executor_network_interface_id",
    "executor_private_dns_zone_id",
    "executor_private_dns_link_id",
    "executor_private_dns_zone_group_id",
)


class RuntimeBindingFailure(RuntimeError):
    """A bounded validation failure safe to report without policy contents."""


def _fail(code: str) -> NoReturn:
    raise RuntimeBindingFailure(code)


def _object(value: Any, fields: set[str], code: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != fields:
        _fail(code)
    return value


def _exact(value: Any, expected: Any, code: str) -> None:
    if type(value) is not type(expected) or value != expected:
        _fail(code)


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()
    except (TypeError, ValueError, UnicodeEncodeError, RecursionError):
        raise RuntimeBindingFailure("canonical.invalid") from None


def _digest(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def _azure_resource_id(value: str) -> bool:
    if "//" in value or value.endswith("/") or any(char in value for char in "%?#"):
        return False
    parts = value.split("/")
    if len(parts) < 4 or parts[:2] != ["", "subscriptions"] or AZURE_GUID_RE.fullmatch(parts[2]) is None:
        return False
    index = 3
    if parts[index] == "resourceGroups":
        if len(parts) < 5 or AZURE_RESOURCE_SEGMENT_RE.fullmatch(parts[4]) is None:
            return False
        index = 5
        if index == len(parts):
            return True
    if index >= len(parts) or parts[index] != "providers":
        return False
    provider_parts = parts[index + 1 :]
    return len(provider_parts) >= 3 and len(provider_parts) % 2 == 1 and all(AZURE_RESOURCE_SEGMENT_RE.fullmatch(part) for part in provider_parts)


def _pin(value: Any, kind: str, code: str) -> str:
    if type(value) is not str or not value or len(value) > 2048:
        _fail(code)
    if PLACEHOLDER_RE.fullmatch(value):
        return value
    valid = False
    if kind == "digest":
        valid = DIGEST_RE.fullmatch(value) is not None
    elif kind == "uuid":
        valid = UUID_RE.fullmatch(value) is not None
    elif kind == "resource_id":
        valid = _azure_resource_id(value)
    elif kind == "host":
        valid = HOST_RE.fullmatch(value) is not None
    elif kind == "version":
        valid = VERSION_RE.fullmatch(value) is not None
    elif kind == "jwk_coordinate":
        try:
            decoded = base64.b64decode(value + "=", altchars=b"-_", validate=True)
            valid = (
                JWK_COORDINATE_RE.fullmatch(value) is not None
                and len(decoded) == 32
                and base64.urlsafe_b64encode(decoded).rstrip(b"=").decode("ascii") == value
            )
        except (ValueError, binascii.Error):
            valid = False
    elif kind in {"https_uri", "blob_version_uri"}:
        try:
            parsed = urllib.parse.urlsplit(value)
            query = urllib.parse.parse_qs(parsed.query, strict_parsing=True)
            common = (
                parsed.scheme == "https"
                and parsed.hostname is not None
                and parsed.netloc == parsed.hostname
                and bool(parsed.path)
                and not parsed.fragment
            )
            valid = common and (
                (kind == "https_uri" and not parsed.query)
                or (kind == "blob_version_uri" and set(query) == {"versionid"} and len(query["versionid"]) == 1)
            )
        except ValueError:
            valid = False
    elif kind == "image_repository":
        host, separator, repository = value.partition("/")
        valid = (
            value == value.lower()
            and len(value) <= 255
            and separator == "/"
            and host.endswith(".azurecr.io")
            and HOST_RE.fullmatch(host) is not None
            and re.fullmatch(r"[a-z0-9]+(?:[._-][a-z0-9]+)*(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)*", repository) is not None
        )
    elif kind == "signer_identity":
        valid = (
            value.startswith("https://github.com/CosmaNeura/cosmaneura/")
            and value.endswith("@refs/heads/master")
            and len(value) <= 512
        )
    if not valid:
        _fail(code)
    return value


def _pin_kind(placeholder: str) -> str:
    if placeholder.endswith(("_JWK_X", "_JWK_Y")):
        return "jwk_coordinate"
    if placeholder.endswith("_SHA256"):
        return "digest"
    if placeholder.endswith("_UUID"):
        return "uuid"
    if placeholder.endswith("_HOST"):
        return "host"
    if placeholder.endswith("_IMAGE_REPOSITORY"):
        return "image_repository"
    if placeholder.endswith("_SIGNER_IDENTITY"):
        return "signer_identity"
    if placeholder.endswith("_VERSIONED_HTTPS_URI"):
        return "blob_version_uri" if "BUNDLE" in placeholder else "https_uri"
    if placeholder.endswith(("_VERSION", "_VERSION_ID")):
        return "version"
    return "resource_id"


def _shape(value: Any, template: Any, path: tuple[str, ...] = ()) -> None:
    code = "policy." + ".".join(path or ("closed_world",))
    if path == ("activation_ready",) or (len(path) == 2 and path[0] == "activation") or path in IMPLEMENTATION_PATHS:
        if type(value) is not bool:
            _fail(code)
        return
    if path == ("activation_blockers",):
        if type(value) is not list or any(type(item) is not str for item in value):
            _fail(code)
        return
    if type(template) is dict:
        if type(value) is not dict or set(value) != set(template):
            _fail(code)
        for key, child in template.items():
            _shape(value[key], child, (*path, key))
        return
    if type(template) is list:
        if type(value) is not list or len(value) != len(template):
            _fail(code)
        for index, child in enumerate(template):
            _shape(value[index], child, (*path, str(index)))
        return
    if type(template) is str and PLACEHOLDER_RE.fullmatch(template):
        if type(value) is str and PLACEHOLDER_RE.fullmatch(value):
            if value != template:
                _fail(code)
        else:
            _pin(value, _pin_kind(template), code)
        return
    _exact(value, template, code)


def _placeholder_values(value: Any, *, depth: int = 0) -> tuple[str, ...]:
    if depth > 32:
        _fail("policy.too_deep")
    if type(value) is str:
        return (value,) if PLACEHOLDER_RE.fullmatch(value) else ()
    if type(value) in {dict, list}:
        items = value.values() if type(value) is dict else value
        return tuple(
            placeholder
            for item in items
            for placeholder in _placeholder_values(item, depth=depth + 1)
        )
    return ()


def _policy_binding_digest(policy: dict[str, Any]) -> str:
    excluded = {"activation_ready", "activation", "activation_blockers"}
    if not excluded < set(policy):
        _fail("policy.binding_projection")
    return _digest(_canonical({key: value for key, value in policy.items() if key not in excluded}))


def _utc(value: Any, code: str) -> datetime:
    if type(value) is not str or UTC_RE.fullmatch(value) is None:
        _fail(code)
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        raise RuntimeBindingFailure(code) from None
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        _fail(code)
    return parsed


def _parse_json_object(raw: bytes, code: str) -> dict[str, Any]:
    def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in values:
            if key in result:
                _fail(f"{code}.duplicate_key")
            result[key] = item
        return result

    def nonfinite(_value: str) -> NoReturn:
        _fail(f"{code}.nonfinite_number")

    try:
        value = json.loads(raw, object_pairs_hook=pairs, parse_constant=nonfinite, parse_float=lambda raw_float: number if math.isfinite(number := float(raw_float)) else nonfinite(raw_float))
    except RuntimeBindingFailure:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        raise RuntimeBindingFailure(code) from None
    if type(value) is not dict:
        _fail(code)
    return value


def _read_canonical_policy(path: Path) -> bytes:
    descriptor = -1
    try:
        if path.resolve(strict=True) != POLICY_PATH.resolve(strict=True):
            _fail("policy.untrusted_path")
        before = path.lstat()
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid not in {0, os.geteuid()}
            or stat.S_IMODE(before.st_mode) & 0o022
            or not 1 <= before.st_size <= MAXIMUM_POLICY_BYTES
        ):
            _fail("policy.unreadable")
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0))
        after = os.fstat(descriptor)
        raw = os.read(descriptor, MAXIMUM_POLICY_BYTES + 1)
        if (
            (before.st_dev, before.st_ino, before.st_size)
            != (after.st_dev, after.st_ino, after.st_size)
            or len(raw) != before.st_size
            or os.read(descriptor, 1)
        ):
            _fail("policy.unreadable")
    except RuntimeBindingFailure:
        raise
    except OSError:
        raise RuntimeBindingFailure("policy.unreadable") from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    return raw


def _canonical_template() -> dict[str, Any]:
    raw = _read_canonical_policy(POLICY_PATH)
    if hashlib.sha256(raw).hexdigest() != CANONICAL_POLICY_SHA256:
        _fail("policy.digest_mismatch")
    return _parse_json_object(raw, "policy.invalid_json")


def _activation(policy: dict[str, Any], *, executable: bool) -> None:
    activation = _object(policy["activation"], {name for name, _ in ACTIVATION}, "activation")
    if any(type(value) is not bool for value in activation.values()):
        _fail("activation")
    ready = policy["activation_ready"]
    if type(ready) is not bool or ready is not all(activation.values()):
        _fail("activation")
    blockers = [] if ready else ["restore_runtime_binding_policy_disabled"]
    blockers.extend(blocker for name, blocker in ACTIVATION if not activation[name])
    _exact(policy["activation_blockers"], blockers, "activation_blockers")
    launch = policy["launch_binding"]
    handoff = launch["mailbox"]["execution_receipt_handoff"]
    implementations = {
        "broker_oidc_authentication_bound": (launch["broker_request_authentication"]["replay_store"]["implemented"],),
        "immutable_launch_record_mailbox_bound": (launch["mailbox"]["implemented"],),
        "launch_receipt_binding_implemented": (launch["receipt_signing"]["implemented"], handoff["execution_name_source_adapter_implemented"], handoff["platform_source_validation"]["adapter_implemented"]),
    }
    if any(any(type(value) is not bool for value in values) or activation[name] and not all(values) for name, values in implementations.items()):
        _fail("activation.implementation")
    if executable and not ready:
        _fail("policy.not_activated")


def _cross_bind(policy: dict[str, Any]) -> None:
    runtime = policy["runtime"]
    environment = runtime["container_apps_environment"]
    jobs = runtime["jobs"]
    identities = policy["identities"]
    resources: list[str] = [environment["resource_id"]]
    clients: list[str] = []
    principals: list[str] = []
    role_assignments: list[str] = []
    for action in ("provision", "cleanup"):
        job = jobs[action]
        dispatcher = identities["dispatchers"][action]
        broker = identities["brokers"][action]
        job_identity = identities["jobs"][action]
        if (
            job["environment_resource_id"] != environment["resource_id"]
            or dispatcher["broker_request"]["broker_identity_resource_id"] != broker["resource_id"]
            or dispatcher["broker_request"]["endpoint_resource_id"] != broker["endpoint_resource_id"]
            or dispatcher["broker_request"]["oidc_audience_client_id"] != broker["client_id"]
            or dispatcher["broker_request"]["job_read_or_start_actions"]
            or dispatcher["broker_request"]["role_assignment_ids"]
            or broker["caller_identity_resource_id"] != dispatcher["resource_id"]
            or broker["job_control"]["selected_job_resource_id"] != job["resource_id"]
            or broker["job_control"]["readable_job_resource_ids"] != [jobs[name]["resource_id"] for name in ("provision", "cleanup")]
            or broker["job_control"]["read_role_assignment_scopes"] != {name: jobs[name]["resource_id"] for name in ("provision", "cleanup")}
            or broker["job_control"]["start_role_assignment_scope"] != job["resource_id"]
            or broker["job_control"]["actions"] != {"read": ["Microsoft.App/jobs/read"], "start": ["Microsoft.App/jobs/start/action"]}
            or broker["launch_receipt_sign_role_assignment_id"] != policy["launch_binding"]["receipt_signing"]["broker_role_assignment_ids"][action]
            or policy["launch_binding"]["receipt_signing"]["broker_role_assignment_scopes"][action] != policy["launch_binding"]["receipt_signing"]["key_resource_id"]
            or broker["federated_credential_ids"]
            or broker["other_role_assignment_ids"]
            or job_identity["attached_job_resource_ids"] != [job["resource_id"]]
            or job["user_assigned_identity_resource_ids"] != [job_identity["resource_id"]]
            or job["registry_identity_resource_id"] != job_identity["resource_id"]
            or job_identity["federated_credential_ids"]
            or dispatcher["other_role_assignment_ids"]
        ):
            _fail(f"identities.{action}.binding")
        resources.extend((job["resource_id"], dispatcher["resource_id"], dispatcher["federated_credential"]["resource_id"], broker["resource_id"], broker["endpoint_resource_id"], job_identity["resource_id"]))
        clients.extend((dispatcher["client_id"], broker["client_id"], job_identity["client_id"]))
        principals.extend((dispatcher["principal_id"], broker["principal_id"], job_identity["principal_id"]))
        role_assignments.extend((*broker["job_control"]["read_role_assignment_ids"].values(), broker["job_control"]["start_role_assignment_id"], broker["launch_mailbox_create_role_assignment_id"], broker["launch_receipt_sign_role_assignment_id"], *job_identity["role_assignment_ids"].values()))
    if (
        jobs["provision"]["resource_id"] == jobs["cleanup"]["resource_id"]
        or jobs["provision"]["image_repository"] != jobs["cleanup"]["image_repository"]
        or jobs["provision"]["configuration_template_sha256"] == jobs["cleanup"]["configuration_template_sha256"]
    ):
        _fail("runtime.jobs.distinct")
    ingress = policy["evidence_ingress"]
    runtime_ingress = policy["runtime_record_ingress"]
    registry = policy["private_registry"]
    marker = policy["private_control_marker_store"]
    launch = policy["launch_binding"]
    mailbox = launch["mailbox"]
    publisher = runtime_ingress["publisher"]
    blob = ingress["private_restore_evidence_container"]
    secret = ingress["private_versioned_secret_source"]
    provision_id = identities["jobs"]["provision"]["resource_id"]
    vnet_id = environment["control_vnet_resource_id"]
    hmac = secret["secrets"]["server_scope_hmac_key"]
    root = secret["secrets"]["github_trusted_root"]
    provision_identity = identities["jobs"]["provision"]
    cleanup_identity = identities["jobs"]["cleanup"]
    runtime_retrieval = runtime_ingress["retrieval"]
    secret_retrieval = secret["retrieval"]
    both_job_ids = [provision_identity["resource_id"], cleanup_identity["resource_id"]]
    resources.append(publisher["identity_resource_id"])
    clients.append(publisher["client_id"])
    principals.append(publisher["principal_id"])
    role_assignments.append(publisher["role_assignment_id"])
    if len({value.casefold() for value in (*resources, *role_assignments)}) != len(resources) + len(role_assignments) or len(set((*clients, *principals))) != len(clients) + len(principals):
        _fail("identities.distinct")
    if (
        blob["allowed_job_identity_resource_ids"] != [provision_id]
        or secret["allowed_job_identity_resource_ids"] != both_job_ids
        or hmac["allowed_job_identity_resource_ids"] != [provision_id]
        or root["allowed_job_identity_resource_ids"] != both_job_ids
        or runtime_ingress["allowed_job_identity_resource_ids"] != both_job_ids
        or registry["pull_identity_resource_ids"] != both_job_ids
        or marker["allowed_job_identity_resource_ids"] != both_job_ids
        or mailbox["linked_vnet_resource_ids"] != [vnet_id]
        or blob["linked_vnet_resource_ids"] != [vnet_id]
        or secret["linked_vnet_resource_ids"] != [vnet_id]
        or runtime_ingress["linked_vnet_resource_ids"] != [vnet_id]
        or registry["linked_vnet_resource_ids"] != [vnet_id]
        or marker["linked_vnet_resource_ids"] != [vnet_id]
        or publisher["execution_vnet_resource_id"] != vnet_id
        or policy["per_rehearsal_postgres"]["private_dns"]["linked_vnet_resource_ids"] != [vnet_id]
        or registry["repository"] != jobs["provision"]["image_repository"]
        or marker["policy_id"] != policy["authenticated_controller_receipt"]["source_policy_id"]
        or marker["container_names"]["cleanup"] != policy["cleanup"]["dominant_tombstone"]["container"]
        or marker["role_assignment_ids"] != {
            "provision_intent": provision_identity["role_assignment_ids"]["marker_intent_create"],
            "provision_submission": provision_identity["role_assignment_ids"]["marker_submission_create"],
            "provision_cleanup": provision_identity["role_assignment_ids"]["cleanup_tombstone_read_create"],
            "cleanup_intent": cleanup_identity["role_assignment_ids"]["marker_intent_read"],
            "cleanup_submission": cleanup_identity["role_assignment_ids"]["marker_submission_read"],
            "cleanup_cleanup": cleanup_identity["role_assignment_ids"]["cleanup_tombstone_create"],
        }
        or blob["account_resource_id"].casefold() == marker["account_resource_id"].casefold()
        or blob["account_host"] == marker["account_host"]
        or runtime_ingress["account_resource_id"].casefold() == marker["account_resource_id"].casefold()
        or runtime_ingress["account_host"] == marker["account_host"]
        or len({value.casefold() for value in (blob["container_resource_id"], runtime_ingress["container_resource_id"], *marker["container_resource_ids"].values(), *mailbox["container_resource_ids"].values())}) != 7
        or runtime_ingress["role_assignment_ids"]["provision"] != provision_identity["role_assignment_ids"].get("runtime_binding_reader")
        or runtime_ingress["role_assignment_ids"]["cleanup"] != cleanup_identity["role_assignment_ids"].get("runtime_binding_reader")
        or mailbox["role_assignment_ids"]["provision_job_read"] != provision_identity["role_assignment_ids"].get("launch_mailbox_read")
        or mailbox["role_assignment_ids"]["cleanup_job_read"] != cleanup_identity["role_assignment_ids"].get("launch_mailbox_read")
        or mailbox["role_assignment_ids"]["provision_broker_create"] != identities["brokers"]["provision"]["launch_mailbox_create_role_assignment_id"]
        or mailbox["role_assignment_ids"]["cleanup_broker_create"] != identities["brokers"]["cleanup"]["launch_mailbox_create_role_assignment_id"]
        or mailbox["role_scopes"] != {"provision_broker_create": mailbox["container_resource_ids"]["provision"], "cleanup_broker_create": mailbox["container_resource_ids"]["cleanup"], "provision_job_read": mailbox["container_resource_ids"]["provision"], "cleanup_job_read": mailbox["container_resource_ids"]["cleanup"]}
        or mailbox["role_assignment_role_definition_ids"] != {"provision_broker_create": mailbox["role_definitions"]["broker_create"]["role_definition_id"], "cleanup_broker_create": mailbox["role_definitions"]["broker_create"]["role_definition_id"], "provision_job_read": mailbox["role_definitions"]["job_read"]["role_definition_id"], "cleanup_job_read": mailbox["role_definitions"]["job_read"]["role_definition_id"]}
        or launch["receipt_signing"]["role_definition"]["data_actions"] != [launch["receipt_signing"]["broker_sign_action"]]
        or any(launch["receipt_signing"]["role_definition"][name] for name in ("actions", "not_actions", "not_data_actions"))
        or launch["receipt_signing"]["broker_role_assignment_role_definition_ids"] != dict.fromkeys(("provision", "cleanup"), launch["receipt_signing"]["role_definition"]["role_definition_id"])
        or len(set(mailbox["container_names"].values())) != 2
        or len(set(mailbox["container_resource_ids"].values())) != 2
        or mailbox["account_resource_id"].casefold() in {value.casefold() for value in (blob["account_resource_id"], runtime_ingress["account_resource_id"], marker["account_resource_id"])}
        or mailbox["account_host"] in {blob["account_host"], runtime_ingress["account_host"], marker["account_host"]}
        or registry["acrpull_role_assignment_ids"]["provision"] != provision_identity["role_assignment_ids"]["registry_pull"]
        or registry["acrpull_role_assignment_ids"]["cleanup"] != cleanup_identity["role_assignment_ids"]["registry_pull"]
        or hmac["role_assignment_ids"]["provision"] != provision_identity["role_assignment_ids"]["hmac_secret_reader"]
        or root["role_assignment_ids"]["provision"] != provision_identity["role_assignment_ids"]["github_root_reader"]
        or root["role_assignment_ids"]["cleanup"] != cleanup_identity["role_assignment_ids"]["github_root_reader"]
        or hmac["versioned_https_uri"] == root["versioned_https_uri"]
        or hmac["version"] == root["version"]
        or runtime_retrieval["record_media_type"]
        != policy["external_runtime_record"]["record"]["content_type"]
        or runtime_retrieval["signature_bundle_media_type"]
        != policy["external_runtime_record"]["signature"]["format"]
        or secret_retrieval["request_query_parameters"]
        != [{"name": "api-version", "value": secret_retrieval["api_version"]}]
        or secret_retrieval["maximum_response_bytes"]
        <= root["maximum_bytes"]
    ):
        _fail("evidence_ingress.binding")
    receipt_fields = ["target_fqdn", "approved_database_role", *TARGET_CLEANUP_FIELDS, *EXECUTOR_CLEANUP_FIELDS]
    if (
        policy["authenticated_controller_receipt"]["required_runtime_fields"] != receipt_fields
        or policy["per_rehearsal_postgres"]["executor_side_required_fields"] != list(EXECUTOR_CLEANUP_FIELDS)
        or policy["cleanup"]["bound_resource_fields"]["target"] != list(TARGET_CLEANUP_FIELDS)
        or policy["cleanup"]["bound_resource_fields"]["executor_side"] != list(EXECUTOR_CLEANUP_FIELDS)
    ):
        _fail("authenticated_controller_receipt.binding")


def _executable_bindings(policy: dict[str, Any]) -> None:
    if _placeholder_values(policy):
        _fail("policy.placeholders")
    blob = policy["evidence_ingress"]["private_restore_evidence_container"]
    secret = policy["evidence_ingress"]["private_versioned_secret_source"]
    runtime_ingress = policy["runtime_record_ingress"]
    registry = policy["private_registry"]
    marker = policy["private_control_marker_store"]
    mailbox = policy["launch_binding"]["mailbox"]
    signing = policy["launch_binding"]["receipt_signing"]
    registry_host, _, _repository_path = registry["repository"].partition("/")
    mailbox_account_name = mailbox["account_host"].removesuffix(
        ".blob.core.windows.net"
    )
    mailbox_account_parts = mailbox["account_resource_id"].split("/")
    expected_mailbox_containers = {
        action: (
            f"{mailbox['account_resource_id']}/blobServices/default/containers/"
            f"{mailbox['container_names'][action]}"
        )
        for action in ("provision", "cleanup")
    }
    if (
        not blob["account_host"].endswith(".blob.core.windows.net")
        or not runtime_ingress["account_host"].endswith(".blob.core.windows.net")
        or not registry["host"].endswith(".azurecr.io")
        or registry_host != registry["host"]
        or not secret["vault_host"].endswith(".vault.azure.net")
        or not marker["account_host"].endswith(".blob.core.windows.net")
        or not mailbox["account_host"].endswith(".blob.core.windows.net")
        or not mailbox_account_name
        or len(mailbox_account_parts) != 9
        or mailbox_account_parts[5:8]
        != ["providers", "Microsoft.Storage", "storageAccounts"]
        or mailbox_account_parts[8] != mailbox_account_name
        or mailbox["container_resource_ids"] != expected_mailbox_containers
        or signing["key_resource_id"]
        != f"{secret['vault_resource_id']}/keys/{signing['key_name']}"
        or signing["public_key_sha256"] != _digest(_canonical(signing["public_key_jwk"]))
    ):
        _fail("evidence_ingress.private_binding")
    for item in secret["secrets"].values():
        uri = urllib.parse.urlsplit(item["versioned_https_uri"])
        parts = uri.path.split("/")
        if (
            uri.scheme != "https"
            or uri.hostname != secret["vault_host"]
            or uri.netloc != secret["vault_host"]
            or uri.query
            or uri.fragment
            or len(parts) != 4
            or parts[:2] != ["", "secrets"]
            or KEY_VAULT_SECRET_NAME_RE.fullmatch(parts[2]) is None
            or KEY_VAULT_SECRET_VERSION_RE.fullmatch(parts[3]) is None
            or parts[3] != item["version"]
        ):
            _fail("evidence_ingress.secret.version_binding")
    if blob["objects"]["source"] != "validated_immutable_launch_record.provision":
        _fail("evidence_ingress.blob.launch_binding")


def validate_runtime_binding_policy(policy: Any, *, executable: bool = False) -> dict[str, Any]:
    """Validate one policy object against the digest-pinned closed-world template."""

    template = _canonical_template()
    _shape(policy, template)
    document = _object(policy, set(template), "policy.closed_world")
    _activation(document, executable=executable)
    _cross_bind(document)
    if executable:
        _executable_bindings(document)
    return document


def validate_policy(policy: Any, *, executable: bool = False) -> dict[str, Any]:
    """Compatibility alias for the explicit runtime-binding policy API."""

    return validate_runtime_binding_policy(policy, executable=executable)


_VERIFIED_SIGNATURE_SEAL = object()
_VALIDATED_RUNTIME_BINDING_SEAL = object()
_RUNTIME_REFERENCE_SEAL = object()


@dataclass(frozen=True, slots=True, init=False)
class ExternalRuntimeBindingReference:
    """Adapter-minted pointer attributed to one exact Container Apps job."""

    source_job_kind: str
    source_job_resource_id: str
    record_uri: str
    record_sha256: str
    signature_bundle_uri: str
    signature_bundle_sha256: str
    _seal: object = dataclass_field(repr=False, compare=False)

    def __init__(self) -> None:
        _fail("runtime_record.reference_invalid")

    @classmethod
    def _from_values(cls, source_job_kind: str, source_job_resource_id: str, values: dict[str, str]) -> ExternalRuntimeBindingReference:
        if cls is not ExternalRuntimeBindingReference or source_job_kind not in ("provision", "cleanup"):
            _fail("runtime_record.reference_invalid")
        _pin(source_job_resource_id, "resource_id", "runtime_record.reference_invalid")
        if PLACEHOLDER_RE.fullmatch(source_job_resource_id):
            _fail("runtime_record.reference_invalid")
        for name in RUNTIME_REFERENCE_FIELDS:
            _pin(values.get(name), "https_uri" if name.endswith("uri") else "digest", "runtime_record.reference_invalid")
            if PLACEHOLDER_RE.fullmatch(values[name]):
                _fail("runtime_record.reference_invalid")
        result = object.__new__(cls)
        object.__setattr__(result, "source_job_kind", source_job_kind)
        object.__setattr__(result, "source_job_resource_id", source_job_resource_id)
        for name in RUNTIME_REFERENCE_FIELDS:
            object.__setattr__(result, name, values[name])
        object.__setattr__(result, "_seal", _RUNTIME_REFERENCE_SEAL)
        return result

    @classmethod
    def _from_authenticated_job_observation(
        cls,
        observation: Any,
    ) -> ExternalRuntimeBindingReference:
        """Derive a pointer only from one sealed authenticated GET observation."""

        try:
            from scripts import (
                validate_staging_restore_launch_binding as launch_binding,
            )
        except ModuleNotFoundError:
            import validate_staging_restore_launch_binding as launch_binding
        if (
            cls is not ExternalRuntimeBindingReference
            or type(observation) is not launch_binding.AuthenticatedJobConfigurationObservation
            or not observation._is_authenticated()
        ):
            _fail("runtime_record.reference_invalid")
        return cls._from_values(observation.job_kind, observation.job_resource_id, dict(observation.runtime_reference))

    @classmethod
    def _from_authenticated_broker_launch_receipt(cls, policy: Any, receipt: Any, *, source_job_kind: str) -> ExternalRuntimeBindingReference:
        """Recreate a job-local pointer solely from authenticated receipt bytes."""

        try:
            from scripts import (
                validate_staging_restore_launch_binding as launch_binding,
            )
        except ModuleNotFoundError:
            import validate_staging_restore_launch_binding as launch_binding
        document = validate_runtime_binding_policy(policy, executable=True)
        if type(receipt) is not launch_binding.AuthenticatedBrokerLaunchReceipt or getattr(receipt, "_seal", None) is not launch_binding._AUTHENTICATED_RECEIPT_SEAL or source_job_kind not in ("provision", "cleanup"):
            _fail("runtime_record.reference_invalid")
        return cls._from_values(source_job_kind, document["runtime"]["jobs"][source_job_kind]["resource_id"], dict(receipt.runtime_reference))

    def _is_bound(self) -> bool:
        return type(self) is ExternalRuntimeBindingReference and getattr(self, "_seal", None) is _RUNTIME_REFERENCE_SEAL


@dataclass(frozen=True, slots=True, init=False)
class VerifiedExternalRuntimeRecordSignature:
    """Verifier result; its seal is not protection from hostile in-process code.

    Only a future independently reviewed verifier adapter may mint this token.
    """

    record_sha256: str
    bundle_sha256: str
    trusted_root_sha256: str
    certificate_oidc_issuer: str
    certificate_identity: str
    _seal: object = dataclass_field(repr=False, compare=False)

    def __init__(self) -> None:
        _fail("signature_verification.invalid")

    @classmethod
    def _mint_from_reviewed_verifier(
        cls,
        *,
        record_sha256: str,
        bundle_sha256: str,
        trusted_root_sha256: str,
        certificate_oidc_issuer: str,
        certificate_identity: str,
    ) -> VerifiedExternalRuntimeRecordSignature:
        """Internal adapter hook; this method does not perform verification."""

        if cls is not VerifiedExternalRuntimeRecordSignature or any(
            type(value) is not str or DIGEST_RE.fullmatch(value) is None
            for value in (record_sha256, bundle_sha256, trusted_root_sha256)
        ):
            _fail("signature_verification.invalid")
        if certificate_oidc_issuer != "https://token.actions.githubusercontent.com" or type(certificate_identity) is not str or not certificate_identity:
            _fail("signature_verification.invalid")
        result = object.__new__(cls)
        values = locals()
        for name in ("record_sha256", "bundle_sha256", "trusted_root_sha256", "certificate_oidc_issuer", "certificate_identity"):
            object.__setattr__(result, name, values[name])
        object.__setattr__(result, "_seal", _VERIFIED_SIGNATURE_SEAL)
        return result

    def _is_verified(self) -> bool:
        return type(self) is VerifiedExternalRuntimeRecordSignature and getattr(self, "_seal", None) is _VERIFIED_SIGNATURE_SEAL


@dataclass(frozen=True, slots=True, init=False)
class ValidatedExternalRuntimeBinding:
    """Opaque exact record bytes accepted against an executable policy."""

    record_bytes: bytes
    record_uri: str
    record_sha256: str
    signature_bundle_uri: str
    signature_bundle_sha256: str
    _seal: object = dataclass_field(repr=False, compare=False)

    def __init__(self) -> None:
        _fail("runtime_record.invalid")

    @classmethod
    def _from_validated(
        cls, *, record_bytes: bytes, reference: ExternalRuntimeBindingReference
    ) -> ValidatedExternalRuntimeBinding:
        if (
            cls is not ValidatedExternalRuntimeBinding
            or type(record_bytes) is not bytes
            or type(reference) is not ExternalRuntimeBindingReference
            or not reference._is_bound()
        ):
            _fail("runtime_record.invalid")
        result = object.__new__(cls)
        object.__setattr__(result, "record_bytes", record_bytes)
        for field in RUNTIME_REFERENCE_FIELDS:
            object.__setattr__(result, field, getattr(reference, field))
        object.__setattr__(result, "_seal", _VALIDATED_RUNTIME_BINDING_SEAL)
        return result

    def _is_validated(self) -> bool:
        return type(self) is ValidatedExternalRuntimeBinding and getattr(self, "_seal", None) is _VALIDATED_RUNTIME_BINDING_SEAL and type(getattr(self, "record_bytes", None)) is bytes

    @property
    def record(self) -> dict[str, Any]:
        if not self._is_validated():
            _fail("runtime_record.invalid")
        return _parse_json_object(self.record_bytes, "runtime_record.invalid")

    @property
    def image_reference(self) -> str:
        record = self.record
        return f"{record['image_repository']}@{record['image_digest']}"


def validate_external_runtime_binding(
    policy: Any,
    record_bytes: bytes,
    *,
    provision_reference: ExternalRuntimeBindingReference,
    cleanup_reference: ExternalRuntimeBindingReference,
    signature_verification: VerifiedExternalRuntimeRecordSignature,
) -> ValidatedExternalRuntimeBinding:
    """Bind exact bytes after a future reviewed verifier adapter reports success.

    The signature token's Python seal is not a boundary against hostile code in
    this process; callers must not mint it outside that reviewed adapter.
    """

    document = validate_runtime_binding_policy(policy, executable=True)
    jobs = document["runtime"]["jobs"]
    pointers = (("provision", provision_reference), ("cleanup", cleanup_reference))
    for job_kind, candidate in pointers:
        if (
            not isinstance(candidate, ExternalRuntimeBindingReference)
            or not candidate._is_bound()
            or candidate.source_job_kind != job_kind
            or candidate.source_job_resource_id != jobs[job_kind]["resource_id"]
        ):
            _fail("runtime_record.reference_invalid")
    pointer = provision_reference
    if any(getattr(pointer, field) != getattr(cleanup_reference, field) for field in RUNTIME_REFERENCE_FIELDS):
        _fail("runtime_record.reference_divergence")
    maximum = document["external_runtime_record"]["record"]["maximum_bytes"]
    if type(record_bytes) is not bytes or not 1 <= len(record_bytes) <= maximum:
        _fail("runtime_record.bytes")
    observed_digest = _digest(record_bytes)
    signature = signature_verification
    if not isinstance(signature, VerifiedExternalRuntimeRecordSignature) or not signature._is_verified():
        _fail("signature_verification.invalid")
    signature_policy = document["external_runtime_record"]["signature"]
    root = document["evidence_ingress"]["private_versioned_secret_source"]["secrets"]["github_trusted_root"]
    if (
        pointer.record_sha256 != observed_digest
        or signature.record_sha256 != observed_digest
        or signature.bundle_sha256 != pointer.signature_bundle_sha256
        or signature.trusted_root_sha256 != root["sha256"]
        or signature.certificate_oidc_issuer != signature_policy["certificate_oidc_issuer"]
        or signature.certificate_identity != signature_policy["certificate_identity"]
    ):
        _fail("signature_verification.binding")
    runtime_ingress = document["runtime_record_ingress"]
    blob_host = runtime_ingress["account_host"]
    for uri_name, digest_name, suffix in (("record_uri", "record_sha256", ".json"), ("signature_bundle_uri", "signature_bundle_sha256", ".sigstore.json")):
        uri = urllib.parse.urlsplit(getattr(pointer, uri_name))
        expected_path = f"/restore-runtime-bindings-v1/{getattr(pointer, digest_name)[7:]}{suffix}"
        if uri.hostname != blob_host or uri.path != expected_path:
            _fail("runtime_record.reference_binding")
    record = _parse_json_object(record_bytes, "runtime_record.invalid")
    if record_bytes != _canonical(record):
        _fail("runtime_record.noncanonical")
    _object(record, set(RUNTIME_RECORD_FIELDS), "runtime_record.closed_world")
    runtime = document["runtime"]
    environment = runtime["container_apps_environment"]
    jobs = runtime["jobs"]
    identities = document["identities"]["jobs"]
    image_reference = f"{record.get('image_repository')}@{record.get('image_digest')}"
    expected = {
        "schema_version": 1,
        "policy_id": document["policy_id"],
        "policy_binding_sha256": _policy_binding_digest(document),
        **document["external_runtime_record"]["binding"],
        "image_repository": jobs["provision"]["image_repository"],
        "provision_job_image_reference": image_reference,
        "cleanup_job_image_reference": image_reference,
        "environment_resource_id": environment["resource_id"],
        "provision_job_resource_id": jobs["provision"]["resource_id"],
        "provision_job_configuration_template_sha256": jobs["provision"]["configuration_template_sha256"],
        "provision_job_identity_resource_id": identities["provision"]["resource_id"],
        "cleanup_job_resource_id": jobs["cleanup"]["resource_id"],
        "cleanup_job_configuration_template_sha256": jobs["cleanup"]["configuration_template_sha256"],
        "cleanup_job_identity_resource_id": identities["cleanup"]["resource_id"],
        "network_policy_sha256": environment["network_policy_sha256"],
    }
    for name, value in expected.items():
        _exact(record.get(name), value, f"runtime_record.{name}")
    if (
        type(record["record_id"]) is not str
        or RECORD_ID_RE.fullmatch(record["record_id"]) is None
        or type(record["image_digest"]) is not str
        or DIGEST_RE.fullmatch(record["image_digest"]) is None
    ):
        _fail("runtime_record.identity")
    if _utc(record["created_at"], "runtime_record.time") <= _utc(record["image_published_at"], "runtime_record.time"):
        _fail("runtime_record.publication_order")
    return ValidatedExternalRuntimeBinding._from_validated(
        record_bytes=record_bytes, reference=pointer
    )


def load_policy(path: Path = POLICY_PATH, *, executable: bool = False) -> dict[str, Any]:
    """Read and validate only the canonical policy file."""

    if not isinstance(path, Path):
        _fail("policy.untrusted_path")
    raw = _read_canonical_policy(path)
    if hashlib.sha256(raw).hexdigest() != CANONICAL_POLICY_SHA256:
        _fail("policy.digest_mismatch")
    return validate_runtime_binding_policy(_parse_json_object(raw, "policy.invalid_json"), executable=executable)


def _parse_policy(raw: bytes) -> dict[str, Any]:
    return _parse_json_object(raw, "policy.invalid_json")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", action="store_true", help="require all activation gates and exact non-placeholder pins")
    args = parser.parse_args(argv)
    try:
        policy = load_policy(executable=args.executable)
    except RuntimeBindingFailure as exc:
        print(str(exc), file=sys.stderr)
        return 1
    state = "executable" if args.executable else "dormant"
    print(f"{policy['policy_id']} valid ({state}) sha256:{CANONICAL_POLICY_SHA256}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# fmt: on
