from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, NoReturn

try:
    from scripts import (
        staging_restore_sanitizer_data_contract_executor as data_executor,
    )
    from scripts import (
        staging_restore_sanitizer_fixed_catalog_post_projection as post_projection,
    )
except ModuleNotFoundError:  # Direct import compatibility for local review.
    import staging_restore_sanitizer_data_contract_executor as data_executor
    import staging_restore_sanitizer_fixed_catalog_post_projection as post_projection


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
TRANSACTION_PLAN_CONTRACT_SHA256 = (
    "374fefc36125a3c5e74d54e4fd67b8b92c229c256aaed6fca19ef4b13b172732"
)
ACTIVATION_FAILURE = "activation.data_contract_executor_unimplemented"
SCHEMA_PROFILE = "pre"
SCHEMA_REVISION = "89c31f0ae275"
SCHEMA_DESCRIPTOR_SHA256 = (
    "b6dd67990533bec013f9db94c9c24940fabf9388ba97b585db45db24ca786849"
)
APPLICATION_TABLE_COUNT, REWRITE_TABLE_COUNT, REWRITE_COLUMN_COUNT = 62, 19, 204
DISCARD_TABLE_COUNT, DISCARD_COLUMN_COUNT, REQUIRED_EMPTY_TABLE_COUNT = 10, 139, 33
OPERATION_PROJECTION_DOMAIN = "transaction-plan:operation-projection"
AUTHORITY_DOMAIN = "transaction-plan:authority"
TRANSACTION_PLAN_EXECUTION_HANDOFF_COPY_DOMAIN = (
    "transaction-plan:execution-handoff-copy"
)
INSERT_TABLE_ORDER = (
    "authority_actor",
    "authority_person",
    "authority_tenant",
    "baa_version",
    "doctor",
    "drug_reference",
    "provider_organization",
    "authority_actor_external_identity",
    "authority_person_binding",
    "authority_tenant_membership",
    "doctor_note_schema",
    "lab_range_set",
    "patient",
    "practice",
    "appointment",
    "ehr_sandbox_patient_designation",
    "note_type_customization",
    "ehr_sandbox_record_mapping",
    "synthetic_preview_appointment_fixture",
)
_ZERO = timedelta(0)
_HEX_256_RE = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
_KEY_VERSION_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_POSITIVE_CLAIMS = dict.fromkeys(
    "source_snapshot_authenticated candidate_dataset_authenticated fixed_catalog_post_projection_authenticated post_projection_authenticated transaction_plan_authenticated".split(),
    True,
)
_NEGATIVE_CLAIMS = dict.fromkeys(
    "database_connection_authority database_mutation_authority evidence_publication_authority executable live_provider_calls_enabled migration_authority provider_calls_permitted provider_facts_authenticated runtime_authority sanitizer_authority transaction_committed transaction_executed workflow_integrated".split(),
    False,
)
_PUBLIC_FIXED = {
    "schema_profile": SCHEMA_PROFILE,
    "schema_revision": SCHEMA_REVISION,
    "schema_descriptor_sha256": SCHEMA_DESCRIPTOR_SHA256,
    "control_table": "alembic_version",
    "control_table_exact_rows": 1,
    "transaction_isolation": "SERIALIZABLE",
    "transaction_read_only": False,
    "connection_autocommit": False,
    "synchronous_commit": "on",
    "retry_permitted": False,
    "lock_control_table": True,
    "verify_revision_under_lock": True,
    "unexpected_on_truncate_triggers": "reject",
    "truncate_table_count": APPLICATION_TABLE_COUNT,
    "insert_table_count": REWRITE_TABLE_COUNT,
    "insert_column_count": REWRITE_COLUMN_COUNT,
    "discard_table_count": DISCARD_TABLE_COUNT,
    "discard_column_count": DISCARD_COLUMN_COUNT,
    "required_empty_table_count": REQUIRED_EMPTY_TABLE_COUNT,
    "operation_count": 1 + REWRITE_TABLE_COUNT,
    **_POSITIVE_CLAIMS,
    **_NEGATIVE_CLAIMS,
}
_PLAN_PUBLIC_NAMES = (
    *_PUBLIC_FIXED,
    "planned_insert_row_count",
    "transform_key_version",
    "planned_at",
)
_HANDOFF_PUBLIC_NAMES = (*_PLAN_PUBLIC_NAMES, "copied_at")
_FAILURE_CODES = frozenset(
    f"transaction_plan.{suffix}"
    for suffix in "authority_invalid candidate_invalid clock_invalid clock_regressed configuration_invalid output_invalid policy_invalid target_expired".split()
)
_TRUNCATE_SEAL, _INSERT_SEAL, _PLAN_SEAL, _EXECUTION_HANDOFF_SEAL = (
    object(),
    object(),
    object(),
    object(),
)


class SanitizerTransactionPlanFailure(RuntimeError):
    """Bounded refusal without table-row values or key material."""


def _fail(code: str) -> NoReturn:
    raise SanitizerTransactionPlanFailure(code)


def _bounded_code(error: Exception, fallback: str) -> str:
    if type(error) is SanitizerTransactionPlanFailure and error.args in tuple(
        (code,) for code in _FAILURE_CODES
    ):
        return str(error.args[0])
    value: str = (
        error.args[0] if len(error.args) == 1 and type(error.args[0]) is str else ""
    )
    if type(error) is post_projection.SanitizerFixedCatalogPostProjectionFailure:
        return {
            "fixed_catalog_post_projection.clock_regressed": "transaction_plan.clock_regressed",
            "fixed_catalog_post_projection.target_expired": "transaction_plan.target_expired",
        }.get(value, "transaction_plan.authority_invalid")
    if type(error) is data_executor.SanitizerDataContractExecutorFailure:
        return {
            "data_contract_executor.clock_regressed": "transaction_plan.clock_regressed",
            "data_contract_executor.target_expired": "transaction_plan.target_expired",
        }.get(value, "transaction_plan.candidate_invalid")
    return fallback


def _utc(clock: Callable[[], datetime]) -> datetime:
    try:
        value = clock()
    except Exception:
        _fail("transaction_plan.clock_invalid")
    if (
        type(value) is not datetime
        or value.tzinfo is not UTC
        or value.utcoffset() != _ZERO
        or value.microsecond
        or value.fold
    ):
        _fail("transaction_plan.clock_invalid")
    return value


def _parse_pinned(raw: bytes, digest: str) -> dict[str, Any]:
    if type(raw) is not bytes or not hmac.compare_digest(
        hashlib.sha256(raw).hexdigest(), digest
    ):
        _fail("transaction_plan.policy_invalid")

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for name, value in items:
            if name in result:
                _fail("transaction_plan.policy_invalid")
            result[name] = value
        return result

    try:
        value = json.loads(
            raw,
            object_pairs_hook=pairs,
            parse_constant=lambda _: _fail("transaction_plan.policy_invalid"),
        )
    except SanitizerTransactionPlanFailure:
        raise
    except (UnicodeDecodeError, ValueError, RecursionError):
        _fail("transaction_plan.policy_invalid")
    if type(value) is not dict:
        _fail("transaction_plan.policy_invalid")
    return value


def _canonical(value: Any) -> bytes:
    try:
        return data_executor._canonical(value)  # noqa: SLF001
    except Exception:
        _fail("transaction_plan.output_invalid")


