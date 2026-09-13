from __future__ import annotations

import ast
import copy
import hashlib
from pathlib import Path
from typing import Any, cast

import pytest

from scripts import staging_restore_sanitizer_database_capability as capability
from scripts import (
    staging_restore_sanitizer_target_role_observation_plan as observation_plan,
)
from scripts import (
    staging_restore_sanitizer_target_role_observer_contract as observer_contract,
)

ROOT = Path(__file__).parents[2]
CONTRACT = (
    ROOT
    / "contracts/staging-rehearsal/v1/restore-sanitizer-target-role-observer-contract.json"
)
SCRIPT = ROOT / "scripts/staging_restore_sanitizer_target_role_observer_contract.py"


def test_contract_is_hash_pinned_closed_world_partitioned_and_dormant() -> None:
    raw = CONTRACT.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == observer_contract.CONTRACT_SHA256
    contract = observer_contract.load_target_role_observer_contract()
    plan_contract = observation_plan.load_target_role_observation_plan_contract()

    assert contract["closed_world"] is True
    assert contract["upstream_policies"] == {
        "database_capability": {
            "policy_id": capability.load_database_capability_contract()["policy_id"],
            "sha256": f"sha256:{capability.CONTRACT_SHA256}",
        },
        "target_role_observation_plan": {
            "policy_id": plan_contract["policy_id"],
            "sha256": f"sha256:{observation_plan.CONTRACT_SHA256}",
        },
    }
    assert contract["scope"]["requirements"] == list(
        observer_contract.TARGET_REQUIREMENTS
    )
    assert (
        contract["scope"]["requirements"]
        == plan_contract["requirement_partition"]["target_role_observer"]
    )
    assert len(observer_contract.TARGET_REQUIREMENTS) == len(
        set(observer_contract.TARGET_REQUIREMENTS)
    )
    assert contract["observation_frames"]["frames"] == list(
        observer_contract.FRAME_NAMES
    )
    assert contract["claims"] == observer_contract.CLAIMS
    assert all(value is False for value in contract["claims"].values())
    assert contract["status"]["contract_validator_implemented"] is True
    assert contract["status"]["query_templates_selected"] is False
    assert contract["status"]["observation_authority_implemented"] is False
    assert contract["status"]["activation_ready"] is False
    assert (
        contract["status"]["public_activation_failure"]
        == observer_contract.ACTIVATION_FAILURE
    )


