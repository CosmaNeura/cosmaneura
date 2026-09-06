from __future__ import annotations

import ast
import hashlib
import hmac
import json
import pickle
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from scripts import bind_staging_restore_fixed_catalog_source_state as fixed
from scripts import bind_staging_restore_sanitizer_evidence as evidence_binding
from scripts import compose_staging_restore_evidence_binding as composition
from scripts import staging_restore_sanitizer_source_snapshot as snapshot
from scripts import staging_synthetic_inventory as inventory

from tests.infrastructure import (
    _staging_restore_fixed_catalog_source_state_helpers as fixed_helpers,
)
from tests.infrastructure import test_staging_controller_receipt_v2 as content_helpers
from tests.infrastructure import test_staging_synthetic_inventory as inventory_helpers
from tests.infrastructure.test_staging_restore_sanitizer_data_contract_executor import (
    _source_rows,
)

ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "scripts/staging_restore_sanitizer_source_snapshot.py"
CONTRACTS = ROOT / "contracts/staging-rehearsal/v1"
POLICY = CONTRACTS / "synthetic-data-policy.json"
CONTRACT = CONTRACTS / "restore-sanitizer-source-snapshot-contract.json"
KEY = inventory_helpers.KEY
BIND_AT = fixed_helpers.NOW + timedelta(seconds=45)
SNAPSHOT_AT = BIND_AT + timedelta(seconds=5)
FAILURE = snapshot.SanitizerSourceSnapshotFailure


def _wire(value: Any) -> Any:
    if type(value) is uuid.UUID:
        return str(value)
    if type(value) is datetime:
        return value.isoformat()
    if type(value) is date:
        return value.isoformat()
    if type(value) is dict:
        return {name: _wire(item) for name, item in value.items()}
    if type(value) in {tuple, list}:
        return [_wire(item) for item in value]
    return value


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        _wire(value), ensure_ascii=False, allow_nan=False, separators=(",", ":")
    ).encode("utf-8")


def _schemas() -> dict[str, dict[str, Any]]:
    return inventory._expected_tables(json.loads(POLICY.read_bytes()), "pre")  # noqa: SLF001


def _frames() -> tuple[tuple[str, tuple[tuple[bytes | None, bytes], ...]], ...]:
    schemas = _schemas()
    source = _source_rows()
    result = []
    for table_name in sorted(schemas):
        spec = schemas[table_name]
        if spec["disposition"] == "required_empty":
            result.append((table_name, ()))
            continue
        rows = []
        for row in source[table_name]:
            ordered = {name: row[name] for name, _type, _required in spec["columns"]}
            identities = spec.get("identity_columns", [])
            identity = (
                _json_bytes([row[name] for name in identities]) if identities else None
            )
            rows.append((identity, _json_bytes(ordered)))
        result.append((table_name, tuple(rows)))
    return tuple(result)


def _mac(domain: str, values: tuple[bytes, ...]) -> str:
    value = hmac.new(
        KEY,
        inventory.HMAC_DOMAIN + domain.encode("ascii") + b"\x00",
        hashlib.sha256,
    )
    for item in values:
        value.update(len(item).to_bytes(8, "big"))
        value.update(item)
    return value.hexdigest()


def _document_for(
    frames: tuple[tuple[str, tuple[tuple[bytes | None, bytes], ...]], ...],
) -> dict[str, Any]:
    document = fixed_helpers._inventory_document()
    schemas = _schemas()
    identities: dict[str, str] = {}
    for table_name, rows in frames:
        document["counts"][f"database.{table_name}"] = len(rows)
        if schemas[table_name]["disposition"] == "required_empty":
            document["state_hmacs"][table_name] = _mac(
                f"database-table:{table_name}:empty-count", (b"0",)
            )
            continue
        document["state_hmacs"][table_name] = _mac(
            f"{snapshot.INVENTORY_POLICY_ID}:database-table:{table_name}:state",
            tuple(state for _identity, state in rows),
        )
        if schemas[table_name].get("identity_columns"):
            identities[table_name] = _mac(
                f"{snapshot.INVENTORY_POLICY_ID}:database-table:{table_name}:identities",
                tuple(identity for identity, _state in rows if identity is not None),
            )
    document["identity_hmacs"] = identities
    return document


