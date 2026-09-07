from __future__ import annotations

import ast
import copy
import hashlib
import inspect
import json
import pickle
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, cast

import pytest

from scripts import (
    staging_restore_sanitizer_privileged_global_connection_plan as plan,
)
from scripts import staging_restore_sanitizer_target_role_observation_plan as target

from tests.infrastructure._staging_restore_fixed_catalog_source_state_helpers import (
    NOW,
    RealChain,
    real_chain,
)

__all__ = ("real_chain",)

ROOT = Path(__file__).parents[2]
CONTRACT = (
    ROOT
    / "contracts/staging-rehearsal/v1/restore-sanitizer-privileged-global-connection-plan-contract.json"
)
SCRIPT = ROOT / "scripts/staging_restore_sanitizer_privileged_global_connection_plan.py"


def _target_plan(real_chain: RealChain) -> target.SanitizerTargetRoleObservationPlan:
    _document, _raw, _authority, built, _written, binding = real_chain
    observation = built._isolation_inventory._database_observation
    planner = target.SanitizerTargetRoleObservationPlanner._from_reviewed_configuration(
        clock=lambda: NOW
    )
    return planner.plan(
        target_binding=binding,
        database_observation=observation,
    )


def _planner(*times: datetime) -> plan.SanitizerPrivilegedGlobalConnectionPlanner:
    values = iter(times or (NOW, NOW))
    return plan.SanitizerPrivilegedGlobalConnectionPlanner._from_reviewed_configuration(
        clock=lambda: next(values)
    )


def _make(real_chain: RealChain) -> plan.SanitizerPrivilegedGlobalConnectionPlan:
    return _planner().plan(target_role_plan=_target_plan(real_chain))


def test_contract_is_hash_pinned_closed_world_and_dormant() -> None:
    raw = CONTRACT.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == plan.CONTRACT_SHA256
    contract = plan.load_privileged_global_connection_plan_contract()
    assert contract["closed_world"] is True
    assert contract["database_inventory"]["exact_expected_names"] == list(
        plan.EXPECTED_DATABASE_NAMES
    )
    assert contract["connection_sweep"]["maintenance_database"] == "postgres"
    assert contract["disabled_database_strategy"]["strategy"] == (
        "authenticated-server-scope-inaccessibility"
    )
    assert (
        contract["disabled_database_strategy"][
            "disabled_database_application_data_absence_claimed"
        ]
        is False
    )
    assert contract["status"]["connection_plan_binder_implemented"] is True
    assert contract["status"]["bootstrap_authority_selected"] is False
    assert contract["status"]["observer_implemented"] is False
    assert contract["status"]["activation_ready"] is False
    assert contract["claims"] == plan.CLAIMS
    with pytest.raises(
        plan.SanitizerPrivilegedGlobalConnectionPlanFailure,
        match=plan.ACTIVATION_FAILURE,
    ):
        plan.load_privileged_global_connection_plan_contract(executable=True)


def test_connection_strategy_is_exact_bounded_and_fail_closed() -> None:
    contract = plan.load_privileged_global_connection_plan_contract()
    inventory = contract["database_inventory"]
    sweep = contract["connection_sweep"]
    disabled = contract["disabled_database_strategy"]
    assert inventory["target_database_must_equal"] == "staging"
    assert inventory["unexpected_missing_or_duplicate_entry_permitted"] is False
    assert inventory["strictly_increasing_oid_order_required"] is True
    assert sweep["every_connectable_database_must_be_observed"] is True
    assert sweep["managed_database_connection_or_catalog_failure_permitted"] is False
    assert sweep["maximum_connections"] == inventory["maximum_entries"] == 64
    assert sweep["maximum_concurrent_connections"] == 1
    assert sweep["maximum_token_requests"] == 64
    assert sweep["pooling_permitted"] is False
    assert sweep["retry_permitted"] is False
    assert disabled["allowed_disabled_database_names"] == [
        "azure_maintenance",
        "azure_sys",
        "template0",
    ]
    assert disabled["template0_must_be_present_disabled_and_template"] is True
    assert disabled["target_postgres_and_template1_must_be_connectable"] is True
    assert disabled["per_database_connection_or_catalog_claim_permitted"] is False
    assert (
        disabled["future_enablement_or_database_creation_during_window_permitted"]
        is False
    )