def test_future_input_connection_transaction_and_output_are_narrow() -> None:
    contract = observer_contract.load_target_role_observer_contract()
    input_contract = contract["input"]
    connection = contract["future_connection"]
    transaction = contract["future_transaction"]
    grammar = contract["query_grammar"]
    frames = contract["observation_frames"]

    assert input_contract["only_future_public_input"] == (
        "sealed_target_role_observation_plan"
    )
    assert (
        input_contract[
            "caller_bytes_maps_paths_urls_dsns_hosts_hostaddrs_ports_roles_identity_ids_"
            "tokens_credentials_connectors_callbacks_sql_policies_timestamps_or_"
            "runtime_overrides_permitted"
        ]
        is False
    )
    assert connection["implementation_selected"] is False
    assert connection["managed_identity_only"] is True
    assert connection["maximum_connections"] == 1
    assert connection["maximum_token_requests"] == 1
    assert connection["pooling_permitted"] is False
    assert connection["retry_permitted"] is False
    assert connection["token_retention_permitted"] is False
    assert connection["tls_mode"] == "verify-full"
    assert transaction == {
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
    assert grammar["positive_allowlist_required"] is True
    assert grammar["query_templates_selected"] is False
    assert grammar["exact_template_sha256_values_selected"] is False
    assert grammar["exact_order_selected"] is False
    assert frames["frame_schemas_selected"] is False
    assert frames["raw_application_values_permitted"] is False
    assert frames["raw_password_or_password_verifier_permitted"] is False
    assert frames["disk_log_artifact_or_evidence_publication_permitted"] is False


@pytest.mark.parametrize(
    ("path", "value"),
    (
        ("closed_world", False),
        ("input.exact_type_seal_and_process_local_identity_required", False),
        ("scope.privileged_or_server_global_requirements_permitted", True),
        ("future_connection.implementation_selected", True),
        ("future_connection.maximum_connections", 2),
        ("future_transaction.read_only", False),
        ("future_transaction.commit_permitted", True),
        ("query_grammar.query_templates_selected", True),
        ("query_grammar.caller_sql_or_identifier_interpolation_permitted", True),
        ("observation_frames.frame_schemas_selected", True),
        ("claims.database_role_state_authenticated", True),
        ("status.activation_ready", True),
    ),
)
def test_security_relevant_mutations_fail_closed(path: str, value: object) -> None:
    contract = observer_contract.load_target_role_observer_contract()
    changed = copy.deepcopy(contract)
    owner = changed
    parts = path.split(".")
    for name in parts[:-1]:
        owner = owner[name]
    owner[parts[-1]] = value
    with pytest.raises(
        observer_contract.SanitizerTargetRoleObserverContractFailure,
        match="target_role_observer_contract.invalid",
    ):
        observer_contract.validate_target_role_observer_contract(changed)


def test_requirement_substitution_duplication_and_reordering_fail_closed() -> None:
    contract = observer_contract.load_target_role_observer_contract()
    variants = []
    removed = copy.deepcopy(contract)
    removed["scope"]["requirements"].pop()
    variants.append(removed)
    duplicated = copy.deepcopy(contract)
    duplicated["scope"]["requirements"].append(duplicated["scope"]["requirements"][-1])
    variants.append(duplicated)
    reordered = copy.deepcopy(contract)
    reordered["scope"]["requirements"].reverse()
    variants.append(reordered)
    substituted = copy.deepcopy(contract)
    substituted["scope"]["requirements"][0] = "exact-provision-job-uami-mapping"
    variants.append(substituted)

    for document in variants:
        with pytest.raises(
            observer_contract.SanitizerTargetRoleObserverContractFailure,
            match="target_role_observer_contract.invalid",
        ):
            observer_contract.validate_target_role_observer_contract(document)


def test_unknown_missing_duplicate_nonfinite_and_malformed_contracts_refuse() -> None:
    contract = observer_contract.load_target_role_observer_contract()
    unknown = copy.deepcopy(contract)
    unknown["unknown"] = True
    missing = copy.deepcopy(contract)
    del missing["claims"]
    for document in (unknown, missing, [], None):
        with pytest.raises(
            observer_contract.SanitizerTargetRoleObserverContractFailure,
            match="target_role_observer_contract.invalid",
        ):
            observer_contract.validate_target_role_observer_contract(
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
            observer_contract.SanitizerTargetRoleObserverContractFailure,
            match="target_role_observer_contract.invalid",
        ):
            observer_contract._parse(
                raw,
                expected_sha256=hashlib.sha256(raw).hexdigest(),
            )


def test_upstream_digest_drift_and_unexpected_failure_are_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contract = observer_contract.load_target_role_observer_contract()
    original_plan_sha256 = observation_plan.CONTRACT_SHA256
    monkeypatch.setattr(observation_plan, "CONTRACT_SHA256", "0" * 64)
    with pytest.raises(
        observer_contract.SanitizerTargetRoleObserverContractFailure,
        match="target_role_observer_contract.invalid",
    ):
        observer_contract.validate_target_role_observer_contract(contract)

    monkeypatch.setattr(
        observation_plan,
        "CONTRACT_SHA256",
        original_plan_sha256,
    )
    monkeypatch.setattr(
        observer_contract,
        "_expected_upstream",
        lambda: (_ for _ in ()).throw(RuntimeError("secret://must-not-escape")),
    )
    with pytest.raises(
        observer_contract.SanitizerTargetRoleObserverContractFailure,
        match="target_role_observer_contract.invalid",
    ) as captured:
        observer_contract.validate_target_role_observer_contract(contract)
    assert "secret" not in str(captured.value)


def test_module_contains_no_live_database_network_provider_or_process_surface() -> None:
    source = SCRIPT.read_text()
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
            "executemany",
            "getenv",
            "open",
            "Popen",
            "run",
            "system",
            "urlopen",
        }
    )
    assert "SELECT " not in source


def test_executable_validation_remains_terminal() -> None:
    with pytest.raises(
        observer_contract.SanitizerTargetRoleObserverContractFailure,
        match=observer_contract.ACTIVATION_FAILURE,
    ):
        observer_contract.load_target_role_observer_contract(executable=True)
    with pytest.raises(
        observer_contract.SanitizerTargetRoleObserverContractFailure,
        match="target_role_observer_contract.invalid",
    ):
        observer_contract.load_target_role_observer_contract(executable=cast(Any, 1))
