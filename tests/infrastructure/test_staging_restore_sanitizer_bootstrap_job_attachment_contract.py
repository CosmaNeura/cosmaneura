from __future__ import annotations

import ast
import copy
import hashlib
import tracemalloc
from pathlib import Path
from typing import Any, cast

import pytest

from scripts import staging_restore_sanitizer_bootstrap_binding_plan as binding
from scripts import (
    staging_restore_sanitizer_bootstrap_job_attachment_contract as contract_module,
)
from scripts import validate_staging_restore_runtime_binding as runtime_binding

ROOT = Path(__file__).parents[2]
CONTRACT = (
    ROOT / "contracts/staging-rehearsal/v1/"
    "restore-sanitizer-bootstrap-job-attachment-contract.json"
)
SCRIPT = ROOT / "scripts/staging_restore_sanitizer_bootstrap_job_attachment_contract.py"


def _set_path(document: dict[str, Any], path: str, value: object) -> None:
    owner: dict[str, Any] = document
    parts = path.split(".")
    for name in parts[:-1]:
        owner = owner[name]
    owner[parts[-1]] = value


def test_contract_is_hash_pinned_closed_world_upstream_bound_and_dormant() -> None:
    raw = CONTRACT.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == contract_module.CONTRACT_SHA256
    contract = contract_module.load_bootstrap_job_attachment_contract()
    runtime = runtime_binding.validate_runtime_binding_policy(
        runtime_binding._canonical_template()  # noqa: SLF001
    )

    assert contract["closed_world"] is True
    assert contract["upstream_policies"] == {
        "bootstrap_binding_plan": {
            "policy_id": binding.load_bootstrap_binding_plan_contract()["policy_id"],
            "sha256": f"sha256:{binding.CONTRACT_SHA256}",
        },
        "restore_runtime_binding": {
            "policy_id": runtime["policy_id"],
            "sha256": f"sha256:{runtime_binding.CANONICAL_POLICY_SHA256}",
        },
    }
    assert contract["claims"] == contract_module.CLAIMS
    assert set(contract["claims"].values()) == {False}
    assert contract["status"]["contract_validator_implemented"] is True
    assert contract["status"]["job_topology_selected"] is True
    assert contract["status"]["bootstrap_job_attachment_implemented"] is False
    assert contract["status"]["live_provider_calls_enabled"] is False
    assert contract["status"]["activation_ready"] is False
    assert contract["status"]["public_activation_failure"] == (
        contract_module.ACTIVATION_FAILURE
    )
    assert contract["status"]["activation_blockers"] == list(
        contract_module.ACTIVATION_BLOCKERS
    )


def test_job_topology_is_one_shot_manual_private_and_nonpersistent() -> None:
    contract = contract_module.load_bootstrap_job_attachment_contract()
    job = contract["job_topology"]

    assert job == {
        "implementation_selected": False,
        "resource_type": "Microsoft.App/jobs",
        "name": contract_module.JOB_NAME,
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
        "application_container_count": 1,
        "init_container_count": 0,
        "sidecar_or_additional_containers_permitted": False,
        "complete_container_image_list_must_equal_single_authenticated_image_reference": True,
        "minimum_executions": 0,
        "maximum_executions": 1,
        "start_overrides_permitted": False,
        "revision_or_traffic_surface_permitted": False,
        "ingress_permitted": False,
        "dapr_permitted": False,
    }
    assert len(job["name"]) <= 32


