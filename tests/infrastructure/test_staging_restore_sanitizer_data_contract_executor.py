from __future__ import annotations

import ast
import copy
import hashlib
import json
import pickle
import subprocess
import sys
import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from typeid import TypeID

from scripts import staging_restore_sanitizer as sanitizer
from scripts import staging_restore_sanitizer_data_contract_executor as executor
from scripts import staging_restore_sanitizer_executor as public_executor
from scripts import staging_synthetic_inventory as inventory

from tests.infrastructure.test_staging_restore_sanitizer_executor import (
    real_case as executor_real_case,
)

real_case = executor_real_case

ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "scripts/staging_restore_sanitizer_data_contract_executor.py"
POLICY = ROOT / "contracts/staging-rehearsal/v1/restore-sanitizer-policy.json"
INVENTORY_POLICY = ROOT / "contracts/staging-rehearsal/v1/synthetic-data-policy.json"
DATA_CONTRACT = (
    ROOT / "contracts/staging-rehearsal/v1/restore-sanitizer-data-contract.json"
)
KEY = bytes(range(32))
OTHER_KEY = bytes(reversed(range(32)))
KEY_VERSION = "staging-sanitizer-transform-v1"
PREFLIGHT_AT = datetime(2026, 9, 5, 12, 10, 50, tzinfo=UTC)
SOURCE_ID = uuid.UUID("01890f4c-7b00-7000-8000-000000000001")
OTHER_ID = uuid.UUID("01890f4c-7b00-7000-8000-000000000002")


def _engine(
    key: bytes = KEY, clock: Callable[[], datetime] | None = None
) -> executor.SanitizerDataContractExecutor:
    return executor.SanitizerDataContractExecutor._from_reviewed_configuration(
        clock=clock or (lambda: PREFLIGHT_AT),
        transform_key=key,
        transform_key_version=KEY_VERSION,
        sanitizer_policy_bytes=POLICY.read_bytes(),
        inventory_policy_bytes=INVENTORY_POLICY.read_bytes(),
        data_contract_bytes=DATA_CONTRACT.read_bytes(),
    )


def _value(data_type: str) -> Any:
    if data_type == "uuid":
        return SOURCE_ID
    if data_type in {"integer", "bigint"}:
        return 1
    if data_type == "boolean":
        return False
    if data_type == "double precision":
        return 1.5
    if data_type == "date":
        return date(2026, 1, 2)
    if data_type == "timestamp with time zone":
        return datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    if data_type == "timestamp without time zone":
        return datetime(2026, 1, 2, 3, 4, 5)
    if data_type in {"json", "jsonb"}:
        return {"nested": ["approved", 1, True, None]}
    enum_values = {
        "doctorrole": "normal",
        "planordering": "patient_then_doctor",
        "pronounstyle": "infer_from_transcript",
        "subjectivesource": "patient_only",
        "terminology": "doctor_patient",
    }
    if data_type in enum_values:
        return enum_values[data_type]
    return "value"


