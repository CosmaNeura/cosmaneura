from __future__ import annotations

import ast
import copy
import hashlib
import inspect
import json
import pickle
import subprocess
import sys
import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from typing import Any, cast

import pytest
import typeid
from typeid import TypeID

from scripts import staging_restore_sanitizer as sanitizer
from scripts import staging_restore_sanitizer_data_contract_executor as executor
from scripts import staging_restore_sanitizer_executor as public_executor
from scripts import staging_restore_sanitizer_source_snapshot as source_snapshot
from scripts import staging_synthetic_inventory as inventory

from tests.infrastructure.test_staging_restore_sanitizer_executor import (
    real_case as case,
)
from tests.infrastructure.test_staging_restore_sanitizer_source_snapshot import (
    KEY as INVENTORY_KEY,
)
from tests.infrastructure.test_staging_restore_sanitizer_source_snapshot import (
    _authenticate as authenticate_source_snapshot,
)
from tests.infrastructure.test_staging_restore_sanitizer_source_snapshot import (
    authenticated_case as snapshot_case,
)

real_case = case
source_snapshot_case = snapshot_case

ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "scripts/staging_restore_sanitizer_data_contract_executor.py"
CONTRACTS = ROOT / "contracts/staging-rehearsal/v1"
POLICY = CONTRACTS / "restore-sanitizer-policy.json"
INVENTORY_POLICY = CONTRACTS / "synthetic-data-policy.json"
DATA_CONTRACT = CONTRACTS / "restore-sanitizer-data-contract.json"
SOURCE_SNAPSHOT_CONTRACT = CONTRACTS / "restore-sanitizer-source-snapshot-contract.json"
SITE_PACKAGES = Path(typeid.__file__ or "").parents[1]
KEY = bytes(range(32))
OTHER_KEY = bytes(reversed(range(32)))
KEY_VERSION = "staging-sanitizer-transform-v1"
PREFLIGHT_AT = datetime(2026, 9, 5, 12, 10, 50, tzinfo=UTC)
SOURCE_ID = uuid.UUID("01890f4c-7b00-7000-8000-000000000001")
OTHER_ID = uuid.UUID("01890f4c-7b00-7000-8000-000000000002")
FAILURE = executor.SanitizerDataContractExecutorFailure


def _engine(
    key: bytes = KEY,
    clock: Callable[[], datetime] | None = None,
    *,
    inventory_key: bytes = INVENTORY_KEY,
) -> Any:
    return executor.SanitizerDataContractExecutor._from_reviewed_configuration(
        clock=clock or (lambda: PREFLIGHT_AT),
        transform_key=key,
        transform_key_version=KEY_VERSION,
        inventory_hmac_key=inventory_key,
        inventory_hmac_key_version=inventory.SUPPORTED_HMAC_KEY_VERSION,
        sanitizer_policy_bytes=POLICY.read_bytes(),
        inventory_policy_bytes=INVENTORY_POLICY.read_bytes(),
        data_contract_bytes=DATA_CONTRACT.read_bytes(),
        source_snapshot_contract_bytes=SOURCE_SNAPSHOT_CONTRACT.read_bytes(),
    )


def _value(data_type: str) -> Any:
    values = {
        "uuid": SOURCE_ID,
        "integer": 1,
        "bigint": 1,
        "boolean": False,
        "double precision": 1.5,
        "date": date(2026, 1, 2),
        "timestamp with time zone": datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
        "timestamp without time zone": datetime(2026, 1, 2, 3, 4, 5),
        "json": {"nested": ["approved", 1, True, None], "second": "kept"},
        "jsonb": {"nested": ["approved", 1, True, None], "second": "kept"},
        "doctorrole": "normal",
        "planordering": "patient_then_doctor",
        "pronounstyle": "infer_from_transcript",
        "subjectivesource": "patient_only",
        "terminology": "doctor_patient",
    }
    return values.get(data_type, "value")


def _source_rows() -> dict[str, tuple[dict[str, Any], ...]]:
    policy = json.loads(INVENTORY_POLICY.read_bytes())
    schemas = inventory._expected_tables(policy, "pre")  # noqa: SLF001
    rows = {
        table_name: (
            {name: _value(data_type) for name, data_type, _required in spec["columns"]},
        )
        for table_name, spec in schemas.items()
        if spec["disposition"] != "required_empty"
    }
    rows["authority_actor_external_identity"][0].update(
        issuer="https://clerk.example.invalid", subject="user_same-identity"
    )
    rows["authority_person_binding"][0].update(
        binding_kind="provider_record",
        binding_ref="appointment_01h47mryr0e008000000000001",
    )
    rows["doctor"][0].update(
        api_key=None, clerk_id="user_same-identity", email="person@example.com"
    )
    sandbox = dict(adapter="athena", environment="preview", synthetic_only=True)
    sandbox["practice_ref"] = "195900"
    for name in ("ehr_sandbox_patient_designation", "ehr_sandbox_record_mapping"):
        rows[name][0].update(sandbox)
    rows["practice"][0].update(email_domain="example.com", join_code="OLDCODE12345")
    rows["provider_organization"][0].update(
        access_code_salt="a" * 32,
        access_code_digest="b" * 64,
        email_domain="example.com",
    )
    rows["synthetic_preview_appointment_fixture"][0].update(
        fixture_version="synthetic_preview_labs_v1", synthetic_only=True
    )
    rows["practice"][0].update(
        dict.fromkeys(
            "stripe_customer_id stripe_subscription_id subscription_status stripe_last_event_created_at stripe_last_event_id stripe_checkout_session_id stripe_checkout_session_url stripe_checkout_session_expires_at stripe_quantity_sync_target stripe_quantity_sync_error stripe_quantity_synced_at".split()
        )
    )
    return rows


