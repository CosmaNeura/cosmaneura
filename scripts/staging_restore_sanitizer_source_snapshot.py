from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import MappingProxyType
from typing import Any, NoReturn

try:
    from scripts import (
        staging_restore_sanitizer_evidence_authority as evidence_authority,
    )
except ModuleNotFoundError:  # Direct import compatibility for local review.
    import staging_restore_sanitizer_evidence_authority as evidence_authority


INVENTORY_POLICY_SHA256 = (
    "daaf7fc72c9e44dd5b9ae3467e39bb137697868a2beba848d48681ff0141886b"
)
SOURCE_SNAPSHOT_CONTRACT_SHA256 = (
    "bd90ff0fca53e5dfe74532bdec986b06db3c93a3e5acef3144c2128c4b27bd16"
)
ACTIVATION_FAILURE = "activation.data_contract_executor_unimplemented"
INVENTORY_POLICY_ID = "cosmaneura-staging-synthetic-inventory-v1"
INVENTORY_HMAC_KEY_VERSION = "staging-inventory-hmac-v1"
INVENTORY_HMAC_DOMAIN = b"cosmaneura:staging-synthetic-inventory:v1\x00"
SNAPSHOT_HMAC_DOMAIN = b"cosmaneura:staging-sanitizer-source-snapshot:v1\x00"
_HMAC_CONTRACT = MappingProxyType(
    {
        "hmac_domain_prefix": INVENTORY_HMAC_DOMAIN.decode(),
        "retained_state_domain": "{policy_id}:database-table:{table}:state\0",
        "retained_identity_domain": "{policy_id}:database-table:{table}:identities\0",
        "required_empty_domain": "database-table:{table}:empty-count\0",
    }
)
MINIMUM_KEY_BYTES = 32
MAXIMUM_KEY_BYTES = 4096
MAXIMUM_RECEIPT_BYTES = 256 * 1024
MAXIMUM_TOTAL_FRAME_BYTES = 64 * 1024 * 1024
MAXIMUM_STATE_FRAME_BYTES = 1024 * 1024
MAXIMUM_IDENTITY_FRAME_BYTES = 16 * 1024
MAXIMUM_TOTAL_ROWS = 100_000
MAXIMUM_ROWS_PER_TABLE = 50_000
MAXIMUM_JSON_DEPTH = 32
MAXIMUM_JSON_NODES = 50_000
MAXIMUM_NUMBER_TOKEN_BYTES = 128
TABLE_COUNT = 62
RETAINED_TABLE_COUNT = 29
RETAINED_COLUMN_COUNT = 343
IDENTITY_HMAC_TABLE_COUNT = 19
REQUIRED_EMPTY_TABLE_COUNT = 33
_RETAINED_TABLE_NAMES = tuple(
    sorted(
        "appointment authority_actor authority_actor_external_identity authority_person authority_person_binding authority_person_origin authority_tenant authority_tenant_membership baa_acceptance baa_version doctor doctor_note_schema drug_reference ehr_sandbox_patient_designation ehr_sandbox_record_mapping lab_range_set note_type_customization organization_roster_approval patient pilot_access_continuity_amendment pilot_access_continuity_amendment_identity pilot_access_grandfather_grant practice practice_access_audit practice_member provider_organization superadmin_capability_audit superadmin_capability_grant synthetic_preview_appointment_fixture".split()
    )
)
_ZERO = timedelta(0)
_HEX_256_RE = re.compile(r"^[0-9a-f]{64}$")
_INTEGER_RE = re.compile(r"^-?(?:0|[1-9][0-9]*)$")
_NUMBER_RE = re.compile(r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?$")
_VARCHAR_RE = re.compile(r"^character varying(?:\(([1-9][0-9]*)\))?$")
_DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_TIMESTAMP_RE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,6})?$"
)
_TIMESTAMPTZ_RE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,6})?\+00:00$"
)
_ENUM_VALUES = MappingProxyType(
    {
        "doctorrole": frozenset({"normal", "admin"}),
        "planordering": frozenset({"patient_then_doctor", "as_discussed"}),
        "pronounstyle": frozenset({"infer_from_transcript", "neutral"}),
        "subjectivesource": frozenset({"patient_only", "patient_and_doctor"}),
        "terminology": frozenset(
            "doctor_patient therapist_client counselor_client nutritionist_client".split()
        ),
    }
)
_POSITIVE_CLAIMS = MappingProxyType(
    {
        "source_snapshot_authenticated": True,
        "inventory_state_hmacs_authenticated": True,
        "inventory_identity_hmacs_authenticated": True,
        "required_empty_tables_authenticated": True,
    }
)
_NEGATIVE_CLAIMS = MappingProxyType(
    dict.fromkeys(
        "database_connection_authority database_mutation_authority evidence_publication_authority executable live_provider_calls_enabled post_projection_authenticated provider_calls_permitted provider_facts_authenticated runtime_authority sanitizer_authority workflow_integrated".split(),
        False,
    )
)
_SNAPSHOT_SEAL = object()
_EVIDENCE_FAILURE_MAP = MappingProxyType(
    {
        "sanitizer_evidence.clock_invalid": "source_snapshot.clock_invalid",
        "sanitizer_evidence.clock_regressed": "source_snapshot.clock_regressed",
        "sanitizer_evidence.target_expired": "source_snapshot.target_expired",
    }
)
_FAILURE_CODES = frozenset(
    {
        ACTIVATION_FAILURE,
        "source_snapshot.authority_invalid",
        "source_snapshot.clock_invalid",
        "source_snapshot.clock_regressed",
        "source_snapshot.configuration_invalid",
        "source_snapshot.frames_invalid",
        "source_snapshot.integrity_invalid",
        "source_snapshot.output_invalid",
        "source_snapshot.policy_invalid",
        "source_snapshot.target_expired",
    }
)


class SanitizerSourceSnapshotFailure(RuntimeError):
    """A bounded refusal that never identifies a table, row, or value."""


def _fail(code: str) -> NoReturn:
    raise SanitizerSourceSnapshotFailure(code)


