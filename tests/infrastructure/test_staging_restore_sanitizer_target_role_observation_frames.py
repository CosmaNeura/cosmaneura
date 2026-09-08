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
    staging_restore_sanitizer_target_role_observation_frames as observation_frames,
)
from scripts import staging_restore_sanitizer_target_role_query_grammar as query_grammar

ROOT = Path(__file__).parents[2]
CONTRACT = (
    ROOT / "contracts/staging-rehearsal/v1/"
    "restore-sanitizer-target-role-observation-frames-contract.json"
)
SCRIPT = ROOT / "scripts/staging_restore_sanitizer_target_role_observation_frames.py"


def _sample_value(
    *, logical_type: str, nullable: bool, column_name: str, seed: int
) -> observation_frames.Scalar:
    if nullable and seed % 2:
        return None
    if logical_type == "boolean":
        return bool(seed % 2)
    if logical_type in {"int16", "int32", "int64"}:
        return seed
    if logical_type == "text":
        return f"{column_name}-caf\u00e9-{seed}"
    raise AssertionError(logical_type)


def _row(template: dict[str, Any], seed: int = 0) -> tuple[object, ...]:
    return tuple(
        _sample_value(
            logical_type=column["type"],
            nullable=column["nullable"],
            column_name=column["name"],
            seed=seed,
        )
        for column in template["exact_columns"]
    )


def _raw_frames(
    *, rows_each: int = 1
) -> tuple[tuple[str, tuple[tuple[object, ...], ...]], ...]:
    grammar = query_grammar.load_target_role_query_grammar_contract()
    return tuple(
        (
            template["frame"],
            tuple(_row(template, seed) for seed in range(rows_each)),
        )
        for template in grammar["grammar"]["templates"]
    )


def _replace_frame(
    frames: tuple[tuple[str, tuple[tuple[object, ...], ...]], ...],
    name: str,
    rows: tuple[tuple[object, ...], ...],
) -> tuple[tuple[str, tuple[tuple[object, ...], ...]], ...]:
    return tuple(
        (frame, rows if frame == name else current) for frame, current in frames
    )


def _replace_cell(
    frames: tuple[tuple[str, tuple[tuple[object, ...], ...]], ...],
    *,
    frame_name: str,
    row_index: int,
    column_index: int,
    value: object,
) -> tuple[tuple[str, tuple[tuple[object, ...], ...]], ...]:
    frame_rows = dict(frames)[frame_name]
    row = frame_rows[row_index]
    changed_row = (*row[:column_index], value, *row[column_index + 1 :])
    changed_rows = (*frame_rows[:row_index], changed_row, *frame_rows[row_index + 1 :])
    return _replace_frame(frames, frame_name, changed_rows)


def test_contract_is_hash_pinned_closed_world_and_dormant() -> None:
    assert hashlib.sha256(CONTRACT.read_bytes()).hexdigest() == (
        observation_frames.CONTRACT_SHA256
    )
    contract = observation_frames.load_target_role_observation_frames_contract()

    assert contract["closed_world"] is True
    assert contract["upstream_policies"] == {
        "target_role_query_grammar": {
            "policy_id": query_grammar.load_target_role_query_grammar_contract()[
                "policy_id"
            ],
            "sha256": f"sha256:{query_grammar.CONTRACT_SHA256}",
        }
    }
    assert contract["status"]["frame_decoder_implemented"] is True
    assert contract["status"]["observer_implemented"] is False
    assert contract["status"]["activation_ready"] is False
    assert contract["claims"] == observation_frames.CLAIMS
    assert contract["claims"]["frame_schema_validated"] is True
    assert all(
        contract["claims"][name] is False
        for name in observation_frames._NEGATIVE_CLAIMS
    )


def test_exact_frames_decode_to_sealed_non_authoritative_metadata() -> None:
    raw = _raw_frames()
    value = observation_frames._decode_target_role_observation_frames(raw)

    assert type(value) is observation_frames.DecodedSanitizerTargetRoleObservationFrames
    assert value._is_valid()
    assert value.kind == observation_frames.FRAME_SET_KIND
    assert value.frame_count == 13
    assert value.row_count == 13
    assert value.canonical_byte_count > 0
    assert value.frame_set_sha256.startswith("sha256:")
    assert value.frame_schema_validated is True
    assert value.row_order_preserved is True
    assert value.resource_bounds_enforced is True
    assert all(
        getattr(value, name) is False for name in observation_frames._NEGATIVE_CLAIMS
    )
    assert value._frames == raw
    assert "caf\u00e9" not in repr(value)