def _tables(candidate: Any) -> dict[str, tuple[Any, ...]]:
    return {name: candidate._rows_for_table(name) for name, _rows in candidate._tables}


def _snapshot_value(value: Any) -> Any:
    if type(value) is dict:
        return source_snapshot._FrozenMap(  # noqa: SLF001
            tuple((name, _snapshot_value(item)) for name, item in value.items())
        )
    if type(value) in {list, tuple}:
        return tuple(_snapshot_value(item) for item in value)
    return value


def _snapshot_from_rows(
    case: Any,
    rows: dict[str, tuple[dict[str, Any], ...]],
    *,
    inventory_key: bytes = INVENTORY_KEY,
) -> Any:
    tables = tuple(
        (
            table_name,
            tuple(_snapshot_value(row) for row in rows[table_name]),
        )
        for table_name in sorted(rows)
    )
    proofs = tuple(
        (
            table_name,
            tuple(
                hashlib.sha256(f"{table_name}:{index}".encode()).hexdigest()
                for index, _row in enumerate(table_rows)
            ),
        )
        for table_name, table_rows in tables
    )
    return source_snapshot._mint_snapshot(  # noqa: SLF001
        key=inventory_key,
        evidence=case.authenticated,
        tables=tables,
        fragment_proofs=proofs,
        authenticated_at=PREFLIGHT_AT,
    )


def _transformed(
    snapshot_case: Any,
    key: bytes = KEY,
    rows: dict[str, tuple[dict[str, Any], ...]] | None = None,
) -> executor.SanitizerCandidateDataset:
    authenticated = (
        snapshot_case.authenticated_source_snapshot
        if rows is None
        else _snapshot_from_rows(snapshot_case, rows)
    )
    return _engine(key).transform(
        authenticated_source_snapshot=authenticated,
    )


def _clone(value: Any, **changes: Any) -> Any:
    result = object.__new__(type(value))
    for field in value.__dataclass_fields__:
        object.__setattr__(result, field, getattr(value, field))
    for field, item in changes.items():
        object.__setattr__(result, field, item)
    return result


def _candidate_refuses(candidate: Any, engine: Any = None) -> None:
    with pytest.raises(FAILURE, match="data_contract_executor.candidate_invalid"):
        (engine or _engine()).validate_candidate(candidate)


def _module_trace(error: BaseException) -> str:
    rendered, seen = [], set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        frame = current.__traceback__
        while frame is not None:
            if frame.tb_frame.f_globals.get("__name__") == executor.__name__:
                rendered.append(repr(frame.tb_frame.f_locals))
            frame = frame.tb_next
        current = current.__cause__ or current.__context__
    return "".join(rendered)