def _bounded_code(error: Exception) -> str:
    if (
        type(error) is SanitizerSourceSnapshotFailure
        and len(error.args) == 1
        and type(error.args[0]) is str
        and error.args[0] in _FAILURE_CODES
    ):
        return error.args[0]
    if (
        type(error) is evidence_authority.SanitizerEvidenceFailure
        and len(error.args) == 1
        and type(error.args[0]) is str
    ):
        return _EVIDENCE_FAILURE_MAP.get(
            error.args[0], "source_snapshot.authority_invalid"
        )
    return "source_snapshot.authority_invalid"


@dataclass(frozen=True, slots=True)
class _FrozenMap(Mapping[str, Any]):
    _items: tuple[tuple[str, Any], ...]

    def __getitem__(self, key: str) -> Any:
        for name, value in self._items:
            if name == key:
                return value
        raise KeyError(key)

    def __iter__(self) -> Any:
        return (name for name, _value in self._items)

    def __len__(self) -> int:
        return len(self._items)


@dataclass(frozen=True, slots=True)
class _ParsedObject:
    pairs: tuple[tuple[str, Any], ...]


@dataclass(frozen=True, slots=True)
class _JsonNumber:
    token: str
    integer: bool


@dataclass(frozen=True, slots=True)
class _TableSpec:
    name: str
    disposition: str
    columns: tuple[tuple[str, str, bool], ...]
    identity_columns: tuple[str, ...]


Frame = tuple[bytes | None, bytes]
OrderedTableFrames = tuple[tuple[str, tuple[Frame, ...]], ...]
FrozenTables = tuple[tuple[str, tuple[_FrozenMap, ...]], ...]
FragmentProofs = tuple[tuple[str, tuple[str, ...]], ...]


def _trusted_utc(clock: Callable[[], datetime]) -> datetime:
    try:
        value = clock()
    except Exception:
        _fail("source_snapshot.clock_invalid")
    if (
        type(value) is not datetime
        or value.tzinfo is not UTC
        or value.utcoffset() != _ZERO
        or value.microsecond != 0
        or value.fold != 0
    ):
        _fail("source_snapshot.clock_invalid")
    return value


def _has_module_seal(value: object, method_name: str, seal_name: str) -> bool:
    method = type(value).__dict__.get(method_name)
    namespace = getattr(method, "__globals__", {})
    return (
        callable(method)
        and type(namespace) is dict
        and namespace.get(seal_name) is getattr(value, "_seal", None)
    )


def _validate_retained_evidence(
    evidence: evidence_authority.AuthenticatedSanitizerEvidence,
    *,
    now: datetime,
    inventory_policy_bytes: bytes | None = None,
) -> Any:
    evidence_authority._validate_retained_envelope_at_current_time(  # noqa: SLF001
        evidence, now=now
    )
    source = evidence._fixed_catalog_source_state  # noqa: SLF001
    target = source._target_binding  # noqa: SLF001
    written = target._written_receipt  # noqa: SLF001
    built = written._built_receipt  # noqa: SLF001
    try:
        retained_until = datetime.strptime(
            written.retained_until, "%Y-%m-%dT%H:%M:%SZ"
        ).replace(tzinfo=UTC)
        source_policy_bytes = source._inventory_policy_bytes  # noqa: SLF001
        policy_matches = inventory_policy_bytes is None or hmac.compare_digest(
            source_policy_bytes, inventory_policy_bytes
        )
    except Exception:
        _fail("source_snapshot.authority_invalid")
    if (
        type(source) is not evidence._fixed_catalog_source_state_type  # noqa: SLF001
        or source._seal is not evidence._fixed_catalog_source_state_seal  # noqa: SLF001
        or not _has_module_seal(
            source, "_reauthenticate_at_current_time", "_SOURCE_STATE_SEAL"
        )
        or type(source_policy_bytes) is not bytes
        or hashlib.sha256(source_policy_bytes).hexdigest() != INVENTORY_POLICY_SHA256
        or not policy_matches
        or source.inventory_policy_sha256 != f"sha256:{INVENTORY_POLICY_SHA256}"
        or source.hmac_key_version != INVENTORY_HMAC_KEY_VERSION
        or source.source_profile != "pre"
        or source.source_revision != "89c31f0ae275"
        or source.authenticated_at > now
        or not _has_module_seal(target, "_is_bound", "_TARGET_BINDING_SEAL")
        or target.bound_at > source.authenticated_at
        or not (written.receipt_sha256 == target.receipt_sha256 == built.receipt_sha256)
    ):
        _fail("source_snapshot.authority_invalid")
    if retained_until <= now or now >= min(target.expires_at, built.cleanup_not_after):
        _fail("source_snapshot.target_expired")
    return source


def _canonical(value: Any, code: str) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeEncodeError, RecursionError):
        _fail(code)


def _parse_object(
    raw: object,
    code: str,
    *,
    digest: str | None = None,
    maximum: int | None = None,
    canonical: bool = False,
) -> dict[str, Any]:
    if (
        type(raw) is not bytes
        or (maximum is not None and not 0 < len(raw) <= maximum)
        or (
            digest is not None
            and not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), digest)
        )
    ):
        _fail(code)

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for name, value in pairs:
            if name in result:
                _fail(code)
            result[name] = value
        return result

    try:
        value = json.loads(
            raw,
            object_pairs_hook=reject_duplicates,
            parse_constant=lambda _token: _fail(code),
            parse_float=(lambda _token: _fail(code)) if canonical else float,
        )
    except SanitizerSourceSnapshotFailure:
        raise
    except (UnicodeDecodeError, ValueError, RecursionError):
        _fail(code)
    if type(value) is not dict or (
        canonical and not hmac.compare_digest(raw, _canonical(value, code))
    ):
        _fail(code)
    return value


def _parse_document(raw: bytes, digest: str) -> dict[str, Any]:
    return _parse_object(raw, "source_snapshot.policy_invalid", digest=digest)


def _parse_receipt(raw: object) -> dict[str, Any]:
    return _parse_object(
        raw,
        "source_snapshot.authority_invalid",
        maximum=MAXIMUM_RECEIPT_BYTES,
        canonical=True,
    )


def _safe_string(value: object) -> bool:
    if type(value) is not str or "\x00" in value:
        return False
    return not any(0xD800 <= ord(character) <= 0xDFFF for character in value)


def _number(token: str, *, integer: bool) -> _JsonNumber:
    expression = _INTEGER_RE if integer else _NUMBER_RE
    if len(
        token.encode("ascii")
    ) > MAXIMUM_NUMBER_TOKEN_BYTES or not expression.fullmatch(token):
        _fail("source_snapshot.frames_invalid")
    return _JsonNumber(token, integer)


