from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import re
import urllib.parse
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import UTC, date, datetime, timedelta
from types import MappingProxyType
from typing import Any, NoReturn, cast

from typeid import TypeID

try:
    from scripts import (
        staging_restore_sanitizer_evidence_authority as evidence_authority,
    )
    from scripts import staging_restore_sanitizer_source_snapshot as source_snapshot
except ModuleNotFoundError:  # Direct import compatibility for local review.
    import staging_restore_sanitizer_evidence_authority as evidence_authority
    import staging_restore_sanitizer_source_snapshot as source_snapshot


SANITIZER_POLICY_SHA256 = (
    "233476e83f8f45419735052a334ea06aa0153d9b463e599c849c1f212e34dad3"
)
INVENTORY_POLICY_SHA256 = (
    "daaf7fc72c9e44dd5b9ae3467e39bb137697868a2beba848d48681ff0141886b"
)
DATA_CONTRACT_SHA256 = (
    "f1c0c7dce56cd2aa42647578940f807186d8e888b70c66da9aa64aa7402b1360"
)
SOURCE_SNAPSHOT_CONTRACT_SHA256 = (
    "26d6e802fde2f32560e6e12111bf01a457307e36374324061145da69dd6dbe91"
)
HMAC_DOMAIN = "cosmaneura:staging-restore-sanitizer:v1"
MINIMUM_KEY_BYTES = 32
MAXIMUM_KEY_BYTES = 4096
INVENTORY_HMAC_KEY_VERSION = "staging-inventory-hmac-v1"
CANDIDATE_PROOF_DOMAIN = "candidate_dataset.source_snapshot_v1.proof"
CANDIDATE_COPY_DOMAIN = "fixed-catalog:candidate-copy"
CANDIDATE_TRANSACTION_COPY_DOMAIN = "candidate-dataset:transaction-plan-copy"
UUID7_EPOCH_START_MS = 1_577_836_800_000
UUID7_EPOCH_SPAN_MS = 315_576_000_000
ACCESS_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
REWRITE_TABLE_COUNT, REWRITE_COLUMN_COUNT = 19, 204
DISCARD_TABLE_COUNT, DISCARD_COLUMN_COUNT = 10, 139
FOREIGN_KEY_COUNT, DERIVED_RECIPE_COUNT = 46, 4
FIXED_CATALOG_TABLES = frozenset(
    "baa_version doctor_note_schema drug_reference lab_range_set note_type_customization".split()
)
_DOMAIN_RE = re.compile(r"^[a-z0-9][a-z0-9:._-]{0,127}$")
_KEY_VERSION_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_VARCHAR_RE = re.compile(r"^character varying(?:\(([1-9][0-9]*)\))?$")
_ZERO = timedelta(0)
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
_CANDIDATE_SEAL = object()
_AUTHORITY_CLAIMS = MappingProxyType(
    dict.fromkeys(
        "database_connection_authority database_mutation_authority evidence_publication_authority executable live_provider_calls_enabled post_projection_authenticated provider_calls_permitted provider_facts_authenticated runtime_authority sanitizer_authority workflow_integrated".split(),
        False,
    )
)
_SOURCE_SNAPSHOT_CONSUMPTION_CONTRACT = MappingProxyType(
    {
        "kind": "private-exact-immutable-copy-v1",
        "inventory_hmac_key_required": True,
        "proof_recomputed_over_exact_copy": True,
        "exact_evidence_identity_preserved": True,
        "caller_supplied_raw_mappings_permitted": False,
        "thawed_dict_or_list_handoff_permitted": False,
        "keyed_copy_proof_retained": True,
        "copy_proof_domain": "source-snapshot:executor-copy",
        "copy_proof_binds_copy_and_source_identity": True,
        "callbacks_permitted": False,
        "io_permitted": False,
    }
)
_TARGET_BINDING_FIELDS = tuple(
    "rehearsal_id target_fqdn database_name approved_database_role target_postgres_server_id approved_restore_time source_sha candidate_sha workflow_sha run_id run_attempt receipt_sha256 expires_at bound_at".split()
)
_SOURCE_TIME_FIELDS = tuple(
    "inventory_observed_at inventory_expires_at target_binding_expires_at authenticated_at".split()
)
_EVIDENCE_FAILURE_MAP = MappingProxyType(
    {
        "sanitizer_evidence.clock_invalid": "data_contract_executor.clock_invalid",
        "sanitizer_evidence.clock_regressed": "data_contract_executor.clock_regressed",
        "sanitizer_evidence.target_expired": "data_contract_executor.target_expired",
    }
)
_SOURCE_SNAPSHOT_FAILURE_MAP = MappingProxyType(
    {
        "source_snapshot.authority_invalid": "data_contract_executor.authority_invalid",
        "source_snapshot.clock_invalid": "data_contract_executor.clock_invalid",
        "source_snapshot.clock_regressed": "data_contract_executor.clock_regressed",
        "source_snapshot.target_expired": "data_contract_executor.target_expired",
    }
)
_FAILURE_CODES = frozenset(
    {
        "data_contract_executor.authority_invalid",
        "data_contract_executor.candidate_invalid",
        "data_contract_executor.clock_invalid",
        "data_contract_executor.clock_regressed",
        "data_contract_executor.configuration_invalid",
        "data_contract_executor.output_invalid",
        "data_contract_executor.policy_invalid",
        "data_contract_executor.source_integrity_invalid",
        "data_contract_executor.source_invalid",
        "data_contract_executor.target_expired",
        "data_contract_executor.transform_invalid",
    }
)


class SanitizerDataContractExecutorFailure(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class _FrozenMap(Mapping[str, Any]):
    _items: tuple[tuple[str, Any], ...]

    def __getitem__(self, key: str) -> Any:
        return dict(self._items)[key]

    def __iter__(self) -> Any:
        return (name for name, _item in self._items)

    def __len__(self) -> int:
        return len(self._items)


def _fail(code: str) -> NoReturn:
    raise SanitizerDataContractExecutorFailure(code)


def _bounded_code(error: Exception, fallback: str) -> str:
    if (
        type(error) is SanitizerDataContractExecutorFailure
        and len(error.args) == 1
        and type(error.args[0]) is str
        and error.args[0] in _FAILURE_CODES
    ):
        return error.args[0]
    if (
        type(error) is source_snapshot.SanitizerSourceSnapshotFailure
        and len(error.args) == 1
        and type(error.args[0]) is str
    ):
        return _SOURCE_SNAPSHOT_FAILURE_MAP.get(error.args[0], fallback)
    if (
        type(error) is evidence_authority.SanitizerEvidenceFailure
        and len(error.args) == 1
        and type(error.args[0]) is str
    ):
        return _EVIDENCE_FAILURE_MAP.get(error.args[0], fallback)
    return fallback


def _trusted_utc(clock: Callable[[], datetime]) -> datetime:
    try:
        value = clock()
    except Exception:
        _fail("data_contract_executor.clock_invalid")
    if type(value) is not datetime or value.utcoffset() != _ZERO or value.microsecond:
        _fail("data_contract_executor.clock_invalid")
    return value.astimezone(UTC).replace(fold=0)


def _parse_json(raw: bytes, code: str) -> Any:
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
            parse_constant=lambda _value: _fail(code),
        )
    except SanitizerDataContractExecutorFailure:
        raise
    except (UnicodeDecodeError, ValueError, RecursionError):
        _fail(code)
    return value


def _parse_pinned(raw: bytes, digest: str) -> dict[str, Any]:
    if type(raw) is not bytes or not hmac.compare_digest(
        hashlib.sha256(raw).hexdigest(), digest
    ):
        _fail("data_contract_executor.policy_invalid")
    value = _parse_json(raw, "data_contract_executor.policy_invalid")
    if type(value) is not dict:
        _fail("data_contract_executor.policy_invalid")
    return value


def _canonical_document(
    value: Any, code: str = "data_contract_executor.policy_invalid"
) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    except (TypeError, ValueError, UnicodeEncodeError, RecursionError):
        _fail(code)


def _canonical_authority(value: Any) -> bytes:
    return _canonical_document(value, "data_contract_executor.authority_invalid")


def _parse_authority_document(raw: object) -> dict[str, Any]:
    if type(raw) is not bytes or not 0 < len(raw) <= 256 * 1024:
        _fail("data_contract_executor.authority_invalid")
    value = _parse_json(raw, "data_contract_executor.authority_invalid")
    if type(value) is not dict or not hmac.compare_digest(
        raw, _canonical_document(value, "data_contract_executor.authority_invalid")
    ):
        _fail("data_contract_executor.authority_invalid")
    return value


def _utc_string(value: object) -> str:
    if type(value) is not datetime or value.utcoffset() != _ZERO or value.microsecond:
        _fail("data_contract_executor.authority_invalid")
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _has_module_seal(value: object, method_name: str, seal_name: str) -> bool:
    method = type(value).__dict__.get(method_name)
    namespace = getattr(method, "__globals__", {})
    return (
        callable(method)
        and isinstance(namespace, dict)
        and namespace.get(seal_name) is getattr(value, "_seal", None)
    )


