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

from scripts import staging_restore_sanitizer_bootstrap_binding_plan as bootstrap
from scripts import (
    staging_restore_sanitizer_privileged_global_connection_plan as connection,
)
from scripts import (
    staging_restore_sanitizer_target_role_observation_plan as target_plan,
)

from tests.infrastructure._staging_restore_fixed_catalog_source_state_helpers import (
    NOW,
    RealChain,
    real_chain,
)

__all__ = ("real_chain",)

ROOT = Path(__file__).parents[2]
CONTRACT = (
    ROOT
    / "contracts/staging-rehearsal/v1/restore-sanitizer-bootstrap-binding-plan-contract.json"
)
SCRIPT = ROOT / "scripts/staging_restore_sanitizer_bootstrap_binding_plan.py"


def _connection_plan(
    real_chain: RealChain,
) -> connection.SanitizerPrivilegedGlobalConnectionPlan:
    _document, _raw, _authority, built, _written, binding = real_chain
    observation = built._isolation_inventory._database_observation
    target_worker = (
        target_plan.SanitizerTargetRoleObservationPlanner._from_reviewed_configuration(
            clock=lambda: NOW
        )
    )
    retained = target_worker.plan(
        target_binding=binding,
        database_observation=observation,
    )
    worker = connection.SanitizerPrivilegedGlobalConnectionPlanner._from_reviewed_configuration(
        clock=lambda: NOW
    )
    return worker.plan(target_role_plan=retained)


def _planner(*times: datetime) -> bootstrap.SanitizerBootstrapBindingPlanner:
    values = iter(times or (NOW, NOW))
    return bootstrap.SanitizerBootstrapBindingPlanner._from_reviewed_configuration(
        clock=lambda: next(values)
    )


@pytest.fixture(scope="module")
def retained_connection_plan(
    real_chain: RealChain,
) -> connection.SanitizerPrivilegedGlobalConnectionPlan:
    return _connection_plan(real_chain)


def _make(
    retained_connection_plan: connection.SanitizerPrivilegedGlobalConnectionPlan,
) -> bootstrap.SanitizerBootstrapBindingPlan:
    return _planner().plan(connection_plan=retained_connection_plan)


def test_contract_is_hash_pinned_closed_world_and_dormant() -> None:
    raw = CONTRACT.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == bootstrap.CONTRACT_SHA256
    contract = bootstrap.load_bootstrap_binding_plan_contract()
    assert contract["closed_world"] is True
    assert contract["claims"] == bootstrap.CLAIMS
    assert contract["status"] == bootstrap._expected_status()
    assert contract["status"]["bootstrap_mechanism_selected"] is True
    assert contract["status"]["bootstrap_identity_selected"] is False
    assert contract["status"]["activation_ready"] is False
    assert (
        "exact_database_role_unselected"
        not in contract["status"]["activation_blockers"]
    )
    assert (
        "provision_job_identity_exact_ids_unpinned"
        not in contract["status"]["activation_blockers"]
    )
    with pytest.raises(
        bootstrap.SanitizerBootstrapBindingPlanFailure,
        match=bootstrap.ACTIVATION_FAILURE,
    ):
        bootstrap.load_bootstrap_binding_plan_contract(executable=True)


def test_bootstrap_design_is_separate_ephemeral_and_teardown_bound() -> None:
    contract = bootstrap.load_bootstrap_binding_plan_contract()
    design = contract["bootstrap_design"]
    lifecycle = contract["future_lifecycle"]
    assert design["mechanism"] == bootstrap.BOOTSTRAP_MECHANISM
    assert design["identity_source"].endswith("identities.jobs.bootstrap")
    assert design["identity_name"] == bootstrap.BOOTSTRAP_IDENTITY_NAME
    assert design["identity_selected"] is False
    assert (
        design[
            "must_be_distinct_from_provision_cleanup_dispatcher_github_and_scribe_identities"
        ]
        is True
    )
    assert design["federated_credentials_permitted"] is False
    assert design["persistent_compute_attachment_permitted"] is False
    assert design["attached_only_to_single_use_bootstrap_job_required"] is True
    assert design["runtime_identity_may_bootstrap"] is False
    assert design["password_or_connection_string_permitted"] is False
    assert design["default_or_ambient_credential_chain_permitted"] is False
    assert design["maximum_database_token_requests"] == 1
    assert lifecycle["exact_order"] == list(bootstrap.LIFECYCLE_ORDER)
    assert lifecycle["provider_create_read_delete_readback_required"] is True
    assert lifecycle["database_session_must_end_before_provider_teardown"] is True
    assert (
        lifecycle["bootstrap_authority_absence_required_before_runtime_role_execution"]
        is True
    )
    assert lifecycle["teardown_required_after_success_failure_or_uncertainty"] is True
    assert lifecycle["retry_pooling_or_ambiguous_outcome_permitted"] is False