@pytest.fixture
def fast_case(source_snapshot_case: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setattr(
        executor.SanitizerDataContractExecutor,
        "_reauthenticate",
        lambda self, evidence, now: None,
    )
    return SimpleNamespace(
        authenticated=source_snapshot_case.authenticated,
        authenticated_source_snapshot=authenticate_source_snapshot(
            source_snapshot_case
        ),
    )


def test_exact_contract_coverage_and_every_action_dispatch(fast_case: Any) -> None:
    contract = json.loads(DATA_CONTRACT.read_bytes())
    source = _source_rows()
    candidate = _transformed(fast_case, rows=source)
    tables = _tables(candidate)
    rewrite = set(contract["row_actions"]["rewrite_rows"])
    discard = set(contract["row_actions"]["discard_rows"])
    assert (len(rewrite), sum(len(source[name][0]) for name in rewrite)) == (19, 204)
    assert (len(discard), sum(len(source[name][0]) for name in discard)) == (10, 139)
    assert (len(contract["foreign_keys"]), len(contract["derived_values"])) == (46, 4)
    assert set(tables) == set(source) == rewrite | discard
    assert all(tables[name] == () for name in discard)
    assert (candidate.source_row_count, candidate.candidate_row_count) == (29, 19)

    for table_name in rewrite:
        output = tables[table_name][0]
        assert tuple(output) == tuple(source[table_name][0])
        for column_name, action in contract["tables"][table_name]["columns"].items():
            before = source[table_name][0][column_name]
            after = output[column_name]
            if action in {"preserve_approved", "preserve_verified_catalog"}:
                assert after == before
            elif action == "set_null":
                assert after is None
            elif action.startswith("hmac_"):
                assert after is None if before is None else after != before


def test_foreign_keys_and_all_derived_recipes_are_coherent(fast_case: Any) -> None:
    contract = json.loads(DATA_CONTRACT.read_bytes())
    candidate = _transformed(fast_case)
    tables = _tables(candidate)
    discarded = set(contract["row_actions"]["discard_rows"])
    assert len(contract["foreign_keys"]) == 46
    for binding in contract["foreign_keys"]:
        source = [item.split(".", 1) for item in binding["source"]]
        target = [item.split(".", 1) for item in binding["target"]]
        if source[0][0] not in discarded:
            targets = {
                tuple(row[column] for _table, column in target)
                for row in tables[target[0][0]]
            }
            for row in tables[source[0][0]]:
                key = tuple(row[column] for _table, column in source)
                assert any(item is None for item in key) or key in targets
    appointment_id = tables["appointment"][0]["id"]
    binding_ref = TypeID.from_string(
        tables["authority_person_binding"][0]["binding_ref"]
    )
    assert binding_ref.prefix == "appointment"
    assert binding_ref.uuid == appointment_id
    join_code = tables["practice"][0]["join_code"]
    assert len(join_code) == 12
    assert set(join_code) <= set(sanitizer.ACCESS_CODE_ALPHABET)
    credentials = sanitizer.pseudonymize_provider_access_credentials(
        tables["provider_organization"][0]["id"], key=KEY
    )
    assert {
        name: tables["provider_organization"][0][name] for name in credentials
    } == credentials


def test_all_discarded_source_foreign_keys_are_validated(fast_case: Any) -> None:
    contract = json.loads(DATA_CONTRACT.read_bytes())
    discarded = set(contract["row_actions"]["discard_rows"])
    bindings = [
        binding
        for binding in contract["foreign_keys"]
        if binding["source"][0].split(".", 1)[0] in discarded
    ]
    assert len(bindings) == 20
    for binding in bindings:
        rows = _source_rows()
        for reference in binding["source"]:
            table_name, column_name = reference.split(".", 1)
            original = rows[table_name][0][column_name]
            rows[table_name][0][column_name] = (
                OTHER_ID
                if isinstance(original, uuid.UUID)
                else 2
                if type(original) is int
                else "unmatched"
            )
        with pytest.raises(
            FAILURE, match="data_contract_executor.source_integrity_invalid"
        ):
            _transformed(fast_case, rows=rows)


def test_golden_identity_email_domain_join_and_access_outputs(fast_case: Any) -> None:
    tables = _tables(_transformed(fast_case))
    assert (
        str(tables["doctor"][0]["id"]),
        tables["doctor"][0]["email"],
        tables["practice"][0]["email_domain"],
        tables["authority_actor_external_identity"][0]["issuer"],
        tables["authority_person_binding"][0]["binding_ref"],
        tables["practice"][0]["join_code"],
        tables["provider_organization"][0]["access_code_salt"],
        tables["provider_organization"][0]["access_code_digest"],
    ) == (
        "01b0c4ce-eb9b-7750-8a13-0ead0350e55d",
        "u_ahuv4znf2zjboiv3rbjnjnbe5apx7yogxoasnvlilzvpe75k@example.invalid",
        "org-s2qcgdtkv6x7bwwqlc4yhvfsxy4vlygw.example.invalid",
        "https://id-2w6ejf2mtm4l753q5dg22sgmasz74u3q.identity.invalid",
        "appointment_01mcxsxadgf7brq3sv5aygms7r",
        "698X6AC2N95P",
        "15c6a970036fd107a1c70b2256624cd5",
        "dd1321f94a4984e7a3aa1d92eac51256d62805519a72d7425d03f4546bf915a5",
    )


def test_engine_reauthenticates_at_start_final_and_candidate_validation(
    source_snapshot_case: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    later = PREFLIGHT_AT + timedelta(seconds=1)
    latest = PREFLIGHT_AT + timedelta(seconds=2)
    times = iter((PREFLIGHT_AT, later, latest))
    seen: list[datetime] = []
    snapshot_seen: list[tuple[Any, datetime, object]] = []
    original_copy = source_snapshot._copy_verified_tables_for_executor  # noqa: SLF001

    def observe_copy(value: Any, *, now: datetime, inventory_hmac_key: object) -> Any:
        snapshot_seen.append((value, now, inventory_hmac_key))
        return original_copy(value, now=now, inventory_hmac_key=inventory_hmac_key)

    def observe(self: Any, value: Any, now: datetime) -> None:
        assert type(self) is executor.SanitizerDataContractExecutor
        assert value is source_snapshot_case.authenticated
        seen.append(now)

    monkeypatch.setattr(
        executor.SanitizerDataContractExecutor, "_reauthenticate", observe
    )
    monkeypatch.setattr(
        source_snapshot, "_copy_verified_tables_for_executor", observe_copy
    )
    engine = _engine(clock=lambda: next(times))
    authenticated_source_snapshot = authenticate_source_snapshot(source_snapshot_case)
    candidate = engine.transform(
        authenticated_source_snapshot=authenticated_source_snapshot,
    )
    engine.validate_candidate(candidate)
    assert seen == [PREFLIGHT_AT, later, latest]
    assert snapshot_seen == [
        (authenticated_source_snapshot, PREFLIGHT_AT, INVENTORY_KEY),
        (authenticated_source_snapshot, later, INVENTORY_KEY),
    ]


def test_candidate_is_verified_after_the_validation_clock_callback(
    fast_case: Any,
) -> None:
    candidate = _transformed(fast_case)

    def mutate_candidate() -> datetime:
        object.__setattr__(
            candidate, "source_row_count", candidate.source_row_count + 1
        )
        return PREFLIGHT_AT

    with pytest.raises(FAILURE, match="^data_contract_executor.candidate_invalid$"):
        _engine(clock=mutate_candidate).validate_candidate(candidate)


def test_inventory_and_transform_keys_have_separate_roles(
    fast_case: Any, source_snapshot_case: Any
) -> None:
    with pytest.raises(FAILURE, match="^data_contract_executor.source_invalid$"):
        _engine(inventory_key=OTHER_KEY).transform(
            authenticated_source_snapshot=fast_case.authenticated_source_snapshot
        )
    baseline = _transformed(fast_case)
    other_transform = _transformed(fast_case, OTHER_KEY)
    assert baseline._tables != other_transform._tables
    assert baseline._proof_sha256 != other_transform._proof_sha256
    _candidate_refuses(baseline, _engine(OTHER_KEY))
    same_key_snapshot = _snapshot_from_rows(
        source_snapshot_case, _source_rows(), inventory_key=KEY
    )
    same_key_candidate = _engine(inventory_key=KEY).transform(
        authenticated_source_snapshot=same_key_snapshot
    )
    _engine(inventory_key=KEY).validate_candidate(same_key_candidate)


def test_determinism_domain_separation_and_fixed_payload_preservation(
    fast_case: Any,
) -> None:
    source = _source_rows()
    first = _transformed(fast_case)
    second = _transformed(fast_case)
    other = _transformed(fast_case, OTHER_KEY)
    first_tables = _tables(first)
    other_tables = _tables(other)
    assert first_tables == _tables(second)
    assert first._proof_sha256 == second._proof_sha256
    ordered_rows = _source_rows()
    added = ordered_rows["appointment"][0].copy()
    added["id"] = OTHER_ID
    ordered_rows["appointment"] = (ordered_rows["appointment"][0], added)
    reversed_rows = copy.deepcopy(ordered_rows)
    reversed_rows["appointment"] = tuple(reversed(reversed_rows["appointment"]))
    ordered_candidate = _transformed(fast_case, rows=ordered_rows)
    reversed_candidate = _transformed(fast_case, rows=reversed_rows)
    assert ordered_candidate._tables == reversed_candidate._tables
    assert (
        ordered_candidate._source_snapshot_proof_sha256
        != reversed_candidate._source_snapshot_proof_sha256
    )
    assert ordered_candidate._proof_sha256 != reversed_candidate._proof_sha256
    mapped_ids = {
        first_tables[table][0]["id"]
        for table in json.loads(DATA_CONTRACT.read_bytes())["row_actions"][
            "rewrite_rows"
        ]
    }
    assert len(mapped_ids) == 19
    assert all(
        value.version == 7 and value.variant == uuid.RFC_4122 for value in mapped_ids
    )
    assert (
        first_tables["doctor"][0]["clerk_id"]
        == first_tables["authority_actor_external_identity"][0]["subject"]
    )
    assert (
        first_tables["practice"][0]["email_domain"]
        == first_tables["provider_organization"][0]["email_domain"]
    )
    assert first_tables["doctor"][0]["email"].endswith("@example.invalid")
    assert first_tables["authority_actor_external_identity"][0]["issuer"].startswith(
        "https://id-"
    )
    contract = json.loads(DATA_CONTRACT.read_bytes())
    for table_name in sanitizer.FIXED_CATALOG_TABLES:
        for column_name, action in contract["tables"][table_name]["columns"].items():
            if action == "preserve_verified_catalog":
                expected = source[table_name][0][column_name]
                assert first_tables[table_name][0][column_name] == expected
                assert other_tables[table_name][0][column_name] == expected
    assert first_tables["doctor"][0]["id"] != other_tables["doctor"][0]["id"]
    assert (
        first_tables["practice"][0]["join_code"]
        != other_tables["practice"][0]["join_code"]
    )


def test_nullability_json_immutability_and_false_authority(fast_case: Any) -> None:
    source = _source_rows()
    canonical_time = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    naive_time = datetime(2026, 1, 2, 3, 4, 5)
    source["appointment"][0]["external_id"] = None
    source["appointment"][0].update(
        created_at=canonical_time, appointment_date=naive_time
    )
    ordered_candidate = _transformed(fast_case, rows=source)
    fields = source["doctor_note_schema"][0]["fields"]
    source["doctor_note_schema"][0]["fields"] = dict(reversed(fields.items()))
    candidate = _transformed(fast_case, rows=source)
    tables = _tables(candidate)
    assert tables["appointment"][0]["external_id"] is None
    normalized = tables["appointment"][0]["created_at"]
    assert normalized == datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    normalized_naive = tables["appointment"][0]["appointment_date"]
    assert normalized.tzinfo is UTC and normalized.fold == 0
    assert normalized_naive == naive_time and normalized_naive.fold == 0
    assert normalized_naive.tzinfo is None
    clock_time = PREFLIGHT_AT.replace(tzinfo=timezone(timedelta(0), "custom"), fold=1)
    trusted = executor._trusted_utc(lambda: clock_time)  # noqa: SLF001
    assert trusted == PREFLIGHT_AT and trusted.tzinfo is UTC and trusted.fold == 0
    assert trusted is not clock_time
    nested = tables["doctor_note_schema"][0]["fields"]
    assert list(nested) == ["second", "nested"]
    assert candidate._proof_sha256 != ordered_candidate._proof_sha256
    assert nested["nested"] == ["approved", 1, True, None]
    nested["new"] = "copy-only"
    assert "new" not in candidate._rows_for_table("doctor_note_schema")[0]["fields"]
    assert candidate.post_projection_authenticated is False
    assert not any(getattr(candidate, name) for name in executor._AUTHORITY_CLAIMS)
    assert candidate.table_count == 29


def test_candidate_rejects_nonexact_nested_pairs_without_callbacks(
    fast_case: Any,
) -> None:
    candidate = _transformed(fast_case)
    rows = dict(candidate._tables)["doctor_note_schema"]  # noqa: SLF001
    row = cast(executor._FrozenMap, rows[0])  # noqa: SLF001
    nested = next(item[1] for item in row._items if item[0] == "fields")  # noqa: SLF001
    assert type(nested) is executor._FrozenMap  # noqa: SLF001
    callbacks: list[str] = []

    class CallbackTuple(tuple[Any, ...]):
        def __iter__(self) -> Any:
            callbacks.append("iterated")
            return super().__iter__()

    original_items = nested._items  # noqa: SLF001
    object.__setattr__(
        nested, "_items", (CallbackTuple(original_items[0]), *original_items[1:])
    )
    try:
        with pytest.raises(FAILURE, match="^data_contract_executor.candidate_invalid$"):
            _engine().validate_candidate(candidate)
        assert callbacks == []
    finally:
        object.__setattr__(nested, "_items", original_items)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda rows: rows.pop("appointment"),
        lambda rows: rows.update({"unexpected": tuple()}),
        lambda rows: rows["appointment"][0].pop("created_at"),
        lambda rows: rows["appointment"][0].update({"unexpected": "value"}),
        lambda rows: rows["appointment"][0].update({"id": str(SOURCE_ID)}),
        lambda rows: rows["appointment"][0].update({"created_at": None}),
        lambda rows: rows["doctor"][0].update({"role": "not_a_doctor_role"}),
        lambda rows: rows["provider_organization"][0].update(access_code_version=2**31),
        lambda rows: rows.update(
            appointment=(rows["appointment"][0], rows["appointment"][0].copy())
        ),
        lambda rows: rows["appointment"][0].update({"doctor_id": OTHER_ID}),
        lambda rows: rows["authority_person_binding"][0].update(
            {"binding_ref": "appointment_not-a-typeid"}
        ),
        lambda rows: rows.update({"baa_version": tuple()}),
    ],
)
def test_malformed_missing_extra_wrong_duplicate_and_unbound_inputs_refuse(
    fast_case: Any, mutate: Any
) -> None:
    rows = _source_rows()
    mutate(rows)
    with pytest.raises(FAILURE, match=r"^data_contract_executor\.[a-z_]+$"):
        _transformed(fast_case, rows=rows)