def _load_frame_json(raw: bytes) -> Any:
    if raw.startswith(b"\xef\xbb\xbf") or b"\x00" in raw:
        _fail("source_snapshot.frames_invalid")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        _fail("source_snapshot.frames_invalid")
    if not text or text != text.strip() or text.encode("utf-8") != raw:
        _fail("source_snapshot.frames_invalid")

    def object_pairs(pairs: list[tuple[str, Any]]) -> _ParsedObject:
        names: set[str] = set()
        result: list[tuple[str, Any]] = []
        for name, value in pairs:
            if not _safe_string(name) or name in names:
                _fail("source_snapshot.frames_invalid")
            names.add(name)
            result.append((name, value))
        return _ParsedObject(tuple(result))

    try:
        value = json.loads(
            text,
            object_pairs_hook=object_pairs,
            parse_int=lambda token: _number(token, integer=True),
            parse_float=lambda token: _number(token, integer=False),
            parse_constant=lambda _token: _fail("source_snapshot.frames_invalid"),
        )
    except SanitizerSourceSnapshotFailure:
        raise
    except (UnicodeDecodeError, ValueError, RecursionError):
        _fail("source_snapshot.frames_invalid")

    nodes = 0

    def inspect(item: Any, depth: int) -> None:
        nonlocal nodes
        nodes += 1
        if nodes > MAXIMUM_JSON_NODES or depth > MAXIMUM_JSON_DEPTH:
            _fail("source_snapshot.frames_invalid")
        if type(item) is str:
            if not _safe_string(item):
                _fail("source_snapshot.frames_invalid")
            return
        if item is None or type(item) in {bool, _JsonNumber}:
            return
        if type(item) is list:
            for child in item:
                inspect(child, depth + 1)
            return
        if type(item) is _ParsedObject:
            for name, child in item.pairs:
                inspect(name, depth + 1)
                inspect(child, depth + 1)
            return
        _fail("source_snapshot.frames_invalid")

    inspect(value, 0)
    return value


def _json_value(value: Any) -> Any:
    if value is None or type(value) in {bool, str}:
        return value
    if type(value) is _JsonNumber:
        if value.integer:
            if value.token == "-0":
                _fail("source_snapshot.frames_invalid")
            converted = int(value.token)
            if -(2**63) <= converted <= 2**63 - 1:
                return converted
            _fail("source_snapshot.frames_invalid")
        converted_float = float(value.token)
        if not math.isfinite(converted_float) or Decimal.from_float(
            converted_float
        ) != Decimal(value.token):
            _fail("source_snapshot.frames_invalid")
        return converted_float
    if type(value) is list:
        return tuple(_json_value(item) for item in value)
    if type(value) is _ParsedObject:
        return _FrozenMap(
            tuple((name, _json_value(item)) for name, item in value.pairs)
        )
    _fail("source_snapshot.frames_invalid")


def _maximum_length(data_type: str) -> int | None:
    match = _VARCHAR_RE.fullmatch(data_type)
    return int(match.group(1)) if match is not None and match.group(1) else None


def _typed_value(value: Any, data_type: str, required: bool) -> Any:
    if value is None:
        if required:
            _fail("source_snapshot.frames_invalid")
        return None
    if data_type == "uuid" and type(value) is str:
        try:
            parsed_uuid = uuid.UUID(value)
        except (ValueError, AttributeError):
            _fail("source_snapshot.frames_invalid")
        if str(parsed_uuid) == value:
            return parsed_uuid
        _fail("source_snapshot.frames_invalid")
    if data_type in {"integer", "bigint"} and type(value) is _JsonNumber:
        if not value.integer:
            _fail("source_snapshot.frames_invalid")
        converted = _json_value(value)
        lower, upper = (
            (-(2**31), 2**31 - 1) if data_type == "integer" else (-(2**63), 2**63 - 1)
        )
        if lower <= converted <= upper:
            return converted
        _fail("source_snapshot.frames_invalid")
    if data_type == "boolean" and type(value) is bool:
        return value
    if data_type == "double precision" and type(value) is _JsonNumber:
        converted_float = float(value.token)
        if math.isfinite(converted_float):
            return converted_float
        _fail("source_snapshot.frames_invalid")
    if data_type == "date" and type(value) is str and _DATE_RE.fullmatch(value):
        try:
            converted_date = date.fromisoformat(value)
        except ValueError:
            _fail("source_snapshot.frames_invalid")
        if converted_date.isoformat() == value:
            return converted_date
        _fail("source_snapshot.frames_invalid")
    if (
        data_type == "timestamp with time zone"
        and type(value) is str
        and _TIMESTAMPTZ_RE.fullmatch(value)
    ):
        try:
            converted_aware = datetime.fromisoformat(value)
        except ValueError:
            _fail("source_snapshot.frames_invalid")
        if converted_aware.utcoffset() == _ZERO:
            return converted_aware.astimezone(UTC).replace(fold=0)
        _fail("source_snapshot.frames_invalid")
    if (
        data_type == "timestamp without time zone"
        and type(value) is str
        and _TIMESTAMP_RE.fullmatch(value)
    ):
        try:
            converted_naive = datetime.fromisoformat(value)
        except ValueError:
            _fail("source_snapshot.frames_invalid")
        if converted_naive.tzinfo is None:
            return converted_naive.replace(fold=0)
        _fail("source_snapshot.frames_invalid")
    if data_type in {"json", "jsonb"}:
        return _json_value(value)
    maximum = _maximum_length(data_type)
    if (
        data_type == "text" or maximum is not None or data_type == "character varying"
    ) and type(value) is str:
        if maximum is None or len(value) <= maximum:
            return value
        _fail("source_snapshot.frames_invalid")
    if data_type in _ENUM_VALUES and type(value) is str:
        if value in _ENUM_VALUES[data_type]:
            return value
    _fail("source_snapshot.frames_invalid")


def _parse_row(raw: bytes, spec: _TableSpec) -> _FrozenMap:
    parsed = _load_frame_json(raw)
    if type(parsed) is not _ParsedObject:
        _fail("source_snapshot.frames_invalid")
    if tuple(name for name, _value in parsed.pairs) != tuple(
        name for name, _data_type, _required in spec.columns
    ):
        _fail("source_snapshot.frames_invalid")
    values = {
        name: (item, data_type, required)
        for (name, item), (_expected, data_type, required) in zip(
            parsed.pairs, spec.columns, strict=True
        )
    }
    return _FrozenMap(
        tuple(
            (name, _typed_value(*values[name]))
            for name, _data_type, _required in spec.columns
        )
    )