def _source_rows() -> dict[str, tuple[dict[str, Any], ...]]:
    policy = json.loads(INVENTORY_POLICY.read_bytes())
    schemas = inventory._expected_tables(policy, "pre")  # noqa: SLF001
    retained = {
        name: spec
        for name, spec in schemas.items()
        if spec["disposition"] != "required_empty"
    }
    rows = {
        table_name: (
            {name: _value(data_type) for name, data_type, _required in spec["columns"]},
        )
        for table_name, spec in retained.items()
    }
    overrides = {
        ("authority_actor_external_identity", "issuer"): (
            "https://clerk.example.invalid"
        ),
        ("authority_actor_external_identity", "subject"): "user_same-identity",
        ("authority_person_binding", "binding_kind"): "provider_record",
        ("authority_person_binding", "binding_ref"): (
            "appointment_01h47mryr0e008000000000001"
        ),
        ("doctor", "api_key"): None,
        ("doctor", "clerk_id"): "user_same-identity",
        ("doctor", "email"): "person@example.com",
        ("ehr_sandbox_patient_designation", "adapter"): "athena",
        ("ehr_sandbox_patient_designation", "environment"): "preview",
        ("ehr_sandbox_patient_designation", "practice_ref"): "195900",
        ("ehr_sandbox_patient_designation", "synthetic_only"): True,
        ("ehr_sandbox_record_mapping", "adapter"): "athena",
        ("ehr_sandbox_record_mapping", "environment"): "preview",
        ("ehr_sandbox_record_mapping", "practice_ref"): "195900",
        ("ehr_sandbox_record_mapping", "synthetic_only"): True,
        ("practice", "email_domain"): "example.com",
        ("practice", "join_code"): "OLDCODE12345",
        ("provider_organization", "access_code_salt"): "a" * 32,
        ("provider_organization", "access_code_digest"): "b" * 64,
        ("provider_organization", "email_domain"): "example.com",
        ("synthetic_preview_appointment_fixture", "fixture_version"): (
            "synthetic_preview_labs_v1"
        ),
        ("synthetic_preview_appointment_fixture", "synthetic_only"): True,
    }
    required_null = {
        "practice": {
            "stripe_customer_id",
            "stripe_subscription_id",
            "subscription_status",
            "stripe_last_event_created_at",
            "stripe_last_event_id",
            "stripe_checkout_session_id",
            "stripe_checkout_session_url",
            "stripe_checkout_session_expires_at",
            "stripe_quantity_sync_target",
            "stripe_quantity_sync_error",
            "stripe_quantity_synced_at",
        }
    }
    for (table_name, column_name), value in overrides.items():
        rows[table_name][0][column_name] = value
    for table_name, columns in required_null.items():
        for column_name in columns:
            rows[table_name][0][column_name] = None
    return rows


def _tables(
    candidate: executor.SanitizerCandidateDataset,
) -> dict[str, tuple[Any, ...]]:
    return {name: candidate._rows_for_table(name) for name, _rows in candidate._tables}


def _transformed(
    real_case: Any, key: bytes = KEY
) -> executor.SanitizerCandidateDataset:
    return _engine(key).transform(
        authenticated_sanitizer_evidence=real_case.authenticated,
        source_rows=_source_rows(),
    )


@pytest.fixture
def fast_case(real_case: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setattr(
        executor.SanitizerDataContractExecutor,
        "_reauthenticate",
        lambda self, evidence, now: None,
    )
    return real_case


def test_exact_contract_coverage_and_every_action_dispatch(fast_case: Any) -> None:
    contract = json.loads(DATA_CONTRACT.read_bytes())
    source = _source_rows()
    candidate = _engine().transform(
        authenticated_sanitizer_evidence=fast_case.authenticated,
        source_rows=source,
    )
    tables = _tables(candidate)
    rewrite = set(contract["row_actions"]["rewrite_rows"])
    discard = set(contract["row_actions"]["discard_rows"])

    assert (len(rewrite), sum(len(source[name][0]) for name in rewrite)) == (19, 204)
    assert (len(discard), sum(len(source[name][0]) for name in discard)) == (10, 139)
    assert len(contract["foreign_keys"]) == 46
    assert len(contract["derived_values"]) == 4
    assert set(tables) == set(source) == rewrite | discard
    assert all(tables[name] == () for name in discard)
    assert candidate.source_row_count == 29
    assert candidate.candidate_row_count == 19

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
            elif action.startswith(("hmac_uuid7:", "hmac_text:")) or action in {
                "hmac_email",
                "hmac_domain",
                "hmac_issuer",
                "hmac_subject",
            }:
                assert after is None if before is None else after != before


def test_foreign_keys_and_all_derived_recipes_are_coherent(fast_case: Any) -> None:
    contract = json.loads(DATA_CONTRACT.read_bytes())
    candidate = _transformed(fast_case)
    tables = _tables(candidate)
    discarded = set(contract["row_actions"]["discard_rows"])
    checked = 0
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
        checked += 1
    assert checked == 46

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
    assert (
        tables["provider_organization"][0]["access_code_salt"]
        == credentials["access_code_salt"]
    )
    assert (
        tables["provider_organization"][0]["access_code_digest"]
        == credentials["access_code_digest"]
    )


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
            executor.SanitizerDataContractExecutorFailure,
            match="data_contract_executor.source_integrity_invalid",
        ):
            _engine().transform(
                authenticated_sanitizer_evidence=fast_case.authenticated,
                source_rows=rows,
            )


