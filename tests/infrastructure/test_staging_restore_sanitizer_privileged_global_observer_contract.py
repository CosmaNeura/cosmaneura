from __future__ import annotations

import ast
import copy
import hashlib
from pathlib import Path
from typing import Any, cast

import pytest

from scripts import staging_restore_sanitizer_database_capability as capability
from scripts import (
    staging_restore_sanitizer_privileged_global_observer_contract as contract_module,
)
from scripts import (
    staging_restore_sanitizer_target_role_observation_plan as observation_plan,
)

ROOT = Path(__file__).parents[2]
CONTRACT = (
    ROOT
    / "contracts/staging-rehearsal/v1/restore-sanitizer-privileged-global-observer-contract.json"
)
SCRIPT = (
    ROOT / "scripts/staging_restore_sanitizer_privileged_global_observer_contract.py"
)
Failure = contract_module.SanitizerPrivilegedGlobalObserverContractFailure


def test_contract_is_hash_pinned_partition_exact_and_dormant() -> None:
    raw = CONTRACT.read_bytes()
    contract = contract_module.load_privileged_global_observer_contract()
    plan = observation_plan.load_target_role_observation_plan_contract()

    assert hashlib.sha256(raw).hexdigest() == contract_module.CONTRACT_SHA256
    assert contract["closed_world"] is True
    assert contract["upstream_policies"] == {
        "database_capability": {
            "policy_id": capability.load_database_capability_contract()["policy_id"],
            "sha256": f"sha256:{capability.CONTRACT_SHA256}",
        },
        "target_role_observation_plan": {
            "policy_id": plan["policy_id"],
            "sha256": f"sha256:{observation_plan.CONTRACT_SHA256}",
        },
    }
    assert contract["scope"]["requirements"] == list(
        contract_module.PRIVILEGED_GLOBAL_REQUIREMENTS
    )
    assert (
        contract["scope"]["requirements"]
        == plan["requirement_partition"]["privileged_global_observer"]
    )
    assert len(contract_module.PRIVILEGED_GLOBAL_REQUIREMENTS) == len(
        set(contract_module.PRIVILEGED_GLOBAL_REQUIREMENTS)
    )
    assert contract["observation_frames"]["logical_surfaces"] == list(
        contract_module.LOGICAL_SURFACES
    )
    assert contract["claims"] == contract_module.CLAIMS
    assert all(value is False for value in contract["claims"].values())
    assert contract["status"]["contract_validator_implemented"] is True
    assert contract["status"]["activation_ready"] is False


def test_future_bootstrap_connection_and_observation_boundaries_are_narrow() -> None:
    contract = contract_module.load_privileged_global_observer_contract()
    input_contract = contract["input"]
    bootstrap = contract["future_bootstrap_authority"]
    sweep = contract["future_connection_sweep"]
    grammar = contract["query_grammar"]
    frames = contract["observation_frames"]

    assert input_contract["only_future_public_input"] == (
        "sealed_target_role_observation_plan"
    )
    assert input_contract["bootstrap_authority_caller_input_permitted"] is False
    assert bootstrap["selection_implemented"] is False
    assert bootstrap["must_be_distinct_from_provision_runtime_identity"] is True
    assert bootstrap["runtime_identity_may_bootstrap"] is False
    assert bootstrap["role_or_privilege_mutation_permitted_by_observer"] is False
    assert bootstrap["credential_or_token_retention_permitted"] is False
    assert sweep["implementation_selected"] is False
    assert sweep["connectable_database_sweep_required"] is True
    assert sweep["disabled_database_surface_observation_strategy_selected"] is False
    assert sweep["maximum_concurrent_connections"] == 1
    assert sweep["pooling_permitted"] is False
    assert sweep["retry_permitted"] is False
    assert sweep["each_transaction_read_only"] is True
    assert sweep["commit_permitted"] is False
    assert sweep["always_rollback_and_close"] is True
    assert sweep["observer_may_acquire_or_claim_external_stability_authority"] is False
    assert grammar["query_templates_selected"] is False
    assert grammar["entra_principal_mapping_source_selected"] is False
    assert (
        grammar[
            "target_role_and_provision_uami_client_and_principal_ids_must_come_from_"
            "sealed_plan_ancestry"
        ]
        is True
    )
    assert grammar["raw_password_or_password_verifier_selection_permitted"] is False
    assert frames["frame_names_and_schemas_selected"] is False
    assert frames["raw_password_or_password_verifier_permitted"] is False
    assert frames["disk_log_artifact_or_evidence_publication_permitted"] is False