def test_real_chain_mints_only_redacted_binding_metadata(
    retained_connection_plan: connection.SanitizerPrivilegedGlobalConnectionPlan,
) -> None:
    retained = retained_connection_plan
    reads: list[datetime] = []

    def clock() -> datetime:
        reads.append(NOW)
        return NOW

    worker = bootstrap.SanitizerBootstrapBindingPlanner._from_reviewed_configuration(
        clock=clock
    )
    result = worker.plan(connection_plan=retained)
    assert reads == [NOW, NOW]
    assert result._is_authenticated()
    assert result._connection_plan is retained
    assert result.connection_plan_sha256 == retained.connection_plan_sha256
    assert result.rehearsal_id == retained.rehearsal_id
    assert result.planned_at == NOW
    assert result.expires_at == retained.expires_at
    assert result.bootstrap_mechanism == bootstrap.BOOTSTRAP_MECHANISM
    assert result.bootstrap_identity_key == "bootstrap"
    assert result.runtime_role_binding_sha256.startswith("sha256:")
    assert len(result.runtime_role_binding_sha256) == 71
    assert all(
        getattr(result, name) is expected for name, expected in bootstrap.CLAIMS.items()
    )
    projection = {
        name: getattr(result, name)
        for name in bootstrap.PUBLIC_FIELDS
        if name != "bootstrap_binding_plan_sha256"
    }
    assert result.bootstrap_binding_plan_sha256 == bootstrap._plan_digest(projection)

    target = retained._target_role_plan
    role = target._target_binding.approved_database_role
    identity = target._database_observation._production_authority.authority["identity"]
    public_text = json.dumps(
        {name: str(getattr(result, name)) for name in bootstrap.PUBLIC_FIELDS},
        sort_keys=True,
    )
    assert role not in public_text
    assert all(
        identity[name] not in public_text
        for name in bootstrap.PROVISION_IDENTITY_FIELDS
    )
    for forbidden in (
        "database_role",
        "provision_identity",
        "bootstrap_authority",
        "token",
        "credential",
        "dsn",
        "connection",
        "provider_client",
        "sql",
    ):
        assert not hasattr(result, forbidden)


def test_binding_digest_is_domain_separated_and_exact(
    retained_connection_plan: connection.SanitizerPrivilegedGlobalConnectionPlan,
) -> None:
    retained = retained_connection_plan
    material, target = bootstrap._binding_material(retained, now=NOW)
    result = _planner().plan(connection_plan=retained)
    assert target is retained._target_role_plan
    assert tuple(material) == bootstrap.BINDING_DIGEST_FIELDS
    assert result.runtime_role_binding_sha256 == bootstrap._runtime_role_binding_digest(
        material
    )
    changed = dict(material)
    changed["approved_database_role"] += "_other"
    assert bootstrap._runtime_role_binding_digest(changed) != (
        result.runtime_role_binding_sha256
    )