def test_failure_is_atomic_and_never_mints_a_partial_result(
    fast_case: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = _source_rows()
    rows["synthetic_preview_appointment_fixture"][0]["id"] = "bad-last-table"
    original = copy.deepcopy(rows)
    minted = 0
    original_mint = executor._mint_candidate

    def observe(**kwargs: Any) -> Any:
        nonlocal minted
        minted += 1
        return original_mint(**kwargs)

    monkeypatch.setattr(executor, "_mint_candidate", observe)
    with pytest.raises(FAILURE):
        _transformed(fast_case, rows=rows)
    assert minted == 0
    assert rows == original


def test_mixed_key_and_forged_candidates_refuse(fast_case: Any) -> None:
    candidate = _transformed(fast_case)
    engine = _engine()
    engine.validate_candidate(candidate)
    _candidate_refuses(candidate, _engine(OTHER_KEY))
    _candidate_refuses(_clone(candidate, _proof_sha256="0" * 64))
    _candidate_refuses(
        candidate, _engine(clock=lambda: PREFLIGHT_AT - timedelta(seconds=1))
    )
    for name in "table_count rewrite_table_count discard_table_count source_row_count candidate_row_count".split():
        _candidate_refuses(_clone(candidate, **{name: float(getattr(candidate, name))}))
    for name, item in (
        ("_source_snapshot_contract_sha256", "0" * 64),
        ("_source_snapshot_proof_sha256", "0" * 64),
        ("_source_snapshot_identity", candidate._source_snapshot_identity + 1),
        (
            "_source_snapshot_type_identity",
            candidate._source_snapshot_type_identity + 1,
        ),
        (
            "_source_snapshot_seal_identity",
            candidate._source_snapshot_seal_identity + 1,
        ),
    ):
        _candidate_refuses(_clone(candidate, **{name: item}))
    for table_name, nested in (("appointment", None), ("doctor_note_schema", "fields")):
        forged = _clone(candidate)
        tables = dict(forged._tables)
        row = dict(tables[table_name][0])
        backing = dict(row[nested]) if nested else {}
        if nested:
            row[nested] = MappingProxyType(backing)
            row = executor._FrozenMap(tuple(row.items()))  # noqa: SLF001
        tables[table_name] = (row, *tables[table_name][1:])
        object.__setattr__(
            forged, "_tables", tuple((name, tables[name]) for name, _ in forged._tables)
        )
        _candidate_refuses(forged)
        if nested:
            backing["after_refusal"] = True
            assert "after_refusal" in dict(forged._tables)[table_name][0][nested]
    noncanonical = PREFLIGHT_AT.replace(tzinfo=timezone(timedelta(0), "custom"), fold=1)
    assert noncanonical == PREFLIGHT_AT
    assert executor._canonical(noncanonical) != executor._canonical(PREFLIGHT_AT)  # noqa: SLF001
    _candidate_refuses(_clone(candidate, transformed_at=noncanonical))
    later = _clone(
        fast_case.authenticated,
        authenticated_at=fast_case.authenticated.authenticated_at
        + timedelta(seconds=1),
    )
    public = {
        name: getattr(later, name) for name in executor.evidence_authority.PUBLIC_FIELDS
    }
    proof = executor.evidence_authority._proof(  # noqa: SLF001
        later._evidence_record_bytes, public, later._retained_source_witness_sha256
    )
    object.__setattr__(later, "_proof_sha256", proof)
    executor.evidence_authority._validate_retained_envelope_at_current_time(  # noqa: SLF001
        later, now=PREFLIGHT_AT
    )
    object.__setattr__(candidate, "_authenticated_evidence", later)
    _candidate_refuses(candidate, engine)
    callbacks: list[str] = []

    class CallbackTuple(tuple[Any, ...]):
        def __iter__(self) -> Any:
            callbacks.append("iterated")
            return super().__iter__()

    _candidate_refuses(_clone(candidate, _tables=CallbackTuple(candidate._tables)))
    assert callbacks == []


def test_repr_pickle_errors_and_logs_never_disclose_rows_or_keys(
    fast_case: Any, caplog: pytest.LogCaptureFixture
) -> None:
    secret = "SECRET-ROW-DATA-DO-NOT-LEAK"
    rows = _source_rows()
    rows["appointment"][0]["id"] = secret
    with pytest.raises(FAILURE) as caught:
        _transformed(fast_case, rows=rows)
    assert secret not in str(caught.value)
    assert KEY.hex() not in str(caught.value)
    assert not caplog.records
    engine = _engine()
    candidate = _transformed(fast_case)
    assert secret not in repr(engine)
    assert KEY.hex() not in repr(engine)
    assert "person@example.com" not in repr(candidate)
    for value in (candidate, engine):
        with pytest.raises(FAILURE):
            pickle.dumps(value)


def test_public_failure_tracebacks_do_not_retain_keys_rows_or_inputs(
    fast_case: Any,
) -> None:
    secret_row = "TRACE_SECRET_ROW_DO_NOT_RETAIN"
    secret_key = b"TRACE_SECRET_KEY_DO_NOT_RETAIN".ljust(32, b"!")
    rows = _source_rows()
    rows["appointment"][0]["id"] = secret_row
    invalid_snapshot = _snapshot_from_rows(fast_case, rows)
    candidate = _transformed(fast_case)

    def capture(call: Any) -> None:
        with pytest.raises(FAILURE) as raised:
            call()
        retained = _module_trace(raised.value)
        assert not any(
            token in retained
            for token in (secret_row, "TRACE_SECRET_KEY_DO_NOT_RETAIN", "example.com")
        )

    capture(
        lambda: executor.SanitizerDataContractExecutor._from_reviewed_configuration(
            clock=lambda: PREFLIGHT_AT,
            transform_key=secret_key,
            transform_key_version=KEY_VERSION,
            inventory_hmac_key=INVENTORY_KEY,
            inventory_hmac_key_version=inventory.SUPPORTED_HMAC_KEY_VERSION,
            sanitizer_policy_bytes=POLICY.read_bytes(),
            inventory_policy_bytes=INVENTORY_POLICY.read_bytes(),
            data_contract_bytes=DATA_CONTRACT.read_bytes(),
            source_snapshot_contract_bytes=secret_row.encode(),
        )
    )
    capture(
        lambda: _engine(secret_key).transform(
            authenticated_source_snapshot=invalid_snapshot
        )
    )
    forged = _clone(candidate, _proof_sha256=secret_row)
    capture(lambda: _engine(secret_key).validate_candidate(forged))


def test_configuration_is_exact_and_public_constructors_refuse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for constructor in (
        executor.SanitizerDataContractExecutor,
        executor.SanitizerCandidateDataset,
    ):
        with pytest.raises(FAILURE):
            constructor()
    changed = bytearray(DATA_CONTRACT.read_bytes())
    changed[-2] = ord(" ") if changed[-2] != ord(" ") else ord("\n")
    with pytest.raises(FAILURE, match="data_contract_executor.policy_invalid"):
        executor.SanitizerDataContractExecutor._from_reviewed_configuration(
            clock=lambda: PREFLIGHT_AT,
            transform_key=KEY,
            transform_key_version=KEY_VERSION,
            inventory_hmac_key=INVENTORY_KEY,
            inventory_hmac_key_version=inventory.SUPPORTED_HMAC_KEY_VERSION,
            sanitizer_policy_bytes=POLICY.read_bytes(),
            inventory_policy_bytes=INVENTORY_POLICY.read_bytes(),
            data_contract_bytes=bytes(changed),
            source_snapshot_contract_bytes=SOURCE_SNAPSHOT_CONTRACT.read_bytes(),
        )
    snapshot_contract = json.loads(SOURCE_SNAPSHOT_CONTRACT.read_bytes())
    assert (
        hashlib.sha256(SOURCE_SNAPSHOT_CONTRACT.read_bytes()).hexdigest()
        == source_snapshot.SOURCE_SNAPSHOT_CONTRACT_SHA256
        == executor.SOURCE_SNAPSHOT_CONTRACT_SHA256
    )
    assert snapshot_contract["status"]["executor_consumption_implemented"] is True
    assert (
        snapshot_contract["executor_consumption"]
        == executor._SOURCE_SNAPSHOT_CONSUMPTION_CONTRACT  # noqa: SLF001
    )
    monkeypatch.setattr(source_snapshot, "SOURCE_SNAPSHOT_CONTRACT_SHA256", "0" * 64)
    with pytest.raises(FAILURE, match="data_contract_executor.policy_invalid"):
        _engine()


def test_transform_accepts_only_the_authenticated_snapshot(
    fast_case: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    parameters = inspect.signature(
        executor.SanitizerDataContractExecutor.transform
    ).parameters
    assert tuple(parameters) == ("self", "authenticated_source_snapshot")
    assert (
        parameters["authenticated_source_snapshot"].kind
        is inspect.Parameter.KEYWORD_ONLY
    )
    engine = _engine()
    with pytest.raises(TypeError):
        engine.transform(  # type: ignore[call-arg]
            authenticated_sanitizer_evidence=fast_case.authenticated,
            source_rows=_source_rows(),
        )
    candidate = engine.transform(
        authenticated_source_snapshot=fast_case.authenticated_source_snapshot
    )
    assert candidate._authenticated_evidence is fast_case.authenticated
    assert not hasattr(candidate, "_authenticated_source_snapshot")
    assert candidate._source_snapshot_identity == id(
        fast_case.authenticated_source_snapshot
    )
    assert (
        candidate._source_snapshot_proof_sha256
        == fast_case.authenticated_source_snapshot._proof_sha256
    )
    callbacks: list[str] = []

    class CallbackSnapshot:
        def __getattr__(self, name: str) -> Any:
            callbacks.append(name)
            raise AttributeError(name)

    with pytest.raises(FAILURE, match="^data_contract_executor.source_invalid$"):
        engine.transform(authenticated_source_snapshot=CallbackSnapshot())  # type: ignore[arg-type]
    assert callbacks == []

    class CallbackExecutor(executor.SanitizerDataContractExecutor):
        @classmethod
        def _build_from_reviewed_configuration(cls, **kwargs: Any) -> Any:
            del cls, kwargs
            callbacks.append("built")

        def _transform(self, **kwargs: Any) -> Any:
            del self, kwargs
            callbacks.append("transformed")

        def _validate_candidate(self, candidate: Any) -> None:
            del self, candidate
            callbacks.append("validated")

    with pytest.raises(FAILURE, match="^data_contract_executor.configuration_invalid$"):
        CallbackExecutor._from_reviewed_configuration(
            clock=lambda: PREFLIGHT_AT,
            transform_key=KEY,
            transform_key_version=KEY_VERSION,
            inventory_hmac_key=INVENTORY_KEY,
            inventory_hmac_key_version=inventory.SUPPORTED_HMAC_KEY_VERSION,
            sanitizer_policy_bytes=POLICY.read_bytes(),
            inventory_policy_bytes=INVENTORY_POLICY.read_bytes(),
            data_contract_bytes=DATA_CONTRACT.read_bytes(),
            source_snapshot_contract_bytes=SOURCE_SNAPSHOT_CONTRACT.read_bytes(),
        )
    callback_executor = object.__new__(CallbackExecutor)
    with pytest.raises(FAILURE, match="^data_contract_executor.configuration_invalid$"):
        callback_executor.transform(
            authenticated_source_snapshot=fast_case.authenticated_source_snapshot
        )
    with pytest.raises(FAILURE, match="^data_contract_executor.configuration_invalid$"):
        callback_executor.validate_candidate(candidate)
    assert callbacks == []

    other_rows = _source_rows()
    other_rows["doctor"][0]["email"] = "other-doctor@example.test"
    replacement = _snapshot_from_rows(fast_case, other_rows)
    source_values = iter((fast_case.authenticated_source_snapshot, replacement))
    original_copy = source_snapshot._copy_verified_tables_for_executor  # noqa: SLF001

    def swap_copy(_value: Any, *, now: datetime, inventory_hmac_key: object) -> Any:
        return original_copy(
            next(source_values), now=now, inventory_hmac_key=inventory_hmac_key
        )

    monkeypatch.setattr(
        source_snapshot, "_copy_verified_tables_for_executor", swap_copy
    )
    with pytest.raises(FAILURE, match="^data_contract_executor.source_invalid$"):
        engine.transform(
            authenticated_source_snapshot=fast_case.authenticated_source_snapshot
        )

    replacement_copy = original_copy(
        replacement,
        now=PREFLIGHT_AT,
        inventory_hmac_key=INVENTORY_KEY,
    )
    monkeypatch.setattr(
        source_snapshot,
        "_copy_verified_tables_for_executor",
        lambda *_args, **_kwargs: replacement_copy,
    )
    with pytest.raises(FAILURE, match="^data_contract_executor.source_invalid$"):
        engine.transform(
            authenticated_source_snapshot=fast_case.authenticated_source_snapshot
        )

    class CallbackValue:
        def __eq__(self, other: object) -> bool:
            del other
            callbacks.append("compared")
            return False

        def __ne__(self, other: object) -> bool:
            del other
            callbacks.append("compared")
            return True

    original_contract = fast_case.authenticated_source_snapshot._contract_sha256
    object.__setattr__(
        fast_case.authenticated_source_snapshot,
        "_contract_sha256",
        CallbackValue(),
    )
    try:
        with pytest.raises(FAILURE, match="^data_contract_executor.source_invalid$"):
            engine.transform(
                authenticated_source_snapshot=fast_case.authenticated_source_snapshot
            )
        assert callbacks == []
    finally:
        object.__setattr__(
            fast_case.authenticated_source_snapshot,
            "_contract_sha256",
            original_contract,
        )

    corrupted_copy = original_copy(
        fast_case.authenticated_source_snapshot,
        now=PREFLIGHT_AT,
        inventory_hmac_key=INVENTORY_KEY,
    )
    object.__setattr__(corrupted_copy, "_proof_sha256", "0" * 64)
    monkeypatch.setattr(
        source_snapshot,
        "_copy_verified_tables_for_executor",
        lambda *_args, **_kwargs: corrupted_copy,
    )
    with pytest.raises(FAILURE, match="^data_contract_executor.source_invalid$"):
        engine.transform(
            authenticated_source_snapshot=fast_case.authenticated_source_snapshot
        )


def test_configuration_is_deeply_immutable(fast_case: Any) -> None:
    engine = _engine()
    with pytest.raises(TypeError):
        engine._contract["tables"]["doctor"]["columns"]["email"] = "preserve_approved"
    with pytest.raises(FAILURE):
        engine._contract = {}
    with pytest.raises(FAILURE):
        del engine._contract
    assert _transformed(fast_case)._rows_for_table("doctor")[0]["email"] != (
        "person@example.com"
    )


def test_transform_has_no_filesystem_reauthentication(
    source_snapshot_case: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = _engine()
    authenticated_source_snapshot = authenticate_source_snapshot(source_snapshot_case)

    def refuse(*args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        raise AssertionError("filesystem or legacy reauthentication invoked")

    monkeypatch.setattr(Path, "read_bytes", refuse)
    monkeypatch.setattr(
        executor.evidence_authority, "_reauthenticate_at_current_time", refuse
    )
    authenticated = source_snapshot_case.authenticated
    kwargs = dict(authenticated_source_snapshot=authenticated_source_snapshot)
    source = authenticated._fixed_catalog_source_state
    target = source._target_binding
    built = target._written_receipt._built_receipt
    receipt = built._isolation_inventory._database_observation._production_authority._launch_material.authenticated_receipt
    mapping_subtype = type("MappingSubtype", (dict,), {})
    candidate = engine.transform(**kwargs)
    engine.validate_candidate(candidate)
    assert dict(candidate._tables)["appointment"][0]["created_at"].tzinfo is UTC
    with pytest.raises(FAILURE, match="data_contract_executor.target_expired"):
        engine._reauthenticate(authenticated, target.expires_at)
    for owner, name, tampered in (
        (built, "submission_bytes", b"{}"),
        (built, "_isolation_inventory", object()),
        (receipt, "runtime_reference", mapping_subtype(receipt.runtime_reference)),
        (source, "authenticated_at", source.authenticated_at - timedelta(seconds=1)),
        (
            authenticated,
            "authenticated_at",
            authenticated.authenticated_at - timedelta(seconds=1),
        ),
    ):
        original = getattr(owner, name)
        object.__setattr__(owner, name, tampered)
        try:
            with pytest.raises(FAILURE):
                engine.transform(**kwargs)
        finally:
            object.__setattr__(owner, name, original)


def test_public_preflight_remains_terminally_unimplemented(real_case: Any) -> None:
    preflight = public_executor.SanitizerExecutorPreflight._from_reviewed_configuration(
        clock=lambda: PREFLIGHT_AT
    )
    with pytest.raises(
        public_executor.SanitizerExecutorFailure,
        match=f"^{public_executor.ACTIVATION_FAILURE}$",
    ):
        preflight.preflight(authenticated_sanitizer_evidence=real_case.authenticated)


def test_import_is_adapter_free_and_source_has_no_live_surfaces() -> None:
    tree = ast.parse(SCRIPT.read_text())
    imports = {
        alias.name.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert not imports & set(
        "azure asyncpg boto3 httpx os pathlib psycopg requests sqlalchemy subprocess".split()
    )
    calls = {
        node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, (ast.Attribute, ast.Name))
    }
    assert not calls & set(
        "Popen commit connect execute getenv open rollback run".split()
    )
    code = f"""import json, sys
sys.path[:0] = [{str(ROOT)!r}, {str(SITE_PACKAGES)!r}]
from scripts import staging_restore_sanitizer_data_contract_executor
roots = '_posixsubprocess asyncpg azure boto3 httpx psycopg requests sqlalchemy subprocess'.split()
scripts = '''scripts.authenticate_staging_github_attestation scripts.bind_staging_restore_sanitizer_evidence scripts.compose_staging_restore_evidence_binding scripts.staging_restore_runtime_adapters scripts.staging_restore_safe_submit scripts.verify_staging_source_provider_observation'''.split()
print(json.dumps(sorted(name for name in sys.modules if name in scripts or any(name == root or name.startswith(root + '.') for root in roots))))
"""
    completed = subprocess.run(
        [sys.executable, "-I", "-S", "-c", code],
        cwd=ROOT,
        capture_output=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == []
    for path, expected, pinned in (
        (POLICY, sanitizer.CANONICAL_POLICY_SHA256, executor.SANITIZER_POLICY_SHA256),
        (
            INVENTORY_POLICY,
            inventory.CANONICAL_POLICY_SHA256,
            executor.INVENTORY_POLICY_SHA256,
        ),
        (
            DATA_CONTRACT,
            sanitizer.CANONICAL_DATA_CONTRACT_SHA256,
            executor.DATA_CONTRACT_SHA256,
        ),
        (
            SOURCE_SNAPSHOT_CONTRACT,
            source_snapshot.SOURCE_SNAPSHOT_CONTRACT_SHA256,
            executor.SOURCE_SNAPSHOT_CONTRACT_SHA256,
        ),
    ):
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected == pinned
    assert executor.HMAC_DOMAIN == sanitizer.HMAC_DOMAIN
    assert executor.CANDIDATE_PROOF_DOMAIN == (
        "candidate_dataset.source_snapshot_v1.proof"
    )
    assert "candidate_dataset.proof" not in SCRIPT.read_text()
