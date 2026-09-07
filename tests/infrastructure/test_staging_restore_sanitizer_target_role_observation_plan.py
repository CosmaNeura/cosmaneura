from __future__ import annotations

import ast
import copy
import dataclasses
import hashlib
import inspect
import json
import pickle
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, cast

import pytest

from scripts import staging_restore_sanitizer as sanitizer
from scripts import staging_restore_sanitizer_target_role_observation_plan as plan

from tests.infrastructure._staging_restore_fixed_catalog_source_state_helpers import (
    NOW,
    RealChain,
    real_chain,
)

__all__ = ("real_chain",)

ROOT = Path(__file__).parents[2]
CONTRACT = (
    ROOT
    / "contracts/staging-rehearsal/v1/restore-sanitizer-target-role-observation-plan-contract.json"
)
SCRIPT = ROOT / "scripts/staging_restore_sanitizer_target_role_observation_plan.py"


def _inputs(real_chain: RealChain) -> tuple[Any, Any]:
    _document, _raw, _authority, built, _written, target = real_chain
    return target, built._isolation_inventory._database_observation


def _planner(*times: datetime) -> plan.SanitizerTargetRoleObservationPlanner:
    values = iter(times or (NOW, NOW))
    return plan.SanitizerTargetRoleObservationPlanner._from_reviewed_configuration(
        clock=lambda: next(values)
    )


def _make(real_chain: RealChain) -> plan.SanitizerTargetRoleObservationPlan:
    target, observation = _inputs(real_chain)
    return _planner().plan(
        target_binding=target,
        database_observation=observation,
    )


def _reject(
    target: Any,
    observation: Any,
    code: str = "target_role_observation.authority_invalid",
) -> None:
    with pytest.raises(plan.SanitizerTargetRoleObservationFailure, match=code):
        _planner().plan(
            target_binding=cast(Any, target),
            database_observation=cast(Any, observation),
        )


def test_contract_is_hash_pinned_closed_world_partitioned_and_dormant() -> None:
    raw = CONTRACT.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == plan.CONTRACT_SHA256
    contract = plan.load_target_role_observation_plan_contract()
    assert contract["closed_world"] is True
    assert contract["plan"]["public_fields"] == list(plan.PUBLIC_FIELDS)
    assert contract["plan"]["private_fields"] == list(plan.PRIVATE_FIELDS)
    assert contract["claims"] == plan.CLAIMS
    assert contract["status"]["observation_plan_binder_implemented"] is True
    assert contract["status"]["database_observer_implemented"] is False
    assert contract["status"]["activation_ready"] is False
    assert contract["status"]["public_activation_failure"] == plan.ACTIVATION_FAILURE
    assert contract["post_provision_descriptor"] == {
        "accepted_as_public_input": False,
        "selected_by_this_plan": False,
        "selection_implemented": False,
        "selection_source": "separately-reviewed-provisioning-output",
        "observer_output_may_select_expected_digest": False,
        "pre_provision_descriptor_substitution_permitted": False,
    }
    partition = contract["requirement_partition"]
    groups = [
        value
        for name, value in partition.items()
        if name not in {"source", "exact_disjoint_exhaustive_partition_required"}
    ]
    flattened = [item for group in groups for item in group]
    capability = plan.capability.load_database_capability_contract()
    assert len(flattened) == len(set(flattened))
    assert set(flattened) == set(capability["verification"]["must_authenticate"])
    with pytest.raises(
        plan.SanitizerTargetRoleObservationFailure,
        match=plan.ACTIVATION_FAILURE,
    ):
        plan.load_target_role_observation_plan_contract(executable=True)


def test_real_chain_mints_only_a_sealed_metadata_plan(real_chain: RealChain) -> None:
    target, observation = _inputs(real_chain)
    reads: list[datetime] = []

    def clock() -> datetime:
        reads.append(NOW)
        return NOW

    worker = plan.SanitizerTargetRoleObservationPlanner._from_reviewed_configuration(
        clock=clock
    )
    result = worker.plan(
        target_binding=target,
        database_observation=observation,
    )
    assert reads == [NOW, NOW]
    assert result._is_authenticated()
    assert result.kind == plan.PLAN_KIND
    assert result.observation_plan_contract_sha256 == f"sha256:{plan.CONTRACT_SHA256}"
    assert result.planned_at == NOW
    assert result.expires_at <= target.expires_at
    assert result.controller_receipt_sha256 == target.receipt_sha256
    assert result.database_observation_sha256 == sanitizer.receipt_content._digest(
        observation.authority_bytes
    )
    assert result._target_binding is target
    assert result._database_observation is observation
    assert all(getattr(result, name) is value for name, value in plan.CLAIMS.items())
    assert result.observation_plan_sha256 == plan._plan_digest(
        {
            name: getattr(result, name)
            for name in plan.PUBLIC_FIELDS
            if name != "observation_plan_sha256"
        }
    )
    public = tuple(name for name in result.__slots__ if not name.startswith("_"))
    assert public == plan.PUBLIC_FIELDS
    assert tuple(name for name in result.__slots__ if name.startswith("_")) == (
        plan.PRIVATE_FIELDS
    )
    assert dataclasses.is_dataclass(result) is False
    for forbidden in (
        "target_fqdn",
        "database_name",
        "approved_database_role",
        "target_postgres_server_id",
        "token",
        "dsn",
        "connection",
        "cursor",
        "sql",
    ):
        assert not hasattr(result, forbidden)