def _retained_tables(policy: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    try:
        profile = policy["profiles"]["pre"]
        excluded = set(profile["excluded_tables"])
        exclusions = profile["column_exclusions"]
        result: dict[str, dict[str, Any]] = {}
        for table_name, spec in policy["database"]["tables"].items():
            if table_name in excluded:
                continue
            table = dict(spec)
            removed = set(exclusions.get(table_name, []))
            table["columns"] = [
                column for column in spec["columns"] if column[0] not in removed
            ]
            if table["disposition"] != "required_empty":
                result[table_name] = table
        return result
    except (KeyError, TypeError, ValueError):
        _fail("data_contract_executor.policy_invalid")


def _hmac_digest(key: bytes, semantic_domain: str, value: bytes) -> bytes:
    if type(key) is not bytes or len(key) < MINIMUM_KEY_BYTES:
        _fail("data_contract_executor.configuration_invalid")
    if _DOMAIN_RE.fullmatch(semantic_domain) is None:
        _fail("data_contract_executor.policy_invalid")
    domain = f"{HMAC_DOMAIN}:{semantic_domain}".encode()
    framed = (
        len(domain).to_bytes(2, "big") + domain + len(value).to_bytes(8, "big") + value
    )
    return hmac.new(key, framed, hashlib.sha256).digest()


def _pseudonymize_uuid7(value: uuid.UUID, *, key: bytes, domain: str) -> uuid.UUID:
    if not isinstance(value, uuid.UUID):
        _fail("data_contract_executor.transform_invalid")
    digest = _hmac_digest(key, domain, b"uuid:" + value.bytes)
    timestamp = (
        UUID7_EPOCH_START_MS + int.from_bytes(digest[:6], "big") % UUID7_EPOCH_SPAN_MS
    )
    output = bytearray(timestamp.to_bytes(6, "big") + digest[6:16])
    output[6] = (output[6] & 0x0F) | 0x70
    output[8] = (output[8] & 0x3F) | 0x80
    return uuid.UUID(bytes=bytes(output))


def _pseudonymize_text(
    value: str,
    *,
    key: bytes,
    domain: str,
    prefix: str = "",
    maximum_length: int = 64,
) -> str:
    if type(value) is not str or not value or maximum_length < len(prefix) + 8:
        _fail("data_contract_executor.transform_invalid")
    token = (
        base64.b32encode(_hmac_digest(key, domain, b"text:" + value.encode("utf-8")))
        .decode("ascii")
        .lower()
        .rstrip("=")
    )
    return (prefix + token)[:maximum_length]


def _pseudonymize_email(value: str, *, key: bytes) -> str:
    normalized = value.strip().casefold()
    if (
        value != normalized
        or normalized.count("@") != 1
        or any(character.isspace() for character in normalized)
    ):
        _fail("data_contract_executor.transform_invalid")
    local = _pseudonymize_text(
        normalized,
        key=key,
        domain="verified_email",
        prefix="u_",
        maximum_length=50,
    )
    return f"{local}@example.invalid"


def _pseudonymize_domain(value: str, *, key: bytes) -> str:
    normalized = value.strip().lower().lstrip("@")
    if (
        value != normalized
        or not normalized
        or "." not in normalized
        or any(character.isspace() for character in normalized)
    ):
        _fail("data_contract_executor.transform_invalid")
    token = _pseudonymize_text(
        normalized,
        key=key,
        domain="organization_email_domain",
        maximum_length=32,
    )
    return f"org-{token}.example.invalid"


def _pseudonymize_issuer(value: str, *, key: bytes) -> str:
    try:
        parsed = urllib.parse.urlsplit(value)
    except ValueError:
        _fail("data_contract_executor.transform_invalid")
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or value.endswith("/")
    ):
        _fail("data_contract_executor.transform_invalid")
    token = _pseudonymize_text(value, key=key, domain="clerk_issuer", maximum_length=32)
    return f"https://id-{token}.identity.invalid"


def _provider_access_credentials(
    organization_id: uuid.UUID, *, key: bytes
) -> dict[str, str]:
    if not isinstance(organization_id, uuid.UUID):
        _fail("data_contract_executor.transform_invalid")
    source = b"uuid:" + organization_id.bytes
    code_material = _hmac_digest(key, "provider_access_code.code", source)
    code = "".join(
        ACCESS_CODE_ALPHABET[byte % len(ACCESS_CODE_ALPHABET)]
        for byte in code_material[:12]
    )
    salt = _hmac_digest(key, "provider_access_code.salt", source)[:16]
    digest = hashlib.scrypt(
        code.encode("ascii"), salt=salt, n=2**14, r=8, p=1, dklen=32
    )
    return {"access_code_salt": salt.hex(), "access_code_digest": digest.hex()}


def _pack(value: Any) -> Any:
    if value is None or type(value) in {bool, int, str}:
        return value
    if type(value) is float and math.isfinite(value):
        return {"$float": value.hex()}
    if type(value) is uuid.UUID:
        return {"$uuid": str(value)}
    if type(value) is datetime:
        zone = value.tzinfo
        return {"$datetime": [value.isoformat(), value.fold, id(type(zone)), id(zone)]}
    if type(value) is date:
        return {"$date": value.isoformat()}
    if isinstance(value, Mapping):
        if not all(type(name) is str for name in value):
            _fail("data_contract_executor.output_invalid")
        return {"$map": [[name, _pack(value[name])] for name in value]}
    if type(value) is tuple:
        return {"$tuple": [_pack(item) for item in value]}
    _fail("data_contract_executor.output_invalid")


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(_pack(value), sort_keys=True, separators=(",", ":")).encode()
    except (TypeError, ValueError, UnicodeEncodeError, RecursionError):
        _fail("data_contract_executor.output_invalid")


def _freeze_json(value: Any) -> Any:
    if value is None or type(value) in {bool, int, str}:
        return value
    if type(value) is float and math.isfinite(value):
        return value
    if type(value) is list:
        return tuple(_freeze_json(item) for item in value)
    if type(value) is dict and all(type(name) is str for name in value):
        return _FrozenMap(tuple((name, _freeze_json(value[name])) for name in value))
    _fail("data_contract_executor.source_invalid")


def _copy_snapshot_json(value: Any) -> Any:
    kind = type(value)
    if value is None or kind in {bool, int, str}:
        return value
    if kind is float and math.isfinite(cast(float, value)):
        return value
    if kind is tuple:
        return tuple(_copy_snapshot_json(item) for item in value)
    if kind is source_snapshot._FrozenMap:  # noqa: SLF001
        return _FrozenMap(
            tuple(
                (name, _copy_snapshot_json(item))
                for name, item in value._items  # noqa: SLF001
            )
        )
    _fail("data_contract_executor.source_invalid")


def _copy_candidate_value(value: Any) -> Any:
    kind = type(value)
    if kind is _FrozenMap:
        items = value._items
        if type(items) is not tuple or any(
            type(item) is not tuple or len(item) != 2 or type(item[0]) is not str
            for item in items
        ):
            _fail("data_contract_executor.candidate_invalid")
        return _FrozenMap(
            tuple((name, _copy_candidate_value(item)) for name, item in items)
        )
    if kind is tuple:
        return tuple(_copy_candidate_value(item) for item in value)
    if kind is uuid.UUID:
        return uuid.UUID(int=value.int)
    if kind is datetime:
        return datetime(
            value.year,
            value.month,
            value.day,
            value.hour,
            value.minute,
            value.second,
            value.microsecond,
            tzinfo=value.tzinfo,
            fold=value.fold,
        )
    if kind is date:
        return date(value.year, value.month, value.day)
    if value is None or kind in {bool, int, str}:
        return value
    if kind is float and math.isfinite(cast(float, value)):
        return value
    _fail("data_contract_executor.candidate_invalid")


def _is_frozen(value: Any) -> bool:
    kind = type(value)
    if value is None or kind in {bool, int, float, str, uuid.UUID, date}:
        return kind is not float or math.isfinite(cast(float, value))
    if kind is datetime:
        return value.fold == 0 and (value.tzinfo is None or value.tzinfo is UTC)
    if kind is _FrozenMap:
        if type(value._items) is not tuple or any(
            type(item) is not tuple or len(item) != 2 or type(item[0]) is not str
            for item in value._items
        ):
            return False
        names = tuple(item[0] for item in value._items)
        return len(names) == len(set(names)) and all(
            _is_frozen(item[1]) for item in value._items
        )
    return kind is tuple and all(_is_frozen(item) for item in value)


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {name: _thaw(item) for name, item in value.items()}
    if type(value) is tuple:
        return [_thaw(item) for item in value]
    return value


def _maximum_length(data_type: str) -> int | None:
    match = _VARCHAR_RE.fullmatch(data_type)
    return int(match.group(1)) if match is not None and match.group(1) else None


def _validate_and_freeze(value: Any, data_type: str, required: bool) -> Any:
    if value is None:
        if required:
            _fail("data_contract_executor.source_invalid")
        return None
    maximum = _maximum_length(data_type)
    if data_type == "uuid" and type(value) is uuid.UUID:
        return value
    if data_type in {"integer", "bigint"} and type(value) is int:
        lower, upper = (
            (-(2**31), 2**31 - 1) if data_type == "integer" else (-(2**63), 2**63 - 1)
        )
        if lower <= value <= upper:
            return value
        _fail("data_contract_executor.source_invalid")
    if data_type == "boolean" and type(value) is bool:
        return value
    if data_type == "double precision" and type(value) is float:
        if math.isfinite(value):
            return value
    if data_type == "date" and type(value) is date:
        return value
    if data_type == "timestamp with time zone" and (
        type(value) is datetime and value.tzinfo is UTC and value.fold == 0
    ):
        return value
    if data_type == "timestamp without time zone" and (
        type(value) is datetime and value.tzinfo is None and value.fold == 0
    ):
        return value
    if data_type in {"json", "jsonb"}:
        return _copy_snapshot_json(value)
    if (
        data_type == "text" or maximum is not None or data_type == "character varying"
    ) and type(value) is str:
        if maximum is not None and len(value) > maximum:
            _fail("data_contract_executor.source_invalid")
        return value
    if data_type in _ENUM_VALUES and type(value) is str:
        if value in _ENUM_VALUES[data_type]:
            return value
        _fail("data_contract_executor.source_invalid")
    _fail("data_contract_executor.source_invalid")


def _identity_columns(spec: Mapping[str, Any]) -> tuple[str, ...]:
    configured = spec.get("identity_columns")
    if isinstance(configured, (list, tuple)) and configured:
        return tuple(configured)
    return ("id",) if any(column[0] == "id" for column in spec["columns"]) else ()


def _validate_unique(
    tables: Mapping[str, tuple[Mapping[str, Any], ...]],
    schemas: Mapping[str, Mapping[str, Any]],
) -> None:
    for table_name, rows in tables.items():
        columns = _identity_columns(schemas[table_name])
        if not columns:
            continue
        seen: set[tuple[Any, ...]] = set()
        for row in rows:
            identity = tuple(row[name] for name in columns)
            if identity in seen:
                _fail("data_contract_executor.source_integrity_invalid")
            seen.add(identity)


def _split_reference(reference: str) -> tuple[str, str]:
    try:
        table_name, column_name = reference.split(".", 1)
    except (AttributeError, ValueError):
        _fail("data_contract_executor.policy_invalid")
    return table_name, column_name


def _validate_foreign_keys(
    tables: Mapping[str, tuple[Mapping[str, Any], ...]],
    bindings: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...],
    skipped_sources: frozenset[str] = frozenset(),
) -> None:
    for source, target in bindings:
        source_parts = tuple(_split_reference(item) for item in source)
        target_parts = tuple(_split_reference(item) for item in target)
        source_table = source_parts[0][0]
        target_table = target_parts[0][0]
        if source_table in skipped_sources:
            continue
        target_keys = {
            tuple(row[column] for _table, column in target_parts)
            for row in tables[target_table]
        }
        for row in tables[source_table]:
            key = tuple(row[column] for _table, column in source_parts)
            if all(item is not None for item in key) and key not in target_keys:
                _fail("data_contract_executor.source_integrity_invalid")


