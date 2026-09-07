from __future__ import annotations

import ast
import hashlib
import inspect
import json
import pickle
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from scripts import staging_restore_sanitizer_data_contract_executor as executor
from scripts import staging_restore_sanitizer_executor as public_executor
from scripts import (
    staging_restore_sanitizer_fixed_catalog_post_projection as projection,
)
from scripts import staging_restore_sanitizer_transaction_plan as plan
from scripts import staging_synthetic_inventory as inventory

from tests.infrastructure import (
    test_staging_restore_sanitizer_source_snapshot as source_tests,
)
from tests.infrastructure.test_staging_restore_sanitizer_data_contract_executor import (
    KEY,
    PREFLIGHT_AT,
    _engine,
    _snapshot_from_rows,
    _source_rows,
)
from tests.infrastructure.test_staging_restore_sanitizer_source_snapshot import (
    KEY as INVENTORY_KEY,
)

authenticated_case = source_tests.authenticated_case
ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "scripts/staging_restore_sanitizer_transaction_plan.py"
CONTRACTS = ROOT / "contracts/staging-rehearsal/v1"
POLICY = CONTRACTS / "restore-sanitizer-policy.json"
INVENTORY_POLICY = CONTRACTS / "synthetic-data-policy.json"
DATA_CONTRACT = CONTRACTS / "restore-sanitizer-data-contract.json"
SOURCE_CONTRACT = CONTRACTS / "restore-sanitizer-source-snapshot-contract.json"
POST_CONTRACT = (
    CONTRACTS / "restore-sanitizer-fixed-catalog-post-projection-contract.json"
)
CONTRACT = CONTRACTS / "restore-sanitizer-transaction-plan-contract.json"
KEY_VERSION = "staging-sanitizer-transform-v1"
FAILURE = plan.SanitizerTransactionPlanFailure


def _planner(
    *times: datetime,
    key: bytes = KEY,
    key_version: str = KEY_VERSION,
    contract_bytes: bytes | None = None,
    clock: Callable[[], datetime] | None = None,
) -> plan.SanitizerSerializableTransactionPlanner:
    values = iter(times)
    return plan.SanitizerSerializableTransactionPlanner._from_reviewed_configuration(
        clock=clock or ((lambda: next(values)) if times else (lambda: PREFLIGHT_AT)),
        inventory_hmac_key=INVENTORY_KEY,
        inventory_hmac_key_version=inventory.SUPPORTED_HMAC_KEY_VERSION,
        transform_key=key,
        transform_key_version=key_version,
        sanitizer_policy_bytes=POLICY.read_bytes(),
        inventory_policy_bytes=INVENTORY_POLICY.read_bytes(),
        data_contract_bytes=DATA_CONTRACT.read_bytes(),
        source_snapshot_contract_bytes=SOURCE_CONTRACT.read_bytes(),
        post_projection_contract_bytes=POST_CONTRACT.read_bytes(),
        transaction_plan_contract_bytes=contract_bytes or CONTRACT.read_bytes(),
    )


@pytest.fixture(scope="module")
def artifacts(authenticated_case: SimpleNamespace) -> SimpleNamespace:
    snapshot = _snapshot_from_rows(authenticated_case, _source_rows())
    candidate = _engine().transform(authenticated_source_snapshot=snapshot)
    authority = projection.SanitizerFixedCatalogPostProjectionAuthenticator._from_reviewed_configuration(
        clock=lambda: PREFLIGHT_AT,
        inventory_hmac_key=INVENTORY_KEY,
        inventory_hmac_key_version=inventory.SUPPORTED_HMAC_KEY_VERSION,
        transform_key=KEY,
        transform_key_version=KEY_VERSION,
        sanitizer_policy_bytes=POLICY.read_bytes(),
        inventory_policy_bytes=INVENTORY_POLICY.read_bytes(),
        data_contract_bytes=DATA_CONTRACT.read_bytes(),
        source_snapshot_contract_bytes=SOURCE_CONTRACT.read_bytes(),
        post_projection_contract_bytes=POST_CONTRACT.read_bytes(),
    ).authenticate(
        authenticated_source_snapshot=snapshot, sanitizer_candidate=candidate
    )
    return SimpleNamespace(
        snapshot=snapshot,
        candidate=candidate,
        authority=authority,
        evidence=authenticated_case.authenticated,
    )