def _parse_identity(raw: bytes, spec: _TableSpec, row: _FrozenMap) -> None:
    parsed = _load_frame_json(raw)
    if type(parsed) is not list or len(parsed) != len(spec.identity_columns):
        _fail("source_snapshot.frames_invalid")
    columns = {
        name: (data_type, required) for name, data_type, required in spec.columns
    }
    for name, item in zip(spec.identity_columns, parsed, strict=True):
        if _typed_value(item, *columns[name]) != row[name]:
            _fail("source_snapshot.frames_invalid")


def _update_mac(mac: hmac.HMAC, value: bytes) -> None:
    mac.update(len(value).to_bytes(8, "big"))
    mac.update(value)


def _inventory_digest(key: bytes, domain: str, values: tuple[bytes, ...]) -> str:
    mac = hmac.new(
        key, INVENTORY_HMAC_DOMAIN + domain.encode("ascii") + b"\x00", hashlib.sha256
    )
    for value in values:
        _update_mac(mac, value)
    return mac.hexdigest()


def _frame_proof(
    key: bytes, table: str, index: int, identity: bytes | None, state: bytes
) -> str:
    mac = hmac.new(key, SNAPSHOT_HMAC_DOMAIN + b"row\x00", hashlib.sha256)
    _update_mac(mac, table.encode("ascii"))
    _update_mac(mac, index.to_bytes(8, "big"))
    _update_mac(mac, b"none" if identity is None else b"bytes")
    if identity is not None:
        _update_mac(mac, identity)
    _update_mac(mac, state)
    return mac.hexdigest()


def _pack(value: Any) -> Any:
    if value is None or type(value) in {bool, int, str}:
        return value
    if type(value) is float and math.isfinite(value):
        return {"$float": value.hex()}
    if type(value) is uuid.UUID:
        return {"$uuid": str(value)}
    if type(value) is date and type(value) is not datetime:
        return {"$date": value.isoformat()}
    if type(value) is datetime:
        return {"$datetime": [value.isoformat(), value.fold]}
    if type(value) is dict and all(type(name) is str for name in value):
        return {name: _pack(item) for name, item in value.items()}
    if type(value) is _FrozenMap:
        return {"$map": [[name, _pack(item)] for name, item in value._items]}
    if type(value) is tuple:
        return {"$tuple": [_pack(item) for item in value]}
    _fail("source_snapshot.output_invalid")


def _proof(
    key: bytes,
    public: Mapping[str, Any],
    tables: FrozenTables,
    fragment_proofs: FragmentProofs,
    evidence: evidence_authority.AuthenticatedSanitizerEvidence,
    snapshot_identity: int,
) -> str:
    material = _canonical(
        _pack(
            {
                "contract_sha256": SOURCE_SNAPSHOT_CONTRACT_SHA256,
                "evidence_identity": id(evidence),
                "evidence_proof_sha256": evidence._proof_sha256,  # noqa: SLF001
                "evidence_seal_identity": id(evidence._seal),  # noqa: SLF001
                "evidence_type_identity": id(type(evidence)),
                "fragment_proofs": fragment_proofs,
                "public": dict(public),
                "snapshot_identity": snapshot_identity,
                "tables": tables,
            }
        ),
        "source_snapshot.output_invalid",
    )
    return _inventory_digest(key, "source-snapshot:proof", (material,))


def _thaw(value: Any) -> Any:
    if type(value) is _FrozenMap:
        return {name: _thaw(item) for name, item in value._items}
    if type(value) is tuple:
        return [_thaw(item) for item in value]
    return value


def _is_frozen(value: Any) -> bool:
    kind = type(value)
    if value is None or kind in {bool, int, str, uuid.UUID, date}:
        return kind is not str or _safe_string(value)
    if kind is float:
        return math.isfinite(value)
    if kind is datetime:
        return value.fold == 0 and (value.tzinfo is None or value.tzinfo is UTC)
    if kind is tuple:
        return all(_is_frozen(item) for item in value)
    if kind is _FrozenMap and type(value._items) is tuple:
        names = tuple(name for name, _item in value._items)
        return (
            len(names) == len(set(names))
            and all(type(name) is str and _safe_string(name) for name in names)
            and all(_is_frozen(item) for _name, item in value._items)
        )
    return False


def _snapshot_public(
    evidence: evidence_authority.AuthenticatedSanitizerEvidence,
    tables: FrozenTables,
    authenticated_at: datetime,
) -> dict[str, Any]:
    return {
        "inventory_receipt_sha256": evidence.inventory_receipt_sha256,
        "inventory_policy_sha256": f"sha256:{INVENTORY_POLICY_SHA256}",
        "source_profile": "pre",
        "source_revision": "89c31f0ae275",
        "table_count": TABLE_COUNT,
        "retained_table_count": RETAINED_TABLE_COUNT,
        "required_empty_table_count": REQUIRED_EMPTY_TABLE_COUNT,
        "source_row_count": sum(len(rows) for _name, rows in tables),
        "authenticated_at": authenticated_at,
        **_POSITIVE_CLAIMS,
        **_NEGATIVE_CLAIMS,
    }