def _join_code(transformed_id: uuid.UUID, key: bytes) -> str:
    material = _hmac_digest(
        key, "practice_join_code.code", b"uuid:" + transformed_id.bytes
    )
    alphabet = ACCESS_CODE_ALPHABET
    return "".join(alphabet[byte % len(alphabet)] for byte in material[:12])


@dataclass(frozen=True, slots=True, init=False, repr=False)
class SanitizerCandidateDataset:
    table_count: int
    rewrite_table_count: int
    discard_table_count: int
    source_row_count: int
    candidate_row_count: int
    transform_key_version: str
    transformed_at: datetime
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
    _tables: tuple[tuple[str, tuple[Mapping[str, Any], ...]], ...] = dataclass_field(
        repr=False
    )
    _authenticated_evidence: evidence_authority.AuthenticatedSanitizerEvidence = (
        dataclass_field(repr=False)
    )
    _source_snapshot_contract_sha256: str = dataclass_field(repr=False)
    _source_snapshot_proof_sha256: str = dataclass_field(repr=False)
    _source_snapshot_identity: int = dataclass_field(repr=False)
    _source_snapshot_type_identity: int = dataclass_field(repr=False)
    _source_snapshot_seal_identity: int = dataclass_field(repr=False)
    _contract_sha256: str = dataclass_field(repr=False)
    _proof_sha256: str = dataclass_field(repr=False)
    _seal: object = dataclass_field(repr=False, compare=False)

    def __init__(self) -> None:
        _fail("data_contract_executor.output_invalid")

    def __repr__(self) -> str:
        return "<SanitizerCandidateDataset sealed non-authoritative>"

    def __reduce__(self) -> NoReturn:
        del self
        _fail("data_contract_executor.output_invalid")

    def __reduce_ex__(self, protocol: object) -> NoReturn:
        del self, protocol
        _fail("data_contract_executor.output_invalid")

    def _rows_for_table(self, table_name: str) -> tuple[dict[str, Any], ...]:
        if type(table_name) is not str or self._seal is not _CANDIDATE_SEAL:
            del self, table_name
            _fail("data_contract_executor.output_invalid")
        try:
            rows = dict(self._tables)[table_name]
        except KeyError:
            del self, table_name
            _fail("data_contract_executor.output_invalid")
        return tuple({name: _thaw(item) for name, item in row.items()} for row in rows)


@dataclass(frozen=True, slots=True, init=False, repr=False)
class _VerifiedFixedCatalogCandidateCopy:
    _tables: tuple[tuple[str, tuple[_FrozenMap, ...]], ...] = dataclass_field(
        repr=False
    )
    _authenticated_evidence: evidence_authority.AuthenticatedSanitizerEvidence = (
        dataclass_field(repr=False)
    )
    _candidate_identity: int = dataclass_field(repr=False)
    _candidate_type_identity: int = dataclass_field(repr=False)
    _candidate_seal_identity: int = dataclass_field(repr=False)
    _candidate_proof_sha256: str = dataclass_field(repr=False)
    _candidate_contract_sha256: str = dataclass_field(repr=False)
    _source_snapshot_identity: int = dataclass_field(repr=False)
    _source_snapshot_type_identity: int = dataclass_field(repr=False)
    _source_snapshot_seal_identity: int = dataclass_field(repr=False)
    _source_snapshot_proof_sha256: str = dataclass_field(repr=False)
    _source_snapshot_contract_sha256: str = dataclass_field(repr=False)
    _transform_key_version: str = dataclass_field(repr=False)
    _transformed_at: datetime = dataclass_field(repr=False)
    _proof_sha256: str = dataclass_field(repr=False)
    _seal: object = dataclass_field(repr=False, compare=False)

    def __init__(self) -> None:
        _fail("data_contract_executor.output_invalid")

    def __repr__(self) -> str:
        return "<_VerifiedFixedCatalogCandidateCopy sealed sensitive>"

    def __reduce__(self) -> NoReturn:
        del self
        _fail("data_contract_executor.output_invalid")

    def __reduce_ex__(self, protocol: object) -> NoReturn:
        del self, protocol
        _fail("data_contract_executor.output_invalid")


_FIXED_CATALOG_COPY_SEAL = object()


@dataclass(frozen=True, slots=True, init=False, repr=False)
class _VerifiedCandidateTransactionCopy:
    _tables: tuple[tuple[str, tuple[_FrozenMap, ...]], ...] = dataclass_field(
        repr=False
    )
    _authenticated_evidence: evidence_authority.AuthenticatedSanitizerEvidence = (
        dataclass_field(repr=False)
    )
    _candidate_identity: int = dataclass_field(repr=False)
    _candidate_type_identity: int = dataclass_field(repr=False)
    _candidate_seal_identity: int = dataclass_field(repr=False)
    _candidate_proof_sha256: str = dataclass_field(repr=False)
    _candidate_contract_sha256: str = dataclass_field(repr=False)
    _source_snapshot_identity: int = dataclass_field(repr=False)
    _source_snapshot_type_identity: int = dataclass_field(repr=False)
    _source_snapshot_seal_identity: int = dataclass_field(repr=False)
    _source_snapshot_proof_sha256: str = dataclass_field(repr=False)
    _source_snapshot_contract_sha256: str = dataclass_field(repr=False)
    _source_row_count: int = dataclass_field(repr=False)
    _candidate_row_count: int = dataclass_field(repr=False)
    _transform_key_version: str = dataclass_field(repr=False)
    _transformed_at: datetime = dataclass_field(repr=False)
    _proof_sha256: str = dataclass_field(repr=False)
    _seal: object = dataclass_field(repr=False, compare=False)

    def __init__(self) -> None:
        _fail("data_contract_executor.output_invalid")

    def __repr__(self) -> str:
        return "<_VerifiedCandidateTransactionCopy sealed sensitive>"

    def __reduce__(self) -> NoReturn:
        del self
        _fail("data_contract_executor.output_invalid")

    def __reduce_ex__(self, protocol: object) -> NoReturn:
        del self, protocol
        _fail("data_contract_executor.output_invalid")


_CANDIDATE_TRANSACTION_COPY_SEAL = object()


def _fixed_catalog_copy_proof(
    value: _VerifiedFixedCatalogCandidateCopy, key: bytes
) -> str:
    material = _canonical(
        (
            ("copy_identity", id(value)),
            ("candidate_identity", value._candidate_identity),
            ("candidate_type_identity", value._candidate_type_identity),
            ("candidate_seal_identity", value._candidate_seal_identity),
            ("candidate_proof_sha256", value._candidate_proof_sha256),
            ("candidate_contract_sha256", value._candidate_contract_sha256),
            ("source_snapshot_identity", value._source_snapshot_identity),
            ("source_snapshot_type_identity", value._source_snapshot_type_identity),
            ("source_snapshot_seal_identity", value._source_snapshot_seal_identity),
            ("source_snapshot_proof_sha256", value._source_snapshot_proof_sha256),
            (
                "source_snapshot_contract_sha256",
                value._source_snapshot_contract_sha256,
            ),
            ("evidence_identity", id(value._authenticated_evidence)),
            ("evidence_type_identity", id(type(value._authenticated_evidence))),
            (
                "evidence_seal_identity",
                id(value._authenticated_evidence._seal),  # noqa: SLF001
            ),
            (
                "evidence_proof_sha256",
                value._authenticated_evidence._proof_sha256,  # noqa: SLF001
            ),
            ("transform_key_version", value._transform_key_version),
            ("transformed_at", value._transformed_at),
            ("tables", value._tables),
        )
    )
    return _hmac_digest(key, CANDIDATE_COPY_DOMAIN, material).hex()


def _candidate_transaction_copy_proof(
    value: _VerifiedCandidateTransactionCopy, key: bytes
) -> str:
    material = _canonical(
        (
            ("copy_identity", id(value)),
            ("candidate_identity", value._candidate_identity),
            ("candidate_type_identity", value._candidate_type_identity),
            ("candidate_seal_identity", value._candidate_seal_identity),
            ("candidate_proof_sha256", value._candidate_proof_sha256),
            ("candidate_contract_sha256", value._candidate_contract_sha256),
            ("source_snapshot_identity", value._source_snapshot_identity),
            ("source_snapshot_type_identity", value._source_snapshot_type_identity),
            ("source_snapshot_seal_identity", value._source_snapshot_seal_identity),
            ("source_snapshot_proof_sha256", value._source_snapshot_proof_sha256),
            (
                "source_snapshot_contract_sha256",
                value._source_snapshot_contract_sha256,
            ),
            ("evidence_identity", id(value._authenticated_evidence)),
            ("evidence_type_identity", id(type(value._authenticated_evidence))),
            (
                "evidence_seal_identity",
                id(value._authenticated_evidence._seal),  # noqa: SLF001
            ),
            (
                "evidence_proof_sha256",
                value._authenticated_evidence._proof_sha256,  # noqa: SLF001
            ),
            ("source_row_count", value._source_row_count),
            ("candidate_row_count", value._candidate_row_count),
            ("transform_key_version", value._transform_key_version),
            ("transformed_at", value._transformed_at),
            ("tables", value._tables),
        )
    )
    return _hmac_digest(key, CANDIDATE_TRANSACTION_COPY_DOMAIN, material).hex()


def _candidate_proof(
    *,
    key: bytes,
    tables: tuple[tuple[str, tuple[Mapping[str, Any], ...]], ...],
    evidence: evidence_authority.AuthenticatedSanitizerEvidence,
    source_snapshot_contract_sha256: str,
    source_snapshot_proof_sha256: str,
    source_snapshot_identity: int,
    source_snapshot_type_identity: int,
    source_snapshot_seal_identity: int,
    source_row_count: int,
    transformed_at: datetime,
    transform_key_version: str,
) -> str:
    material = _canonical(
        {
            "contract_sha256": DATA_CONTRACT_SHA256,
            "source_snapshot": {
                "contract_sha256": source_snapshot_contract_sha256,
                "proof_sha256": source_snapshot_proof_sha256,
                "identity": source_snapshot_identity,
                "type_identity": source_snapshot_type_identity,
                "seal_identity": source_snapshot_seal_identity,
                "evidence": {
                    "binding_sha256": evidence.evidence_binding_sha256,
                    "envelope_proof_sha256": evidence._proof_sha256,  # noqa: SLF001
                    "identity": id(evidence),
                    "type_identity": id(type(evidence)),
                    "seal_identity": id(evidence._seal),  # noqa: SLF001
                },
            },
            "source_row_count": source_row_count,
            "tables": tables,
            "transform_key_version": transform_key_version,
            "transformed_at": transformed_at,
        }
    )
    return _hmac_digest(key, CANDIDATE_PROOF_DOMAIN, material).hex()