@pytest.fixture(scope="module")
def result(artifacts: SimpleNamespace) -> SimpleNamespace:
    planner = _planner()
    value = planner.prepare(
        authenticated_post_projection=artifacts.authority,
        sanitizer_candidate=artifacts.candidate,
    )
    return SimpleNamespace(planner=planner, value=value)


def _clone_slots(value: Any, **changes: Any) -> Any:
    copied = object.__new__(type(value))
    for name in type(value).__slots__:
        object.__setattr__(copied, name, getattr(value, name))
    for name, item in changes.items():
        object.__setattr__(copied, name, item)
    return copied


def _operations(value: Any) -> tuple[Any, ...]:
    return (value._truncate, *value._inserts)  # noqa: SLF001


def _handoff(result: SimpleNamespace) -> Any:
    return result.planner._copy_verified_plan_for_execution_handoff(  # noqa: SLF001
        result.value
    )


def _validate_handoff(result: SimpleNamespace, value: Any, now: datetime) -> None:
    result.planner._validate_verified_plan_execution_handoff_at_current_time(  # noqa: SLF001
        value, now=now
    )


def test_exact_structured_plan_and_claims(
    result: SimpleNamespace, artifacts: SimpleNamespace
) -> None:
    value = result.value
    assert (
        value.schema_profile,
        value.schema_revision,
        value.schema_descriptor_sha256,
    ) == ("pre", "89c31f0ae275", plan.SCHEMA_DESCRIPTOR_SHA256)
    assert (
        value.transaction_isolation,
        value.transaction_read_only,
        value.connection_autocommit,
    ) == ("SERIALIZABLE", False, False)
    assert (
        value.control_table,
        value.control_table_exact_rows,
        value.lock_control_table,
    ) == ("alembic_version", 1, True)
    assert (
        value.verify_revision_under_lock is True
        and value.unexpected_on_truncate_triggers == "reject"
    )
    assert (value.synchronous_commit, value.retry_permitted, value.operation_count) == (
        "on",
        False,
        20,
    )
    assert (
        value.truncate_table_count,
        value.insert_table_count,
        value.insert_column_count,
    ) == (62, 19, 204)
    assert (
        value.discard_table_count,
        value.discard_column_count,
        value.required_empty_table_count,
    ) == (10, 139, 33)
    assert (
        value.planned_insert_row_count == artifacts.candidate.candidate_row_count == 19
    )
    assert all(getattr(value, name) is True for name in plan._POSITIVE_CLAIMS)  # noqa: SLF001
    assert all(getattr(value, name) is False for name in plan._NEGATIVE_CLAIMS)  # noqa: SLF001
    result.planner.validate(value)


def test_truncate_and_insert_operations_are_exact(
    result: SimpleNamespace, artifacts: SimpleNamespace
) -> None:
    truncate, inserts = result.value._truncate, result.value._inserts  # noqa: SLF001
    assert len(truncate._tables) == 62 and truncate._tables == tuple(
        sorted(truncate._tables)
    )  # noqa: SLF001
    assert "alembic_version" not in truncate._tables  # noqa: SLF001
    assert (
        truncate._cascade,
        truncate._restart_identity,
        truncate._triggers_disabled,
    ) == (False, False, False)  # noqa: SLF001
    assert tuple(item._table for item in inserts) == plan.INSERT_TABLE_ORDER  # noqa: SLF001
    assert all(
        item._whole_table_batch is True and item._on_conflict_permitted is False
        for item in inserts
    )  # noqa: SLF001
    assert sum(len(item._columns) for item in inserts) == 204  # noqa: SLF001
    candidate = dict(artifacts.candidate._tables)  # noqa: SLF001
    for item in inserts:
        expected = tuple(
            tuple(row[column] for column in item._columns)
            for row in candidate[item._table]
        )  # noqa: SLF001
        assert item._values == expected  # noqa: SLF001
    people = inserts[plan.INSERT_TABLE_ORDER.index("authority_person")]
    assert people._whole_table_batch is True and len(people._values) == len(
        candidate["authority_person"]
    )  # noqa: SLF001