@pytest.fixture(scope="module")
def authenticated_case() -> Iterator[SimpleNamespace]:
    frames = _frames()
    raw = fixed_helpers._seal(_document_for(frames))
    monkeypatch = pytest.MonkeyPatch()
    original_evidence = content_helpers._evidence

    def evidence() -> dict[str, Any]:
        value = original_evidence()
        value["expires_at"] = "2026-09-05T12:10:30Z"
        return value

    monkeypatch.setattr(content_helpers, "_evidence", evidence)
    try:
        with fixed_helpers._chain(raw) as (_authority, built, _written, target):
            source = fixed.FixedCatalogSourceStateBinder._from_reviewed_configuration(
                clock=lambda: fixed_helpers.NOW
            ).bind(target_binding=target, inventory_receipt_bytes=raw)
            restore = composition._mint_binding(
                json.loads(built.intent_bytes)["evidence_binding"]
            )
            authenticated = (
                evidence_binding.SanitizerEvidenceBinder._from_reviewed_configuration(
                    clock=lambda: BIND_AT
                ).bind(
                    fixed_catalog_source_state=source,
                    restore_evidence_binding=restore,
                )
            )
            yield SimpleNamespace(
                authenticated=authenticated,
                frames=frames,
                document=json.loads(raw),
            )
    finally:
        monkeypatch.undo()


def _authenticator(*times: datetime, key: bytes = KEY) -> Any:
    values = iter(times or (SNAPSHOT_AT, SNAPSHOT_AT))
    return snapshot.SanitizerSourceSnapshotAuthenticator._from_reviewed_configuration(
        clock=lambda: next(values),
        inventory_hmac_key=key,
        inventory_hmac_key_version=inventory.SUPPORTED_HMAC_KEY_VERSION,
        inventory_policy_bytes=POLICY.read_bytes(),
        source_snapshot_contract_bytes=CONTRACT.read_bytes(),
    )