def _digest(key: bytes, domain: str, value: bytes) -> str:
    if (
        type(key) is not bytes
        or domain
        not in {
            OPERATION_PROJECTION_DOMAIN,
            AUTHORITY_DOMAIN,
            TRANSACTION_PLAN_EXECUTION_HANDOFF_COPY_DOMAIN,
        }
        or type(value) is not bytes
    ):
        _fail("transaction_plan.configuration_invalid")
    encoded = f"{data_executor.HMAC_DOMAIN}:{domain}".encode()
    framed = (
        len(encoded).to_bytes(2, "big")
        + encoded
        + len(value).to_bytes(8, "big")
        + value
    )
    return hmac.new(key, framed, hashlib.sha256).hexdigest()


class _Opaque:
    __slots__ = ()

    def __init__(self) -> None:
        _fail("transaction_plan.output_invalid")

    def __setattr__(self, name: str, value: object) -> NoReturn:
        del name, value
        _fail("transaction_plan.output_invalid")

    def __delattr__(self, name: str) -> NoReturn:
        del name
        _fail("transaction_plan.output_invalid")

    def __repr__(self) -> str:
        return f"<{type(self).__name__} sealed non-executable>"

    def __reduce__(self) -> NoReturn:
        del self
        _fail("transaction_plan.output_invalid")

    def __reduce_ex__(self, protocol: object) -> NoReturn:
        del self, protocol
        _fail("transaction_plan.output_invalid")


class _TruncatePlanOperation(_Opaque):
    __slots__ = (
        "_tables",
        "_cascade",
        "_restart_identity",
        "_triggers_disabled",
        "_seal",
    )
    _tables: tuple[str, ...]
    _cascade: bool
    _restart_identity: bool
    _triggers_disabled: bool
    _seal: object


class _InsertPlanOperation(_Opaque):
    __slots__ = (
        "_table",
        "_columns",
        "_values",
        "_whole_table_batch",
        "_on_conflict_permitted",
        "_seal",
    )
    _table: str
    _columns: tuple[str, ...]
    _values: tuple[tuple[Any, ...], ...]
    _whole_table_batch: bool
    _on_conflict_permitted: bool
    _seal: object


class AuthenticatedSanitizerTransactionPlan(_Opaque):
    __slots__ = (
        *_PUBLIC_FIXED,
        "planned_insert_row_count",
        "transform_key_version",
        "planned_at",
        "_truncate",
        "_inserts",
        "_authenticated_post_projection",
        "_candidate_dataset_identity",
        "_candidate_dataset_proof_sha256",
        "_candidate_dataset_contract_sha256",
        "_candidate_copy_proof_sha256",
        "_source_snapshot_identity",
        "_source_snapshot_proof_sha256",
        "_source_snapshot_contract_sha256",
        "_operation_projection_sha256",
        "_contract_sha256",
        "_proof_sha256",
        "_seal",
    )
    schema_profile: str
    schema_revision: str
    schema_descriptor_sha256: str
    control_table: str
    control_table_exact_rows: int
    transaction_isolation: str
    transaction_read_only: bool
    connection_autocommit: bool
    synchronous_commit: str
    retry_permitted: bool
    lock_control_table: bool
    verify_revision_under_lock: bool
    unexpected_on_truncate_triggers: str
    truncate_table_count: int
    insert_table_count: int
    insert_column_count: int
    discard_table_count: int
    discard_column_count: int
    required_empty_table_count: int
    operation_count: int
    source_snapshot_authenticated: bool
    candidate_dataset_authenticated: bool
    fixed_catalog_post_projection_authenticated: bool
    post_projection_authenticated: bool
    transaction_plan_authenticated: bool
    database_connection_authority: bool
    database_mutation_authority: bool
    evidence_publication_authority: bool
    executable: bool
    live_provider_calls_enabled: bool
    migration_authority: bool
    provider_calls_permitted: bool
    provider_facts_authenticated: bool
    runtime_authority: bool
    sanitizer_authority: bool
    transaction_committed: bool
    transaction_executed: bool
    workflow_integrated: bool
    planned_insert_row_count: int
    transform_key_version: str
    planned_at: datetime
    _truncate: _TruncatePlanOperation
    _inserts: tuple[_InsertPlanOperation, ...]
    _authenticated_post_projection: (
        post_projection.AuthenticatedFixedCatalogPostProjection
    )
    _candidate_dataset_identity: int
    _candidate_dataset_proof_sha256: str
    _candidate_dataset_contract_sha256: str
    _candidate_copy_proof_sha256: str
    _source_snapshot_identity: int
    _source_snapshot_proof_sha256: str
    _source_snapshot_contract_sha256: str
    _operation_projection_sha256: str
    _contract_sha256: str
    _proof_sha256: str
    _seal: object


class _VerifiedTransactionPlanExecutionHandoff(_Opaque):
    __slots__ = (
        *_PUBLIC_FIXED,
        "planned_insert_row_count",
        "transform_key_version",
        "planned_at",
        "copied_at",
        "_truncate",
        "_inserts",
        "_authenticated_post_projection",
        "_candidate_dataset_identity",
        "_candidate_dataset_proof_sha256",
        "_candidate_dataset_contract_sha256",
        "_candidate_copy_proof_sha256",
        "_source_snapshot_identity",
        "_source_snapshot_proof_sha256",
        "_source_snapshot_contract_sha256",
        "_operation_projection_sha256",
        "_contract_sha256",
        "_transaction_plan_identity",
        "_transaction_plan_type_identity",
        "_transaction_plan_seal_identity",
        "_transaction_plan_proof_sha256",
        "_transaction_plan_operation_identities",
        "_transaction_plan_operation_type_identities",
        "_transaction_plan_operation_seal_identities",
        "_proof_sha256",
        "_seal",
    )
    planned_insert_row_count: int
    transform_key_version: str
    planned_at: datetime
    copied_at: datetime
    _truncate: _TruncatePlanOperation
    _inserts: tuple[_InsertPlanOperation, ...]
    _authenticated_post_projection: (
        post_projection.AuthenticatedFixedCatalogPostProjection
    )
    _candidate_dataset_identity: int
    _candidate_dataset_proof_sha256: str
    _candidate_dataset_contract_sha256: str
    _candidate_copy_proof_sha256: str
    _source_snapshot_identity: int
    _source_snapshot_proof_sha256: str
    _source_snapshot_contract_sha256: str
    _operation_projection_sha256: str
    _contract_sha256: str
    _transaction_plan_identity: int
    _transaction_plan_type_identity: int
    _transaction_plan_seal_identity: int
    _transaction_plan_proof_sha256: str
    _transaction_plan_operation_identities: tuple[int, ...]
    _transaction_plan_operation_type_identities: tuple[int, ...]
    _transaction_plan_operation_seal_identities: tuple[int, ...]
    _proof_sha256: str
    _seal: object


def _set(value: object, **fields: Any) -> None:
    for name, item in fields.items():
        object.__setattr__(value, name, item)


def _operation_material(
    truncate: _TruncatePlanOperation, inserts: tuple[_InsertPlanOperation, ...]
) -> tuple[Any, ...]:
    return (
        (
            "truncate",
            truncate._tables,
            truncate._cascade,
            truncate._restart_identity,
            truncate._triggers_disabled,
        ),
        (
            "inserts",
            tuple(
                (
                    item._table,
                    item._columns,
                    item._values,
                    item._whole_table_batch,
                    item._on_conflict_permitted,
                )
                for item in inserts
            ),
        ),
    )