def test_contract_is_hash_pinned_closed_world_and_planning_only() -> None:
    raw, document = CONTRACT.read_bytes(), json.loads(CONTRACT.read_bytes())
    assert hashlib.sha256(raw).hexdigest() == plan.TRANSACTION_PLAN_CONTRACT_SHA256
    status = document["status"]
    assert document["closed_world"] is status["planning_only"] is True
    assert status["activation_blockers"] == ["transaction_adapter_unimplemented"]
    assert status["existing_executor_preflight_remains_terminal"] is True
    schema = document["schema"]
    assert (schema["profile"], schema["revision"], schema["descriptor_sha256"]) == (
        "pre",
        "89c31f0ae275",
        plan.SCHEMA_DESCRIPTOR_SHA256,
    )
    assert tuple(
        schema[name]
        for name in (
            "application_table_count",
            "rewrite_table_count",
            "discard_table_count",
            "required_empty_table_count",
        )
    ) == (62, 19, 10, 33)
    assert document["transaction"]["inserts"]["table_order"] == list(
        plan.INSERT_TABLE_ORDER
    )
    assert (
        document["authentication"]["candidate_copy_domain"]
        == executor.CANDIDATE_TRANSACTION_COPY_DOMAIN
    )
    handoff = document["execution_handoff"]
    assert handoff["kind"] == "sealed-verified-transaction-plan-execution-handoff-v1"
    assert all(
        handoff[name] is True
        for name in "implemented producer_owned exact_type_and_process_local_seal_required fresh_operation_objects_required deep_copied_nested_operation_values_required post_projection_authority_retained evidence_ancestry_retained_transitively authenticated_target_context_retained_transitively start_and_end_plan_reauthentication_required current_time_handoff_validation_required".split()
    )
    assert all(
        handoff[name] is False
        for name in "original_plan_retained original_operations_retained additional_or_caller_supplied_target_role_runtime_configuration_retained credentials_retained connectors_retained sql_text_retained callbacks_retained hmac_keys_retained".split()
    )
    assert handoff["authority"] == plan._NEGATIVE_CLAIMS  # noqa: SLF001
    assert document["authentication"]["execution_handoff_copy_key"] == "transform_key"
    assert (
        document["authentication"]["execution_handoff_copy_domain"]
        == plan.TRANSACTION_PLAN_EXECUTION_HANDOFF_COPY_DOMAIN
    )
    assert (
        document["authentication"]["constant_time_execution_handoff_proof_comparison"]
        is True
    )
    assert (
        len(
            {
                executor.CANDIDATE_PROOF_DOMAIN,
                executor.CANDIDATE_COPY_DOMAIN,
                executor.CANDIDATE_TRANSACTION_COPY_DOMAIN,
                projection.PROJECTION_DOMAIN,
                projection.AUTHORITY_DOMAIN,
                plan.OPERATION_PROJECTION_DOMAIN,
                plan.AUTHORITY_DOMAIN,
                plan.TRANSACTION_PLAN_EXECUTION_HANDOFF_COPY_DOMAIN,
            }
        )
        == 8
    )


def test_upstream_contracts_and_public_preflight_remain_terminal(
    artifacts: SimpleNamespace,
) -> None:
    paths = (POLICY, INVENTORY_POLICY, DATA_CONTRACT, SOURCE_CONTRACT, POST_CONTRACT)
    expected = (
        plan.SANITIZER_POLICY_SHA256,
        plan.INVENTORY_POLICY_SHA256,
        plan.DATA_CONTRACT_SHA256,
        plan.SOURCE_SNAPSHOT_CONTRACT_SHA256,
        plan.POST_PROJECTION_CONTRACT_SHA256,
    )
    assert (
        tuple(hashlib.sha256(path.read_bytes()).hexdigest() for path in paths)
        == expected
    )
    preflight = public_executor.SanitizerExecutorPreflight._from_reviewed_configuration(
        clock=lambda: PREFLIGHT_AT
    )
    with pytest.raises(
        public_executor.SanitizerExecutorFailure,
        match=f"^{plan.ACTIVATION_FAILURE}$",
    ):
        preflight.preflight(authenticated_sanitizer_evidence=artifacts.evidence)