@pytest.fixture
def fast_case(
    authenticated_case: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> Any:
    monkeypatch.setattr(
        snapshot.SanitizerSourceSnapshotAuthenticator,
        "_reauthenticate",
        lambda *_args: None,
    )
    return authenticated_case


def _authenticate(
    case: SimpleNamespace, *, frames: Any = None, key: bytes = KEY
) -> Any:
    return _authenticator(key=key).authenticate(
        authenticated_sanitizer_evidence=case.authenticated,
        ordered_table_frames=case.frames if frames is None else frames,
    )


def _clone(value: Any) -> Any:
    result = object.__new__(type(value))
    for field in value.__dataclass_fields__:
        object.__setattr__(result, field, getattr(value, field))
    return result


def _revalidate(value: Any, key: object = KEY) -> None:
    snapshot._validate_retained_snapshot_at_current_time(  # noqa: SLF001
        value, now=SNAPSHOT_AT, inventory_hmac_key=key
    )


def _module_trace(error: BaseException) -> str:
    rendered, seen = [], set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        frame = current.__traceback__
        while frame is not None:
            if frame.tb_frame.f_globals.get("__name__") == snapshot.__name__:
                rendered.append(repr(frame.tb_frame.f_locals))
            frame = frame.tb_next
        current = current.__cause__ or current.__context__
    return "".join(rendered)


def _replace_rows(
    frames: tuple[tuple[str, tuple[tuple[bytes | None, bytes], ...]], ...],
    table_name: str,
    rows: tuple[tuple[bytes | None, bytes], ...],
) -> tuple[tuple[str, tuple[tuple[bytes | None, bytes], ...]], ...]:
    return tuple(
        (name, rows if name == table_name else current) for name, current in frames
    )


def test_contract_is_hash_pinned_closed_world_and_dormant() -> None:
    contract = json.loads(CONTRACT.read_bytes())
    assert (
        hashlib.sha256(CONTRACT.read_bytes()).hexdigest()
        == snapshot.SOURCE_SNAPSHOT_CONTRACT_SHA256
    )
    authentication = contract["authentication"]
    assert all(
        authentication[name] is True
        for name in (
            "raw_hmac_verified_before_json_parse",
            "retained_revalidation_requires_inventory_hmac_key",
            "keyed_proof_binds_snapshot_and_evidence_identity",
        )
    )
    assert {
        name: authentication[name]
        for name in snapshot._HMAC_CONTRACT  # noqa: SLF001
    } == snapshot._HMAC_CONTRACT  # noqa: SLF001
    assert not contract["input"]["parallel_decoded_rows_permitted"]
    assert not contract["output"]["raw_frames_retained"]
    assert not contract["output"]["private_unkeyed_process_local_witness_retained"]
    assert not any(contract["claims"][name] for name in snapshot._NEGATIVE_CLAIMS)  # noqa: SLF001
    assert contract["status"]["executor_consumption_implemented"] is False


def test_exact_frames_authenticate_to_sealed_typed_immutable_rows(
    authenticated_case: SimpleNamespace,
) -> None:
    result = _authenticate(authenticated_case)
    tables = dict(result._tables)  # noqa: SLF001
    assert (
        result.table_count,
        result.retained_table_count,
        result.required_empty_table_count,
        result.source_row_count,
    ) == (62, 29, 33, 29)
    assert len(tables) == len(result._raw_fragment_proofs) == 29  # noqa: SLF001
    assert result.authenticated_at == SNAPSHOT_AT
    assert result.source_snapshot_authenticated
    assert not any(getattr(result, name) for name in snapshot._NEGATIVE_CLAIMS)  # noqa: SLF001
    appointment = result._rows_for_table("appointment")[0]  # noqa: SLF001
    assert (type(appointment["id"]), type(appointment["created_at"])) == (
        uuid.UUID,
        datetime,
    )
    assert appointment["created_at"].tzinfo is UTC
    assert "sensitive" in repr(result) and "example.com" not in repr(result)
    with pytest.raises((AttributeError, TypeError)):
        result.source_row_count = 0


def test_keyed_revalidation_detects_replay_wrong_key_and_retained_tampering(
    authenticated_case: SimpleNamespace,
) -> None:
    result = _authenticate(authenticated_case)
    _revalidate(result)
    with pytest.raises(FAILURE, match="^source_snapshot.output_invalid$"):
        result._reauthenticate_at_current_time(now=SNAPSHOT_AT)  # noqa: SLF001
    for key in (None, b"x" * 32):
        with pytest.raises(FAILURE, match="^source_snapshot.output_invalid$"):
            _revalidate(result, key)
    forged = _clone(result)
    with pytest.raises(FAILURE, match="^source_snapshot.output_invalid$"):
        _revalidate(forged)
    row = result._tables[0][1][0]  # noqa: SLF001
    original_items = row._items  # noqa: SLF001
    object.__setattr__(
        row,
        "_items",
        ((original_items[0][0], uuid.UUID(int=2)), *original_items[1:]),
    )
    with pytest.raises(FAILURE, match="^source_snapshot.output_invalid$"):
        _revalidate(result)
    object.__setattr__(row, "_items", original_items)
    original_proofs = result._raw_fragment_proofs  # noqa: SLF001
    name, proofs = original_proofs[0]
    object.__setattr__(
        result, "_raw_fragment_proofs", ((name, ("0" * 64,)), *original_proofs[1:])
    )
    with pytest.raises(FAILURE, match="^source_snapshot.output_invalid$"):
        _revalidate(result)
    object.__setattr__(result, "_raw_fragment_proofs", original_proofs)
    for name, item in (
        ("table_count", 62.0),
        ("source_row_count", True),
        ("authenticated_at", SNAPSHOT_AT - timedelta(seconds=1)),
    ):
        forged = _clone(result)
        object.__setattr__(forged, name, item)
        with pytest.raises(FAILURE, match="^source_snapshot.output_invalid$"):
            _revalidate(forged)
    replacement = _clone(result._authenticated_evidence)  # noqa: SLF001
    object.__setattr__(result, "_authenticated_evidence", replacement)
    with pytest.raises(FAILURE, match="^source_snapshot.output_invalid$"):
        _revalidate(result)


def test_raw_hmac_is_verified_before_json_parse(
    fast_case: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    table = "synthetic_preview_appointment_fixture"
    rows = dict(fast_case.frames)[table]
    malformed = _replace_rows(
        fast_case.frames,
        table,
        ((None, rows[0][1][:-1] + b"!"),),
    )
    monkeypatch.setattr(
        snapshot,
        "_parse_row",
        lambda *_args, **_kwargs: pytest.fail(
            "raw row parsed before HMAC verification"
        ),
    )
    with pytest.raises(FAILURE, match="^source_snapshot.integrity_invalid$"):
        _authenticate(fast_case, frames=malformed)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda frames: list(frames),
        lambda frames: tuple(reversed(frames)),
        lambda frames: frames[:-1],
        lambda frames: _replace_rows(frames, "zoom_meeting", ((None, b"{}"),)),
        lambda frames: _replace_rows(
            frames,
            "appointment",
            ((b"[]", dict(frames)["appointment"][0][1]),),
        ),
        lambda frames: _replace_rows(
            frames,
            "authority_actor",
            ((None, dict(frames)["authority_actor"][0][1]),),
        ),
        lambda frames: _replace_rows(
            frames,
            "appointment",
            ((None, cast(Any, bytearray(dict(frames)["appointment"][0][1]))),),
        ),
    ],
)
def test_only_exact_builtin_closed_world_frame_shape_is_accepted(
    fast_case: SimpleNamespace, mutation: Any
) -> None:
    with pytest.raises(FAILURE, match="^source_snapshot.frames_invalid$"):
        _authenticate(fast_case, frames=mutation(fast_case.frames))


