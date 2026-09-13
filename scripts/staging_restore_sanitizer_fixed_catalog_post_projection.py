from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import UTC, date, datetime, timedelta
from typing import Any, NoReturn

try:
    from scripts import (
        staging_restore_sanitizer_data_contract_executor as data_executor,
    )
    from scripts import (
        staging_restore_sanitizer_evidence_authority as evidence_authority,
    )
    from scripts import staging_restore_sanitizer_source_snapshot as source_snapshot
except ModuleNotFoundError:  # Direct import compatibility for local review.
    import staging_restore_sanitizer_data_contract_executor as data_executor
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
POST_PROJECTION_CONTRACT_SHA256 = (
    "88d0f564ec52a060dd30b308a9d669173a078acd2c1ba7ee06744301a5c89f6f"
)
ACTIVATION_FAILURE = "activation.data_contract_executor_unimplemented"
INVENTORY_HMAC_KEY_VERSION = "staging-inventory-hmac-v1"
FIXED_CATALOG_TABLE_COUNT = 5
FIXED_CATALOG_COLUMN_COUNT = 46
MINIMUM_KEY_BYTES = 32
MAXIMUM_KEY_BYTES = 4096
_TRANSFORM_HMAC_ROOT = "cosmaneura:staging-restore-sanitizer:v1"
_UUID7_EPOCH_START_MS = 1_577_836_800_000
_UUID7_EPOCH_SPAN_MS = 315_576_000_000
PROJECTION_DOMAIN = "fixed-catalog:post-projection"
AUTHORITY_DOMAIN = "fixed-catalog:post-projection-authority"
_ZERO = timedelta(0)
_HEX_256_RE = re.compile(r"^[0-9a-f]{64}$")
_KEY_VERSION_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_FIXED_TABLES = (
    "baa_version",
    "doctor_note_schema",
    "drug_reference",
    "lab_range_set",
    "note_type_customization",
)
_POSITIVE_CLAIMS = {
    "source_snapshot_authenticated": True,
    "candidate_dataset_authenticated": True,
    "fixed_catalog_post_projection_authenticated": True,
    "post_projection_authenticated": True,
}
_NEGATIVE_CLAIMS = dict.fromkeys(
    "database_connection_authority database_mutation_authority evidence_publication_authority executable live_provider_calls_enabled provider_calls_permitted provider_facts_authenticated runtime_authority sanitizer_authority workflow_integrated".split(),
    False,
)
_AUTHORITY_SEAL = object()
_FAILURE_CODES = frozenset(
    f"fixed_catalog_post_projection.{suffix}"
    for suffix in (
        "authority_invalid",
        "candidate_invalid",
        "clock_invalid",
        "clock_regressed",
        "configuration_invalid",
        "output_invalid",
        "policy_invalid",
        "projection_invalid",
        "source_invalid",
        "target_expired",
    )
)


class SanitizerFixedCatalogPostProjectionFailure(RuntimeError):
    """Bounded refusal without table, row, or value details."""


def _fail(code: str) -> NoReturn:
    raise SanitizerFixedCatalogPostProjectionFailure(code)


def _bounded_code(error: Exception, fallback: str) -> str:
    if (
        type(error) is SanitizerFixedCatalogPostProjectionFailure
        and len(error.args) == 1
        and type(error.args[0]) is str
        and error.args[0] in _FAILURE_CODES
    ):
        return str(error.args[0])
    value: str = (
        error.args[0] if len(error.args) == 1 and type(error.args[0]) is str else ""
    )
    if type(error) is source_snapshot.SanitizerSourceSnapshotFailure:
        return {
            "source_snapshot.clock_regressed": "fixed_catalog_post_projection.clock_regressed",
            "source_snapshot.target_expired": "fixed_catalog_post_projection.target_expired",
        }.get(value, "fixed_catalog_post_projection.source_invalid")
    if type(error) is data_executor.SanitizerDataContractExecutorFailure:
        return {
            "data_contract_executor.clock_regressed": "fixed_catalog_post_projection.clock_regressed",
            "data_contract_executor.target_expired": "fixed_catalog_post_projection.target_expired",
        }.get(value, "fixed_catalog_post_projection.candidate_invalid")
    return fallback


def _utc(clock: Callable[[], datetime]) -> datetime:
    try:
        value = clock()
    except Exception:
        _fail("fixed_catalog_post_projection.clock_invalid")
    if (
        type(value) is not datetime
        or value.tzinfo is not UTC
        or value.utcoffset() != _ZERO
        or value.microsecond != 0
        or value.fold != 0
    ):
        _fail("fixed_catalog_post_projection.clock_invalid")
    return value