def test_identity_runtime_and_network_boundaries_are_narrow() -> None:
    contract = contract_module.load_bootstrap_job_attachment_contract()
    identity = contract["identity_attachment"]
    runtime = contract["runtime"]
    network = contract["network"]

    assert identity["required_identity_name"] == contract_module.IDENTITY_NAME
    assert identity["identity_type"] == "UserAssigned"
    assert identity["exactly_one_user_assigned_identity_required"] is True
    assert identity["system_assigned_identity_permitted"] is False
    assert identity["additional_user_assigned_identities_permitted"] is False
    assert identity["federated_credentials_permitted"] is False
    assert identity["private_registry_pull_role_assignment_required"] is True
    assert identity["private_registry_pull_role_definition_id"].endswith(
        "/7f951dda-4ed3-4680-a7ca-43fe172d538d"
    )
    assert identity["job_registry_configuration_exactly_one_entry_required"] is True
    assert identity["direct_and_inherited_authorization_inventory_required"] is True
    assert (
        identity[
            "authorization_inventory_must_include_management_group_subscription_resource_group_and_resource_scopes"
        ]
        is True
    )
    assert identity["exact_effective_role_assignment_count_during_job"] == 1
    assert identity["exact_effective_role_definition_ids_during_job"] == [
        identity["private_registry_pull_role_definition_id"]
    ]
    assert (
        identity["effective_role_assignment_set_must_equal_private_registry_acrpull"]
        is True
    )
    assert identity["unlisted_direct_or_inherited_role_assignment_permitted"] is False
    assert (
        identity[
            "subscription_uami_and_effective_role_assignment_counts_after_teardown"
        ]
        == 0
    )
    assert identity["other_azure_rbac_assignments_permitted"] is False
    assert identity["persistent_compute_attachment_permitted"] is False

    assert runtime["digest_pinned_image_required"] is True
    assert runtime["image_tag_permitted"] is False
    assert runtime["private_registry_only"] is True
    assert runtime["image_registry_resource_id_must_equal_source"].endswith(
        "private_registry.resource_id"
    )
    assert runtime["image_registry_host_must_equal_source"].endswith(
        "private_registry.host"
    )
    assert runtime["image_repository_must_equal_source"].endswith(
        "private_registry.repository"
    )
    assert (
        runtime["image_reference_must_equal_registry_host_repository_at_sha256_digest"]
        is True
    )
    assert runtime["image_digest_pattern"] == "sha256:[0-9a-f]{64}"
    assert (
        runtime["job_image_reference_must_equal_authenticated_image_reference"] is True
    )
    assert (
        runtime["job_registry_entry_server_and_identity_must_be_provider_observed"]
        is True
    )
    assert runtime["shell_entrypoint_permitted"] is False
    assert runtime["secrets_or_secret_references_permitted"] is False
    assert runtime["maximum_database_token_requests"] == 1
    assert runtime["token_retention_permitted"] is False

    assert network["private_environment_required"] is True
    assert network["environment_internal_only_required"] is True
    assert network["environment_public_network_access_required"] == "Disabled"
    assert network["environment_default_outbound_access_required"] is False
    assert network["public_ingress_permitted"] is False
    assert network["public_database_access_permitted"] is False
    assert network["vnet_peering_permitted"] is False
    assert network["target_database_private_endpoint_required"] is True
    assert network["target_private_dns_binding_required"] is True
    assert network["target_database_public_network_access_required"] == "Disabled"
    assert network["private_registry_public_network_access_required"] == "Disabled"
    assert network["private_registry_admin_credentials_enabled_required"] is False
    assert network["private_registry_anonymous_pull_enabled_required"] is False
    assert network["private_registry_private_dns_zone_required"] == (
        "privatelink.azurecr.io"
    )
    assert (
        network[
            "environment_and_subnet_configuration_must_equal_authenticated_runtime_binding"
        ]
        is True
    )
    assert (
        network[
            "route_table_network_security_group_and_egress_must_equal_authenticated_network_policy"
        ]
        is True
    )
    assert (
        network[
            "private_registry_configuration_endpoint_and_dns_must_equal_authenticated_runtime_binding"
        ]
        is True
    )
    assert (
        network[
            "target_database_configuration_endpoint_and_dns_must_equal_authenticated_controller_receipt"
        ]
        is True
    )
    assert network["unlisted_egress_permitted"] is False


