#!/usr/bin/env python3
"""Decode and seal dormant target-role catalog observation frames.

This module has no database, network, credential, provider, or process surface.
It validates already-materialized tuples against the exact query-grammar schema
and retains them only inside a non-serializable process-local object.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any, NoReturn

try:
    from scripts import (
        staging_restore_sanitizer_target_role_query_grammar as query_grammar,
    )
except ModuleNotFoundError:  # Direct `python scripts/...` compatibility.
    import staging_restore_sanitizer_target_role_query_grammar as query_grammar


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = (
    ROOT / "contracts/staging-rehearsal/v1/"
    "restore-sanitizer-target-role-observation-frames-contract.json"
)
CONTRACT_SHA256 = "7326d189125f1b01646a0de221eee59ddace31ff353573e8f7186bf77009162b"
QUERY_GRAMMAR_CONTRACT_SHA256 = (
    "8f1c4831c4766942778e5be24149a779ba0cd84081d03c11ed21463aabc3d1fe"
)
TEMPLATE_SET_SHA256 = "a740c41e36314c9954456d3c0dadb7d496904ec8d94fdbf14c106e2d125f9e33"
ACTIVATION_FAILURE = "activation.sanitizer_target_role_database_observer_unimplemented"
FRAME_SET_KIND = "sealed-sanitizer-target-role-decoded-frames-v1"
MAXIMUM_CONTRACT_BYTES = 64 * 1024
_FRAME_SEAL = object()
_FAILURE_CODES = frozenset(
    {
        ACTIVATION_FAILURE,
        "target_role_observation_frames.bounds_invalid",
        "target_role_observation_frames.contract_invalid",
        "target_role_observation_frames.input_invalid",
        "target_role_observation_frames.output_invalid",
    }
)
_POSITIVE_CLAIMS = {
    "frame_schema_validated": True,
    "row_order_preserved": True,
    "resource_bounds_enforced": True,
}
_NEGATIVE_CLAIMS = dict.fromkeys(
    (
        "database_connection_authority",
        "database_mutation_authority",
        "database_role_state_authenticated",
        "evidence_publication_authority",
        "live_database_calls_enabled",
        "observer_implemented",
        "provider_calls_permitted",
        "runtime_authority",
        "sanitizer_authority",
        "transaction_executed",
        "workflow_integrated",
    ),
    False,
)
CLAIMS = {**_POSITIVE_CLAIMS, **_NEGATIVE_CLAIMS}
PUBLIC_FIELDS = (
    "kind",
    "observation_frames_contract_sha256",
    "query_grammar_contract_sha256",
    "template_set_sha256",
    "frame_set_sha256",
    "frame_count",
    "row_count",
    "canonical_byte_count",
    *CLAIMS,
)
_INTEGER_RANGES = {
    "int16": (-(2**15), 2**15 - 1),
    "int32": (-(2**31), 2**31 - 1),
    "int64": (-(2**63), 2**63 - 1),
}

Scalar = bool | int | str | None
DecodedRow = tuple[Scalar, ...]
DecodedFrames = tuple[tuple[str, tuple[DecodedRow, ...]], ...]
FrameSchema = tuple[tuple[str, str, bool], ...]
FrameSpec = tuple[str, int, FrameSchema]


class SanitizerTargetRoleObservationFramesFailure(RuntimeError):
    """Bounded refusal that never includes a frame name or value."""


def _fail(code: str) -> NoReturn:
    if code not in _FAILURE_CODES:
        code = "target_role_observation_frames.input_invalid"
    raise SanitizerTargetRoleObservationFramesFailure(code) from None


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeEncodeError, RecursionError):
        _fail("target_role_observation_frames.input_invalid")


def _parse_contract(raw: bytes, *, expected_sha256: str) -> dict[str, Any]:
    if (
        type(raw) is not bytes
        or not 1 <= len(raw) <= MAXIMUM_CONTRACT_BYTES
        or not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), expected_sha256)
    ):
        _fail("target_role_observation_frames.contract_invalid")

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for name, value in items:
            if name in result:
                _fail("target_role_observation_frames.contract_invalid")
            result[name] = value
        return result

    try:
        document = json.loads(
            raw,
            object_pairs_hook=pairs,
            parse_constant=lambda _value: _fail(
                "target_role_observation_frames.contract_invalid"
            ),
        )
    except SanitizerTargetRoleObservationFramesFailure:
        raise
    except (UnicodeDecodeError, ValueError, RecursionError):
        _fail("target_role_observation_frames.contract_invalid")
    if type(document) is not dict:
        _fail("target_role_observation_frames.contract_invalid")
    return document


def _read_contract() -> tuple[dict[str, Any], bytes]:
    try:
        raw = CONTRACT_PATH.read_bytes()
    except OSError:
        _fail("target_role_observation_frames.contract_invalid")
    return _parse_contract(raw, expected_sha256=CONTRACT_SHA256), raw


def _expected_status() -> dict[str, Any]:
    return {
        "contract_validator_implemented": True,
        "frame_decoder_implemented": True,
        "connection_adapter_implemented": False,
        "observer_implemented": False,
        "offline_authenticator_implemented": False,
        "live_database_calls_enabled": False,
        "database_connection_authority": False,
        "database_mutation_authority": False,
        "runtime_authority": False,
        "sanitizer_authority": False,
        "workflow_integrated": False,
        "executable": False,
        "activation_ready": False,
        "public_activation_failure": ACTIVATION_FAILURE,
        "activation_blockers": [
            "exact_database_role_unselected",
            "provision_job_identity_exact_ids_unpinned",
            "target_role_database_observer_unimplemented",
            "offline_authenticator_unimplemented",
            "privileged_global_observer_unimplemented",
            "stability_authorities_unimplemented",
            "post_provision_schema_descriptor_unselected",
            "adapter_join_unimplemented",
        ],
    }


def _validate_contract(document: Mapping[str, Any]) -> dict[str, Any]:
    try:
        grammar = query_grammar.load_target_role_query_grammar_contract()
    except Exception:
        _fail("target_role_observation_frames.contract_invalid")
    if (
        query_grammar.CONTRACT_SHA256 != QUERY_GRAMMAR_CONTRACT_SHA256
        or query_grammar.TEMPLATE_SET_SHA256 != TEMPLATE_SET_SHA256
        or set(document)
        != {
            "schema_version",
            "policy_id",
            "closed_world",
            "upstream_policies",
            "input",
            "decoding",
            "resource_bounds",
            "output",
            "claims",
            "status",
        }
        or document.get("schema_version") != 1
        or document.get("policy_id")
        != "cosmaneura-staging-sanitizer-target-role-observation-frames-v1"
        or document.get("closed_world") is not True
        or document.get("upstream_policies")
        != {
            "target_role_query_grammar": {
                "policy_id": grammar["policy_id"],
                "sha256": f"sha256:{QUERY_GRAMMAR_CONTRACT_SHA256}",
            }
        }
        or document.get("input")
        != {
            "source": "future_target_role_database_observer_exact_query_results",
            "container_shape": "tuple-of-frame-name-and-tuple-of-row-tuples",
            "exact_builtin_scalar_types_only": True,
            "caller_maps_iterators_generators_callbacks_connections_cursors_sql_"
            "schemas_or_bounds_permitted": False,
        }
        or document.get("decoding")
        != {
            "frame_order_source": "target_role_query_grammar.grammar.exact_order",
            "column_schema_source": (
                "target_role_query_grammar.grammar.templates.exact_columns"
            ),
            "row_order_preserved_exactly": True,
            "duplicate_rows_per_frame_permitted": False,
            "unknown_missing_or_duplicate_frames_permitted": False,
            "unknown_missing_or_duplicate_columns_permitted": False,
            "boolean_is_integer_permitted": False,
            "integer_ranges_enforced": True,
            "utf8_text_required": True,
            "nul_in_text_permitted": False,
            "canonical_json_encoding": "utf-8-sort-keys-compact-no-nan",
        }
        or document.get("resource_bounds")
        != {
            "source": "target_role_query_grammar.resource_bounds",
            **grammar["resource_bounds"],
        }
        or document.get("output")
        != {
            "kind": FRAME_SET_KIND,
            "public_metadata_fields": list(PUBLIC_FIELDS),
            "raw_frames_retained_process_local_only": True,
            "raw_frames_publicly_serializable": False,
            "raw_frames_in_repr_or_exception_permitted": False,
            "copy_pickle_or_mutation_permitted": False,
            "offline_authenticator_must_consume_identical_sealed_object": True,
        }
        or document.get("claims") != CLAIMS
        or document.get("status") != _expected_status()
    ):
        _fail("target_role_observation_frames.contract_invalid")
    return grammar


def load_target_role_observation_frames_contract(
    *, executable: bool = False
) -> dict[str, Any]:
    if type(executable) is not bool:
        _fail("target_role_observation_frames.contract_invalid")
    try:
        document, _raw = _read_contract()
        _validate_contract(document)
        if executable:
            _fail(ACTIVATION_FAILURE)
        return document
    except SanitizerTargetRoleObservationFramesFailure:
        raise
    except Exception:
        _fail("target_role_observation_frames.contract_invalid")


def _frame_specs(grammar: Mapping[str, Any]) -> tuple[FrameSpec, ...]:
    try:
        templates = grammar["grammar"]["templates"]
        specs = tuple(
            (
                item["frame"],
                item["maximum_rows"],
                tuple(
                    (column["name"], column["type"], column["nullable"])
                    for column in item["exact_columns"]
                ),
            )
            for item in templates
        )
    except (KeyError, TypeError):
        _fail("target_role_observation_frames.contract_invalid")
    if (
        tuple(name for name, _maximum_rows, _columns in specs)
        != query_grammar.FRAME_NAMES
        or len(specs) != 13
    ):
        _fail("target_role_observation_frames.contract_invalid")
    return specs


def _decode_scalar(value: object, *, logical_type: str, nullable: bool) -> Scalar:
    if value is None:
        if nullable:
            return None
        _fail("target_role_observation_frames.input_invalid")
    if logical_type == "boolean":
        if type(value) is not bool:
            _fail("target_role_observation_frames.input_invalid")
        return value
    if logical_type in _INTEGER_RANGES:
        if type(value) is not int:
            _fail("target_role_observation_frames.input_invalid")
        minimum, maximum = _INTEGER_RANGES[logical_type]
        if not minimum <= value <= maximum:
            _fail("target_role_observation_frames.input_invalid")
        return value
    if logical_type == "text":
        if type(value) is not str or "\x00" in value:
            _fail("target_role_observation_frames.input_invalid")
        try:
            value.encode("utf-8", errors="strict")
        except UnicodeEncodeError:
            _fail("target_role_observation_frames.input_invalid")
        return value
    _fail("target_role_observation_frames.contract_invalid")


def _decode_frames(
    raw_frames: object,
    *,
    grammar: Mapping[str, Any],
) -> tuple[DecodedFrames, bytes]:
    if type(raw_frames) is not tuple:
        _fail("target_role_observation_frames.input_invalid")
    specs = _frame_specs(grammar)
    if len(raw_frames) != len(specs):
        _fail("target_role_observation_frames.input_invalid")

    decoded_frames: list[tuple[str, tuple[DecodedRow, ...]]] = []
    total_rows = 0
    maximum_row_bytes = grammar["resource_bounds"]["maximum_row_bytes"]
    for raw_frame, (expected_name, maximum_rows, columns) in zip(
        raw_frames, specs, strict=True
    ):
        if (
            type(raw_frame) is not tuple
            or len(raw_frame) != 2
            or type(raw_frame[0]) is not str
            or raw_frame[0] != expected_name
            or type(raw_frame[1]) is not tuple
            or len(raw_frame[1]) > maximum_rows
        ):
            _fail("target_role_observation_frames.input_invalid")
        decoded_rows: list[DecodedRow] = []
        row_digests: set[bytes] = set()
        for raw_row in raw_frame[1]:
            if type(raw_row) is not tuple or len(raw_row) != len(columns):
                _fail("target_role_observation_frames.input_invalid")
            row = tuple(
                _decode_scalar(value, logical_type=logical_type, nullable=nullable)
                for value, (_name, logical_type, nullable) in zip(
                    raw_row, columns, strict=True
                )
            )
            encoded_row = _canonical(row)
            if len(encoded_row) > maximum_row_bytes:
                _fail("target_role_observation_frames.bounds_invalid")
            row_digest = hashlib.sha256(encoded_row).digest()
            if row_digest in row_digests:
                _fail("target_role_observation_frames.input_invalid")
            row_digests.add(row_digest)
            decoded_rows.append(row)
        total_rows += len(decoded_rows)
        decoded_frames.append((expected_name, tuple(decoded_rows)))

    if total_rows > grammar["resource_bounds"]["maximum_rows_total"]:
        _fail("target_role_observation_frames.bounds_invalid")
    frames = tuple(decoded_frames)
    canonical = _canonical(
        tuple({"frame": frame, "rows": rows} for frame, rows in frames)
    )
    if len(canonical) > grammar["resource_bounds"]["maximum_output_bytes"]:
        _fail("target_role_observation_frames.bounds_invalid")
    return frames, canonical


def _public_projection(*, canonical: bytes, row_count: int) -> dict[str, Any]:
    return {
        "kind": FRAME_SET_KIND,
        "observation_frames_contract_sha256": f"sha256:{CONTRACT_SHA256}",
        "query_grammar_contract_sha256": f"sha256:{QUERY_GRAMMAR_CONTRACT_SHA256}",
        "template_set_sha256": f"sha256:{TEMPLATE_SET_SHA256}",
        "frame_set_sha256": f"sha256:{hashlib.sha256(canonical).hexdigest()}",
        "frame_count": len(query_grammar.FRAME_NAMES),
        "row_count": row_count,
        "canonical_byte_count": len(canonical),
        **CLAIMS,
    }


def _private_proof(
    *,
    public: Mapping[str, Any],
    value_identity: int,
    frames: DecodedFrames,
) -> str:
    return hashlib.sha256(
        _canonical(
            {
                "public": dict(public),
                "value_identity": value_identity,
                "seal_identity": id(_FRAME_SEAL),
                "frames": frames,
            }
        )
    ).hexdigest()


class DecodedSanitizerTargetRoleObservationFrames:
    """Opaque, immutable, non-authoritative decoded catalog frames."""

    __slots__ = (*PUBLIC_FIELDS, "_frames", "_contract_bytes", "_proof_sha256", "_seal")

    kind: str
    observation_frames_contract_sha256: str
    query_grammar_contract_sha256: str
    template_set_sha256: str
    frame_set_sha256: str
    frame_count: int
    row_count: int
    canonical_byte_count: int
    frame_schema_validated: bool
    row_order_preserved: bool
    resource_bounds_enforced: bool
    database_connection_authority: bool
    database_mutation_authority: bool
    database_role_state_authenticated: bool
    evidence_publication_authority: bool
    live_database_calls_enabled: bool
    observer_implemented: bool
    provider_calls_permitted: bool
    runtime_authority: bool
    sanitizer_authority: bool
    transaction_executed: bool
    workflow_integrated: bool
    _frames: DecodedFrames
    _contract_bytes: bytes
    _proof_sha256: str
    _seal: object

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        _fail("target_role_observation_frames.output_invalid")

    def __setattr__(self, name: str, value: object) -> NoReturn:
        del name, value
        _fail("target_role_observation_frames.output_invalid")

    def __delattr__(self, name: str) -> NoReturn:
        del name
        _fail("target_role_observation_frames.output_invalid")

    def __repr__(self) -> str:
        return "<DecodedSanitizerTargetRoleObservationFrames sealed non-authoritative>"

    def __copy__(self) -> NoReturn:
        _fail("target_role_observation_frames.output_invalid")

    def __deepcopy__(self, memo: object) -> NoReturn:
        del memo
        _fail("target_role_observation_frames.output_invalid")

    def __reduce__(self) -> NoReturn:
        _fail("target_role_observation_frames.output_invalid")

    def __reduce_ex__(self, protocol: object) -> NoReturn:
        del protocol
        _fail("target_role_observation_frames.output_invalid")

    def _is_valid(self) -> bool:
        try:
            document = _parse_contract(
                self._contract_bytes,
                expected_sha256=CONTRACT_SHA256,
            )
            grammar = _validate_contract(document)
            frames, canonical = _decode_frames(self._frames, grammar=grammar)
            public = _public_projection(
                canonical=canonical,
                row_count=sum(len(rows) for _frame, rows in frames),
            )
            return (
                type(self) is DecodedSanitizerTargetRoleObservationFrames
                and self._seal is _FRAME_SEAL
                and frames == self._frames
                and tuple(public) == PUBLIC_FIELDS
                and all(getattr(self, name) == value for name, value in public.items())
                and hmac.compare_digest(
                    self._proof_sha256,
                    _private_proof(
                        public=public,
                        value_identity=id(self),
                        frames=frames,
                    ),
                )
            )
        except Exception:
            return False


def _decode_target_role_observation_frames(
    raw_frames: object,
) -> DecodedSanitizerTargetRoleObservationFrames:
    """Internal structural decoder for the future one-shot database observer."""

    code = "target_role_observation_frames.input_invalid"
    try:
        document, contract_bytes = _read_contract()
        grammar = _validate_contract(document)
        frames, canonical = _decode_frames(raw_frames, grammar=grammar)
        public = _public_projection(
            canonical=canonical,
            row_count=sum(len(rows) for _frame, rows in frames),
        )
        if tuple(public) != PUBLIC_FIELDS:
            _fail("target_role_observation_frames.output_invalid")
        value = object.__new__(DecodedSanitizerTargetRoleObservationFrames)
        for name, item in public.items():
            object.__setattr__(value, name, item)
        object.__setattr__(value, "_frames", frames)
        object.__setattr__(value, "_contract_bytes", bytes(contract_bytes))
        object.__setattr__(
            value,
            "_proof_sha256",
            _private_proof(
                public=public,
                value_identity=id(value),
                frames=frames,
            ),
        )
        object.__setattr__(value, "_seal", _FRAME_SEAL)
        if not value._is_valid():
            _fail("target_role_observation_frames.output_invalid")
        return value
    except Exception as error:
        if (
            type(error) is SanitizerTargetRoleObservationFramesFailure
            and len(error.args) == 1
            and type(error.args[0]) is str
            and error.args[0] in _FAILURE_CODES
        ):
            code = error.args[0]
    raise SanitizerTargetRoleObservationFramesFailure(code) from None


def build_sanitizer_target_role_observation_frames() -> NoReturn:
    """Refuse public construction until the reviewed database observer exists."""

    load_target_role_observation_frames_contract()
    _fail(ACTIVATION_FAILURE)


if __name__ == "__main__":
    load_target_role_observation_frames_contract(executable="--executable" in sys.argv)
    print("sanitizer target-role observation frames contract valid (dormant)")