def test_producer_owned_full_candidate_copy_is_authenticated_and_sealed(
    artifacts: SimpleNamespace,
) -> None:
    engine = _engine()
    copied = engine._copy_verified_candidate_for_transaction_plan(
        artifacts.candidate, now=PREFLIGHT_AT
    )  # noqa: SLF001
    original = dict(artifacts.candidate._tables)  # noqa: SLF001
    assert len(copied._tables) == 29 and tuple(
        name for name, _rows in copied._tables
    ) == tuple(sorted(original))  # noqa: SLF001
    assert all(
        not copied_rows or copied_rows[0] is not original[name][0]
        for name, copied_rows in copied._tables
    )  # noqa: SLF001
    assert sum(len(rows) for _name, rows in copied._tables) == 19  # noqa: SLF001
    engine._validate_verified_candidate_transaction_copy_at_current_time(
        copied, now=PREFLIGHT_AT
    )  # noqa: SLF001
    with pytest.raises(
        executor.SanitizerDataContractExecutorFailure, match="output_invalid"
    ):
        pickle.dumps(copied)
    object.__setattr__(copied, "_proof_sha256", "0" * 64)
    with pytest.raises(
        executor.SanitizerDataContractExecutorFailure, match="candidate_invalid"
    ):
        engine._validate_verified_candidate_transaction_copy_at_current_time(
            copied, now=PREFLIGHT_AT
        )  # noqa: SLF001


@pytest.mark.parametrize("target", ["plan", "truncate", "insert", "planner"])
def test_outputs_are_opaque_nonserializable_and_nonrevealing(
    result: SimpleNamespace, target: str
) -> None:
    item = {
        "plan": result.value,
        "truncate": result.value._truncate,
        "insert": result.value._inserts[0],
        "planner": result.planner,
    }[target]  # noqa: SLF001
    assert "value" not in repr(item).lower() and "person@example" not in repr(item)
    with pytest.raises(FAILURE):
        pickle.dumps(item)
    with pytest.raises(FAILURE):
        item.unapproved = True
    if target == "plan":
        slots = set(plan.AuthenticatedSanitizerTransactionPlan.__slots__)
        assert not slots & {
            "_candidate",
            "_candidate_copy",
            "_source_snapshot",
            "_key",
            "_raw_frames",
            "_sql",
        }
        assert item._authenticated_post_projection is not None  # noqa: SLF001


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("_proof_sha256", "0" * 64),
        ("_operation_projection_sha256", "0" * 64),
        ("planned_insert_row_count", 0),
    ],
)
def test_tampered_plan_is_rejected(
    result: SimpleNamespace, field: str, replacement: Any
) -> None:
    original = getattr(result.value, field)
    object.__setattr__(result.value, field, replacement)
    try:
        with pytest.raises(FAILURE, match="^transaction_plan.output_invalid$"):
            _planner(PREFLIGHT_AT).validate(result.value)
    finally:
        object.__setattr__(result.value, field, original)


def test_authority_and_candidate_lineages_cannot_be_mixed(
    authenticated_case: SimpleNamespace, artifacts: SimpleNamespace
) -> None:
    other_snapshot = _snapshot_from_rows(authenticated_case, _source_rows())
    other_candidate = _engine().transform(authenticated_source_snapshot=other_snapshot)
    with pytest.raises(FAILURE, match="^transaction_plan.authority_invalid$"):
        _planner().prepare(
            authenticated_post_projection=artifacts.authority,
            sanitizer_candidate=other_candidate,
        )


@pytest.mark.parametrize(
    ("make", "code"),
    [
        (
            lambda: _planner(contract_bytes=CONTRACT.read_bytes() + b"\n"),
            "policy_invalid",
        ),
        (lambda: _planner(key_version="UPPER"), "configuration_invalid"),
        (lambda: _planner(clock=lambda: datetime(2026, 9, 5)), "clock_invalid"),
    ],
)
def test_bounded_configuration_and_clock_refusals(
    make: Callable[[], Any], code: str, artifacts: SimpleNamespace
) -> None:
    with pytest.raises(FAILURE, match=f"^transaction_plan.{code}$"):
        value = make()
        value.prepare(
            authenticated_post_projection=artifacts.authority,
            sanitizer_candidate=artifacts.candidate,
        )


