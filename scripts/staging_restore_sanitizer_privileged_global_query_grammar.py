#!/usr/bin/env python3
"""Validate the dormant privileged/global observer query grammar.

The module authenticates fixed PostgreSQL 16 catalog-only SELECT templates.
It opens no database connection, obtains no credential, and executes no SQL.
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
        staging_restore_sanitizer_privileged_global_observer_contract as observer_contract,
    )
except ModuleNotFoundError:  # Direct `python scripts/...` compatibility.
    import staging_restore_sanitizer_privileged_global_observer_contract as observer_contract


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = (
    ROOT
    / "contracts/staging-rehearsal/v1/restore-sanitizer-privileged-global-query-grammar-contract.json"
)
TEMPLATE_PATH = (
    ROOT
    / "contracts/staging-rehearsal/v1/restore-sanitizer-privileged-global-query-templates.sql"
)
CONTRACT_SHA256 = "d6d751a71f87936768f153986f087a89ca5edf4cf1a3dc9f5193a225dbe23bb8"
TEMPLATE_SET_SHA256 = "bd6a985e8101363332de22a753ffde90837c9d8fdd8532a03e1a87066992d3fa"
OBSERVER_CONTRACT_SHA256 = (
    "040874bb93f30c17168a49284b9255be982f2530e1cf144325ad89ae174a63d0"
)
ACTIVATION_FAILURE = (
    "activation.sanitizer_privileged_global_database_observer_unimplemented"
)
MAXIMUM_CONTRACT_BYTES = 128 * 1024
MAXIMUM_TEMPLATE_SET_BYTES = 32 * 1024
MAXIMUM_TEMPLATE_BYTES = 20 * 1024
_TEMPLATE_HEADER = (
    "-- CosmaNeura staging sanitizer privileged/global observer query templates v1\n"
    "-- Contract data only. Execute server-once frames first and database frames once per authenticated database.\n"
)
_FRAME_MARKER = re.compile(r"^-- frame: ([a-z_]+)\n", re.MULTILINE)
_STRING_LITERAL = re.compile(r"'(?:''|[^'])*'")
_QUALIFIED_CALL = re.compile(r"\bpg_catalog\.([a-z_]+)\s*\(", re.IGNORECASE)
_UNQUALIFIED_CALL = re.compile(r"(?<![a-z0-9_.])([a-z_][a-z0-9_]*)\s*\(", re.IGNORECASE)
_FORBIDDEN_SQL = re.compile(
    r"\b(?:ALTER|ANALYZE|CALL|COPY|CREATE|DELETE|DO|DROP|GRANT|INSERT|LISTEN|"
    r"MERGE|NOTIFY|RESET|REVOKE|SET|TRUNCATE|UNLISTEN|UPDATE|VACUUM)\b",
    re.IGNORECASE,
)
_FAILURE_CODES = frozenset(
    {ACTIVATION_FAILURE, "privileged_global_query_grammar.invalid"}
)

SERVER_ONCE_FRAME_NAMES = (
    "entra_principal_mappings",
    "password_null_state",
    "role_shared_dependencies",
    "database_inventory",
    "database_acl_sources",
    "database_effective_privileges",
    "runtime_role_sessions",
    "tablespace_acl_sources",
    "tablespace_effective_privileges",
    "parameter_acl_sources",
    "parameter_effective_privileges",
)
PER_DATABASE_FRAME_NAMES = (
    "database_object_authority",
    "system_object_acl_deltas",
)
FRAME_NAMES = SERVER_ONCE_FRAME_NAMES + PER_DATABASE_FRAME_NAMES
TEMPLATE_SHA256 = {
    "entra_principal_mappings": "529d0f94f277a0493e0104960f78cb99e9f25d9903924d7c397bd229139083ec",
    "password_null_state": "d7ec47ff983303a40bb6174cdaaa0ec5b6e88de35a06248d1e4ca54ef7582c93",
    "role_shared_dependencies": "ded6ce1f85344ea229abad37eb307ab1186b77f0474cdad7266fef6570eee512",
    "database_inventory": "b37ad59fd60c1a5824f1fcad49ce07e5c5570d0fd60fc13232df46c85a8f8262",
    "database_acl_sources": "903d824d6d45d052a84a39c4239e38eee779ab9947616547f9741e0d8eeb7e27",
    "database_effective_privileges": "31c09417e53e17fb3a502bf7776588e68388f68baf984cbc0f8f4c5c1f5f88a3",
    "runtime_role_sessions": "6b48f65c5d4e3108fcbbd378ac991d518268af67a7f458d67c574cdf79d84379",
    "tablespace_acl_sources": "30693eb204dab83ce4c43c99c16b0c9160962b0e200ed72a4672c3ee11b574e4",
    "tablespace_effective_privileges": "25507ea8d1d3685f9ca6d3e76c53b29fa97d034e7b6806a0d898caa2747b33af",
    "parameter_acl_sources": "32d7a60df6a26f0f01d40e7ad5bdd3e9411a3bf296c1ff165395810ea79889d4",
    "parameter_effective_privileges": "67c266a03a146736092b6150a59e4f898224448541523167a7728833155e6959",
    "database_object_authority": "6c7f90e0a19d4492e5499ff510737be65b9b0880e92e00a7c23231fa088bc430",
    "system_object_acl_deltas": "821f075a78a4d3f109886b8362de38ab09055747fa66990a589d48d9d40c7a29",
}
MAXIMUM_ROWS = {
    "entra_principal_mappings": 512,
    "password_null_state": 512,
    "role_shared_dependencies": 65536,
    "database_inventory": 64,
    "database_acl_sources": 2048,
    "database_effective_privileges": 32768,
    "runtime_role_sessions": 1024,
    "tablespace_acl_sources": 1024,
    "tablespace_effective_privileges": 4096,
    "parameter_acl_sources": 4096,
    "parameter_effective_privileges": 65536,
    "database_object_authority": 4096,
    "system_object_acl_deltas": 65536,
}


def _columns(*items: tuple[str, str, bool]) -> tuple[dict[str, object], ...]:
    return tuple(
        {"name": name, "type": value_type, "nullable": nullable}
        for name, value_type, nullable in items
    )


FRAME_COLUMNS = {
    "entra_principal_mappings": _columns(
        ("role_oid", "int64", False),
        ("role_name", "text", False),
        ("label_provider", "text", False),
        ("label", "text", False),
    ),
    "password_null_state": _columns(
        ("role_oid", "int64", False),
        ("role_name", "text", False),
        ("password_is_null", "boolean", False),
    ),
    "role_shared_dependencies": _columns(
        ("database_oid", "int64", False),
        ("database_name", "text", True),
        ("dependent_class_oid", "int64", False),
        ("dependent_object_oid", "int64", False),
        ("dependent_object_sub_id", "int32", False),
        ("referenced_role_oid", "int64", False),
        ("referenced_role_name", "text", False),
        ("dependency_type", "text", False),
    ),
    "database_inventory": _columns(
        ("database_oid", "int64", False),
        ("database_name", "text", False),
        ("owner_role_oid", "int64", False),
        ("owner_role_name", "text", False),
        ("is_template", "boolean", False),
        ("allows_connections", "boolean", False),
        ("connection_limit", "int32", False),
        ("tablespace_oid", "int64", False),
    ),
    "database_acl_sources": _columns(
        ("database_oid", "int64", False),
        ("database_name", "text", False),
        ("grantor_role_oid", "int64", False),
        ("grantor_role_name", "text", True),
        ("grantee_role_oid", "int64", False),
        ("grantee_role_name", "text", True),
        ("privilege_type", "text", False),
        ("is_grantable", "boolean", False),
    ),
    "database_effective_privileges": _columns(
        ("role_oid", "int64", False),
        ("role_name", "text", False),
        ("database_oid", "int64", False),
        ("database_name", "text", False),
        ("role_is_owner", "boolean", False),
        ("effective_connect", "boolean", False),
        ("effective_create", "boolean", False),
        ("effective_temporary", "boolean", False),
    ),
    "runtime_role_sessions": _columns(
        ("database_oid", "int64", True),
        ("database_name", "text", True),
        ("process_id", "int32", False),
        ("leader_process_id", "int32", True),
        ("role_oid", "int64", True),
        ("role_name", "text", True),
        ("backend_type", "text", False),
        ("state", "text", True),
    ),
    "tablespace_acl_sources": _columns(
        ("tablespace_oid", "int64", False),
        ("tablespace_name", "text", False),
        ("owner_role_oid", "int64", False),
        ("owner_role_name", "text", False),
        ("grantor_role_oid", "int64", False),
        ("grantor_role_name", "text", True),
        ("grantee_role_oid", "int64", False),
        ("grantee_role_name", "text", True),
        ("privilege_type", "text", False),
        ("is_grantable", "boolean", False),
    ),
    "tablespace_effective_privileges": _columns(
        ("role_oid", "int64", False),
        ("role_name", "text", False),
        ("tablespace_oid", "int64", False),
        ("tablespace_name", "text", False),
        ("role_is_owner", "boolean", False),
        ("effective_create", "boolean", False),
    ),
    "parameter_acl_sources": _columns(
        ("parameter_acl_oid", "int64", False),
        ("parameter_name", "text", False),
        ("grantor_role_oid", "int64", False),
        ("grantor_role_name", "text", True),
        ("grantee_role_oid", "int64", False),
        ("grantee_role_name", "text", True),
        ("privilege_type", "text", False),
        ("is_grantable", "boolean", False),
    ),
    "parameter_effective_privileges": _columns(
        ("role_oid", "int64", False),
        ("role_name", "text", False),
        ("parameter_acl_oid", "int64", False),
        ("parameter_name", "text", False),
        ("effective_set", "boolean", False),
        ("effective_alter_system", "boolean", False),
    ),
    "database_object_authority": _columns(
        ("object_class", "text", False),
        ("object_oid", "int64", False),
        ("object_name", "text", False),
        ("owner_role_oid", "int64", False),
        ("owner_role_name", "text", False),
    ),
    "system_object_acl_deltas": _columns(
        ("delta_side", "text", False),
        ("object_class", "text", False),
        ("object_oid", "int64", False),
        ("object_sub_id", "int32", False),
        ("object_identity", "text", False),
        ("grantor_role_oid", "int64", False),
        ("grantor_role_name", "text", True),
        ("grantee_role_oid", "int64", False),
        ("grantee_role_name", "text", True),
        ("privilege_type", "text", False),
        ("is_grantable", "boolean", False),
    ),
}
CATALOG_ALLOWLIST = (
    "pg_catalog.pg_attribute",
    "pg_catalog.pg_authid",
    "pg_catalog.pg_class",
    "pg_catalog.pg_database",
    "pg_catalog.pg_event_trigger",
    "pg_catalog.pg_extension",
    "pg_catalog.pg_foreign_data_wrapper",
    "pg_catalog.pg_foreign_server",
    "pg_catalog.pg_init_privs",
    "pg_catalog.pg_language",
    "pg_catalog.pg_namespace",
    "pg_catalog.pg_parameter_acl",
    "pg_catalog.pg_proc",
    "pg_catalog.pg_publication",
    "pg_catalog.pg_roles",
    "pg_catalog.pg_shdepend",
    "pg_catalog.pg_shseclabel",
    "pg_catalog.pg_stat_activity",
    "pg_catalog.pg_subscription",
    "pg_catalog.pg_tablespace",
    "pg_catalog.pg_type",
)
FUNCTION_ALLOWLIST = (
    ('pg_catalog.acldefault("char",oid)', "immutable"),
    ("pg_catalog.aclexplode(aclitem[])", "stable"),
    ("pg_catalog.has_database_privilege(oid,oid,text)", "stable"),
    ("pg_catalog.has_parameter_privilege(oid,text,text)", "stable"),
    ("pg_catalog.has_tablespace_privilege(oid,oid,text)", "stable"),
)
REQUIREMENT_FRAMES = (
    ("exact-provision-job-uami-mapping", ("entra_principal_mappings",)),
    ("null-password-state", ("password_null_state",)),
    (
        "empty-ownership-sets",
        ("role_shared_dependencies", "database_object_authority"),
    ),
    (
        "no-database-or-shared-object-ownership-dependencies",
        ("role_shared_dependencies", "database_inventory"),
    ),
    (
        "closed-world-every-pg-database-entry-and-datallowconn",
        ("database_inventory",),
    ),
    (
        "exact-direct-public-membership-and-effective-database-privileges",
        ("database_acl_sources", "database_effective_privileges"),
    ),
    (
        "target-only-effective-connect-and-no-create-or-temporary-anywhere",
        (
            "database_inventory",
            "database_effective_privileges",
            "runtime_role_sessions",
        ),
    ),
    (
        "empty-tablespace-create-privileges",
        ("tablespace_acl_sources", "tablespace_effective_privileges"),
    ),
    (
        "empty-parameter-set-and-alter-system-privileges",
        ("parameter_acl_sources", "parameter_effective_privileges"),
    ),
    (
        "empty-publication-subscription-extension-and-event-trigger-authority",
        ("database_object_authority",),
    ),
    (
        "system-object-explicit-and-public-acl-deltas-absent",
        ("system_object_acl_deltas",),
    ),
)
INFORMATION_SCHEMA_PUBLIC_SELECT_BASELINE = (
    "administrable_role_authorizations",
    "applicable_roles",
    "attributes",
    "character_sets",
    "check_constraint_routine_usage",
    "check_constraints",
    "collation_character_set_applicability",
    "collations",
    "column_column_usage",
    "column_domain_usage",
    "column_options",
    "column_privileges",
    "column_udt_usage",
    "columns",
    "constraint_column_usage",
    "constraint_table_usage",
    "data_type_privileges",
    "domain_constraints",
    "domain_udt_usage",
    "domains",
    "element_types",
    "enabled_roles",
    "foreign_data_wrapper_options",
    "foreign_data_wrappers",
    "foreign_server_options",
    "foreign_servers",
    "foreign_table_options",
    "foreign_tables",
    "information_schema_catalog_name",
    "key_column_usage",
    "parameters",
    "referential_constraints",
    "role_column_grants",
    "role_routine_grants",
    "role_table_grants",
    "role_udt_grants",
    "role_usage_grants",
    "routine_column_usage",
    "routine_privileges",
    "routine_routine_usage",
    "routine_sequence_usage",
    "routine_table_usage",
    "routines",
    "schemata",
    "sequences",
    "sql_features",
    "sql_implementation_info",
    "sql_sizing",
    "table_constraints",
    "table_privileges",
    "tables",
    "triggered_update_columns",
    "triggers",
    "udt_privileges",
    "usage_privileges",
    "user_defined_types",
    "user_mapping_options",
    "user_mappings",
    "view_column_usage",
    "view_routine_usage",
    "view_table_usage",
    "views",
)
CLAIMS = dict.fromkeys(observer_contract.CLAIMS, False)


class SanitizerPrivilegedGlobalQueryGrammarFailure(RuntimeError):
    """Bounded refusal without SQL, identity, role, or catalog content."""


def _fail(code: str) -> NoReturn:
    if code not in _FAILURE_CODES:
        code = "privileged_global_query_grammar.invalid"
    raise SanitizerPrivilegedGlobalQueryGrammarFailure(code) from None


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
        _fail("privileged_global_query_grammar.invalid")


def _parse_json(raw: bytes, *, expected_sha256: str) -> dict[str, Any]:
    if (
        type(raw) is not bytes
        or not 1 <= len(raw) <= MAXIMUM_CONTRACT_BYTES
        or not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), expected_sha256)
    ):
        _fail("privileged_global_query_grammar.invalid")

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for name, value in items:
            if name in result:
                _fail("privileged_global_query_grammar.invalid")
            result[name] = value
        return result

    try:
        document = json.loads(
            raw,
            object_pairs_hook=pairs,
            parse_constant=lambda _value: _fail(
                "privileged_global_query_grammar.invalid"
            ),
        )
    except SanitizerPrivilegedGlobalQueryGrammarFailure:
        raise
    except (UnicodeDecodeError, ValueError, RecursionError):
        _fail("privileged_global_query_grammar.invalid")
    if type(document) is not dict:
        _fail("privileged_global_query_grammar.invalid")
    return document


def _read_contract() -> tuple[dict[str, Any], bytes]:
    try:
        raw = CONTRACT_PATH.read_bytes()
    except OSError:
        _fail("privileged_global_query_grammar.invalid")
    return _parse_json(raw, expected_sha256=CONTRACT_SHA256), raw


def _parse_templates(raw: bytes) -> dict[str, str]:
    if (
        type(raw) is not bytes
        or not 1 <= len(raw) <= MAXIMUM_TEMPLATE_SET_BYTES
        or not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), TEMPLATE_SET_SHA256)
    ):
        _fail("privileged_global_query_grammar.invalid")
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError:
        _fail("privileged_global_query_grammar.invalid")
    if not text.startswith(_TEMPLATE_HEADER) or not text.endswith("\n"):
        _fail("privileged_global_query_grammar.invalid")
    parts = _FRAME_MARKER.split(text[len(_TEMPLATE_HEADER) :])
    if not parts or parts[0] != "" or len(parts) != 1 + 2 * len(FRAME_NAMES):
        _fail("privileged_global_query_grammar.invalid")

    templates: dict[str, str] = {}
    observed_functions: set[str] = set()
    for index in range(1, len(parts), 2):
        frame = parts[index]
        statement = parts[index + 1].strip()
        statement_without_literals = _STRING_LITERAL.sub("''", statement)
        if (
            frame in templates
            or not statement.startswith(("SELECT ", "WITH "))
            or not statement.endswith(";")
            or statement.count(";") != 1
            or len(statement.encode("ascii")) > MAXIMUM_TEMPLATE_BYTES
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
            _fail("privileged_global_query_grammar.invalid")
        observed_functions.update(
            f"pg_catalog.{name.lower()}"
            for name in _QUALIFIED_CALL.findall(statement_without_literals)
        )
        templates[frame] = statement

    expected_functions = {
        signature.split("(", 1)[0] for signature, _volatility in FUNCTION_ALLOWLIST
    }
    unqualified_calls = {
        call.upper()
        for call in _UNQUALIFIED_CALL.findall(
            _STRING_LITERAL.sub("''", "\n".join(templates.values()))
        )
    }
    all_sql = "\n".join(templates.values())
    password_sql = templates.get("password_null_state", "")
    if (
        tuple(templates) != FRAME_NAMES
        or observed_functions != expected_functions
        or not unqualified_calls
        <= {"AND", "AS", "COALESCE", "FROM", "IN", "OR", "WHERE"}
        or any(catalog not in all_sql for catalog in CATALOG_ALLOWLIST)
        or password_sql.count("rolpassword") != 1
        or "roles.rolpassword IS NULL" not in password_sql
        or "subconninfo" in all_sql
        or "activity.query" in all_sql
        or "client_addr" in all_sql
        or "client_hostname" in all_sql
        or "pgaadauth_list_principals" in all_sql
    ):
        _fail("privileged_global_query_grammar.invalid")
    return templates


def _read_templates() -> tuple[dict[str, str], bytes]:
    try:
        raw = TEMPLATE_PATH.read_bytes()
    except OSError:
        _fail("privileged_global_query_grammar.invalid")
    return _parse_templates(raw), raw


def _expected_frames() -> list[dict[str, object]]:
    return [
        {
            "frame": frame,
            "phase": "server_once"
            if frame in SERVER_ONCE_FRAME_NAMES
            else "per_database",
            "sha256": f"sha256:{TEMPLATE_SHA256[frame]}",
            "maximum_rows": MAXIMUM_ROWS[frame],
            "exact_columns": [dict(column) for column in FRAME_COLUMNS[frame]],
        }
        for frame in FRAME_NAMES
    ]


def _validate_semantics(document: Mapping[str, Any]) -> None:
    try:
        envelope = observer_contract.load_privileged_global_observer_contract()
        grammar = document["grammar"]
        status = document["status"]
    except (KeyError, TypeError):
        _fail("privileged_global_query_grammar.invalid")

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
    expected_baseline = {
        "postgresql_major_version": 16,
        "pg_init_privs_types": ["i", "e"],
        "information_schema_public_usage": True,
        "information_schema_public_select_relations": list(
            INFORMATION_SCHEMA_PUBLIC_SELECT_BASELINE
        ),
        "unknown_builtin_or_extension_acl_baseline_permitted": False,
    }
    if (
        observer_contract.CONTRACT_SHA256 != OBSERVER_CONTRACT_SHA256
        or envelope["policy_id"]
        != "cosmaneura-staging-sanitizer-privileged-global-observer-envelope-v1"
        or set(document)
        != {
            "schema_version",
            "policy_id",
            "closed_world",
            "upstream_policies",
            "input",
            "template_source",
            "requirement_coverage",
            "postgresql16_builtin_acl_baseline",
            "grammar",
            "resource_bounds",
            "execution_envelope",
            "output",
            "claims",
            "status",
        }
        or document.get("schema_version") != 1
        or document.get("policy_id")
        != "cosmaneura-staging-sanitizer-privileged-global-query-grammar-v1"
        or document.get("closed_world") is not True
        or document.get("upstream_policies")
        != {
            "privileged_global_observer_envelope": {
                "policy_id": envelope["policy_id"],
                "sha256": f"sha256:{OBSERVER_CONTRACT_SHA256}",
            }
        }
        or document.get("input")
        != {
            "only_future_public_input": "sealed_target_role_observation_plan",
            "caller_sql_identifiers_fragments_parameters_templates_order_catalogs_functions_bounds_output_schemas_or_bootstrap_authorities_permitted": False,
            "exact_process_local_template_set_required": True,
            "parameter_count": 0,
            "target_role_and_identity_values_derived_only_from_sealed_plan_ancestry": True,
        }
        or document.get("template_source")
        != {
            "path": "contracts/staging-rehearsal/v1/restore-sanitizer-privileged-global-query-templates.sql",
            "sha256": f"sha256:{TEMPLATE_SET_SHA256}",
            "maximum_bytes": MAXIMUM_TEMPLATE_SET_BYTES,
            "maximum_template_bytes": MAXIMUM_TEMPLATE_BYTES,
            "encoding": "ascii",
            "format": "named-server-once-and-per-database-select-templates-v1",
        }
        or document.get("requirement_coverage") != expected_coverage
        or [item["requirement"] for item in expected_coverage]
        != list(observer_contract.PRIVILEGED_GLOBAL_REQUIREMENTS)
        or document.get("postgresql16_builtin_acl_baseline") != expected_baseline
        or grammar.get("postgresql_major_version") != 16
        or grammar.get("statement_class") != "SELECT_OR_WITH_SELECT"
        or grammar.get("exact_template_count") != len(FRAME_NAMES)
        or grammar.get("server_once_exact_order") != list(SERVER_ONCE_FRAME_NAMES)
        or grammar.get("per_database_exact_order") != list(PER_DATABASE_FRAME_NAMES)
        or grammar.get("frames") != _expected_frames()
        or grammar.get("catalog_allowlist") != list(CATALOG_ALLOWLIST)
        or grammar.get("function_allowlist") != expected_functions
    ):
        _fail("privileged_global_query_grammar.invalid")

    fixed_flags = {
        "exact_template_bytes_only": True,
        "one_statement_per_execute": True,
        "server_once_templates_execute_exactly_once": True,
        "per_database_templates_execute_exactly_once_per_authenticated_database": True,
        "server_side_prepare_permitted": False,
        "caller_parameter_binding_permitted": False,
        "caller_identifier_interpolation_permitted": False,
        "application_schema_or_table_reads_permitted": False,
        "raw_password_or_password_verifier_selection_permitted": False,
        "password_null_predicate_only": True,
        "entra_principal_mapping_source": "pg_catalog.pg_shseclabel:pgaadauth",
        "vendor_principal_listing_function_execution_permitted": False,
        "unlisted_catalog_or_function_access_permitted": False,
        "unlisted_select_call_do_copy_ddl_dml_notify_large_object_prepared_transaction_advisory_lock_or_guc_operations_permitted": False,
    }
    if (
        any(grammar.get(name) != value for name, value in fixed_flags.items())
        or set(grammar)
        != {
            "postgresql_major_version",
            "statement_class",
            "exact_template_count",
            "server_once_exact_order",
            "per_database_exact_order",
            "frames",
            "catalog_allowlist",
            "function_allowlist",
            *fixed_flags,
        }
        or document.get("resource_bounds")
        != {
            "maximum_roles": 512,
            "maximum_database_entries": 64,
            "maximum_queries": 139,
            "maximum_template_bytes": MAXIMUM_TEMPLATE_BYTES,
            "maximum_rows_total": 200000,
            "maximum_row_bytes": 16384,
            "maximum_output_bytes": 67108864,
            "maximum_elapsed_seconds_per_query": 15,
            "maximum_elapsed_seconds_total": 300,
            "retry_permitted": False,
        }
        or document.get("execution_envelope")
        != {
            "connection_source": "privileged_global_observer_envelope.future_connection_sweep",
            "server_once_database_source": "future_selected_maintenance_database",
            "per_database_source": "future_authenticated_connectable_database_inventory",
            "disabled_database_strategy_remains_unselected": True,
            "read_only_repeatable_read_required": True,
            "cursor_description_must_match_exact_columns_before_first_row": True,
            "row_and_byte_bounds_must_be_enforced_while_streaming": True,
            "rollback_and_close_required_on_success_or_failure": True,
            "raw_rows_sql_tokens_credentials_connections_cursors_or_callbacks_escape_process_permitted": False,
        }
        or document.get("output")
        != {
            "frame_count": len(FRAME_NAMES),
            "server_once_exact_order": list(SERVER_ONCE_FRAME_NAMES),
            "per_database_exact_order": list(PER_DATABASE_FRAME_NAMES),
            "canonical_json_required": True,
            "duplicate_rows_permitted": False,
            "nullability_defined_by_exact_columns": True,
            "sealed_in_process_output_required": True,
            "raw_catalog_role_database_object_acl_or_identity_values_may_be_public": False,
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
            "entra_principal_mapping_source_selected": True,
            "frame_schemas_selected": True,
            "resource_bounds_selected": True,
            "bootstrap_authority_selected": False,
            "maintenance_database_selected": False,
            "disabled_database_surface_observation_strategy_selected": False,
            "connection_adapter_implemented": False,
            "observer_implemented": False,
            "offline_authenticator_implemented": False,
            "live_database_calls_enabled": False,
            "provider_calls_permitted": False,
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
                "bootstrap_authority_unselected",
                "maintenance_database_unselected",
                "disabled_database_surface_observation_strategy_unselected",
                "privileged_global_database_observer_unimplemented",
                "privileged_global_offline_authenticator_unimplemented",
                "target_role_and_privileged_global_join_unimplemented",
                "stability_authorities_unimplemented",
                "post_provision_schema_descriptor_unselected",
                "final_adapter_join_unimplemented",
            ],
        }
    ):
        _fail("privileged_global_query_grammar.invalid")


def validate_privileged_global_query_grammar_contract(
    contract: Any, *, executable: bool = False
) -> dict[str, Any]:
    """Validate exact grammar and templates; executable use remains unavailable."""

    try:
        if type(contract) is not dict or type(executable) is not bool:
            _fail("privileged_global_query_grammar.invalid")
        template, _raw = _read_contract()
        canonical = _canonical(contract)
        document = _parse_json(
            canonical,
            expected_sha256=hashlib.sha256(canonical).hexdigest(),
        )
        if not hmac.compare_digest(_canonical(document), _canonical(template)):
            _fail("privileged_global_query_grammar.invalid")
        _validate_semantics(document)
        _read_templates()
        if executable:
            _fail(ACTIVATION_FAILURE)
        return document
    except SanitizerPrivilegedGlobalQueryGrammarFailure:
        raise
    except Exception:
        _fail("privileged_global_query_grammar.invalid")


def load_privileged_global_query_grammar_contract(
    *, executable: bool = False
) -> dict[str, Any]:
    document, _raw = _read_contract()
    return validate_privileged_global_query_grammar_contract(
        document,
        executable=executable,
    )


if __name__ == "__main__":
    load_privileged_global_query_grammar_contract(executable="--executable" in sys.argv)
    print("sanitizer privileged/global query grammar contract valid (dormant)")