def _authority_material(value: AuthenticatedSanitizerTransactionPlan) -> bytes:
    authority = value._authenticated_post_projection
    public = tuple((name, getattr(value, name)) for name in _PLAN_PUBLIC_NAMES)
    return _canonical(
        (
            ("plan_identity", id(value)),
            ("contract_sha256", value._contract_sha256),
            ("public", public),
            (
                "post_projection",
                (
                    id(authority),
                    id(type(authority)),
                    id(authority._seal),
                    authority._proof_sha256,
                ),
            ),  # noqa: SLF001
            (
                "candidate",
                (
                    value._candidate_dataset_identity,
                    value._candidate_dataset_proof_sha256,
                    value._candidate_dataset_contract_sha256,
                    value._candidate_copy_proof_sha256,
                ),
            ),
            (
                "source",
                (
                    value._source_snapshot_identity,
                    value._source_snapshot_proof_sha256,
                    value._source_snapshot_contract_sha256,
                ),
            ),
            ("operation_projection_sha256", value._operation_projection_sha256),
        )
    )


def _plan_authority_material_from_handoff(
    value: _VerifiedTransactionPlanExecutionHandoff,
) -> bytes:
    authority = value._authenticated_post_projection
    public = tuple((name, getattr(value, name)) for name in _PLAN_PUBLIC_NAMES)
    return _canonical(
        (
            ("plan_identity", value._transaction_plan_identity),
            ("contract_sha256", value._contract_sha256),
            ("public", public),
            (
                "post_projection",
                (
                    id(authority),
                    id(type(authority)),
                    id(authority._seal),
                    authority._proof_sha256,
                ),
            ),  # noqa: SLF001
            (
                "candidate",
                (
                    value._candidate_dataset_identity,
                    value._candidate_dataset_proof_sha256,
                    value._candidate_dataset_contract_sha256,
                    value._candidate_copy_proof_sha256,
                ),
            ),
            (
                "source",
                (
                    value._source_snapshot_identity,
                    value._source_snapshot_proof_sha256,
                    value._source_snapshot_contract_sha256,
                ),
            ),
            ("operation_projection_sha256", value._operation_projection_sha256),
        )
    )


def _operation_identities(
    truncate: _TruncatePlanOperation, inserts: tuple[_InsertPlanOperation, ...]
) -> tuple[tuple[int, ...], tuple[int, ...], tuple[int, ...]]:
    operations = (truncate, *inserts)
    return (
        tuple(id(item) for item in operations),
        tuple(id(type(item)) for item in operations),
        tuple(id(item._seal) for item in operations),
    )


def _transaction_plan_copy_source_material(
    value: AuthenticatedSanitizerTransactionPlan,
) -> bytes:
    identities, type_identities, seal_identities = _operation_identities(
        value._truncate, value._inserts
    )
    return _canonical(
        (
            ("plan_identity", id(value)),
            ("plan_type_identity", id(type(value))),
            ("plan_seal_identity", id(value._seal)),
            ("plan_proof_sha256", value._proof_sha256),
            (
                "plan_authority_material_sha256",
                hashlib.sha256(_authority_material(value)).hexdigest(),
            ),
            ("operation_identities", identities),
            ("operation_type_identities", type_identities),
            ("operation_seal_identities", seal_identities),
            ("operations", _operation_material(value._truncate, value._inserts)),
        )
    )


def _execution_handoff_material(
    value: _VerifiedTransactionPlanExecutionHandoff,
) -> bytes:
    authority = value._authenticated_post_projection
    identities, type_identities, seal_identities = _operation_identities(
        value._truncate, value._inserts
    )
    public = tuple((name, getattr(value, name)) for name in _HANDOFF_PUBLIC_NAMES)
    return _canonical(
        (
            (
                "handoff",
                (id(value), id(type(value)), id(value._seal)),
            ),
            ("contract_sha256", value._contract_sha256),
            ("public", public),
            (
                "transaction_plan",
                (
                    value._transaction_plan_identity,
                    value._transaction_plan_type_identity,
                    value._transaction_plan_seal_identity,
                    value._transaction_plan_proof_sha256,
                    value._transaction_plan_operation_identities,
                    value._transaction_plan_operation_type_identities,
                    value._transaction_plan_operation_seal_identities,
                    _plan_authority_material_from_handoff(value).decode("utf-8"),
                ),
            ),
            (
                "post_projection",
                (
                    id(authority),
                    id(type(authority)),
                    id(authority._seal),
                    authority._proof_sha256,
                    post_projection._authority_material(authority).decode("utf-8"),  # noqa: SLF001
                    id(authority._authenticated_evidence),  # noqa: SLF001
                    id(type(authority._authenticated_evidence)),  # noqa: SLF001
                    id(authority._authenticated_evidence._seal),  # noqa: SLF001
                    authority._authenticated_evidence._proof_sha256,  # noqa: SLF001
                ),
            ),
            (
                "candidate",
                (
                    value._candidate_dataset_identity,
                    value._candidate_dataset_proof_sha256,
                    value._candidate_dataset_contract_sha256,
                    value._candidate_copy_proof_sha256,
                ),
            ),
            (
                "source",
                (
                    value._source_snapshot_identity,
                    value._source_snapshot_proof_sha256,
                    value._source_snapshot_contract_sha256,
                ),
            ),
            ("operation_projection_sha256", value._operation_projection_sha256),
            (
                "copied_operations",
                (
                    identities,
                    type_identities,
                    seal_identities,
                    _operation_material(value._truncate, value._inserts),
                ),
            ),
        )
    )