def test_output_is_immutable_nonserializable_and_tamper_evident() -> None:
    value = observation_frames._decode_target_role_observation_frames(_raw_frames())

    with pytest.raises(observation_frames.SanitizerTargetRoleObservationFramesFailure):
        value.row_count = 0
    with pytest.raises(observation_frames.SanitizerTargetRoleObservationFramesFailure):
        del value.row_count
    with pytest.raises(observation_frames.SanitizerTargetRoleObservationFramesFailure):
        copy.copy(value)
    with pytest.raises(observation_frames.SanitizerTargetRoleObservationFramesFailure):
        copy.deepcopy(value)
    with pytest.raises(observation_frames.SanitizerTargetRoleObservationFramesFailure):
        pickle.dumps(value)
    with pytest.raises(TypeError):
        json.dumps(value)
    with pytest.raises(TypeError):
        vars(value)

    original_row_count = value.row_count
    object.__setattr__(value, "row_count", 0)
    assert not value._is_valid()
    object.__setattr__(value, "row_count", original_row_count)
    assert value._is_valid()

    class EqualitySpoof:
        def __eq__(self, other: object) -> bool:
            return other is False

    object.__setattr__(value, "database_connection_authority", EqualitySpoof())
    assert not value._is_valid()


def test_frame_container_order_names_and_exact_tuple_shapes_are_closed() -> None:
    raw = _raw_frames()
    hostile_iterator_called = False

    class Hostile:
        def __iter__(self) -> Any:
            nonlocal hostile_iterator_called
            hostile_iterator_called = True
            raise RuntimeError("must not run")

    variants: tuple[object, ...] = (
        list(raw),
        raw[:-1],
        (raw[1], raw[0], *raw[2:]),
        (("unknown", raw[0][1]), *raw[1:]),
        ([raw[0][0], raw[0][1]], *raw[1:]),
        ((raw[0][0], list(raw[0][1])), *raw[1:]),
        ((raw[0][0], (list(raw[0][1][0]),)), *raw[1:]),
        Hostile(),
    )
    for changed in variants:
        with pytest.raises(
            observation_frames.SanitizerTargetRoleObservationFramesFailure,
            match="target_role_observation_frames.input_invalid",
        ):
            observation_frames._decode_target_role_observation_frames(changed)
    assert hostile_iterator_called is False


def test_scalar_types_ranges_nullability_and_text_encoding_are_exact() -> None:
    raw = _raw_frames()
    invalid = (
        _replace_cell(
            raw,
            frame_name="session_and_role",
            row_index=0,
            column_index=0,
            value=None,
        ),
        _replace_cell(
            raw,
            frame_name="session_and_role",
            row_index=0,
            column_index=3,
            value=True,
        ),
        _replace_cell(
            raw,
            frame_name="session_and_role",
            row_index=0,
            column_index=12,
            value=2**31,
        ),
        _replace_cell(
            raw,
            frame_name="session_and_role",
            row_index=0,
            column_index=0,
            value="bad\x00name",
        ),
        _replace_cell(
            raw,
            frame_name="session_and_role",
            row_index=0,
            column_index=0,
            value="bad\ud800text",
        ),
    )
    for changed in invalid:
        with pytest.raises(
            observation_frames.SanitizerTargetRoleObservationFramesFailure,
            match="target_role_observation_frames.input_invalid",
        ):
            observation_frames._decode_target_role_observation_frames(changed)

    nullable = _replace_cell(
        raw,
        frame_name="schemas",
        row_index=0,
        column_index=4,
        value=None,
    )
    assert observation_frames._decode_target_role_observation_frames(
        nullable
    )._is_valid()


