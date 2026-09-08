#!/usr/bin/env python3
"""Decode and seal dormant privileged/global catalog observation frames.

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
        staging_restore_sanitizer_privileged_global_query_grammar as query_grammar,
    )
except ModuleNotFoundError:  # Direct `python scripts/...` compatibility.
    import staging_restore_sanitizer_privileged_global_query_grammar as query_grammar


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = (
    ROOT / "contracts/staging-rehearsal/v1/"
    "restore-sanitizer-privileged-global-observation-frames-contract.json"
)
CONTRACT_SHA256 = "6cbff47fbd162b52c0bc6e22d71d28adea3d91666ef09c84d8125172b0e1b1bf"
QUERY_GRAMMAR_CONTRACT_SHA256 = (
    "80930c0516f0c3090d0c80e37c5d33c544443900b71aaa88490700c0daf8ce7e"
)
TEMPLATE_SET_SHA256 = "6dbc52487fccdefe28bfa18e08feccc99fe8f433012ec08b5cdc48cca96ea88c"
ACTIVATION_FAILURE = (
    "activation.sanitizer_privileged_global_database_observer_unimplemented"
)
FRAME_SET_KIND = "sealed-sanitizer-privileged-global-decoded-frames-v1"
MAXIMUM_CONTRACT_BYTES = 96 * 1024
_FRAME_SEAL = object()
_FAILURE_CODES = frozenset(
    {
        ACTIVATION_FAILURE,
        "privileged_global_observation_frames.bounds_invalid",
        "privileged_global_observation_frames.contract_invalid",
        "privileged_global_observation_frames.input_invalid",
        "privileged_global_observation_frames.output_invalid",
    }
)
_POSITIVE_CLAIMS = {
    "database_partition_shape_validated": True,
    "frame_schema_validated": True,
    "row_order_preserved": True,
    "resource_bounds_enforced": True,
}
_NEGATIVE_CLAIMS = dict.fromkeys(
    (
        "bootstrap_authority_authenticated",
        "database_connection_authority",
        "database_mutation_authority",
        "database_role_state_authenticated",
        "evidence_publication_authority",
        "live_database_calls_enabled",
        "observer_implemented",
        "privileged_global_observation_authenticated",
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
    "server_once_frame_count",
    "database_count",
    "per_database_frame_count",
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
DecodedFrame = tuple[str, tuple[DecodedRow, ...]]
DecodedFrames = tuple[DecodedFrame, ...]
DecodedDatabaseFrames = tuple[int, str, DecodedFrames]
DecodedDatabaseFrameSets = tuple[DecodedDatabaseFrames, ...]
DecodedBundle = tuple[DecodedFrames, DecodedDatabaseFrameSets]
FrameSchema = tuple[tuple[str, str, bool], ...]
FrameSpec = tuple[str, int, FrameSchema]


class SanitizerPrivilegedGlobalObservationFramesFailure(RuntimeError):
    """Bounded refusal that never includes a database, frame, or row value."""


def _fail(code: str) -> NoReturn:
    if code not in _FAILURE_CODES:
        code = "privileged_global_observation_frames.input_invalid"
    raise SanitizerPrivilegedGlobalObservationFramesFailure(code) from None


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
        _fail("privileged_global_observation_frames.input_invalid")


def _canonical_bounded(value: Any, *, maximum_bytes: int) -> bytes:
    if type(maximum_bytes) is not int or maximum_bytes < 1:
        _fail("privileged_global_observation_frames.bounds_invalid")
    encoded = bytearray()
    try:
        chunks = json.JSONEncoder(
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).iterencode(value)
        for chunk in chunks:
            chunk_bytes = chunk.encode("ascii")
            if len(encoded) + len(chunk_bytes) > maximum_bytes:
                _fail("privileged_global_observation_frames.bounds_invalid")
            encoded.extend(chunk_bytes)
    except SanitizerPrivilegedGlobalObservationFramesFailure:
        raise
    except (TypeError, ValueError, UnicodeEncodeError, RecursionError):
        _fail("privileged_global_observation_frames.input_invalid")
    return bytes(encoded)


def _parse_contract(raw: bytes, *, expected_sha256: str) -> dict[str, Any]:
    if (
        type(raw) is not bytes
        or not 1 <= len(raw) <= MAXIMUM_CONTRACT_BYTES
        or not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), expected_sha256)
    ):
        _fail("privileged_global_observation_frames.contract_invalid")

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for name, value in items:
            if name in result:
                _fail("privileged_global_observation_frames.contract_invalid")
            result[name] = value
        return result

    try:
        document = json.loads(
            raw,
            object_pairs_hook=pairs,
            parse_constant=lambda _value: _fail(
                "privileged_global_observation_frames.contract_invalid"
            ),
        )
    except SanitizerPrivilegedGlobalObservationFramesFailure:
        raise
    except (UnicodeDecodeError, ValueError, RecursionError):
        _fail("privileged_global_observation_frames.contract_invalid")
    if type(document) is not dict:
        _fail("privileged_global_observation_frames.contract_invalid")
    return document


def _read_contract() -> tuple[dict[str, Any], bytes]:
    try:
        raw = CONTRACT_PATH.read_bytes()
    except OSError:
        _fail("privileged_global_observation_frames.contract_invalid")
    return _parse_contract(raw, expected_sha256=CONTRACT_SHA256), raw


def _expected_status() -> dict[str, Any]:
    return {
        "contract_validator_implemented": True,
        "frame_decoder_implemented": True,
        "bootstrap_authority_selected": False,
        "maintenance_database_selected": False,
        "disabled_database_surface_observation_strategy_selected": False,
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
            "bootstrap_authority_unselected",
            "maintenance_database_unselected",
            "disabled_database_surface_observation_strategy_unselected",
            "privileged_global_database_observer_unimplemented",
            "privileged_global_offline_authenticator_unimplemented",
            "target_role_and_privileged_global_join_unimplemented",
            "stability_authorities_unimplemented",
            "post_provision_schema_descriptor_unselected",
            "final_adapter_join_unimplemented",
        ],
    }


def _validate_contract(document: Mapping[str, Any]) -> dict[str, Any]:
    try:
        grammar = query_grammar.load_privileged_global_query_grammar_contract()
    except Exception:
        _fail("privileged_global_observation_frames.contract_invalid")
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
        != "cosmaneura-staging-sanitizer-privileged-global-observation-frames-v1"
        or document.get("closed_world") is not True
        or document.get("upstream_policies")
        != {
            "privileged_global_query_grammar": {
                "policy_id": grammar["policy_id"],
                "sha256": f"sha256:{QUERY_GRAMMAR_CONTRACT_SHA256}",
            }
        }
        or document.get("input")
        != {
            "source": "future_privileged_global_database_observer_exact_query_results",
            "container_shape": "server-once-frames-and-ordered-database-frame-tuples",
            "database_key_shape": "int64-oid-and-utf8-name",
            "exact_builtin_scalar_types_only": True,
            "caller_maps_iterators_generators_callbacks_connections_cursors_sql_schemas_or_bounds_permitted": False,
        }
        or document.get("decoding")
        != {
            "section_order": ["server_once", "per_database"],
            "server_once_frame_order_source": (
                "privileged_global_query_grammar.grammar.server_once_exact_order"
            ),
            "per_database_frame_order_source": (
                "privileged_global_query_grammar.grammar.per_database_exact_order"
            ),
            "column_schema_source": (
                "privileged_global_query_grammar.grammar.frames.exact_columns"
            ),
            "database_key_maximum_bytes_source": "resource_bounds.maximum_row_bytes",
            "database_order_preserved_exactly": True,
            "row_order_preserved_exactly": True,
            "duplicate_database_oid_or_name_permitted": False,
            "duplicate_rows_per_frame_permitted": False,
            "unknown_missing_or_duplicate_sections_or_frames_permitted": False,
            "unknown_missing_or_duplicate_columns_permitted": False,
            "boolean_is_integer_permitted": False,
            "integer_ranges_enforced": True,
            "utf8_text_required": True,
            "nul_in_text_permitted": False,
            "canonical_json_encoding": "utf-8-sort-keys-compact-no-nan",
        }
        or document.get("resource_bounds")
        != {
            "source": "privileged_global_query_grammar.resource_bounds",
            **grammar["resource_bounds"],
        }
        or document.get("output")
        != {
            "kind": FRAME_SET_KIND,
            "public_metadata_fields": list(PUBLIC_FIELDS),
            "raw_frames_and_database_keys_retained_process_local_only": True,
            "raw_frames_or_database_keys_publicly_serializable": False,
            "raw_frames_or_database_keys_in_repr_or_exception_permitted": False,
            "copy_pickle_or_mutation_permitted": False,
            "offline_authenticator_must_consume_identical_sealed_object": True,
        }
        or document.get("claims") != CLAIMS
        or document.get("status") != _expected_status()
    ):
        _fail("privileged_global_observation_frames.contract_invalid")
    return grammar


def load_privileged_global_observation_frames_contract(
    *, executable: bool = False
) -> dict[str, Any]:
    if type(executable) is not bool:
        _fail("privileged_global_observation_frames.contract_invalid")
    try:
        document, _raw = _read_contract()
        _validate_contract(document)
        if executable:
            _fail(ACTIVATION_FAILURE)
        return document
    except SanitizerPrivilegedGlobalObservationFramesFailure:
        raise
    except Exception:
        _fail("privileged_global_observation_frames.contract_invalid")


def _frame_specs(
    grammar: Mapping[str, Any], names: tuple[str, ...]
) -> tuple[FrameSpec, ...]:
    try:
        by_name = {item["frame"]: item for item in grammar["grammar"]["frames"]}
        specs = tuple(
            (
                name,
                by_name[name]["maximum_rows"],
                tuple(
                    (column["name"], column["type"], column["nullable"])
                    for column in by_name[name]["exact_columns"]
                ),
            )
            for name in names
        )
    except (KeyError, TypeError):
        _fail("privileged_global_observation_frames.contract_invalid")
    if tuple(by_name) != query_grammar.FRAME_NAMES or len(by_name) != 13:
        _fail("privileged_global_observation_frames.contract_invalid")
    return specs


def _decode_scalar(
    value: object,
    *,
    logical_type: str,
    nullable: bool,
    maximum_text_bytes: int,
) -> Scalar:
    if value is None:
        if nullable:
            return None
        _fail("privileged_global_observation_frames.input_invalid")
    if logical_type == "boolean":
        if type(value) is not bool:
            _fail("privileged_global_observation_frames.input_invalid")
        return value
    if logical_type in _INTEGER_RANGES:
        if type(value) is not int:
            _fail("privileged_global_observation_frames.input_invalid")
        minimum, maximum = _INTEGER_RANGES[logical_type]
        if not minimum <= value <= maximum:
            _fail("privileged_global_observation_frames.input_invalid")
        return value
    if logical_type == "text":
        if type(value) is not str:
            _fail("privileged_global_observation_frames.input_invalid")
        if (
            type(maximum_text_bytes) is not int
            or maximum_text_bytes < 1
            or len(value) > maximum_text_bytes
        ):
            _fail("privileged_global_observation_frames.bounds_invalid")
        if "\x00" in value:
            _fail("privileged_global_observation_frames.input_invalid")
        try:
            encoded = value.encode("utf-8", errors="strict")
        except UnicodeEncodeError:
            _fail("privileged_global_observation_frames.input_invalid")
        if len(encoded) > maximum_text_bytes:
            _fail("privileged_global_observation_frames.bounds_invalid")
        return value
    _fail("privileged_global_observation_frames.contract_invalid")


def _decode_frame_group(
    raw_frames: object,
    *,
    specs: tuple[FrameSpec, ...],
    maximum_row_bytes: int,
    maximum_rows_total: int,
    rows_already_decoded: int,
    maximum_output_bytes: int,
    encoded_bytes_already: int,
    maximum_roles: int,
    role_oids: set[int],
) -> tuple[DecodedFrames, int, int]:
    if type(raw_frames) is not tuple or len(raw_frames) != len(specs):
        _fail("privileged_global_observation_frames.input_invalid")
    decoded_frames: list[DecodedFrame] = []
    total_rows = 0
    encoded_bytes = 0
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
            _fail("privileged_global_observation_frames.input_invalid")
        if rows_already_decoded + total_rows + len(raw_frame[1]) > maximum_rows_total:
            _fail("privileged_global_observation_frames.bounds_invalid")
        rows: list[DecodedRow] = []
        row_digests: set[bytes] = set()
        for raw_row in raw_frame[1]:
            if type(raw_row) is not tuple or len(raw_row) != len(columns):
                _fail("privileged_global_observation_frames.input_invalid")
            row = tuple(
                _decode_scalar(
                    value,
                    logical_type=logical_type,
                    nullable=nullable,
                    maximum_text_bytes=maximum_row_bytes,
                )
                for value, (_name, logical_type, nullable) in zip(
                    raw_row, columns, strict=True
                )
            )
            encoded_row = _canonical_bounded(row, maximum_bytes=maximum_row_bytes)
            if (
                encoded_bytes_already + encoded_bytes + len(encoded_row)
                > maximum_output_bytes
            ):
                _fail("privileged_global_observation_frames.bounds_invalid")
            row_role_oids = {
                value
                for value, (name, _logical_type, _nullable) in zip(
                    row, columns, strict=True
                )
                if name.endswith("role_oid") and type(value) is int
            }
            if len(role_oids | row_role_oids) > maximum_roles:
                _fail("privileged_global_observation_frames.bounds_invalid")
            digest = hashlib.sha256(encoded_row).digest()
            if digest in row_digests:
                _fail("privileged_global_observation_frames.input_invalid")
            row_digests.add(digest)
            role_oids.update(row_role_oids)
            encoded_bytes += len(encoded_row)
            rows.append(row)
        total_rows += len(rows)
        decoded_frames.append((expected_name, tuple(rows)))
    return tuple(decoded_frames), total_rows, encoded_bytes


def _decode_bundle(
    raw_bundle: object,
    *,
    grammar: Mapping[str, Any],
) -> tuple[DecodedBundle, bytes, int]:
    if (
        type(raw_bundle) is not tuple
        or len(raw_bundle) != 2
        or any(
            type(section) is not tuple or len(section) != 2 for section in raw_bundle
        )
        or type(raw_bundle[0][0]) is not str
        or type(raw_bundle[1][0]) is not str
        or raw_bundle[0][0] != "server_once"
        or raw_bundle[1][0] != "per_database"
        or type(raw_bundle[0][1]) is not tuple
        or type(raw_bundle[1][1]) is not tuple
    ):
        _fail("privileged_global_observation_frames.input_invalid")
    bounds = grammar["resource_bounds"]
    server_specs = _frame_specs(grammar, query_grammar.SERVER_ONCE_FRAME_NAMES)
    database_specs = _frame_specs(grammar, query_grammar.PER_DATABASE_FRAME_NAMES)
    role_oids: set[int] = set()
    server_frames, total_rows, encoded_bytes = _decode_frame_group(
        raw_bundle[0][1],
        specs=server_specs,
        maximum_row_bytes=bounds["maximum_row_bytes"],
        maximum_rows_total=bounds["maximum_rows_total"],
        rows_already_decoded=0,
        maximum_output_bytes=bounds["maximum_output_bytes"],
        encoded_bytes_already=0,
        maximum_roles=bounds["maximum_roles"],
        role_oids=role_oids,
    )
    if len(raw_bundle[1][1]) > bounds["maximum_database_entries"]:
        _fail("privileged_global_observation_frames.bounds_invalid")
    database_frames: list[DecodedDatabaseFrames] = []
    database_oids: set[int] = set()
    database_names: set[str] = set()
    for raw_database in raw_bundle[1][1]:
        if type(raw_database) is not tuple or len(raw_database) != 3:
            _fail("privileged_global_observation_frames.input_invalid")
        database_oid = _decode_scalar(
            raw_database[0],
            logical_type="int64",
            nullable=False,
            maximum_text_bytes=bounds["maximum_row_bytes"],
        )
        database_name = _decode_scalar(
            raw_database[1],
            logical_type="text",
            nullable=False,
            maximum_text_bytes=bounds["maximum_row_bytes"],
        )
        if (
            type(database_oid) is not int
            or database_oid <= 0
            or type(database_name) is not str
            or not database_name
            or database_oid in database_oids
            or database_name in database_names
        ):
            _fail("privileged_global_observation_frames.input_invalid")
        encoded_database_key = _canonical_bounded(
            (database_oid, database_name),
            maximum_bytes=bounds["maximum_row_bytes"],
        )
        if encoded_bytes + len(encoded_database_key) > bounds["maximum_output_bytes"]:
            _fail("privileged_global_observation_frames.bounds_invalid")
        encoded_bytes += len(encoded_database_key)
        database_oids.add(database_oid)
        database_names.add(database_name)
        frames, row_count, frame_bytes = _decode_frame_group(
            raw_database[2],
            specs=database_specs,
            maximum_row_bytes=bounds["maximum_row_bytes"],
            maximum_rows_total=bounds["maximum_rows_total"],
            rows_already_decoded=total_rows,
            maximum_output_bytes=bounds["maximum_output_bytes"],
            encoded_bytes_already=encoded_bytes,
            maximum_roles=bounds["maximum_roles"],
            role_oids=role_oids,
        )
        total_rows += row_count
        encoded_bytes += frame_bytes
        database_frames.append((database_oid, database_name, frames))
    query_count = len(server_frames) + len(database_frames) * len(database_specs)
    if query_count > bounds["maximum_queries"]:
        _fail("privileged_global_observation_frames.bounds_invalid")
    bundle: DecodedBundle = (server_frames, tuple(database_frames))
    canonical = _canonical_bounded(
        {
            "server_once": tuple(
                {"frame": frame, "rows": rows} for frame, rows in server_frames
            ),
            "per_database": tuple(
                {
                    "database_oid": database_oid,
                    "database_name": database_name,
                    "frames": tuple(
                        {"frame": frame, "rows": rows} for frame, rows in frames
                    ),
                }
                for database_oid, database_name, frames in database_frames
            ),
        },
        maximum_bytes=bounds["maximum_output_bytes"],
    )
    return bundle, canonical, total_rows


def _public_projection(
    *, canonical: bytes, database_count: int, row_count: int
) -> dict[str, Any]:
    per_database_frame_count = database_count * len(
        query_grammar.PER_DATABASE_FRAME_NAMES
    )
    return {
        "kind": FRAME_SET_KIND,
        "observation_frames_contract_sha256": f"sha256:{CONTRACT_SHA256}",
        "query_grammar_contract_sha256": f"sha256:{QUERY_GRAMMAR_CONTRACT_SHA256}",
        "template_set_sha256": f"sha256:{TEMPLATE_SET_SHA256}",
        "frame_set_sha256": f"sha256:{hashlib.sha256(canonical).hexdigest()}",
        "server_once_frame_count": len(query_grammar.SERVER_ONCE_FRAME_NAMES),
        "database_count": database_count,
        "per_database_frame_count": per_database_frame_count,
        "frame_count": len(query_grammar.SERVER_ONCE_FRAME_NAMES)
        + per_database_frame_count,
        "row_count": row_count,
        "canonical_byte_count": len(canonical),
        **CLAIMS,
    }


def _private_proof(
    *, public: Mapping[str, Any], value_identity: int, bundle: DecodedBundle
) -> str:
    return hashlib.sha256(
        _canonical(
            {
                "public": dict(public),
                "value_identity": value_identity,
                "seal_identity": id(_FRAME_SEAL),
                "bundle": bundle,
            }
        )
    ).hexdigest()


class DecodedSanitizerPrivilegedGlobalObservationFrames:
    """Opaque, immutable, non-authoritative decoded catalog frames."""

    __slots__ = (*PUBLIC_FIELDS, "_bundle", "_contract_bytes", "_proof_sha256", "_seal")

    kind: str
    observation_frames_contract_sha256: str
    query_grammar_contract_sha256: str
    template_set_sha256: str
    frame_set_sha256: str
    server_once_frame_count: int
    database_count: int
    per_database_frame_count: int
    frame_count: int
    row_count: int
    canonical_byte_count: int
    database_partition_shape_validated: bool
    frame_schema_validated: bool
    row_order_preserved: bool
    resource_bounds_enforced: bool
    bootstrap_authority_authenticated: bool
    database_connection_authority: bool
    database_mutation_authority: bool
    database_role_state_authenticated: bool
    evidence_publication_authority: bool
    live_database_calls_enabled: bool
    observer_implemented: bool
    privileged_global_observation_authenticated: bool
    provider_calls_permitted: bool
    runtime_authority: bool
    sanitizer_authority: bool
    transaction_executed: bool
    workflow_integrated: bool
    _bundle: DecodedBundle
    _contract_bytes: bytes
    _proof_sha256: str
    _seal: object

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        _fail("privileged_global_observation_frames.output_invalid")

    def __setattr__(self, name: str, value: object) -> NoReturn:
        del name, value
        _fail("privileged_global_observation_frames.output_invalid")

    def __delattr__(self, name: str) -> NoReturn:
        del name
        _fail("privileged_global_observation_frames.output_invalid")

    def __repr__(self) -> str:
        return "<DecodedSanitizerPrivilegedGlobalObservationFrames sealed non-authoritative>"

    def __copy__(self) -> NoReturn:
        _fail("privileged_global_observation_frames.output_invalid")

    def __deepcopy__(self, memo: object) -> NoReturn:
        del memo
        _fail("privileged_global_observation_frames.output_invalid")

    def __reduce__(self) -> NoReturn:
        _fail("privileged_global_observation_frames.output_invalid")

    def __reduce_ex__(self, protocol: object) -> NoReturn:
        del protocol
        _fail("privileged_global_observation_frames.output_invalid")

    def _is_valid(self) -> bool:
        try:
            document = _parse_contract(
                self._contract_bytes, expected_sha256=CONTRACT_SHA256
            )
            grammar = _validate_contract(document)
            raw_bundle = (
                ("server_once", self._bundle[0]),
                ("per_database", self._bundle[1]),
            )
            bundle, canonical, row_count = _decode_bundle(raw_bundle, grammar=grammar)
            public = _public_projection(
                canonical=canonical,
                database_count=len(bundle[1]),
                row_count=row_count,
            )
            actual_public = {name: getattr(self, name) for name in PUBLIC_FIELDS}
            return (
                type(self) is DecodedSanitizerPrivilegedGlobalObservationFrames
                and self._seal is _FRAME_SEAL
                and bundle == self._bundle
                and tuple(public) == PUBLIC_FIELDS
                and all(
                    type(actual_public[name]) is type(value)
                    and actual_public[name] == value
                    for name, value in public.items()
                )
                and hmac.compare_digest(
                    self._proof_sha256,
                    _private_proof(
                        public=actual_public, value_identity=id(self), bundle=bundle
                    ),
                )
            )
        except Exception:
            return False


def _decode_privileged_global_observation_frames(
    raw_bundle: object,
) -> DecodedSanitizerPrivilegedGlobalObservationFrames:
    """Internal structural decoder for the future database observer."""

    code = "privileged_global_observation_frames.input_invalid"
    try:
        document, contract_bytes = _read_contract()
        grammar = _validate_contract(document)
        bundle, canonical, row_count = _decode_bundle(raw_bundle, grammar=grammar)
        public = _public_projection(
            canonical=canonical,
            database_count=len(bundle[1]),
            row_count=row_count,
        )
        if tuple(public) != PUBLIC_FIELDS:
            _fail("privileged_global_observation_frames.output_invalid")
        value = object.__new__(DecodedSanitizerPrivilegedGlobalObservationFrames)
        for name, item in public.items():
            object.__setattr__(value, name, item)
        object.__setattr__(value, "_bundle", bundle)
        object.__setattr__(value, "_contract_bytes", bytes(contract_bytes))
        object.__setattr__(
            value,
            "_proof_sha256",
            _private_proof(public=public, value_identity=id(value), bundle=bundle),
        )
        object.__setattr__(value, "_seal", _FRAME_SEAL)
        if not value._is_valid():
            _fail("privileged_global_observation_frames.output_invalid")
        return value
    except Exception as error:
        if (
            type(error) is SanitizerPrivilegedGlobalObservationFramesFailure
            and len(error.args) == 1
            and type(error.args[0]) is str
            and error.args[0] in _FAILURE_CODES
        ):
            code = error.args[0]
    raise SanitizerPrivilegedGlobalObservationFramesFailure(code) from None


def build_sanitizer_privileged_global_observation_frames() -> NoReturn:
    """Refuse public construction until the reviewed database observer exists."""

    load_privileged_global_observation_frames_contract()
    _fail(ACTIVATION_FAILURE)


if __name__ == "__main__":
    load_privileged_global_observation_frames_contract(
        executable="--executable" in sys.argv
    )
    print("sanitizer privileged/global observation frames contract valid (dormant)")