def _parse_pinned(raw: bytes, digest: str) -> dict[str, Any]:
    if type(raw) is not bytes or not hmac.compare_digest(
        hashlib.sha256(raw).hexdigest(), digest
    ):
        _fail("fixed_catalog_post_projection.policy_invalid")

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for name, value in pairs:
            if name in result:
                _fail("fixed_catalog_post_projection.policy_invalid")
            result[name] = value
        return result

    try:
        value = json.loads(
            raw,
            object_pairs_hook=reject_duplicates,
            parse_constant=lambda _value: _fail(
                "fixed_catalog_post_projection.policy_invalid"
            ),
        )
    except SanitizerFixedCatalogPostProjectionFailure:
        raise
    except (UnicodeDecodeError, ValueError, RecursionError):
        _fail("fixed_catalog_post_projection.policy_invalid")
    if type(value) is not dict:
        _fail("fixed_catalog_post_projection.policy_invalid")
    return value


def _token(value: Any) -> Any:
    kind = type(value)
    if value is None:
        return ["none"]
    if kind is bool:
        return ["bool", value]
    if kind is int:
        return ["int", str(value)]
    if kind is str:
        return ["str", value]
    if kind is float and math.isfinite(value):
        return ["float", value.hex()]
    if kind is uuid.UUID:
        return ["uuid", str(value)]
    if kind is datetime:
        return [
            "datetime",
            value.isoformat(),
            value.fold,
            id(type(value.tzinfo)),
            id(value.tzinfo),
        ]
    if kind is date:
        return ["date", value.isoformat()]
    if kind is tuple:
        return ["tuple", [_token(item) for item in value]]
    if kind in {
        data_executor._FrozenMap,  # noqa: SLF001
        source_snapshot._FrozenMap,  # noqa: SLF001
    }:
        items = value._items  # noqa: SLF001
        if type(items) is not tuple or any(
            type(item) is not tuple or len(item) != 2 or type(item[0]) is not str
            for item in items
        ):
            _fail("fixed_catalog_post_projection.projection_invalid")
        names = tuple(name for name, _item in items)
        if len(names) != len(set(names)):
            _fail("fixed_catalog_post_projection.projection_invalid")
        return ["map", [[name, _token(item)] for name, item in items]]
    _fail("fixed_catalog_post_projection.projection_invalid")


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    except (TypeError, ValueError, UnicodeEncodeError, RecursionError):
        _fail("fixed_catalog_post_projection.output_invalid")


def _digest(key: bytes, domain: str, value: bytes) -> str:
    if (
        type(key) is not bytes
        or not MINIMUM_KEY_BYTES <= len(key) <= MAXIMUM_KEY_BYTES
        or domain not in {PROJECTION_DOMAIN, AUTHORITY_DOMAIN}
        or type(value) is not bytes
    ):
        _fail("fixed_catalog_post_projection.configuration_invalid")
    encoded_domain = f"{_TRANSFORM_HMAC_ROOT}:{domain}".encode()
    framed = (
        len(encoded_domain).to_bytes(2, "big")
        + encoded_domain
        + len(value).to_bytes(8, "big")
        + value
    )
    return hmac.new(key, framed, hashlib.sha256).hexdigest()


def _uuid7(value: Any, *, key: bytes, domain: str) -> uuid.UUID:
    if type(value) is not uuid.UUID or not domain.startswith("typeid:"):
        _fail("fixed_catalog_post_projection.projection_invalid")
    semantic = f"{_TRANSFORM_HMAC_ROOT}:{domain}".encode()
    raw = b"uuid:" + value.bytes
    framed = (
        len(semantic).to_bytes(2, "big") + semantic + len(raw).to_bytes(8, "big") + raw
    )
    hashed = hmac.new(key, framed, hashlib.sha256).digest()
    timestamp = (
        _UUID7_EPOCH_START_MS + int.from_bytes(hashed[:6], "big") % _UUID7_EPOCH_SPAN_MS
    )
    output = bytearray(timestamp.to_bytes(6, "big") + hashed[6:16])
    output[6] = (output[6] & 0x0F) | 0x70
    output[8] = (output[8] & 0x3F) | 0x80
    return uuid.UUID(bytes=bytes(output))