def test_golden_identity_email_domain_join_and_access_outputs(fast_case: Any) -> None:
    tables = _tables(_transformed(fast_case))
    assert str(tables["doctor"][0]["id"]) == ("01b0c4ce-eb9b-7750-8a13-0ead0350e55d")
    assert tables["doctor"][0]["email"] == (
        "u_ahuv4znf2zjboiv3rbjnjnbe5apx7yogxoasnvlilzvpe75k@example.invalid"
    )
    assert tables["practice"][0]["email_domain"] == (
        "org-s2qcgdtkv6x7bwwqlc4yhvfsxy4vlygw.example.invalid"
    )
    assert tables["authority_actor_external_identity"][0]["issuer"] == (
        "https://id-2w6ejf2mtm4l753q5dg22sgmasz74u3q.identity.invalid"
    )
    assert tables["authority_person_binding"][0]["binding_ref"] == (
        "appointment_01mcxsxadgf7brq3sv5aygms7r"
    )
    assert tables["practice"][0]["join_code"] == "698X6AC2N95P"
    assert tables["provider_organization"][0]["access_code_salt"] == (
        "15c6a970036fd107a1c70b2256624cd5"
    )
    assert tables["provider_organization"][0]["access_code_digest"] == (
        "dd1321f94a4984e7a3aa1d92eac51256d62805519a72d7425d03f4546bf915a5"
    )


