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
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NoReturn

import staging_synthetic_inventory as inventory

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "contracts/staging-rehearsal/v1/restore-sanitizer-policy.json"
DATA_CONTRACT_PATH = (
    ROOT / "contracts/staging-rehearsal/v1/restore-sanitizer-data-contract.json"
)
INVENTORY_POLICY_PATH = (
    ROOT / "contracts/staging-rehearsal/v1/synthetic-data-policy.json"
)
CANONICAL_POLICY_SHA256 = (
    "4c20b0118ee5e93b9ee4138418bdd93b2f411fbe69af4c834a4ae39651e74291"
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

SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
REHEARSAL_RE = re.compile(r"^[0-9a-f]{12}$")
TARGET_NAME_PATTERN = r"cnr-[0-9a-f]{12}-e[0-9]{10}-pg"
TARGET_NAME_RE = re.compile(
    r"^cnr-(?P<rehearsal>[0-9a-f]{12})-e(?P<expiry>[0-9]{10})-pg$"
)
DOMAIN_RE = re.compile(r"^[a-z0-9][a-z0-9:._-]{0,127}$")
ROLE_RE = re.compile(r"^[a-z][a-z0-9_]{2,62}$")
UUID_TEXT = r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
TARGET_RESOURCE_RE = re.compile(
    rf"^/subscriptions/({UUID_TEXT})/resourceGroups/"
    rf"cosmaneura-staging-rehearsal/providers/"
    rf"Microsoft\.DBforPostgreSQL/flexibleServers/({TARGET_NAME_PATTERN})$",
    re.IGNORECASE,
)

TOP_LEVEL_KEYS = {
    "schema_version",
    "policy_id",
    "closed_world",
    "status",
    "inventory",
    "target",
    "controller_receipt",
    "transform_contract",
    "execution_contract",
    "migration_contract",
    "evidence_contract",
    "limitations",
}
RECEIPT_TOP_LEVEL = {
    "schema_version",
    "policy_id",
    "policy_sha256",
    "repository",
    "workflow_sha",
    "run_id",
    "run_attempt",
    "rehearsal_id",
    "source_inventory",
    "source_postgres",
    "target",
    "provisioner",
    "created_at",
    "expires_at",
    "cleanup_not_after",
    "isolation",
    "pass",
}
RECEIPT_SOURCE_INVENTORY = {
    "artifact_sha256",
    "quiescence_sha256",
    "profile",
    "revision",
    "source_sha",
    "candidate_sha",
}
RECEIPT_TARGET = {
    "postgres_resource_id",
    "database_name",
    "vnet_id",
    "subnet_id",
    "private_endpoint_id",
    "network_interface_id",
    "private_dns_zone_id",
    "private_dns_link_id",
    "private_dns_zone_group_id",
}
FIXED_CATALOG_TABLES = {
    "baa_version",
    "doctor_note_schema",
    "drug_reference",
    "lab_range_set",
    "note_type_customization",
}
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
    "authenticated_controller_receipt_verification",
    "controller_receipt_target_fqdn",
    "controller_receipt_authoritative_restore_point",
    "authenticated_server_scope_evidence_binding",
    "approved_database_role",
    "data_contract_executor_implementation",
    "fixed_catalog_source_state_and_post_projection_verification",
    "derived_value_execution_proof",
    "atomic_transaction_and_postcommit_verification",
    "guarded_table_truncate_permission_and_rollback_proof",
}
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


