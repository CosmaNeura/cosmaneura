from __future__ import annotations

import ast
import copy
import hashlib
import json
import pickle
from pathlib import Path
from typing import Any, cast

import pytest

from scripts import (
    staging_restore_sanitizer_privileged_global_observation_frames as observation_frames,
)
from scripts import (
    staging_restore_sanitizer_privileged_global_query_grammar as query_grammar,
)

ROOT = Path(__file__).parents[2]
CONTRACT = (
    ROOT / "contracts/staging-rehearsal/v1/"
    "restore-sanitizer-privileged-global-observation-frames-contract.json"
)
SCRIPT = (
    ROOT / "scripts/staging_restore_sanitizer_privileged_global_observation_frames.py"
)


def _sample_value(
    *, logical_type: str, nullable: bool, column_name: str, seed: int
) -> observation_frames.Scalar:
    if nullable and seed % 2:
        return None
    if logical_type == "boolean":
        return bool(seed % 2)
    if logical_type in {"int16", "int32", "int64"}:
        return seed + 1
    if logical_type == "text":
        return f"{column_name}-caf\u00e9-{seed}"
    raise AssertionError(logical_type)


def _row(frame: dict[str, Any], seed: int = 0) -> tuple[object, ...]:
    return tuple(
        _sample_value(
            logical_type=column["type"],
            nullable=column["nullable"],
            column_name=column["name"],
            seed=seed,
        )
        for column in frame["exact_columns"]
    )


def _frames(names: tuple[str, ...], *, seed: int = 0, rows_each: int = 1) -> tuple:
    grammar = query_grammar.load_privileged_global_query_grammar_contract()
    by_name = {frame["frame"]: frame for frame in grammar["grammar"]["frames"]}
    return tuple(
        (
            name,
            tuple(_row(by_name[name], seed + row) for row in range(rows_each)),
        )
        for name in names
    )


def _raw_bundle(*, database_count: int = 2) -> tuple:
    return (
        (
            "server_once",
            _frames(query_grammar.SERVER_ONCE_FRAME_NAMES),
        ),
        (
            "per_database",
            tuple(
                (
                    100 + index,
                    f"database-{index}",
                    _frames(
                        query_grammar.PER_DATABASE_FRAME_NAMES,
                        seed=10 + index,
                    ),
                )
                for index in range(database_count)
            ),
        ),
    )


def _replace_server_frame(bundle: tuple, name: str, rows: tuple) -> tuple:
    server = tuple(
        (frame, rows if frame == name else current) for frame, current in bundle[0][1]
    )
    return (("server_once", server), bundle[1])


def _replace_server_cell(
    bundle: tuple, *, frame_name: str, column_index: int, value: object
) -> tuple:
    rows = dict(bundle[0][1])[frame_name]
    row = rows[0]
    changed = (*row[:column_index], value, *row[column_index + 1 :])
    return _replace_server_frame(bundle, frame_name, (changed, *rows[1:]))


def test_contract_is_hash_pinned_closed_world_and_dormant() -> None:
    assert hashlib.sha256(CONTRACT.read_bytes()).hexdigest() == (
        observation_frames.CONTRACT_SHA256
    )
    contract = observation_frames.load_privileged_global_observation_frames_contract()

    assert contract["closed_world"] is True
    assert contract["upstream_policies"] == {
        "privileged_global_query_grammar": {
            "policy_id": query_grammar.load_privileged_global_query_grammar_contract()[
                "policy_id"
            ],
            "sha256": f"sha256:{query_grammar.CONTRACT_SHA256}",
        }
    }
    assert contract["status"]["frame_decoder_implemented"] is True
    assert contract["status"]["observer_implemented"] is False
    assert contract["status"]["activation_ready"] is False
    assert contract["claims"] == observation_frames.CLAIMS
    assert all(
        contract["claims"][name] is False
        for name in observation_frames._NEGATIVE_CLAIMS
    )


def test_exact_partitioned_frames_decode_to_sealed_non_authoritative_metadata() -> None:
    raw = _raw_bundle()
    value = observation_frames._decode_privileged_global_observation_frames(raw)

    assert (
        type(value)
        is observation_frames.DecodedSanitizerPrivilegedGlobalObservationFrames
    )
    assert value._is_valid()
    assert value.kind == observation_frames.FRAME_SET_KIND
    assert value.server_once_frame_count == 11
    assert value.database_count == 2
    assert value.per_database_frame_count == 4
    assert value.frame_count == 15
    assert value.row_count == 15
    assert value.canonical_byte_count > 0
    assert value.frame_set_sha256.startswith("sha256:")
    assert value.database_partition_shape_validated is True
    assert value.frame_schema_validated is True
    assert value.row_order_preserved is True
    assert value.resource_bounds_enforced is True
    assert all(
        getattr(value, name) is False for name in observation_frames._NEGATIVE_CLAIMS
    )
    assert value._bundle == (raw[0][1], raw[1][1])
    assert "database-" not in repr(value)
    assert "caf\u00e9" not in repr(value)