def test_duplicate_per_frame_row_and_per_frame_bounds_refuse() -> None:
    raw = _raw_frames()
    duplicate = _replace_frame(
        raw,
        "memberships",
        (dict(raw)["memberships"][0], dict(raw)["memberships"][0]),
    )
    with pytest.raises(
        observation_frames.SanitizerTargetRoleObservationFramesFailure,
        match="target_role_observation_frames.input_invalid",
    ):
        observation_frames._decode_target_role_observation_frames(duplicate)

    grammar = query_grammar.load_target_role_query_grammar_contract()["grammar"]
    membership = next(
        item for item in grammar["templates"] if item["frame"] == "memberships"
    )
    too_many = _replace_frame(
        raw,
        "memberships",
        tuple(_row(membership, seed) for seed in range(65)),
    )
    with pytest.raises(
        observation_frames.SanitizerTargetRoleObservationFramesFailure,
        match="target_role_observation_frames.input_invalid",
    ):
        observation_frames._decode_target_role_observation_frames(too_many)

    too_large = _replace_cell(
        raw,
        frame_name="session_and_role",
        row_index=0,
        column_index=0,
        value="x" * 20_000,
    )
    with pytest.raises(
        observation_frames.SanitizerTargetRoleObservationFramesFailure,
        match="target_role_observation_frames.bounds_invalid",
    ):
        observation_frames._decode_target_role_observation_frames(too_large)


def test_total_row_and_output_bounds_are_enforced() -> None:
    grammar = query_grammar.load_target_role_query_grammar_contract()
    raw = tuple(
        (
            template["frame"],
            tuple(_row(template, seed) for seed in range(template["maximum_rows"])),
        )
        for template in grammar["grammar"]["templates"]
    )
    assert sum(len(rows) for _frame, rows in raw) == 4337
    with pytest.raises(
        observation_frames.SanitizerTargetRoleObservationFramesFailure,
        match="target_role_observation_frames.bounds_invalid",
    ):
        observation_frames._decode_target_role_observation_frames(raw)

    changed = copy.deepcopy(grammar)
    changed["resource_bounds"]["maximum_output_bytes"] = 1
    with pytest.raises(
        observation_frames.SanitizerTargetRoleObservationFramesFailure,
        match="target_role_observation_frames.bounds_invalid",
    ):
        observation_frames._decode_frames(_raw_frames(), grammar=changed)


def test_contract_drift_and_unexpected_failures_are_content_free(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contract = observation_frames.load_target_role_observation_frames_contract()
    changed = copy.deepcopy(contract)
    changed["claims"]["observer_implemented"] = True
    with pytest.raises(
        observation_frames.SanitizerTargetRoleObservationFramesFailure,
        match="target_role_observation_frames.contract_invalid",
    ):
        observation_frames._validate_contract(changed)

    monkeypatch.setattr(query_grammar, "CONTRACT_SHA256", "0" * 64)
    with pytest.raises(
        observation_frames.SanitizerTargetRoleObservationFramesFailure,
        match="target_role_observation_frames.contract_invalid",
    ):
        observation_frames.load_target_role_observation_frames_contract()

    monkeypatch.setattr(
        query_grammar,
        "CONTRACT_SHA256",
        observation_frames.QUERY_GRAMMAR_CONTRACT_SHA256,
    )
    monkeypatch.setattr(
        observation_frames,
        "_decode_frames",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("secret://must-not-escape")
        ),
    )
    with pytest.raises(
        observation_frames.SanitizerTargetRoleObservationFramesFailure,
        match="target_role_observation_frames.input_invalid",
    ) as captured:
        observation_frames._decode_target_role_observation_frames(_raw_frames())
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
        observation_frames.SanitizerTargetRoleObservationFramesFailure,
        match=observation_frames.ACTIVATION_FAILURE,
    ):
        observation_frames.build_sanitizer_target_role_observation_frames()
    with pytest.raises(
        observation_frames.SanitizerTargetRoleObservationFramesFailure,
        match=observation_frames.ACTIVATION_FAILURE,
    ):
        observation_frames.load_target_role_observation_frames_contract(executable=True)
    with pytest.raises(
        observation_frames.SanitizerTargetRoleObservationFramesFailure,
        match="target_role_observation_frames.contract_invalid",
    ):
        observation_frames.load_target_role_observation_frames_contract(
            executable=cast(Any, 1)
        )
