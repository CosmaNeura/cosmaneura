#!/usr/bin/env python3
"""Validate the dormant target-role database observer envelope contract.

This module selects no query, opens no connection, obtains no credential, and
creates no observation authority.  It only validates the closed-world boundary
that later query-grammar and adapter slices must satisfy.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any, NoReturn

try:
    from scripts import staging_restore_sanitizer_database_capability as capability
    from scripts import (
        staging_restore_sanitizer_target_role_observation_plan as observation_plan,
    )
except ModuleNotFoundError:  # Direct `python scripts/...` compatibility.
    import staging_restore_sanitizer_database_capability as capability
    import staging_restore_sanitizer_target_role_observation_plan as observation_plan


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = (
    ROOT
    / "contracts/staging-rehearsal/v1/restore-sanitizer-target-role-observer-contract.json"
)
CONTRACT_SHA256 = "acf34a80c63b0b103147ccd574195c11ebde16357a24bccf935030bca0f80fad"
CAPABILITY_CONTRACT_SHA256 = (
    "40684569cbf34d80e144565db73b6f1e81505eb4509c9403797fc0a1114eb98d"
)
OBSERVATION_PLAN_CONTRACT_SHA256 = (
    "d341d653195b9b3d3d941304205f801d952e3195cd0eefafd0e9d6a9131a0dbb"
)
ACTIVATION_FAILURE = "activation.sanitizer_target_role_query_grammar_unselected"
MAXIMUM_CONTRACT_BYTES = 64 * 1024

TARGET_REQUIREMENTS = (
    "exact-database-role-name",
    "role-flags-and-connection-limit",
    "empty-outbound-memberships",
    "empty-inbound-members",
    "set-role-escalation-unavailable",
    "exact-direct-and-effective-schema-privileges",
    "exact-direct-and-effective-table-privileges",
    "empty-explicit-column-acls",
    "column-effective-privileges-derived-only-from-exact-table-privileges",
    "empty-sequence-privileges",
    "empty-public-schema-routine-execute-privileges",
    "exact-fourteen-acl-manageable-custom-enum-types",
    "empty-acl-and-effective-usage-for-acl-manageable-custom-enums",
    "postgresql16-intrinsic-relation-row-and-row-array-usage-baseline",
    "postgresql16-enum-array-usage-derived-from-element-enum-acl",
    "empty-language-usage-privileges",
    "empty-foreign-data-wrapper-and-server-usage-privileges",
    "empty-user-mappings",
    "all-pg-db-role-setting-source-rows-including-setrole-zero",
    "session-replication-role-origin",
    "all-grant-options-absent",
    "public-role-acl-sources-included-for-every-object-class",
)
FRAME_NAMES = (
    "session_and_role",
    "memberships",
    "role_settings",
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
        "password_authentication_absence_proven",
        "post_provision_schema_descriptor_authenticated",
        "provider_calls_permitted",
        "provider_facts_authenticated",
        "provision_job_identity_bound",
        "role_or_privilege_mutation_authority",
        "runtime_authority",
        "sanitizer_authority",
        "server_wide_authorization_stability_authenticated",
        "target_role_observation_authenticated",
        "transaction_committed",
        "transaction_executed",
        "workflow_integrated",
        "write_path_effects_authenticated",
    ),
    False,
)
_FAILURE_CODES = frozenset(
    {ACTIVATION_FAILURE, "target_role_observer_contract.invalid"}
)


class SanitizerTargetRoleObserverContractFailure(RuntimeError):
    """Bounded refusal with no target, identity, or catalog content."""


def _fail(code: str) -> NoReturn:
    if code not in _FAILURE_CODES:
        code = "target_role_observer_contract.invalid"
    raise SanitizerTargetRoleObserverContractFailure(code) from None


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
        _fail("target_role_observer_contract.invalid")


def _parse(raw: bytes, *, expected_sha256: str) -> dict[str, Any]:
    if (
        type(raw) is not bytes
        or not 1 <= len(raw) <= MAXIMUM_CONTRACT_BYTES
        or not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), expected_sha256)
    ):
        _fail("target_role_observer_contract.invalid")

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for name, value in items:
            if name in result:
                _fail("target_role_observer_contract.invalid")
            result[name] = value
        return result

    try:
        document = json.loads(
            raw,
            object_pairs_hook=pairs,
            parse_constant=lambda _value: _fail(
                "target_role_observer_contract.invalid"
            ),
        )
    except SanitizerTargetRoleObserverContractFailure:
        raise
    except (UnicodeDecodeError, ValueError, RecursionError):
        _fail("target_role_observer_contract.invalid")
    if type(document) is not dict:
        _fail("target_role_observer_contract.invalid")
    return document


def _read_contract() -> tuple[dict[str, Any], bytes]:
    try:
        raw = CONTRACT_PATH.read_bytes()
    except OSError:
        _fail("target_role_observer_contract.invalid")
    return _parse(raw, expected_sha256=CONTRACT_SHA256), raw


def _expected_upstream() -> dict[str, Any]:
    capability_contract = capability.load_database_capability_contract()
    plan_contract = observation_plan.load_target_role_observation_plan_contract()
    return {
        "database_capability": {
            "policy_id": capability_contract["policy_id"],
            "sha256": f"sha256:{CAPABILITY_CONTRACT_SHA256}",
        },
        "target_role_observation_plan": {
            "policy_id": plan_contract["policy_id"],
            "sha256": f"sha256:{OBSERVATION_PLAN_CONTRACT_SHA256}",
        },
    }


def _validate_semantics(document: Mapping[str, Any]) -> None:
    try:
        plan_contract = observation_plan.load_target_role_observation_plan_contract()
        expected_requirements = plan_contract["requirement_partition"][
            "target_role_observer"
        ]
        upstream = document["upstream_policies"]
        input_contract = document["input"]
        scope = document["scope"]
        connection = document["future_connection"]
        transaction = document["future_transaction"]
        grammar = document["query_grammar"]
        frames = document["observation_frames"]
        claims = document["claims"]
        status = document["status"]
    except (KeyError, TypeError):
        _fail("target_role_observer_contract.invalid")

    if (
        capability.CONTRACT_SHA256 != CAPABILITY_CONTRACT_SHA256
        or observation_plan.CONTRACT_SHA256 != OBSERVATION_PLAN_CONTRACT_SHA256
        or set(document)
        != {
            "schema_version",
            "policy_id",
            "closed_world",
            "upstream_policies",
            "input",
            "scope",
            "future_connection",
            "future_transaction",
            "query_grammar",
            "observation_frames",
            "claims",
            "status",
        }
        or document.get("schema_version") != 1
        or document.get("policy_id")
        != "cosmaneura-staging-sanitizer-target-role-database-observer-envelope-v1"
        or document.get("closed_world") is not True
        or upstream != _expected_upstream()
        or input_contract
        != {
            "only_future_public_input": "sealed_target_role_observation_plan",
            "required_kind": observation_plan.PLAN_KIND,
            "exact_type_seal_and_process_local_identity_required": True,
            "current_time_reauthentication_required_before_every_future_stage": True,
            "caller_bytes_maps_paths_urls_dsns_hosts_hostaddrs_ports_roles_identity_ids_"
            "tokens_credentials_connectors_callbacks_sql_policies_timestamps_or_"
            "runtime_overrides_permitted": False,
        }
        or scope
        != {
            "source": (
                "target_role_observation_plan.requirement_partition."
                "target_role_observer"
            ),
            "requirements": list(TARGET_REQUIREMENTS),
            "privileged_or_server_global_requirements_permitted": False,
            "post_provision_descriptor_selection_permitted": False,
            "final_database_role_state_authentication_claimed": False,
        }
        or expected_requirements != list(TARGET_REQUIREMENTS)
        or len(TARGET_REQUIREMENTS) != len(set(TARGET_REQUIREMENTS))
        or connection
        != {
            "implementation_selected": False,
            "source": "sealed_plan_retained_authenticated_ancestry_only",
            "target_fqdn_database_role_database_name_hostaddr_and_provision_uami_"
            "client_id_must_be_derived_without_caller_override": True,
            "managed_identity_only": True,
            "default_or_ambient_credential_chain_permitted": False,
            "password_or_connection_string_permitted": False,
            "maximum_connections": 1,
            "pooling_permitted": False,
            "retry_permitted": False,
            "maximum_token_requests": 1,
            "token_retention_permitted": False,
            "socket_peer_exact_match_required": True,
            "tls_mode": "verify-full",
            "minimum_tls_version": "TLSv1.2",
            "maximum_tls_version": "TLSv1.3",
            "ambient_libpq_environment_permitted": False,
        }
        or transaction
        != {
            "implementation_selected": False,
            "autocommit": False,
            "read_only": True,
            "isolation": "REPEATABLE READ",
            "commit_permitted": False,
            "always_rollback": True,
            "always_close": True,
            "application_data_reads_permitted": False,
            "role_or_privilege_mutation_permitted": False,
            "ddl_or_dml_permitted": False,
            "advisory_lock_or_external_fence_claimed": False,
        }
        or grammar
        != {
            "positive_allowlist_required": True,
            "statement_class": "SELECT",
            "query_templates_selected": False,
            "exact_template_sha256_values_selected": False,
            "caller_sql_or_identifier_interpolation_permitted": False,
            "multiple_statements_per_execute_permitted": False,
            "functions_may_be_called_only_from_an_exact_positive_allowlist": True,
            "catalogs_may_be_read_only_from_an_exact_positive_allowlist": True,
            "volatile_or_side_effectful_functions_permitted": False,
            "unlisted_select_call_do_copy_ddl_dml_notify_large_object_prepared_"
            "transaction_advisory_lock_or_guc_operations_permitted": False,
            "exact_order_selected": False,
            "maximum_queries_selected": False,
            "maximum_rows_per_surface_selected": False,
            "maximum_row_bytes_selected": False,
            "maximum_total_rows_selected": False,
            "maximum_total_bytes_selected": False,
            "maximum_elapsed_seconds_selected": False,
        }
        or frames
        != {
            "closed_world": True,
            "unknown_frames_or_fields_permitted": False,
            "canonical_json_required": True,
            "exact_row_order_required": True,
            "duplicate_rows_permitted": False,
            "null_and_empty_semantics_must_be_explicit": True,
            "frames": list(FRAME_NAMES),
            "frame_schemas_selected": False,
            "raw_application_values_permitted": False,
            "raw_password_or_password_verifier_permitted": False,
            "credential_token_dsn_connection_cursor_connector_callback_or_sql_"
            "retention_permitted": False,
            "disk_log_artifact_or_evidence_publication_permitted": False,
            "sealed_in_process_output_required": True,
            "offline_authenticator_must_consume_the_identical_sealed_output": True,
        }
        or claims != CLAIMS
        or status
        != {
            "contract_validator_implemented": True,
            "connection_adapter_implemented": False,
            "query_templates_selected": False,
            "frame_schemas_selected": False,
            "observation_authority_implemented": False,
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
                "query_templates_unselected",
                "query_template_digests_unselected",
                "catalog_and_function_allowlists_unselected",
                "observation_frame_schemas_unselected",
                "resource_bounds_unselected",
                "target_role_database_observer_unimplemented",
                "offline_authenticator_unimplemented",
                "privileged_global_observer_unimplemented",
                "stability_authorities_unimplemented",
                "post_provision_schema_descriptor_unselected",
                "adapter_join_unimplemented",
            ],
        }
    ):
        _fail("target_role_observer_contract.invalid")


def validate_target_role_observer_contract(
    contract: Any, *, executable: bool = False
) -> dict[str, Any]:
    """Validate the exact contract; executable use remains unavailable."""

    try:
        if type(contract) is not dict or type(executable) is not bool:
            _fail("target_role_observer_contract.invalid")
        template, _raw = _read_contract()
        canonical = _canonical(contract)
        document = _parse(
            canonical,
            expected_sha256=hashlib.sha256(canonical).hexdigest(),
        )
        if not hmac.compare_digest(_canonical(document), _canonical(template)):
            _fail("target_role_observer_contract.invalid")
        _validate_semantics(document)
        if executable:
            _fail(ACTIVATION_FAILURE)
        return document
    except SanitizerTargetRoleObserverContractFailure:
        raise
    except Exception:
        _fail("target_role_observer_contract.invalid")


def load_target_role_observer_contract(*, executable: bool = False) -> dict[str, Any]:
    document, _raw = _read_contract()
    return validate_target_role_observer_contract(
        document,
        executable=executable,
    )


if __name__ == "__main__":
    load_target_role_observer_contract(executable="--executable" in sys.argv)
    print("sanitizer target-role observer envelope contract valid (dormant)")