@pytest.mark.parametrize(
    ("raw", "column", "data_type", "required"),
    [
        (b'{"id":"01890f4c-7b00-7000-8000-000000000001","id":"x"}', "id", "uuid", True),
        (
            b'\xef\xbb\xbf{"id":"01890f4c-7b00-7000-8000-000000000001"}',
            "id",
            "uuid",
            True,
        ),
        (b'{"id":"bad\\u0000value"}', "id", "uuid", True),
        (b'{"id":"01890f4c-7b00-7000-8000-000000000001"} trailing', "id", "uuid", True),
        (b'{"id":"01890F4C-7B00-7000-8000-000000000001"}', "id", "uuid", True),
        (b'{"x":"2026-01-02T03:04:05"}', "x", "timestamp with time zone", True),
        (b'{"created_at":"infinity"}', "created_at", "timestamp with time zone", True),
        (b'{"value":1e999}', "value", "double precision", True),
        (b'{"payload":null}', "payload", "jsonb", True),
        (b'{"payload":1.0000000000000000001}', "payload", "json", True),
        (b'{"payload":1e-999}', "payload", "jsonb", True),
        (b'{"payload":-0}', "payload", "json", True),
        (b'{"payload":' + b"[" * 34 + b"0" + b"]" * 34 + b"}", "payload", "json", True),
        (b'{"payload":' + b"1" * 129 + b"}", "payload", "json", True),
        (b'{"id":"x"} ', "id", "text", True),
    ],
)
def test_row_decoder_rejects_ambiguous_or_noncanonical_values(
    raw: bytes, column: str, data_type: str, required: bool
) -> None:
    spec = snapshot._TableSpec(
        "example", "synthetic_lineage", ((column, data_type, required),), ()
    )  # noqa: SLF001
    with pytest.raises(FAILURE, match="^source_snapshot.frames_invalid$"):
        snapshot._parse_row(raw, spec)  # noqa: SLF001