def test_plan_api_accepts_only_two_keyword_only_exact_sealed_inputs(
    real_chain: RealChain,
) -> None:
    target, observation = _inputs(real_chain)
    parameters = inspect.signature(
        plan.SanitizerTargetRoleObservationPlanner.plan
    ).parameters
    assert tuple(parameters) == ("self", "target_binding", "database_observation")
    assert all(
        parameters[name].kind is inspect.Parameter.KEYWORD_ONLY
        for name in ("target_binding", "database_observation")
    )
    with pytest.raises(TypeError):
        cast(Any, _planner().plan)(target, observation)
    with pytest.raises(TypeError):
        cast(Any, _planner().plan)(
            target_binding=target,
            database_observation=observation,
            connector=lambda: None,
        )

    class TargetSubclass(sanitizer.SanitizerTargetBinding):
        pass

    class ObservationSubclass(type(observation)):
        pass

    for other in (
        object(),
        {},
        target._written_receipt,
        object.__new__(sanitizer.SanitizerTargetBinding),
        object.__new__(TargetSubclass),
    ):
        _reject(other, observation)
    for other in (
        object(),
        {},
        observation.authority_bytes,
        observation.authority,
        object.__new__(type(observation)),
        object.__new__(ObservationSubclass),
    ):
        _reject(target, other)


def test_database_observation_must_be_identical_retained_ancestor(
    real_chain: RealChain,
) -> None:
    target, observation = _inputs(real_chain)
    duplicate = object.__new__(type(observation))
    for field in dataclasses.fields(observation):
        object.__setattr__(duplicate, field.name, getattr(observation, field.name))
    assert duplicate is not observation
    assert duplicate.authority_bytes == observation.authority_bytes
    assert duplicate._is_authenticated()
    _reject(target, duplicate, "target_role_observation.lineage_invalid")


@pytest.mark.parametrize(
    ("owner_name", "field", "replacement"),
    (
        ("target", "rehearsal_id", "0" * 36),
        ("target", "receipt_sha256", "0" * 64),
        ("observation", "rehearsal_id", "0" * 36),
        ("built", "database_observation_sha256", "sha256:" + "0" * 64),
    ),
)
def test_tampered_target_observation_and_retained_ancestry_refuse(
    real_chain: RealChain,
    owner_name: str,
    field: str,
    replacement: str,
) -> None:
    target, observation = _inputs(real_chain)
    built = target._written_receipt._built_receipt
    owner = {"target": target, "observation": observation, "built": built}[owner_name]
    original = getattr(owner, field)
    try:
        object.__setattr__(owner, field, replacement)
        _reject(target, observation, "authority_invalid|lineage_invalid")
    finally:
        object.__setattr__(owner, field, original)


@pytest.mark.parametrize(
    "invalid",
    (
        datetime(2026, 9, 5, 12),
        NOW.replace(microsecond=1),
        NOW.replace(tzinfo=timezone(timedelta(0), name="not-utc-singleton")),
    ),
)
def test_clock_requires_exact_whole_second_utc(
    real_chain: RealChain, invalid: datetime
) -> None:
    target, observation = _inputs(real_chain)
    with pytest.raises(
        plan.SanitizerTargetRoleObservationFailure,
        match="target_role_observation.clock_invalid",
    ):
        _planner(invalid).plan(
            target_binding=target,
            database_observation=observation,
        )


def test_clock_regression_expiry_and_between_read_mutation_refuse(
    real_chain: RealChain,
) -> None:
    target, observation = _inputs(real_chain)
    with pytest.raises(
        plan.SanitizerTargetRoleObservationFailure,
        match="target_role_observation.clock_regressed",
    ):
        _planner(NOW, NOW - timedelta(seconds=1)).plan(
            target_binding=target,
            database_observation=observation,
        )
    with pytest.raises(
        plan.SanitizerTargetRoleObservationFailure,
        match="target_role_observation.target_expired|authority_invalid",
    ):
        _planner(target.expires_at).plan(
            target_binding=target,
            database_observation=observation,
        )

    original = target._proof_sha256
    reads = 0

    def mutating_clock() -> datetime:
        nonlocal reads
        reads += 1
        if reads == 2:
            object.__setattr__(target, "_proof_sha256", "0" * 64)
        return NOW

    try:
        worker = (
            plan.SanitizerTargetRoleObservationPlanner._from_reviewed_configuration(
                clock=mutating_clock
            )
        )
        with pytest.raises(
            plan.SanitizerTargetRoleObservationFailure,
            match="target_role_observation.authority_invalid",
        ):
            worker.plan(
                target_binding=target,
                database_observation=observation,
            )
    finally:
        object.__setattr__(target, "_proof_sha256", original)