def _mint_candidate(
    *,
    key: bytes,
    key_version: str,
    verified_source_snapshot: source_snapshot._VerifiedSourceSnapshotCopy,  # noqa: SLF001
    tables: tuple[tuple[str, tuple[Mapping[str, Any], ...]], ...],
    transformed_at: datetime,
    source_row_count: int,
) -> SanitizerCandidateDataset:
    evidence = verified_source_snapshot._authenticated_evidence  # noqa: SLF001
    value = object.__new__(SanitizerCandidateDataset)
    public = {
        "table_count": len(tables),
        "rewrite_table_count": REWRITE_TABLE_COUNT,
        "discard_table_count": DISCARD_TABLE_COUNT,
        "source_row_count": source_row_count,
        "candidate_row_count": sum(len(rows) for _table, rows in tables),
        "transform_key_version": key_version,
        "transformed_at": transformed_at,
        **_AUTHORITY_CLAIMS,
    }
    for name, item in public.items():
        object.__setattr__(value, name, item)
    object.__setattr__(value, "_tables", tables)
    object.__setattr__(value, "_authenticated_evidence", evidence)
    object.__setattr__(
        value,
        "_source_snapshot_contract_sha256",
        verified_source_snapshot._source_contract_sha256,  # noqa: SLF001
    )
    object.__setattr__(
        value,
        "_source_snapshot_proof_sha256",
        verified_source_snapshot._source_snapshot_proof_sha256,  # noqa: SLF001
    )
    object.__setattr__(
        value,
        "_source_snapshot_identity",
        verified_source_snapshot._source_snapshot_identity,  # noqa: SLF001
    )
    object.__setattr__(
        value,
        "_source_snapshot_type_identity",
        id(source_snapshot.AuthenticatedSanitizerSourceSnapshot),
    )
    object.__setattr__(
        value,
        "_source_snapshot_seal_identity",
        id(source_snapshot._SNAPSHOT_SEAL),  # noqa: SLF001
    )
    object.__setattr__(value, "_contract_sha256", DATA_CONTRACT_SHA256)
    object.__setattr__(
        value,
        "_proof_sha256",
        _candidate_proof(
            key=key,
            tables=tables,
            evidence=evidence,
            source_snapshot_contract_sha256=value._source_snapshot_contract_sha256,
            source_snapshot_proof_sha256=value._source_snapshot_proof_sha256,
            source_snapshot_identity=value._source_snapshot_identity,
            source_snapshot_type_identity=value._source_snapshot_type_identity,
            source_snapshot_seal_identity=value._source_snapshot_seal_identity,
            source_row_count=source_row_count,
            transformed_at=transformed_at,
            transform_key_version=key_version,
        ),
    )
    object.__setattr__(value, "_seal", _CANDIDATE_SEAL)
    return value