def test_regressed_and_expired_clocks_are_rejected(artifacts: SimpleNamespace) -> None:
    with pytest.raises(FAILURE, match="^transaction_plan.clock_regressed$"):
        _planner(PREFLIGHT_AT, PREFLIGHT_AT - timedelta(seconds=1)).prepare(
            authenticated_post_projection=artifacts.authority,
            sanitizer_candidate=artifacts.candidate,
        )
    with pytest.raises(FAILURE, match="^transaction_plan.target_expired$"):
        _planner(
            PREFLIGHT_AT + timedelta(days=1), PREFLIGHT_AT + timedelta(days=1)
        ).prepare(
            authenticated_post_projection=artifacts.authority,
            sanitizer_candidate=artifacts.candidate,
        )


def test_candidate_toctou_mutation_is_rejected(
    monkeypatch: pytest.MonkeyPatch, artifacts: SimpleNamespace
) -> None:
    original = executor.SanitizerDataContractExecutor._copy_verified_candidate_for_transaction_plan
    calls = 0

    def mutate(self: Any, candidate: Any, *, now: datetime) -> Any:
        nonlocal calls
        calls += 1
        if calls == 2:
            object.__setattr__(candidate, "_proof_sha256", "0" * 64)
        return original(self, candidate, now=now)

    monkeypatch.setattr(
        executor.SanitizerDataContractExecutor,
        "_copy_verified_candidate_for_transaction_plan",
        mutate,
    )
    saved = artifacts.candidate._proof_sha256  # noqa: SLF001
    try:
        with pytest.raises(FAILURE, match="^transaction_plan.candidate_invalid$"):
            _planner().prepare(
                authenticated_post_projection=artifacts.authority,
                sanitizer_candidate=artifacts.candidate,
            )
    finally:
        object.__setattr__(artifacts.candidate, "_proof_sha256", saved)


def test_final_copy_clock_advance_cannot_return_expired_plan(
    monkeypatch: pytest.MonkeyPatch, artifacts: SimpleNamespace
) -> None:
    current, calls = [PREFLIGHT_AT], 0
    method = "_copy_verified_candidate_for_transaction_plan"
    original = getattr(executor.SanitizerDataContractExecutor, method)

    def advance(self: Any, candidate: Any, *, now: datetime) -> Any:
        nonlocal calls
        calls += 1
        copied = original(self, candidate, now=now)
        if calls == 2:
            current[0] = PREFLIGHT_AT + timedelta(days=1)
        return copied

    monkeypatch.setattr(executor.SanitizerDataContractExecutor, method, advance)
    with pytest.raises(FAILURE, match="^transaction_plan.target_expired$"):
        _planner(clock=lambda: current[0]).prepare(
            authenticated_post_projection=artifacts.authority,
            sanitizer_candidate=artifacts.candidate,
        )


def test_copy_only_row_mutation_cannot_gain_authentication(
    monkeypatch: pytest.MonkeyPatch, artifacts: SimpleNamespace
) -> None:
    original, changed = executor._copy_candidate_value, [False]  # noqa: SLF001

    def mutate(value: Any) -> Any:
        copied = original(value)
        if type(copied) is executor._FrozenMap and "actor_kind" in copied:  # noqa: SLF001
            changed[0] = True
            return executor._FrozenMap(
                tuple(
                    (name, "tampered" if name == "actor_kind" else item)
                    for name, item in copied._items
                )
            )  # noqa: SLF001
        return copied

    monkeypatch.setattr(executor, "_copy_candidate_value", mutate)
    with pytest.raises(FAILURE, match="^transaction_plan.candidate_invalid$"):
        _planner().prepare(
            authenticated_post_projection=artifacts.authority,
            sanitizer_candidate=artifacts.candidate,
        )
    assert changed == [True]