def test_row_decoder_rejects_column_order() -> None:
    spec = snapshot._TableSpec(  # noqa: SLF001
        "example",
        "synthetic_lineage",
        (("first", "text", True), ("second", "integer", True)),
        (),
    )
    with pytest.raises(FAILURE, match="source_snapshot.frames_invalid"):
        snapshot._parse_row(b'{"second":1,"first":"x"}', spec)  # noqa: SLF001


def test_nested_json_is_deep_immutable_and_duplicate_keys_refuse() -> None:
    spec = snapshot._TableSpec(  # noqa: SLF001
        "example", "fixed_catalog", (("payload", "jsonb", True),), ()
    )
    row = snapshot._parse_row(b'{"payload":{"a":[1,-0.0,true,null,{"b":"c"}]}}', spec)  # noqa: SLF001
    payload = row["payload"]
    assert type(payload) is snapshot._FrozenMap  # noqa: SLF001
    assert type(payload["a"]) is tuple
    assert str(payload["a"][1]) == "-0.0"
    assert type(payload["a"][4]) is snapshot._FrozenMap  # noqa: SLF001
    with pytest.raises((AttributeError, TypeError)):
        payload["a"].append("no")
    with pytest.raises(FAILURE, match="source_snapshot.frames_invalid"):
        snapshot._parse_row(b'{"payload":{"a":1,"a":2}}', spec)  # noqa: SLF001


def test_recorded_postgresql_wire_forms_decode_without_regeneration() -> None:
    columns = (
        ("id", "uuid", True),
        ("observed_at", "timestamp with time zone", True),
        ("ratio", "double precision", True),
        ("json_payload", "json", True),
        ("jsonb_payload", "jsonb", True),
    )
    spec = snapshot._TableSpec("golden", "fixed_catalog", columns, ("id",))  # noqa: SLF001
    raw = (
        b'{"id":"01890f4c-7b00-7000-8000-000000000001",'
        b'"observed_at":"2026-01-02T03:04:05+00:00","ratio":-0,'
        b'"json_payload":{"z": 1, "a": [true, null]},'
        b'"jsonb_payload":{"a": [true, null], "z": 1}}'
    )
    row = snapshot._parse_row(raw, spec)  # noqa: SLF001
    snapshot._parse_identity(  # noqa: SLF001
        b'["01890f4c-7b00-7000-8000-000000000001"]', spec, row
    )
    assert row["observed_at"].tzinfo is UTC and str(row["ratio"]) == "-0.0"
    assert tuple(row["json_payload"]) == ("z", "a")
    assert tuple(row["jsonb_payload"]) == ("a", "z")