def test_engine_reauthenticates_at_start_final_and_candidate_validation(
    real_case: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    later = PREFLIGHT_AT + timedelta(seconds=1)
    latest = PREFLIGHT_AT + timedelta(seconds=2)
    times = iter((PREFLIGHT_AT, later, latest))
    seen: list[datetime] = []

    def observe(self: Any, value: Any, now: datetime) -> None:
        assert type(self) is executor.SanitizerDataContractExecutor
        assert value is real_case.authenticated
        seen.append(now)

    monkeypatch.setattr(
        executor.SanitizerDataContractExecutor, "_reauthenticate", observe
    )
    engine = _engine(clock=lambda: next(times))
    candidate = engine.transform(
        authenticated_sanitizer_evidence=real_case.authenticated,
        source_rows=_source_rows(),
    )
    engine.validate_candidate(candidate)
    assert seen == [PREFLIGHT_AT, later, latest]


def test_determinism_domain_separation_and_fixed_payload_preservation(
    fast_case: Any,
) -> None:
    source = _source_rows()
    first = _transformed(fast_case)
    second = _transformed(fast_case)
    other = _transformed(fast_case, OTHER_KEY)
    first_tables = _tables(first)
    second_tables = _tables(second)
    other_tables = _tables(other)
    assert first_tables == second_tables
    assert first._proof_sha256 == second._proof_sha256

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
                assert (
                    first_tables[table_name][0][column_name]
                    == source[table_name][0][column_name]
                )
                assert (
                    other_tables[table_name][0][column_name]
                    == source[table_name][0][column_name]
                )
    assert first_tables["doctor"][0]["id"] != other_tables["doctor"][0]["id"]
    assert (
        first_tables["practice"][0]["join_code"]
        != other_tables["practice"][0]["join_code"]
    )


def test_nullability_json_immutability_and_false_authority(fast_case: Any) -> None:
    source = _source_rows()
    source["appointment"][0]["external_id"] = None
    candidate = _engine().transform(
        authenticated_sanitizer_evidence=fast_case.authenticated,
        source_rows=source,
    )
    tables = _tables(candidate)
    assert tables["appointment"][0]["external_id"] is None
    nested = tables["doctor_note_schema"][0]["fields"]
    assert nested["nested"] == ["approved", 1, True, None]
    nested["new"] = "copy-only"
    assert "new" not in candidate._rows_for_table("doctor_note_schema")[0]["fields"]
    assert candidate.post_projection_authenticated is False
    assert not any(getattr(candidate, name) for name in executor._AUTHORITY_CLAIMS)
    assert candidate.table_count == 29


@pytest.mark.parametrize(
    "mutate",
    [
        lambda rows: rows.pop("appointment"),
        lambda rows: rows.update({"unexpected": tuple()}),
        lambda rows: rows.update({"appointment": list(rows["appointment"])}),
        lambda rows: rows["appointment"][0].pop("created_at"),
        lambda rows: rows["appointment"][0].update({"unexpected": "value"}),
        lambda rows: rows["appointment"][0].update({"id": str(SOURCE_ID)}),
        lambda rows: rows["appointment"][0].update({"created_at": None}),
        lambda rows: rows["doctor"][0].update({"role": "not_a_doctor_role"}),
        lambda rows: rows["provider_organization"][0].update(
            {"access_code_version": 2**31}
        ),
        lambda rows: rows.update(
            {"appointment": (rows["appointment"][0], rows["appointment"][0].copy())}
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
    with pytest.raises(
        executor.SanitizerDataContractExecutorFailure,
        match=r"^data_contract_executor\.[a-z_]+$",
    ):
        _engine().transform(
            authenticated_sanitizer_evidence=fast_case.authenticated,
            source_rows=rows,
        )


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
    with pytest.raises(executor.SanitizerDataContractExecutorFailure):
        _engine().transform(
            authenticated_sanitizer_evidence=fast_case.authenticated,
            source_rows=rows,
        )
    assert minted == 0
    assert rows == original


def test_mixed_key_and_forged_candidates_refuse(fast_case: Any) -> None:
    candidate = _transformed(fast_case)
    _engine().validate_candidate(candidate)
    with pytest.raises(
        executor.SanitizerDataContractExecutorFailure,
        match="data_contract_executor.candidate_invalid",
    ):
        _engine(OTHER_KEY).validate_candidate(candidate)
    forged = object.__new__(executor.SanitizerCandidateDataset)
    for field in candidate.__dataclass_fields__:
        object.__setattr__(forged, field, getattr(candidate, field))
    object.__setattr__(forged, "_proof_sha256", "0" * 64)
    with pytest.raises(executor.SanitizerDataContractExecutorFailure):
        _engine().validate_candidate(forged)
    with pytest.raises(
        executor.SanitizerDataContractExecutorFailure,
        match="data_contract_executor.candidate_invalid",
    ):
        _engine(clock=lambda: PREFLIGHT_AT - timedelta(seconds=1)).validate_candidate(
            candidate
        )


def test_repr_pickle_errors_and_logs_never_disclose_rows_or_keys(
    fast_case: Any, caplog: pytest.LogCaptureFixture
) -> None:
    secret = "SECRET-ROW-DATA-DO-NOT-LEAK"
    rows = _source_rows()
    rows["appointment"][0]["id"] = secret
    with pytest.raises(executor.SanitizerDataContractExecutorFailure) as caught:
        _engine().transform(
            authenticated_sanitizer_evidence=fast_case.authenticated,
            source_rows=rows,
        )
    assert secret not in str(caught.value)
    assert KEY.hex() not in str(caught.value)
    assert not caplog.records

    engine = _engine()
    candidate = _transformed(fast_case)
    assert secret not in repr(engine)
    assert KEY.hex() not in repr(engine)
    assert "person@example.com" not in repr(candidate)
    with pytest.raises(executor.SanitizerDataContractExecutorFailure):
        pickle.dumps(candidate)
    with pytest.raises(executor.SanitizerDataContractExecutorFailure):
        pickle.dumps(engine)


def test_configuration_is_exact_and_public_constructors_refuse() -> None:
    with pytest.raises(executor.SanitizerDataContractExecutorFailure):
        executor.SanitizerDataContractExecutor()
    with pytest.raises(executor.SanitizerDataContractExecutorFailure):
        executor.SanitizerCandidateDataset()
    changed = bytearray(DATA_CONTRACT.read_bytes())
    changed[-2] = ord(" ") if changed[-2] != ord(" ") else ord("\n")
    with pytest.raises(
        executor.SanitizerDataContractExecutorFailure,
        match="data_contract_executor.policy_invalid",
    ):
        executor.SanitizerDataContractExecutor._from_reviewed_configuration(
            clock=lambda: PREFLIGHT_AT,
            transform_key=KEY,
            transform_key_version=KEY_VERSION,
            sanitizer_policy_bytes=POLICY.read_bytes(),
            inventory_policy_bytes=INVENTORY_POLICY.read_bytes(),
            data_contract_bytes=bytes(changed),
        )


def test_configuration_is_deeply_immutable(fast_case: Any) -> None:
    engine = _engine()
    with pytest.raises(TypeError):
        engine._contract["tables"]["doctor"]["columns"]["email"] = "preserve_approved"
    with pytest.raises(executor.SanitizerDataContractExecutorFailure):
        engine._contract = {}
    assert _transformed(fast_case)._rows_for_table("doctor")[0]["email"] != (
        "person@example.com"
    )


def test_transform_has_no_filesystem_reauthentication(
    real_case: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = _engine()
    rows = _source_rows()

    def refuse(*args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        raise AssertionError("filesystem or legacy reauthentication invoked")

    monkeypatch.setattr(Path, "read_bytes", refuse)
    monkeypatch.setattr(
        executor.evidence_authority, "_reauthenticate_at_current_time", refuse
    )
    candidate = engine.transform(
        authenticated_sanitizer_evidence=real_case.authenticated,
        source_rows=rows,
    )
    engine.validate_candidate(candidate)


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
    assert not imports & {
        "azure",
        "asyncpg",
        "boto3",
        "httpx",
        "os",
        "pathlib",
        "psycopg",
        "requests",
        "sqlalchemy",
        "subprocess",
    }
    calls = {
        node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, (ast.Attribute, ast.Name))
    }
    assert not calls & {
        "Popen",
        "commit",
        "connect",
        "execute",
        "getenv",
        "open",
        "rollback",
        "run",
    }
    code = f"""
import json, sys
sys.path.insert(0, {str(ROOT)!r})
from scripts import staging_restore_sanitizer_data_contract_executor
forbidden_roots = (
    '_posixsubprocess', 'asyncpg', 'azure', 'boto3', 'httpx', 'psycopg',
    'requests', 'sqlalchemy', 'subprocess',
)
forbidden_scripts = {{
    'scripts.authenticate_staging_github_attestation',
    'scripts.bind_staging_restore_sanitizer_evidence',
    'scripts.compose_staging_restore_evidence_binding',
    'scripts.staging_restore_runtime_adapters',
    'scripts.staging_restore_safe_submit',
    'scripts.verify_staging_source_provider_observation',
}}
loaded = sorted(
    name for name in sys.modules
    if name in forbidden_scripts
    or any(name == root or name.startswith(root + '.') for root in forbidden_roots)
)
print(json.dumps(loaded))
"""
    completed = subprocess.run(
        [sys.executable, "-I", "-c", code],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == []
    assert hashlib.sha256(POLICY.read_bytes()).hexdigest() == (
        sanitizer.CANONICAL_POLICY_SHA256
    )
    assert executor.SANITIZER_POLICY_SHA256 == sanitizer.CANONICAL_POLICY_SHA256
    assert hashlib.sha256(INVENTORY_POLICY.read_bytes()).hexdigest() == (
        inventory.CANONICAL_POLICY_SHA256
    )
    assert executor.INVENTORY_POLICY_SHA256 == inventory.CANONICAL_POLICY_SHA256
    assert hashlib.sha256(DATA_CONTRACT.read_bytes()).hexdigest() == (
        sanitizer.CANONICAL_DATA_CONTRACT_SHA256
    )
    assert executor.DATA_CONTRACT_SHA256 == sanitizer.CANONICAL_DATA_CONTRACT_SHA256
    assert executor.HMAC_DOMAIN == sanitizer.HMAC_DOMAIN