@dataclass(frozen=True, slots=True, init=False, repr=False)
class AuthenticatedSanitizerSourceSnapshot:
    inventory_receipt_sha256: str
    inventory_policy_sha256: str
    source_profile: str
    source_revision: str
    table_count: int
    retained_table_count: int
    required_empty_table_count: int
    source_row_count: int
    authenticated_at: datetime
    source_snapshot_authenticated: bool
    inventory_state_hmacs_authenticated: bool
    inventory_identity_hmacs_authenticated: bool
    required_empty_tables_authenticated: bool
    database_connection_authority: bool
    database_mutation_authority: bool
    evidence_publication_authority: bool
    executable: bool
    live_provider_calls_enabled: bool
    post_projection_authenticated: bool
    provider_calls_permitted: bool
    provider_facts_authenticated: bool
    runtime_authority: bool
    sanitizer_authority: bool
    workflow_integrated: bool
    _tables: FrozenTables = dataclass_field(repr=False)
    _raw_fragment_proofs: FragmentProofs = dataclass_field(repr=False)
    _authenticated_evidence: evidence_authority.AuthenticatedSanitizerEvidence = (
        dataclass_field(repr=False)
    )
    _contract_sha256: str = dataclass_field(repr=False)
    _proof_sha256: str = dataclass_field(repr=False)
    _seal: object = dataclass_field(repr=False, compare=False)

    def __init__(self) -> None:
        _fail("source_snapshot.output_invalid")

    def __repr__(self) -> str:
        return "<AuthenticatedSanitizerSourceSnapshot sealed sensitive>"

    def __reduce__(self) -> NoReturn:
        del self
        _fail("source_snapshot.output_invalid")

    def __reduce_ex__(self, protocol: object) -> NoReturn:
        del self, protocol
        _fail("source_snapshot.output_invalid")

    def _rows_for_table(self, table_name: str) -> tuple[dict[str, Any], ...]:
        if type(table_name) is not str or self._seal is not _SNAPSHOT_SEAL:
            del self, table_name
            _fail("source_snapshot.output_invalid")
        match = next((rows for name, rows in self._tables if name == table_name), None)
        if match is not None:
            return tuple(_thaw(row) for row in match)
        del self, table_name, match
        _fail("source_snapshot.output_invalid")

    def _reauthenticate_at_current_time(
        self, *, now: datetime, inventory_hmac_key: object = None
    ) -> None:
        code = _retained_snapshot_failure(
            self, now=now, inventory_hmac_key=inventory_hmac_key
        )
        if code is not None:
            del self, now, inventory_hmac_key
            _fail(code)


def _validate_retained_snapshot_at_current_time(
    value: AuthenticatedSanitizerSourceSnapshot,
    *,
    now: datetime,
    inventory_hmac_key: object = None,
) -> None:
    code = _retained_snapshot_failure(
        value, now=now, inventory_hmac_key=inventory_hmac_key
    )
    if code is not None:
        del value, now, inventory_hmac_key
        _fail(code)


def _retained_snapshot_failure(
    value: AuthenticatedSanitizerSourceSnapshot,
    *,
    now: datetime,
    inventory_hmac_key: object,
) -> str | None:
    try:
        if (
            type(now) is not datetime
            or now.tzinfo is not UTC
            or now.utcoffset() != _ZERO
            or now.microsecond != 0
            or now.fold != 0
            or type(value) is not AuthenticatedSanitizerSourceSnapshot
            or getattr(value, "_seal", None) is not _SNAPSHOT_SEAL
            or type(inventory_hmac_key) is not bytes
            or not MINIMUM_KEY_BYTES <= len(inventory_hmac_key) <= MAXIMUM_KEY_BYTES
        ):
            _fail("source_snapshot.output_invalid")
        evidence = value._authenticated_evidence
        _validate_retained_evidence(evidence, now=now)
        if (
            type(value.authenticated_at) is not datetime
            or value.authenticated_at.tzinfo is not UTC
            or value.authenticated_at.microsecond != 0
            or value.authenticated_at.fold != 0
            or value.authenticated_at > now
            or any(
                type(getattr(value, name)) is not str
                for name in (
                    "inventory_receipt_sha256",
                    "inventory_policy_sha256",
                    "source_profile",
                    "source_revision",
                )
            )
            or any(
                type(getattr(value, name)) is not int
                for name in (
                    "table_count",
                    "retained_table_count",
                    "required_empty_table_count",
                    "source_row_count",
                )
            )
            or type(value._tables) is not tuple
            or len(value._tables) != RETAINED_TABLE_COUNT
            or any(
                type(item) is not tuple
                or len(item) != 2
                or type(item[0]) is not str
                or type(item[1]) is not tuple
                or any(
                    type(row) is not _FrozenMap or not _is_frozen(row)
                    for row in item[1]
                )
                for item in value._tables
            )
            or tuple(name for name, _rows in value._tables) != _RETAINED_TABLE_NAMES
            or len({name for name, _rows in value._tables}) != RETAINED_TABLE_COUNT
            or type(value._raw_fragment_proofs) is not tuple
            or tuple(name for name, _proofs in value._raw_fragment_proofs)
            != tuple(name for name, _rows in value._tables)
            or any(
                type(proofs) is not tuple
                or len(proofs) != len(dict(value._tables)[name])
                or any(
                    type(proof) is not str or _HEX_256_RE.fullmatch(proof) is None
                    for proof in proofs
                )
                for name, proofs in value._raw_fragment_proofs
            )
            or type(value._proof_sha256) is not str
            or _HEX_256_RE.fullmatch(value._proof_sha256) is None
            or value._contract_sha256 != SOURCE_SNAPSHOT_CONTRACT_SHA256
        ):
            _fail("source_snapshot.output_invalid")
        public = _snapshot_public(evidence, value._tables, value.authenticated_at)
        if (
            any(getattr(value, name) != item for name, item in public.items())
            or any(
                getattr(value, name) is not item
                for name, item in _POSITIVE_CLAIMS.items()
            )
            or any(
                getattr(value, name) is not item
                for name, item in _NEGATIVE_CLAIMS.items()
            )
            or not hmac.compare_digest(
                value._proof_sha256,
                _proof(
                    inventory_hmac_key,
                    public,
                    value._tables,
                    value._raw_fragment_proofs,
                    evidence,
                    id(value),
                ),
            )
        ):
            _fail("source_snapshot.output_invalid")
    except SanitizerSourceSnapshotFailure as error:
        return _bounded_code(error)
    except evidence_authority.SanitizerEvidenceFailure as error:
        return _bounded_code(error)
    except Exception:
        return "source_snapshot.output_invalid"
    return None


