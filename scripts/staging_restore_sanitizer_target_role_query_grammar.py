#!/usr/bin/env python3
"""Validate the dormant target-role observer query grammar contract.

The module parses and authenticates fixed catalog-only SELECT templates.  It
does not open a database connection, obtain a credential, or execute SQL.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any, NoReturn

try:
    from scripts import (
        staging_restore_sanitizer_target_role_observer_contract as observer_contract,
    )
except ModuleNotFoundError:  # Direct `python scripts/...` compatibility.
    import staging_restore_sanitizer_target_role_observer_contract as observer_contract


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = (
    ROOT
    / "contracts/staging-rehearsal/v1/restore-sanitizer-target-role-query-grammar-contract.json"
)
TEMPLATE_PATH = (
    ROOT
    / "contracts/staging-rehearsal/v1/restore-sanitizer-target-role-query-templates.sql"
)
CONTRACT_SHA256 = "8f1c4831c4766942778e5be24149a779ba0cd84081d03c11ed21463aabc3d1fe"
TEMPLATE_SET_SHA256 = "a740c41e36314c9954456d3c0dadb7d496904ec8d94fdbf14c106e2d125f9e33"
OBSERVER_CONTRACT_SHA256 = (
    "acf34a80c63b0b103147ccd574195c11ebde16357a24bccf935030bca0f80fad"
)
ACTIVATION_FAILURE = "activation.sanitizer_target_role_observer_unimplemented"
MAXIMUM_CONTRACT_BYTES = 64 * 1024
MAXIMUM_TEMPLATE_SET_BYTES = 32 * 1024
_TEMPLATE_HEADER = (
    "-- CosmaNeura staging sanitizer target-role observer query templates v1\n"
    "-- Contract data only. Execute one named statement at a time in exact order.\n"
)
_FRAME_MARKER = re.compile(r"^-- frame: ([a-z_]+)\n", re.MULTILINE)
_STRING_LITERAL = re.compile(r"'(?:''|[^'])*'")
_FROM_OR_JOIN_REFERENCE = re.compile(
    r"\b(?:FROM|JOIN)\s+(?:LATERAL\s+)?([a-z_][a-z0-9_.]*)",
    re.IGNORECASE,
)
_UNQUALIFIED_CALL = re.compile(r"(?<![a-z0-9_.])([a-z_][a-z0-9_]*)\s*\(", re.IGNORECASE)
_FORBIDDEN_SQL = re.compile(
    r"\b(?:ALTER|ANALYZE|CALL|COPY|CREATE|DELETE|DO|DROP|GRANT|INSERT|LISTEN|"
    r"MERGE|NOTIFY|RESET|REVOKE|SET|TRUNCATE|UNLISTEN|UPDATE|VACUUM)\b",
    re.IGNORECASE,
)
_FAILURE_CODES = frozenset({ACTIVATION_FAILURE, "target_role_query_grammar.invalid"})

FRAME_NAMES = observer_contract.FRAME_NAMES
TEMPLATE_SHA256 = {
    "session_and_role": "72bd1091b3db0809c0ba7073fc0a16ee3c75b4e2c283c1c2afd55526eb4a6ca9",
    "memberships": "6a0b8664335b23537017524aca31826ea54c5f435be2e54bdb36564d9c0e22ed",
    "role_settings": "60ab0f87d52659a585e21120517f4620526fabc18d4baa69446a52be8ad1bcc4",
    "schemas": "1be61dc665dea591fa4a44d0623e097c06107d13b59c0045dd9ef5cfa9b579ff",
    "relations": "29ed4bc1c49dc39d2877251682332087480b2e4948b2e496e1531a14e4c49e0d",
    "columns": "e2e1b3652f7f853386c06e598d6d99df416bb30539b3ed841eb197083e7f39df",
    "sequences": "768305e199d2a4ff0be76662bf4b3742fc1f47de73da3521b494b4b64e3ec741",
    "routines": "081608fef1ac0aa50e825ae9c72730611312fcd7be85644c1331ae6231ec1ea7",
    "types": "8db9f409dbb8ca8245d267dd933da4ef63c168442137998b1bd944e644e383c3",
    "languages": "84c6f0f51168ffddd5f2353724f91a30c5991352052599f24d12265de793bcad",
    "foreign_data_wrappers": "f27ac22fdd0460be923d0ec12362b59fd68bc8a30f7f69d7e0183fdd29a4a0b6",
    "foreign_servers": "a491abd494c497f2d884998ecfcd9a913406c99603ab4799d283f1c500aaa4de",
    "user_mappings": "9133f16e06740441df0d80e554d60c5912422091e4f6b8db4f111cc27abd18cb",
}
MAXIMUM_ROWS = {
    "session_and_role": 1,
    "memberships": 64,
    "role_settings": 16,
    "schemas": 32,
    "relations": 2048,
    "columns": 256,
    "sequences": 256,
    "routines": 256,
    "types": 1024,
    "languages": 64,
    "foreign_data_wrappers": 64,
    "foreign_servers": 128,
    "user_mappings": 128,
}
CATALOG_ALLOWLIST = (
    "pg_catalog.pg_attribute",
    "pg_catalog.pg_auth_members",
    "pg_catalog.pg_class",
    "pg_catalog.pg_database",
    "pg_catalog.pg_db_role_setting",
    "pg_catalog.pg_foreign_data_wrapper",
    "pg_catalog.pg_foreign_server",
    "pg_catalog.pg_language",
    "pg_catalog.pg_namespace",
    "pg_catalog.pg_proc",
    "pg_catalog.pg_roles",
    "pg_catalog.pg_type",
    "pg_catalog.pg_user_mappings",
)
FUNCTION_ALLOWLIST = (
    ('pg_catalog.acldefault("char",oid)', "immutable"),
    ("pg_catalog.aclexplode(aclitem[])", "stable"),
    ("pg_catalog.current_database()", "stable"),
    ("pg_catalog.current_setting(text)", "stable"),
    ("pg_catalog.has_column_privilege(name,oid,smallint,text)", "stable"),
    ("pg_catalog.has_foreign_data_wrapper_privilege(name,oid,text)", "stable"),
    ("pg_catalog.has_function_privilege(name,oid,text)", "stable"),
    ("pg_catalog.has_language_privilege(name,oid,text)", "stable"),
    ("pg_catalog.has_schema_privilege(name,oid,text)", "stable"),
    ("pg_catalog.has_sequence_privilege(name,oid,text)", "stable"),
    ("pg_catalog.has_server_privilege(name,oid,text)", "stable"),
    ("pg_catalog.has_table_privilege(name,oid,text)", "stable"),
    ("pg_catalog.has_type_privilege(name,oid,text)", "stable"),
    ("pg_catalog.pg_get_function_identity_arguments(oid)", "stable"),
    ("pg_catalog.to_regrole(text)", "stable"),
)
REQUIREMENT_FRAMES = (
    ("exact-database-role-name", ("session_and_role",)),
    ("role-flags-and-connection-limit", ("session_and_role",)),
    ("empty-outbound-memberships", ("memberships",)),
    ("empty-inbound-members", ("memberships",)),
    ("set-role-escalation-unavailable", ("memberships",)),
    ("exact-direct-and-effective-schema-privileges", ("schemas",)),
    ("exact-direct-and-effective-table-privileges", ("relations",)),
    ("empty-explicit-column-acls", ("columns",)),
    (
        "column-effective-privileges-derived-only-from-exact-table-privileges",
        ("relations", "columns"),
    ),
    ("empty-sequence-privileges", ("sequences",)),
    ("empty-public-schema-routine-execute-privileges", ("routines",)),
    ("exact-fourteen-acl-manageable-custom-enum-types", ("types",)),
    ("empty-acl-and-effective-usage-for-acl-manageable-custom-enums", ("types",)),
    (
        "postgresql16-intrinsic-relation-row-and-row-array-usage-baseline",
        ("types",),
    ),
    ("postgresql16-enum-array-usage-derived-from-element-enum-acl", ("types",)),
    ("empty-language-usage-privileges", ("languages",)),
    (
        "empty-foreign-data-wrapper-and-server-usage-privileges",
        ("foreign_data_wrappers", "foreign_servers"),
    ),
    ("empty-user-mappings", ("user_mappings",)),
    ("all-pg-db-role-setting-source-rows-including-setrole-zero", ("role_settings",)),
    ("session-replication-role-origin", ("session_and_role",)),
    (
        "all-grant-options-absent",
        (
            "schemas",
            "relations",
            "columns",
            "sequences",
            "routines",
            "types",
            "languages",
            "foreign_data_wrappers",
            "foreign_servers",
        ),
    ),
    (
        "public-role-acl-sources-included-for-every-object-class",
        (
            "schemas",
            "relations",
            "columns",
            "sequences",
            "routines",
            "types",
            "languages",
            "foreign_data_wrappers",
            "foreign_servers",
            "user_mappings",
        ),
    ),
)
CLAIMS = dict.fromkeys(
    (
        "database_connection_authority",
        "database_mutation_authority",
        "database_role_state_authenticated",
        "effective_session_settings_authenticated",
        "evidence_publication_authority",
        "executable",
        "live_database_calls_enabled",
        "offline_authenticator_implemented",
        "observer_implemented",
        "provider_calls_permitted",
        "runtime_authority",
        "sanitizer_authority",
        "target_role_observation_authenticated",
        "transaction_executed",
        "workflow_integrated",
    ),
    False,
)


class SanitizerTargetRoleQueryGrammarFailure(RuntimeError):
    """Bounded refusal with no SQL, role, catalog, or target content."""


def _fail(code: str) -> NoReturn:
    if code not in _FAILURE_CODES:
        code = "target_role_query_grammar.invalid"
    raise SanitizerTargetRoleQueryGrammarFailure(code) from None


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
        _fail("target_role_query_grammar.invalid")


def _parse_json(raw: bytes, *, expected_sha256: str) -> dict[str, Any]:
    if (
        type(raw) is not bytes
        or not 1 <= len(raw) <= MAXIMUM_CONTRACT_BYTES
        or not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), expected_sha256)
    ):
        _fail("target_role_query_grammar.invalid")

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for name, value in items:
            if name in result:
                _fail("target_role_query_grammar.invalid")
            result[name] = value
        return result

    try:
        document = json.loads(
            raw,
            object_pairs_hook=pairs,
            parse_constant=lambda _value: _fail("target_role_query_grammar.invalid"),
        )
    except SanitizerTargetRoleQueryGrammarFailure:
        raise
    except (UnicodeDecodeError, ValueError, RecursionError):
        _fail("target_role_query_grammar.invalid")
    if type(document) is not dict:
        _fail("target_role_query_grammar.invalid")
    return document


def _read_contract() -> tuple[dict[str, Any], bytes]:
    try:
        raw = CONTRACT_PATH.read_bytes()
    except OSError:
        _fail("target_role_query_grammar.invalid")
    return _parse_json(raw, expected_sha256=CONTRACT_SHA256), raw


def _parse_templates(raw: bytes) -> dict[str, str]:
    if (
        type(raw) is not bytes
        or not 1 <= len(raw) <= MAXIMUM_TEMPLATE_SET_BYTES
        or not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), TEMPLATE_SET_SHA256)
    ):
        _fail("target_role_query_grammar.invalid")
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError:
        _fail("target_role_query_grammar.invalid")
    if not text.startswith(_TEMPLATE_HEADER) or not text.endswith("\n"):
        _fail("target_role_query_grammar.invalid")
    body = text[len(_TEMPLATE_HEADER) :]
    parts = _FRAME_MARKER.split(body)
    if not parts or parts[0] != "" or len(parts) != 1 + 2 * len(FRAME_NAMES):
        _fail("target_role_query_grammar.invalid")

    templates: dict[str, str] = {}
    observed_catalogs: set[str] = set()
    observed_functions: set[str] = set()
    observed_references: set[str] = set()
    observed_unqualified_calls: set[str] = set()
    for index in range(1, len(parts), 2):
        frame = parts[index]
        statement = parts[index + 1].strip()
        if frame in templates:
            _fail("target_role_query_grammar.invalid")
        statement_without_literals = _STRING_LITERAL.sub("''", statement)
        if (
            not statement.startswith("SELECT ")
            or not statement.endswith(";")
            or statement.count(";") != 1
            or len(statement.encode("ascii")) > 4096
            or "--" in statement
            or "/*" in statement
            or "*/" in statement
            or "$" in statement_without_literals
            or "{" in statement_without_literals
            or "}" in statement_without_literals
            or _FORBIDDEN_SQL.search(statement_without_literals)
            or not hmac.compare_digest(
                hashlib.sha256(statement.encode("ascii")).hexdigest(),
                TEMPLATE_SHA256.get(frame, ""),
            )
        ):
            _fail("target_role_query_grammar.invalid")
        observed_catalogs.update(
            match.lower()
            for match in re.findall(
                r"\b(?:FROM|JOIN)\s+(pg_catalog\.pg_[a-z_]+)\b",
                statement_without_literals,
                flags=re.IGNORECASE,
            )
        )
        observed_functions.update(
            f"pg_catalog.{name.lower()}"
            for name in re.findall(
                r"\bpg_catalog\.([a-z_]+)\s*\(",
                statement_without_literals,
                flags=re.IGNORECASE,
            )
        )
        observed_references.update(
            reference.lower()
            for reference in _FROM_OR_JOIN_REFERENCE.findall(statement_without_literals)
        )
        observed_unqualified_calls.update(
            call.upper()
            for call in _UNQUALIFIED_CALL.findall(statement_without_literals)
        )
        templates[frame] = statement

    expected_functions = {
        signature.split("(", 1)[0] for signature, _volatility in FUNCTION_ALLOWLIST
    }
    allowed_references = set(CATALOG_ALLOWLIST) | expected_functions
    if (
        tuple(templates) != FRAME_NAMES
        or observed_catalogs != set(CATALOG_ALLOWLIST)
        or observed_functions != expected_functions
        or not observed_references <= allowed_references
        or not observed_unqualified_calls <= {"COALESCE", "IN"}
    ):
        _fail("target_role_query_grammar.invalid")
    return templates


def _read_templates() -> tuple[dict[str, str], bytes]:
    try:
        raw = TEMPLATE_PATH.read_bytes()
    except OSError:
        _fail("target_role_query_grammar.invalid")
    return _parse_templates(raw), raw


def _validate_semantics(document: Mapping[str, Any]) -> None:
    try:
        envelope = observer_contract.load_target_role_observer_contract()
        grammar = document["grammar"]
        templates = grammar["templates"]
        status = document["status"]
    except (KeyError, TypeError):
        _fail("target_role_query_grammar.invalid")

    expected_coverage = [
        {"requirement": requirement, "covered_by_frames": list(frames)}
        for requirement, frames in REQUIREMENT_FRAMES
    ]
    expected_functions = [
        {
            "signature": signature,
            "volatility": volatility,
            "security_definer": False,
            "leakproof": False,
        }
        for signature, volatility in FUNCTION_ALLOWLIST
    ]
    if (
        observer_contract.CONTRACT_SHA256 != OBSERVER_CONTRACT_SHA256
        or envelope["policy_id"]
        != "cosmaneura-staging-sanitizer-target-role-database-observer-envelope-v1"
        or set(document)
        != {
            "schema_version",
            "policy_id",
            "closed_world",
            "upstream_policies",
            "input",
            "template_source",
            "requirement_coverage",
            "grammar",
            "resource_bounds",
            "execution_envelope",
            "output",
            "claims",
            "status",
        }
        or document.get("schema_version") != 1
        or document.get("policy_id")
        != "cosmaneura-staging-sanitizer-target-role-query-grammar-v1"
        or document.get("closed_world") is not True
        or document.get("upstream_policies")
        != {
            "target_role_observer_envelope": {
                "policy_id": envelope["policy_id"],
                "sha256": f"sha256:{OBSERVER_CONTRACT_SHA256}",
            }
        }
        or document.get("input")
        != {
            "only_future_public_input": "sealed_target_role_observation_plan",
            "caller_sql_identifiers_fragments_parameters_templates_order_catalogs_"
            "functions_bounds_or_output_schemas_permitted": False,
            "exact_process_local_template_set_required": True,
            "parameter_count": 0,
        }
        or document.get("template_source")
        != {
            "path": (
                "contracts/staging-rehearsal/v1/"
                "restore-sanitizer-target-role-query-templates.sql"
            ),
            "sha256": f"sha256:{TEMPLATE_SET_SHA256}",
            "maximum_bytes": MAXIMUM_TEMPLATE_SET_BYTES,
            "encoding": "ascii",
            "format": "named-single-statement-select-templates-v1",
        }
        or document.get("requirement_coverage") != expected_coverage
        or [item["requirement"] for item in expected_coverage]
        != list(observer_contract.TARGET_REQUIREMENTS)
        or grammar.get("postgresql_major_version") != 16
        or grammar.get("statement_class") != "SELECT"
        or grammar.get("exact_template_count") != len(FRAME_NAMES)
        or grammar.get("exact_order") != list(FRAME_NAMES)
        or grammar.get("catalog_allowlist") != list(CATALOG_ALLOWLIST)
        or grammar.get("function_allowlist") != expected_functions
        or not isinstance(templates, list)
        or len(templates) != len(FRAME_NAMES)
    ):
        _fail("target_role_query_grammar.invalid")

    for item, frame in zip(templates, FRAME_NAMES, strict=True):
        if (
            type(item) is not dict
            or set(item) != {"frame", "sha256", "maximum_rows", "exact_columns"}
            or item.get("frame") != frame
            or item.get("sha256") != f"sha256:{TEMPLATE_SHA256[frame]}"
            or item.get("maximum_rows") != MAXIMUM_ROWS[frame]
            or type(item.get("exact_columns")) is not list
            or not item["exact_columns"]
        ):
            _fail("target_role_query_grammar.invalid")
        column_names: list[str] = []
        for column in item["exact_columns"]:
            if (
                type(column) is not dict
                or set(column) != {"name", "type", "nullable"}
                or type(column.get("name")) is not str
                or not column["name"]
                or column.get("type")
                not in {"boolean", "int16", "int32", "int64", "text"}
                or type(column.get("nullable")) is not bool
            ):
                _fail("target_role_query_grammar.invalid")
            column_names.append(column["name"])
        if len(column_names) != len(set(column_names)):
            _fail("target_role_query_grammar.invalid")

    fixed_grammar_flags = {
        "exact_template_bytes_only": True,
        "one_statement_per_execute": True,
        "execute_in_exact_order_once": True,
        "server_side_prepare_permitted": False,
        "caller_parameter_binding_permitted": False,
        "caller_identifier_interpolation_permitted": False,
        "application_schema_or_table_reads_permitted": False,
        "unlisted_catalog_or_function_access_permitted": False,
        "unlisted_select_call_do_copy_ddl_dml_notify_large_object_prepared_"
        "transaction_advisory_lock_or_guc_operations_permitted": False,
    }
    if (
        any(
            grammar.get(name) is not value
            for name, value in fixed_grammar_flags.items()
        )
        or set(grammar)
        != {
            "postgresql_major_version",
            "statement_class",
            "exact_template_count",
            "exact_order",
            "templates",
            "catalog_allowlist",
            "function_allowlist",
            *fixed_grammar_flags,
        }
        or document.get("resource_bounds")
        != {
            "maximum_queries": 13,
            "maximum_template_bytes": 4096,
            "maximum_rows_total": 4096,
            "maximum_row_bytes": 16384,
            "maximum_output_bytes": 8388608,
            "maximum_elapsed_seconds_per_query": 5,
            "maximum_elapsed_seconds_total": 30,
            "retry_permitted": False,
        }
        or document.get("execution_envelope")
        != {
            "connection_source": "target_role_observer_envelope.future_connection",
            "transaction_source": "target_role_observer_envelope.future_transaction",
            "read_only_repeatable_read_required": True,
            "cursor_description_must_match_exact_columns_before_first_row": True,
            "row_and_byte_bounds_must_be_enforced_while_streaming": True,
            "rollback_and_close_required_on_success_or_failure": True,
            "raw_rows_sql_tokens_credentials_connections_cursors_or_callbacks_escape_"
            "process_permitted": False,
        }
        or document.get("output")
        != {
            "frame_count": 13,
            "exact_order": list(FRAME_NAMES),
            "canonical_json_required": True,
            "duplicate_rows_permitted": False,
            "nullability_defined_by_exact_columns": True,
            "sealed_in_process_output_required": True,
            "disk_log_artifact_or_evidence_publication_permitted": False,
            "raw_application_values_permitted": False,
        }
        or document.get("claims") != CLAIMS
        or status
        != {
            "template_source_hash_pinned": True,
            "query_templates_selected": True,
            "query_template_digests_selected": True,
            "catalog_allowlist_selected": True,
            "function_allowlist_selected": True,
            "frame_schemas_selected": True,
            "resource_bounds_selected": True,
            "connection_adapter_implemented": False,
            "observer_implemented": False,
            "offline_authenticator_implemented": False,
            "live_database_calls_enabled": False,
            "database_connection_authority": False,
            "database_mutation_authority": False,
            "runtime_authority": False,
            "sanitizer_authority": False,
            "workflow_integrated": False,
            "executable": False,
            "activation_ready": False,
            "public_activation_failure": ACTIVATION_FAILURE,
            "activation_blockers": [
                "exact_database_role_unselected",
                "provision_job_identity_exact_ids_unpinned",
                "target_role_database_observer_unimplemented",
                "offline_authenticator_unimplemented",
                "privileged_global_observer_unimplemented",
                "stability_authorities_unimplemented",
                "post_provision_schema_descriptor_unselected",
                "adapter_join_unimplemented",
            ],
        }
    ):
        _fail("target_role_query_grammar.invalid")


def validate_target_role_query_grammar_contract(
    contract: Any, *, executable: bool = False
) -> dict[str, Any]:
    """Validate exact grammar and templates; executable use remains unavailable."""

    try:
        if type(contract) is not dict or type(executable) is not bool:
            _fail("target_role_query_grammar.invalid")
        template, _raw = _read_contract()
        canonical = _canonical(contract)
        document = _parse_json(
            canonical,
            expected_sha256=hashlib.sha256(canonical).hexdigest(),
        )
        if not hmac.compare_digest(_canonical(document), _canonical(template)):
            _fail("target_role_query_grammar.invalid")
        _validate_semantics(document)
        _read_templates()
        if executable:
            _fail(ACTIVATION_FAILURE)
        return document
    except SanitizerTargetRoleQueryGrammarFailure:
        raise
    except Exception:
        _fail("target_role_query_grammar.invalid")


def load_target_role_query_grammar_contract(
    *, executable: bool = False
) -> dict[str, Any]:
    document, _raw = _read_contract()
    return validate_target_role_query_grammar_contract(
        document,
        executable=executable,
    )


if __name__ == "__main__":
    load_target_role_query_grammar_contract(executable="--executable" in sys.argv)
    print("sanitizer target-role query grammar contract valid (dormant)")