def test_output_is_immutable_nonserializable_and_tamper_evident() -> None:
    value = observation_frames._decode_privileged_global_observation_frames(
        _raw_bundle()
    )

    with pytest.raises(
        observation_frames.SanitizerPrivilegedGlobalObservationFramesFailure
    ):
        value.row_count = 0
    with pytest.raises(
        observation_frames.SanitizerPrivilegedGlobalObservationFramesFailure
    ):
        del value.row_count
    with pytest.raises(
        observation_frames.SanitizerPrivilegedGlobalObservationFramesFailure
    ):
        copy.copy(value)
    with pytest.raises(
        observation_frames.SanitizerPrivilegedGlobalObservationFramesFailure
    ):
        copy.deepcopy(value)
    with pytest.raises(
        observation_frames.SanitizerPrivilegedGlobalObservationFramesFailure
    ):
        pickle.dumps(value)
    with pytest.raises(TypeError):
        json.dumps(value)
    with pytest.raises(TypeError):
        vars(value)

    object.__setattr__(value, "database_count", 0)
    assert not value._is_valid()


def test_section_frame_and_database_container_shapes_are_closed() -> None:
    raw = _raw_bundle()
    hostile_iterator_called = False

    class Hostile:
        def __iter__(self) -> Any:
            nonlocal hostile_iterator_called
            hostile_iterator_called = True
            raise RuntimeError("must not run")

    server = raw[0][1]
    databases = raw[1][1]
    variants: tuple[object, ...] = (
        list(raw),
        raw[:1],
        (raw[1], raw[0]),
        (("unknown", server), raw[1]),
        (("server_once", list(server)), raw[1]),
        (("server_once", (server[1], server[0], *server[2:])), raw[1]),
        (raw[0], ("per_database", ([*databases],))),
        (raw[0], ("per_database", ((100, "database", list(databases[0][2])),))),
        Hostile(),
    )
    for changed in variants:
        with pytest.raises(
            observation_frames.SanitizerPrivilegedGlobalObservationFramesFailure,
            match="privileged_global_observation_frames.input_invalid",
        ):
            observation_frames._decode_privileged_global_observation_frames(changed)
    assert hostile_iterator_called is False


def test_database_keys_are_exact_unique_and_bounded() -> None:
    raw = _raw_bundle()
    first, second = raw[1][1]
    variants = (
        (raw[0], ("per_database", ((True, first[1], first[2]),))),
        (raw[0], ("per_database", ((0, first[1], first[2]),))),
        (raw[0], ("per_database", ((first[0], "", first[2]),))),
        (raw[0], ("per_database", ((first[0], "bad\x00name", first[2]),))),
        (raw[0], ("per_database", ((first[0], "bad\ud800name", first[2]),))),
        (raw[0], ("per_database", (first, (first[0], second[1], second[2])))),
        (raw[0], ("per_database", (first, (second[0], first[1], second[2])))),
    )
    for changed in variants:
        with pytest.raises(
            observation_frames.SanitizerPrivilegedGlobalObservationFramesFailure,
            match="privileged_global_observation_frames.input_invalid",
        ):
            observation_frames._decode_privileged_global_observation_frames(changed)

    grammar = query_grammar.load_privileged_global_query_grammar_contract()
    changed_grammar = copy.deepcopy(grammar)
    changed_grammar["resource_bounds"]["maximum_database_entries"] = 1
    with pytest.raises(
        observation_frames.SanitizerPrivilegedGlobalObservationFramesFailure,
        match="privileged_global_observation_frames.bounds_invalid",
    ):
        observation_frames._decode_bundle(raw, grammar=changed_grammar)

    oversized_name = (
        raw[0],
        ("per_database", ((first[0], "x" * 20_000, first[2]),)),
    )
    with pytest.raises(
        observation_frames.SanitizerPrivilegedGlobalObservationFramesFailure,
        match="privileged_global_observation_frames.bounds_invalid",
    ):
        observation_frames._decode_privileged_global_observation_frames(oversized_name)


def test_scalar_types_ranges_nullability_and_text_encoding_are_exact() -> None:
    raw = _raw_bundle()
    invalid = (
        _replace_server_cell(
            raw, frame_name="password_null_state", column_index=0, value=None
        ),
        _replace_server_cell(
            raw, frame_name="password_null_state", column_index=0, value=True
        ),
        _replace_server_cell(
            raw, frame_name="runtime_role_sessions", column_index=2, value=2**31
        ),
        _replace_server_cell(
            raw,
            frame_name="password_null_state",
            column_index=1,
            value="bad\x00name",
        ),
        _replace_server_cell(
            raw,
            frame_name="password_null_state",
            column_index=1,
            value="bad\ud800text",
        ),
    )
    for changed in invalid:
        with pytest.raises(
            observation_frames.SanitizerPrivilegedGlobalObservationFramesFailure,
            match="privileged_global_observation_frames.input_invalid",
        ):
            observation_frames._decode_privileged_global_observation_frames(changed)

    nullable = _replace_server_cell(
        raw, frame_name="runtime_role_sessions", column_index=0, value=None
    )
    assert observation_frames._decode_privileged_global_observation_frames(
        nullable
    )._is_valid()