class SanitizerDataContractExecutor:
    __slots__ = (
        "_clock",
        "_contract",
        "_discarded",
        "_foreign_keys",
        "_inventory_hmac_key",
        "_inventory_hmac_key_version",
        "_inventory_policy",
        "_inventory_policy_bytes",
        "_key",
        "_key_version",
        "_last",
        "_rewritten",
        "_sanitizer_policy",
        "_sanitizer_policy_bytes",
        "_schemas",
        "_source_snapshot_contract",
    )

    def __init__(self) -> None:
        _fail("data_contract_executor.configuration_invalid")

    def __setattr__(self, name: str, value: object) -> NoReturn:
        del name, value
        _fail("data_contract_executor.configuration_invalid")

    def __delattr__(self, name: str) -> NoReturn:
        del name
        _fail("data_contract_executor.configuration_invalid")

    def __repr__(self) -> str:
        return "<SanitizerDataContractExecutor dormant in-memory-only>"

    def __reduce__(self) -> NoReturn:
        del self
        _fail("data_contract_executor.configuration_invalid")

    def __reduce_ex__(self, protocol: object) -> NoReturn:
        del self, protocol
        _fail("data_contract_executor.configuration_invalid")

    @classmethod
    def _from_reviewed_configuration(
        cls,
        *,
        clock: Callable[[], datetime],
        transform_key: bytes,
        transform_key_version: str,
        inventory_hmac_key: bytes,
        inventory_hmac_key_version: str,
        sanitizer_policy_bytes: bytes,
        inventory_policy_bytes: bytes,
        data_contract_bytes: bytes,
        source_snapshot_contract_bytes: bytes,
    ) -> SanitizerDataContractExecutor:
        code = "data_contract_executor.configuration_invalid"
        if cls is SanitizerDataContractExecutor:
            try:
                return SanitizerDataContractExecutor._build_from_reviewed_configuration(
                    clock=clock,
                    transform_key=transform_key,
                    transform_key_version=transform_key_version,
                    inventory_hmac_key=inventory_hmac_key,
                    inventory_hmac_key_version=inventory_hmac_key_version,
                    sanitizer_policy_bytes=sanitizer_policy_bytes,
                    inventory_policy_bytes=inventory_policy_bytes,
                    data_contract_bytes=data_contract_bytes,
                    source_snapshot_contract_bytes=source_snapshot_contract_bytes,
                )
            except Exception as error:
                code = _bounded_code(
                    error, "data_contract_executor.configuration_invalid"
                )
        del cls, clock, transform_key, transform_key_version
        del inventory_hmac_key, inventory_hmac_key_version
        del sanitizer_policy_bytes, inventory_policy_bytes, data_contract_bytes
        del source_snapshot_contract_bytes
        _fail(code)

    @classmethod
    def _build_from_reviewed_configuration(
        cls,
        *,
        clock: Callable[[], datetime],
        transform_key: bytes,
        transform_key_version: str,
        inventory_hmac_key: bytes,
        inventory_hmac_key_version: str,
        sanitizer_policy_bytes: bytes,
        inventory_policy_bytes: bytes,
        data_contract_bytes: bytes,
        source_snapshot_contract_bytes: bytes,
    ) -> SanitizerDataContractExecutor:
        if (
            cls is not SanitizerDataContractExecutor
            or not callable(clock)
            or type(transform_key) is not bytes
            or not MINIMUM_KEY_BYTES <= len(transform_key) <= MAXIMUM_KEY_BYTES
            or type(transform_key_version) is not str
            or _KEY_VERSION_RE.fullmatch(transform_key_version) is None
            or type(inventory_hmac_key) is not bytes
            or not MINIMUM_KEY_BYTES <= len(inventory_hmac_key) <= MAXIMUM_KEY_BYTES
            or type(inventory_hmac_key_version) is not str
            or inventory_hmac_key_version != INVENTORY_HMAC_KEY_VERSION
        ):
            _fail("data_contract_executor.configuration_invalid")
        policy = _parse_pinned(sanitizer_policy_bytes, SANITIZER_POLICY_SHA256)
        inventory_policy = _parse_pinned(
            inventory_policy_bytes, INVENTORY_POLICY_SHA256
        )
        contract = _parse_pinned(data_contract_bytes, DATA_CONTRACT_SHA256)
        snapshot_contract = _parse_pinned(
            source_snapshot_contract_bytes,
            SOURCE_SNAPSHOT_CONTRACT_SHA256,
        )
        try:
            schemas = _retained_tables(inventory_policy)
            rewritten = frozenset(contract["row_actions"]["rewrite_rows"])
            discarded = frozenset(contract["row_actions"]["discard_rows"])
            bindings = tuple(
                (tuple(item["source"]), tuple(item["target"]))
                for item in contract["foreign_keys"]
            )
            rewrite_columns = sum(len(schemas[name]["columns"]) for name in rewritten)
            discard_columns = sum(len(schemas[name]["columns"]) for name in discarded)
        except Exception:
            _fail("data_contract_executor.policy_invalid")
        if (
            policy.get("policy_id")
            != "cosmaneura-isolated-restore-sanitizer-contract-v1"
            or policy.get("closed_world") is not True
            or policy.get("inventory", {}).get("policy_sha256")
            != INVENTORY_POLICY_SHA256
            or policy.get("transform_contract", {}).get("data_contract_sha256")
            != DATA_CONTRACT_SHA256
            or policy.get("execution_contract", {}).get("implemented") is not False
            or policy.get("execution_contract", {}).get(
                "database_connections_permitted"
            )
            is not False
            or policy.get("execution_contract", {}).get("sql_mutation_permitted")
            is not False
            or snapshot_contract.get("policy_id")
            != "cosmaneura-staging-sanitizer-source-snapshot-v1"
            or snapshot_contract.get("closed_world") is not True
            or source_snapshot.SOURCE_SNAPSHOT_CONTRACT_SHA256
            != SOURCE_SNAPSHOT_CONTRACT_SHA256
            or snapshot_contract.get("status", {}).get(
                "executor_consumption_implemented"
            )
            is not True
            or snapshot_contract.get("executor_consumption")
            != _SOURCE_SNAPSHOT_CONSUMPTION_CONTRACT
            or snapshot_contract.get("inventory", {}).get("hmac_key_version")
            != INVENTORY_HMAC_KEY_VERSION
            or any(
                snapshot_contract.get("claims", {}).get(name) is not False
                for name in _AUTHORITY_CLAIMS
            )
            or contract.get("retained_table_count") != len(schemas)
            or contract.get("retained_column_count")
            != sum(len(spec["columns"]) for spec in schemas.values())
            or set(contract.get("tables", {})) != set(schemas)
            or len(rewritten) != REWRITE_TABLE_COUNT
            or rewrite_columns != REWRITE_COLUMN_COUNT
            or len(discarded) != DISCARD_TABLE_COUNT
            or discard_columns != DISCARD_COLUMN_COUNT
            or len(bindings) != FOREIGN_KEY_COUNT
            or len(contract["derived_values"]) != DERIVED_RECIPE_COUNT
        ):
            _fail("data_contract_executor.policy_invalid")
        value = object.__new__(cls)
        object.__setattr__(value, "_clock", clock)
        object.__setattr__(value, "_last", None)
        object.__setattr__(value, "_key", transform_key)
        object.__setattr__(value, "_key_version", transform_key_version)
        object.__setattr__(value, "_inventory_hmac_key", inventory_hmac_key)
        object.__setattr__(
            value, "_inventory_hmac_key_version", inventory_hmac_key_version
        )
        object.__setattr__(
            value, "_sanitizer_policy_bytes", _canonical_document(policy)
        )
        object.__setattr__(value, "_inventory_policy_bytes", inventory_policy_bytes)
        object.__setattr__(value, "_sanitizer_policy", _freeze_json(policy))
        object.__setattr__(value, "_inventory_policy", _freeze_json(inventory_policy))
        object.__setattr__(value, "_contract", _freeze_json(contract))
        object.__setattr__(
            value, "_source_snapshot_contract", _freeze_json(snapshot_contract)
        )
        object.__setattr__(value, "_schemas", _freeze_json(schemas))
        object.__setattr__(value, "_rewritten", rewritten)
        object.__setattr__(value, "_discarded", discarded)
        object.__setattr__(value, "_foreign_keys", bindings)
        return value

    def _read_clock(self) -> datetime:
        now = _trusted_utc(self._clock)
        if self._last is not None and now < self._last:
            _fail("data_contract_executor.clock_regressed")
        object.__setattr__(self, "_last", now)
        return now

    def _reauthenticate(
        self,
        evidence: evidence_authority.AuthenticatedSanitizerEvidence,
        now: datetime,
    ) -> None:
        try:
            evidence_authority._validate_retained_envelope_at_current_time(  # noqa: SLF001
                evidence, now=now
            )
            if evidence._sanitizer_policy_bytes != self._sanitizer_policy_bytes:  # noqa: SLF001
                _fail("data_contract_executor.authority_invalid")
            source = evidence._fixed_catalog_source_state  # noqa: SLF001
            if (
                type(source) is not evidence._fixed_catalog_source_state_type  # noqa: SLF001
                or source._seal  # noqa: SLF001
                is not evidence._fixed_catalog_source_state_seal  # noqa: SLF001
                or not _has_module_seal(
                    source, "_reauthenticate_at_current_time", "_SOURCE_STATE_SEAL"
                )
                or source._sanitizer_policy_bytes  # noqa: SLF001
                != self._sanitizer_policy_bytes
                or source._inventory_policy_bytes  # noqa: SLF001
                != self._inventory_policy_bytes
                or _utc_string(source.authenticated_at) > _utc_string(now)
            ):
                _fail("data_contract_executor.authority_invalid")

            target = source._target_binding  # noqa: SLF001
            written = target._written_receipt  # noqa: SLF001
            built = written._built_receipt  # noqa: SLF001
            retained_until = datetime.strptime(
                written.retained_until, "%Y-%m-%dT%H:%M:%SZ"
            ).replace(tzinfo=UTC)
            if (
                not _has_module_seal(target, "_is_bound", "_TARGET_BINDING_SEAL")
                or target._policy_bytes != self._sanitizer_policy_bytes  # noqa: SLF001
                or not (
                    written.receipt_sha256
                    == target.receipt_sha256
                    == built.receipt_sha256
                )
                or _utc_string(target.bound_at) > _utc_string(source.authenticated_at)
                or retained_until <= now
            ):
                _fail("data_contract_executor.authority_invalid")
            if now >= min(target.expires_at, built.cleanup_not_after):
                _fail("data_contract_executor.target_expired")
            target_projection = {
                name: getattr(target, name) for name in _TARGET_BINDING_FIELDS
            }
            target_serializable = dict(target_projection)
            for name in ("approved_restore_time", "expires_at", "bound_at"):
                target_serializable[name] = _utc_string(target_serializable[name])
            if any(
                getattr(target, name) is not value
                for name, value in self._sanitizer_policy[
                    "controller_receipt_v2_binding"
                ]["claims"].items()
            ) or not hmac.compare_digest(
                target._proof_sha256,  # noqa: SLF001
                hashlib.sha256(_canonical_authority(target_serializable)).hexdigest(),
            ):
                _fail("data_contract_executor.authority_invalid")

            inventory_receipt = _parse_authority_document(
                source._inventory_receipt_bytes  # noqa: SLF001
            )
            unsigned_inventory_receipt = dict(inventory_receipt)
            receipt_sha256 = unsigned_inventory_receipt.pop("receipt_sha256")
            calculated_receipt_sha256 = hashlib.sha256(
                _canonical_authority(unsigned_inventory_receipt)
            ).hexdigest()
            fixed_tables = tuple(
                self._sanitizer_policy["transform_contract"]["fixed_catalog"]["tables"]
            )
            counts = inventory_receipt["counts"]
            state_hmacs = inventory_receipt["state_hmacs"]
            fixed_catalog = tuple(
                (name, counts[f"database.{name}"], state_hmacs[name])
                for name in fixed_tables
            )
            fixed_projection = (
                "sha256:"
                + hashlib.sha256(
                    _canonical_authority(
                        [
                            {"table": name, "count": count, "state_hmac": state_hmac}
                            for name, count, state_hmac in fixed_catalog
                        ]
                    )
                ).hexdigest()
            )
            source_fields = tuple(
                self._sanitizer_policy["fixed_catalog_source_state_binding"]["output"][
                    "public_fields"
                ]
            )
            source_public = {name: getattr(source, name) for name in source_fields}
            source_serializable = dict(source_public)
            for name in _SOURCE_TIME_FIELDS:
                source_serializable[name] = _utc_string(source_serializable[name])
            ordered = source._ordered_projection  # noqa: SLF001
            ordered_names = tuple(name for name, _item in ordered)
            if (
                ordered_names
                != (
                    "rehearsal_id",
                    "target_binding_receipt_sha256",
                    *evidence_authority.CONTEXT_FIELDS,
                    "inventory_policy_sha256",
                    "approved_state_sha256",
                    "hmac_key_version",
                    "source_profile",
                    "source_revision",
                    "inventory_observed_at",
                    "inventory_expires_at",
                    "fixed_catalog",
                )
                or receipt_sha256 != calculated_receipt_sha256
                or source.inventory_receipt_sha256
                != f"sha256:{calculated_receipt_sha256}"
                or source.inventory_policy_sha256 != f"sha256:{INVENTORY_POLICY_SHA256}"
                or inventory_receipt["policy_sha256"] != INVENTORY_POLICY_SHA256
                or source.approved_state_sha256
                != f"sha256:{inventory_receipt['approved_state_sha256']}"
                or source.fixed_catalog_projection_sha256 != fixed_projection
                or source.hmac_key_version != inventory_receipt["hmac_key_version"]
                or source.source_profile != "pre"
                or source.source_revision
                != self._inventory_policy["profiles"]["pre"]["revision"]
                or _utc_string(source.inventory_observed_at)
                != inventory_receipt["observed_at"]
                or _utc_string(source.inventory_expires_at)
                != inventory_receipt["expires_at"]
                or source.target_binding_expires_at != target.expires_at
                or dict(ordered)["fixed_catalog"] != fixed_catalog
                or any(
                    getattr(source, name) is not value
                    for name, value in self._sanitizer_policy[
                        "fixed_catalog_source_state_binding"
                    ]["claims"].items()
                )
                or not hmac.compare_digest(
                    source._proof_sha256,  # noqa: SLF001
                    hashlib.sha256(
                        _canonical_authority(
                            {
                                "ordered_projection": ordered,
                                "public": source_serializable,
                            }
                        )
                    ).hexdigest(),
                )
            ):
                _fail("data_contract_executor.authority_invalid")

        except SanitizerDataContractExecutorFailure:
            raise
        except evidence_authority.SanitizerEvidenceFailure as error:
            code = (
                _EVIDENCE_FAILURE_MAP.get(error.args[0])
                if len(error.args) == 1 and type(error.args[0]) is str
                else None
            )
            _fail(code or "data_contract_executor.authority_invalid")
        except Exception:
            _fail("data_contract_executor.authority_invalid")

    def _source_tables_from_snapshot(
        self, snapshot_tables: object
    ) -> dict[str, tuple[Mapping[str, Any], ...]]:
        expected_names = tuple(sorted(self._schemas))
        if (
            type(snapshot_tables) is not tuple
            or len(snapshot_tables) != len(expected_names)
            or any(
                type(item) is not tuple
                or len(item) != 2
                or type(item[0]) is not str
                or type(item[1]) is not tuple
                for item in snapshot_tables
            )
            or tuple(item[0] for item in snapshot_tables) != expected_names
        ):
            _fail("data_contract_executor.source_invalid")
        result: dict[str, tuple[Mapping[str, Any], ...]] = {}
        for (table_name, rows), expected_name in zip(
            snapshot_tables, expected_names, strict=True
        ):
            if type(table_name) is not str or table_name != expected_name:
                _fail("data_contract_executor.source_invalid")
            spec = self._schemas[table_name]
            expected = tuple(column[0] for column in spec["columns"])
            if type(rows) is not tuple:
                _fail("data_contract_executor.source_invalid")
            frozen_rows: list[Mapping[str, Any]] = []
            for row in rows:
                if (
                    type(row) is not source_snapshot._FrozenMap  # noqa: SLF001
                    or type(row._items) is not tuple  # noqa: SLF001
                    or any(
                        type(item) is not tuple
                        or len(item) != 2
                        or type(item[0]) is not str
                        for item in row._items  # noqa: SLF001
                    )
                    or tuple(row) != expected
                ):
                    _fail("data_contract_executor.source_invalid")
                frozen_rows.append(
                    _FrozenMap(
                        tuple(
                            (name, _validate_and_freeze(row[name], data_type, required))
                            for name, data_type, required in spec["columns"]
                        )
                    )
                )
            identity = _identity_columns(spec)
            result[table_name] = tuple(
                sorted(
                    frozen_rows,
                    key=lambda row: _canonical(tuple(row[name] for name in identity)),
                )
                if identity
                else frozen_rows
            )
        return result

    def _validated_snapshot_source(
        self,
        value: source_snapshot.AuthenticatedSanitizerSourceSnapshot,
        now: datetime,
        *,
        invalid_code: str,
        copy_tables: bool,
    ) -> tuple[
        dict[str, tuple[Mapping[str, Any], ...]] | None,
        evidence_authority.AuthenticatedSanitizerEvidence,
        source_snapshot._VerifiedSourceSnapshotCopy,  # noqa: SLF001
    ]:
        if (
            type(self) is not SanitizerDataContractExecutor
            or type(value) is not source_snapshot.AuthenticatedSanitizerSourceSnapshot
        ):
            _fail(invalid_code)
        try:
            source_identity = id(value)
            source_proof = value._proof_sha256  # noqa: SLF001
            source_contract = value._contract_sha256  # noqa: SLF001
            source_evidence = value._authenticated_evidence  # noqa: SLF001
            source_seal = value._seal  # noqa: SLF001
            if (
                type(source_proof) is not str
                or type(source_contract) is not str
                or source_contract != SOURCE_SNAPSHOT_CONTRACT_SHA256
                or type(source_evidence)
                is not evidence_authority.AuthenticatedSanitizerEvidence
                or source_seal is not source_snapshot._SNAPSHOT_SEAL  # noqa: SLF001
            ):
                _fail(invalid_code)
            verified = source_snapshot._copy_verified_tables_for_executor(  # noqa: SLF001
                value,
                now=now,
                inventory_hmac_key=self._inventory_hmac_key,
            )
            source_snapshot._validate_verified_copy_at_current_time(  # noqa: SLF001
                verified,
                now=now,
                inventory_hmac_key=self._inventory_hmac_key,
            )
            if (
                type(verified) is not source_snapshot._VerifiedSourceSnapshotCopy  # noqa: SLF001
                or verified._seal is not source_snapshot._VERIFIED_COPY_SEAL  # noqa: SLF001
                or type(verified._tables) is not tuple  # noqa: SLF001
                or type(verified._authenticated_evidence)  # noqa: SLF001
                is not evidence_authority.AuthenticatedSanitizerEvidence
                or type(verified._source_snapshot_identity) is not int  # noqa: SLF001
                or type(verified._source_snapshot_proof_sha256) is not str  # noqa: SLF001
                or verified._source_contract_sha256  # noqa: SLF001
                != SOURCE_SNAPSHOT_CONTRACT_SHA256
                or id(value) != source_identity
                or value._seal is not source_seal  # noqa: SLF001
                or source_seal is not source_snapshot._SNAPSHOT_SEAL  # noqa: SLF001
                or value._authenticated_evidence is not source_evidence  # noqa: SLF001
                or type(value._contract_sha256) is not str  # noqa: SLF001
                or value._contract_sha256 != source_contract  # noqa: SLF001
                or type(value._proof_sha256) is not str  # noqa: SLF001
                or not hmac.compare_digest(  # noqa: SLF001
                    value._proof_sha256, source_proof
                )
                or verified._source_snapshot_identity != source_identity  # noqa: SLF001
                or not hmac.compare_digest(  # noqa: SLF001
                    verified._source_snapshot_proof_sha256, source_proof
                )
                or verified._source_contract_sha256 != source_contract  # noqa: SLF001
                or verified._authenticated_evidence is not source_evidence  # noqa: SLF001
            ):
                _fail(invalid_code)
            evidence = verified._authenticated_evidence  # noqa: SLF001
            self._reauthenticate(evidence, now)
            tables = (
                self._source_tables_from_snapshot(verified._tables)  # noqa: SLF001
                if copy_tables
                else None
            )
            return tables, evidence, verified
        except SanitizerDataContractExecutorFailure:
            raise
        except source_snapshot.SanitizerSourceSnapshotFailure as error:
            mapped = _SOURCE_SNAPSHOT_FAILURE_MAP.get(
                error.args[0]
                if len(error.args) == 1 and type(error.args[0]) is str
                else ""
            )
            _fail(mapped or invalid_code)
        except evidence_authority.SanitizerEvidenceFailure as error:
            mapped = _EVIDENCE_FAILURE_MAP.get(
                error.args[0]
                if len(error.args) == 1 and type(error.args[0]) is str
                else ""
            )
            _fail(mapped or invalid_code)
        except Exception:
            _fail(invalid_code)

    def _validate_fixed_catalog_counts(
        self,
        evidence: evidence_authority.AuthenticatedSanitizerEvidence,
        tables: Mapping[str, tuple[Mapping[str, Any], ...]],
    ) -> None:
        try:
            projection = dict(evidence._fixed_catalog_source_state._ordered_projection)  # noqa: SLF001
            fixed_catalog = projection["fixed_catalog"]
            counts = {table: count for table, count, _state_hmac in fixed_catalog}
        except Exception:
            _fail("data_contract_executor.authority_invalid")
        if set(counts) != FIXED_CATALOG_TABLES or any(
            type(counts[name]) is not int or counts[name] != len(tables[name])
            for name in counts
        ):
            _fail("data_contract_executor.source_integrity_invalid")

    def _transform_value(self, value: Any, action: str, data_type: str) -> Any:
        if action in {"preserve_approved", "preserve_verified_catalog"}:
            return value
        if action == "set_null":
            return None
        if value is None:
            return None
        maximum = _maximum_length(data_type) or 64
        if action.startswith("hmac_uuid7:"):
            return _pseudonymize_uuid7(
                value, key=self._key, domain=action.removeprefix("hmac_uuid7:")
            )
        if action.startswith("hmac_text:"):
            return _pseudonymize_text(
                value,
                key=self._key,
                domain=action.removeprefix("hmac_text:"),
                maximum_length=maximum,
            )
        if action == "hmac_email":
            return _pseudonymize_email(value, key=self._key)
        if action == "hmac_domain":
            return _pseudonymize_domain(value, key=self._key)
        if action == "hmac_issuer":
            return _pseudonymize_issuer(value, key=self._key)
        if action == "hmac_subject":
            if not value.startswith("user_") or any(
                character.isspace() for character in value
            ):
                _fail("data_contract_executor.transform_invalid")
            return _pseudonymize_text(
                value,
                key=self._key,
                domain="clerk_subject",
                prefix="user_",
                maximum_length=maximum,
            )
        if (
            action
            in "derive_typeid_text:appointment rotate_join_code derive_access_code_salt derive_access_code_digest".split()
        ):
            return None
        _fail("data_contract_executor.policy_invalid")

    def _rewrite_row(
        self,
        table_name: str,
        row: Mapping[str, Any],
        appointment_ids: frozenset[uuid.UUID],
    ) -> Mapping[str, Any]:
        spec = self._schemas[table_name]
        actions = self._contract["tables"][table_name]["columns"]
        output = {
            name: self._transform_value(row[name], actions[name], data_type)
            for name, data_type, _required in spec["columns"]
        }
        if table_name == "authority_person_binding":
            if row["binding_kind"] != "provider_record":
                _fail("data_contract_executor.transform_invalid")
            try:
                parsed = TypeID.from_string(row["binding_ref"])
            except Exception:
                _fail("data_contract_executor.transform_invalid")
            if parsed.prefix != "appointment" or parsed.uuid not in appointment_ids:
                _fail("data_contract_executor.source_integrity_invalid")
            mapped = _pseudonymize_uuid7(
                parsed.uuid, key=self._key, domain="typeid:appointment"
            )
            output["binding_ref"] = str(TypeID.from_uuid(mapped, "appointment"))
        elif table_name == "practice":
            output["join_code"] = _join_code(output["id"], self._key)
        elif table_name == "provider_organization":
            credentials = _provider_access_credentials(output["id"], key=self._key)
            output.update(credentials)
        return _FrozenMap(tuple(output.items()))

    def transform(
        self,
        *,
        authenticated_source_snapshot: (
            source_snapshot.AuthenticatedSanitizerSourceSnapshot
        ),
    ) -> SanitizerCandidateDataset:
        code = "data_contract_executor.configuration_invalid"
        if type(self) is SanitizerDataContractExecutor:
            try:
                return SanitizerDataContractExecutor._transform(
                    self, authenticated_source_snapshot=authenticated_source_snapshot
                )
            except Exception as error:
                code = _bounded_code(error, "data_contract_executor.source_invalid")
        del self, authenticated_source_snapshot
        _fail(code)

    def _transform(
        self,
        *,
        authenticated_source_snapshot: (
            source_snapshot.AuthenticatedSanitizerSourceSnapshot
        ),
    ) -> SanitizerCandidateDataset:
        if type(self) is not SanitizerDataContractExecutor:
            _fail("data_contract_executor.configuration_invalid")
        if (
            type(authenticated_source_snapshot)
            is not source_snapshot.AuthenticatedSanitizerSourceSnapshot
        ):
            _fail("data_contract_executor.source_invalid")
        source, evidence, verified_source = self._validated_snapshot_source(
            authenticated_source_snapshot,
            self._read_clock(),
            invalid_code="data_contract_executor.source_invalid",
            copy_tables=True,
        )
        if source is None:
            _fail("data_contract_executor.source_invalid")
        self._validate_fixed_catalog_counts(evidence, source)
        _validate_unique(source, self._schemas)
        _validate_foreign_keys(source, self._foreign_keys)
        appointment_ids = frozenset(row["id"] for row in source["appointment"])
        transformed: dict[str, tuple[Mapping[str, Any], ...]] = {}
        for table_name in self._schemas:
            transformed[table_name] = (
                tuple()
                if table_name in self._discarded
                else tuple(
                    self._rewrite_row(table_name, row, appointment_ids)
                    for row in source[table_name]
                )
            )
        _validate_unique(transformed, self._schemas)
        _validate_foreign_keys(
            transformed, self._foreign_keys, skipped_sources=self._discarded
        )
        join_codes = [row["join_code"] for row in transformed["practice"]]
        if len(join_codes) != len(set(join_codes)):
            _fail("data_contract_executor.output_invalid")
        completed_at = self._read_clock()
        _unused, final_evidence, final_verified_source = (
            self._validated_snapshot_source(
                authenticated_source_snapshot,
                completed_at,
                invalid_code="data_contract_executor.source_invalid",
                copy_tables=False,
            )
        )
        if (
            final_evidence is not evidence
            or final_verified_source._source_snapshot_identity  # noqa: SLF001
            != verified_source._source_snapshot_identity  # noqa: SLF001
            or final_verified_source._source_snapshot_proof_sha256  # noqa: SLF001
            != verified_source._source_snapshot_proof_sha256  # noqa: SLF001
            or final_verified_source._source_contract_sha256  # noqa: SLF001
            != verified_source._source_contract_sha256  # noqa: SLF001
        ):
            _fail("data_contract_executor.source_invalid")
        ordered = tuple((name, transformed[name]) for name in sorted(transformed))
        candidate = _mint_candidate(
            key=self._key,
            key_version=self._key_version,
            verified_source_snapshot=verified_source,
            tables=ordered,
            transformed_at=completed_at,
            source_row_count=sum(len(rows) for rows in source.values()),
        )
        self._verify_candidate(candidate)
        return candidate

    def _verify_candidate(self, candidate: SanitizerCandidateDataset) -> None:
        if type(self) is not SanitizerDataContractExecutor:
            _fail("data_contract_executor.configuration_invalid")
        if type(candidate) is not SanitizerCandidateDataset:
            _fail("data_contract_executor.candidate_invalid")
        try:
            candidate_tables = candidate._tables
            evidence = candidate._authenticated_evidence
            if (
                candidate._seal is not _CANDIDATE_SEAL
                or type(candidate_tables) is not tuple
                or any(
                    type(item) is not tuple
                    or len(item) != 2
                    or type(item[0]) is not str
                    or type(item[1]) is not tuple
                    or any(type(row) is not _FrozenMap for row in item[1])
                    for item in candidate_tables
                )
                or type(evidence)
                is not evidence_authority.AuthenticatedSanitizerEvidence
                or evidence._seal is not evidence_authority._EVIDENCE_SEAL  # noqa: SLF001
                or type(evidence.evidence_binding_sha256) is not str
                or re.fullmatch(
                    r"sha256:[0-9a-f]{64}", evidence.evidence_binding_sha256
                )
                is None
                or type(evidence._proof_sha256) is not str  # noqa: SLF001
                or re.fullmatch(r"[0-9a-f]{64}", evidence._proof_sha256)  # noqa: SLF001
                is None
                or type(candidate._source_snapshot_contract_sha256) is not str
                or candidate._source_snapshot_contract_sha256
                != SOURCE_SNAPSHOT_CONTRACT_SHA256
                or type(candidate._source_snapshot_proof_sha256) is not str
                or re.fullmatch(
                    r"[0-9a-f]{64}", candidate._source_snapshot_proof_sha256
                )
                is None
                or any(
                    type(item) is not int or item <= 0
                    for item in (
                        candidate._source_snapshot_identity,
                        candidate._source_snapshot_type_identity,
                        candidate._source_snapshot_seal_identity,
                    )
                )
                or candidate._source_snapshot_type_identity
                != id(source_snapshot.AuthenticatedSanitizerSourceSnapshot)
                or candidate._source_snapshot_seal_identity
                != id(source_snapshot._SNAPSHOT_SEAL)  # noqa: SLF001
                or type(candidate._contract_sha256) is not str
                or candidate._contract_sha256 != DATA_CONTRACT_SHA256
                or type(candidate._proof_sha256) is not str
                or re.fullmatch(r"[0-9a-f]{64}", candidate._proof_sha256) is None
                or type(candidate.transform_key_version) is not str
                or candidate.transform_key_version != self._key_version
                or type(candidate.transformed_at) is not datetime
                or candidate.transformed_at.tzinfo is not UTC
                or candidate.transformed_at.microsecond != 0
                or candidate.transformed_at.fold != 0
                or any(
                    type(getattr(candidate, name)) is not int
                    for name in "table_count rewrite_table_count discard_table_count source_row_count candidate_row_count".split()
                )
                or any(
                    getattr(candidate, name) is not False for name in _AUTHORITY_CLAIMS
                )
            ):
                _fail("data_contract_executor.candidate_invalid")
            for _table_name, rows in candidate_tables:
                for row in rows:
                    frozen_row = cast(_FrozenMap, row)
                    if (
                        type(frozen_row._items) is not tuple
                        or any(
                            type(item) is not tuple
                            or len(item) != 2
                            or type(item[0]) is not str
                            for item in frozen_row._items
                        )
                        or not _is_frozen(frozen_row)
                    ):
                        _fail("data_contract_executor.candidate_invalid")
            names = tuple(name for name, _rows in candidate_tables)
            if names != tuple(sorted(self._schemas)):
                _fail("data_contract_executor.candidate_invalid")
            tables = dict(candidate_tables)
            structurally_valid = all(
                not tables[name] for name in self._discarded
            ) and all(
                tuple(row)
                == tuple(column[0] for column in self._schemas[name]["columns"])
                for name, rows in tables.items()
                for row in rows
            )
            candidate_rows = sum(len(rows) for rows in tables.values())
            expected_proof = _candidate_proof(
                key=self._key,
                tables=candidate_tables,
                evidence=evidence,
                source_snapshot_contract_sha256=(
                    candidate._source_snapshot_contract_sha256
                ),
                source_snapshot_proof_sha256=candidate._source_snapshot_proof_sha256,
                source_snapshot_identity=candidate._source_snapshot_identity,
                source_snapshot_type_identity=candidate._source_snapshot_type_identity,
                source_snapshot_seal_identity=candidate._source_snapshot_seal_identity,
                source_row_count=candidate.source_row_count,
                transformed_at=candidate.transformed_at,
                transform_key_version=candidate.transform_key_version,
            )
        except Exception:
            _fail("data_contract_executor.candidate_invalid")
        if (
            not structurally_valid
            or candidate.source_row_count < candidate_rows
            or candidate.candidate_row_count != candidate_rows
            or candidate.table_count != len(self._schemas)
            or candidate.rewrite_table_count != REWRITE_TABLE_COUNT
            or candidate.discard_table_count != DISCARD_TABLE_COUNT
            or not hmac.compare_digest(
                candidate._proof_sha256,
                expected_proof,
            )
        ):
            _fail("data_contract_executor.candidate_invalid")

    def _copy_verified_fixed_catalog_for_post_projection(
        self,
        candidate: SanitizerCandidateDataset,
        *,
        now: datetime,
    ) -> _VerifiedFixedCatalogCandidateCopy:
        if (
            type(self) is not SanitizerDataContractExecutor
            or type(candidate) is not SanitizerCandidateDataset
            or type(now) is not datetime
            or now.tzinfo is not UTC
            or now.utcoffset() != _ZERO
            or now.microsecond != 0
            or now.fold != 0
        ):
            _fail("data_contract_executor.candidate_invalid")
        self._verify_candidate(candidate)
        if candidate.transformed_at > now:
            _fail("data_contract_executor.candidate_invalid")
        self._reauthenticate(candidate._authenticated_evidence, now)
        identity = id(candidate)
        tables = candidate._tables
        evidence = candidate._authenticated_evidence
        proof = candidate._proof_sha256
        contract = candidate._contract_sha256
        source_lineage = (
            candidate._source_snapshot_identity,
            candidate._source_snapshot_type_identity,
            candidate._source_snapshot_seal_identity,
            candidate._source_snapshot_proof_sha256,
            candidate._source_snapshot_contract_sha256,
        )
        transformed_at = candidate.transformed_at
        key_version = candidate.transform_key_version
        copied_tables = tuple(
            (
                table,
                tuple(cast(_FrozenMap, _copy_candidate_value(row)) for row in rows),
            )
            for table, rows in tables
            if table in FIXED_CATALOG_TABLES
        )
        copied = object.__new__(_VerifiedFixedCatalogCandidateCopy)
        object.__setattr__(copied, "_tables", copied_tables)
        object.__setattr__(copied, "_authenticated_evidence", evidence)
        object.__setattr__(copied, "_candidate_identity", identity)
        object.__setattr__(
            copied, "_candidate_type_identity", id(SanitizerCandidateDataset)
        )
        object.__setattr__(copied, "_candidate_seal_identity", id(_CANDIDATE_SEAL))
        object.__setattr__(copied, "_candidate_proof_sha256", proof)
        object.__setattr__(copied, "_candidate_contract_sha256", contract)
        object.__setattr__(copied, "_source_snapshot_identity", source_lineage[0])
        object.__setattr__(copied, "_source_snapshot_type_identity", source_lineage[1])
        object.__setattr__(copied, "_source_snapshot_seal_identity", source_lineage[2])
        object.__setattr__(copied, "_source_snapshot_proof_sha256", source_lineage[3])
        object.__setattr__(
            copied, "_source_snapshot_contract_sha256", source_lineage[4]
        )
        object.__setattr__(copied, "_transform_key_version", key_version)
        object.__setattr__(copied, "_transformed_at", transformed_at)
        object.__setattr__(copied, "_seal", _FIXED_CATALOG_COPY_SEAL)
        object.__setattr__(
            copied, "_proof_sha256", _fixed_catalog_copy_proof(copied, self._key)
        )
        if (
            id(candidate) != identity
            or candidate._seal is not _CANDIDATE_SEAL
            or candidate._tables is not tables
            or candidate._authenticated_evidence is not evidence
            or candidate._proof_sha256 != proof
            or candidate._contract_sha256 != contract
            or candidate.transformed_at is not transformed_at
            or candidate.transform_key_version != key_version
            or (
                candidate._source_snapshot_identity,
                candidate._source_snapshot_type_identity,
                candidate._source_snapshot_seal_identity,
                candidate._source_snapshot_proof_sha256,
                candidate._source_snapshot_contract_sha256,
            )
            != source_lineage
        ):
            _fail("data_contract_executor.candidate_invalid")
        self._verify_candidate(candidate)
        self._validate_verified_fixed_catalog_copy_at_current_time(copied, now=now)
        return copied

    def _validate_verified_fixed_catalog_copy_at_current_time(
        self,
        value: _VerifiedFixedCatalogCandidateCopy,
        *,
        now: datetime,
    ) -> None:
        if (
            type(self) is not SanitizerDataContractExecutor
            or type(value) is not _VerifiedFixedCatalogCandidateCopy
            or value._seal is not _FIXED_CATALOG_COPY_SEAL
            or type(now) is not datetime
            or now.tzinfo is not UTC
            or now.utcoffset() != _ZERO
            or now.microsecond != 0
            or now.fold != 0
            or type(value._tables) is not tuple
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
            or tuple(item[0] for item in value._tables)
            != tuple(sorted(FIXED_CATALOG_TABLES))
            or type(value._authenticated_evidence)
            is not evidence_authority.AuthenticatedSanitizerEvidence
            or value._authenticated_evidence._seal  # noqa: SLF001
            is not evidence_authority._EVIDENCE_SEAL  # noqa: SLF001
            or type(value._authenticated_evidence._proof_sha256)  # noqa: SLF001
            is not str
            or re.fullmatch(  # noqa: SLF001
                r"[0-9a-f]{64}", value._authenticated_evidence._proof_sha256
            )
            is None
            or any(
                type(item) is not int or item <= 0
                for item in (
                    value._candidate_identity,
                    value._candidate_type_identity,
                    value._candidate_seal_identity,
                    value._source_snapshot_identity,
                    value._source_snapshot_type_identity,
                    value._source_snapshot_seal_identity,
                )
            )
            or any(
                type(item) is not str or re.fullmatch(r"[0-9a-f]{64}", item) is None
                for item in (
                    value._candidate_proof_sha256,
                    value._candidate_contract_sha256,
                    value._source_snapshot_proof_sha256,
                    value._source_snapshot_contract_sha256,
                    value._proof_sha256,
                )
            )
            or value._candidate_contract_sha256 != DATA_CONTRACT_SHA256
            or value._candidate_type_identity != id(SanitizerCandidateDataset)
            or value._candidate_seal_identity != id(_CANDIDATE_SEAL)
            or value._source_snapshot_contract_sha256 != SOURCE_SNAPSHOT_CONTRACT_SHA256
            or value._source_snapshot_type_identity
            != id(source_snapshot.AuthenticatedSanitizerSourceSnapshot)
            or value._source_snapshot_seal_identity
            != id(source_snapshot._SNAPSHOT_SEAL)  # noqa: SLF001
            or type(value._transform_key_version) is not str
            or value._transform_key_version != self._key_version
            or type(value._transformed_at) is not datetime
            or value._transformed_at.tzinfo is not UTC
            or value._transformed_at.utcoffset() != _ZERO
            or value._transformed_at.microsecond != 0
            or value._transformed_at.fold != 0
            or value._transformed_at > now
            or not hmac.compare_digest(
                value._proof_sha256, _fixed_catalog_copy_proof(value, self._key)
            )
        ):
            _fail("data_contract_executor.candidate_invalid")
        self._reauthenticate(value._authenticated_evidence, now)

    def _copy_verified_candidate_for_transaction_plan(
        self,
        candidate: SanitizerCandidateDataset,
        *,
        now: datetime,
    ) -> _VerifiedCandidateTransactionCopy:
        if (
            type(self) is not SanitizerDataContractExecutor
            or type(candidate) is not SanitizerCandidateDataset
            or type(now) is not datetime
            or now.tzinfo is not UTC
            or now.utcoffset() != _ZERO
            or now.microsecond != 0
            or now.fold != 0
        ):
            _fail("data_contract_executor.candidate_invalid")
        self._verify_candidate(candidate)
        if candidate.transformed_at > now:
            _fail("data_contract_executor.candidate_invalid")
        self._reauthenticate(candidate._authenticated_evidence, now)
        identity = id(candidate)
        tables = candidate._tables
        evidence = candidate._authenticated_evidence
        proof = candidate._proof_sha256
        contract = candidate._contract_sha256
        source_lineage = (
            candidate._source_snapshot_identity,
            candidate._source_snapshot_type_identity,
            candidate._source_snapshot_seal_identity,
            candidate._source_snapshot_proof_sha256,
            candidate._source_snapshot_contract_sha256,
        )
        transformed_at = candidate.transformed_at
        key_version = candidate.transform_key_version
        source_row_count = candidate.source_row_count
        candidate_row_count = candidate.candidate_row_count
        copied_tables = tuple(
            (
                table,
                tuple(cast(_FrozenMap, _copy_candidate_value(row)) for row in rows),
            )
            for table, rows in tables
        )
        copied = object.__new__(_VerifiedCandidateTransactionCopy)
        object.__setattr__(copied, "_tables", copied_tables)
        object.__setattr__(copied, "_authenticated_evidence", evidence)
        object.__setattr__(copied, "_candidate_identity", identity)
        object.__setattr__(
            copied, "_candidate_type_identity", id(SanitizerCandidateDataset)
        )
        object.__setattr__(copied, "_candidate_seal_identity", id(_CANDIDATE_SEAL))
        object.__setattr__(copied, "_candidate_proof_sha256", proof)
        object.__setattr__(copied, "_candidate_contract_sha256", contract)
        object.__setattr__(copied, "_source_snapshot_identity", source_lineage[0])
        object.__setattr__(copied, "_source_snapshot_type_identity", source_lineage[1])
        object.__setattr__(copied, "_source_snapshot_seal_identity", source_lineage[2])
        object.__setattr__(copied, "_source_snapshot_proof_sha256", source_lineage[3])
        object.__setattr__(
            copied, "_source_snapshot_contract_sha256", source_lineage[4]
        )
        object.__setattr__(copied, "_source_row_count", source_row_count)
        object.__setattr__(copied, "_candidate_row_count", candidate_row_count)
        object.__setattr__(copied, "_transform_key_version", key_version)
        object.__setattr__(copied, "_transformed_at", transformed_at)
        object.__setattr__(copied, "_seal", _CANDIDATE_TRANSACTION_COPY_SEAL)
        object.__setattr__(
            copied,
            "_proof_sha256",
            _candidate_transaction_copy_proof(copied, self._key),
        )
        if (
            id(candidate) != identity
            or candidate._seal is not _CANDIDATE_SEAL
            or candidate._tables is not tables
            or candidate._authenticated_evidence is not evidence
            or candidate._proof_sha256 != proof
            or candidate._contract_sha256 != contract
            or candidate.transformed_at is not transformed_at
            or candidate.transform_key_version != key_version
            or candidate.source_row_count != source_row_count
            or candidate.candidate_row_count != candidate_row_count
            or (
                candidate._source_snapshot_identity,
                candidate._source_snapshot_type_identity,
                candidate._source_snapshot_seal_identity,
                candidate._source_snapshot_proof_sha256,
                candidate._source_snapshot_contract_sha256,
            )
            != source_lineage
        ):
            _fail("data_contract_executor.candidate_invalid")
        self._verify_candidate(candidate)
        self._validate_verified_candidate_transaction_copy_at_current_time(
            copied, now=now
        )
        return copied

    def _validate_verified_candidate_transaction_copy_at_current_time(
        self,
        value: _VerifiedCandidateTransactionCopy,
        *,
        now: datetime,
    ) -> None:
        if (
            type(self) is not SanitizerDataContractExecutor
            or type(value) is not _VerifiedCandidateTransactionCopy
            or value._seal is not _CANDIDATE_TRANSACTION_COPY_SEAL
            or type(now) is not datetime
            or now.tzinfo is not UTC
            or now.utcoffset() != _ZERO
            or now.microsecond != 0
            or now.fold != 0
            or type(value._tables) is not tuple
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
            or tuple(item[0] for item in value._tables) != tuple(sorted(self._schemas))
            or any(
                tuple(row)
                != tuple(column[0] for column in self._schemas[name]["columns"])
                for name, rows in value._tables
                for row in rows
            )
            or any(dict(value._tables)[name] for name in self._discarded)
            or type(value._authenticated_evidence)
            is not evidence_authority.AuthenticatedSanitizerEvidence
            or value._authenticated_evidence._seal  # noqa: SLF001
            is not evidence_authority._EVIDENCE_SEAL  # noqa: SLF001
            or any(
                type(item) is not int or item <= 0
                for item in (
                    value._candidate_identity,
                    value._candidate_type_identity,
                    value._candidate_seal_identity,
                    value._source_snapshot_identity,
                    value._source_snapshot_type_identity,
                    value._source_snapshot_seal_identity,
                )
            )
            or any(
                type(item) is not str or re.fullmatch(r"[0-9a-f]{64}", item) is None
                for item in (
                    value._candidate_proof_sha256,
                    value._candidate_contract_sha256,
                    value._source_snapshot_proof_sha256,
                    value._source_snapshot_contract_sha256,
                    value._proof_sha256,
                )
            )
            or value._candidate_contract_sha256 != DATA_CONTRACT_SHA256
            or value._candidate_type_identity != id(SanitizerCandidateDataset)
            or value._candidate_seal_identity != id(_CANDIDATE_SEAL)
            or value._source_snapshot_contract_sha256 != SOURCE_SNAPSHOT_CONTRACT_SHA256
            or value._source_snapshot_type_identity
            != id(source_snapshot.AuthenticatedSanitizerSourceSnapshot)
            or value._source_snapshot_seal_identity
            != id(source_snapshot._SNAPSHOT_SEAL)  # noqa: SLF001
            or type(value._source_row_count) is not int
            or type(value._candidate_row_count) is not int
            or value._source_row_count < value._candidate_row_count
            or value._candidate_row_count
            != sum(len(rows) for _name, rows in value._tables)
            or type(value._transform_key_version) is not str
            or value._transform_key_version != self._key_version
            or type(value._transformed_at) is not datetime
            or value._transformed_at.tzinfo is not UTC
            or value._transformed_at.utcoffset() != _ZERO
            or value._transformed_at.microsecond != 0
            or value._transformed_at.fold != 0
            or value._transformed_at > now
            or not hmac.compare_digest(
                value._candidate_proof_sha256,
                _candidate_proof(
                    key=self._key,
                    tables=value._tables,
                    evidence=value._authenticated_evidence,
                    source_snapshot_contract_sha256=value._source_snapshot_contract_sha256,
                    source_snapshot_proof_sha256=value._source_snapshot_proof_sha256,
                    source_snapshot_identity=value._source_snapshot_identity,
                    source_snapshot_type_identity=value._source_snapshot_type_identity,
                    source_snapshot_seal_identity=value._source_snapshot_seal_identity,
                    source_row_count=value._source_row_count,
                    transformed_at=value._transformed_at,
                    transform_key_version=value._transform_key_version,
                ),
            )
            or not hmac.compare_digest(
                value._proof_sha256,
                _candidate_transaction_copy_proof(value, self._key),
            )
        ):
            _fail("data_contract_executor.candidate_invalid")
        self._reauthenticate(value._authenticated_evidence, now)

    def validate_candidate(self, candidate: SanitizerCandidateDataset) -> None:
        code = "data_contract_executor.configuration_invalid"
        if type(self) is SanitizerDataContractExecutor:
            try:
                SanitizerDataContractExecutor._validate_candidate(self, candidate)
                return
            except Exception as error:
                code = _bounded_code(error, "data_contract_executor.candidate_invalid")
        del self, candidate
        _fail(code)

    def _validate_candidate(self, candidate: SanitizerCandidateDataset) -> None:
        if type(self) is not SanitizerDataContractExecutor:
            _fail("data_contract_executor.configuration_invalid")
        if type(candidate) is not SanitizerCandidateDataset:
            _fail("data_contract_executor.candidate_invalid")
        now = self._read_clock()
        self._verify_candidate(candidate)
        if candidate.transformed_at > now:
            _fail("data_contract_executor.candidate_invalid")
        self._reauthenticate(candidate._authenticated_evidence, now)