@pytest.mark.parametrize(
    ("path", "value"),
    (
        ("closed_world", False),
        ("input.bootstrap_authority_caller_input_permitted", True),
        ("scope.target_role_observer_requirements_permitted", True),
        ("scope.external_stability_authority_claimed", True),
        ("future_bootstrap_authority.selection_implemented", True),
        ("future_bootstrap_authority.runtime_identity_may_bootstrap", True),
        ("future_bootstrap_authority.password_or_connection_string_permitted", True),
        ("future_connection_sweep.implementation_selected", True),
        ("future_connection_sweep.maximum_concurrent_connections", 2),
        ("future_connection_sweep.each_transaction_read_only", False),
        ("future_connection_sweep.commit_permitted", True),
        ("query_grammar.query_templates_selected", True),
        ("query_grammar.application_schema_or_table_reads_permitted", True),
        ("query_grammar.raw_password_or_password_verifier_selection_permitted", True),
        ("observation_frames.frame_names_and_schemas_selected", True),
        ("claims.privileged_global_observation_authenticated", True),
        ("status.activation_ready", True),
    ),
)
def test_security_relevant_mutations_fail_closed(path: str, value: object) -> None:
    changed = copy.deepcopy(contract_module.load_privileged_global_observer_contract())
    owner = changed
    parts = path.split(".")
    for name in parts[:-1]:
        owner = owner[name]
    owner[parts[-1]] = value
    with pytest.raises(Failure, match="privileged_global_observer_contract.invalid"):
        contract_module.validate_privileged_global_observer_contract(changed)


def test_requirement_substitution_duplication_and_reordering_fail_closed() -> None:
    contract = contract_module.load_privileged_global_observer_contract()
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
    substituted["scope"]["requirements"][0] = "exact-database-role-name"
    variants.append(substituted)

    for document in variants:
        with pytest.raises(
            Failure, match="privileged_global_observer_contract.invalid"
        ):
            contract_module.validate_privileged_global_observer_contract(document)


def test_unknown_missing_duplicate_nonfinite_and_malformed_contracts_refuse() -> None:
    contract = contract_module.load_privileged_global_observer_contract()
    unknown = copy.deepcopy(contract)
    unknown["unknown"] = True
    missing = copy.deepcopy(contract)
    del missing["claims"]
    for document in (unknown, missing, [], None):
        with pytest.raises(
            Failure, match="privileged_global_observer_contract.invalid"
        ):
            contract_module.validate_privileged_global_observer_contract(
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
            Failure, match="privileged_global_observer_contract.invalid"
        ):
            contract_module._parse(
                raw,
                expected_sha256=hashlib.sha256(raw).hexdigest(),
            )


def test_upstream_digest_drift_and_unexpected_failure_are_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contract = contract_module.load_privileged_global_observer_contract()
    original = observation_plan.CONTRACT_SHA256
    monkeypatch.setattr(observation_plan, "CONTRACT_SHA256", "0" * 64)
    with pytest.raises(Failure, match="privileged_global_observer_contract.invalid"):
        contract_module.validate_privileged_global_observer_contract(contract)

    monkeypatch.setattr(observation_plan, "CONTRACT_SHA256", original)
    monkeypatch.setattr(
        contract_module,
        "_expected_upstream",
        lambda: (_ for _ in ()).throw(RuntimeError("secret://must-not-escape")),
    )
    with pytest.raises(Failure) as captured:
        contract_module.validate_privileged_global_observer_contract(contract)
    assert "secret" not in repr(captured.value)


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
    with pytest.raises(Failure, match=contract_module.ACTIVATION_FAILURE):
        contract_module.load_privileged_global_observer_contract(executable=True)
    with pytest.raises(Failure, match="privileged_global_observer_contract.invalid"):
        contract_module.load_privileged_global_observer_contract(
            executable=cast(Any, 1)
        )