def test_duplicate_rows_and_per_frame_bounds_refuse() -> None:
    raw = _raw_bundle()
    row = dict(raw[0][1])["database_inventory"][0]
    duplicate = _replace_server_frame(raw, "database_inventory", (row, row))
    with pytest.raises(
        observation_frames.SanitizerPrivilegedGlobalObservationFramesFailure,
        match="privileged_global_observation_frames.input_invalid",
    ):
        observation_frames._decode_privileged_global_observation_frames(duplicate)

    grammar = query_grammar.load_privileged_global_query_grammar_contract()
    frame = next(
        item
        for item in grammar["grammar"]["frames"]
        if item["frame"] == "database_inventory"
    )
    too_many = _replace_server_frame(
        raw,
        "database_inventory",
        tuple(_row(frame, seed) for seed in range(frame["maximum_rows"] + 1)),
    )
    with pytest.raises(
        observation_frames.SanitizerPrivilegedGlobalObservationFramesFailure,
        match="privileged_global_observation_frames.input_invalid",
    ):
        observation_frames._decode_privileged_global_observation_frames(too_many)

    too_large = _replace_server_cell(
        raw,
        frame_name="password_null_state",
        column_index=1,
        value="x" * 20_000,
    )
    with pytest.raises(
        observation_frames.SanitizerPrivilegedGlobalObservationFramesFailure,
        match="privileged_global_observation_frames.bounds_invalid",
    ):
        observation_frames._decode_privileged_global_observation_frames(too_large)


def test_total_query_row_and_output_bounds_are_enforced() -> None:
    raw = _raw_bundle()
    grammar = query_grammar.load_privileged_global_query_grammar_contract()

    changed = copy.deepcopy(grammar)
    changed["resource_bounds"]["maximum_rows_total"] = 1
    with pytest.raises(
        observation_frames.SanitizerPrivilegedGlobalObservationFramesFailure,
        match="privileged_global_observation_frames.bounds_invalid",
    ):
        observation_frames._decode_bundle(raw, grammar=changed)

    changed = copy.deepcopy(grammar)
    changed["resource_bounds"]["maximum_queries"] = 14
    with pytest.raises(
        observation_frames.SanitizerPrivilegedGlobalObservationFramesFailure,
        match="privileged_global_observation_frames.bounds_invalid",
    ):
        observation_frames._decode_bundle(raw, grammar=changed)

    changed = copy.deepcopy(grammar)
    changed["resource_bounds"]["maximum_output_bytes"] = 1
    with pytest.raises(
        observation_frames.SanitizerPrivilegedGlobalObservationFramesFailure,
        match="privileged_global_observation_frames.bounds_invalid",
    ):
        observation_frames._decode_bundle(raw, grammar=changed)


def test_contract_drift_and_unexpected_failures_are_content_free(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contract = observation_frames.load_privileged_global_observation_frames_contract()
    changed = copy.deepcopy(contract)
    changed["claims"]["observer_implemented"] = True
    with pytest.raises(
        observation_frames.SanitizerPrivilegedGlobalObservationFramesFailure,
        match="privileged_global_observation_frames.contract_invalid",
    ):
        observation_frames._validate_contract(changed)

    monkeypatch.setattr(query_grammar, "CONTRACT_SHA256", "0" * 64)
    with pytest.raises(
        observation_frames.SanitizerPrivilegedGlobalObservationFramesFailure,
        match="privileged_global_observation_frames.contract_invalid",
    ):
        observation_frames.load_privileged_global_observation_frames_contract()

    monkeypatch.setattr(
        query_grammar,
        "CONTRACT_SHA256",
        observation_frames.QUERY_GRAMMAR_CONTRACT_SHA256,
    )
    monkeypatch.setattr(
        observation_frames,
        "_decode_bundle",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("secret://must-not-escape")
        ),
    )
    with pytest.raises(
        observation_frames.SanitizerPrivilegedGlobalObservationFramesFailure,
        match="privileged_global_observation_frames.input_invalid",
    ) as captured:
        observation_frames._decode_privileged_global_observation_frames(_raw_bundle())
    assert "secret" not in str(captured.value)


def test_module_contains_no_database_network_provider_or_process_surface() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
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


def test_public_construction_and_executable_validation_remain_terminal() -> None:
    with pytest.raises(
        observation_frames.SanitizerPrivilegedGlobalObservationFramesFailure,
        match=observation_frames.ACTIVATION_FAILURE,
    ):
        observation_frames.build_sanitizer_privileged_global_observation_frames()
    with pytest.raises(
        observation_frames.SanitizerPrivilegedGlobalObservationFramesFailure,
        match=observation_frames.ACTIVATION_FAILURE,
    ):
        observation_frames.load_privileged_global_observation_frames_contract(
            executable=True
        )
    with pytest.raises(
        observation_frames.SanitizerPrivilegedGlobalObservationFramesFailure,
        match="privileged_global_observation_frames.contract_invalid",
    ):
        observation_frames.load_privileged_global_observation_frames_contract(
            executable=cast(Any, 1)
        )
