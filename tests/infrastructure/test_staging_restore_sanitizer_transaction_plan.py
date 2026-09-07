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
            }
        )
        == 7
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