class SanitizerSerializableTransactionPlanner:
    __slots__ = "_clock _columns _engine _key _key_version _last _post_authenticator _pre_tables".split()
    _clock: Callable[[], datetime]
    _columns: tuple[tuple[str, tuple[str, ...]], ...]
    _engine: data_executor.SanitizerDataContractExecutor
    _key: bytes
    _key_version: str
    _last: datetime | None
    _post_authenticator: (
        post_projection.SanitizerFixedCatalogPostProjectionAuthenticator
    )
    _pre_tables: tuple[str, ...]

    def __init__(self) -> None:
        _fail("transaction_plan.configuration_invalid")

    def __setattr__(self, name: str, value: object) -> NoReturn:
        del name, value
        _fail("transaction_plan.configuration_invalid")

    def __delattr__(self, name: str) -> NoReturn:
        del name
        _fail("transaction_plan.configuration_invalid")

    def __repr__(self) -> str:
        return "<SanitizerSerializableTransactionPlanner dormant in-memory-only>"

    def __reduce__(self) -> NoReturn:
        del self
        _fail("transaction_plan.configuration_invalid")

    def __reduce_ex__(self, protocol: object) -> NoReturn:
        del self, protocol
        _fail("transaction_plan.configuration_invalid")

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
        transaction_plan_contract_bytes: bytes,
    ) -> SanitizerSerializableTransactionPlanner:
        code = "transaction_plan.configuration_invalid"
        if cls is SanitizerSerializableTransactionPlanner:
            try:
                return cls._build(
                    clock,
                    inventory_hmac_key,
                    inventory_hmac_key_version,
                    transform_key,
                    transform_key_version,
                    sanitizer_policy_bytes,
                    inventory_policy_bytes,
                    data_contract_bytes,
                    source_snapshot_contract_bytes,
                    post_projection_contract_bytes,
                    transaction_plan_contract_bytes,
                )
            except Exception as error:
                code = _bounded_code(error, code)
        del cls, clock, inventory_hmac_key, inventory_hmac_key_version, transform_key
        del transform_key_version, sanitizer_policy_bytes, inventory_policy_bytes
        del data_contract_bytes, source_snapshot_contract_bytes
        del post_projection_contract_bytes, transaction_plan_contract_bytes
        _fail(code)

    @classmethod
    def _build(
        cls,
        clock: Callable[[], datetime],
        inventory_key: bytes,
        inventory_version: str,
        transform_key: bytes,
        transform_version: str,
        sanitizer_raw: bytes,
        inventory_raw: bytes,
        data_raw: bytes,
        source_raw: bytes,
        post_raw: bytes,
        plan_raw: bytes,
    ) -> SanitizerSerializableTransactionPlanner:
        if (
            cls is not SanitizerSerializableTransactionPlanner
            or not callable(clock)
            or type(transform_key) is not bytes
            or not data_executor.MINIMUM_KEY_BYTES
            <= len(transform_key)
            <= data_executor.MAXIMUM_KEY_BYTES
            or type(transform_version) is not str
            or _KEY_VERSION_RE.fullmatch(transform_version) is None
            or type(inventory_key) is not bytes
            or not data_executor.MINIMUM_KEY_BYTES
            <= len(inventory_key)
            <= data_executor.MAXIMUM_KEY_BYTES
            or type(inventory_version) is not str
            or inventory_version != data_executor.INVENTORY_HMAC_KEY_VERSION
        ):
            _fail("transaction_plan.configuration_invalid")
        sanitizer = _parse_pinned(sanitizer_raw, SANITIZER_POLICY_SHA256)
        inventory = _parse_pinned(inventory_raw, INVENTORY_POLICY_SHA256)
        data = _parse_pinned(data_raw, DATA_CONTRACT_SHA256)
        source = _parse_pinned(source_raw, SOURCE_SNAPSHOT_CONTRACT_SHA256)
        post = _parse_pinned(post_raw, POST_PROJECTION_CONTRACT_SHA256)
        contract = _parse_pinned(plan_raw, TRANSACTION_PLAN_CONTRACT_SHA256)
        try:
            profile = inventory["profiles"][SCHEMA_PROFILE]
            excluded = set(profile["excluded_tables"])
            exclusions = profile["column_exclusions"]
            all_tables = inventory["database"]["tables"]
            pre_tables = tuple(sorted(set(all_tables) - excluded))
            schemas = {
                name: tuple(
                    column[0]
                    for column in all_tables[name]["columns"]
                    if column[0] not in set(exclusions.get(name, ()))
                )
                for name in pre_tables
            }
            table_contracts = data["tables"]
            rewritten = tuple(data["row_actions"]["rewrite_rows"])
            discarded = tuple(data["row_actions"]["discard_rows"])
            retained, required_empty = (
                set(table_contracts),
                set(pre_tables) - set(table_contracts),
            )
            columns = tuple(
                (name, tuple(table_contracts[name]["columns"]))
                for name in INSERT_TABLE_ORDER
            )
            positions = {name: index for index, name in enumerate(INSERT_TABLE_ORDER)}
            edges = [
                (item["source"][0].split(".", 1)[0], item["target"][0].split(".", 1)[0])
                for item in data["foreign_keys"]
            ]
        except Exception:
            _fail("transaction_plan.policy_invalid")
        upstream = {
            "sanitizer_policy_sha256": SANITIZER_POLICY_SHA256,
            "inventory_policy_sha256": INVENTORY_POLICY_SHA256,
            "data_contract_sha256": DATA_CONTRACT_SHA256,
            "source_snapshot_contract_sha256": SOURCE_SNAPSHOT_CONTRACT_SHA256,
            "post_projection_contract_sha256": POST_PROJECTION_CONTRACT_SHA256,
        }
        schema = {
            "profile": SCHEMA_PROFILE,
            "revision": SCHEMA_REVISION,
            "descriptor_sha256": SCHEMA_DESCRIPTOR_SHA256,
            "namespace": "public",
            "control_table": "alembic_version",
            "control_table_exact_rows": 1,
            "application_table_count": APPLICATION_TABLE_COUNT,
            "rewrite_table_count": REWRITE_TABLE_COUNT,
            "rewrite_column_count": REWRITE_COLUMN_COUNT,
            "discard_table_count": DISCARD_TABLE_COUNT,
            "discard_column_count": DISCARD_COLUMN_COUNT,
            "required_empty_table_count": REQUIRED_EMPTY_TABLE_COUNT,
            "post_profile_permitted": False,
        }
        transaction = {
            "isolation": "SERIALIZABLE",
            "read_only": False,
            "autocommit": False,
            "synchronous_commit": "on",
            "retry_permitted": False,
            "lock_control_table": True,
            "verify_revision_under_lock": True,
            "triggers_disabled": False,
            "unexpected_on_truncate_triggers": "reject",
            "truncate": {
                "operation_count": 1,
                "table_order": "lexicographic",
                "cascade": False,
                "restart_identity": False,
            },
            "inserts": {
                "operation_count": REWRITE_TABLE_COUNT,
                "table_order": list(INSERT_TABLE_ORDER),
                "row_batch": "whole-table-single-logical-operation",
                "authority_person_self_reference_whole_table_batch": True,
                "on_conflict_permitted": False,
            },
            "ddl_permitted": False,
            "migration_permitted": False,
            "commit_operation_permitted": False,
            "rollback_operation_permitted": False,
        }
        execution_handoff = {
            "kind": "sealed-verified-transaction-plan-execution-handoff-v1",
            **dict.fromkeys(
                "implemented producer_owned exact_type_and_process_local_seal_required fresh_operation_objects_required deep_copied_nested_operation_values_required post_projection_authority_retained evidence_ancestry_retained_transitively authenticated_target_context_retained_transitively start_and_end_plan_reauthentication_required current_time_handoff_validation_required".split(),
                True,
            ),
            **dict.fromkeys(
                "original_plan_retained original_operations_retained additional_or_caller_supplied_target_role_runtime_configuration_retained credentials_retained connectors_retained sql_text_retained callbacks_retained hmac_keys_retained".split(),
                False,
            ),
            "authority": _NEGATIVE_CLAIMS,
        }
        future = sanitizer.get("execution_contract", {}).get(
            "future_transaction_requirements", {}
        )
        if (
            (
                data_executor.SANITIZER_POLICY_SHA256,
                data_executor.INVENTORY_POLICY_SHA256,
                data_executor.DATA_CONTRACT_SHA256,
                data_executor.SOURCE_SNAPSHOT_CONTRACT_SHA256,
            )
            != (
                SANITIZER_POLICY_SHA256,
                INVENTORY_POLICY_SHA256,
                DATA_CONTRACT_SHA256,
                SOURCE_SNAPSHOT_CONTRACT_SHA256,
            )
            or (
                post_projection.SANITIZER_POLICY_SHA256,
                post_projection.INVENTORY_POLICY_SHA256,
                post_projection.DATA_CONTRACT_SHA256,
                post_projection.SOURCE_SNAPSHOT_CONTRACT_SHA256,
                post_projection.POST_PROJECTION_CONTRACT_SHA256,
            )
            != (
                SANITIZER_POLICY_SHA256,
                INVENTORY_POLICY_SHA256,
                DATA_CONTRACT_SHA256,
                SOURCE_SNAPSHOT_CONTRACT_SHA256,
                POST_PROJECTION_CONTRACT_SHA256,
            )
            or contract.get("policy_id")
            != "cosmaneura-staging-sanitizer-transaction-plan-v1"
            or contract.get("closed_world") is not True
            or contract.get("upstream") != upstream
            or contract.get("schema") != schema
            or contract.get("transaction") != transaction
            or contract.get("execution_handoff") != execution_handoff
            or contract.get("claims") != {**_POSITIVE_CLAIMS, **_NEGATIVE_CLAIMS}
            or contract.get("status", {}).get("planning_only") is not True
            or contract.get("status", {}).get(
                "existing_executor_preflight_remains_terminal"
            )
            is not True
            or contract.get("status", {}).get("public_activation_failure")
            != ACTIVATION_FAILURE
            or contract.get("status", {}).get("activation_blockers")
            != ["transaction_adapter_unimplemented"]
            or contract.get("input", {})
            .get("candidate_dataset", {})
            .get("copy_proof_domain")
            != data_executor.CANDIDATE_TRANSACTION_COPY_DOMAIN
            or contract.get("authentication", {}).get("operation_projection_domain")
            != OPERATION_PROJECTION_DOMAIN
            or contract.get("authentication", {}).get("authority_domain")
            != AUTHORITY_DOMAIN
            or contract.get("authentication", {}).get("execution_handoff_copy_key")
            != "transform_key"
            or contract.get("authentication", {}).get("execution_handoff_copy_domain")
            != TRANSACTION_PLAN_EXECUTION_HANDOFF_COPY_DOMAIN
            or contract.get("authentication", {}).get(
                "constant_time_execution_handoff_proof_comparison"
            )
            is not True
            or len(
                {
                    data_executor.CANDIDATE_PROOF_DOMAIN,
                    data_executor.CANDIDATE_COPY_DOMAIN,
                    data_executor.CANDIDATE_TRANSACTION_COPY_DOMAIN,
                    post_projection.PROJECTION_DOMAIN,
                    post_projection.AUTHORITY_DOMAIN,
                    OPERATION_PROJECTION_DOMAIN,
                    AUTHORITY_DOMAIN,
                    TRANSACTION_PLAN_EXECUTION_HANDOFF_COPY_DOMAIN,
                }
            )
            != 8
            or sanitizer.get("execution_contract", {}).get("implemented") is not False
            or future.get("isolation") != "serializable"
            or future.get("synchronous_commit") != "on"
            or future.get("lock_alembic_version") is not True
            or future.get("truncate_all_source_tables_in_one_statement") is not True
            or future.get("truncate_cascade") is not False
            or future.get("disable_triggers") is not False
            or future.get("reject_unexpected_on_truncate_triggers") is not True
            or source.get("status", {}).get("activation_blockers")
            != ["transaction_adapter_unimplemented"]
            or post.get("status", {}).get("activation_blockers")
            != ["transaction_adapter_unimplemented"]
            or profile.get("revision") != SCHEMA_REVISION
            or profile.get("table_count") != APPLICATION_TABLE_COUNT
            or profile.get("schema_descriptor", {}).get("sha256")
            != SCHEMA_DESCRIPTOR_SHA256
            or inventory.get("database", {}).get("schema") != "public"
            or inventory.get("database", {}).get("control_table")
            != {
                "name": "alembic_version",
                "exact_rows": 1,
                "columns": [["version_num", "character varying(32)", True]],
            }
            or inventory.get("database", {}).get("sequences") != []
            or len(pre_tables) != APPLICATION_TABLE_COUNT
            or "alembic_version" in pre_tables
            or len(rewritten) != REWRITE_TABLE_COUNT
            or set(rewritten) != set(INSERT_TABLE_ORDER)
            or len(discarded) != DISCARD_TABLE_COUNT
            or len(required_empty) != REQUIRED_EMPTY_TABLE_COUNT
            or sum(len(table_contracts[name]["columns"]) for name in rewritten)
            != REWRITE_COLUMN_COUNT
            or sum(len(table_contracts[name]["columns"]) for name in discarded)
            != DISCARD_COLUMN_COUNT
            or retained != set(rewritten) | set(discarded)
            or set(rewritten) & set(discarded)
            or any(
                source_name != target_name
                and positions[target_name] >= positions[source_name]
                for source_name, target_name in edges
                if source_name in positions and target_name in positions
            )
            or [
                (source_name, target_name)
                for source_name, target_name in edges
                if source_name == target_name and source_name in positions
            ]
            != [("authority_person", "authority_person")]
            or any(_IDENTIFIER_RE.fullmatch(name) is None for name in pre_tables)
            or any(
                tuple(table_contracts[name]["columns"]) != schemas[name]
                for name in retained
            )
        ):
            _fail("transaction_plan.policy_invalid")
        engine = (
            data_executor.SanitizerDataContractExecutor._from_reviewed_configuration(
                clock=clock,
                transform_key=transform_key,
                transform_key_version=transform_version,
                inventory_hmac_key=inventory_key,
                inventory_hmac_key_version=inventory_version,
                sanitizer_policy_bytes=sanitizer_raw,
                inventory_policy_bytes=inventory_raw,
                data_contract_bytes=data_raw,
                source_snapshot_contract_bytes=source_raw,
            )
        )
        authenticator = post_projection.SanitizerFixedCatalogPostProjectionAuthenticator._from_reviewed_configuration(
            clock=clock,
            inventory_hmac_key=inventory_key,
            inventory_hmac_key_version=inventory_version,
            transform_key=transform_key,
            transform_key_version=transform_version,
            sanitizer_policy_bytes=sanitizer_raw,
            inventory_policy_bytes=inventory_raw,
            data_contract_bytes=data_raw,
            source_snapshot_contract_bytes=source_raw,
            post_projection_contract_bytes=post_raw,
        )
        value = object.__new__(cls)
        _set(
            value,
            _clock=clock,
            _last=None,
            _key=transform_key,
            _key_version=transform_version,
            _engine=engine,
            _post_authenticator=authenticator,
            _pre_tables=pre_tables,
            _columns=columns,
        )
        return value

    def _read_clock(self) -> datetime:
        now = _utc(self._clock)
        if self._last is not None and now < self._last:
            _fail("transaction_plan.clock_regressed")
        object.__setattr__(self, "_last", now)
        return now

    def _capture(
        self,
        authority: post_projection.AuthenticatedFixedCatalogPostProjection,
        candidate: data_executor.SanitizerCandidateDataset,
        now: datetime,
    ) -> data_executor._VerifiedCandidateTransactionCopy:  # noqa: SLF001
        if (
            type(authority)
            is not post_projection.AuthenticatedFixedCatalogPostProjection
        ):
            _fail("transaction_plan.authority_invalid")
        if type(candidate) is not data_executor.SanitizerCandidateDataset:
            _fail("transaction_plan.candidate_invalid")
        self._post_authenticator._validate(authority, now)  # noqa: SLF001
        copied = self._engine._copy_verified_candidate_for_transaction_plan(
            candidate, now=now
        )  # noqa: SLF001
        if (
            authority._candidate_dataset_identity != copied._candidate_identity
            or not hmac.compare_digest(
                authority._candidate_dataset_proof_sha256,
                copied._candidate_proof_sha256,
            )
            or authority._source_snapshot_identity != copied._source_snapshot_identity
            or not hmac.compare_digest(
                authority._source_snapshot_proof_sha256,
                copied._source_snapshot_proof_sha256,
            )
            or authority._authenticated_evidence is not copied._authenticated_evidence
            or authority.transform_key_version != copied._transform_key_version
        ):  # noqa: SLF001
            _fail("transaction_plan.authority_invalid")
        return copied

    @staticmethod
    def _lineage(
        authority: post_projection.AuthenticatedFixedCatalogPostProjection,
        copied: data_executor._VerifiedCandidateTransactionCopy,
    ) -> tuple[Any, ...]:  # noqa: SLF001
        return (
            id(authority),
            id(type(authority)),
            id(authority._seal),
            authority._proof_sha256,
            id(copied._authenticated_evidence),
            copied._authenticated_evidence._proof_sha256,
            copied._candidate_identity,
            copied._candidate_proof_sha256,
            copied._candidate_contract_sha256,
            copied._source_snapshot_identity,
            copied._source_snapshot_proof_sha256,
            copied._source_snapshot_contract_sha256,
            copied._transform_key_version,
            copied._transformed_at,
            copied._source_row_count,
            copied._candidate_row_count,
        )  # noqa: SLF001

    def _operations(
        self, copied: data_executor._VerifiedCandidateTransactionCopy
    ) -> tuple[_TruncatePlanOperation, tuple[_InsertPlanOperation, ...]]:  # noqa: SLF001
        tables = dict(copied._tables)  # noqa: SLF001
        truncate = object.__new__(_TruncatePlanOperation)
        _set(
            truncate,
            _tables=self._pre_tables,
            _cascade=False,
            _restart_identity=False,
            _triggers_disabled=False,
            _seal=_TRUNCATE_SEAL,
        )
        inserts = []
        for table, columns in self._columns:
            operation = object.__new__(_InsertPlanOperation)
            _set(
                operation,
                _table=table,
                _columns=columns,
                _values=tuple(
                    tuple(row[column] for column in columns) for row in tables[table]
                ),
                _whole_table_batch=True,
                _on_conflict_permitted=False,
                _seal=_INSERT_SEAL,
            )
            inserts.append(operation)
        result = truncate, tuple(inserts)
        self._validate_operations(*result)
        return result

    def _validate_operations(
        self,
        truncate: _TruncatePlanOperation,
        inserts: tuple[_InsertPlanOperation, ...],
    ) -> None:
        columns = dict(self._columns)
        if (
            type(truncate) is not _TruncatePlanOperation
            or truncate._seal is not _TRUNCATE_SEAL
            or type(truncate._tables) is not tuple
            or any(type(item) is not str for item in truncate._tables)
            or truncate._tables != self._pre_tables
            or type(truncate._cascade) is not bool
            or truncate._cascade is not False
            or type(truncate._restart_identity) is not bool
            or truncate._restart_identity is not False
            or type(truncate._triggers_disabled) is not bool
            or truncate._triggers_disabled is not False
            or type(inserts) is not tuple
            or len(inserts) != REWRITE_TABLE_COUNT
            or any(type(item) is not _InsertPlanOperation for item in inserts)
            or any(type(item._table) is not str for item in inserts)
            or tuple(item._table for item in inserts) != INSERT_TABLE_ORDER
        ):
            _fail("transaction_plan.output_invalid")
        for item in inserts:
            if (
                item._seal is not _INSERT_SEAL
                or type(item._columns) is not tuple
                or any(type(column) is not str for column in item._columns)
                or item._columns != columns[item._table]
                or type(item._values) is not tuple
                or any(
                    type(row) is not tuple
                    or len(row) != len(item._columns)
                    or not all(data_executor._is_frozen(value) for value in row)
                    for row in item._values
                )
                or type(item._whole_table_batch) is not bool
                or item._whole_table_batch is not True
                or type(item._on_conflict_permitted) is not bool
                or item._on_conflict_permitted is not False
            ):  # noqa: SLF001
                _fail("transaction_plan.output_invalid")

    def _projection(
        self,
        post_proof: str,
        candidate_proof: str,
        truncate: _TruncatePlanOperation,
        inserts: tuple[_InsertPlanOperation, ...],
    ) -> str:
        self._validate_operations(truncate, inserts)
        material = (
            ("contract_sha256", TRANSACTION_PLAN_CONTRACT_SHA256),
            ("post_projection_proof_sha256", post_proof),
            ("candidate_dataset_proof_sha256", candidate_proof),
            ("operations", _operation_material(truncate, inserts)),
        )
        return _digest(self._key, OPERATION_PROJECTION_DOMAIN, _canonical(material))

    def _copy_operations_for_execution_handoff(
        self, value: AuthenticatedSanitizerTransactionPlan
    ) -> tuple[_TruncatePlanOperation, tuple[_InsertPlanOperation, ...]]:
        if (
            type(self) is not SanitizerSerializableTransactionPlanner
            or type(value) is not AuthenticatedSanitizerTransactionPlan
        ):
            _fail("transaction_plan.output_invalid")
        self._validate_operations(value._truncate, value._inserts)
        try:
            truncate = object.__new__(_TruncatePlanOperation)
            _set(
                truncate,
                _tables=tuple(name for name in value._truncate._tables),
                _cascade=value._truncate._cascade,
                _restart_identity=value._truncate._restart_identity,
                _triggers_disabled=value._truncate._triggers_disabled,
                _seal=_TRUNCATE_SEAL,
            )
            inserts = []
            for source in value._inserts:
                operation = object.__new__(_InsertPlanOperation)
                _set(
                    operation,
                    _table=source._table,
                    _columns=tuple(column for column in source._columns),
                    _values=tuple(
                        tuple(
                            data_executor._copy_candidate_value(item)  # noqa: SLF001
                            for item in row
                        )
                        for row in source._values
                    ),
                    _whole_table_batch=source._whole_table_batch,
                    _on_conflict_permitted=source._on_conflict_permitted,
                    _seal=_INSERT_SEAL,
                )
                inserts.append(operation)
        except SanitizerTransactionPlanFailure:
            raise
        except Exception:
            _fail("transaction_plan.output_invalid")
        result = truncate, tuple(inserts)
        self._validate_operations(*result)
        original_operations = (value._truncate, *value._inserts)
        copied_operations = (result[0], *result[1])
        if len({id(item) for item in copied_operations}) != len(
            copied_operations
        ) or any(
            copied is original
            for copied, original in zip(
                copied_operations, original_operations, strict=True
            )
        ):
            _fail("transaction_plan.output_invalid")
        return result

    def _copy_verified_plan_for_execution_handoff(
        self, value: AuthenticatedSanitizerTransactionPlan
    ) -> _VerifiedTransactionPlanExecutionHandoff:
        code = "transaction_plan.output_invalid"
        if type(self) is SanitizerSerializableTransactionPlanner:
            try:
                return self._copy_plan_execution_handoff(value)
            except Exception as error:
                code = _bounded_code(error, code)
        del self, value
        _fail(code)

    def _copy_plan_execution_handoff(
        self, value: AuthenticatedSanitizerTransactionPlan
    ) -> _VerifiedTransactionPlanExecutionHandoff:
        if (
            type(self) is not SanitizerSerializableTransactionPlanner
            or type(value) is not AuthenticatedSanitizerTransactionPlan
        ):
            _fail("transaction_plan.output_invalid")
        first_at = self._read_clock()
        self._validate(value, first_at)
        source_material = _transaction_plan_copy_source_material(value)
        operation_identities = _operation_identities(value._truncate, value._inserts)
        public = {name: getattr(value, name) for name in _PLAN_PUBLIC_NAMES}
        authority = value._authenticated_post_projection
        lineage = (
            value._candidate_dataset_identity,
            value._candidate_dataset_proof_sha256,
            value._candidate_dataset_contract_sha256,
            value._candidate_copy_proof_sha256,
            value._source_snapshot_identity,
            value._source_snapshot_proof_sha256,
            value._source_snapshot_contract_sha256,
            value._operation_projection_sha256,
            value._contract_sha256,
            value._proof_sha256,
        )
        plan_metadata = (id(value), id(type(value)), id(value._seal))
        copied_operations = self._copy_operations_for_execution_handoff(value)
        copied_at = self._read_clock()
        self._validate(value, copied_at)
        if not hmac.compare_digest(
            source_material, _transaction_plan_copy_source_material(value)
        ):
            _fail("transaction_plan.output_invalid")
        copied_projection = self._projection(
            authority._proof_sha256,
            lineage[1],
            *copied_operations,
        )  # noqa: SLF001
        if not hmac.compare_digest(lineage[7], copied_projection):
            _fail("transaction_plan.output_invalid")
        handoff = object.__new__(_VerifiedTransactionPlanExecutionHandoff)
        _set(
            handoff,
            **public,
            copied_at=copied_at,
            _truncate=copied_operations[0],
            _inserts=copied_operations[1],
            _authenticated_post_projection=authority,
            _candidate_dataset_identity=lineage[0],
            _candidate_dataset_proof_sha256=lineage[1],
            _candidate_dataset_contract_sha256=lineage[2],
            _candidate_copy_proof_sha256=lineage[3],
            _source_snapshot_identity=lineage[4],
            _source_snapshot_proof_sha256=lineage[5],
            _source_snapshot_contract_sha256=lineage[6],
            _operation_projection_sha256=lineage[7],
            _contract_sha256=lineage[8],
            _transaction_plan_identity=plan_metadata[0],
            _transaction_plan_type_identity=plan_metadata[1],
            _transaction_plan_seal_identity=plan_metadata[2],
            _transaction_plan_proof_sha256=lineage[9],
            _transaction_plan_operation_identities=operation_identities[0],
            _transaction_plan_operation_type_identities=operation_identities[1],
            _transaction_plan_operation_seal_identities=operation_identities[2],
            _seal=_EXECUTION_HANDOFF_SEAL,
        )
        object.__setattr__(
            handoff,
            "_proof_sha256",
            _digest(
                self._key,
                TRANSACTION_PLAN_EXECUTION_HANDOFF_COPY_DOMAIN,
                _execution_handoff_material(handoff),
            ),
        )
        finalized_at = self._read_clock()
        self._validate(value, finalized_at)
        if not hmac.compare_digest(
            source_material, _transaction_plan_copy_source_material(value)
        ):
            _fail("transaction_plan.output_invalid")
        self._validate_verified_plan_execution_handoff_at_current_time(
            handoff, now=finalized_at
        )
        return handoff

    def _validate_verified_plan_execution_handoff_at_current_time(
        self,
        value: _VerifiedTransactionPlanExecutionHandoff,
        *,
        now: datetime,
    ) -> None:
        code = "transaction_plan.output_invalid"
        if type(self) is SanitizerSerializableTransactionPlanner:
            try:
                self._validate_execution_handoff(value, now=now)
                return
            except Exception as error:
                code = _bounded_code(error, code)
        del self, value, now
        _fail(code)

    def _validate_execution_handoff(
        self,
        value: _VerifiedTransactionPlanExecutionHandoff,
        *,
        now: datetime,
    ) -> None:
        if (
            type(self) is not SanitizerSerializableTransactionPlanner
            or type(value) is not _VerifiedTransactionPlanExecutionHandoff
            or value._seal is not _EXECUTION_HANDOFF_SEAL
        ):
            _fail("transaction_plan.output_invalid")
        if (
            type(now) is not datetime
            or now.tzinfo is not UTC
            or now.utcoffset() != _ZERO
            or now.microsecond
            or now.fold
        ):
            _fail("transaction_plan.clock_invalid")
        if (
            type(value.planned_at) is not datetime
            or value.planned_at.tzinfo is not UTC
            or value.planned_at.utcoffset() != _ZERO
            or value.planned_at.microsecond
            or value.planned_at.fold
            or type(value.copied_at) is not datetime
            or value.copied_at.tzinfo is not UTC
            or value.copied_at.utcoffset() != _ZERO
            or value.copied_at.microsecond
            or value.copied_at.fold
            or value.planned_at > value.copied_at
        ):
            _fail("transaction_plan.output_invalid")
        if value.copied_at > now:
            _fail("transaction_plan.clock_regressed")
        authority = value._authenticated_post_projection
        if (
            type(authority)
            is not post_projection.AuthenticatedFixedCatalogPostProjection
            or authority._seal is not post_projection._AUTHORITY_SEAL
        ):
            _fail("transaction_plan.output_invalid")
        self._post_authenticator._validate(authority, now)  # noqa: SLF001
        expected = {**_PUBLIC_FIXED, "transform_key_version": self._key_version}
        identity_groups = (
            value._transaction_plan_operation_identities,
            value._transaction_plan_operation_type_identities,
            value._transaction_plan_operation_seal_identities,
        )
        operation_count = 1 + REWRITE_TABLE_COUNT
        expected_type_identities = (id(_TruncatePlanOperation),) + (
            id(_InsertPlanOperation),
        ) * REWRITE_TABLE_COUNT
        expected_seal_identities = (id(_TRUNCATE_SEAL),) + (
            id(_INSERT_SEAL),
        ) * REWRITE_TABLE_COUNT
        if (
            any(
                type(getattr(value, name)) is not type(item)
                or getattr(value, name) != item
                for name, item in expected.items()
            )
            or type(value.planned_insert_row_count) is not int
            or value.planned_insert_row_count < 0
            or any(
                type(item) is not int or item <= 0
                for item in (
                    value._candidate_dataset_identity,
                    value._source_snapshot_identity,
                    value._transaction_plan_identity,
                    value._transaction_plan_type_identity,
                    value._transaction_plan_seal_identity,
                )
            )
            or any(
                type(group) is not tuple
                or len(group) != operation_count
                or any(type(item) is not int or item <= 0 for item in group)
                for group in identity_groups
            )
            or len(set(value._transaction_plan_operation_identities)) != operation_count
            or value._transaction_plan_type_identity
            != id(AuthenticatedSanitizerTransactionPlan)
            or value._transaction_plan_seal_identity != id(_PLAN_SEAL)
            or value._transaction_plan_operation_type_identities
            != expected_type_identities
            or value._transaction_plan_operation_seal_identities
            != expected_seal_identities
            or any(
                type(item) is not str or _HEX_256_RE.fullmatch(item) is None
                for item in (
                    value._candidate_dataset_proof_sha256,
                    value._candidate_dataset_contract_sha256,
                    value._candidate_copy_proof_sha256,
                    value._source_snapshot_proof_sha256,
                    value._source_snapshot_contract_sha256,
                    value._operation_projection_sha256,
                    value._contract_sha256,
                    value._transaction_plan_proof_sha256,
                    value._proof_sha256,
                )
            )
            or value._candidate_dataset_contract_sha256 != DATA_CONTRACT_SHA256
            or value._source_snapshot_contract_sha256 != SOURCE_SNAPSHOT_CONTRACT_SHA256
            or value._contract_sha256 != TRANSACTION_PLAN_CONTRACT_SHA256
            or value._candidate_dataset_identity
            != authority._candidate_dataset_identity
            or not hmac.compare_digest(
                value._candidate_dataset_proof_sha256,
                authority._candidate_dataset_proof_sha256,
            )
            or value._source_snapshot_identity != authority._source_snapshot_identity
            or not hmac.compare_digest(
                value._source_snapshot_proof_sha256,
                authority._source_snapshot_proof_sha256,
            )
        ):  # noqa: SLF001
            _fail("transaction_plan.output_invalid")
        self._validate_operations(value._truncate, value._inserts)
        copied_identities = _operation_identities(value._truncate, value._inserts)
        plan_material = _plan_authority_material_from_handoff(value)
        projection = self._projection(
            authority._proof_sha256,
            value._candidate_dataset_proof_sha256,
            value._truncate,
            value._inserts,
        )  # noqa: SLF001
        if (
            len(set(copied_identities[0])) != operation_count
            or any(
                item in value._transaction_plan_operation_identities
                for item in copied_identities[0]
            )
            or copied_identities[1] != expected_type_identities
            or copied_identities[2] != expected_seal_identities
            or value.planned_insert_row_count
            != sum(len(item._values) for item in value._inserts)
            or not hmac.compare_digest(value._operation_projection_sha256, projection)
            or not hmac.compare_digest(
                value._transaction_plan_proof_sha256,
                _digest(self._key, AUTHORITY_DOMAIN, plan_material),
            )
            or not hmac.compare_digest(
                value._proof_sha256,
                _digest(
                    self._key,
                    TRANSACTION_PLAN_EXECUTION_HANDOFF_COPY_DOMAIN,
                    _execution_handoff_material(value),
                ),
            )
        ):
            _fail("transaction_plan.output_invalid")

    def prepare(
        self,
        *,
        authenticated_post_projection: post_projection.AuthenticatedFixedCatalogPostProjection,
        sanitizer_candidate: data_executor.SanitizerCandidateDataset,
    ) -> AuthenticatedSanitizerTransactionPlan:
        code = "transaction_plan.configuration_invalid"
        if type(self) is SanitizerSerializableTransactionPlanner:
            try:
                return self._prepare(authenticated_post_projection, sanitizer_candidate)
            except Exception as error:
                code = _bounded_code(error, "transaction_plan.authority_invalid")
        del self, authenticated_post_projection, sanitizer_candidate
        _fail(code)

    def _prepare(
        self,
        authority: post_projection.AuthenticatedFixedCatalogPostProjection,
        candidate: data_executor.SanitizerCandidateDataset,
    ) -> AuthenticatedSanitizerTransactionPlan:
        first = self._capture(authority, candidate, self._read_clock())
        lineage = self._lineage(authority, first)
        first_ops = self._operations(first)
        first_projection = self._projection(
            authority._proof_sha256, first._candidate_proof_sha256, *first_ops
        )  # noqa: SLF001
        completed_at = self._read_clock()
        self._post_authenticator._validate(authority, completed_at)  # noqa: SLF001
        self._engine._validate_verified_candidate_transaction_copy_at_current_time(
            first, now=completed_at
        )  # noqa: SLF001
        final = self._capture(authority, candidate, completed_at)
        final_ops = self._operations(final)
        final_projection = self._projection(
            authority._proof_sha256, final._candidate_proof_sha256, *final_ops
        )  # noqa: SLF001
        if self._lineage(authority, final) != lineage or not hmac.compare_digest(
            first_projection, final_projection
        ):
            _fail("transaction_plan.authority_invalid")
        finalized_at = self._read_clock()
        self._post_authenticator._validate(authority, finalized_at)  # noqa: SLF001
        self._engine._validate_verified_candidate_transaction_copy_at_current_time(
            final, now=finalized_at
        )  # noqa: SLF001
        return self._mint(authority, final, *final_ops, final_projection, finalized_at)

    def _mint(
        self,
        authority: post_projection.AuthenticatedFixedCatalogPostProjection,
        copied: data_executor._VerifiedCandidateTransactionCopy,
        truncate: _TruncatePlanOperation,
        inserts: tuple[_InsertPlanOperation, ...],
        operation_projection: str,
        planned_at: datetime,
    ) -> AuthenticatedSanitizerTransactionPlan:  # noqa: SLF001
        value = object.__new__(AuthenticatedSanitizerTransactionPlan)
        _set(
            value,
            **_PUBLIC_FIXED,
            planned_insert_row_count=copied._candidate_row_count,
            transform_key_version=self._key_version,
            planned_at=planned_at,
            _truncate=truncate,
            _inserts=inserts,
            _authenticated_post_projection=authority,
            _candidate_dataset_identity=copied._candidate_identity,
            _candidate_dataset_proof_sha256=copied._candidate_proof_sha256,
            _candidate_dataset_contract_sha256=copied._candidate_contract_sha256,
            _candidate_copy_proof_sha256=copied._proof_sha256,
            _source_snapshot_identity=copied._source_snapshot_identity,
            _source_snapshot_proof_sha256=copied._source_snapshot_proof_sha256,
            _source_snapshot_contract_sha256=copied._source_snapshot_contract_sha256,
            _operation_projection_sha256=operation_projection,
            _contract_sha256=TRANSACTION_PLAN_CONTRACT_SHA256,
            _seal=_PLAN_SEAL,
        )  # noqa: SLF001
        object.__setattr__(
            value,
            "_proof_sha256",
            _digest(self._key, AUTHORITY_DOMAIN, _authority_material(value)),
        )
        self._validate(value, planned_at)
        return value

    def validate(self, value: AuthenticatedSanitizerTransactionPlan) -> None:
        code = "transaction_plan.configuration_invalid"
        if type(self) is SanitizerSerializableTransactionPlanner:
            try:
                self._validate(value, self._read_clock())
                return
            except Exception as error:
                code = _bounded_code(error, "transaction_plan.output_invalid")
        del self, value
        _fail(code)

    def _validate(
        self, value: AuthenticatedSanitizerTransactionPlan, now: datetime
    ) -> None:
        if type(value) is not AuthenticatedSanitizerTransactionPlan:
            _fail("transaction_plan.output_invalid")
        authority = value._authenticated_post_projection
        expected = {**_PUBLIC_FIXED, "transform_key_version": self._key_version}
        if (
            value._seal is not _PLAN_SEAL
            or type(authority)
            is not post_projection.AuthenticatedFixedCatalogPostProjection
            or authority._seal is not post_projection._AUTHORITY_SEAL
            or type(value.planned_at) is not datetime
            or value.planned_at.tzinfo is not UTC
            or value.planned_at.utcoffset() != _ZERO
            or value.planned_at.microsecond
            or value.planned_at.fold
            or value.planned_at > now
            or any(
                type(getattr(value, name)) is not type(item)
                or getattr(value, name) != item
                for name, item in expected.items()
            )
            or type(value.planned_insert_row_count) is not int
            or value.planned_insert_row_count < 0
            or any(
                type(item) is not int or item <= 0
                for item in (
                    value._candidate_dataset_identity,
                    value._source_snapshot_identity,
                )
            )
            or any(
                type(item) is not str or _HEX_256_RE.fullmatch(item) is None
                for item in (
                    value._candidate_dataset_proof_sha256,
                    value._candidate_dataset_contract_sha256,
                    value._candidate_copy_proof_sha256,
                    value._source_snapshot_proof_sha256,
                    value._source_snapshot_contract_sha256,
                    value._operation_projection_sha256,
                    value._contract_sha256,
                    value._proof_sha256,
                )
            )
            or value._candidate_dataset_contract_sha256 != DATA_CONTRACT_SHA256
            or value._source_snapshot_contract_sha256 != SOURCE_SNAPSHOT_CONTRACT_SHA256
            or value._candidate_dataset_identity
            != authority._candidate_dataset_identity
            or not hmac.compare_digest(
                value._candidate_dataset_proof_sha256,
                authority._candidate_dataset_proof_sha256,
            )
            or value._source_snapshot_identity != authority._source_snapshot_identity
            or not hmac.compare_digest(
                value._source_snapshot_proof_sha256,
                authority._source_snapshot_proof_sha256,
            )
            or value._contract_sha256 != TRANSACTION_PLAN_CONTRACT_SHA256
        ):  # noqa: SLF001
            _fail("transaction_plan.output_invalid")
        self._validate_operations(value._truncate, value._inserts)
        projection = self._projection(
            authority._proof_sha256,
            value._candidate_dataset_proof_sha256,
            value._truncate,
            value._inserts,
        )  # noqa: SLF001
        if (
            value.planned_insert_row_count
            != sum(len(item._values) for item in value._inserts)
            or not hmac.compare_digest(value._operation_projection_sha256, projection)
            or not hmac.compare_digest(
                value._proof_sha256,
                _digest(self._key, AUTHORITY_DOMAIN, _authority_material(value)),
            )
        ):
            _fail("transaction_plan.output_invalid")
        self._post_authenticator._validate(authority, now)  # noqa: SLF001