def test_real_chain_mints_only_sealed_public_metadata(real_chain: RealChain) -> None:
    retained = _target_plan(real_chain)
    reads: list[datetime] = []

    def clock() -> datetime:
        reads.append(NOW)
        return NOW

    worker = (
        plan.SanitizerPrivilegedGlobalConnectionPlanner._from_reviewed_configuration(
            clock=clock
        )
    )
    result = worker.plan(target_role_plan=retained)
    assert reads == [NOW, NOW]
    assert result._is_authenticated()
    assert result.kind == plan.PLAN_KIND
    assert result._target_role_plan is retained
    assert (
        result.target_role_observation_plan_sha256 == retained.observation_plan_sha256
    )
    assert result.rehearsal_id == retained.rehearsal_id
    assert result.planned_at == NOW
    assert result.expires_at == retained.expires_at
    assert result.maintenance_database == "postgres"
    assert result.expected_database_count == 6
    assert result.maximum_connections == 64
    assert result.maximum_concurrent_connections == 1
    assert result.maximum_token_requests == 64
    assert all(getattr(result, name) is value for name, value in plan.CLAIMS.items())
    projection = {
        name: getattr(result, name)
        for name in plan.PUBLIC_FIELDS
        if name != "connection_plan_sha256"
    }
    assert result.connection_plan_sha256 == plan._plan_digest(projection)
    for forbidden in (
        "database_inventory",
        "database_names",
        "bootstrap_authority",
        "token",
        "credential",
        "dsn",
        "connection",
        "cursor",
        "sql",
    ):
        assert not hasattr(result, forbidden)


def test_plan_api_accepts_only_one_keyword_only_exact_sealed_plan(
    real_chain: RealChain,
) -> None:
    retained = _target_plan(real_chain)
    parameters = inspect.signature(
        plan.SanitizerPrivilegedGlobalConnectionPlanner.plan
    ).parameters
    assert tuple(parameters) == ("self", "target_role_plan")
    assert parameters["target_role_plan"].kind is inspect.Parameter.KEYWORD_ONLY
    with pytest.raises(TypeError):
        cast(Any, _planner().plan)(retained)
    with pytest.raises(TypeError):
        cast(Any, _planner().plan)(target_role_plan=retained, connector=lambda: None)

    class PlanSubclass(target.SanitizerTargetRoleObservationPlan):
        pass

    for value in (
        object(),
        {},
        retained.observation_plan_sha256,
        object.__new__(target.SanitizerTargetRoleObservationPlan),
        object.__new__(PlanSubclass),
    ):
        with pytest.raises(
            plan.SanitizerPrivilegedGlobalConnectionPlanFailure,
            match="privileged_global_connection_plan.target_plan_invalid",
        ):
            _planner().plan(target_role_plan=cast(Any, value))


def test_target_plan_identity_and_nested_lineage_are_retained(
    real_chain: RealChain,
) -> None:
    retained = _target_plan(real_chain)
    result = _planner().plan(target_role_plan=retained)
    duplicate = object.__new__(target.SanitizerTargetRoleObservationPlan)
    for name in retained.__slots__:
        object.__setattr__(duplicate, name, getattr(retained, name))
    assert duplicate is not retained
    assert duplicate._is_authenticated() is False
    object.__setattr__(result, "_target_role_plan", duplicate)
    assert result._is_authenticated() is False


@pytest.mark.parametrize(
    "invalid",
    (
        datetime(2026, 9, 7, 12),
        NOW.replace(microsecond=1),
        NOW.replace(tzinfo=timezone(timedelta(0), name="not-utc-singleton")),
    ),
)
def test_clock_requires_exact_whole_second_utc(
    real_chain: RealChain, invalid: datetime
) -> None:
    retained = _target_plan(real_chain)
    with pytest.raises(
        plan.SanitizerPrivilegedGlobalConnectionPlanFailure,
        match="privileged_global_connection_plan.clock_invalid",
    ):
        _planner(invalid).plan(target_role_plan=retained)


def test_clock_regression_expiry_and_between_read_mutation_refuse(
    real_chain: RealChain,
) -> None:
    retained = _target_plan(real_chain)
    with pytest.raises(
        plan.SanitizerPrivilegedGlobalConnectionPlanFailure,
        match="privileged_global_connection_plan.clock_regressed",
    ):
        _planner(NOW, NOW - timedelta(seconds=1)).plan(target_role_plan=retained)
    with pytest.raises(
        plan.SanitizerPrivilegedGlobalConnectionPlanFailure,
        match="privileged_global_connection_plan.target_expired",
    ):
        _planner(retained.expires_at).plan(target_role_plan=retained)

    original = retained.observation_plan_sha256
    reads = 0

    def mutating_clock() -> datetime:
        nonlocal reads
        reads += 1
        if reads == 2:
            object.__setattr__(
                retained, "observation_plan_sha256", "sha256:" + "0" * 64
            )
        return NOW

    try:
        worker = plan.SanitizerPrivilegedGlobalConnectionPlanner._from_reviewed_configuration(
            clock=mutating_clock
        )
        with pytest.raises(
            plan.SanitizerPrivilegedGlobalConnectionPlanFailure,
            match="privileged_global_connection_plan.target_plan_invalid",
        ):
            worker.plan(target_role_plan=retained)
    finally:
        object.__setattr__(retained, "observation_plan_sha256", original)