def test_api_accepts_only_one_keyword_only_exact_connection_plan(
    retained_connection_plan: connection.SanitizerPrivilegedGlobalConnectionPlan,
) -> None:
    retained = retained_connection_plan
    parameters = inspect.signature(
        bootstrap.SanitizerBootstrapBindingPlanner.plan
    ).parameters
    assert tuple(parameters) == ("self", "connection_plan")
    assert parameters["connection_plan"].kind is inspect.Parameter.KEYWORD_ONLY
    with pytest.raises(TypeError):
        cast(Any, _planner().plan)(retained)
    with pytest.raises(TypeError):
        cast(Any, _planner().plan)(connection_plan=retained, identity={})

    class ConnectionPlanSubclass(connection.SanitizerPrivilegedGlobalConnectionPlan):
        pass

    for value in (
        object(),
        {},
        retained.connection_plan_sha256,
        object.__new__(connection.SanitizerPrivilegedGlobalConnectionPlan),
        object.__new__(ConnectionPlanSubclass),
    ):
        with pytest.raises(
            bootstrap.SanitizerBootstrapBindingPlanFailure,
            match="bootstrap_binding_plan.connection_plan_invalid",
        ):
            _planner().plan(connection_plan=cast(Any, value))


def test_connection_plan_identity_and_nested_ancestry_are_retained(
    retained_connection_plan: connection.SanitizerPrivilegedGlobalConnectionPlan,
) -> None:
    retained = retained_connection_plan
    result = _planner().plan(connection_plan=retained)
    duplicate = object.__new__(connection.SanitizerPrivilegedGlobalConnectionPlan)
    for name in retained.__slots__:
        object.__setattr__(duplicate, name, getattr(retained, name))
    assert duplicate is not retained
    assert duplicate._is_authenticated() is False
    object.__setattr__(result, "_connection_plan", duplicate)
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
    retained_connection_plan: connection.SanitizerPrivilegedGlobalConnectionPlan,
    invalid: datetime,
) -> None:
    retained = retained_connection_plan
    with pytest.raises(
        bootstrap.SanitizerBootstrapBindingPlanFailure,
        match="bootstrap_binding_plan.clock_invalid",
    ):
        _planner(invalid).plan(connection_plan=retained)


def test_clock_regression_expiry_and_between_read_mutation_refuse(
    retained_connection_plan: connection.SanitizerPrivilegedGlobalConnectionPlan,
) -> None:
    retained = retained_connection_plan
    with pytest.raises(
        bootstrap.SanitizerBootstrapBindingPlanFailure,
        match="bootstrap_binding_plan.clock_regressed",
    ):
        _planner(NOW, NOW - timedelta(seconds=1)).plan(connection_plan=retained)
    with pytest.raises(
        bootstrap.SanitizerBootstrapBindingPlanFailure,
        match="bootstrap_binding_plan.target_expired",
    ):
        _planner(retained.expires_at).plan(connection_plan=retained)

    original = retained.connection_plan_sha256
    reads = 0

    def mutating_clock() -> datetime:
        nonlocal reads
        reads += 1
        if reads == 2:
            object.__setattr__(retained, "connection_plan_sha256", "sha256:" + "0" * 64)
        return NOW

    try:
        worker = (
            bootstrap.SanitizerBootstrapBindingPlanner._from_reviewed_configuration(
                clock=mutating_clock
            )
        )
        with pytest.raises(
            bootstrap.SanitizerBootstrapBindingPlanFailure,
            match="bootstrap_binding_plan.connection_plan_invalid",
        ):
            worker.plan(connection_plan=retained)
    finally:
        object.__setattr__(retained, "connection_plan_sha256", original)


def test_output_refuses_serialization_and_direct_construction(
    retained_connection_plan: connection.SanitizerPrivilegedGlobalConnectionPlan,
) -> None:
    result = _make(retained_connection_plan)
    assert repr(result) == "<SanitizerBootstrapBindingPlan sealed metadata-only>"
    for operation in (
        lambda: copy.copy(result),
        lambda: copy.deepcopy(result),
        lambda: pickle.dumps(result),
    ):
        with pytest.raises(bootstrap.SanitizerBootstrapBindingPlanFailure):
            operation()
    for operation in (lambda: vars(result), lambda: json.dumps(result)):
        with pytest.raises(TypeError):
            operation()
    with pytest.raises(
        bootstrap.SanitizerBootstrapBindingPlanFailure,
        match="bootstrap_binding_plan.output_invalid",
    ):
        bootstrap.SanitizerBootstrapBindingPlan()