def test_output_is_sealed_redacted_nonserializable_and_tamper_evident(
    real_chain: RealChain,
) -> None:
    result = _make(real_chain)
    assert repr(result) == "<SanitizerTargetRoleObservationPlan sealed metadata-only>"
    for operation in (
        lambda: copy.copy(result),
        lambda: copy.deepcopy(result),
        lambda: pickle.dumps(result),
    ):
        with pytest.raises(plan.SanitizerTargetRoleObservationFailure):
            operation()
    for operation in (
        lambda: dataclasses.replace(cast(Any, result)),
        lambda: dataclasses.asdict(cast(Any, result)),
        lambda: vars(result),
        lambda: json.dumps(result),
    ):
        with pytest.raises(TypeError):
            operation()
    with pytest.raises(
        plan.SanitizerTargetRoleObservationFailure,
        match="target_role_observation.output_invalid",
    ):
        plan.SanitizerTargetRoleObservationPlan()

    duplicate = object.__new__(plan.SanitizerTargetRoleObservationPlan)
    for name in result.__slots__:
        object.__setattr__(duplicate, name, getattr(result, name))
    assert duplicate._is_authenticated() is False

    object.__setattr__(result, "runtime_authority", True)
    assert result._is_authenticated() is False


@pytest.mark.parametrize(
    ("path", "value"),
    (
        ("claims.database_connection_authority", True),
        ("status.database_observer_implemented", True),
        ("status.activation_ready", True),
        ("post_provision_descriptor.selected_by_this_plan", True),
        ("input.database_observation_must_be_identical_retained_ancestor", False),
        (
            "reauthentication.full_target_binding_projection_required_at_start_and_end",
            False,
        ),
    ),
)
def test_security_relevant_contract_mutations_fail_closed(
    path: str, value: object
) -> None:
    contract = json.loads(CONTRACT.read_bytes())
    owner = contract
    parts = path.split(".")
    for name in parts[:-1]:
        owner = owner[name]
    owner[parts[-1]] = value
    with pytest.raises(
        plan.SanitizerTargetRoleObservationFailure,
        match="target_role_observation.contract_invalid",
    ):
        plan.validate_target_role_observation_plan_contract(contract)


def test_unknown_missing_duplicate_nonfinite_and_malformed_contracts_refuse() -> None:
    contract = json.loads(CONTRACT.read_bytes())
    changed = copy.deepcopy(contract)
    changed["unknown"] = True
    missing = copy.deepcopy(contract)
    del missing["claims"]
    for document in (changed, missing, [], None):
        with pytest.raises(
            plan.SanitizerTargetRoleObservationFailure,
            match="target_role_observation.contract_invalid",
        ):
            plan.validate_target_role_observation_plan_contract(cast(Any, document))
    for raw in (
        b'{"schema_version":1,"schema_version":1}',
        b'{"value":NaN}',
        b"[]",
        b"\xff",
        b"",
    ):
        with pytest.raises(
            plan.SanitizerTargetRoleObservationFailure,
            match="target_role_observation.contract_invalid",
        ):
            plan._parse(raw, expected_sha256=hashlib.sha256(raw).hexdigest())


def test_module_has_no_database_network_provider_or_process_surface() -> None:
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
    forbidden_calls = {
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
    calls = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert calls.isdisjoint(forbidden_calls)
    assert "CONFIGURE_EXACT_POST_PROVISION_SCHEMA_DESCRIPTOR_SHA256" not in source


def test_public_factory_remains_terminal_before_observer_access() -> None:
    with pytest.raises(
        plan.SanitizerTargetRoleObservationFailure,
        match=plan.ACTIVATION_FAILURE,
    ):
        plan.build_sanitizer_target_role_observation_planner()


def test_unexpected_failures_are_content_free_and_return_no_partial_plan(
    real_chain: RealChain, monkeypatch: pytest.MonkeyPatch
) -> None:
    target, observation = _inputs(real_chain)

    def fail(**_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("secret://must-not-escape")

    monkeypatch.setattr(plan, "_lineage_projection", fail)
    with pytest.raises(plan.SanitizerTargetRoleObservationFailure) as captured:
        _planner().plan(
            target_binding=target,
            database_observation=observation,
        )
    assert captured.value.args == ("target_role_observation.authority_invalid",)
    assert "secret" not in str(captured.value)