@dataclass(frozen=True, slots=True, init=False, repr=False)
class AuthenticatedFixedCatalogPostProjection:
    fixed_catalog_table_count: int
    fixed_catalog_column_count: int
    fixed_catalog_row_count: int
    transform_key_version: str
    authenticated_at: datetime
    source_snapshot_authenticated: bool
    candidate_dataset_authenticated: bool
    fixed_catalog_post_projection_authenticated: bool
    post_projection_authenticated: bool
    database_connection_authority: bool
    database_mutation_authority: bool
    evidence_publication_authority: bool
    executable: bool
    live_provider_calls_enabled: bool
    provider_calls_permitted: bool
    provider_facts_authenticated: bool
    runtime_authority: bool
    sanitizer_authority: bool
    workflow_integrated: bool
    _authenticated_evidence: evidence_authority.AuthenticatedSanitizerEvidence = (
        dataclass_field(repr=False)
    )
    _source_snapshot_identity: int = dataclass_field(repr=False)
    _source_snapshot_proof_sha256: str = dataclass_field(repr=False)
    _candidate_dataset_identity: int = dataclass_field(repr=False)
    _candidate_dataset_proof_sha256: str = dataclass_field(repr=False)
    _candidate_copy_proof_sha256: str = dataclass_field(repr=False)
    _projection_proof_sha256: str = dataclass_field(repr=False)
    _contract_sha256: str = dataclass_field(repr=False)
    _proof_sha256: str = dataclass_field(repr=False)
    _seal: object = dataclass_field(repr=False, compare=False)

    def __init__(self) -> None:
        _fail("fixed_catalog_post_projection.output_invalid")

    def __repr__(self) -> str:
        return "<AuthenticatedFixedCatalogPostProjection sealed non-authoritative>"

    def __reduce__(self) -> NoReturn:
        del self
        _fail("fixed_catalog_post_projection.output_invalid")

    def __reduce_ex__(self, protocol: object) -> NoReturn:
        del self, protocol
        _fail("fixed_catalog_post_projection.output_invalid")


def _authority_material(value: AuthenticatedFixedCatalogPostProjection) -> bytes:
    names = (
        "fixed_catalog_table_count",
        "fixed_catalog_column_count",
        "fixed_catalog_row_count",
        "transform_key_version",
        "authenticated_at",
        *_POSITIVE_CLAIMS,
        *_NEGATIVE_CLAIMS,
    )
    return _canonical(
        [
            ["authority_identity", id(value)],
            ["contract_sha256", value._contract_sha256],
            ["public", [[name, _token(getattr(value, name))] for name in names]],
            ["evidence_identity", id(value._authenticated_evidence)],
            ["evidence_type_identity", id(type(value._authenticated_evidence))],
            [
                "evidence_seal_identity",
                id(value._authenticated_evidence._seal),  # noqa: SLF001
            ],
            [
                "evidence_proof_sha256",
                value._authenticated_evidence._proof_sha256,  # noqa: SLF001
            ],
            ["source_snapshot_identity", value._source_snapshot_identity],
            ["source_snapshot_proof_sha256", value._source_snapshot_proof_sha256],
            ["candidate_dataset_identity", value._candidate_dataset_identity],
            ["candidate_dataset_proof_sha256", value._candidate_dataset_proof_sha256],
            ["candidate_copy_proof_sha256", value._candidate_copy_proof_sha256],
            ["projection_proof_sha256", value._projection_proof_sha256],
        ]
    )