@dataclass(frozen=True, slots=True)
class TargetBinding:
    """Shape-validated controller claims; not authenticated evidence."""

    resource_id: str
    server_name: str
    derived_fqdn: str
    database_name: str
    rehearsal_id: str
    created_at: datetime
    expires_at: datetime
    source_sha: str
    candidate_sha: str
    quiescence_digest: str
    workflow_sha: str
    run_id: str
    run_attempt: str


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
            "executable",
            "trusted_evidence_binding_implemented",
            "database_mutation_implemented",
            "activation_blockers",
        },
        "policy.status",
    )
    if (
        status["executable"] is not False
        or status["trusted_evidence_binding_implemented"] is not False
        or status["database_mutation_implemented"] is not False
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
            "approved_role",
            "require_no_competing_sessions",
            "rejected_hosts",
        },
        "policy.target",
    )
    if (
        target["server_name_pattern"] != TARGET_NAME_PATTERN
        or target["resource_group"] != "cosmaneura-staging-rehearsal"
        or target["postgres_fqdn_suffix"] != ".postgres.database.azure.com"
        or target["database_name"] != "staging"
        or target["port"] != 5432
        or target["postgresql_major"] != 16
        or target["sslmode"] != "verify-full"
        or target["application_name"] != APP_NAME
        or target["approved_role"] != "CONFIGURE_EXACT_SANITIZER_ROLE"
        or target["require_no_competing_sessions"] is not True
        or set(target["rejected_hosts"])
        != {
            "cosmaneura-prod-postgres.postgres.database.azure.com",
            "cosmaneura-staging-postgres.postgres.database.azure.com",
        }
    ):
        _fail("policy.target")

    receipt = _exact_keys(
        policy["controller_receipt"],
        {
            "policy_id",
            "policy_sha256",
            "nested_fields_are_exact_and_unknowns_are_rejected",
            "required_top_level_fields",
            "required_source_inventory_fields",
            "required_target_fields",
            "bound_current_fields",
            "pinned_source_postgres",
            "pinned_isolation",
            "missing_required_bindings",
            "derived_target_fqdn_is_not_authenticated_binding",
        },
        "policy.controller_receipt",
    )
    if (
        receipt["policy_id"] != "cosmaneura-staging-restore-envelope-v1"
        or receipt["policy_sha256"]
        != "sha256:6a80a9681bf2527b7c69f48c896baa01a7b5face3ddb415c1300632dab020d92"
        or receipt["nested_fields_are_exact_and_unknowns_are_rejected"] is not True
        or set(receipt["required_top_level_fields"]) != RECEIPT_TOP_LEVEL
        or set(receipt["required_source_inventory_fields"]) != RECEIPT_SOURCE_INVENTORY
        or set(receipt["required_target_fields"]) != RECEIPT_TARGET
        or set(receipt["missing_required_bindings"])
        != {
            "authenticated_receipt_verification",
            "target_fqdn_emitted_by_controller",
            "authoritative_restore_point_emitted_by_controller",
        }
        or receipt["derived_target_fqdn_is_not_authenticated_binding"] is not True
        or receipt["bound_current_fields"]
        != {
            "source_sha": "source_inventory.source_sha",
            "candidate_sha": "source_inventory.candidate_sha",
            "inventory_digest": "source_inventory.artifact_sha256",
            "quiescence_digest": "source_inventory.quiescence_sha256",
            "target_resource_id": "target.postgres_resource_id",
            "created_at": "created_at",
            "expires_at": "expires_at",
            "workflow_sha": "workflow_sha",
            "run_id": "run_id",
            "run_attempt": "run_attempt",
            "isolation": "isolation",
        }
        or receipt["pinned_source_postgres"]
        != {
            "resource_group": "cosmaneura-staging",
            "server_name": "cosmaneura-staging-postgres",
            "fqdn": "cosmaneura-staging-postgres.postgres.database.azure.com",
            "database_name": "staging",
        }
        or receipt["pinned_isolation"]
        != {
            "public_access": "Disabled",
            "firewall_rule_count": 0,
            "vnet_peering_count": 0,
            "public_ip_count": 0,
            "unknown_resource_count": 0,
            "blob_copied": False,
            "redis_copied": False,
        }
    ):
        _fail("policy.controller_receipt")

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


def require_activation(policy: Mapping[str, Any]) -> NoReturn:
    """Refuse before a caller may import a driver or invoke a connector."""

    del policy
    _fail("activation.trusted_binding_unimplemented")


def preflight(
    policy: Mapping[str, Any],
    inventory_policy: Mapping[str, Any],
    connector: Callable[[], Any],
) -> NoReturn:
    """Validate the contract, then unconditionally stop before ``connector``."""

    del connector
    validate_policy(policy, inventory_policy)
    require_activation(policy)


def _utc(value: object, code: str) -> datetime:
    if not isinstance(value, str) or not re.fullmatch(
        r"20[0-9]{2}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", value
    ):
        _fail(code)
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        _fail(code)


def _same_id(actual: object, expected: str, code: str) -> None:
    if str(actual).rstrip("/").lower() != expected.rstrip("/").lower():
        _fail(code)


