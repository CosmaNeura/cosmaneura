#!/usr/bin/env python3
"""Dormant contract and pure primitives for isolated-restore sanitization.

This unit deliberately has no PostgreSQL dependency and no connection, commit,
or mutation path.  The canonical policy must refuse activation before a future
trusted adapter is allowed to read credentials or contact a database.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import re
import sys
import urllib.parse
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, NoReturn

try:
    from scripts import build_staging_controller_receipt_v2 as receipt_builder
    from scripts import staging_synthetic_inventory as inventory
    from scripts import validate_staging_controller_receipt_v2 as receipt_content
    from scripts import write_staging_controller_receipt_v2 as receipt_writer
except ModuleNotFoundError:  # Direct `python scripts/...` execution compatibility.
    import build_staging_controller_receipt_v2 as receipt_builder
    import staging_synthetic_inventory as inventory
    import validate_staging_controller_receipt_v2 as receipt_content
    import write_staging_controller_receipt_v2 as receipt_writer

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "contracts/staging-rehearsal/v1/restore-sanitizer-policy.json"
DATA_CONTRACT_PATH = (
    ROOT / "contracts/staging-rehearsal/v1/restore-sanitizer-data-contract.json"
)
INVENTORY_POLICY_PATH = (
    ROOT / "contracts/staging-rehearsal/v1/synthetic-data-policy.json"
)
CANONICAL_POLICY_SHA256 = (
    "94da2806468324a5307e1b2f34080fdf23c5733b0b395cc21cafb5ddc6d6c03d"
)
CANONICAL_DATA_CONTRACT_SHA256 = (
    "f1c0c7dce56cd2aa42647578940f807186d8e888b70c66da9aa64aa7402b1360"
)
APP_NAME = "cosmaneura-staging-restore-sanitizer-v1"
HMAC_DOMAIN = "cosmaneura:staging-restore-sanitizer:v1"
MINIMUM_KEY_BYTES = 32
UUID7_EPOCH_START_MS = 1_577_836_800_000  # 2020-01-01T00:00:00Z
UUID7_EPOCH_SPAN_MS = 315_576_000_000  # ten Julian years
ACCESS_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
DOMAIN_RE = re.compile(r"^[a-z0-9][a-z0-9:._-]{0,127}$")
ROLE_RE = re.compile(r"^[a-z][a-z0-9_]{2,62}$")
MAXIMUM_POLICY_BYTES = 64 * 1024
MAXIMUM_INVENTORY_RECEIPT_BYTES = 256 * 1024
_TARGET_BINDING_SEAL = object()
_BINDING_FAILURE_CODES = frozenset(
    {
        "sanitizer_binding.authority_invalid",
        "sanitizer_binding.clock_invalid",
        "sanitizer_binding.clock_regressed",
        "sanitizer_binding.configuration_invalid",
        "sanitizer_binding.expired",
        "sanitizer_binding.output_invalid",
        "sanitizer_binding.policy_invalid",
    }
)

TOP_LEVEL_KEYS = {
    "schema_version",
    "policy_id",
    "closed_world",
    "status",
    "inventory",
    "target",
    "controller_receipt_v2_binding",
    "fixed_catalog_source_state_binding",
    "transform_contract",
    "execution_contract",
    "migration_contract",
    "evidence_contract",
    "limitations",
}
FIXED_CATALOG_TABLE_ORDER = (
    "baa_version",
    "doctor_note_schema",
    "drug_reference",
    "lab_range_set",
    "note_type_customization",
)
FIXED_CATALOG_TABLES = set(FIXED_CATALOG_TABLE_ORDER)
GUARDED_TABLES = {
    "authority_person_origin",
    "baa_acceptance",
    "organization_roster_approval",
    "pilot_access_continuity_amendment",
    "pilot_access_continuity_amendment_identity",
    "pilot_access_grandfather_grant",
    "practice_access_audit",
    "practice_member",
    "superadmin_capability_audit",
    "superadmin_capability_grant",
}
MIGRATION_FILES = {
    "migrations/versions/2026_08_27_d0a4c8e2f611_add_clinician_seat_state.py": (
        "14db2216f5afbd89676d6d596d13b4afd97f3897da28ea0f979ee79f17725895"
    ),
    "migrations/versions/2026_08_27_e7a9c3d5f102_add_public_onboarding_evidence.py": (
        "75d6efa8a4633b95b643e40367dbbca2ab29fd38e1ca09fe5069f83684dde62f"
    ),
    "migrations/versions/2026_08_27_da6e199668a4_persist_stripe_checkout_state.py": (
        "20929861af93cd154e294e1a2c18afc070fad392d9abcd86905200c76f03d7dc"
    ),
    "migrations/versions/2026_08_29_9a2399212488_publish_baa_v2_3.py": (
        "b1f2db1d21bb4587b92f84f6ad846377d58db046ee7e76291e1aad213bf5a715"
    ),
}

DATA_CONTRACT_KEYS = {
    "schema_version",
    "contract_id",
    "closed_world",
    "source_profile",
    "retained_table_count",
    "retained_column_count",
    "row_actions",
    "tables",
    "foreign_keys",
    "derived_values",
}
TABLE_CONTRACT_KEYS = {"disposition", "row_action", "columns"}
ACTIVATION_BLOCKERS = {
    "authenticated_server_scope_evidence_binding",
    "data_contract_executor_implementation",
    "fixed_catalog_post_projection_verification",
    "derived_value_execution_proof",
    "atomic_transaction_and_postcommit_verification",
    "guarded_table_truncate_permission_and_rollback_proof",
}
FIXED_CATALOG_SOURCE_STATE_CLAIMS = {
    "controller_receipt_v2_target_binding_authenticated": True,
    "fixed_catalog_source_state_authenticated": True,
    "inventory_receipt_digest_authenticated": True,
    "database_connection_authority": False,
    "database_mutation_authority": False,
    "executable": False,
    "live_provider_calls_enabled": False,
    "post_projection_authenticated": False,
    "provider_calls_permitted": False,
    "provider_facts_authenticated": False,
    "runtime_authority": False,
    "sanitizer_authority": False,
    "server_scope_authenticated": False,
    "workflow_integrated": False,
}
FIXED_CATALOG_CONTEXT_FIELDS = (
    "source_sha",
    "candidate_sha",
    "source_server_resource_id",
    "source_database_name",
    "approved_restore_time",
    "workflow_sha",
    "run_id",
    "run_attempt",
    "repository",
    "protected_ref",
    "workflow_path",
    "signer_issuer",
    "signer_subject",
)
FIXED_CATALOG_PUBLIC_FIELDS = (
    "inventory_receipt_sha256",
    "inventory_policy_sha256",
    "approved_state_sha256",
    "fixed_catalog_projection_sha256",
    "hmac_key_version",
    "source_profile",
    "source_revision",
    "inventory_observed_at",
    "inventory_expires_at",
    "target_binding_expires_at",
    "authenticated_at",
)
BINDING_CLAIMS = {
    "arm_network_facts_authenticated": True,
    "content_built_and_validated": True,
    "controller_receipt_exists": True,
    "controller_receipt_v2_target_binding_authenticated": True,
    "database_role_authenticated": True,
    "full_three_record_source_authenticated": True,
    "intent_submission_source_authenticated": True,
    "point_in_time_isolation_inventory_authenticated": True,
    "private_route_reachability_authenticated": True,
    "tls_transport_authenticated": True,
    "blob_or_redis_copy_absence_proven": False,
    "create_only_behavior_proven": False,
    "database_mutation_authority": False,
    "executable": False,
    "execution_history_authenticated": False,
    "live_provider_calls_enabled": False,
    "provider_facts_authenticated": False,
    "runtime_authority": False,
    "sanitizer_authority": False,
    "workflow_integrated": False,
    "writer_identity_use_authenticated": False,
    "writer_private_route_use_authenticated": False,
}
BINDING_FIELDS = (
    "rehearsal_id",
    "target_fqdn",
    "database_name",
    "approved_database_role",
    "target_postgres_server_id",
    "approved_restore_time",
    "source_sha",
    "candidate_sha",
    "workflow_sha",
    "run_id",
    "run_attempt",
    "receipt_sha256",
    "expires_at",
    "bound_at",
)
SIMPLE_COLUMN_ACTIONS = {
    "discard_row",
    "preserve_approved",
    "preserve_verified_catalog",
    "set_null",
    "hmac_email",
    "hmac_domain",
    "hmac_issuer",
    "hmac_subject",
    "derive_typeid_text:appointment",
    "rotate_join_code",
    "derive_access_code_salt",
    "derive_access_code_digest",
}
ENTITY_ID_DOMAINS = {
    "appointment": "typeid:appointment",
    "authority_actor": "typeid:authority_actor",
    "authority_actor_external_identity": "typeid:actor_identity",
    "authority_person": "typeid:authority_person",
    "authority_person_binding": "typeid:person_binding",
    "authority_tenant": "typeid:authority_tenant",
    "authority_tenant_membership": "typeid:tenant_membership",
    "baa_version": "typeid:baa_version",
    "doctor": "typeid:doctor",
    "doctor_note_schema": "typeid:doctor_note_schema",
    "drug_reference": "typeid:drug_reference",
    "ehr_sandbox_patient_designation": "typeid:ehr_sandbox_designation",
    "ehr_sandbox_record_mapping": "typeid:ehr_sandbox_mapping",
    "lab_range_set": "typeid:lab_range_set",
    "note_type_customization": "typeid:note_type_customization",
    "patient": "typeid:patient",
    "practice": "typeid:practice",
    "provider_organization": "typeid:provider_organization",
    "synthetic_preview_appointment_fixture": "typeid:synthetic_preview_fixture",
}
EXPECTED_FOREIGN_KEYS = {
    (("appointment.doctor_id",), ("doctor.id",)),
    (("appointment.patient_id",), ("patient.id",)),
    (("authority_actor_external_identity.actor_id",), ("authority_actor.id",)),
    (("authority_person.merged_into_person_id",), ("authority_person.id",)),
    (("authority_person_binding.person_id",), ("authority_person.id",)),
    (("authority_person_binding.tenant_id",), ("authority_tenant.id",)),
    (("authority_person_binding.actor_id",), ("authority_actor.id",)),
    (("authority_person_origin.person_id",), ("authority_person.id",)),
    (("authority_tenant_membership.tenant_id",), ("authority_tenant.id",)),
    (("authority_tenant_membership.actor_id",), ("authority_actor.id",)),
    (("baa_acceptance.accepted_by_doctor_id",), ("doctor.id",)),
    (("baa_acceptance.baa_version_id",), ("baa_version.id",)),
    (("baa_acceptance.practice_id",), ("practice.id",)),
    (("doctor_note_schema.doctor_id",), ("doctor.id",)),
    (("ehr_sandbox_patient_designation.doctor_id",), ("doctor.id",)),
    (("ehr_sandbox_patient_designation.patient_id",), ("patient.id",)),
    (
        (
            "ehr_sandbox_patient_designation.patient_id",
            "ehr_sandbox_patient_designation.doctor_id",
        ),
        ("patient.id", "patient.doctor_id"),
    ),
    (("ehr_sandbox_record_mapping.doctor_id",), ("doctor.id",)),
    (("ehr_sandbox_record_mapping.patient_id",), ("patient.id",)),
    (
        ("ehr_sandbox_record_mapping.designation_id",),
        ("ehr_sandbox_patient_designation.id",),
    ),
    (
        (
            "ehr_sandbox_record_mapping.designation_id",
            "ehr_sandbox_record_mapping.doctor_id",
            "ehr_sandbox_record_mapping.patient_id",
        ),
        (
            "ehr_sandbox_patient_designation.id",
            "ehr_sandbox_patient_designation.doctor_id",
            "ehr_sandbox_patient_designation.patient_id",
        ),
    ),
    (("lab_range_set.doctor_id",), ("doctor.id",)),
    (
        ("note_type_customization.doctor_note_schema_id",),
        ("doctor_note_schema.id",),
    ),
    (("organization_roster_approval.claimed_doctor_id",), ("doctor.id",)),
    (
        ("organization_roster_approval.organization_id",),
        ("provider_organization.id",),
    ),
    (
        (
            "organization_roster_approval.target_practice_id",
            "organization_roster_approval.organization_id",
        ),
        ("practice.id", "practice.organization_id"),
    ),
    (("patient.doctor_id",), ("doctor.id",)),
    (
        ("pilot_access_continuity_amendment_identity.amendment_id",),
        ("pilot_access_continuity_amendment.id",),
    ),
    (
        ("pilot_access_continuity_amendment_identity.doctor_id_snapshot",),
        ("doctor.id",),
    ),
    (
        ("pilot_access_continuity_amendment_identity.grant_id",),
        ("pilot_access_grandfather_grant.id",),
    ),
    (
        ("pilot_access_continuity_amendment_identity.practice_id_snapshot",),
        ("practice.id",),
    ),
    (("pilot_access_grandfather_grant.doctor_id_snapshot",), ("doctor.id",)),
    (("pilot_access_grandfather_grant.practice_id_snapshot",), ("practice.id",)),
    (("practice.organization_id",), ("provider_organization.id",)),
    (
        ("practice.organization_id", "practice.email_domain"),
        ("provider_organization.id", "provider_organization.email_domain"),
    ),
    (("practice_access_audit.practice_id",), ("practice.id",)),
    (("practice_member.doctor_id",), ("doctor.id",)),
    (
        ("practice_member.organization_roster_approval_id",),
        ("organization_roster_approval.id",),
    ),
    (
        (
            "practice_member.organization_roster_approval_id",
            "practice_member.practice_id",
            "practice_member.doctor_id",
        ),
        (
            "organization_roster_approval.id",
            "organization_roster_approval.target_practice_id",
            "organization_roster_approval.claimed_doctor_id",
        ),
    ),
    (("practice_member.practice_id",), ("practice.id",)),
    (
        ("superadmin_capability_audit.grant_id",),
        ("superadmin_capability_grant.id",),
    ),
    (("superadmin_capability_grant.doctor_id",), ("doctor.id",)),
    (("synthetic_preview_appointment_fixture.doctor_id",), ("doctor.id",)),
    (("synthetic_preview_appointment_fixture.patient_id",), ("patient.id",)),
    (
        ("synthetic_preview_appointment_fixture.appointment_id",),
        ("appointment.id",),
    ),
    (
        (
            "synthetic_preview_appointment_fixture.patient_id",
            "synthetic_preview_appointment_fixture.doctor_id",
        ),
        ("patient.id", "patient.doctor_id"),
    ),
}
EXPECTED_DERIVED_VALUES = {
    "authority_person_binding.binding_ref": {
        "recipe": "typeid_text_from_transformed_uuid",
        "source": ["appointment.id"],
        "required_source_values": {
            "authority_person_binding.binding_kind": ["provider_record"]
        },
        "typeid_prefix": "appointment",
    },
    "practice.join_code": {
        "recipe": "hmac_join_code_from_transformed_uuid",
        "source": ["practice.id"],
        "alphabet": ACCESS_CODE_ALPHABET,
        "length": 12,
    },
    "provider_organization.access_code_salt": {
        "recipe": "hmac_salt_from_transformed_uuid",
        "source": ["provider_organization.id"],
        "bytes": 16,
    },
    "provider_organization.access_code_digest": {
        "recipe": "scrypt_of_hidden_hmac_code",
        "source": [
            "provider_organization.id",
            "provider_organization.access_code_salt",
        ],
        "code_alphabet": ACCESS_CODE_ALPHABET,
        "code_length": 12,
        "scrypt_n": 16384,
        "scrypt_r": 8,
        "scrypt_p": 1,
        "digest_bytes": 32,
        "plaintext_emitted": False,
    },
}


class SanitizerFailure(RuntimeError):
    """A bounded, content-free refusal code."""


def _fail(code: str) -> NoReturn:
    raise SanitizerFailure(code)


@dataclass(frozen=True, slots=True, init=False, repr=False)
class SanitizerTargetBinding:
    """Sealed in-process receipt-v2 authority for a future sanitizer adapter."""

    rehearsal_id: str
    target_fqdn: str
    database_name: str
    approved_database_role: str
    target_postgres_server_id: str
    approved_restore_time: datetime
    source_sha: str
    candidate_sha: str
    workflow_sha: str
    run_id: str
    run_attempt: str
    receipt_sha256: str
    expires_at: datetime
    bound_at: datetime
    arm_network_facts_authenticated: bool
    content_built_and_validated: bool
    controller_receipt_exists: bool
    controller_receipt_v2_target_binding_authenticated: bool
    database_role_authenticated: bool
    full_three_record_source_authenticated: bool
    intent_submission_source_authenticated: bool
    point_in_time_isolation_inventory_authenticated: bool
    private_route_reachability_authenticated: bool
    tls_transport_authenticated: bool
    blob_or_redis_copy_absence_proven: bool
    create_only_behavior_proven: bool
    database_mutation_authority: bool
    executable: bool
    execution_history_authenticated: bool
    live_provider_calls_enabled: bool
    provider_facts_authenticated: bool
    runtime_authority: bool
    sanitizer_authority: bool
    workflow_integrated: bool
    writer_identity_use_authenticated: bool
    writer_private_route_use_authenticated: bool
    _written_receipt: receipt_writer.WrittenControllerReceiptV2 = dataclass_field(
        repr=False, compare=False
    )
    _policy_bytes: bytes = dataclass_field(repr=False, compare=False)
    _proof_sha256: str = dataclass_field(repr=False, compare=False)
    _seal: object = dataclass_field(repr=False, compare=False)

    def __init__(self) -> None:
        _fail("sanitizer_binding.output_invalid")

    def __reduce__(self) -> NoReturn:
        _fail("sanitizer_binding.output_invalid")

    def __reduce_ex__(self, protocol: object) -> NoReturn:
        del protocol
        _fail("sanitizer_binding.output_invalid")

    def _is_bound(self) -> bool:
        try:
            policy = json.loads(self._policy_bytes)
            inventory_policy = _load_json(
                INVENTORY_POLICY_PATH, "inventory_policy.invalid"
            )
            validate_policy(policy, inventory_policy)
            if self._policy_bytes != _canonical_json(policy):
                return False
            written = self._written_receipt
            built = written._built_receipt
            receipt = receipt_content._parse_canonical(
                built.receipt_bytes, receipt_content.MAXIMUM_POLICY_BYTES
            )
            projection = {name: getattr(self, name) for name in BINDING_FIELDS}
            return (
                type(self) is SanitizerTargetBinding
                and self._seal is _TARGET_BINDING_SEAL
                and type(written) is receipt_writer.WrittenControllerReceiptV2
                and written._seal is receipt_writer._RESULT_SEAL
                and written._is_authenticated()
                and type(built) is receipt_builder.BuiltControllerReceiptV2
                and built._seal is receipt_builder._BUILT_SEAL
                and receipt_content._canonical(receipt) == built.receipt_bytes
                and receipt_content._digest(built.receipt_bytes)
                == self.receipt_sha256
                == written.receipt_sha256
                == built.receipt_sha256
                and receipt["authorization"]["rehearsal_id"] == self.rehearsal_id
                and receipt["target"]["target_fqdn"] == self.target_fqdn
                and receipt["target"]["approved_database_role"]
                == self.approved_database_role
                and receipt["target"]["target_postgres_server_id"]
                == self.target_postgres_server_id
                and receipt["source"]["source_database_name"] == self.database_name
                and all(
                    receipt["source"][name] == getattr(self, name)
                    for name in (
                        "source_sha",
                        "candidate_sha",
                        "workflow_sha",
                        "run_id",
                        "run_attempt",
                    )
                )
                and receipt_content._utc(receipt["source"]["approved_restore_time"])
                == self.approved_restore_time
                and receipt_content._utc(receipt["expires_at"]) == self.expires_at
                and type(self.bound_at) is datetime
                and self.bound_at.tzinfo is not None
                and self.bound_at.utcoffset() == timedelta(0)
                and self.bound_at.microsecond == 0
                and written.validated_at <= self.bound_at < self.expires_at
                and all(
                    getattr(self, name) is value
                    for name, value in BINDING_CLAIMS.items()
                )
                and self._proof_sha256 == _binding_proof_sha256(projection)
            )
        except Exception:
            return False


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()


def _exact_keys(value: object, expected: set[str], code: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != expected:
        _fail(code)
    return value


def _load_json(path: Path, code: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError, UnicodeError):
        _fail(code)
    if not isinstance(value, dict):
        _fail(code)
    return value


def load_canonical_policy() -> dict[str, Any]:
    try:
        raw = POLICY_PATH.read_bytes()
    except OSError:
        _fail("policy.unreadable")
    if not hmac.compare_digest(
        hashlib.sha256(raw).hexdigest(), CANONICAL_POLICY_SHA256
    ):
        _fail("policy.digest_mismatch")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        _fail("policy.invalid")
    if not isinstance(value, dict):
        _fail("policy.invalid")
    return value


def load_canonical_data_contract() -> dict[str, Any]:
    try:
        raw = DATA_CONTRACT_PATH.read_bytes()
    except OSError:
        _fail("data_contract.unreadable")
    if not hmac.compare_digest(
        hashlib.sha256(raw).hexdigest(), CANONICAL_DATA_CONTRACT_SHA256
    ):
        _fail("data_contract.digest_mismatch")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        _fail("data_contract.invalid")
    if not isinstance(value, dict):
        _fail("data_contract.invalid")
    return value


def _retained_tables(
    inventory_policy: Mapping[str, Any], profile: str
) -> dict[str, dict[str, Any]]:
    expected = inventory._expected_tables(inventory_policy, profile)  # noqa: SLF001
    return {
        name: spec
        for name, spec in expected.items()
        if spec["disposition"] != "required_empty"
    }


def _column_reference(
    reference: object,
    columns: Mapping[str, tuple[str, bool]],
    code: str,
) -> tuple[str, bool]:
    if not isinstance(reference, str) or reference not in columns:
        _fail(code)
    return columns[reference]


def _valid_column_action(action: object) -> bool:
    if not isinstance(action, str):
        return False
    if action in SIMPLE_COLUMN_ACTIONS:
        return True
    for prefix in ("hmac_uuid7:", "hmac_text:"):
        if action.startswith(prefix):
            return DOMAIN_RE.fullmatch(action.removeprefix(prefix)) is not None
    return False


def validate_data_contract(
    contract: Mapping[str, Any],
    inventory_policy: Mapping[str, Any],
    sanitizer_policy: Mapping[str, Any],
) -> None:
    """Prove that every retained source column has one approved action.

    This validates a data-only contract.  It does not read rows, connect to
    PostgreSQL, execute a transform, or authorize sanitizer activation.
    """

    try:
        _exact_keys(contract, DATA_CONTRACT_KEYS, "data_contract.closed_world")
        if (
            contract["schema_version"] != 1
            or contract["contract_id"]
            != "cosmaneura-isolated-restore-sanitizer-data-v1"
            or contract["closed_world"] is not True
            or contract["source_profile"] != "pre"
        ):
            _fail("data_contract.identity")

        retained = _retained_tables(inventory_policy, contract["source_profile"])
        if contract["retained_table_count"] != len(retained) or contract[
            "retained_column_count"
        ] != sum(len(spec["columns"]) for spec in retained.values()):
            _fail("data_contract.counts")

        row_actions = _exact_keys(
            contract["row_actions"],
            {"discard_rows", "rewrite_rows"},
            "data_contract.row_actions",
        )
        discard = row_actions["discard_rows"]
        rewrite = row_actions["rewrite_rows"]
        if (
            not isinstance(discard, list)
            or not isinstance(rewrite, list)
            or len(discard) != len(set(discard))
            or len(rewrite) != len(set(rewrite))
            or set(discard) != GUARDED_TABLES
            or set(discard) & set(rewrite)
            or set(discard) | set(rewrite) != set(retained)
        ):
            _fail("data_contract.row_actions")

        tables = contract["tables"]
        if not isinstance(tables, Mapping) or set(tables) != set(retained):
            _fail("data_contract.table_coverage")
        columns: dict[str, tuple[str, bool]] = {}
        actions: dict[str, str] = {}
        for table_name, inventory_table in retained.items():
            table = _exact_keys(
                tables[table_name], TABLE_CONTRACT_KEYS, "data_contract.table"
            )
            expected_action = (
                "discard_rows" if table_name in GUARDED_TABLES else "rewrite_rows"
            )
            expected_columns = {
                str(column[0]): (str(column[1]), bool(column[2]))
                for column in inventory_table["columns"]
            }
            table_actions = table["columns"]
            if (
                table["disposition"] != inventory_table["disposition"]
                or table["row_action"] != expected_action
                or not isinstance(table_actions, Mapping)
                or set(table_actions) != set(expected_columns)
            ):
                _fail("data_contract.column_coverage")
            for column_name, (data_type, required) in expected_columns.items():
                reference = f"{table_name}.{column_name}"
                action = table_actions[column_name]
                if not _valid_column_action(action):
                    _fail("data_contract.column_action")
                if expected_action == "discard_rows":
                    if action != "discard_row":
                        _fail("data_contract.discard_action")
                elif action == "discard_row":
                    _fail("data_contract.discard_action")
                if action == "set_null" and required:
                    _fail("data_contract.nullability")
                if action.startswith("hmac_uuid7:") and data_type != "uuid":
                    _fail("data_contract.column_type")
                if action in {
                    "hmac_email",
                    "hmac_domain",
                    "hmac_issuer",
                    "hmac_subject",
                    "derive_typeid_text:appointment",
                    "rotate_join_code",
                    "derive_access_code_salt",
                    "derive_access_code_digest",
                } or action.startswith("hmac_text:"):
                    if not data_type.startswith("character varying"):
                        _fail("data_contract.column_type")
                if action == "preserve_verified_catalog" and (
                    table_name not in FIXED_CATALOG_TABLES
                ):
                    _fail("data_contract.fixed_catalog")
                columns[reference] = (data_type, required)
                actions[reference] = action

        if len(columns) != contract["retained_column_count"]:
            _fail("data_contract.counts")
        for table_name, domain in ENTITY_ID_DOMAINS.items():
            if actions.get(f"{table_name}.id") != f"hmac_uuid7:{domain}":
                _fail("data_contract.identifier_domain")

        fixed = sanitizer_policy["transform_contract"]["fixed_catalog"]
        identifier_domains = fixed["identifier_domains"]
        for reference, domain in identifier_domains.items():
            if actions.get(reference) != f"hmac_uuid7:{domain}":
                _fail("data_contract.fixed_catalog")
        for table_name, payload_columns in fixed["payload_columns_preserved"].items():
            for column_name in payload_columns:
                if (
                    actions.get(f"{table_name}.{column_name}")
                    != "preserve_verified_catalog"
                ):
                    _fail("data_contract.fixed_catalog")

        foreign_keys = contract["foreign_keys"]
        if not isinstance(foreign_keys, list):
            _fail("data_contract.foreign_keys")
        observed_foreign_keys: set[tuple[tuple[str, ...], tuple[str, ...]]] = set()
        for item in foreign_keys:
            item = _exact_keys(item, {"source", "target"}, "data_contract.foreign_keys")
            source = item["source"]
            target = item["target"]
            if (
                not isinstance(source, list)
                or not isinstance(target, list)
                or not source
                or len(source) != len(target)
                or not all(isinstance(value, str) for value in source + target)
            ):
                _fail("data_contract.foreign_keys")
            binding = (tuple(source), tuple(target))
            if binding in observed_foreign_keys:
                _fail("data_contract.foreign_keys")
            observed_foreign_keys.add(binding)
            source_is_discarded = source[0].split(".", 1)[0] in set(discard)
            for source_ref, target_ref in zip(source, target, strict=True):
                _column_reference(source_ref, columns, "data_contract.foreign_keys")
                _column_reference(target_ref, columns, "data_contract.foreign_keys")
                if not source_is_discarded and (
                    actions[target_ref] == "discard_row"
                    or actions[source_ref] != actions[target_ref]
                ):
                    _fail("data_contract.foreign_key_action")
        if observed_foreign_keys != EXPECTED_FOREIGN_KEYS:
            _fail("data_contract.foreign_keys")

        if contract["derived_values"] != EXPECTED_DERIVED_VALUES:
            _fail("data_contract.derived_values")
        expected_derived_actions = {
            "authority_person_binding.binding_ref": "derive_typeid_text:appointment",
            "practice.join_code": "rotate_join_code",
            "provider_organization.access_code_salt": "derive_access_code_salt",
            "provider_organization.access_code_digest": "derive_access_code_digest",
        }
        for reference, expected in expected_derived_actions.items():
            _column_reference(reference, columns, "data_contract.derived_values")
            if actions[reference] != expected:
                _fail("data_contract.derived_values")

        domains = sanitizer_policy["transform_contract"]["semantic_domains"]
        rewrite_tables = set(rewrite)

        def retained_references(values: object) -> set[str]:
            if not isinstance(values, list):
                _fail("data_contract.semantic_domains")
            return {
                value
                for value in values
                if isinstance(value, str) and value.split(".", 1)[0] in rewrite_tables
            }

        semantic_actions = {
            "clerk_issuer": "hmac_issuer",
            "clerk_subject": "hmac_subject",
            "verified_email": "hmac_email",
            "organization_email_domain": "hmac_domain",
        }
        for domain_name, expected_action in semantic_actions.items():
            expected = retained_references(domains[domain_name]["columns"])
            observed = {
                reference
                for reference, action in actions.items()
                if action == expected_action
            }
            if observed != expected:
                _fail("data_contract.semantic_domains")
        if set(domains["provider_access_code"]["columns"]) != {
            "provider_organization.access_code_salt",
            "provider_organization.access_code_digest",
        }:
            _fail("data_contract.semantic_domains")
    except SanitizerFailure:
        raise
    except (KeyError, TypeError, ValueError):
        _fail("data_contract.invalid")


def _validate_fixed_catalog(
    contract: Mapping[str, Any], tables: Mapping[str, Mapping[str, Any]]
) -> None:
    fixed = _exact_keys(
        contract,
        {
            "tables",
            "strategy",
            "requires_authenticated_source_state_hmacs",
            "source_state_hmacs_bind_input_rows",
            "post_preserved_payload_projection_hmac_required",
            "identifier_domains",
            "payload_columns_preserved",
        },
        "policy.fixed_catalog",
    )
    if (
        set(fixed["tables"]) != FIXED_CATALOG_TABLES
        or fixed["strategy"] != "preserve_verified_payload_map_identifiers"
        or set(fixed["requires_authenticated_source_state_hmacs"])
        != FIXED_CATALOG_TABLES
        or fixed["source_state_hmacs_bind_input_rows"] is not True
        or fixed["post_preserved_payload_projection_hmac_required"] is not True
        or set(fixed["payload_columns_preserved"]) != FIXED_CATALOG_TABLES
    ):
        _fail("policy.fixed_catalog")
    identifier_domains = fixed["identifier_domains"]
    if not isinstance(identifier_domains, Mapping):
        _fail("policy.fixed_catalog")
    if identifier_domains != {
        "baa_version.id": "typeid:baa_version",
        "doctor_note_schema.id": "typeid:doctor_note_schema",
        "doctor_note_schema.doctor_id": "typeid:doctor",
        "drug_reference.id": "typeid:drug_reference",
        "lab_range_set.id": "typeid:lab_range_set",
        "lab_range_set.doctor_id": "typeid:doctor",
        "note_type_customization.id": "typeid:note_type_customization",
        "note_type_customization.doctor_note_schema_id": "typeid:doctor_note_schema",
    }:
        _fail("policy.fixed_catalog")
    for table in FIXED_CATALOG_TABLES:
        columns = {column[0] for column in tables[table]["columns"]}
        mapped = {
            key.split(".", 1)[1]
            for key in identifier_domains
            if key.startswith(f"{table}.")
        }
        preserved = set(fixed["payload_columns_preserved"][table])
        if mapped & preserved or mapped | preserved != columns:
            _fail("policy.fixed_catalog")
    if any(
        not isinstance(domain, str) or not domain.startswith("typeid:")
        for domain in identifier_domains.values()
    ):
        _fail("policy.fixed_catalog")


def _validate_semantic_domains(
    domains: object, tables: Mapping[str, Mapping[str, Any]]
) -> None:
    domains = _exact_keys(
        domains,
        {
            "relational_typeid",
            "clerk_issuer",
            "clerk_subject",
            "verified_email",
            "organization_email_domain",
            "provider_access_code",
            "typed_text_references",
        },
        "policy.semantic_domains",
    )
    known_columns = {
        f"{table}.{column[0]}"
        for table, spec in tables.items()
        for column in spec["columns"]
    }
    relational = _exact_keys(
        domains["relational_typeid"],
        {"mode", "preserve_pk_fk_equality"},
        "policy.semantic_domains",
    )
    if relational != {"mode": "hmac_uuid7", "preserve_pk_fk_equality": True}:
        _fail("policy.semantic_domains")
    for name, mode in (
        ("clerk_issuer", "hmac_https_issuer"),
        ("clerk_subject", "hmac_prefixed_text"),
        ("verified_email", "hmac_email"),
    ):
        expected = {"mode", "columns", "dependent_sha256"}
        if name == "clerk_subject":
            expected.add("prefix")
        domain = _exact_keys(domains[name], expected, "policy.semantic_domains")
        if domain["mode"] != mode or not set(domain["columns"]) <= known_columns:
            _fail("policy.semantic_domains")
        if not set(domain["dependent_sha256"]) <= known_columns:
            _fail("policy.semantic_domains")
    if domains["clerk_subject"].get("prefix") != "user_":
        _fail("policy.semantic_domains")
    organization_domain = _exact_keys(
        domains["organization_email_domain"],
        {"mode", "columns", "preserve_composite_fk_equality"},
        "policy.semantic_domains",
    )
    if organization_domain != {
        "mode": "hmac_domain_name",
        "columns": ["practice.email_domain", "provider_organization.email_domain"],
        "preserve_composite_fk_equality": True,
    }:
        _fail("policy.semantic_domains")
    access = _exact_keys(
        domains["provider_access_code"],
        {"mode", "columns", "parameters"},
        "policy.semantic_domains",
    )
    if (
        access["mode"] != "hmac_code_hmac_salt_scrypt_digest"
        or set(access["columns"])
        != {
            "provider_organization.access_code_salt",
            "provider_organization.access_code_digest",
        }
        or access["parameters"]
        != {
            "code_alphabet": ACCESS_CODE_ALPHABET,
            "code_length": 12,
            "salt_bytes": 16,
            "scrypt_n": 16384,
            "scrypt_r": 8,
            "scrypt_p": 1,
            "digest_bytes": 32,
            "plaintext_code_emitted": False,
        }
    ):
        _fail("policy.semantic_domains")
    typed = _exact_keys(
        domains["typed_text_references"],
        {"mode", "references", "implementation_status"},
        "policy.semantic_domains",
    )
    if (
        typed["mode"] != "derive_typeid_text_from_transformed_uuid"
        or typed["implementation_status"] != "data_contract_complete_execution_missing"
        or typed["references"]
        != {
            "authority_person_binding.binding_ref": {
                "source": "appointment.id",
                "typeid_prefix": "appointment",
            },
        }
    ):
        _fail("policy.semantic_domains")


def validate_policy(
    policy: Mapping[str, Any],
    inventory_policy: Mapping[str, Any],
    data_contract: Mapping[str, Any] | None = None,
) -> None:
    try:
        inventory.validate_policy(inventory_policy)
        _validate_policy(policy, inventory_policy)
        validate_data_contract(
            data_contract
            if data_contract is not None
            else load_canonical_data_contract(),
            inventory_policy,
            policy,
        )
    except inventory.InventoryFailure:
        _fail("inventory_policy.invalid")
    except (KeyError, TypeError, ValueError):
        _fail("policy.invalid")


def _validate_fixed_catalog_source_state_policy(
    contract: object, inventory_policy: Mapping[str, Any]
) -> None:
    value = _exact_keys(
        contract,
        {"binding", "claims", "implemented", "input", "inventory", "kind", "output"},
        "policy.fixed_catalog_source_state",
    )
    input_contract = value["input"]
    inventory_contract = value["inventory"]
    binding = value["binding"]
    output = value["output"]
    pre = inventory_policy["profiles"]["pre"]
    if (
        value["kind"] != "sealed-authenticated-fixed-catalog-source-state-v1"
        or value["implemented"] is not True
        or input_contract
        != {
            "caller_digest_policy_profile_revision_time_path_uri_key_or_runtime_override_permitted": False,
            "inventory_receipt_exact_bytes_required": True,
            "maximum_inventory_receipt_bytes": MAXIMUM_INVENTORY_RECEIPT_BYTES,
            "only_public_data_inputs": [
                "target_binding",
                "inventory_receipt_bytes",
            ],
            "target_binding_kind": "sealed-sanitizer-target-binding-v1",
        }
        or inventory_contract
        != {
            "canonical_json_required": True,
            "duplicate_keys_rejected": True,
            "historical_receipt_expiry_is_activation_deadline": False,
            "hmac_key_version": inventory.SUPPORTED_HMAC_KEY_VERSION,
            "policy_id": inventory_policy["policy_id"],
            "policy_sha256": f"sha256:{inventory.CANONICAL_POLICY_SHA256}",
            "profile": "pre",
            "receipt_schema_version": 2,
            "revision": pre["revision"],
            "schema_descriptor_sha256": pre["schema_descriptor"]["sha256"],
        }
        or binding
        != {
            "constant_time_digest_compare": True,
            "count_nonnegative_exact_int_required": True,
            "cross_bound_fields": list(FIXED_CATALOG_CONTEXT_FIELDS),
            "fixed_catalog_tables": list(FIXED_CATALOG_TABLE_ORDER),
            "intent_digest_source": (
                "target_binding._written_receipt._built_receipt.intent_bytes."
                "evidence_binding.inventory_receipt_sha256"
            ),
            "state_hmac_lowercase_sha256_required": True,
            "target_binding_fresh_at_bind_time_required": True,
        }
        or output
        != {
            "non_serializable": True,
            "private_inventory_receipt_bytes_retained": True,
            "private_ordered_projection_retained": True,
            "private_target_binding_retained": True,
            "public_fields": list(FIXED_CATALOG_PUBLIC_FIELDS),
            "unknown_fields_permitted": False,
        }
        or value["claims"] != FIXED_CATALOG_SOURCE_STATE_CLAIMS
    ):
        _fail("policy.fixed_catalog_source_state")


def _validate_policy(
    policy: Mapping[str, Any], inventory_policy: Mapping[str, Any]
) -> None:
    _exact_keys(policy, TOP_LEVEL_KEYS, "policy.closed_world")
    if (
        policy["schema_version"] != 1
        or policy["policy_id"] != "cosmaneura-isolated-restore-sanitizer-contract-v1"
        or policy["closed_world"] is not True
    ):
        _fail("policy.identity")
    status = _exact_keys(
        policy["status"],
        {
            "controller_receipt_v2_target_binding_implemented",
            "executable",
            "fixed_catalog_source_state_binding_implemented",
            "live_provider_calls_enabled",
            "trusted_evidence_binding_implemented",
            "database_mutation_implemented",
            "workflow_integrated",
            "activation_blockers",
        },
        "policy.status",
    )
    if (
        status["controller_receipt_v2_target_binding_implemented"] is not True
        or status["executable"] is not False
        or status["fixed_catalog_source_state_binding_implemented"] is not True
        or status["live_provider_calls_enabled"] is not False
        or status["trusted_evidence_binding_implemented"] is not False
        or status["database_mutation_implemented"] is not False
        or status["workflow_integrated"] is not False
        or set(status["activation_blockers"]) != ACTIVATION_BLOCKERS
        or len(status["activation_blockers"]) != len(ACTIVATION_BLOCKERS)
    ):
        _fail("policy.status")

    pinned = _exact_keys(
        policy["inventory"],
        {
            "policy_sha256",
            "source_profile",
            "source_revision",
            "source_schema_descriptor_sha256",
            "source_table_count",
            "source_retained_table_count",
            "source_retained_column_count",
            "source_required_empty_table_count",
            "post_profile",
            "post_revision",
            "post_schema_descriptor_sha256",
            "post_table_count",
            "post_required_empty_table_count",
        },
        "policy.inventory",
    )
    pre = inventory_policy["profiles"]["pre"]
    post = inventory_policy["profiles"]["post"]
    pre_tables = inventory._expected_tables(inventory_policy, "pre")  # noqa: SLF001
    post_tables = inventory._expected_tables(inventory_policy, "post")  # noqa: SLF001
    retained = _retained_tables(inventory_policy, "pre")
    if (
        pinned["policy_sha256"] != inventory.CANONICAL_POLICY_SHA256
        or pinned["source_profile"] != "pre"
        or pinned["source_revision"] != pre["revision"]
        or pinned["source_schema_descriptor_sha256"]
        != pre["schema_descriptor"]["sha256"]
        or pinned["source_table_count"] != len(pre_tables)
        or pinned["source_retained_table_count"] != len(retained)
        or pinned["source_retained_column_count"]
        != sum(len(spec["columns"]) for spec in retained.values())
        or pinned["source_required_empty_table_count"]
        != sum(spec["disposition"] == "required_empty" for spec in pre_tables.values())
        or pinned["post_profile"] != "post"
        or pinned["post_revision"] != post["revision"]
        or pinned["post_schema_descriptor_sha256"]
        != post["schema_descriptor"]["sha256"]
        or pinned["post_table_count"] != len(post_tables)
        or pinned["post_required_empty_table_count"]
        != sum(spec["disposition"] == "required_empty" for spec in post_tables.values())
    ):
        _fail("policy.inventory")

    target = _exact_keys(
        policy["target"],
        {
            "resource_group",
            "server_name_pattern",
            "postgres_fqdn_suffix",
            "database_name",
            "port",
            "postgresql_major",
            "sslmode",
            "application_name",
            "approved_role_source",
            "require_no_competing_sessions",
            "rejected_hosts",
        },
        "policy.target",
    )
    if (
        target["server_name_pattern"] != r"cnr-[0-9a-f]{12}-e[0-9]{10}-pg"
        or target["resource_group"] != "cosmaneura-staging-rehearsal"
        or target["postgres_fqdn_suffix"] != ".postgres.database.azure.com"
        or target["database_name"] != "staging"
        or target["port"] != 5432
        or target["postgresql_major"] != 16
        or target["sslmode"] != "verify-full"
        or target["application_name"] != APP_NAME
        or target["approved_role_source"]
        != "controller_receipt_v2.target.approved_database_role"
        or target["require_no_competing_sessions"] is not True
        or set(target["rejected_hosts"])
        != {
            "cosmaneura-prod-postgres.postgres.database.azure.com",
            "cosmaneura-staging-postgres.postgres.database.azure.com",
        }
    ):
        _fail("policy.target")

    receipt = _exact_keys(
        policy["controller_receipt_v2_binding"],
        {
            "bound_fields",
            "claims",
            "implemented",
            "input",
            "kind",
            "output",
            "reauthentication",
            "upstream_policies",
        },
        "policy.controller_receipt_v2_binding",
    )
    expected_upstream = {
        "controller_receipt_v2": {
            "policy_id": "cosmaneura-staging-controller-receipt-v2",
            "sha256": f"sha256:{receipt_content.POLICY_SHA256}",
        },
        "controller_receipt_v2_builder": {
            "policy_id": "cosmaneura-staging-controller-receipt-v2-builder-v1",
            "sha256": f"sha256:{receipt_builder.POLICY_SHA256}",
        },
        "controller_receipt_v2_writer": {
            "policy_id": "cosmaneura-staging-controller-receipt-v2-writer-v1",
            "sha256": f"sha256:{receipt_writer.POLICY_SHA256}",
        },
    }
    if (
        receipt["kind"] != "sealed-sanitizer-target-binding-v1"
        or receipt["implemented"] is not True
        or receipt["upstream_policies"] != expected_upstream
        or receipt["input"]
        != {
            "caller_bytes_maps_paths_urls_dsns_hostnames_roles_digests_policies_statuses_or_times_permitted": False,
            "exact_type_and_in_process_seal_required": True,
            "kind": "sealed-controller-receipt-v2-write-result-v1",
            "only_public_data_input": "written_receipt",
        }
        or receipt["reauthentication"]
        != {
            "canonical_record_chain_required": True,
            "clock_regression_rejected": True,
            "fresh_at_bind_time_required": True,
            "same_rehearsal_required": True,
            "trusted_clock_source": "reviewed-private-configuration-only",
            "whole_second_utc_required": True,
            "writer_and_full_retained_ancestry_required": True,
        }
        or receipt["bound_fields"]
        != {
            "approved_database_role": "receipt.target.approved_database_role",
            "approved_restore_time": "receipt.source.approved_restore_time",
            "candidate_sha": "receipt.source.candidate_sha",
            "database_name": "receipt.source.source_database_name",
            "expires_at": "receipt.expires_at",
            "receipt_sha256": "written_receipt.receipt_sha256",
            "rehearsal_id": "receipt.authorization.rehearsal_id",
            "run_attempt": "receipt.source.run_attempt",
            "run_id": "receipt.source.run_id",
            "source_sha": "receipt.source.source_sha",
            "target_fqdn": "receipt.target.target_fqdn",
            "target_postgres_server_id": "receipt.target.target_postgres_server_id",
            "workflow_sha": "receipt.source.workflow_sha",
        }
        or receipt["output"]
        != {
            "kind": "sealed-sanitizer-target-binding-v1",
            "non_serializable": True,
            "private_written_authority_retained": True,
            "unknown_fields_permitted": False,
        }
        or receipt["claims"] != BINDING_CLAIMS
    ):
        _fail("policy.controller_receipt_v2_binding")

    _validate_fixed_catalog_source_state_policy(
        policy["fixed_catalog_source_state_binding"], inventory_policy
    )

    transform = _exact_keys(
        policy["transform_contract"],
        {
            "algorithm",
            "domain",
            "minimum_key_bytes",
            "primitive_status",
            "retained_column_policy_status",
            "data_contract_path",
            "data_contract_sha256",
            "uuid_output",
            "semantic_domains",
            "dependent_digest_recipes",
            "fixed_catalog",
        },
        "policy.transform",
    )
    if (
        transform["algorithm"] != "HMAC-SHA-256"
        or transform["domain"] != HMAC_DOMAIN
        or transform["minimum_key_bytes"] != MINIMUM_KEY_BYTES
        or transform["primitive_status"] != "implemented_and_pure"
        or transform["retained_column_policy_status"]
        != "complete_sha256_pinned_343_columns"
        or transform["data_contract_path"]
        != "contracts/staging-rehearsal/v1/restore-sanitizer-data-contract.json"
        or transform["data_contract_sha256"] != CANONICAL_DATA_CONTRACT_SHA256
        or transform["uuid_output"] != "RFC-variant-UUIDv7"
        or transform["dependent_digest_recipes"] != {}
    ):
        _fail("policy.transform")
    _validate_semantic_domains(transform["semantic_domains"], retained)
    _validate_fixed_catalog(transform["fixed_catalog"], retained)

    execution = _exact_keys(
        policy["execution_contract"],
        {
            "implemented",
            "database_connections_permitted",
            "sql_mutation_permitted",
            "guarded_tables_must_be_empty",
            "future_transaction_requirements",
        },
        "policy.execution",
    )
    future = _exact_keys(
        execution["future_transaction_requirements"],
        {
            "isolation",
            "synchronous_commit",
            "lock_alembic_version",
            "truncate_all_source_tables_in_one_statement",
            "truncate_is_isolated_copy_sanitizer_behavior_not_append_only_semantics",
            "truncate_cascade",
            "disable_triggers",
            "reject_unexpected_on_truncate_triggers",
            "prove_truncate_and_set_role_permissions",
            "prove_rollback_before_activation",
            "statement_timeout_required",
            "lock_timeout_required",
            "idle_in_transaction_timeout_required",
            "receipt_expiry_deadline_required",
            "recheck_no_competing_sessions_before_commit",
            "fresh_connection_post_commit_verification",
            "unknown_outcome",
        },
        "policy.execution",
    )
    if (
        execution["implemented"] is not False
        or execution["database_connections_permitted"] is not False
        or execution["sql_mutation_permitted"] is not False
        or set(execution["guarded_tables_must_be_empty"]) != GUARDED_TABLES
        or future
        != {
            "isolation": "serializable",
            "synchronous_commit": "on",
            "lock_alembic_version": True,
            "truncate_all_source_tables_in_one_statement": True,
            "truncate_is_isolated_copy_sanitizer_behavior_not_append_only_semantics": True,
            "truncate_cascade": False,
            "disable_triggers": False,
            "reject_unexpected_on_truncate_triggers": True,
            "prove_truncate_and_set_role_permissions": True,
            "prove_rollback_before_activation": True,
            "statement_timeout_required": True,
            "lock_timeout_required": True,
            "idle_in_transaction_timeout_required": True,
            "receipt_expiry_deadline_required": True,
            "recheck_no_competing_sessions_before_commit": True,
            "fresh_connection_post_commit_verification": True,
            "unknown_outcome": "isolated_cleanup_only",
        }
    ):
        _fail("policy.execution")
    migration = _exact_keys(
        policy["migration_contract"],
        {
            "verification_implemented",
            "revisions",
            "files",
            "direct_sql_canaries_required",
        },
        "policy.migrations",
    )
    if (
        migration["verification_implemented"] is not False
        or migration["files"] != MIGRATION_FILES
        or migration["revisions"]
        != ["d0a4c8e2f611", "e7a9c3d5f102", "da6e199668a4", "9a2399212488"]
        or migration["direct_sql_canaries_required"]
        != [
            "clinician_seat_state_backfill",
            "public_onboarding_evidence_constraints",
            "stripe_checkout_state_constraints",
            "baa_v2_3_publication",
        ]
    ):
        _fail("policy.migrations")
    evidence = _exact_keys(
        policy["evidence_contract"],
        {
            "aggregate_only",
            "per_table_counts_forbidden",
            "minimum_reported_group_size",
            "small_count_behavior",
            "hmac_algorithm",
            "hmac_domain",
            "hmac_context_fields",
            "allowed_fields",
            "forbidden_raw_values",
        },
        "policy.evidence",
    )
    if (
        evidence["aggregate_only"] is not True
        or evidence["per_table_counts_forbidden"] is not True
        or evidence["minimum_reported_group_size"] != 10
        or evidence["small_count_behavior"] != "omit"
        or evidence["hmac_algorithm"] != "HMAC-SHA-256"
        or evidence["hmac_domain"] != "cosmaneura:staging-restore-sanitizer-evidence:v1"
        or evidence["hmac_context_fields"]
        != [
            "policy_sha256",
            "source_sha",
            "candidate_sha",
            "rehearsal_id",
            "workflow_sha",
            "run_id",
            "run_attempt",
            "controller_receipt_sha256",
            "transform_key_version",
        ]
        or set(evidence["allowed_fields"])
        != {
            "schema_version",
            "policy_sha256",
            "operation",
            "aggregate_counts",
            "aggregate_hmacs",
            "errors",
            "committed",
            "verified",
            "cleanup_only",
            "pass",
            "evidence_hmac",
        }
        or set(evidence["forbidden_raw_values"])
        != {
            "rows",
            "identifiers",
            "emails",
            "hostnames",
            "database_urls",
            "tokens",
            "secrets",
            "payloads",
        }
    ):
        _fail("policy.evidence")
    if policy["limitations"] != {
        "server_wide_pitr_erasure_proven": False,
        "global_role_erasure_proven": False,
        "logical_erasure_of_backups_replicas_and_logs_proven": False,
    }:
        _fail("policy.limitations")


def validate_migration_files(policy: Mapping[str, Any], root: Path = ROOT) -> None:
    if policy["migration_contract"]["files"] != MIGRATION_FILES:
        _fail("migration.contract")
    for relative, expected in MIGRATION_FILES.items():
        try:
            actual = hashlib.sha256((root / relative).read_bytes()).hexdigest()
        except OSError:
            _fail("migration.file_digest")
        if not hmac.compare_digest(actual, expected):
            _fail("migration.file_digest")


def _trusted_utc(clock: Callable[[], datetime]) -> datetime:
    try:
        value = clock()
    except Exception:
        _fail("sanitizer_binding.clock_invalid")
    if (
        type(value) is not datetime
        or value.tzinfo is None
        or value.utcoffset() != timedelta(0)
        or value.microsecond != 0
    ):
        _fail("sanitizer_binding.clock_invalid")
    return value.astimezone(UTC)


def _binding_projection(
    *,
    written_receipt: receipt_writer.WrittenControllerReceiptV2,
    policy: Mapping[str, Any],
    now: datetime,
) -> dict[str, Any]:
    try:
        if (
            type(written_receipt) is not receipt_writer.WrittenControllerReceiptV2
            or not written_receipt._is_authenticated()
        ):
            _fail("sanitizer_binding.authority_invalid")
        binding_policy = policy["controller_receipt_v2_binding"]
        pins = binding_policy["upstream_policies"]
        writer_policy = receipt_writer.validate_controller_receipt_v2_writer_policy(
            receipt_content._parse_canonical(
                written_receipt._writer_policy_bytes, MAXIMUM_POLICY_BYTES
            )
        )
        built = written_receipt._built_receipt
        if (
            type(built) is not receipt_builder.BuiltControllerReceiptV2
            or pins["controller_receipt_v2_writer"]["sha256"]
            != f"sha256:{receipt_writer.POLICY_SHA256}"
            or pins["controller_receipt_v2_builder"]["sha256"]
            != f"sha256:{receipt_builder.POLICY_SHA256}"
            or pins["controller_receipt_v2"]["sha256"]
            != f"sha256:{receipt_content.POLICY_SHA256}"
        ):
            _fail("sanitizer_binding.authority_invalid")
        builder_policy = receipt_builder.validate_controller_receipt_v2_builder_policy(
            receipt_content._parse_canonical(
                built._builder_policy_bytes, MAXIMUM_POLICY_BYTES
            )
        )
        receipt = receipt_content._parse_canonical(
            built.receipt_bytes, receipt_content.MAXIMUM_POLICY_BYTES
        )
        expires_at = receipt_content._utc(receipt["expires_at"])
        cleanup_not_after = receipt_content._utc(receipt["cleanup_not_after"])
        if now >= min(expires_at, cleanup_not_after):
            _fail("sanitizer_binding.expired")
        current = receipt_builder._build_projection(
            built._isolation_inventory,
            policy=builder_policy,
            now=now,
        )
        production = (
            built._isolation_inventory._database_observation._production_authority
        )
        controller_policy = receipt_content.validate_controller_receipt_v2_policy(
            receipt_content._parse_canonical(
                production._controller_policy, receipt_content.MAXIMUM_POLICY_BYTES
            )
        )
        validated = receipt_content.validate_controller_receipt_v2_content(
            controller_policy,
            intent_bytes=built.intent_bytes,
            submission_bytes=built.submission_bytes,
            receipt_bytes=built.receipt_bytes,
            clock=lambda: now,
        )
        current_receipt = receipt_content._parse_canonical(
            current["receipt_bytes"], receipt_content.MAXIMUM_POLICY_BYTES
        )
        stable_fields = set(receipt_content.TOP_LEVEL_FIELDS).difference(
            ("created_at", "expires_at")
        )
        stable_projection = {name: receipt[name] for name in sorted(stable_fields)}
        current_stable_projection = {
            name: current_receipt[name] for name in sorted(stable_fields)
        }
        stable_build_fields = (
            "arm_observation_sha256",
            "authority_chain_sha256",
            "database_observation_sha256",
            "execution_resource_id",
            "intent_sha256",
            "isolation_inventory_authority_sha256",
            "known_resource_inventory_sha256",
            "policy_binding_sha256",
            "production_authority_sha256",
            "rehearsal_id",
            "resource_set_sha256",
            "restore_observed_at",
            "runtime_policy_binding_sha256",
            "submission_sha256",
        )
        modified = receipt_content._utc(written_receipt.last_modified)
        retained = receipt_content._utc(written_receipt.retained_until)
        skew = timedelta(
            seconds=controller_policy["freshness"]["maximum_clock_skew_seconds"]
        )
        approved_restore_time = receipt_content._utc(
            receipt["source"]["approved_restore_time"]
        )
        if (
            writer_policy["policy_id"]
            != pins["controller_receipt_v2_writer"]["policy_id"]
            or builder_policy["policy_id"]
            != pins["controller_receipt_v2_builder"]["policy_id"]
            or controller_policy["policy_id"]
            != pins["controller_receipt_v2"]["policy_id"]
            or built.intent_bytes != current["intent_bytes"]
            or built.submission_bytes != current["submission_bytes"]
            or stable_projection != current_stable_projection
            or any(
                getattr(built, name) != current[name] for name in stable_build_fields
            )
            or validated.receipt_bytes != built.receipt_bytes
            or validated.receipt_sha256 != written_receipt.receipt_sha256
            or built.receipt_sha256 != written_receipt.receipt_sha256
            or written_receipt.rehearsal_id != receipt["authorization"]["rehearsal_id"]
            or built.rehearsal_id != receipt["authorization"]["rehearsal_id"]
            or receipt["source"]["source_database_name"]
            != policy["target"]["database_name"]
            or ROLE_RE.fullmatch(receipt["target"]["approved_database_role"]) is None
            or built.created_at > written_receipt.validated_at
            or written_receipt.validated_at > now
            or modified < built.created_at - skew
            or modified > now + skew
            or retained <= now
            or retained - modified
            < timedelta(days=writer_policy["readback"]["minimum_retention_days"])
        ):
            _fail("sanitizer_binding.authority_invalid")
        source = receipt["source"]
        target = receipt["target"]
        return {
            "rehearsal_id": receipt["authorization"]["rehearsal_id"],
            "target_fqdn": target["target_fqdn"],
            "database_name": source["source_database_name"],
            "approved_database_role": target["approved_database_role"],
            "target_postgres_server_id": target["target_postgres_server_id"],
            "approved_restore_time": approved_restore_time,
            "source_sha": source["source_sha"],
            "candidate_sha": source["candidate_sha"],
            "workflow_sha": source["workflow_sha"],
            "run_id": source["run_id"],
            "run_attempt": source["run_attempt"],
            "receipt_sha256": written_receipt.receipt_sha256,
            "expires_at": expires_at,
            "bound_at": now,
        }
    except SanitizerFailure:
        raise
    except Exception:
        _fail("sanitizer_binding.authority_invalid")


def _binding_proof_sha256(projection: Mapping[str, Any]) -> str:
    serializable = dict(projection)
    for name in ("approved_restore_time", "expires_at", "bound_at"):
        value = serializable[name]
        if type(value) is not datetime:
            _fail("sanitizer_binding.output_invalid")
        serializable[name] = value.strftime("%Y-%m-%dT%H:%M:%SZ")
    return hashlib.sha256(_canonical_json(serializable)).hexdigest()


def _mint_target_binding(
    *,
    written_receipt: receipt_writer.WrittenControllerReceiptV2,
    policy: Mapping[str, Any],
    projection: Mapping[str, Any],
) -> SanitizerTargetBinding:
    result = object.__new__(SanitizerTargetBinding)
    for name, value in {**projection, **BINDING_CLAIMS}.items():
        object.__setattr__(result, name, value)
    object.__setattr__(result, "_written_receipt", written_receipt)
    object.__setattr__(result, "_policy_bytes", _canonical_json(policy))
    object.__setattr__(result, "_proof_sha256", _binding_proof_sha256(projection))
    object.__setattr__(result, "_seal", _TARGET_BINDING_SEAL)
    if (
        type(result) is not SanitizerTargetBinding
        or result._seal is not _TARGET_BINDING_SEAL
        or any(getattr(result, name) != value for name, value in projection.items())
        or any(
            getattr(result, name) is not value for name, value in BINDING_CLAIMS.items()
        )
        or result._proof_sha256 != _binding_proof_sha256(projection)
    ):
        _fail("sanitizer_binding.output_invalid")
    return result


class SanitizerTargetBinder:
    """Pure binder configured only with a reviewed trusted UTC clock."""

    __slots__ = ("_clock", "_last")

    def __init__(self) -> None:
        _fail("sanitizer_binding.configuration_invalid")

    @classmethod
    def _from_reviewed_configuration(
        cls, *, clock: Callable[[], datetime]
    ) -> SanitizerTargetBinder:
        if cls is not SanitizerTargetBinder or not callable(clock):
            _fail("sanitizer_binding.configuration_invalid")
        value = object.__new__(cls)
        value._clock = clock
        value._last = None
        return value

    def _read_clock(self) -> datetime:
        now = _trusted_utc(self._clock)
        if self._last is not None and now < self._last:
            _fail("sanitizer_binding.clock_regressed")
        self._last = now
        return now

    def bind(
        self, *, written_receipt: receipt_writer.WrittenControllerReceiptV2
    ) -> SanitizerTargetBinding:
        code: str | None = None
        try:
            if type(self) is not SanitizerTargetBinder:
                _fail("sanitizer_binding.configuration_invalid")
            policy = load_canonical_policy()
            inventory_policy = _load_json(
                INVENTORY_POLICY_PATH, "inventory_policy.invalid"
            )
            validate_policy(policy, inventory_policy)
            started_at = self._read_clock()
            projection = _binding_projection(
                written_receipt=written_receipt,
                policy=policy,
                now=started_at,
            )
            completed_at = self._read_clock()
            if completed_at != started_at:
                projection = _binding_projection(
                    written_receipt=written_receipt,
                    policy=policy,
                    now=completed_at,
                )
            return _mint_target_binding(
                written_receipt=written_receipt,
                policy=policy,
                projection=projection,
            )
        except Exception as error:
            code = (
                error.args[0]
                if type(error) is SanitizerFailure
                and len(error.args) == 1
                and type(error.args[0]) is str
                and error.args[0] in _BINDING_FAILURE_CODES
                else "sanitizer_binding.authority_invalid"
            )
        raise SanitizerFailure(code) from None


def build_sanitizer_target_binder() -> NoReturn:
    """Refuse public construction while sanitizer activation remains blocked."""

    load_canonical_policy()
    _fail("activation.trusted_binding_unimplemented")


def require_activation() -> NoReturn:
    """Refuse before a caller may import a driver or invoke a connector."""

    _fail("activation.trusted_binding_unimplemented")


def preflight(
    binding: SanitizerTargetBinding,
    connector: Callable[[], Any],
) -> NoReturn:
    """Authenticate the sealed binding, then stop before ``connector``."""

    del connector
    if type(binding) is not SanitizerTargetBinding or not binding._is_bound():
        _fail("sanitizer_binding.output_invalid")
    require_activation()


def validate_target_url(
    database_url: str,
    binding: SanitizerTargetBinding,
) -> None:
    """Validate a future target URL against only a sealed target binding."""

    if type(binding) is not SanitizerTargetBinding or not binding._is_bound():
        _fail("sanitizer_binding.output_invalid")
    policy = load_canonical_policy()
    try:
        parsed = urllib.parse.urlsplit(database_url)
        port = parsed.port
        query = urllib.parse.parse_qs(
            parsed.query, keep_blank_values=True, strict_parsing=True
        )
    except (TypeError, ValueError):
        _fail("target.invalid")
    expected_query = {
        "sslmode": [policy["target"]["sslmode"]],
        "sslrootcert": query.get("sslrootcert"),
        "application_name": [policy["target"]["application_name"]],
        "connect_timeout": ["10"],
    }
    root_values = query.get("sslrootcert")
    root = Path(root_values[0]) if root_values and len(root_values) == 1 else None
    if (
        parsed.scheme != "postgresql"
        or parsed.hostname != binding.target_fqdn
        or parsed.hostname in set(policy["target"]["rejected_hosts"])
        or port != policy["target"]["port"]
        or urllib.parse.unquote(parsed.username or "") != binding.approved_database_role
        or parsed.path != f"/{binding.database_name}"
        or parsed.fragment
        or query != expected_query
        or root is None
        or not root.is_absolute()
        or root == Path("/")
    ):
        _fail("target.invalid")


def _hmac_digest(key: bytes, semantic_domain: str, value: bytes) -> bytes:
    if not isinstance(key, bytes) or len(key) < MINIMUM_KEY_BYTES:
        _fail("transform.key")
    if DOMAIN_RE.fullmatch(semantic_domain) is None:
        _fail("transform.domain")
    domain = f"{HMAC_DOMAIN}:{semantic_domain}".encode()
    framed = (
        len(domain).to_bytes(2, "big") + domain + len(value).to_bytes(8, "big") + value
    )
    return hmac.new(key, framed, hashlib.sha256).digest()


def pseudonymize_uuid7(value: uuid.UUID, *, key: bytes, domain: str) -> uuid.UUID:
    """Return a deterministic RFC-variant UUIDv7 suitable for TypeID storage."""

    if not isinstance(value, uuid.UUID):
        _fail("transform.uuid")
    digest = _hmac_digest(key, domain, b"uuid:" + value.bytes)
    timestamp = (
        UUID7_EPOCH_START_MS + int.from_bytes(digest[:6], "big") % UUID7_EPOCH_SPAN_MS
    )
    output = bytearray(timestamp.to_bytes(6, "big") + digest[6:16])
    output[6] = (output[6] & 0x0F) | 0x70
    output[8] = (output[8] & 0x3F) | 0x80
    return uuid.UUID(bytes=bytes(output))


def pseudonymize_text(
    value: str,
    *,
    key: bytes,
    domain: str,
    prefix: str = "",
    maximum_length: int = 64,
) -> str:
    if not isinstance(value, str) or not value or maximum_length < len(prefix) + 8:
        _fail("transform.text")
    token = (
        base64.b32encode(_hmac_digest(key, domain, b"text:" + value.encode("utf-8")))
        .decode("ascii")
        .lower()
        .rstrip("=")
    )
    return (prefix + token)[:maximum_length]


def pseudonymize_email(value: str, *, key: bytes) -> str:
    normalized = value.strip().casefold()
    if (
        value != normalized
        or normalized.count("@") != 1
        or any(character.isspace() for character in normalized)
    ):
        _fail("transform.email")
    local = pseudonymize_text(
        normalized,
        key=key,
        domain="verified_email",
        prefix="u_",
        maximum_length=50,
    )
    return f"{local}@example.invalid"


def pseudonymize_domain_name(value: str, *, key: bytes) -> str:
    normalized = value.strip().lower().lstrip("@")
    if (
        value != normalized
        or not normalized
        or "." not in normalized
        or any(character.isspace() for character in normalized)
    ):
        _fail("transform.domain_name")
    token = pseudonymize_text(
        normalized,
        key=key,
        domain="organization_email_domain",
        maximum_length=32,
    )
    return f"org-{token}.example.invalid"


def pseudonymize_issuer(value: str, *, key: bytes) -> str:
    try:
        parsed = urllib.parse.urlsplit(value)
    except ValueError:
        _fail("transform.issuer")
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or value.endswith("/")
    ):
        _fail("transform.issuer")
    token = pseudonymize_text(value, key=key, domain="clerk_issuer", maximum_length=32)
    return f"https://id-{token}.identity.invalid"


def plain_sha256(value: str) -> str:
    if not isinstance(value, str):
        _fail("transform.digest")
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def pseudonymize_identity_bundle(
    *, issuer: str, subject: str, email: str, key: bytes
) -> dict[str, str]:
    """Map correlated identity values once, then recompute dependent digests."""

    if not subject.startswith("user_") or any(
        character.isspace() for character in subject
    ):
        _fail("transform.subject")
    mapped_issuer = pseudonymize_issuer(issuer, key=key)
    mapped_subject = pseudonymize_text(
        subject,
        key=key,
        domain="clerk_subject",
        prefix="user_",
        maximum_length=64,
    )
    mapped_email = pseudonymize_email(email, key=key)
    return {
        "issuer": mapped_issuer,
        "subject": mapped_subject,
        "email": mapped_email,
        "issuer_sha256": plain_sha256(mapped_issuer),
        "subject_sha256": plain_sha256(mapped_subject),
        "email_sha256": plain_sha256(mapped_email),
    }


def pseudonymize_provider_access_credentials(
    organization_id: uuid.UUID, *, key: bytes
) -> dict[str, str]:
    """Rotate one access-code verifier without returning its derived plaintext."""

    if not isinstance(organization_id, uuid.UUID):
        _fail("transform.provider_access_code")
    source = b"uuid:" + organization_id.bytes
    code_material = _hmac_digest(key, "provider_access_code.code", source)
    code = "".join(
        ACCESS_CODE_ALPHABET[byte % len(ACCESS_CODE_ALPHABET)]
        for byte in code_material[:12]
    )
    salt = _hmac_digest(key, "provider_access_code.salt", source)[:16]
    digest = hashlib.scrypt(
        code.encode("ascii"), salt=salt, n=2**14, r=8, p=1, dklen=32
    )
    return {"access_code_salt": salt.hex(), "access_code_digest": digest.hex()}


def superadmin_identity_binding_sha256(
    *,
    environment: str,
    capability_kind: str,
    issuer: str,
    subject: str,
    doctor_typeid: str,
) -> str:
    if environment != "staging" or not all(
        isinstance(value, str) and value
        for value in (capability_kind, issuer, subject, doctor_typeid)
    ):
        _fail("transform.identity_binding")
    return hashlib.sha256(
        _canonical_json(
            {
                "capability_kind": capability_kind,
                "clerk_issuer_sha256": plain_sha256(issuer),
                "clerk_subject_sha256": plain_sha256(subject),
                "doctor_id_sha256": plain_sha256(doctor_typeid),
                "environment": environment,
            }
        )
    ).hexdigest()


def _arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "operation",
        choices=("validate-policy", "preflight"),
        nargs="?",
        default="validate-policy",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    try:
        args = _arguments(argv)
        policy = load_canonical_policy()
        data_contract = load_canonical_data_contract()
        inventory_policy = _load_json(INVENTORY_POLICY_PATH, "inventory_policy.invalid")
        validate_policy(policy, inventory_policy, data_contract)
        validate_migration_files(policy)
        if args.operation == "preflight":
            require_activation()
        print(
            json.dumps(
                {
                    "schema_version": 1,
                    "operation": "validate-policy",
                    "executable": False,
                    "pass": True,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 0
    except SanitizerFailure as exc:
        print(
            json.dumps(
                {"schema_version": 1, "errors": [str(exc)], "pass": False},
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
