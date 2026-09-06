#!/usr/bin/env python3
"""Validate the dormant controller-receipt-v2 production authority."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, NoReturn

try:
    from scripts import read_staging_controller_receipt_v2 as ingress
    from scripts import staging_restore_launch_material as launch_material
    from scripts import validate_staging_controller_receipt_v2 as content
    from scripts import validate_staging_restore_launch_binding as launch
    from scripts import validate_staging_restore_runtime_binding as runtime_binding
except ModuleNotFoundError:  # Direct `python scripts/...` execution.
    import read_staging_controller_receipt_v2 as ingress
    import staging_restore_launch_material as launch_material
    import validate_staging_controller_receipt_v2 as content
    import validate_staging_restore_launch_binding as launch
    import validate_staging_restore_runtime_binding as runtime_binding


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = (
    ROOT / "contracts/staging-rehearsal/v1/controller-receipt-v2-production-policy.json"
)
POLICY_SHA256 = "6c5fd6d89c807df0925d68e20265616cf11da872f4269cb6cde1a1eb0a30831b"
MAXIMUM_POLICY_BYTES = 64 * 1024
PREFIX_RECORD_KINDS = ("intent", "submission")
PRODUCTION_ACTION = "provision"

_PREFIX_SOURCE_SEAL = object()
_PRODUCTION_AUTHORITY_SEAL = object()


class ControllerReceiptV2ProductionFailure(RuntimeError):
    """Bounded refusal without credentials, URIs, or record content."""


def _fail(code: str) -> NoReturn:
    raise ControllerReceiptV2ProductionFailure(code) from None


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
            _fail("controller_receipt_production.policy_invalid")
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
            or hashlib.sha256(raw).hexdigest() != POLICY_SHA256
        ):
            _fail("controller_receipt_production.policy_invalid")
    except ControllerReceiptV2ProductionFailure:
        raise
    except OSError:
        _fail("controller_receipt_production.policy_invalid")
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError):
        _fail("controller_receipt_production.policy_invalid")
    if type(value) is not dict:
        _fail("controller_receipt_production.policy_invalid")
    return value


def validate_controller_receipt_v2_production_policy(
    policy: Any, *, executable: bool = False
) -> dict[str, Any]:
    """Validate the exact downstream policy without activating it."""

    template = _read_pinned_policy()
    try:
        document = json.loads(content._canonical(policy))
    except Exception:
        _fail("controller_receipt_production.policy_invalid")
    expected_activation = {
        "arm_provider_observation_implemented": False,
        "consumer_bindings_implemented": False,
        "controller_receipt_builder_implemented": False,
        "controller_receipt_writer_implemented": False,
        "database_provider_observation_implemented": False,
        "delete_authority_implemented": False,
        "executable": False,
        "live_provider_calls_enabled": False,
        "prefix_reader_implemented": True,
        "production_authority_implemented": True,
        "sanitizer_database_mutation_enabled": False,
        "workflow_integrated": False,
    }
    expected_claims = {
        "controller_receipt_exists": False,
        "executable": False,
        "provider_facts_authenticated": False,
        "record_source_authenticated": True,
        "runtime_authority": False,
        "writer_identity_or_private_route_proven": False,
    }
    upstream = document.get("upstream_policies", {})
    controller_pin = upstream.get("controller_receipt_v2", {})
    runtime_pin = upstream.get("restore_runtime_binding", {})
    prefix = document.get("prefix_source", {})
    authority = document.get("production_authority", {})
    required = authority.get("required_sections", {})
    if (
        type(executable) is not bool
        or content._canonical(document) != content._canonical(template)
        or set(document)
        != {
            "activation",
            "activation_blockers",
            "activation_ready",
            "closed_world",
            "policy_id",
            "prefix_source",
            "production_authority",
            "schema_version",
            "upstream_policies",
        }
        or document.get("schema_version") != 1
        or document.get("policy_id")
        != "cosmaneura-staging-controller-receipt-v2-production-v1"
        or document.get("closed_world") is not True
        or document.get("activation_ready") is not False
        or document.get("activation") != expected_activation
        or not isinstance(document.get("activation_blockers"), list)
        or not document["activation_blockers"]
        or controller_pin
        != {
            "policy_binding_sha256": content._policy_binding_digest(
                content.load_controller_receipt_v2_policy()
            ),
            "policy_id": "cosmaneura-staging-controller-receipt-v2",
            "sha256": f"sha256:{content.POLICY_SHA256}",
        }
        or runtime_pin
        != {
            "policy_binding_sha256": runtime_binding._policy_binding_digest(
                runtime_binding.load_policy()
            ),
            "policy_id": "cosmaneura-staging-restore-runtime-binding-v1",
            "sha256": f"sha256:{runtime_binding.CANONICAL_POLICY_SHA256}",
        }
        or prefix.get("action") != PRODUCTION_ACTION
        or prefix.get("kind")
        != "managed-identity-private-worm-intent-submission-prefix-v1"
        or prefix.get("record_order") != list(PREFIX_RECORD_KINDS)
        or prefix.get("maximum_storage_token_requests") != 1
        or prefix.get("maximum_record_get_requests") != 2
        or prefix.get("maximum_total_http_exchanges") != 3
        or prefix.get(
            "caller_action_rehearsal_path_uri_mapping_or_policy_selector_permitted"
        )
        is not False
        or prefix.get("controller_receipt_existence_claim_permitted") is not False
        or authority.get("kind")
        != "sealed-provision-controller-receipt-v2-production-authority-v1"
        or authority.get("claims") != expected_claims
        or authority.get("retained_exact_record_bytes") != list(PREFIX_RECORD_KINDS)
        or required.get("identity") != list(content.IDENTITY_FIELDS)
        or required.get("authorization") != list(content.AUTHORIZATION_FIELDS)
        or required.get("launch") != list(content.LAUNCH_FIELDS)
        or required.get("source") != list(content.SOURCE_FIELDS)
        or required.get("request")
        != [
            "target_server_resource_id",
            "target_server_name",
            "location",
            "cleanup_not_after",
        ]
    ):
        _fail("controller_receipt_production.policy_invalid")
    if executable:
        _fail("controller_receipt_production.policy_disabled")
    return document


def load_controller_receipt_v2_production_policy(
    *, executable: bool = False
) -> dict[str, Any]:
    return validate_controller_receipt_v2_production_policy(
        _read_pinned_policy(), executable=executable
    )


def _validate_context(
    runtime_policy: Any, controller_policy: Any, production_policy: Any
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    try:
        runtime, controller = ingress._validate_policies(
            runtime_policy, controller_policy
        )
        production = validate_controller_receipt_v2_production_policy(production_policy)
        return runtime, controller, production
    except ControllerReceiptV2ProductionFailure:
        raise
    except Exception:
        _fail("controller_receipt_production.policy_invalid")


def _validate_launch_material(
    runtime: Mapping[str, Any],
    controller: Mapping[str, Any],
    material: Any,
    *,
    validation_now: datetime,
) -> tuple[
    ingress._AuthoritySnapshot,
    launch.ValidatedLaunchRecord,
    Mapping[str, Any],
    runtime_binding.ValidatedExternalRuntimeBinding,
]:
    try:
        if (
            type(material) is not launch_material.ProvisionLaunchMaterial
            or type(material.authenticated_receipt)
            is not launch.AuthenticatedBrokerLaunchReceipt
            or material.authenticated_receipt.action != PRODUCTION_ACTION
            or type(material.launch_record_bytes) is not bytes
            or type(material.authenticated_runtime_record)
            is not launch_material.AuthenticatedRuntimeRecordMaterial
            or not material.authenticated_runtime_record._is_authenticated()
        ):
            _fail("controller_receipt_production.launch_invalid")
        receipt = material.authenticated_receipt
        record = launch.validate_launch_record(
            runtime,
            material.launch_record_bytes,
            record_sha256=receipt.launch_record_sha256,
            record_uri=receipt.launch_record_uri,
        )
        snapshot = ingress._authority_snapshot(runtime, controller, receipt, record)
        if snapshot.action != PRODUCTION_ACTION:
            _fail("controller_receipt_production.launch_invalid")
        reference_type = runtime_binding.ExternalRuntimeBindingReference
        references = {
            action: reference_type._from_authenticated_broker_launch_receipt(
                runtime, receipt, source_job_kind=action
            )
            for action in launch.JOB_KINDS
        }
        validated_runtime = runtime_binding.validate_external_runtime_binding(
            runtime,
            material.authenticated_runtime_record.record_bytes,
            provision_reference=references[PRODUCTION_ACTION],
            cleanup_reference=references["cleanup"],
            signature_verification=(
                material.authenticated_runtime_record.signature_verification
            ),
        )
        signed = ingress._canonical_launch_receipt(runtime, receipt)
        runtime_record = validated_runtime.record
        received = launch.validate_received_launch_receipt(
            runtime,
            authenticated_receipt=receipt,
            launch_record=record,
            runtime_record=validated_runtime,
            validation_now=validation_now,
        )
        if (
            signed["runtime_record_sha256"] != validated_runtime.record_sha256
            or signed["runtime_signature_bundle_sha256"]
            != validated_runtime.signature_bundle_sha256
            or signed["provision_job_image_reference"]
            != validated_runtime.image_reference
            or signed["provision_job_projection_sha256"]
            != runtime_record["provision_job_configuration_template_sha256"]
            or not received._is_validated()
            or received.receipt_sha256 != receipt.receipt_sha256
            or received.execution_resource_id != snapshot.execution_binding
            or received.validated_at != validation_now
            or os.environ.get(launch.EXECUTION_NAME_ENV) != snapshot.execution_name
        ):
            _fail("controller_receipt_production.launch_invalid")
        return snapshot, record, signed, validated_runtime
    except ControllerReceiptV2ProductionFailure:
        raise
    except Exception:
        _fail("controller_receipt_production.launch_invalid")


def _validate_prefix_records(
    runtime: Mapping[str, Any],
    controller: Mapping[str, Any],
    material: launch_material.ProvisionLaunchMaterial,
    *,
    intent_bytes: bytes,
    submission_bytes: bytes,
    validated_at: datetime,
) -> dict[str, Any]:
    snapshot, record, signed, _runtime_record = _validate_launch_material(
        runtime, controller, material, validation_now=validated_at
    )
    maximum = controller["receipt"]["maximum_bytes"]
    try:
        intent = content._parse_canonical(intent_bytes, maximum)
        submission = content._parse_canonical(submission_bytes, maximum)
        content._object(intent, content.INTENT_FIELDS)
        content._object(submission, content.SUBMISSION_FIELDS)
        evidence = content._object(intent["evidence_binding"], content.EVIDENCE_FIELDS)
        evidence_observed, evidence_expires = content._validate_evidence(evidence)
        intent_sha256 = content._digest(intent_bytes)
        submission_sha256 = content._digest(submission_bytes)
        evidence_sha256 = content._digest(content._canonical(evidence))
        safe = controller["upstream_policies"]["safe_submit"]
        authorization_id = content._digest(
            content._canonical(
                {
                    "schema_version": 1,
                    "safe_submit_policy_id": safe["policy_id"],
                    "evidence_binding": evidence,
                }
            )
        )
        rehearsal_id = authorization_id[7:19]
        created = content._utc(intent["created_at"])
        cleanup = content._utc(intent["cleanup_not_after"])
        restored = content._utc(intent["restore_time"])
        expected_tags = {
            "environment": "staging-restore-rehearsal",
            "data-classification": "restricted-restored-copy",
            "managed-by": "staging-restore-envelope-v1",
            "expires-epoch": str(int(cleanup.timestamp())),
            "rehearsal-id": rehearsal_id,
        }
        expected_target_name = f"cnr-{rehearsal_id}-e{int(cleanup.timestamp())}-pg"
        restore_request_sha256 = content._restore_request_sha256(intent, intent_sha256)
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
        fixed = controller["fixed_bindings"]
        identity = runtime["identities"]["jobs"][PRODUCTION_ACTION]
        job = runtime["runtime"]["jobs"][PRODUCTION_ACTION]
        source = controller["record_chain_source"]
        subscription_id = fixed["subscription_id"]
        subscription_bound_ids = (
            job["resource_id"],
            identity["resource_id"],
            fixed["source_server_resource_id"],
            fixed["target_resource_group_id"],
            fixed["executor_resource_group_id"],
            source["account_resource_id"],
            source["private_endpoint_resource_id"],
            source["writer_identity_resource_id"],
            *source["reader_identity_resource_ids"].values(),
            *source["container_resource_ids"].values(),
            source["controller_immutability_policy_resource_id"],
            *source["reader_role_assignment_ids"].values(),
            source["writer_role_assignment_id"],
            intent["target_server_resource_id"],
        )
        launch_evidence_sha256s = dict(record.evidence_file_sha256s)
        expected_launch_evidence_sha256s = {
            "SHA256SUMS": evidence["bundle_index_sha256"],
            "evidence-manifest.json": evidence["manifest_file_sha256"],
        }
        ttl_hours = record.ttl_hours
        maximum_clock_skew = timedelta(
            seconds=controller["freshness"]["maximum_clock_skew_seconds"]
        )
        if (
            type(validated_at) is not datetime
            or validated_at.tzinfo is None
            or validated_at.utcoffset() != timedelta(0)
            or validated_at.microsecond != 0
            or intent["schema_version"] != 4
            or type(intent["schema_version"]) is not int
            or intent["policy_id"] != safe["policy_id"]
            or intent["envelope_policy_id"]
            != controller["upstream_policies"]["restore_envelope"]["policy_id"]
            or intent["envelope_policy_sha256"]
            != controller["upstream_policies"]["restore_envelope"]["sha256"]
            or intent["authorization_id"] != authorization_id
            or intent["rehearsal_id"] != rehearsal_id
            or rehearsal_id != snapshot.rehearsal_id
            or intent["source_server_resource_id"]
            != evidence["source_server_resource_id"]
            or intent["source_server_resource_id"] != fixed["source_server_resource_id"]
            or intent["repository"] != evidence["repository"]
            or intent["repository"] != fixed["repository"]
            or tuple(intent[name] for name in ("workflow_sha", "run_id", "run_attempt"))
            != snapshot.launch_source
            or tuple(
                evidence[name] for name in ("workflow_sha", "run_id", "run_attempt")
            )
            != snapshot.launch_source
            or intent["provision_client_id"] != identity["client_id"]
            or intent["provision_principal_id"] != identity["principal_id"]
            or restored != content._utc(evidence["approved_restore_time"])
            or created != evidence_observed
            or not created < evidence_expires
            or not created < cleanup
            or type(ttl_hours) is not int
            or cleanup != created + timedelta(hours=ttl_hours)
            or created > record.created_at + maximum_clock_skew
            or created > validated_at + maximum_clock_skew
            or record.created_at >= evidence_expires
            or not snapshot.authenticated_receipt_observed_at
            <= validated_at
            < min(snapshot.launch_expires_at, cleanup)
            or any(
                launch_evidence_sha256s.get(name) != sha256
                for name, sha256 in expected_launch_evidence_sha256s.items()
            )
            or any(
                not content._azure_id(resource_id)
                or resource_id.split("/")[2].casefold() != subscription_id.casefold()
                for resource_id in subscription_bound_ids
            )
            or intent["location"] != fixed["restore_location"]
            or intent["required_tags"] != expected_tags
            or intent["target_server_name"] != expected_target_name
            or not content._azure_id(intent["target_server_resource_id"])
            or content._resource_type(intent["target_server_resource_id"])
            != "microsoft.dbforpostgresql/flexibleservers"
            or content._resource_group(intent["target_server_resource_id"]).casefold()
            != fixed["target_resource_group_id"].casefold()
            or intent["target_server_resource_id"].split("/")[-1]
            != expected_target_name
            or content._canonical(submission) != content._canonical(expected_submission)
        ):
            _fail("controller_receipt_production.prefix_invalid")
        return {
            "identity": {
                "tenant_id": fixed["tenant_id"],
                "subscription_id": fixed["subscription_id"],
                "provision_job_resource_id": job["resource_id"],
                "provision_job_uami_resource_id": identity["resource_id"],
                "provision_job_uami_client_id": identity["client_id"],
                "provision_job_uami_principal_id": identity["principal_id"],
            },
            "authorization": {
                "rehearsal_id": rehearsal_id,
                "authorization_id": authorization_id,
                "evidence_binding_sha256": evidence_sha256,
                "intent_sha256": intent_sha256,
                "submission_sha256": submission_sha256,
                "restore_request_sha256": restore_request_sha256,
            },
            "launch": {
                "launch_record_sha256": snapshot.launch_record_sha256,
                "broker_launch_receipt_sha256": snapshot.broker_launch_receipt_sha256,
                "runtime_record_sha256": signed["runtime_record_sha256"],
                "runtime_signature_bundle_sha256": signed[
                    "runtime_signature_bundle_sha256"
                ],
                "execution_resource_id": snapshot.execution_binding,
                "execution_name": snapshot.execution_name,
                "launch_expires_at": snapshot.launch_expires_at.strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                ),
                "provision_job_configuration_template_sha256": signed[
                    "provision_job_projection_sha256"
                ],
                "provision_job_image_reference": signed[
                    "provision_job_image_reference"
                ],
            },
            "source": {name: evidence[name] for name in content.SOURCE_FIELDS},
            "request": {
                "target_server_resource_id": intent["target_server_resource_id"],
                "target_server_name": intent["target_server_name"],
                "location": intent["location"],
                "cleanup_not_after": intent["cleanup_not_after"],
            },
        }
    except ControllerReceiptV2ProductionFailure:
        raise
    except Exception:
        _fail("controller_receipt_production.prefix_invalid")


def _prefix_carrier_sha256(
    *,
    bindings: Mapping[str, Any],
    validated_at: datetime,
    objects: tuple[ingress._AuthenticatedPrivateWormObject, ...],
) -> str:
    return content._digest(
        content._canonical(
            {
                "bindings": bindings,
                "controller_receipt_exists": False,
                "metadata_sha256": ingress._metadata_digest(objects),
                "provider_facts_authenticated": False,
                "record_source_authenticated": True,
                "runtime_authority": False,
                "validated_at": validated_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "writer_identity_or_private_route_proven": False,
            }
        )
    )


@dataclass(frozen=True, slots=True, init=False, repr=False)
class AuthenticatedControllerReceiptV2PrefixSource:
    """Authenticated intent/submission source; it makes no receipt claim."""

    intent_bytes: bytes = dataclass_field(repr=False)
    submission_bytes: bytes = dataclass_field(repr=False)
    intent_sha256: str
    submission_sha256: str
    rehearsal_id: str
    execution_binding: str
    validated_at: datetime
    record_source_authenticated: bool
    controller_receipt_exists: bool
    provider_facts_authenticated: bool
    runtime_authority: bool
    writer_identity_or_private_route_proven: bool
    _objects: tuple[ingress._AuthenticatedPrivateWormObject, ...] = dataclass_field(
        repr=False, compare=False
    )
    _carrier_sha256: str = dataclass_field(repr=False, compare=False)
    _seal: object = dataclass_field(repr=False, compare=False)

    def __init__(self) -> None:
        _fail("controller_receipt_production.prefix_unsealed")

    def _is_authenticated_for(
        self,
        *,
        runtime_policy: Any,
        controller_policy: Any,
        launch_material_value: launch_material.ProvisionLaunchMaterial,
    ) -> bool:
        try:
            runtime, controller = ingress._validate_policies(
                runtime_policy, controller_policy
            )
            snapshot, _record, _signed, _runtime = _validate_launch_material(
                runtime,
                controller,
                launch_material_value,
                validation_now=self.validated_at,
            )
            bindings = _validate_prefix_records(
                runtime,
                controller,
                launch_material_value,
                intent_bytes=self.intent_bytes,
                submission_bytes=self.submission_bytes,
                validated_at=self.validated_at,
            )
            expected_uris = tuple(
                uri for kind, uri in snapshot.object_uris if kind in PREFIX_RECORD_KINDS
            )
            evidence = content._object(
                content._parse_canonical(
                    self.intent_bytes, content.MAXIMUM_POLICY_BYTES
                )["evidence_binding"],
                content.EVIDENCE_FIELDS,
            )
            _evidence_observed_at, evidence_expires_at = content._validate_evidence(
                evidence
            )
            modified_at = tuple(
                content._utc(item.last_modified) for item in self._objects
            )
            clock_skew = timedelta(
                seconds=controller["freshness"]["maximum_clock_skew_seconds"]
            )
            return (
                type(self) is AuthenticatedControllerReceiptV2PrefixSource
                and self._seal is _PREFIX_SOURCE_SEAL
                and self.intent_sha256 == content._digest(self.intent_bytes)
                and self.submission_sha256 == content._digest(self.submission_bytes)
                and self.intent_sha256 == bindings["authorization"]["intent_sha256"]
                and self.submission_sha256
                == bindings["authorization"]["submission_sha256"]
                and self.rehearsal_id == snapshot.rehearsal_id
                and self.execution_binding == snapshot.execution_binding
                and self.record_source_authenticated is True
                and self.controller_receipt_exists is False
                and self.provider_facts_authenticated is False
                and self.runtime_authority is False
                and self.writer_identity_or_private_route_proven is False
                and len(self._objects) == 2
                and all(item._is_authenticated() for item in self._objects)
                and tuple(item.kind for item in self._objects) == PREFIX_RECORD_KINDS
                and tuple(item.uri for item in self._objects) == expected_uris
                and tuple(item.sha256 for item in self._objects)
                == (self.intent_sha256, self.submission_sha256)
                and snapshot.authenticated_receipt_observed_at
                <= modified_at[0] + clock_skew
                and modified_at[0] <= modified_at[1]
                and modified_at[1] <= evidence_expires_at + clock_skew
                and modified_at[1] <= self.validated_at + clock_skew
                and self._carrier_sha256
                == _prefix_carrier_sha256(
                    bindings=bindings,
                    validated_at=self.validated_at,
                    objects=self._objects,
                )
            )
        except Exception:
            return False


def _mint_prefix_source(
    *,
    runtime: Mapping[str, Any],
    controller: Mapping[str, Any],
    material: launch_material.ProvisionLaunchMaterial,
    objects: tuple[ingress._AuthenticatedPrivateWormObject, ...],
    validated_at: datetime,
) -> AuthenticatedControllerReceiptV2PrefixSource:
    if (
        type(objects) is not tuple
        or len(objects) != 2
        or tuple(getattr(item, "kind", None) for item in objects) != PREFIX_RECORD_KINDS
        or not all(item._is_authenticated() for item in objects)
    ):
        _fail("controller_receipt_production.prefix_invalid")
    bindings = _validate_prefix_records(
        runtime,
        controller,
        material,
        intent_bytes=objects[0].body,
        submission_bytes=objects[1].body,
        validated_at=validated_at,
    )
    value = object.__new__(AuthenticatedControllerReceiptV2PrefixSource)
    fields = {
        "intent_bytes": bytes(objects[0].body),
        "submission_bytes": bytes(objects[1].body),
        "intent_sha256": objects[0].sha256,
        "submission_sha256": objects[1].sha256,
        "rehearsal_id": bindings["authorization"]["rehearsal_id"],
        "execution_binding": bindings["launch"]["execution_resource_id"],
        "validated_at": validated_at,
        "record_source_authenticated": True,
        "controller_receipt_exists": False,
        "provider_facts_authenticated": False,
        "runtime_authority": False,
        "writer_identity_or_private_route_proven": False,
        "_objects": objects,
        "_carrier_sha256": _prefix_carrier_sha256(
            bindings=bindings, validated_at=validated_at, objects=objects
        ),
    }
    for name, item in fields.items():
        object.__setattr__(value, name, item)
    object.__setattr__(value, "_seal", _PREFIX_SOURCE_SEAL)
    if not value._is_authenticated_for(
        runtime_policy=runtime,
        controller_policy=controller,
        launch_material_value=material,
    ):
        _fail("controller_receipt_production.prefix_invalid")
    return value


def _authority_document(
    *,
    production: Mapping[str, Any],
    bindings: Mapping[str, Any],
    validated_at: datetime,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "kind": production["production_authority"]["kind"],
        "production_policy_id": production["policy_id"],
        "production_policy_sha256": f"sha256:{POLICY_SHA256}",
        **bindings,
        "validated_at": validated_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        **production["production_authority"]["claims"],
    }


@dataclass(frozen=True, slots=True, init=False, repr=False)
class ControllerReceiptV2ProductionAuthority:
    """Sealed provision authority over exact prefix and launch/runtime inputs."""

    authority_bytes: bytes = dataclass_field(repr=False)
    intent_bytes: bytes = dataclass_field(repr=False)
    submission_bytes: bytes = dataclass_field(repr=False)
    intent_sha256: str
    submission_sha256: str
    rehearsal_id: str
    execution_binding: str
    validated_at: datetime
    record_source_authenticated: bool
    controller_receipt_exists: bool
    provider_facts_authenticated: bool
    runtime_authority: bool
    writer_identity_or_private_route_proven: bool
    executable: bool
    _prefix: AuthenticatedControllerReceiptV2PrefixSource = dataclass_field(
        repr=False, compare=False
    )
    _launch_material: launch_material.ProvisionLaunchMaterial = dataclass_field(
        repr=False, compare=False
    )
    _runtime_policy: bytes = dataclass_field(repr=False, compare=False)
    _controller_policy: bytes = dataclass_field(repr=False, compare=False)
    _production_policy: bytes = dataclass_field(repr=False, compare=False)
    _authority_sha256: str = dataclass_field(repr=False, compare=False)
    _seal: object = dataclass_field(repr=False, compare=False)

    def __init__(self) -> None:
        _fail("controller_receipt_production.authority_unsealed")

    @property
    def authority(self) -> dict[str, Any]:
        if not self._is_authenticated():
            _fail("controller_receipt_production.authority_invalid")
        return content._parse_canonical(self.authority_bytes, MAXIMUM_POLICY_BYTES)

    def _is_authenticated(self) -> bool:
        try:
            runtime, controller, production = _validate_context(
                runtime_binding._parse_policy(self._runtime_policy),
                content._parse_canonical(
                    self._controller_policy, content.MAXIMUM_POLICY_BYTES
                ),
                content._parse_canonical(self._production_policy, MAXIMUM_POLICY_BYTES),
            )
            bindings = _validate_prefix_records(
                runtime,
                controller,
                self._launch_material,
                intent_bytes=self.intent_bytes,
                submission_bytes=self.submission_bytes,
                validated_at=self.validated_at,
            )
            expected = _authority_document(
                production=production,
                bindings=bindings,
                validated_at=self.validated_at,
            )
            expected_bytes = content._canonical(expected)
            return (
                type(self) is ControllerReceiptV2ProductionAuthority
                and self._seal is _PRODUCTION_AUTHORITY_SEAL
                and self._prefix._is_authenticated_for(
                    runtime_policy=runtime,
                    controller_policy=controller,
                    launch_material_value=self._launch_material,
                )
                and self.authority_bytes == expected_bytes
                and self.intent_bytes == self._prefix.intent_bytes
                and self.submission_bytes == self._prefix.submission_bytes
                and self.intent_sha256 == self._prefix.intent_sha256
                and self.submission_sha256 == self._prefix.submission_sha256
                and self.rehearsal_id == self._prefix.rehearsal_id
                and self.execution_binding == self._prefix.execution_binding
                and self.validated_at == self._prefix.validated_at
                and self.record_source_authenticated is True
                and self.controller_receipt_exists is False
                and self.provider_facts_authenticated is False
                and self.runtime_authority is False
                and self.writer_identity_or_private_route_proven is False
                and self.executable is False
                and self._authority_sha256
                == content._digest(
                    content._canonical(
                        {
                            "authority_sha256": content._digest(expected_bytes),
                            "prefix_carrier_sha256": self._prefix._carrier_sha256,
                            "production_policy_sha256": f"sha256:{POLICY_SHA256}",
                        }
                    )
                )
            )
        except Exception:
            return False


def _bind_production_authority(
    *,
    runtime: Mapping[str, Any],
    controller: Mapping[str, Any],
    production: Mapping[str, Any],
    prefix: AuthenticatedControllerReceiptV2PrefixSource,
    material: launch_material.ProvisionLaunchMaterial,
) -> ControllerReceiptV2ProductionAuthority:
    if not prefix._is_authenticated_for(
        runtime_policy=runtime,
        controller_policy=controller,
        launch_material_value=material,
    ):
        _fail("controller_receipt_production.prefix_invalid")
    bindings = _validate_prefix_records(
        runtime,
        controller,
        material,
        intent_bytes=prefix.intent_bytes,
        submission_bytes=prefix.submission_bytes,
        validated_at=prefix.validated_at,
    )
    authority_bytes = content._canonical(
        _authority_document(
            production=production,
            bindings=bindings,
            validated_at=prefix.validated_at,
        )
    )
    value = object.__new__(ControllerReceiptV2ProductionAuthority)
    fields = {
        "authority_bytes": authority_bytes,
        "intent_bytes": bytes(prefix.intent_bytes),
        "submission_bytes": bytes(prefix.submission_bytes),
        "intent_sha256": prefix.intent_sha256,
        "submission_sha256": prefix.submission_sha256,
        "rehearsal_id": prefix.rehearsal_id,
        "execution_binding": prefix.execution_binding,
        "validated_at": prefix.validated_at,
        "record_source_authenticated": True,
        "controller_receipt_exists": False,
        "provider_facts_authenticated": False,
        "runtime_authority": False,
        "writer_identity_or_private_route_proven": False,
        "executable": False,
        "_prefix": prefix,
        "_launch_material": material,
        "_runtime_policy": runtime_binding._canonical(runtime),
        "_controller_policy": content._canonical(controller),
        "_production_policy": content._canonical(production),
    }
    for name, item in fields.items():
        object.__setattr__(value, name, item)
    object.__setattr__(
        value,
        "_authority_sha256",
        content._digest(
            content._canonical(
                {
                    "authority_sha256": content._digest(authority_bytes),
                    "prefix_carrier_sha256": prefix._carrier_sha256,
                    "production_policy_sha256": f"sha256:{POLICY_SHA256}",
                }
            )
        ),
    )
    object.__setattr__(value, "_seal", _PRODUCTION_AUTHORITY_SEAL)
    if not value._is_authenticated():
        _fail("controller_receipt_production.authority_invalid")
    return value