def test_duplicated_output_cannot_authenticate(
    retained_connection_plan: connection.SanitizerPrivilegedGlobalConnectionPlan,
) -> None:
    result = _make(retained_connection_plan)
    duplicate = object.__new__(bootstrap.SanitizerBootstrapBindingPlan)
    for name in result.__slots__:
        object.__setattr__(duplicate, name, getattr(result, name))
    assert duplicate._is_authenticated() is False


def test_mutated_output_cannot_authenticate(
    retained_connection_plan: connection.SanitizerPrivilegedGlobalConnectionPlan,
) -> None:
    result = _make(retained_connection_plan)
    object.__setattr__(result, "bootstrap_identity_bound", True)
    assert result._is_authenticated() is False
    object.__setattr__(result, "bootstrap_identity_bound", False)
    assert result._is_authenticated() is True

    class EqualitySpoof:
        def __eq__(self, other: object) -> bool:
            return other is False

    object.__setattr__(result, "bootstrap_identity_bound", EqualitySpoof())
    assert result._is_authenticated() is False


@pytest.mark.parametrize(
    ("path", "replacement"),
    (
        (
            "retained_runtime_binding.raw_database_role_or_identity_values_may_be_public",
            True,
        ),
        ("bootstrap_design.identity_selected", True),
        ("bootstrap_design.federated_credentials_permitted", True),
        ("bootstrap_design.runtime_identity_may_bootstrap", True),
        ("bootstrap_design.maximum_database_token_requests", 2),
        ("future_lifecycle.implementation_selected", True),
        ("future_lifecycle.retry_pooling_or_ambiguous_outcome_permitted", True),
        ("claims.bootstrap_authority_authenticated", True),
        ("claims.database_mutation_authority", True),
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
        bootstrap.SanitizerBootstrapBindingPlanFailure,
        match="bootstrap_binding_plan.contract_invalid",
    ):
        bootstrap.validate_bootstrap_binding_plan_contract(document)


def test_unknown_missing_duplicate_nonfinite_and_malformed_contracts_refuse() -> None:
    contract = json.loads(CONTRACT.read_bytes())
    changed = copy.deepcopy(contract)
    changed["unknown"] = True
    missing = copy.deepcopy(contract)
    del missing["claims"]
    for document in (changed, missing, [], None):
        with pytest.raises(
            bootstrap.SanitizerBootstrapBindingPlanFailure,
            match="bootstrap_binding_plan.contract_invalid",
        ):
            bootstrap.validate_bootstrap_binding_plan_contract(cast(Any, document))
    for raw in (
        b'{"schema_version":1,"schema_version":1}',
        b'{"value":NaN}',
        b"[]",
        b"\xff",
        b"",
    ):
        with pytest.raises(
            bootstrap.SanitizerBootstrapBindingPlanFailure,
            match="bootstrap_binding_plan.contract_invalid",
        ):
            bootstrap._parse(raw, expected_sha256=hashlib.sha256(raw).hexdigest())


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


def test_public_factory_remains_terminal() -> None:
    with pytest.raises(
        bootstrap.SanitizerBootstrapBindingPlanFailure,
        match=bootstrap.ACTIVATION_FAILURE,
    ):
        bootstrap.build_sanitizer_bootstrap_binding_planner()


def test_unexpected_failures_are_content_free_and_return_no_partial_plan(
    retained_connection_plan: connection.SanitizerPrivilegedGlobalConnectionPlan,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    retained = retained_connection_plan

    def fail(*_args: Any, **_kwargs: Any) -> tuple[dict[str, Any], Any]:
        raise RuntimeError("postgresql://role:secret@private/database")

    monkeypatch.setattr(bootstrap, "_binding_material", fail)
    with pytest.raises(bootstrap.SanitizerBootstrapBindingPlanFailure) as captured:
        _planner().plan(connection_plan=retained)
    assert captured.value.args == ("bootstrap_binding_plan.connection_plan_invalid",)
    assert "secret" not in str(captured.value)