def test_start_and_end_reauthentication_and_clock_guards(
    authenticated_case: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    final = SNAPSHOT_AT + timedelta(seconds=1)
    seen = []
    original = snapshot._validate_retained_evidence  # noqa: SLF001

    def observe(
        value: Any, *, now: datetime, inventory_policy_bytes: Any = None
    ) -> Any:
        seen.append(now)
        return original(value, now=now, inventory_policy_bytes=inventory_policy_bytes)

    monkeypatch.setattr(snapshot, "_validate_retained_evidence", observe)
    authenticator = _authenticator(SNAPSHOT_AT, final)
    result = authenticator.authenticate(
        authenticated_sanitizer_evidence=authenticated_case.authenticated,
        ordered_table_frames=authenticated_case.frames,
    )
    assert result.authenticated_at == final
    assert seen == [SNAPSHOT_AT, final]


def test_authenticate_snapshot_revalidation_and_preflight_never_read_files(
    authenticated_case: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    authenticator = _authenticator()
    preflight = snapshot.SanitizerSourceSnapshotPreflight._from_reviewed_configuration(
        clock=lambda: SNAPSHOT_AT
    )
    monkeypatch.setattr(
        Path,
        "read_bytes",
        lambda _self: pytest.fail("pure boundary attempted filesystem I/O"),
    )
    result = authenticator.authenticate(
        authenticated_sanitizer_evidence=authenticated_case.authenticated,
        ordered_table_frames=authenticated_case.frames,
    )
    _revalidate(result)
    with pytest.raises(FAILURE, match=f"^{snapshot.ACTIVATION_FAILURE}$"):
        preflight.preflight(
            authenticated_sanitizer_evidence=authenticated_case.authenticated
        )


def test_failure_tracebacks_do_not_retain_module_owned_keys_frames_or_rows(
    authenticated_case: SimpleNamespace,
) -> None:
    secret_frame = b"RAW_SECRET_FRAME"
    secret_key = b"TRACE_KEY_MATERIAL".ljust(32, b"!")
    frames = _replace_rows(
        authenticated_case.frames,
        "synthetic_preview_appointment_fixture",
        ((None, secret_frame),),
    )

    def capture(call: Any) -> None:
        with pytest.raises(FAILURE) as raised:
            call()
        retained = _module_trace(raised.value)
        assert not any(
            token in retained
            for token in ("RAW_SECRET_FRAME", "TRACE_KEY_MATERIAL", "example.com")
        )

    capture(
        lambda: _authenticator(key=secret_key).authenticate(
            authenticated_sanitizer_evidence=authenticated_case.authenticated,
            ordered_table_frames=frames,
        )
    )
    capture(
        lambda: snapshot.SanitizerSourceSnapshotAuthenticator._from_reviewed_configuration(
            clock=lambda: SNAPSHOT_AT,
            inventory_hmac_key=secret_key,
            inventory_hmac_key_version=inventory.SUPPORTED_HMAC_KEY_VERSION,
            inventory_policy_bytes=POLICY.read_bytes(),
            source_snapshot_contract_bytes=secret_frame,
        )
    )
    result = _authenticate(authenticated_case)
    for call in (
        lambda: _revalidate(result, secret_key),
        lambda: result._rows_for_table(secret_frame.decode()),  # noqa: SLF001
        lambda: pickle.dumps(result),
    ):
        capture(call)


def test_aggregate_row_limit_fails_before_visiting_frame_bytes(
    authenticated_case: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    authenticator = _authenticator()
    state = dict(authenticated_case.frames)["appointment"][0][1]
    visited = []

    def tracked_len(value: Any) -> int:
        visited.append(value is state)
        return len(value)

    monkeypatch.setattr(snapshot, "len", tracked_len, raising=False)
    monkeypatch.setattr(snapshot, "MAXIMUM_TOTAL_ROWS", 0)
    with pytest.raises(FAILURE, match="source_snapshot.frames_invalid"):
        authenticator._validated_frames(authenticated_case.frames)  # noqa: SLF001
    assert True not in visited


def test_configuration_output_and_module_surfaces_are_narrow() -> None:
    for key in (b"short", bytearray(KEY)):
        with pytest.raises(FAILURE, match="source_snapshot.configuration_invalid"):
            _authenticator(key=key)  # type: ignore[arg-type]

    tree = ast.parse(SCRIPT.read_text())
    imports = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert not imports & set(
        "asyncio httpx os pathlib psycopg requests socket sqlalchemy subprocess urllib".split()
    )
    source = SCRIPT.read_text()
    assert all(
        token not in source
        for token in ("workflow_dispatch", "azure/login", "psycopg.connect", "open(")
    )