def _mint_snapshot(
    *,
    key: bytes,
    evidence: evidence_authority.AuthenticatedSanitizerEvidence,
    tables: FrozenTables,
    fragment_proofs: FragmentProofs,
    authenticated_at: datetime,
) -> AuthenticatedSanitizerSourceSnapshot:
    value = object.__new__(AuthenticatedSanitizerSourceSnapshot)
    public = _snapshot_public(evidence, tables, authenticated_at)
    for name, item in public.items():
        object.__setattr__(value, name, item)
    object.__setattr__(value, "_tables", tables)
    object.__setattr__(value, "_raw_fragment_proofs", fragment_proofs)
    object.__setattr__(value, "_authenticated_evidence", evidence)
    object.__setattr__(value, "_contract_sha256", SOURCE_SNAPSHOT_CONTRACT_SHA256)
    keyed_proof = _proof(
        key,
        public,
        tables,
        fragment_proofs,
        evidence,
        id(value),
    )
    object.__setattr__(value, "_proof_sha256", keyed_proof)
    object.__setattr__(value, "_seal", _SNAPSHOT_SEAL)
    if (
        type(value) is not AuthenticatedSanitizerSourceSnapshot
        or value._seal is not _SNAPSHOT_SEAL
        or value._authenticated_evidence is not evidence
        or value._contract_sha256 != SOURCE_SNAPSHOT_CONTRACT_SHA256
        or any(getattr(value, name) != item for name, item in public.items())
        or not hmac.compare_digest(
            value._proof_sha256,
            _proof(key, public, tables, fragment_proofs, evidence, id(value)),
        )
    ):
        _fail("source_snapshot.output_invalid")
    return value