def validate_controller_receipt_shape(
    receipt: Mapping[str, Any], policy: Mapping[str, Any]
) -> TargetBinding:
    """Validate only the current controller receipt shape and internal binding.

    This cannot authenticate the receipt and deliberately does not synthesize
    either the omitted target-FQDN attestation or authoritative restore point.
    """

    _exact_keys(receipt, RECEIPT_TOP_LEVEL, "receipt.closed_world")
    source = _exact_keys(
        receipt["source_inventory"],
        RECEIPT_SOURCE_INVENTORY,
        "receipt.source_inventory",
    )
    source_postgres = _exact_keys(
        receipt["source_postgres"],
        {"resource_id", "fqdn", "database_name"},
        "receipt.source_postgres",
    )
    target = _exact_keys(receipt["target"], RECEIPT_TARGET, "receipt.target")
    _exact_keys(
        receipt["provisioner"], {"client_id", "principal_id"}, "receipt.provisioner"
    )
    isolation = _exact_keys(
        receipt["isolation"],
        {
            "public_access",
            "firewall_rule_count",
            "vnet_peering_count",
            "public_ip_count",
            "unknown_resource_count",
            "blob_copied",
            "redis_copied",
        },
        "receipt.isolation",
    )
    if (
        receipt["schema_version"] != 1
        or receipt["policy_id"] != policy["controller_receipt"]["policy_id"]
        or receipt["policy_sha256"] != policy["controller_receipt"]["policy_sha256"]
        or receipt["repository"] != "CosmaNeura/cosmaneura"
        or receipt["pass"] is not True
        or not DIGEST_RE.fullmatch(str(receipt["policy_sha256"]))
        or not SHA40_RE.fullmatch(str(receipt["workflow_sha"]))
        or not str(receipt["run_id"]).isdigit()
        or not str(receipt["run_attempt"]).isdigit()
        or not REHEARSAL_RE.fullmatch(str(receipt["rehearsal_id"]))
        or not DIGEST_RE.fullmatch(str(source["artifact_sha256"]))
        or not DIGEST_RE.fullmatch(str(source["quiescence_sha256"]))
        or not SHA40_RE.fullmatch(str(source["source_sha"]))
        or not SHA40_RE.fullmatch(str(source["candidate_sha"]))
        or source["profile"] != policy["inventory"]["source_profile"]
        or source["revision"] != policy["inventory"]["source_revision"]
        or source_postgres["fqdn"]
        != "cosmaneura-staging-postgres.postgres.database.azure.com"
        or source_postgres["database_name"] != "staging"
        or isolation != policy["controller_receipt"]["pinned_isolation"]
    ):
        _fail("receipt.invalid")

    resource_id = str(target["postgres_resource_id"])
    resource_match = TARGET_RESOURCE_RE.fullmatch(resource_id)
    if resource_match is None:
        _fail("receipt.target_resource")
    subscription, server_name = resource_match.groups()
    name_match = TARGET_NAME_RE.fullmatch(server_name.lower())
    if name_match is None or name_match.group("rehearsal") != receipt["rehearsal_id"]:
        _fail("receipt.target_name")
    created_at = _utc(receipt["created_at"], "receipt.created_at")
    expires_at = _utc(receipt["expires_at"], "receipt.expires_at")
    cleanup_at = _utc(receipt["cleanup_not_after"], "receipt.cleanup_not_after")
    if (
        created_at >= expires_at
        or cleanup_at != expires_at
        or int(expires_at.timestamp()) != int(name_match.group("expiry"))
        or target["database_name"] != policy["target"]["database_name"]
    ):
        _fail("receipt.lifecycle")

    group_root = (
        f"/subscriptions/{subscription}/resourceGroups/cosmaneura-staging-rehearsal"
    )
    base = server_name.removesuffix("-pg")
    vnet = f"{group_root}/providers/Microsoft.Network/virtualNetworks/{base}-vnet"
    pe = f"{group_root}/providers/Microsoft.Network/privateEndpoints/{base}-pep"
    nic = f"{group_root}/providers/Microsoft.Network/networkInterfaces/{base}-nic"
    zone = (
        f"{group_root}/providers/Microsoft.Network/privateDnsZones/"
        "privatelink.postgres.database.azure.com"
    )
    _same_id(target["vnet_id"], vnet, "receipt.network")
    _same_id(
        target["subnet_id"], f"{vnet}/subnets/private-endpoints", "receipt.network"
    )
    _same_id(target["private_endpoint_id"], pe, "receipt.network")
    _same_id(target["network_interface_id"], nic, "receipt.network")
    _same_id(target["private_dns_zone_id"], zone, "receipt.network")
    _same_id(
        target["private_dns_link_id"],
        f"{zone}/virtualNetworkLinks/{base}-dnslink",
        "receipt.network",
    )
    _same_id(
        target["private_dns_zone_group_id"],
        f"{pe}/privateDnsZoneGroups/default",
        "receipt.network",
    )
    source_id = (
        f"/subscriptions/{subscription}/resourceGroups/cosmaneura-staging/providers/"
        "Microsoft.DBforPostgreSQL/flexibleServers/cosmaneura-staging-postgres"
    )
    _same_id(source_postgres["resource_id"], source_id, "receipt.source_postgres")
    return TargetBinding(
        resource_id=resource_id,
        server_name=server_name.lower(),
        derived_fqdn=server_name.lower() + policy["target"]["postgres_fqdn_suffix"],
        database_name=str(target["database_name"]),
        rehearsal_id=str(receipt["rehearsal_id"]),
        created_at=created_at,
        expires_at=expires_at,
        source_sha=str(source["source_sha"]),
        candidate_sha=str(source["candidate_sha"]),
        quiescence_digest=str(source["quiescence_sha256"]),
        workflow_sha=str(receipt["workflow_sha"]),
        run_id=str(receipt["run_id"]),
        run_attempt=str(receipt["run_attempt"]),
    )


def validate_target_url(
    database_url: str,
    binding: TargetBinding,
    policy: Mapping[str, Any],
    *,
    expected_role: str,
) -> None:
    """Validate an isolated target URL without opening a socket."""

    if not ROLE_RE.fullmatch(expected_role):
        _fail("target.role")
    try:
        parsed = urllib.parse.urlsplit(database_url)
        port = parsed.port
        query = urllib.parse.parse_qs(parsed.query, strict_parsing=True)
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
        or parsed.hostname != binding.derived_fqdn
        or parsed.hostname in set(policy["target"]["rejected_hosts"])
        or port != policy["target"]["port"]
        or urllib.parse.unquote(parsed.username or "") != expected_role
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
            preflight(policy, inventory_policy, lambda: None)
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
