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
CONTRACT_SHA256 = "7d85f301ac94f04fc6aa4c78b845f3ca3875ea8ac54bb4b1e7c105548a2b9207"
BOOTSTRAP_BINDING_PLAN_CONTRACT_SHA256 = (
    "2e670cfc42a64c1f33e21791eb4795832d4c3be39141c7cde4186cd7c90739c2"
)
RESTORE_RUNTIME_BINDING_POLICY_SHA256 = (
    "8b5854323f2e783674f510ee63c0d030bb51d2ec35ff85c511b88514f9b1c8f3"
)
ACTIVATION_FAILURE = "activation.sanitizer_bootstrap_job_attachment_unavailable"
MAXIMUM_CONTRACT_BYTES = 64 * 1024
JOB_NAME = "cosmaneura-stg-restore-bootstrap"
IDENTITY_NAME = "cosmaneura-staging-restore-bootstrap-job"

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

    try:
        document = json.loads(
            raw,
            object_pairs_hook=pairs,
            parse_constant=lambda _value: _fail(
                "bootstrap_job_attachment_contract.invalid"
            ),
        )
    except SanitizerBootstrapJobAttachmentContractFailure:
        raise
    except (UnicodeDecodeError, ValueError, RecursionError):
        _fail("bootstrap_job_attachment_contract.invalid")
    if type(document) is not dict:
        _fail("bootstrap_job_attachment_contract.invalid")
    return document


def _read_contract() -> tuple[dict[str, Any], bytes]:
    try:
        raw = CONTRACT_PATH.read_bytes()
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
        "private_environment_required": True,
        "public_ingress_permitted": False,
        "public_database_access_permitted": False,
        "vnet_peering_permitted": False,
        "target_database_private_endpoint_required": True,
        "target_private_dns_binding_required": True,
        "explicit_allowlist_egress_required": True,
        "unlisted_egress_permitted": False,
    }:
        _fail("bootstrap_job_attachment_contract.invalid")

    if control != {
        "implementation_selected": False,
        "external_stability_authority_required_before_job_create": True,
        "external_stability_authority_must_span_final_job_and_database_authority_absence_readback": True,
        "exact_create_observe_start_poll_terminal_or_stop_delete_observe_order_required": True,
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
        "teardown_required_after_success_failure_or_uncertainty": True,
        "caller_provider_client_or_transport_permitted": False,
    }:
        _fail("bootstrap_job_attachment_contract.invalid")

    if evidence != {
        "fresh_live_provider_observation_required": True,
        "same_process_authentication_and_use_required": True,
        "exact_job_resource_configuration_execution_and_identity_projections_required": True,
        "job_and_identity_readback_must_be_repeated_before_and_after_execution": True,
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