class SanitizerFixedCatalogPostProjectionAuthenticator:
    __slots__ = (
        "_clock",
        "_engine",
        "_fixed_specs",
        "_inventory_key",
        "_last",
        "_transform_key",
        "_transform_key_version",
    )

    def __init__(self) -> None:
        _fail("fixed_catalog_post_projection.configuration_invalid")

    def __setattr__(self, name: str, value: object) -> NoReturn:
        del self, name, value
        _fail("fixed_catalog_post_projection.configuration_invalid")

    def __delattr__(self, name: str) -> NoReturn:
        del self, name
        _fail("fixed_catalog_post_projection.configuration_invalid")

    def __repr__(self) -> str:
        return (
            "<SanitizerFixedCatalogPostProjectionAuthenticator dormant in-memory-only>"
        )

    def __reduce__(self) -> NoReturn:
        del self
        _fail("fixed_catalog_post_projection.configuration_invalid")

    def __reduce_ex__(self, protocol: object) -> NoReturn:
        del self, protocol
        _fail("fixed_catalog_post_projection.configuration_invalid")

    @classmethod
    def _from_reviewed_configuration(
        cls,
        *,
        clock: Callable[[], datetime],
        inventory_hmac_key: bytes,
        inventory_hmac_key_version: str,
        transform_key: bytes,
        transform_key_version: str,
        sanitizer_policy_bytes: bytes,
        inventory_policy_bytes: bytes,
        data_contract_bytes: bytes,
        source_snapshot_contract_bytes: bytes,
        post_projection_contract_bytes: bytes,
    ) -> SanitizerFixedCatalogPostProjectionAuthenticator:
        code = "fixed_catalog_post_projection.configuration_invalid"
        if cls is SanitizerFixedCatalogPostProjectionAuthenticator:
            try:
                return cls._build(
                    clock=clock,
                    inventory_hmac_key=inventory_hmac_key,
                    inventory_hmac_key_version=inventory_hmac_key_version,
                    transform_key=transform_key,
                    transform_key_version=transform_key_version,
                    sanitizer_policy_bytes=sanitizer_policy_bytes,
                    inventory_policy_bytes=inventory_policy_bytes,
                    data_contract_bytes=data_contract_bytes,
                    source_snapshot_contract_bytes=source_snapshot_contract_bytes,
                    post_projection_contract_bytes=post_projection_contract_bytes,
                )
            except Exception as error:
                code = _bounded_code(
                    error, "fixed_catalog_post_projection.configuration_invalid"
                )
        del cls, clock, inventory_hmac_key, inventory_hmac_key_version
        del transform_key, transform_key_version, sanitizer_policy_bytes
        del inventory_policy_bytes, data_contract_bytes
        del source_snapshot_contract_bytes, post_projection_contract_bytes
        _fail(code)

    @classmethod
    def _build(
        cls,
        *,
        clock: Callable[[], datetime],
        inventory_hmac_key: bytes,
        inventory_hmac_key_version: str,
        transform_key: bytes,
        transform_key_version: str,
        sanitizer_policy_bytes: bytes,
        inventory_policy_bytes: bytes,
        data_contract_bytes: bytes,
        source_snapshot_contract_bytes: bytes,
        post_projection_contract_bytes: bytes,
    ) -> SanitizerFixedCatalogPostProjectionAuthenticator:
        if (
            cls is not SanitizerFixedCatalogPostProjectionAuthenticator
            or not callable(clock)
            or type(inventory_hmac_key) is not bytes
            or not MINIMUM_KEY_BYTES <= len(inventory_hmac_key) <= MAXIMUM_KEY_BYTES
            or type(inventory_hmac_key_version) is not str
            or inventory_hmac_key_version != INVENTORY_HMAC_KEY_VERSION
            or type(transform_key) is not bytes
            or not MINIMUM_KEY_BYTES <= len(transform_key) <= MAXIMUM_KEY_BYTES
            or type(transform_key_version) is not str
            or _KEY_VERSION_RE.fullmatch(transform_key_version) is None
        ):
            _fail("fixed_catalog_post_projection.configuration_invalid")
        sanitizer_policy = _parse_pinned(
            sanitizer_policy_bytes, SANITIZER_POLICY_SHA256
        )
        inventory_policy = _parse_pinned(
            inventory_policy_bytes, INVENTORY_POLICY_SHA256
        )
        data_contract = _parse_pinned(data_contract_bytes, DATA_CONTRACT_SHA256)
        source_contract = _parse_pinned(
            source_snapshot_contract_bytes, SOURCE_SNAPSHOT_CONTRACT_SHA256
        )
        post_contract = _parse_pinned(
            post_projection_contract_bytes, POST_PROJECTION_CONTRACT_SHA256
        )
        try:
            projection = post_contract["projection"]
            contract_tables = projection["tables"]
            data_tables = data_contract["tables"]
            schemas = data_executor._retained_tables(inventory_policy)  # noqa: SLF001
            specs = tuple(
                (
                    name,
                    tuple(data_tables[name]["columns"].items()),
                    data_executor._identity_columns(schemas[name]),  # noqa: SLF001
                )
                for name in _FIXED_TABLES
            )
        except (KeyError, TypeError, ValueError):
            _fail("fixed_catalog_post_projection.policy_invalid")
        if (
            sanitizer_policy.get("policy_id")
            != "cosmaneura-isolated-restore-sanitizer-contract-v1"
            or source_contract.get("policy_id")
            != "cosmaneura-staging-sanitizer-source-snapshot-v1"
            or data_executor.SOURCE_SNAPSHOT_CONTRACT_SHA256
            != SOURCE_SNAPSHOT_CONTRACT_SHA256
            or source_snapshot.SOURCE_SNAPSHOT_CONTRACT_SHA256
            != SOURCE_SNAPSHOT_CONTRACT_SHA256
            or data_executor.HMAC_DOMAIN != _TRANSFORM_HMAC_ROOT
            or data_executor.UUID7_EPOCH_START_MS != _UUID7_EPOCH_START_MS
            or data_executor.UUID7_EPOCH_SPAN_MS != _UUID7_EPOCH_SPAN_MS
            or post_contract.get("policy_id")
            != "cosmaneura-staging-sanitizer-fixed-catalog-post-projection-v1"
            or post_contract.get("closed_world") is not True
            or post_contract.get("status", {}).get(
                "existing_executor_preflight_remains_terminal"
            )
            is not True
            or post_contract.get("status", {}).get("public_activation_failure")
            != ACTIVATION_FAILURE
            or post_contract.get("status", {}).get("activation_blockers")
            != ["transaction_adapter_unimplemented"]
            or post_contract.get("upstream")
            != {
                "sanitizer_policy_sha256": SANITIZER_POLICY_SHA256,
                "inventory_policy_sha256": INVENTORY_POLICY_SHA256,
                "data_contract_sha256": DATA_CONTRACT_SHA256,
                "source_snapshot_contract_sha256": SOURCE_SNAPSHOT_CONTRACT_SHA256,
            }
            or projection.get("table_count") != FIXED_CATALOG_TABLE_COUNT
            or projection.get("column_count") != FIXED_CATALOG_COLUMN_COUNT
            or projection.get("table_order") != "lexicographic"
            or projection.get("row_order") != "canonical-transformed-identifier-tuple"
            or tuple(contract_tables) != _FIXED_TABLES
            or sum(len(actions) for actions in contract_tables.values())
            != FIXED_CATALOG_COLUMN_COUNT
            or any(
                data_tables[name].get("disposition") != "fixed_catalog"
                or data_tables[name].get("row_action") != "rewrite_rows"
                or data_tables[name].get("columns") != contract_tables[name]
                or tuple(contract_tables[name])
                != tuple(column[0] for column in schemas[name]["columns"])
                for name in _FIXED_TABLES
            )
            or any(
                action != "preserve_verified_catalog"
                and not action.startswith("hmac_uuid7:typeid:")
                for actions in contract_tables.values()
                for action in actions.values()
            )
            or post_contract.get("authentication", {}).get("candidate_copy_domain")
            != data_executor.CANDIDATE_COPY_DOMAIN
            or post_contract.get("authentication", {}).get("projection_domain")
            != PROJECTION_DOMAIN
            or post_contract.get("authentication", {}).get("authority_domain")
            != AUTHORITY_DOMAIN
            or post_contract.get("authentication", {}).get("source_copy_protocol")
            != "pinned-source-snapshot-contract"
            or post_contract.get("authentication", {}).get("local_hmac_domain_prefix")
            != _TRANSFORM_HMAC_ROOT
            or post_contract.get("authentication", {}).get("local_frame")
            != "uint16be(domain_length)||domain||uint64be(value_length)||value"
            or post_contract.get("claims") != {**_POSITIVE_CLAIMS, **_NEGATIVE_CLAIMS}
            or any(
                post_contract.get("status", {}).get(name) is not False
                for name in _NEGATIVE_CLAIMS
            )
        ):
            _fail("fixed_catalog_post_projection.policy_invalid")
        engine = (
            data_executor.SanitizerDataContractExecutor._from_reviewed_configuration(
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
        )
        value = object.__new__(cls)
        object.__setattr__(value, "_clock", clock)
        object.__setattr__(value, "_last", None)
        object.__setattr__(value, "_inventory_key", inventory_hmac_key)
        object.__setattr__(value, "_transform_key", transform_key)
        object.__setattr__(value, "_transform_key_version", transform_key_version)
        object.__setattr__(value, "_engine", engine)
        object.__setattr__(value, "_fixed_specs", specs)
        return value

    def _read_clock(self) -> datetime:
        now = _utc(self._clock)
        if self._last is not None and now < self._last:
            _fail("fixed_catalog_post_projection.clock_regressed")
        object.__setattr__(self, "_last", now)
        return now

    def _copies(
        self,
        snapshot: source_snapshot.AuthenticatedSanitizerSourceSnapshot,
        candidate: data_executor.SanitizerCandidateDataset,
        now: datetime,
    ) -> tuple[
        source_snapshot._VerifiedSourceSnapshotCopy,  # noqa: SLF001
        data_executor._VerifiedFixedCatalogCandidateCopy,  # noqa: SLF001
    ]:
        source_copy = source_snapshot._copy_verified_tables_for_executor(  # noqa: SLF001
            snapshot, now=now, inventory_hmac_key=self._inventory_key
        )
        candidate_copy = self._engine._copy_verified_fixed_catalog_for_post_projection(  # noqa: SLF001
            candidate, now=now
        )
        self._validate_copies(source_copy, candidate_copy, now)
        if (
            source_copy._source_snapshot_identity != id(snapshot)  # noqa: SLF001
            or candidate_copy._candidate_identity != id(candidate)  # noqa: SLF001
            or candidate_copy._source_snapshot_identity  # noqa: SLF001
            != source_copy._source_snapshot_identity  # noqa: SLF001
            or candidate_copy._source_snapshot_type_identity  # noqa: SLF001
            != id(source_snapshot.AuthenticatedSanitizerSourceSnapshot)
            or candidate_copy._source_snapshot_seal_identity  # noqa: SLF001
            != id(source_snapshot._SNAPSHOT_SEAL)  # noqa: SLF001
            or candidate_copy._source_snapshot_contract_sha256  # noqa: SLF001
            != SOURCE_SNAPSHOT_CONTRACT_SHA256
            or not hmac.compare_digest(
                candidate_copy._source_snapshot_proof_sha256,  # noqa: SLF001
                source_copy._source_snapshot_proof_sha256,  # noqa: SLF001
            )
            or candidate_copy._authenticated_evidence  # noqa: SLF001
            is not source_copy._authenticated_evidence  # noqa: SLF001
        ):
            _fail("fixed_catalog_post_projection.authority_invalid")
        return source_copy, candidate_copy

    def _validate_copies(
        self,
        source_copy: source_snapshot._VerifiedSourceSnapshotCopy,  # noqa: SLF001
        candidate_copy: data_executor._VerifiedFixedCatalogCandidateCopy,  # noqa: SLF001
        now: datetime,
    ) -> None:
        if (
            type(source_copy) is not source_snapshot._VerifiedSourceSnapshotCopy  # noqa: SLF001
            or source_copy._seal is not source_snapshot._VERIFIED_COPY_SEAL  # noqa: SLF001
            or type(candidate_copy)
            is not data_executor._VerifiedFixedCatalogCandidateCopy  # noqa: SLF001
            or candidate_copy._seal  # noqa: SLF001
            is not data_executor._FIXED_CATALOG_COPY_SEAL  # noqa: SLF001
        ):
            _fail("fixed_catalog_post_projection.authority_invalid")
        source_snapshot._validate_verified_copy_at_current_time(  # noqa: SLF001
            source_copy, now=now, inventory_hmac_key=self._inventory_key
        )
        self._engine._validate_verified_fixed_catalog_copy_at_current_time(  # noqa: SLF001
            candidate_copy, now=now
        )

    def _projection_digest(
        self,
        source_copy: source_snapshot._VerifiedSourceSnapshotCopy,  # noqa: SLF001
        candidate_copy: data_executor._VerifiedFixedCatalogCandidateCopy,  # noqa: SLF001
    ) -> str:
        source_tables = dict(source_copy._tables)  # noqa: SLF001
        candidate_tables = dict(candidate_copy._tables)  # noqa: SLF001
        expected_projection, observed_projection = [], []
        for table, actions, identities in self._fixed_specs:
            expected_rows, observed_rows = [], []
            for row in source_tables[table]:
                projected = {
                    column: (
                        value
                        if action == "preserve_verified_catalog"
                        else _uuid7(
                            value,
                            key=self._transform_key,
                            domain=action.removeprefix("hmac_uuid7:"),
                        )
                    )
                    for column, action in actions
                    for value in (row[column],)
                }
                expected_rows.append(
                    (
                        _canonical([_token(projected[name]) for name in identities]),
                        [[name, _token(projected[name])] for name, _action in actions],
                    )
                )
            for row in candidate_tables[table]:
                if tuple(row) != tuple(name for name, _action in actions):
                    _fail("fixed_catalog_post_projection.projection_invalid")
                observed_rows.append(
                    (
                        _canonical([_token(row[name]) for name in identities]),
                        [[name, _token(row[name])] for name, _action in actions],
                    )
                )
            expected_rows.sort(key=lambda item: item[0])
            observed_rows.sort(key=lambda item: item[0])
            expected_projection.append([table, [item[1] for item in expected_rows]])
            observed_projection.append([table, [item[1] for item in observed_rows]])
        header = [
            ["contract_sha256", POST_PROJECTION_CONTRACT_SHA256],
            [
                "source_snapshot_proof_sha256",
                source_copy._source_snapshot_proof_sha256,  # noqa: SLF001
            ],
            [
                "candidate_dataset_proof_sha256",
                candidate_copy._candidate_proof_sha256,  # noqa: SLF001
            ],
        ]
        expected = _digest(
            self._transform_key,
            PROJECTION_DOMAIN,
            _canonical([header, expected_projection]),
        )
        observed = _digest(
            self._transform_key,
            PROJECTION_DOMAIN,
            _canonical([header, observed_projection]),
        )
        if not hmac.compare_digest(expected, observed):
            _fail("fixed_catalog_post_projection.projection_invalid")
        return expected

    def authenticate(
        self,
        *,
        authenticated_source_snapshot: (
            source_snapshot.AuthenticatedSanitizerSourceSnapshot
        ),
        sanitizer_candidate: data_executor.SanitizerCandidateDataset,
    ) -> AuthenticatedFixedCatalogPostProjection:
        code = "fixed_catalog_post_projection.configuration_invalid"
        if type(self) is SanitizerFixedCatalogPostProjectionAuthenticator:
            try:
                return self._authenticate(
                    authenticated_source_snapshot, sanitizer_candidate
                )
            except Exception as error:
                code = _bounded_code(
                    error, "fixed_catalog_post_projection.authority_invalid"
                )
        del self, authenticated_source_snapshot, sanitizer_candidate
        _fail(code)

    def _authenticate(
        self,
        snapshot: source_snapshot.AuthenticatedSanitizerSourceSnapshot,
        candidate: data_executor.SanitizerCandidateDataset,
    ) -> AuthenticatedFixedCatalogPostProjection:
        if (
            type(self) is not SanitizerFixedCatalogPostProjectionAuthenticator
            or type(snapshot)
            is not source_snapshot.AuthenticatedSanitizerSourceSnapshot
            or type(candidate) is not data_executor.SanitizerCandidateDataset
        ):
            _fail("fixed_catalog_post_projection.authority_invalid")
        first_source, first_candidate = self._copies(
            snapshot, candidate, self._read_clock()
        )
        projection_proof = self._projection_digest(first_source, first_candidate)
        completed_at = self._read_clock()
        self._validate_copies(first_source, first_candidate, completed_at)
        final_source, final_candidate = self._copies(snapshot, candidate, completed_at)
        final_projection = self._projection_digest(final_source, final_candidate)
        if (
            first_source._authenticated_evidence  # noqa: SLF001
            is not final_source._authenticated_evidence  # noqa: SLF001
            or first_source._source_snapshot_identity  # noqa: SLF001
            != final_source._source_snapshot_identity  # noqa: SLF001
            or first_source._source_snapshot_proof_sha256  # noqa: SLF001
            != final_source._source_snapshot_proof_sha256  # noqa: SLF001
            or first_candidate._candidate_identity  # noqa: SLF001
            != final_candidate._candidate_identity  # noqa: SLF001
            or first_candidate._candidate_proof_sha256  # noqa: SLF001
            != final_candidate._candidate_proof_sha256  # noqa: SLF001
            or first_candidate._authenticated_evidence  # noqa: SLF001
            is not final_candidate._authenticated_evidence  # noqa: SLF001
            or not hmac.compare_digest(projection_proof, final_projection)
        ):
            _fail("fixed_catalog_post_projection.authority_invalid")
        return self._mint(
            source_copy=final_source,
            candidate_copy=final_candidate,
            projection_proof=final_projection,
            authenticated_at=completed_at,
            row_count=sum(
                len(rows)
                for _name, rows in final_candidate._tables  # noqa: SLF001
            ),
        )

    def _mint(
        self,
        *,
        source_copy: source_snapshot._VerifiedSourceSnapshotCopy,  # noqa: SLF001
        candidate_copy: data_executor._VerifiedFixedCatalogCandidateCopy,  # noqa: SLF001
        projection_proof: str,
        authenticated_at: datetime,
        row_count: int,
    ) -> AuthenticatedFixedCatalogPostProjection:
        value = object.__new__(AuthenticatedFixedCatalogPostProjection)
        public = {
            "fixed_catalog_table_count": FIXED_CATALOG_TABLE_COUNT,
            "fixed_catalog_column_count": FIXED_CATALOG_COLUMN_COUNT,
            "fixed_catalog_row_count": row_count,
            "transform_key_version": self._transform_key_version,
            "authenticated_at": authenticated_at,
            **_POSITIVE_CLAIMS,
            **_NEGATIVE_CLAIMS,
        }
        for name, item in public.items():
            object.__setattr__(value, name, item)
        object.__setattr__(
            value,
            "_authenticated_evidence",
            source_copy._authenticated_evidence,  # noqa: SLF001
        )
        object.__setattr__(
            value,
            "_source_snapshot_identity",
            source_copy._source_snapshot_identity,  # noqa: SLF001
        )
        object.__setattr__(
            value,
            "_source_snapshot_proof_sha256",
            source_copy._source_snapshot_proof_sha256,  # noqa: SLF001
        )
        object.__setattr__(
            value,
            "_candidate_dataset_identity",
            candidate_copy._candidate_identity,  # noqa: SLF001
        )
        object.__setattr__(
            value,
            "_candidate_dataset_proof_sha256",
            candidate_copy._candidate_proof_sha256,  # noqa: SLF001
        )
        object.__setattr__(
            value,
            "_candidate_copy_proof_sha256",
            candidate_copy._proof_sha256,  # noqa: SLF001
        )
        object.__setattr__(value, "_projection_proof_sha256", projection_proof)
        object.__setattr__(value, "_contract_sha256", POST_PROJECTION_CONTRACT_SHA256)
        object.__setattr__(value, "_seal", _AUTHORITY_SEAL)
        object.__setattr__(
            value,
            "_proof_sha256",
            _digest(self._transform_key, AUTHORITY_DOMAIN, _authority_material(value)),
        )
        self._validate(value, authenticated_at)
        return value

    def validate(self, value: AuthenticatedFixedCatalogPostProjection) -> None:
        code = "fixed_catalog_post_projection.configuration_invalid"
        if type(self) is SanitizerFixedCatalogPostProjectionAuthenticator:
            try:
                self._validate(value, self._read_clock())
                return
            except Exception as error:
                code = _bounded_code(
                    error, "fixed_catalog_post_projection.output_invalid"
                )
        del self, value
        _fail(code)

    def _validate(
        self, value: AuthenticatedFixedCatalogPostProjection, now: datetime
    ) -> None:
        if type(value) is not AuthenticatedFixedCatalogPostProjection:
            _fail("fixed_catalog_post_projection.output_invalid")
        if (
            value._seal is not _AUTHORITY_SEAL
            or type(value._authenticated_evidence)
            is not evidence_authority.AuthenticatedSanitizerEvidence
            or value._authenticated_evidence._seal  # noqa: SLF001
            is not evidence_authority._EVIDENCE_SEAL  # noqa: SLF001
            or type(value.authenticated_at) is not datetime
            or value.authenticated_at.tzinfo is not UTC
            or value.authenticated_at.microsecond != 0
            or value.authenticated_at.fold != 0
            or value.authenticated_at > now
            or type(value.fixed_catalog_table_count) is not int
            or value.fixed_catalog_table_count != FIXED_CATALOG_TABLE_COUNT
            or type(value.fixed_catalog_column_count) is not int
            or value.fixed_catalog_column_count != FIXED_CATALOG_COLUMN_COUNT
            or type(value.fixed_catalog_row_count) is not int
            or value.fixed_catalog_row_count < 0
            or type(value.transform_key_version) is not str
            or value.transform_key_version != self._transform_key_version
            or any(
                type(getattr(value, name)) is not bool
                or getattr(value, name) is not expected
                for name, expected in {
                    **_POSITIVE_CLAIMS,
                    **_NEGATIVE_CLAIMS,
                }.items()
            )
            or any(
                type(item) is not int or item <= 0
                for item in (
                    value._source_snapshot_identity,
                    value._candidate_dataset_identity,
                )
            )
            or any(
                type(item) is not str or _HEX_256_RE.fullmatch(item) is None
                for item in (
                    value._source_snapshot_proof_sha256,
                    value._candidate_dataset_proof_sha256,
                    value._candidate_copy_proof_sha256,
                    value._projection_proof_sha256,
                    value._contract_sha256,
                    value._proof_sha256,
                )
            )
            or value._contract_sha256 != POST_PROJECTION_CONTRACT_SHA256
            or not hmac.compare_digest(
                value._proof_sha256,
                _digest(
                    self._transform_key, AUTHORITY_DOMAIN, _authority_material(value)
                ),
            )
        ):
            _fail("fixed_catalog_post_projection.output_invalid")
        self._engine._reauthenticate(value._authenticated_evidence, now)  # noqa: SLF001