def test_future_control_plane_is_single_start_and_teardown_first() -> None:
    contract = contract_module.load_bootstrap_job_attachment_contract()
    control = contract["future_control_plane"]
    evidence = contract["evidence"]

    assert control["external_stability_authority_required_before_job_create"] is True
    assert (
        control["external_stability_authority_must_span_every_final_absence_readback"]
        is True
    )
    assert control["required_stage_order"] == list(contract_module.REQUIRED_STAGE_ORDER)
    order = control["required_stage_order"]
    assert control["required_stage_order"][0] == (
        "acquire_external_stability_authority"
    )
    assert control["required_stage_order"][-1] == (
        "release_external_stability_authority"
    )
    assert order.index(
        "observe_initial_bootstrap_job_identity_and_acrpull_assignment_absence"
    ) < order.index("create_bootstrap_identity_with_create_only_semantics")
    assert order.index(
        "observe_and_authenticate_bootstrap_identity_with_zero_effective_authority"
    ) < order.index(
        "create_private_registry_acrpull_role_assignment_with_create_only_semantics"
    )
    assert order.index(
        "observe_exact_acrpull_and_exhaustive_effective_authorization_inventory"
    ) < order.index("create_bootstrap_job_with_create_only_semantics")
    assert order.index("observe_terminal_execution_and_replica_result") + 1 == (
        order.index(
            "reobserve_and_authenticate_job_configuration_image_identity_registry_and_pull_attachment"
        )
    )
    assert control["initial_absence_observation_required_before_any_create"] is True
    assert (
        control["identity_role_assignment_and_job_create_only_outcomes_required"]
        is True
    )
    assert control["bootstrap_identity_creation_and_readback_required"] is True
    assert (
        control[
            "bootstrap_identity_zero_effective_authority_readback_required_before_acrpull_create"
        ]
        is True
    )
    assert (
        control["private_registry_pull_assignment_creation_and_readback_required"]
        is True
    )
    assert control["maximum_start_requests"] == 1
    assert control["retry_permitted"] is False
    assert control["ambiguous_start_or_execution_outcome_permitted"] is False
    assert control["job_configuration_observation_required_before_start"] is True
    assert (
        control["job_identity_attachment_authentication_required_before_start"] is True
    )
    assert (
        control["database_session_must_close_before_bootstrap_administrator_removal"]
        is True
    )
    assert control["bootstrap_administrator_absence_required_before_job_delete"] is True
    assert (
        control["job_and_identity_attachment_absence_required_before_identity_delete"]
        is True
    )
    assert (
        control[
            "private_registry_pull_assignment_absence_required_before_identity_delete"
        ]
        is True
    )
    assert control["bootstrap_identity_deletion_and_absence_readback_required"] is True
    assert (
        control[
            "private_registry_pull_assignment_deletion_and_absence_readback_required"
        ]
        is True
    )
    assert control["teardown_required_after_success_failure_or_uncertainty"] is True
    assert evidence["fresh_live_provider_observation_required"] is True
    assert evidence["same_process_authentication_and_use_required"] is True
    assert evidence["required_initial_absence_observations"] == list(
        contract_module.REQUIRED_INITIAL_ABSENCE_OBSERVATIONS
    )
    assert evidence["required_pre_start_provider_observations"] == list(
        contract_module.REQUIRED_PRE_START_PROVIDER_OBSERVATIONS
    )
    assert evidence["pre_start_execution_inventory_must_be_empty"] is True
    assert evidence["post_start_execution_inventory_must_contain_exactly_one"] is True
    assert evidence["successful_terminal_execution_status_required"] == "Succeeded"
    assert evidence["successful_replica_count_required"] == 1
    assert evidence["failed_replica_count_required"] == 0
    assert evidence["stopped_failed_or_unknown_execution_is_not_success"] is True
    assert (
        evidence[
            "post_terminal_pre_teardown_job_image_identity_registry_and_pull_attachment_reauthentication_required"
        ]
        is True
    )
    assert evidence["required_final_absence_observations"] == list(
        contract_module.REQUIRED_FINAL_ABSENCE_OBSERVATIONS
    )
    assert evidence["retrospective_or_expired_evidence_permitted"] is False


