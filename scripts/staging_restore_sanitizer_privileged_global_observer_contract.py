#!/usr/bin/env python3
"""Validate the dormant privileged/global database observer envelope.

This module selects no bootstrap identity or query, opens no connection, and
creates no observation authority.  It validates only the boundary that later
query-grammar, observer, and offline-authenticator slices must satisfy.
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
    / "contracts/staging-rehearsal/v1/restore-sanitizer-privileged-global-observer-contract.json"
)
CONTRACT_SHA256 = "040874bb93f30c17168a49284b9255be982f2530e1cf144325ad89ae174a63d0"
CAPABILITY_CONTRACT_SHA256 = (
    "40684569cbf34d80e144565db73b6f1e81505eb4509c9403797fc0a1114eb98d"
)
OBSERVATION_PLAN_CONTRACT_SHA256 = (
    "d341d653195b9b3d3d941304205f801d952e3195cd0eefafd0e9d6a9131a0dbb"
)
ACTIVATION_FAILURE = "activation.sanitizer_privileged_global_query_grammar_unselected"
MAXIMUM_CONTRACT_BYTES = 128 * 1024

PRIVILEGED_GLOBAL_REQUIREMENTS = (
    "exact-provision-job-uami-mapping",
    "null-password-state",
    "empty-ownership-sets",
    "no-database-or-shared-object-ownership-dependencies",
    "closed-world-every-pg-database-entry-and-datallowconn",
    "exact-direct-public-membership-and-effective-database-privileges",
    "target-only-effective-connect-and-no-create-or-temporary-anywhere",
    "empty-tablespace-create-privileges",
    "empty-parameter-set-and-alter-system-privileges",
    "empty-publication-subscription-extension-and-event-trigger-authority",
    "system-object-explicit-and-public-acl-deltas-absent",
)
LOGICAL_SURFACES = (
    "entra_principal_mapping",
    "password_null_state",
    "role_owned_dependencies",
    "database_inventory",
    "database_acl_sources_and_effective_privileges",
    "runtime_role_sessions_by_database",
    "tablespace_acl_sources_and_effective_privileges",
    "parameter_acl_sources_and_effective_privileges",
    "publication_subscription_extension_and_event_trigger_authority",
    "system_object_acl_deltas",
)
CLAIMS = dict.fromkeys(
    (
        "bootstrap_authority_authenticated",
        "database_connection_authority",
        "database_mutation_authority",
        "database_role_state_authenticated",
        "effective_session_settings_authenticated",
        "evidence_publication_authority",
        "executable",
        "live_database_calls_enabled",
        "password_authentication_absence_proven",
        "post_provision_schema_descriptor_authenticated",
        "privileged_global_observation_authenticated",
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
    {ACTIVATION_FAILURE, "privileged_global_observer_contract.invalid"}
)


class SanitizerPrivilegedGlobalObserverContractFailure(RuntimeError):
    """Bounded refusal without target, identity, or catalog content."""


def _fail(code: str) -> NoReturn:
    if code not in _FAILURE_CODES:
        code = "privileged_global_observer_contract.invalid"
    raise SanitizerPrivilegedGlobalObserverContractFailure(code) from None


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
        _fail("privileged_global_observer_contract.invalid")


def _parse(raw: bytes, *, expected_sha256: str) -> dict[str, Any]:
    if (
        type(raw) is not bytes
        or not 1 <= len(raw) <= MAXIMUM_CONTRACT_BYTES
        or not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), expected_sha256)
    ):
        _fail("privileged_global_observer_contract.invalid")

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for name, value in items:
            if name in result:
                _fail("privileged_global_observer_contract.invalid")
            result[name] = value
        return result

    try:
        document = json.loads(
            raw,
            object_pairs_hook=pairs,
            parse_constant=lambda _value: _fail(
                "privileged_global_observer_contract.invalid"
            ),
        )
    except SanitizerPrivilegedGlobalObserverContractFailure:
        raise
    except (UnicodeDecodeError, ValueError, RecursionError):
        _fail("privileged_global_observer_contract.invalid")
    if type(document) is not dict:
        _fail("privileged_global_observer_contract.invalid")
    return document


def _read_contract() -> tuple[dict[str, Any], bytes]:
    try:
        raw = CONTRACT_PATH.read_bytes()
    except OSError:
        _fail("privileged_global_observer_contract.invalid")
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
        input_contract = document["input"]
        scope = document["scope"]
        bootstrap = document["future_bootstrap_authority"]
        sweep = document["future_connection_sweep"]
        grammar = document["query_grammar"]
        frames = document["observation_frames"]
        status = document["status"]
    except (KeyError, TypeError):
        _fail("privileged_global_observer_contract.invalid")

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
            "future_bootstrap_authority",
            "future_connection_sweep",
            "query_grammar",
            "observation_frames",
            "claims",
            "status",
        }
        or document.get("schema_version") != 1
        or document.get("policy_id")
        != "cosmaneura-staging-sanitizer-privileged-global-observer-envelope-v1"
        or document.get("closed_world") is not True
        or document.get("upstream_policies") != _expected_upstream()
        or input_contract.get("only_future_public_input")
        != "sealed_target_role_observation_plan"
        or input_contract.get("required_kind") != observation_plan.PLAN_KIND
        or input_contract.get("exact_type_seal_and_process_local_identity_required")
        is not True
        or input_contract.get("bootstrap_authority_caller_input_permitted") is not False
        or scope.get("source")
        != "target_role_observation_plan.requirement_partition.privileged_global_observer"
        or scope.get("requirements") != list(PRIVILEGED_GLOBAL_REQUIREMENTS)
        or plan_contract["requirement_partition"]["privileged_global_observer"]
        != list(PRIVILEGED_GLOBAL_REQUIREMENTS)
        or len(PRIVILEGED_GLOBAL_REQUIREMENTS)
        != len(set(PRIVILEGED_GLOBAL_REQUIREMENTS))
        or scope.get("postgresql_major_version") != 16
        or scope.get("target_role_observer_requirements_permitted") is not False
        or scope.get("external_stability_authority_claimed") is not False
        or scope.get("final_database_role_state_authentication_claimed") is not False
        or bootstrap.get("selection_implemented") is not False
        or bootstrap.get("caller_substitution_permitted") is not False
        or bootstrap.get("must_be_distinct_from_provision_runtime_identity") is not True
        or bootstrap.get("runtime_identity_may_bootstrap") is not False
        or bootstrap.get("role_or_privilege_mutation_permitted_by_observer")
        is not False
        or bootstrap.get("provider_calls_permitted_by_observer") is not False
        or bootstrap.get("password_or_connection_string_permitted") is not False
        or bootstrap.get("credential_or_token_retention_permitted") is not False
        or sweep.get("implementation_selected") is not False
        or sweep.get("maintenance_database_selected") is not False
        or sweep.get("disabled_database_surface_observation_strategy_selected")
        is not False
        or sweep.get("maximum_concurrent_connections") != 1
        or sweep.get("pooling_permitted") is not False
        or sweep.get("retry_permitted") is not False
        or sweep.get("each_transaction_read_only") is not True
        or sweep.get("commit_permitted") is not False
        or sweep.get("external_stability_authority_must_be_held_by_final_adapter")
        is not True
        or sweep.get("observer_may_acquire_or_claim_external_stability_authority")
        is not False
        or grammar.get("positive_allowlist_required") is not True
        or grammar.get("statement_class") != "SELECT"
        or grammar.get("query_templates_selected") is not False
        or grammar.get("catalog_allowlist_selected") is not False
        or grammar.get("function_allowlist_selected") is not False
        or grammar.get("entra_principal_mapping_source_selected") is not False
        or grammar.get(
            "target_role_and_provision_uami_client_and_principal_ids_must_come_from_"
            "sealed_plan_ancestry"
        )
        is not True
        or grammar.get("application_schema_or_table_reads_permitted") is not False
        or grammar.get("raw_password_or_password_verifier_selection_permitted")
        is not False
        or grammar.get("password_null_predicate_only") is not True
        or frames.get("logical_surfaces") != list(LOGICAL_SURFACES)
        or frames.get("frame_names_and_schemas_selected") is not False
        or frames.get("raw_application_values_permitted") is not False
        or frames.get("raw_password_or_password_verifier_permitted") is not False
        or frames.get("disk_log_artifact_or_evidence_publication_permitted")
        is not False
        or document.get("claims") != CLAIMS
        or status.get("contract_validator_implemented") is not True
        or status.get("bootstrap_authority_selected") is not False
        or status.get("connection_sweep_implemented") is not False
        or status.get("query_templates_selected") is not False
        or status.get("frame_schemas_selected") is not False
        or status.get("observation_authority_implemented") is not False
        or status.get("offline_authenticator_implemented") is not False
        or status.get("activation_ready") is not False
        or status.get("public_activation_failure") != ACTIVATION_FAILURE
    ):
        _fail("privileged_global_observer_contract.invalid")


def validate_privileged_global_observer_contract(
    contract: Any, *, executable: bool = False
) -> dict[str, Any]:
    """Validate the exact envelope; executable use remains unavailable."""

    try:
        if type(contract) is not dict or type(executable) is not bool:
            _fail("privileged_global_observer_contract.invalid")
        template, _raw = _read_contract()
        canonical = _canonical(contract)
        document = _parse(
            canonical,
            expected_sha256=hashlib.sha256(canonical).hexdigest(),
        )
        if not hmac.compare_digest(_canonical(document), _canonical(template)):
            _fail("privileged_global_observer_contract.invalid")
        _validate_semantics(document)
        if executable:
            _fail(ACTIVATION_FAILURE)
        return document
    except SanitizerPrivilegedGlobalObserverContractFailure:
        raise
    except Exception:
        _fail("privileged_global_observer_contract.invalid")


def load_privileged_global_observer_contract(
    *, executable: bool = False
) -> dict[str, Any]:
    document, _raw = _read_contract()
    return validate_privileged_global_observer_contract(
        document,
        executable=executable,
    )


if __name__ == "__main__":
    load_privileged_global_observer_contract(executable="--executable" in sys.argv)
    print("sanitizer privileged/global observer envelope contract valid (dormant)")