def test_output_is_redacted_nonserializable_and_tamper_evident(
    real_chain: RealChain,
) -> None:
    result = _make(real_chain)
    assert repr(result) == (
        "<SanitizerPrivilegedGlobalConnectionPlan sealed metadata-only>"
    )
    for operation in (
        lambda: copy.copy(result),
        lambda: copy.deepcopy(result),
        lambda: pickle.dumps(result),
    ):
        with pytest.raises(plan.SanitizerPrivilegedGlobalConnectionPlanFailure):
            operation()
    for operation in (lambda: vars(result), lambda: json.dumps(result)):
        with pytest.raises(TypeError):
            operation()
    with pytest.raises(
        plan.SanitizerPrivilegedGlobalConnectionPlanFailure,
        match="privileged_global_connection_plan.output_invalid",
    ):
        plan.SanitizerPrivilegedGlobalConnectionPlan()

    duplicate = object.__new__(plan.SanitizerPrivilegedGlobalConnectionPlan)
    for name in result.__slots__:
        object.__setattr__(duplicate, name, getattr(result, name))
    assert duplicate._is_authenticated() is False
    object.__setattr__(result, "database_connection_authority", True)
    assert result._is_authenticated() is False


@pytest.mark.parametrize(
    ("path", "replacement"),
    (
        ("database_inventory.exact_expected_names", ["staging"]),
        ("database_inventory.target_database_must_equal", "production"),
        ("connection_sweep.maintenance_database", "staging"),
        ("connection_sweep.maximum_concurrent_connections", 2),
        (
            "connection_sweep.managed_database_connection_or_catalog_failure_permitted",
            True,
        ),
        ("disabled_database_strategy.allowed_disabled_database_names", ["anything"]),
        (
            "disabled_database_strategy.disabled_database_application_data_absence_claimed",
            True,
        ),
        ("claims.database_connection_authority", True),
        ("status.bootstrap_authority_selected", True),
        ("status.activation_ready", True),
    ),
)
def test_security_relevant_contract_mutations_fail_closed(
    path: str, replacement: object
) -> None:
    document = json.loads(CONTRACT.read_bytes())
    owner = document
    names = path.split(".")
    for name in names[:-1]:
        owner = owner[name]
    owner[names[-1]] = replacement
    with pytest.raises(
        plan.SanitizerPrivilegedGlobalConnectionPlanFailure,
        match="privileged_global_connection_plan.contract_invalid",
    ):
        plan.validate_privileged_global_connection_plan_contract(document)


def test_unknown_missing_duplicate_nonfinite_and_malformed_contracts_refuse() -> None:
    contract = json.loads(CONTRACT.read_bytes())
    changed = copy.deepcopy(contract)
    changed["unknown"] = True
    missing = copy.deepcopy(contract)
    del missing["claims"]
    for document in (changed, missing, [], None):
        with pytest.raises(
            plan.SanitizerPrivilegedGlobalConnectionPlanFailure,
            match="privileged_global_connection_plan.contract_invalid",
        ):
            plan.validate_privileged_global_connection_plan_contract(
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
            plan.SanitizerPrivilegedGlobalConnectionPlanFailure,
            match="privileged_global_connection_plan.contract_invalid",
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


def test_public_factory_remains_terminal() -> None:
    with pytest.raises(
        plan.SanitizerPrivilegedGlobalConnectionPlanFailure,
        match=plan.ACTIVATION_FAILURE,
    ):
        plan.build_sanitizer_privileged_global_connection_planner()


def test_unexpected_failures_are_content_free_and_return_no_partial_plan(
    real_chain: RealChain, monkeypatch: pytest.MonkeyPatch
) -> None:
    retained = _target_plan(real_chain)

    def fail(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("postgresql://role:secret@private/database")

    monkeypatch.setattr(plan, "_projection", fail)
    with pytest.raises(plan.SanitizerPrivilegedGlobalConnectionPlanFailure) as captured:
        _planner().plan(target_role_plan=retained)
    assert captured.value.args == (
        "privileged_global_connection_plan.target_plan_invalid",
    )
    assert "secret" not in str(captured.value)