def test_planner_has_no_candidate_introspection_or_execution_surface() -> None:
    source = inspect.getsource(plan.SanitizerSerializableTransactionPlanner)
    assert "candidate._" not in source and "sanitizer_candidate._" not in source
    tree = ast.parse(SCRIPT.read_text())
    imports = {
        node.names[0].name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom)) and node.names
    }
    assert not imports & set(
        "asyncpg azure psycopg requests socket sqlalchemy subprocess".split()
    )
    strings = {
        node.value.lower()
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    forbidden = "truncate table|insert into|on conflict|alter table|create table|drop table".split(
        "|"
    )
    assert not any(token in value for token in forbidden for value in strings)


def test_execution_handoff_is_a_deep_nonaliased_exact_plan_copy(
    result: SimpleNamespace,
) -> None:
    original, copied = result.value, _handoff(result)
    _validate_handoff(result, copied, PREFLIGHT_AT)
    assert type(copied) is plan._VerifiedTransactionPlanExecutionHandoff  # noqa: SLF001
    assert copied.copied_at == PREFLIGHT_AT
    public = (
        *plan._PUBLIC_FIXED,
        "planned_insert_row_count",
        "transform_key_version",
        "planned_at",
    )  # noqa: SLF001
    assert all(getattr(copied, name) == getattr(original, name) for name in public)
    assert copied._authenticated_post_projection is (  # noqa: SLF001
        original._authenticated_post_projection  # noqa: SLF001
    )
    lineage = "_candidate_dataset_identity _candidate_dataset_proof_sha256 _candidate_dataset_contract_sha256 _candidate_copy_proof_sha256 _source_snapshot_identity _source_snapshot_proof_sha256 _source_snapshot_contract_sha256 _operation_projection_sha256 _contract_sha256".split()
    assert tuple(getattr(copied, name) for name in lineage) == tuple(
        getattr(original, name) for name in lineage
    )
    original_operations, copied_operations = _operations(original), _operations(copied)
    assert plan._operation_material(copied._truncate, copied._inserts) == (  # noqa: SLF001
        plan._operation_material(original._truncate, original._inserts)  # noqa: SLF001
    )
    assert len(original_operations) == len(copied_operations) == 20
    assert all(
        copied_operation is not original_operation
        for original_operation, copied_operation in zip(
            original_operations, copied_operations, strict=True
        )
    )
    expected_metadata = (
        id(original),
        id(type(original)),
        id(original._seal),  # noqa: SLF001
        original._proof_sha256,  # noqa: SLF001
        tuple(id(item) for item in original_operations),
        tuple(id(type(item)) for item in original_operations),
        tuple(id(item._seal) for item in original_operations),  # noqa: SLF001
    )
    metadata = "_transaction_plan_identity _transaction_plan_type_identity _transaction_plan_seal_identity _transaction_plan_proof_sha256 _transaction_plan_operation_identities _transaction_plan_operation_type_identities _transaction_plan_operation_seal_identities".split()
    assert tuple(getattr(copied, name) for name in metadata) == expected_metadata
    original_map = next(
        item
        for operation in original._inserts  # noqa: SLF001
        for row in operation._values  # noqa: SLF001
        for item in row
        if type(item) is executor._FrozenMap  # noqa: SLF001
    )
    copied_map = next(
        item
        for operation in copied._inserts  # noqa: SLF001
        for row in operation._values  # noqa: SLF001
        for item in row
        if type(item) is executor._FrozenMap  # noqa: SLF001
    )
    assert copied_map == original_map and copied_map is not original_map


def test_execution_handoff_is_opaque_exact_and_nonserializable(
    result: SimpleNamespace,
) -> None:
    copied = _handoff(result)
    rendered = repr(copied)
    assert "sealed" in rendered and "person@example" not in rendered
    with pytest.raises(FAILURE, match="^transaction_plan.output_invalid$"):
        plan._VerifiedTransactionPlanExecutionHandoff()  # noqa: SLF001
    with pytest.raises(FAILURE, match="^transaction_plan.output_invalid$"):
        pickle.dumps(copied)
    with pytest.raises(FAILURE, match="^transaction_plan.output_invalid$"):
        copied.unapproved = True

    class HandoffSubclass(plan._VerifiedTransactionPlanExecutionHandoff):  # noqa: SLF001
        pass

    forged = object.__new__(HandoffSubclass)
    with pytest.raises(FAILURE, match="^transaction_plan.output_invalid$"):
        _validate_handoff(result, forged, PREFLIGHT_AT)


def test_execution_handoff_rejects_scalar_proof_domain_and_operation_tampering(
    result: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    copied = _handoff(result)
    first_insert = copied._inserts[0]  # noqa: SLF001
    for owner, field, replacement in (
        (copied, "planned_insert_row_count", copied.planned_insert_row_count + 1),
        (copied, "_proof_sha256", "0" * 64),
        (first_insert, "_whole_table_batch", False),
    ):
        original = getattr(owner, field)
        object.__setattr__(owner, field, replacement)
        try:
            with pytest.raises(FAILURE, match="^transaction_plan.output_invalid$"):
                _validate_handoff(result, copied, PREFLIGHT_AT)
        finally:
            object.__setattr__(owner, field, original)

    with monkeypatch.context() as scoped:
        scoped.setattr(
            plan,
            "TRANSACTION_PLAN_EXECUTION_HANDOFF_COPY_DOMAIN",
            plan.AUTHORITY_DOMAIN,
        )
        with pytest.raises(FAILURE, match="^transaction_plan.output_invalid$"):
            _validate_handoff(result, copied, PREFLIGHT_AT)


def test_execution_handoff_rejects_valid_plan_substitution_and_input_mutation(
    result: SimpleNamespace,
    artifacts: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    other = _planner().prepare(
        authenticated_post_projection=artifacts.authority,
        sanitizer_candidate=artifacts.candidate,
    )
    copied = _handoff(result)
    other_operations = _operations(other)
    replacements = {
        "_transaction_plan_identity": id(other),
        "_transaction_plan_proof_sha256": other._proof_sha256,  # noqa: SLF001
        "_transaction_plan_operation_identities": tuple(
            id(item) for item in other_operations
        ),
    }
    originals = {name: getattr(copied, name) for name in replacements}
    try:
        for name, item in replacements.items():
            object.__setattr__(copied, name, item)
        with pytest.raises(FAILURE, match="^transaction_plan.output_invalid$"):
            _validate_handoff(result, copied, PREFLIGHT_AT)
    finally:
        for name, item in originals.items():
            object.__setattr__(copied, name, item)

    planner_type = plan.SanitizerSerializableTransactionPlanner
    copy_method = planner_type._copy_operations_for_execution_handoff

    def mutate_proof(self: Any, value: Any) -> Any:
        operations = copy_method(self, value)
        object.__setattr__(value, "_proof_sha256", "0" * 64)
        return operations

    def replace_equal_operation(self: Any, value: Any) -> Any:
        operations = copy_method(self, value)
        equal_insert = _clone_slots(value._inserts[0])  # noqa: SLF001
        object.__setattr__(value, "_inserts", (equal_insert, *value._inserts[1:]))  # noqa: SLF001
        return operations

    original = result.value._proof_sha256, result.value._inserts  # noqa: SLF001
    for mutation in (mutate_proof, replace_equal_operation):
        with monkeypatch.context() as scoped:
            scoped.setattr(planner_type, copy_method.__name__, mutation)
            try:
                with pytest.raises(FAILURE, match="^transaction_plan.output_invalid$"):
                    _handoff(result)
            finally:
                object.__setattr__(result.value, "_proof_sha256", original[0])
                object.__setattr__(result.value, "_inserts", original[1])


def test_execution_handoff_rejects_copy_only_nested_value_mutation(
    result: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    planner_type = plan.SanitizerSerializableTransactionPlanner
    copy_method = planner_type._copy_operations_for_execution_handoff
    changed = [False]

    def corrupt_copy(self: Any, value: Any) -> Any:
        truncate, inserts = copy_method(self, value)
        for operation in inserts:
            for row_index, row in enumerate(operation._values):  # noqa: SLF001
                for value_index, item in enumerate(row):
                    if type(item) is executor._FrozenMap:  # noqa: SLF001
                        replacement = executor._FrozenMap(  # noqa: SLF001
                            (*item._items, ("copy_only", "tampered"))  # noqa: SLF001
                        )
                        new_row = (
                            *row[:value_index],
                            replacement,
                            *row[value_index + 1 :],
                        )
                        new_values = (
                            *operation._values[:row_index],  # noqa: SLF001
                            new_row,
                            *operation._values[row_index + 1 :],  # noqa: SLF001
                        )
                        object.__setattr__(operation, "_values", new_values)
                        changed[0] = True
                        return truncate, inserts
        raise AssertionError("fixture has no nested frozen value")

    monkeypatch.setattr(
        planner_type,
        "_copy_operations_for_execution_handoff",
        corrupt_copy,
    )
    with pytest.raises(FAILURE, match="^transaction_plan.output_invalid$"):
        _handoff(result)
    assert changed == [True]


def test_execution_handoff_rejects_clock_regression_expiry_and_invalid_shape(
    result: SimpleNamespace,
) -> None:
    copied = _handoff(result)
    with pytest.raises(FAILURE, match="^transaction_plan.clock_invalid$"):
        _validate_handoff(result, copied, datetime(2026, 9, 5))
    with pytest.raises(FAILURE, match="^transaction_plan.clock_regressed$"):
        _validate_handoff(result, copied, PREFLIGHT_AT - timedelta(seconds=1))
    with pytest.raises(FAILURE, match="^transaction_plan.target_expired$"):
        _validate_handoff(result, copied, PREFLIGHT_AT + timedelta(days=1))
    with pytest.raises(FAILURE, match="^transaction_plan.clock_regressed$"):
        _planner(
            PREFLIGHT_AT,
            PREFLIGHT_AT - timedelta(seconds=1),
        )._copy_verified_plan_for_execution_handoff(result.value)  # noqa: SLF001
    with pytest.raises(FAILURE, match="^transaction_plan.target_expired$"):
        _planner(
            PREFLIGHT_AT,
            PREFLIGHT_AT,
            PREFLIGHT_AT + timedelta(days=1),
        )._copy_verified_plan_for_execution_handoff(result.value)  # noqa: SLF001
    calls, original_proof = [0], result.value._proof_sha256  # noqa: SLF001

    def mutate_at_final_clock() -> datetime:
        calls[0] += 1
        if calls[0] == 3:
            object.__setattr__(result.value, "_proof_sha256", "0" * 64)
        return PREFLIGHT_AT

    try:
        with pytest.raises(FAILURE, match="^transaction_plan.output_invalid$"):
            _planner(
                clock=mutate_at_final_clock
            )._copy_verified_plan_for_execution_handoff(  # noqa: SLF001
                result.value
            )
    finally:
        object.__setattr__(result.value, "_proof_sha256", original_proof)
    assert calls == [3]


def test_execution_handoff_rejects_hostile_shapes_without_callbacks(
    result: SimpleNamespace,
) -> None:
    callbacks: list[str] = []

    class HostileTuple(tuple[Any, ...]):
        def __iter__(self) -> Any:
            callbacks.append("iterated")
            raise AssertionError

    class HostileStr(str):
        def __eq__(self, other: object) -> bool:
            del other
            callbacks.append("compared")
            raise AssertionError

        def __ne__(self, other: object) -> bool:
            del other
            callbacks.append("compared")
            raise AssertionError

    class HostilePlan:
        def __getattr__(self, name: str) -> Any:
            callbacks.append(name)
            raise AssertionError

    copied = _handoff(result)
    for field, replacement in (
        ("_inserts", HostileTuple(copied._inserts)),  # noqa: SLF001
        (
            "_transaction_plan_proof_sha256",
            HostileStr(copied._transaction_plan_proof_sha256),  # noqa: SLF001
        ),
    ):
        original = getattr(copied, field)
        object.__setattr__(copied, field, replacement)
        try:
            with pytest.raises(FAILURE, match="^transaction_plan.output_invalid$"):
                _validate_handoff(result, copied, PREFLIGHT_AT)
        finally:
            object.__setattr__(copied, field, original)
    with pytest.raises(FAILURE, match="^transaction_plan.output_invalid$"):
        result.planner._copy_verified_plan_for_execution_handoff(  # type: ignore[arg-type]  # noqa: SLF001
            HostilePlan()
        )
    assert callbacks == []


def test_execution_handoff_retains_no_direct_execution_config_or_sensitive_inputs(
    result: SimpleNamespace,
) -> None:
    copied = _handoff(result)
    slots = set(type(copied).__slots__)
    assert not hasattr(copied, "__dict__")
    forbidden = "_transaction_plan _plan _candidate _candidate_dataset _candidate_copy _source_snapshot _inventory_hmac_key _transform_key _key _dsn _sql _connector _connection _callback _clock _target _target_binding _role _database_role _runtime".split()
    assert slots.isdisjoint(forbidden)
    assert copied._authenticated_post_projection is not None  # noqa: SLF001
    assert all(
        getattr(copied, name) is False
        for name in "database_connection_authority database_mutation_authority executable runtime_authority transaction_executed workflow_integrated".split()
    )
