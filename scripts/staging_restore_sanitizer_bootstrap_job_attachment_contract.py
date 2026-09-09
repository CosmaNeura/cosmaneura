#!/usr/bin/env python3
"""Validate the dormant single-use sanitizer bootstrap-job contract.

This module performs no network, provider, credential, job, database, or
workflow operation. It only pins the closed-world boundary a later private
executor and attachment authenticator must satisfy.
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
    from scripts import staging_restore_sanitizer_bootstrap_binding_plan as binding
    from scripts import validate_staging_restore_runtime_binding as runtime_binding
except ModuleNotFoundError:  # Direct `python scripts/...` compatibility.
    import staging_restore_sanitizer_bootstrap_binding_plan as binding
    import validate_staging_restore_runtime_binding as runtime_binding


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = (
    ROOT / "contracts/staging-rehearsal/v1/"
    "restore-sanitizer-bootstrap-job-attachment-contract.json"
)
CONTRACT_SHA256 = "f9255a4244ddea01f1d5b1b9cd2455ecf5d2e2e8a2f953be6c9aef8709f59760"
BOOTSTRAP_BINDING_PLAN_CONTRACT_SHA256 = (
    "2e670cfc42a64c1f33e21791eb4795832d4c3be39141c7cde4186cd7c90739c2"
)
RESTORE_RUNTIME_BINDING_POLICY_SHA256 = (
    "8b5854323f2e783674f510ee63c0d030bb51d2ec35ff85c511b88514f9b1c8f3"
)
ACTIVATION_FAILURE = "activation.sanitizer_bootstrap_job_attachment_unavailable"
MAXIMUM_CONTRACT_BYTES = 64 * 1024
MAXIMUM_CONTRACT_DEPTH = 16
MAXIMUM_CONTRACT_NODES = 4096
MAXIMUM_CONTRACT_SCALAR_CHARACTERS = 8192
MAXIMUM_CONTRACT_INTEGER_BITS = 64
JOB_NAME = "cosmaneura-stg-restore-bootstrap"
IDENTITY_NAME = "cosmaneura-staging-restore-bootstrap-job"

REQUIRED_STAGE_ORDER = (
    "acquire_external_stability_authority",
    "observe_initial_bootstrap_job_identity_and_acrpull_assignment_absence",
    "create_bootstrap_identity_with_create_only_semantics",
    "observe_and_authenticate_bootstrap_identity_with_zero_effective_authority",
    "create_private_registry_acrpull_role_assignment_with_create_only_semantics",
    "observe_exact_acrpull_and_exhaustive_effective_authorization_inventory",
    "observe_private_registry_and_network_path",
    "create_bootstrap_job_with_create_only_semantics",
    "observe_job_configuration_identity_and_registry_attachment",
    "observe_pre_start_execution_history_empty",
    "start_bootstrap_job_once_without_overrides",
    "observe_provider_execution_name",
    "poll_to_terminal_or_stop_once_on_bounded_timeout",
    "observe_terminal_execution_and_replica_result",
    "reobserve_and_authenticate_job_configuration_image_identity_registry_and_pull_attachment",
    "close_database_session",
    "remove_bootstrap_database_administrator",
    "observe_bootstrap_database_administrator_absence",
    "delete_bootstrap_job",
    "observe_bootstrap_job_and_identity_attachment_absence",
    "delete_private_registry_acrpull_role_assignment",
    "observe_private_registry_acrpull_role_assignment_absence",
    "delete_bootstrap_identity",
    "observe_bootstrap_identity_and_effective_authorization_absence",
    "release_external_stability_authority",
)
REQUIRED_INITIAL_ABSENCE_OBSERVATIONS = (
    "bootstrap_job",
    "bootstrap_identity",
    "private_registry_acrpull_role_assignment",
)
REQUIRED_PRE_START_PROVIDER_OBSERVATIONS = (
    "container_apps_environment_configuration",
    "infrastructure_subnet_configuration",
    "infrastructure_subnet_route_table_configuration",
    "infrastructure_subnet_network_security_group_configuration",
    "private_registry_configuration",
    "private_registry_private_endpoint_configuration",
    "private_registry_private_dns_binding",
    "target_database_configuration",
    "target_database_private_endpoint_configuration",
    "target_database_private_dns_binding",
    "bootstrap_identity_resource",
    "exhaustive_direct_and_inherited_role_assignment_inventory",
    "bootstrap_job_configuration",
    "bootstrap_job_identity_and_registry_attachment",
    "bootstrap_job_execution_history",
)
REQUIRED_FINAL_ABSENCE_OBSERVATIONS = (
    "bootstrap_database_administrator",
    "bootstrap_job",
    "bootstrap_job_identity_attachment",
    "private_registry_acrpull_role_assignment",
    "bootstrap_identity",
    "bootstrap_identity_effective_authorization_inventory",
)

CLAIMS = dict.fromkeys(
    (
        "bootstrap_binding_plan_authenticated",
        "bootstrap_identity_current_live_state_authenticated",
        "bootstrap_identity_bound_to_single_use_job",
        "bootstrap_job_configuration_authenticated",
        "bootstrap_job_execution_authenticated",
        "bootstrap_authority_authenticated",
        "database_connection_authority",
        "database_mutation_authority",
        "provider_calls_permitted",
        "provider_mutation_authority",
        "runtime_authority",
        "sanitizer_authority",
        "server_wide_authorization_stability_authenticated",
        "workflow_integrated",
        "executable",
    ),
    False,
)
ACTIVATION_BLOCKERS = (
    "private_executor_environment_unimplemented",
    "bootstrap_runtime_image_unpublished",
    "fresh_bootstrap_identity_uncreated",
    "bootstrap_job_resource_unimplemented",
    "bootstrap_job_configuration_observer_unimplemented",
    "bootstrap_job_attachment_authenticator_unimplemented",
    "bootstrap_control_plane_adapter_unimplemented",
    "external_stability_authority_unimplemented",
    "workflow_integration_unimplemented",
)
_FAILURE_CODES = frozenset(
    {ACTIVATION_FAILURE, "bootstrap_job_attachment_contract.invalid"}
)


class SanitizerBootstrapJobAttachmentContractFailure(RuntimeError):
    """Bounded refusal with no identity, runtime, or provider content."""


def _fail(code: str) -> NoReturn:
    if code not in _FAILURE_CODES:
        code = "bootstrap_job_attachment_contract.invalid"
    raise SanitizerBootstrapJobAttachmentContractFailure(code) from None


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
        _fail("bootstrap_job_attachment_contract.invalid")


def _validate_exact_json_tree(value: Any) -> None:
    stack: list[tuple[Any, int]] = [(value, 0)]
    seen_containers = (
        {id(value)} if type(value) is dict or type(value) is list else set()
    )
    discovered_nodes = 1
    scalar_characters = 0

    while stack:
        current, depth = stack.pop()
        if depth > MAXIMUM_CONTRACT_DEPTH:
            _fail("bootstrap_job_attachment_contract.invalid")

        if type(current) is dict:
            if current and depth >= MAXIMUM_CONTRACT_DEPTH:
                _fail("bootstrap_job_attachment_contract.invalid")
            additional_nodes = len(current) * 2
            if discovered_nodes + additional_nodes > MAXIMUM_CONTRACT_NODES:
                _fail("bootstrap_job_attachment_contract.invalid")
            discovered_nodes += additional_nodes
            for name, nested in current.items():
                if type(name) is not str:
                    _fail("bootstrap_job_attachment_contract.invalid")
                scalar_characters += len(name)
                if (
                    len(name) > MAXIMUM_CONTRACT_SCALAR_CHARACTERS
                    or scalar_characters > MAXIMUM_CONTRACT_BYTES
                ):
                    _fail("bootstrap_job_attachment_contract.invalid")
                if type(nested) is dict or type(nested) is list:
                    identity = id(nested)
                    if identity in seen_containers:
                        _fail("bootstrap_job_attachment_contract.invalid")
                    seen_containers.add(identity)
                stack.append((nested, depth + 1))
        elif type(current) is list:
            if current and depth >= MAXIMUM_CONTRACT_DEPTH:
                _fail("bootstrap_job_attachment_contract.invalid")
            if discovered_nodes + len(current) > MAXIMUM_CONTRACT_NODES:
                _fail("bootstrap_job_attachment_contract.invalid")
            discovered_nodes += len(current)
            for nested in current:
                if type(nested) is dict or type(nested) is list:
                    identity = id(nested)
                    if identity in seen_containers:
                        _fail("bootstrap_job_attachment_contract.invalid")
                    seen_containers.add(identity)
                stack.append((nested, depth + 1))
        elif type(current) is str:
            scalar_characters += len(current)
            if (
                len(current) > MAXIMUM_CONTRACT_SCALAR_CHARACTERS
                or scalar_characters > MAXIMUM_CONTRACT_BYTES
            ):
                _fail("bootstrap_job_attachment_contract.invalid")
        elif type(current) is int:
            if current.bit_length() > MAXIMUM_CONTRACT_INTEGER_BITS:
                _fail("bootstrap_job_attachment_contract.invalid")
        elif type(current) is bool or current is None:
            continue
        else:
            _fail("bootstrap_job_attachment_contract.invalid")


def _parse(raw: bytes, *, expected_sha256: str) -> dict[str, Any]:
    if (
        type(raw) is not bytes
        or not 1 <= len(raw) <= MAXIMUM_CONTRACT_BYTES
        or not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), expected_sha256)
    ):
        _fail("bootstrap_job_attachment_contract.invalid")

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for name, value in items:
            if name in result:
                _fail("bootstrap_job_attachment_contract.invalid")
            result[name] = value
        return result

    def integer(value: str) -> int:
        if len(value) > 20:
            _fail("bootstrap_job_attachment_contract.invalid")
        result = int(value)
        if result.bit_length() > MAXIMUM_CONTRACT_INTEGER_BITS:
            _fail("bootstrap_job_attachment_contract.invalid")
        return result

    try:
        document = json.loads(
            raw,
            object_pairs_hook=pairs,
            parse_constant=lambda _value: _fail(
                "bootstrap_job_attachment_contract.invalid"
            ),
            parse_float=lambda _value: _fail(
                "bootstrap_job_attachment_contract.invalid"
            ),
            parse_int=integer,
        )
    except SanitizerBootstrapJobAttachmentContractFailure:
        raise
    except (UnicodeDecodeError, ValueError, RecursionError):
        _fail("bootstrap_job_attachment_contract.invalid")
    if type(document) is not dict:
        _fail("bootstrap_job_attachment_contract.invalid")
    _validate_exact_json_tree(document)
    return document


def _read_contract() -> tuple[dict[str, Any], bytes]:
    try:
        with CONTRACT_PATH.open("rb") as handle:
            raw = handle.read(MAXIMUM_CONTRACT_BYTES + 1)
    except OSError:
        _fail("bootstrap_job_attachment_contract.invalid")
    return _parse(raw, expected_sha256=CONTRACT_SHA256), raw


def _upstream_policies() -> dict[str, Any]:
    try:
        bootstrap = binding.load_bootstrap_binding_plan_contract()
        runtime = runtime_binding.validate_runtime_binding_policy(
            runtime_binding._canonical_template()  # noqa: SLF001
        )
    except Exception:
        _fail("bootstrap_job_attachment_contract.invalid")
    return {
        "bootstrap_binding_plan": {
            "policy_id": bootstrap["policy_id"],
            "sha256": f"sha256:{BOOTSTRAP_BINDING_PLAN_CONTRACT_SHA256}",
        },
        "restore_runtime_binding": {
            "policy_id": runtime["policy_id"],
            "sha256": f"sha256:{RESTORE_RUNTIME_BINDING_POLICY_SHA256}",
        },
    }


def _validate_semantics(document: Mapping[str, Any]) -> None:
    try:
        job = document["job_topology"]
        identity = document["identity_attachment"]
        runtime = document["runtime"]
        network = document["network"]
        control = document["future_control_plane"]
        evidence = document["evidence"]
        status = document["status"]
    except (KeyError, TypeError):
        _fail("bootstrap_job_attachment_contract.invalid")

    if (
        binding.CONTRACT_SHA256 != BOOTSTRAP_BINDING_PLAN_CONTRACT_SHA256
        or runtime_binding.CANONICAL_POLICY_SHA256
        != RESTORE_RUNTIME_BINDING_POLICY_SHA256
        or set(document)
        != {
            "schema_version",
            "policy_id",
            "closed_world",
            "upstream_policies",
            "input",
            "job_topology",
            "identity_attachment",
            "runtime",
            "network",
            "future_control_plane",
            "evidence",
            "claims",
            "status",
        }
        or document.get("schema_version") != 1
        or document.get("policy_id")
        != "cosmaneura-staging-sanitizer-bootstrap-job-attachment-envelope-v1"
        or document.get("closed_world") is not True
        or document.get("upstream_policies") != _upstream_policies()
        or document.get("input")
        != {
            "only_future_public_input": "sealed_sanitizer_bootstrap_binding_plan",
            "required_kind": binding.PLAN_KIND,
            "exact_type_seal_process_local_identity_and_retained_ancestry_required": True,
            "current_time_reauthentication_required_before_and_after_every_future_stage": True,
            "caller_bytes_maps_paths_urls_images_commands_environment_identity_ids_tokens_credentials_provider_clients_callbacks_policies_timestamps_or_runtime_overrides_permitted": False,
        }
        or document.get("claims") != CLAIMS
    ):
        _fail("bootstrap_job_attachment_contract.invalid")

    if job != {
        "implementation_selected": False,
        "resource_type": "Microsoft.App/jobs",
        "name": JOB_NAME,
        "resource_group": "cosmaneura-staging-rehearsal-control",
        "location": "eastus",
        "environment_source": "future_authenticated_restore_runtime_binding.runtime.container_apps_environment",
        "same_private_environment_as_provision_and_cleanup_required": True,
        "persistent_job_permitted": False,
        "one_job_per_rehearsal_required": True,
        "manual_trigger_only": True,
        "replica_retry_limit": 0,
        "replica_timeout_seconds": 900,
        "parallelism": 1,
        "replica_completion_count": 1,
        "minimum_executions": 0,
        "maximum_executions": 1,
        "start_overrides_permitted": False,
        "revision_or_traffic_surface_permitted": False,
        "ingress_permitted": False,
        "dapr_permitted": False,
    }:
        _fail("bootstrap_job_attachment_contract.invalid")

    if identity != {
        "implementation_selected": False,
        "source": "bootstrap_binding_plan.retained_authenticated_bootstrap_identity_evidence.private_identity",
        "required_identity_name": IDENTITY_NAME,
        "identity_type": "UserAssigned",
        "exactly_one_user_assigned_identity_required": True,
        "system_assigned_identity_permitted": False,
        "additional_user_assigned_identities_permitted": False,
        "federated_credentials_permitted": False,
        "private_registry_pull_role_definition_id": "/providers/Microsoft.Authorization/roleDefinitions/7f951dda-4ed3-4680-a7ca-43fe172d538d",
        "private_registry_pull_role_assignment_required": True,
        "private_registry_pull_scope_source": "future_authenticated_restore_runtime_binding.private_registry.resource_id",
        "job_registry_configuration_exactly_one_entry_required": True,
        "job_registry_server_source": "future_authenticated_restore_runtime_binding.private_registry.host",
        "job_registry_identity_source": "bootstrap_binding_plan.retained_authenticated_bootstrap_identity_evidence.private_identity.resource_id",
        "direct_and_inherited_authorization_inventory_required": True,
        "authorization_inventory_query_scope_source": "subscription.id",
        "authorization_inventory_must_include_management_group_subscription_resource_group_and_resource_scopes": True,
        "exact_effective_role_assignment_count_during_job": 1,
        "exact_effective_role_definition_ids_during_job": [
            "/providers/Microsoft.Authorization/roleDefinitions/7f951dda-4ed3-4680-a7ca-43fe172d538d"
        ],
        "effective_role_assignment_set_must_equal_private_registry_acrpull": True,
        "unlisted_direct_or_inherited_role_assignment_permitted": False,
        "subscription_uami_inventory_must_contain_exactly_one_matching_identity_during_job": True,
        "subscription_uami_and_effective_role_assignment_counts_after_teardown": 0,
        "other_azure_rbac_assignments_permitted": False,
        "persistent_compute_attachment_permitted": False,
        "identity_resource_client_principal_and_tenant_ids_must_match_authenticated_ancestry": True,
        "attachment_must_be_observed_from_job_and_subscription_inventory": True,
    }:
        _fail("bootstrap_job_attachment_contract.invalid")

    if runtime != {
        "implementation_selected": False,
        "image_source": "future_authenticated_content_addressed_sanitizer_bootstrap_image",
        "digest_pinned_image_required": True,
        "image_tag_permitted": False,
        "private_registry_only": True,
        "image_registry_resource_id_must_equal_source": "future_authenticated_restore_runtime_binding.private_registry.resource_id",
        "image_registry_host_must_equal_source": "future_authenticated_restore_runtime_binding.private_registry.host",
        "image_repository_must_equal_source": "future_authenticated_restore_runtime_binding.private_registry.repository",
        "image_digest_pattern": "sha256:[0-9a-f]{64}",
        "image_reference_must_equal_registry_host_repository_at_sha256_digest": True,
        "job_image_reference_must_equal_authenticated_image_reference": True,
        "provider_observed_repository_and_digest_readback_required": True,
        "job_registry_entry_server_and_identity_must_be_provider_observed": True,
        "registry_pull_identity_must_equal_bootstrap_identity": True,
        "shell_entrypoint_permitted": False,
        "caller_command_or_argument_override_permitted": False,
        "secrets_or_secret_references_permitted": False,
        "volumes_or_mounts_permitted": False,
        "password_or_connection_string_permitted": False,
        "default_or_ambient_credential_chain_permitted": False,
        "explicit_managed_identity_client_id_required": True,
        "maximum_database_token_requests": 1,
        "token_retention_permitted": False,
    }:
        _fail("bootstrap_job_attachment_contract.invalid")

    if network != {
        "implementation_selected": False,
        "control_vnet_resource_id_source": "future_authenticated_restore_runtime_binding.runtime.container_apps_environment.control_vnet_resource_id",
        "infrastructure_subnet_resource_id_source": "future_authenticated_restore_runtime_binding.runtime.container_apps_environment.infrastructure_subnet_resource_id",
        "environment_internal_only_required": True,
        "environment_public_network_access_required": "Disabled",
        "environment_default_outbound_access_required": False,
        "private_environment_required": True,
        "public_ingress_permitted": False,
        "public_database_access_permitted": False,
        "vnet_peering_permitted": False,
        "target_database_private_endpoint_required": True,
        "target_private_dns_binding_required": True,
        "target_database_public_network_access_required": "Disabled",
        "private_registry_resource_id_source": "future_authenticated_restore_runtime_binding.private_registry.resource_id",
        "private_registry_host_source": "future_authenticated_restore_runtime_binding.private_registry.host",
        "private_registry_public_network_access_required": "Disabled",
        "private_registry_admin_credentials_enabled_required": False,
        "private_registry_anonymous_pull_enabled_required": False,
        "private_registry_private_endpoint_resource_id_source": "future_authenticated_restore_runtime_binding.private_registry.private_endpoint_resource_id",
        "private_registry_private_dns_zone_id_source": "future_authenticated_restore_runtime_binding.private_registry.private_dns_zone_resource_id",
        "private_registry_private_dns_link_id_source": "future_authenticated_restore_runtime_binding.private_registry.private_dns_link_resource_id",
        "private_registry_private_dns_zone_required": "privatelink.azurecr.io",
        "infrastructure_subnet_route_table_observation_required": True,
        "infrastructure_subnet_network_security_group_observation_required": True,
        "environment_and_subnet_configuration_must_equal_authenticated_runtime_binding": True,
        "route_table_network_security_group_and_egress_must_equal_authenticated_network_policy": True,
        "private_registry_configuration_endpoint_and_dns_must_equal_authenticated_runtime_binding": True,
        "target_database_configuration_endpoint_and_dns_must_equal_authenticated_controller_receipt": True,
        "explicit_allowlist_egress_required": True,
        "unlisted_egress_permitted": False,
    }:
        _fail("bootstrap_job_attachment_contract.invalid")

    if control != {
        "implementation_selected": False,
        "external_stability_authority_required_before_job_create": True,
        "external_stability_authority_must_span_every_final_absence_readback": True,
        "required_stage_order": list(REQUIRED_STAGE_ORDER),
        "initial_absence_observation_required_before_any_create": True,
        "identity_role_assignment_and_job_create_only_outcomes_required": True,
        "bootstrap_identity_creation_and_readback_required": True,
        "bootstrap_identity_zero_effective_authority_readback_required_before_acrpull_create": True,
        "private_registry_pull_assignment_creation_and_readback_required": True,
        "private_registry_pull_assignment_readback_required_before_job_create": True,
        "job_configuration_observation_required_before_start": True,
        "job_identity_attachment_authentication_required_before_start": True,
        "maximum_start_requests": 1,
        "retry_permitted": False,
        "ambiguous_start_or_execution_outcome_permitted": False,
        "execution_name_must_be_provider_observed": True,
        "database_session_must_close_before_bootstrap_administrator_removal": True,
        "bootstrap_administrator_absence_required_before_job_delete": True,
        "job_and_identity_attachment_absence_required_before_identity_delete": True,
        "private_registry_pull_assignment_absence_required_before_identity_delete": True,
        "bootstrap_identity_deletion_and_absence_readback_required": True,
        "private_registry_pull_assignment_deletion_and_absence_readback_required": True,
        "teardown_required_after_success_failure_or_uncertainty": True,
        "caller_provider_client_or_transport_permitted": False,
    }:
        _fail("bootstrap_job_attachment_contract.invalid")

    if evidence != {
        "fresh_live_provider_observation_required": True,
        "same_process_authentication_and_use_required": True,
        "exact_job_resource_configuration_execution_and_identity_projections_required": True,
        "required_initial_absence_observations": list(
            REQUIRED_INITIAL_ABSENCE_OBSERVATIONS
        ),
        "required_pre_start_provider_observations": list(
            REQUIRED_PRE_START_PROVIDER_OBSERVATIONS
        ),
        "pre_start_execution_inventory_must_be_empty": True,
        "post_start_execution_inventory_must_contain_exactly_one": True,
        "provider_observed_execution_name_must_match_started_execution": True,
        "successful_terminal_execution_status_required": "Succeeded",
        "successful_replica_count_required": 1,
        "failed_replica_count_required": 0,
        "stopped_failed_or_unknown_execution_is_not_success": True,
        "effective_authorization_inventory_must_include_direct_and_inherited_assignments": True,
        "effective_authorization_inventory_must_be_exhaustive_at_subscription_scope": True,
        "job_and_identity_readback_must_be_repeated_before_and_after_execution": True,
        "post_terminal_pre_teardown_job_image_identity_registry_and_pull_attachment_reauthentication_required": True,
        "required_final_absence_observations": list(
            REQUIRED_FINAL_ABSENCE_OBSERVATIONS
        ),
        "raw_provider_bodies_identity_ids_tokens_or_credentials_may_be_public": False,
        "serialized_seal_or_cross_process_authority_permitted": False,
        "retrospective_or_expired_evidence_permitted": False,
    }:
        _fail("bootstrap_job_attachment_contract.invalid")

    expected_status = {
        "contract_validator_implemented": True,
        "job_topology_selected": True,
        "identity_attachment_requirements_selected": True,
        "runtime_requirements_selected": True,
        "network_requirements_selected": True,
        "bootstrap_job_attachment_implemented": False,
        "bootstrap_job_observer_implemented": False,
        "bootstrap_job_attachment_authenticator_implemented": False,
        "bootstrap_control_plane_adapter_implemented": False,
        "live_provider_calls_enabled": False,
        "workflow_integrated": False,
        "executable": False,
        "activation_ready": False,
        "public_activation_failure": ACTIVATION_FAILURE,
        "activation_blockers": list(ACTIVATION_BLOCKERS),
    }
    if (
        status != expected_status
        or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,30}[a-z0-9])?", JOB_NAME)
        or len(JOB_NAME) > 32
        or not all(value is False for value in CLAIMS.values())
        or any(
            section["implementation_selected"] is not False
            for section in (job, identity, runtime, network, control)
        )
    ):
        _fail("bootstrap_job_attachment_contract.invalid")


def validate_bootstrap_job_attachment_contract(
    contract: Any, *, executable: bool = False
) -> dict[str, Any]:
    """Validate the exact dormant contract; never authenticate an attachment."""

    if type(contract) is not dict or type(executable) is not bool:
        _fail("bootstrap_job_attachment_contract.invalid")
    template, _raw = _read_contract()
    try:
        _validate_exact_json_tree(contract)
        canonical = _canonical(contract)
        document = _parse(
            canonical, expected_sha256=hashlib.sha256(canonical).hexdigest()
        )
        if not hmac.compare_digest(_canonical(document), _canonical(template)):
            _fail("bootstrap_job_attachment_contract.invalid")
        _validate_semantics(document)
        if executable:
            _fail(ACTIVATION_FAILURE)
        return document
    except SanitizerBootstrapJobAttachmentContractFailure:
        raise
    except Exception:
        _fail("bootstrap_job_attachment_contract.invalid")


def load_bootstrap_job_attachment_contract(
    *, executable: bool = False
) -> dict[str, Any]:
    document, _raw = _read_contract()
    return validate_bootstrap_job_attachment_contract(document, executable=executable)


if __name__ == "__main__":
    load_bootstrap_job_attachment_contract(executable="--executable" in sys.argv)
    print("sanitizer bootstrap job attachment contract valid (dormant)")