@pytest.mark.parametrize(
    ("path", "value"),
    (
        ("closed_world", False),
        ("input.required_kind", "forged"),
        ("job_topology.implementation_selected", True),
        ("job_topology.replica_retry_limit", 1),
        ("job_topology.application_container_count", 2),
        ("job_topology.init_container_count", 1),
        ("job_topology.sidecar_or_additional_containers_permitted", True),
        (
            "job_topology.complete_container_image_list_must_equal_single_authenticated_image_reference",
            False,
        ),
        ("job_topology.maximum_executions", 2),
        ("job_topology.start_overrides_permitted", True),
        ("identity_attachment.exactly_one_user_assigned_identity_required", False),
        ("identity_attachment.federated_credentials_permitted", True),
        ("identity_attachment.exact_effective_role_assignment_count_during_job", 2),
        ("identity_attachment.other_azure_rbac_assignments_permitted", True),
        ("runtime.digest_pinned_image_required", False),
        ("runtime.image_tag_permitted", True),
        ("runtime.maximum_database_token_requests", 2),
        ("network.private_environment_required", False),
        ("network.public_database_access_permitted", True),
        ("network.private_registry_public_network_access_required", "Enabled"),
        ("network.vnet_peering_permitted", True),
        ("future_control_plane.required_stage_order", []),
        (
            "future_control_plane.initial_absence_observation_required_before_any_create",
            False,
        ),
        ("future_control_plane.maximum_start_requests", 2),
        ("future_control_plane.retry_permitted", True),
        (
            "future_control_plane.teardown_required_after_success_failure_or_uncertainty",
            False,
        ),
        ("evidence.retrospective_or_expired_evidence_permitted", True),
        ("evidence.pre_start_execution_inventory_must_be_empty", False),
        ("evidence.required_initial_absence_observations", []),
        ("evidence.required_final_absence_observations", []),
        ("claims.bootstrap_identity_bound_to_single_use_job", True),
        ("status.bootstrap_job_attachment_implemented", True),
        ("status.activation_ready", True),
    ),
)
def test_security_relevant_mutations_fail_closed(path: str, value: object) -> None:
    changed = copy.deepcopy(contract_module.load_bootstrap_job_attachment_contract())
    _set_path(changed, path, value)
    with pytest.raises(
        contract_module.SanitizerBootstrapJobAttachmentContractFailure,
        match="bootstrap_job_attachment_contract.invalid",
    ):
        contract_module.validate_bootstrap_job_attachment_contract(changed)


def test_unknown_missing_duplicate_nonfinite_and_malformed_contracts_refuse() -> None:
    contract = contract_module.load_bootstrap_job_attachment_contract()
    unknown = copy.deepcopy(contract)
    unknown["unknown"] = True
    missing = copy.deepcopy(contract)
    del missing["claims"]
    for document in (unknown, missing, [], None):
        with pytest.raises(
            contract_module.SanitizerBootstrapJobAttachmentContractFailure,
            match="bootstrap_job_attachment_contract.invalid",
        ):
            contract_module.validate_bootstrap_job_attachment_contract(
                cast(Any, document)
            )

    for raw in (
        b'{"schema_version":1,"schema_version":1}',
        b'{"value":NaN}',
        b'{"value":1e999}',
        b'{"value":-1e999}',
        b'{"value":1.25}',
        b"[]",
        b"\xff",
        b"",
    ):
        with pytest.raises(
            contract_module.SanitizerBootstrapJobAttachmentContractFailure,
            match="bootstrap_job_attachment_contract.invalid",
        ):
            contract_module._parse(  # noqa: SLF001
                raw,
                expected_sha256=hashlib.sha256(raw).hexdigest(),
            )


def test_exact_nested_types_aliases_cycles_and_resource_bounds_refuse() -> None:
    class DictSubclass(dict[str, Any]):
        pass

    class ListSubclass(list[Any]):
        pass

    class StringSubclass(str):
        pass

    class IntegerSubclass(int):
        pass

    exact = contract_module.load_bootstrap_job_attachment_contract()
    mutations: tuple[tuple[str, object], ...] = (
        ("job_topology", DictSubclass(exact["job_topology"])),
        ("status.activation_blockers", tuple(exact["status"]["activation_blockers"])),
        (
            "status.activation_blockers",
            ListSubclass(exact["status"]["activation_blockers"]),
        ),
        ("policy_id", StringSubclass(exact["policy_id"])),
        ("schema_version", IntegerSubclass(1)),
    )
    for path, value in mutations:
        changed = copy.deepcopy(exact)
        _set_path(changed, path, value)
        with pytest.raises(
            contract_module.SanitizerBootstrapJobAttachmentContractFailure,
            match="bootstrap_job_attachment_contract.invalid",
        ):
            contract_module.validate_bootstrap_job_attachment_contract(changed)

    shared: list[object] = []
    cycle: list[object] = []
    cycle.append(cycle)
    too_deep: object = None
    for _index in range(contract_module.MAXIMUM_CONTRACT_DEPTH + 1):
        too_deep = [too_deep]

    for invalid in (
        {"left": shared, "right": shared},
        {"cycle": cycle},
        {"too_deep": too_deep},
        {"too_many": list(range(contract_module.MAXIMUM_CONTRACT_NODES + 1))},
        {"too_long": "x" * (contract_module.MAXIMUM_CONTRACT_SCALAR_CHARACTERS + 1)},
        {"too_large": 1 << (contract_module.MAXIMUM_CONTRACT_INTEGER_BITS + 1)},
    ):
        with pytest.raises(
            contract_module.SanitizerBootstrapJobAttachmentContractFailure,
            match="bootstrap_job_attachment_contract.invalid",
        ):
            contract_module._validate_exact_json_tree(invalid)  # noqa: SLF001