class SanitizerSourceSnapshotAuthenticator:
    __slots__ = ("_clock", "_key", "_last", "_policy_bytes", "_specs")

    def __init__(self) -> None:
        _fail("source_snapshot.configuration_invalid")

    def __setattr__(self, name: str, value: object) -> NoReturn:
        del self, name, value
        _fail("source_snapshot.configuration_invalid")

    def __delattr__(self, name: str) -> NoReturn:
        del self, name
        _fail("source_snapshot.configuration_invalid")

    def __repr__(self) -> str:
        return "<SanitizerSourceSnapshotAuthenticator dormant in-memory-only>"

    def __reduce__(self) -> NoReturn:
        del self
        _fail("source_snapshot.configuration_invalid")

    def __reduce_ex__(self, protocol: object) -> NoReturn:
        del self, protocol
        _fail("source_snapshot.configuration_invalid")

    @classmethod
    def _from_reviewed_configuration(
        cls,
        *,
        clock: Callable[[], datetime],
        inventory_hmac_key: bytes,
        inventory_hmac_key_version: str,
        inventory_policy_bytes: bytes,
        source_snapshot_contract_bytes: bytes,
    ) -> SanitizerSourceSnapshotAuthenticator:
        try:
            return cls._build_from_reviewed_configuration(
                clock=clock,
                inventory_hmac_key=inventory_hmac_key,
                inventory_hmac_key_version=inventory_hmac_key_version,
                inventory_policy_bytes=inventory_policy_bytes,
                source_snapshot_contract_bytes=source_snapshot_contract_bytes,
            )
        except Exception as error:
            code = _bounded_code(error)
        del cls, clock, inventory_hmac_key, inventory_policy_bytes
        del inventory_hmac_key_version, source_snapshot_contract_bytes
        _fail(code)

    @classmethod
    def _build_from_reviewed_configuration(
        cls,
        *,
        clock: Callable[[], datetime],
        inventory_hmac_key: bytes,
        inventory_hmac_key_version: str,
        inventory_policy_bytes: bytes,
        source_snapshot_contract_bytes: bytes,
    ) -> SanitizerSourceSnapshotAuthenticator:
        if (
            cls is not SanitizerSourceSnapshotAuthenticator
            or not callable(clock)
            or type(inventory_hmac_key) is not bytes
            or not MINIMUM_KEY_BYTES <= len(inventory_hmac_key) <= MAXIMUM_KEY_BYTES
            or type(inventory_hmac_key_version) is not str
            or inventory_hmac_key_version != INVENTORY_HMAC_KEY_VERSION
        ):
            _fail("source_snapshot.configuration_invalid")
        policy = _parse_document(inventory_policy_bytes, INVENTORY_POLICY_SHA256)
        contract = _parse_document(
            source_snapshot_contract_bytes, SOURCE_SNAPSHOT_CONTRACT_SHA256
        )
        try:
            profile = policy["profiles"]["pre"]
            excluded = set(profile["excluded_tables"])
            exclusions = profile["column_exclusions"]
            specs = []
            for table_name, table in policy["database"]["tables"].items():
                if table_name in excluded:
                    continue
                removed = set(exclusions.get(table_name, []))
                columns = tuple(
                    (name, data_type, required)
                    for name, data_type, required in table["columns"]
                    if name not in removed
                )
                specs.append(
                    _TableSpec(
                        table_name,
                        table["disposition"],
                        columns,
                        tuple(table.get("identity_columns", [])),
                    )
                )
            specs.sort(key=lambda item: item.name)
            inventory_contract = contract["inventory"]
            authentication = contract["authentication"]
            limits = contract["limits"]
            claims = contract["claims"]
        except (KeyError, TypeError, ValueError):
            _fail("source_snapshot.policy_invalid")
        retained = tuple(spec for spec in specs if spec.disposition != "required_empty")
        identity = tuple(spec for spec in retained if spec.identity_columns)
        if (
            policy.get("policy_id") != INVENTORY_POLICY_ID
            or profile.get("revision") != "89c31f0ae275"
            or len(specs) != TABLE_COUNT
            or len(retained) != RETAINED_TABLE_COUNT
            or sum(len(spec.columns) for spec in retained) != RETAINED_COLUMN_COUNT
            or len(identity) != IDENTITY_HMAC_TABLE_COUNT
            or len(specs) - len(retained) != REQUIRED_EMPTY_TABLE_COUNT
            or inventory_contract.get("policy_sha256") != INVENTORY_POLICY_SHA256
            or inventory_contract.get("hmac_key_version") != INVENTORY_HMAC_KEY_VERSION
            or inventory_contract.get("table_count") != TABLE_COUNT
            or inventory_contract.get("retained_table_count") != RETAINED_TABLE_COUNT
            or inventory_contract.get("identity_hmac_table_count")
            != IDENTITY_HMAC_TABLE_COUNT
            or inventory_contract.get("required_empty_table_count")
            != REQUIRED_EMPTY_TABLE_COUNT
            or any(
                authentication.get(name) != item
                for name, item in _HMAC_CONTRACT.items()
            )
            or limits
            != {
                "maximum_total_frame_bytes": MAXIMUM_TOTAL_FRAME_BYTES,
                "maximum_state_frame_bytes": MAXIMUM_STATE_FRAME_BYTES,
                "maximum_identity_frame_bytes": MAXIMUM_IDENTITY_FRAME_BYTES,
                "maximum_total_rows": MAXIMUM_TOTAL_ROWS,
                "maximum_rows_per_table": MAXIMUM_ROWS_PER_TABLE,
                "maximum_json_depth": MAXIMUM_JSON_DEPTH,
                "maximum_json_nodes_per_frame": MAXIMUM_JSON_NODES,
                "maximum_number_token_bytes": MAXIMUM_NUMBER_TOKEN_BYTES,
            }
            or claims != {**_POSITIVE_CLAIMS, **_NEGATIVE_CLAIMS}
        ):
            _fail("source_snapshot.policy_invalid")
        value = object.__new__(cls)
        object.__setattr__(value, "_clock", clock)
        object.__setattr__(value, "_last", None)
        object.__setattr__(value, "_key", inventory_hmac_key)
        object.__setattr__(value, "_policy_bytes", inventory_policy_bytes)
        object.__setattr__(value, "_specs", tuple(specs))
        return value

    def _read_clock(self) -> datetime:
        now = _trusted_utc(self._clock)
        if self._last is not None and now < self._last:
            _fail("source_snapshot.clock_regressed")
        object.__setattr__(self, "_last", now)
        return now

    def _reauthenticate(
        self,
        evidence: evidence_authority.AuthenticatedSanitizerEvidence,
        now: datetime,
    ) -> None:
        try:
            source = _validate_retained_evidence(
                evidence,
                now=now,
                inventory_policy_bytes=self._policy_bytes,
            )
            if (
                type(source) is not evidence._fixed_catalog_source_state_type  # noqa: SLF001
                or source._seal  # noqa: SLF001
                is not evidence._fixed_catalog_source_state_seal  # noqa: SLF001
                or source._inventory_policy_bytes != self._policy_bytes  # noqa: SLF001
                or source.hmac_key_version != INVENTORY_HMAC_KEY_VERSION
            ):
                _fail("source_snapshot.authority_invalid")
        except SanitizerSourceSnapshotFailure:
            raise
        except evidence_authority.SanitizerEvidenceFailure as error:
            code = (
                _EVIDENCE_FAILURE_MAP.get(error.args[0])
                if len(error.args) == 1 and type(error.args[0]) is str
                else None
            )
            _fail(code or "source_snapshot.authority_invalid")
        except Exception:
            _fail("source_snapshot.authority_invalid")

    def _receipt(
        self, evidence: evidence_authority.AuthenticatedSanitizerEvidence
    ) -> dict[str, Any]:
        try:
            source = evidence._fixed_catalog_source_state  # noqa: SLF001
            receipt = _parse_receipt(source._inventory_receipt_bytes)  # noqa: SLF001
            unsigned = dict(receipt)
            receipt_sha256 = unsigned.pop("receipt_sha256")
            calculated = hashlib.sha256(
                _canonical(unsigned, "source_snapshot.authority_invalid")
            ).hexdigest()
            counts = receipt["counts"]
            states = receipt["state_hmacs"]
            identities = receipt["identity_hmacs"]
        except SanitizerSourceSnapshotFailure:
            raise
        except Exception:
            _fail("source_snapshot.authority_invalid")
        expected_names = {spec.name for spec in self._specs}
        identity_names = {spec.name for spec in self._specs if spec.identity_columns}
        if (
            type(receipt_sha256) is not str
            or not hmac.compare_digest(receipt_sha256, calculated)
            or evidence.inventory_receipt_sha256 != f"sha256:{calculated}"
            or source.inventory_receipt_sha256 != f"sha256:{calculated}"
            or receipt.get("policy_id") != INVENTORY_POLICY_ID
            or receipt.get("policy_sha256") != INVENTORY_POLICY_SHA256
            or receipt.get("hmac_key_version") != INVENTORY_HMAC_KEY_VERSION
            or receipt.get("phase") != "pre"
            or receipt.get("outcome") != "pass"
            or receipt.get("errors") != []
            or type(counts) is not dict
            or type(states) is not dict
            or type(identities) is not dict
            or set(states) != expected_names
            or set(identities) != identity_names
            or any(
                type(counts.get(f"database.{name}")) is not int
                or counts[f"database.{name}"] < 0
                for name in expected_names
            )
            or any(
                type(value) is not str or _HEX_256_RE.fullmatch(value) is None
                for value in (*states.values(), *identities.values())
            )
        ):
            _fail("source_snapshot.authority_invalid")
        return receipt

    def _validated_frames(self, frames: object) -> OrderedTableFrames:
        if type(frames) is not tuple or len(frames) != TABLE_COUNT:
            _fail("source_snapshot.frames_invalid")
        total_bytes = 0
        total_rows = 0
        expected_names = tuple(spec.name for spec in self._specs)
        if any(type(item) is not tuple or len(item) != 2 for item in frames):
            _fail("source_snapshot.frames_invalid")
        if any(type(item[0]) is not str for item in frames):
            _fail("source_snapshot.frames_invalid")
        if tuple(item[0] for item in frames) != expected_names:
            _fail("source_snapshot.frames_invalid")
        for spec, (name, rows) in zip(self._specs, frames, strict=True):
            if type(name) is not str or type(rows) is not tuple:
                _fail("source_snapshot.frames_invalid")
            if len(rows) > MAXIMUM_ROWS_PER_TABLE:
                _fail("source_snapshot.frames_invalid")
            if spec.disposition == "required_empty" and rows:
                _fail("source_snapshot.frames_invalid")
            total_rows += len(rows)
            if total_rows > MAXIMUM_TOTAL_ROWS:
                _fail("source_snapshot.frames_invalid")
            for frame in rows:
                if type(frame) is not tuple or len(frame) != 2:
                    _fail("source_snapshot.frames_invalid")
                identity, state = frame
                if (
                    (spec.identity_columns and type(identity) is not bytes)
                    or (not spec.identity_columns and identity is not None)
                    or type(state) is not bytes
                    or not 0 < len(state) <= MAXIMUM_STATE_FRAME_BYTES
                    or (
                        type(identity) is bytes
                        and not 0 < len(identity) <= MAXIMUM_IDENTITY_FRAME_BYTES
                    )
                ):
                    _fail("source_snapshot.frames_invalid")
                total_bytes += len(state) + (
                    len(identity) if identity is not None else 0
                )
                if total_bytes > MAXIMUM_TOTAL_FRAME_BYTES:
                    _fail("source_snapshot.frames_invalid")
        return frames

    def _verify_hmacs(
        self,
        receipt: Mapping[str, Any],
        frames: OrderedTableFrames,
    ) -> None:
        counts = receipt["counts"]
        states = receipt["state_hmacs"]
        identities = receipt["identity_hmacs"]
        for spec, (_name, rows) in zip(self._specs, frames, strict=True):
            if counts[f"database.{spec.name}"] != len(rows):
                _fail("source_snapshot.integrity_invalid")
            if spec.disposition == "required_empty":
                state = _inventory_digest(
                    self._key,
                    f"database-table:{spec.name}:empty-count",
                    (b"0",),
                )
            else:
                state = _inventory_digest(
                    self._key,
                    f"{INVENTORY_POLICY_ID}:database-table:{spec.name}:state",
                    tuple(frame[1] for frame in rows),
                )
            if not hmac.compare_digest(state, states[spec.name]):
                _fail("source_snapshot.integrity_invalid")
            if spec.identity_columns:
                identity = _inventory_digest(
                    self._key,
                    f"{INVENTORY_POLICY_ID}:database-table:{spec.name}:identities",
                    tuple(frame[0] for frame in rows if frame[0] is not None),
                )
                if not hmac.compare_digest(identity, identities[spec.name]):
                    _fail("source_snapshot.integrity_invalid")

    def authenticate(
        self,
        *,
        authenticated_sanitizer_evidence: evidence_authority.AuthenticatedSanitizerEvidence,
        ordered_table_frames: OrderedTableFrames,
    ) -> AuthenticatedSanitizerSourceSnapshot:
        try:
            return self._authenticate(
                authenticated_sanitizer_evidence=authenticated_sanitizer_evidence,
                ordered_table_frames=ordered_table_frames,
            )
        except Exception as error:
            code = _bounded_code(error)
        del self, authenticated_sanitizer_evidence, ordered_table_frames
        _fail(code)

    def _authenticate(
        self,
        *,
        authenticated_sanitizer_evidence: evidence_authority.AuthenticatedSanitizerEvidence,
        ordered_table_frames: OrderedTableFrames,
    ) -> AuthenticatedSanitizerSourceSnapshot:
        if type(self) is not SanitizerSourceSnapshotAuthenticator:
            _fail("source_snapshot.configuration_invalid")
        started_at = self._read_clock()
        self._reauthenticate(authenticated_sanitizer_evidence, started_at)
        receipt = self._receipt(authenticated_sanitizer_evidence)
        frames = self._validated_frames(ordered_table_frames)
        self._verify_hmacs(receipt, frames)

        tables: list[tuple[str, tuple[_FrozenMap, ...]]] = []
        proofs: list[tuple[str, tuple[str, ...]]] = []
        for spec, (_name, rows) in zip(self._specs, frames, strict=True):
            if spec.disposition == "required_empty":
                continue
            parsed_rows: list[_FrozenMap] = []
            row_proofs: list[str] = []
            seen_identities: set[tuple[Any, ...]] = set()
            previous_identity: tuple[Any, ...] | None = None
            unique_columns = spec.identity_columns or tuple(
                name for name, _data_type, _required in spec.columns if name == "id"
            )
            for index, (identity, state) in enumerate(rows):
                row = _parse_row(state, spec)
                if identity is not None:
                    _parse_identity(identity, spec, row)
                key = tuple(row[name] for name in unique_columns)
                if unique_columns:
                    if key in seen_identities or (
                        previous_identity is not None and key <= previous_identity
                    ):
                        _fail("source_snapshot.frames_invalid")
                    previous_identity = key
                seen_identities.add(key)
                parsed_rows.append(row)
                row_proofs.append(
                    _frame_proof(self._key, spec.name, index, identity, state)
                )
            tables.append((spec.name, tuple(parsed_rows)))
            proofs.append((spec.name, tuple(row_proofs)))

        completed_at = self._read_clock()
        self._reauthenticate(authenticated_sanitizer_evidence, completed_at)
        return _mint_snapshot(
            key=self._key,
            evidence=authenticated_sanitizer_evidence,
            tables=tuple(tables),
            fragment_proofs=tuple(proofs),
            authenticated_at=completed_at,
        )


