from __future__ import annotations

import ast
import copy
import hashlib
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
    assert identity["other_azure_rbac_assignments_permitted"] is False
    assert identity["persistent_compute_attachment_permitted"] is False

    assert runtime["digest_pinned_image_required"] is True
    assert runtime["image_tag_permitted"] is False
    assert runtime["private_registry_only"] is True
    assert runtime["shell_entrypoint_permitted"] is False
    assert runtime["secrets_or_secret_references_permitted"] is False
    assert runtime["maximum_database_token_requests"] == 1
    assert runtime["token_retention_permitted"] is False

    assert network["private_environment_required"] is True
    assert network["public_ingress_permitted"] is False
    assert network["public_database_access_permitted"] is False
    assert network["vnet_peering_permitted"] is False
    assert network["target_database_private_endpoint_required"] is True
    assert network["target_private_dns_binding_required"] is True
    assert network["unlisted_egress_permitted"] is False


def test_future_control_plane_is_single_start_and_teardown_first() -> None:
    contract = contract_module.load_bootstrap_job_attachment_contract()
    control = contract["future_control_plane"]
    evidence = contract["evidence"]

    assert control["external_stability_authority_required_before_job_create"] is True
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
    assert control["teardown_required_after_success_failure_or_uncertainty"] is True
    assert evidence["fresh_live_provider_observation_required"] is True
    assert evidence["same_process_authentication_and_use_required"] is True
    assert evidence["retrospective_or_expired_evidence_permitted"] is False


@pytest.mark.parametrize(
    ("path", "value"),
    (
        ("closed_world", False),
        ("input.required_kind", "forged"),
        ("job_topology.implementation_selected", True),
        ("job_topology.replica_retry_limit", 1),
        ("job_topology.maximum_executions", 2),
        ("job_topology.start_overrides_permitted", True),
        ("identity_attachment.exactly_one_user_assigned_identity_required", False),
        ("identity_attachment.federated_credentials_permitted", True),
        ("identity_attachment.other_azure_rbac_assignments_permitted", True),
        ("runtime.digest_pinned_image_required", False),
        ("runtime.image_tag_permitted", True),
        ("runtime.maximum_database_token_requests", 2),
        ("network.private_environment_required", False),
        ("network.public_database_access_permitted", True),
        ("network.vnet_peering_permitted", True),
        ("future_control_plane.maximum_start_requests", 2),
        ("future_control_plane.retry_permitted", True),
        (
            "future_control_plane.teardown_required_after_success_failure_or_uncertainty",
            False,
        ),
        ("evidence.retrospective_or_expired_evidence_permitted", True),
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
            "open",
            "Popen",
            "run",
            "system",
            "urlopen",
        }
    )


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