def test_contract_file_read_is_bounded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    oversized = tmp_path / "oversized-contract.json"
    oversized.write_bytes(b" " * (contract_module.MAXIMUM_CONTRACT_BYTES + 1))
    monkeypatch.setattr(contract_module, "CONTRACT_PATH", oversized)
    with pytest.raises(
        contract_module.SanitizerBootstrapJobAttachmentContractFailure,
        match="bootstrap_job_attachment_contract.invalid",
    ):
        contract_module.load_bootstrap_job_attachment_contract()


def test_oversized_list_refuses_before_bulk_traversal_stack_allocation() -> None:
    oversized = [None] * (contract_module.MAXIMUM_CONTRACT_NODES * 128)
    tracemalloc.start()
    try:
        with pytest.raises(
            contract_module.SanitizerBootstrapJobAttachmentContractFailure,
            match="bootstrap_job_attachment_contract.invalid",
        ):
            contract_module._validate_exact_json_tree(oversized)  # noqa: SLF001
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 1024 * 1024


def test_upstream_digest_drift_and_unexpected_failure_are_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contract = contract_module.load_bootstrap_job_attachment_contract()
    original = binding.CONTRACT_SHA256
    monkeypatch.setattr(binding, "CONTRACT_SHA256", "0" * 64)
    with pytest.raises(
        contract_module.SanitizerBootstrapJobAttachmentContractFailure,
        match="bootstrap_job_attachment_contract.invalid",
    ):
        contract_module.validate_bootstrap_job_attachment_contract(contract)

    monkeypatch.setattr(binding, "CONTRACT_SHA256", original)
    monkeypatch.setattr(
        contract_module,
        "_upstream_policies",
        lambda: (_ for _ in ()).throw(RuntimeError("secret://must-not-escape")),
    )
    with pytest.raises(
        contract_module.SanitizerBootstrapJobAttachmentContractFailure,
        match="bootstrap_job_attachment_contract.invalid",
    ) as captured:
        contract_module.validate_bootstrap_job_attachment_contract(contract)
    assert "secret" not in str(captured.value)


def test_module_contains_no_live_network_provider_database_or_process_surface() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert imported.isdisjoint(
        {
            "azure",
            "httpx",
            "os",
            "psycopg",
            "requests",
            "socket",
            "subprocess",
            "urllib",
        }
    )
    calls = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert calls.isdisjoint(
        {
            "connect",
            "cursor",
            "execute",
            "getenv",
            "Popen",
            "run",
            "system",
            "urlopen",
        }
    )
    assert source.count('CONTRACT_PATH.open("rb")') == 1
    assert source.count("handle.read(MAXIMUM_CONTRACT_BYTES + 1)") == 1


def test_executable_validation_remains_terminal() -> None:
    with pytest.raises(
        contract_module.SanitizerBootstrapJobAttachmentContractFailure,
        match=contract_module.ACTIVATION_FAILURE,
    ):
        contract_module.load_bootstrap_job_attachment_contract(executable=True)
    with pytest.raises(
        contract_module.SanitizerBootstrapJobAttachmentContractFailure,
        match="bootstrap_job_attachment_contract.invalid",
    ):
        contract_module.load_bootstrap_job_attachment_contract(executable=cast(Any, 1))