class SanitizerSourceSnapshotPreflight:
    """Public evidence-only gate; no row source or key can enter this surface."""

    __slots__ = ("_clock", "_last")

    def __init__(self) -> None:
        _fail("source_snapshot.configuration_invalid")

    def __setattr__(self, name: str, value: object) -> NoReturn:
        del self, name, value
        _fail("source_snapshot.configuration_invalid")

    def __delattr__(self, name: str) -> NoReturn:
        del self, name
        _fail("source_snapshot.configuration_invalid")

    @classmethod
    def _from_reviewed_configuration(
        cls, *, clock: Callable[[], datetime]
    ) -> SanitizerSourceSnapshotPreflight:
        if cls is not SanitizerSourceSnapshotPreflight or not callable(clock):
            del cls, clock
            _fail("source_snapshot.configuration_invalid")
        value = object.__new__(cls)
        object.__setattr__(value, "_clock", clock)
        object.__setattr__(value, "_last", None)
        return value

    def _read_clock(self) -> datetime:
        now = _trusted_utc(self._clock)
        if self._last is not None and now < self._last:
            _fail("source_snapshot.clock_regressed")
        object.__setattr__(self, "_last", now)
        return now

    def preflight(
        self,
        *,
        authenticated_sanitizer_evidence: evidence_authority.AuthenticatedSanitizerEvidence,
    ) -> NoReturn:
        code = None
        try:
            if type(self) is not SanitizerSourceSnapshotPreflight:
                _fail("source_snapshot.configuration_invalid")
            started_at = self._read_clock()
            _validate_retained_evidence(
                authenticated_sanitizer_evidence, now=started_at
            )
            completed_at = self._read_clock()
            _validate_retained_evidence(
                authenticated_sanitizer_evidence, now=completed_at
            )
        except Exception as error:
            code = _bounded_code(error)
        del self, authenticated_sanitizer_evidence
        if code is not None:
            _fail(code)
        _fail(ACTIVATION_FAILURE)
