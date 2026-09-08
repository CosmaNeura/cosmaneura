#!/usr/bin/env python3
# pyright: reportAttributeAccessIssue=false
"""Authenticate the target-role catalog partition without external effects."""

from __future__ import annotations

import hashlib
import hmac
import json
import sys
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, NoReturn, TypeAlias

try:
    import scripts.staging_restore_sanitizer_database_capability as capability
    import scripts.staging_restore_sanitizer_target_role_database_observer as observer
    import scripts.staging_restore_sanitizer_target_role_observation_frames as frames
    import scripts.staging_restore_sanitizer_target_role_observation_plan as plan
except ModuleNotFoundError:  # Direct `python scripts/...` compatibility.
    import staging_restore_sanitizer_database_capability as capability
    import staging_restore_sanitizer_target_role_database_observer as observer
    import staging_restore_sanitizer_target_role_observation_frames as frames
    import staging_restore_sanitizer_target_role_observation_plan as plan


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = (
    ROOT / "contracts/staging-rehearsal/v1/"
    "restore-sanitizer-target-role-offline-authenticator-contract.json"
)
CONTRACT_SHA256 = "b505f070038bf0f63beee5343575b8c02ffa93c72ad6e9b8db04e05e5679aa41"
CAPABILITY_CONTRACT_SHA256 = (
    "40684569cbf34d80e144565db73b6f1e81505eb4509c9403797fc0a1114eb98d"
)
DATABASE_OBSERVER_CONTRACT_SHA256 = (
    "b361317a20d450372eadacd7bee18eec7d1a58686df3ac212283836d347ebe67"
)
REQUIREMENTS_SHA256 = "bba365517c6eab0eb0ab8c5f6746050b9f58756232e3554981761c485e17a506"
AUTHORITY_KIND = "sealed-authenticated-sanitizer-target-role-observation-v1"
ACTIVATION_FAILURE = (
    "activation.sanitizer_target_role_privileged_global_observer_unimplemented"
)
MAXIMUM_CONTRACT_BYTES = 128 * 1024
_ZERO = timedelta(0)
_AUTHORITY_SEAL = object()
_POSITIVE_CLAIMS = dict.fromkeys(
    "target_role_database_observation_authenticated target_role_requirement_partition_authenticated database_role_name_authenticated role_posture_authenticated database_object_privileges_authenticated public_acl_sources_observed all_target_and_public_grant_options_absent".split(),
    True,
)
_NEGATIVE_CLAIMS = dict.fromkeys(
    "database_connection_authority database_mutation_authority database_role_state_authenticated effective_session_settings_authenticated evidence_publication_authority password_authentication_absence_proven post_provision_schema_descriptor_authenticated privileged_global_requirements_authenticated provider_calls_permitted provider_facts_authenticated provision_job_identity_bound runtime_authority sanitizer_authority server_wide_authorization_stability_authenticated transaction_committed workflow_integrated write_path_effects_authenticated".split(),
    False,
)
CLAIMS = {**_POSITIVE_CLAIMS, **_NEGATIVE_CLAIMS}
PUBLIC_FIELDS = (
    "kind",
    "offline_authenticator_contract_sha256",
    "database_capability_contract_sha256",
    "database_observer_contract_sha256",
    "observation_plan_sha256",
    "frame_set_sha256",
    "rehearsal_id",
    "authenticated_at",
    "expires_at",
    "requirement_count",
    "requirements_sha256",
    "public_schema_count",
    "relation_count",
    "column_acl_count",
    "sequence_count",
    "public_schema_routine_count",
    "acl_manageable_custom_enum_count",
    "relation_row_type_count",
    "generated_array_type_count",
    *CLAIMS,
)
_FAILURE_CODES = frozenset(
    {
        ACTIVATION_FAILURE,
        "target_role_offline_authenticator.authority_invalid",
        "target_role_offline_authenticator.clock_invalid",
        "target_role_offline_authenticator.clock_regressed",
        "target_role_offline_authenticator.configuration_invalid",
        "target_role_offline_authenticator.contract_invalid",
        "target_role_offline_authenticator.observation_invalid",
        "target_role_offline_authenticator.output_invalid",
        "target_role_offline_authenticator.state_invalid",
        "target_role_offline_authenticator.target_expired",
    }
)

Scalar: TypeAlias = bool | int | str | None
Row: TypeAlias = tuple[Scalar, ...]
Rows: TypeAlias = tuple[Row, ...]


class SanitizerTargetRoleOfflineAuthenticatorFailure(RuntimeError):
    """Bounded refusal with no role, object, ACL, or observed row content."""


def _fail(code: str) -> NoReturn:
    if code not in _FAILURE_CODES:
        code = "target_role_offline_authenticator.authority_invalid"
    raise SanitizerTargetRoleOfflineAuthenticatorFailure(code) from None


def _bounded_code(error: Exception, fallback: str) -> str:
    if (
        type(error) is SanitizerTargetRoleOfflineAuthenticatorFailure
        and len(error.args) == 1
        and type(error.args[0]) is str
        and error.args[0] in _FAILURE_CODES
    ):
        return error.args[0]
    if type(error) in {
        observer.SanitizerTargetRoleDatabaseObserverFailure,
        frames.SanitizerTargetRoleObservationFramesFailure,
    }:
        return "target_role_offline_authenticator.observation_invalid"
    return fallback


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
        _fail("target_role_offline_authenticator.output_invalid")


def _parse(raw: bytes, *, expected_sha256: str) -> dict[str, Any]:
    if (
        type(raw) is not bytes
        or not 1 <= len(raw) <= MAXIMUM_CONTRACT_BYTES
        or not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), expected_sha256)
    ):
        _fail("target_role_offline_authenticator.contract_invalid")

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for name, value in items:
            if name in result:
                _fail("target_role_offline_authenticator.contract_invalid")
            result[name] = value
        return result

    try:
        value = json.loads(
            raw,
            object_pairs_hook=pairs,
            parse_constant=lambda _value: _fail(
                "target_role_offline_authenticator.contract_invalid"
            ),
        )
    except SanitizerTargetRoleOfflineAuthenticatorFailure:
        raise
    except (UnicodeDecodeError, ValueError, RecursionError):
        _fail("target_role_offline_authenticator.contract_invalid")
    if type(value) is not dict:
        _fail("target_role_offline_authenticator.contract_invalid")
    return value


def _read_contract() -> tuple[dict[str, Any], bytes]:
    try:
        raw = CONTRACT_PATH.read_bytes()
    except OSError:
        _fail("target_role_offline_authenticator.contract_invalid")
    return _parse(raw, expected_sha256=CONTRACT_SHA256), raw


def _requirements() -> tuple[str, ...]:
    try:
        requirements = tuple(
            plan.load_target_role_observation_plan_contract()["requirement_partition"][
                "target_role_observer"
            ]
        )
    except Exception:
        _fail("target_role_offline_authenticator.contract_invalid")
    if (
        len(requirements) != 22
        or any(type(item) is not str for item in requirements)
        or hashlib.sha256(_canonical(requirements)).hexdigest() != REQUIREMENTS_SHA256
    ):
        _fail("target_role_offline_authenticator.contract_invalid")
    return requirements


def _expected_counts(policy: Mapping[str, Any]) -> dict[str, int]:
    try:
        table_sets = policy["schema"]["table_sets"]
        type_policy = policy["privileges"]["types"]["public_schema"]
        return {
            "public_schema_count": 1,
            "relation_count": table_sets["application"]["count"]
            + table_sets["control"]["count"],
            "column_acl_count": 0,
            "sequence_count": table_sets["sequences"]["count"],
            "public_schema_routine_count": policy["privileges"]["routines"][
                "public_schema"
            ]["observed_schema_routine_count"],
            "acl_manageable_custom_enum_count": type_policy[
                "acl_manageable_custom_enums"
            ]["expected_type_count"],
            "relation_row_type_count": type_policy["relation_row_types"][
                "expected_type_count"
            ],
            "generated_array_type_count": type_policy["generated_array_types"][
                "expected_type_count"
            ],
        }
    except (KeyError, TypeError):
        _fail("target_role_offline_authenticator.contract_invalid")


def _validate_contract(document: Mapping[str, Any]) -> dict[str, Any]:
    try:
        pinned, _raw = _read_contract()
        policy = capability.load_database_capability_contract()
        observer_contract = observer.load_target_role_database_observer_contract()
        requirements = _requirements()
        counts = _expected_counts(policy)
        scope = document["scope"]
        status = document["status"]
        if (
            document != pinned
            or capability.CONTRACT_SHA256 != CAPABILITY_CONTRACT_SHA256
            or observer.CONTRACT_SHA256 != DATABASE_OBSERVER_CONTRACT_SHA256
            or document.get("schema_version") != 1
            or document.get("policy_id")
            != "cosmaneura-staging-sanitizer-target-role-offline-authenticator-v1"
            or document.get("closed_world") is not True
            or document.get("upstream_policies")
            != {
                "database_capability": {
                    "policy_id": policy["policy_id"],
                    "sha256": f"sha256:{CAPABILITY_CONTRACT_SHA256}",
                },
                "target_role_database_observer": {
                    "policy_id": observer_contract["policy_id"],
                    "sha256": f"sha256:{DATABASE_OBSERVER_CONTRACT_SHA256}",
                },
            }
            or document["input"]["required_kind"] != observer.OBSERVATION_KIND
            or document["output"]["kind"] != AUTHORITY_KIND
            or scope["requirements"] != list(requirements)
            or scope["requirement_count"] != len(requirements)
            or scope["requirements_sha256"] != f"sha256:{REQUIREMENTS_SHA256}"
            or document["authentication"]["catalog_scope"] != counts
            or document.get("claims") != CLAIMS
            or status["offline_authenticator_implemented"] is not True
            or status["activation_ready"] is not False
            or status["public_activation_failure"] != ACTIVATION_FAILURE
        ):
            _fail("target_role_offline_authenticator.contract_invalid")
    except SanitizerTargetRoleOfflineAuthenticatorFailure:
        raise
    except Exception:
        _fail("target_role_offline_authenticator.contract_invalid")
    return policy


def validate_target_role_offline_authenticator_contract(
    document: Mapping[str, Any], *, executable: bool = False
) -> dict[str, Any]:
    if type(document) is not dict or type(executable) is not bool:
        _fail("target_role_offline_authenticator.contract_invalid")
    policy = _validate_contract(document)
    if executable:
        _fail(ACTIVATION_FAILURE)
    return policy


def load_target_role_offline_authenticator_contract(
    *, executable: bool = False
) -> dict[str, Any]:
    document, _raw = _read_contract()
    validate_target_role_offline_authenticator_contract(document, executable=executable)
    return document


def _trusted_utc(clock: Callable[[], datetime]) -> datetime:
    try:
        value = clock()
    except Exception:
        _fail("target_role_offline_authenticator.clock_invalid")
    if (
        type(value) is not datetime
        or value.tzinfo is not UTC
        or value.utcoffset() != _ZERO
        or value.microsecond
        or value.fold
    ):
        _fail("target_role_offline_authenticator.clock_invalid")
    return value


def _observation_guard(value: observer.SanitizerTargetRoleDatabaseObservation) -> bytes:
    try:
        public = {
            name: (
                getattr(value, name).strftime("%Y-%m-%dT%H:%M:%SZ")
                if type(getattr(value, name)) is datetime
                else getattr(value, name)
            )
            for name in observer.PUBLIC_FIELDS
        }
        material = {
            "observation_identity": id(value),
            "observation_type_identity": id(type(value)),
            "observation_seal_identity": id(value._seal),  # noqa: SLF001
            "observation_proof_sha256": value._proof_sha256,  # noqa: SLF001
            "public": public,
            "plan_identity": id(value._plan),  # noqa: SLF001
            "plan_seal_identity": id(value._plan._seal),  # noqa: SLF001
            "plan_proof_sha256": value._plan._proof_sha256,  # noqa: SLF001
            "frames_identity": id(value._frames),  # noqa: SLF001
            "frames_seal_identity": id(value._frames._seal),  # noqa: SLF001
            "frames_proof_sha256": value._frames._proof_sha256,  # noqa: SLF001
            "frames": value._frames._frames,  # noqa: SLF001
        }
        return hashlib.sha256(_canonical(material)).digest()
    except SanitizerTargetRoleOfflineAuthenticatorFailure:
        raise
    except Exception:
        _fail("target_role_offline_authenticator.observation_invalid")


def _context(
    value: observer.SanitizerTargetRoleDatabaseObservation, *, now: datetime
) -> tuple[str, str, frames.DecodedFrames]:
    try:
        if (
            type(value) is not observer.SanitizerTargetRoleDatabaseObservation
            or value._seal is not observer._OBSERVATION_SEAL  # noqa: SLF001
            or not value._is_authenticated()  # noqa: SLF001
            or value.observed_at > now
        ):
            _fail("target_role_offline_authenticator.observation_invalid")
        if not now < value.expires_at:
            _fail("target_role_offline_authenticator.target_expired")
        decoded = value._frames  # noqa: SLF001
        target = value._plan._target_binding  # noqa: SLF001
        if (
            type(decoded) is not frames.DecodedSanitizerTargetRoleObservationFrames
            or decoded._seal is not frames._FRAME_SEAL  # noqa: SLF001
            or decoded.frame_set_sha256 != value.frame_set_sha256
            or type(target.database_name) is not str
            or type(target.approved_database_role) is not str
        ):
            _fail("target_role_offline_authenticator.observation_invalid")
        return (  # noqa: SLF001
            target.database_name,
            target.approved_database_role,
            decoded._frames,
        )
    except SanitizerTargetRoleOfflineAuthenticatorFailure:
        raise
    except Exception:
        _fail("target_role_offline_authenticator.observation_invalid")


def _frame_map(raw: frames.DecodedFrames) -> dict[str, Rows]:
    try:
        result = dict(raw)
        if tuple(result) != tuple(name for name, _rows in raw) or len(result) != 13:
            _fail("target_role_offline_authenticator.state_invalid")
        return result
    except SanitizerTargetRoleOfflineAuthenticatorFailure:
        raise
    except Exception:
        _fail("target_role_offline_authenticator.state_invalid")


def _groups(
    rows: Rows, *, identity: Sequence[int], stable: Sequence[int]
) -> dict[tuple[Scalar, ...], Rows]:
    grouped: dict[tuple[Scalar, ...], list[Row]] = {}
    stable_values: dict[tuple[Scalar, ...], tuple[Scalar, ...]] = {}
    for row in rows:
        key = tuple(row[index] for index in identity)
        state = tuple(row[index] for index in stable)
        if key in stable_values and stable_values[key] != state:
            _fail("target_role_offline_authenticator.state_invalid")
        stable_values[key] = state
        grouped.setdefault(key, []).append(row)
    return {key: tuple(values) for key, values in grouped.items()}


def _owner(
    row: Row,
    *,
    oid_index: int,
    name_index: int,
    role_oid: int,
    role_name: str,
) -> tuple[int, str]:
    owner_oid = row[oid_index]
    owner_name = row[name_index]
    if (
        type(owner_oid) is not int
        or owner_oid <= 0
        or type(owner_name) is not str
        or not owner_name
        or owner_oid == role_oid
        or owner_name == role_name
    ):
        _fail("target_role_offline_authenticator.state_invalid")
    return owner_oid, owner_name


def _acl(
    row: Row,
    *,
    start: int,
    owner_oid: int,
    owner_name: str,
    role_oid: int,
    role_name: str,
    allowed: frozenset[str],
) -> tuple[str, str, bool]:
    grantor_oid, grantor_name, grantee_oid, grantee_name, privilege, grantable = row[
        start : start + 6
    ]
    if (
        type(grantor_oid) is not int
        or grantor_oid <= 0
        or type(grantor_name) is not str
        or not grantor_name
        or type(grantee_oid) is not int
        or grantee_oid < 0
        or type(grantee_name) is not str
        or not grantee_name
        or type(privilege) is not str
        or privilege not in allowed
        or type(grantable) is not bool
        or (grantee_oid == 0) != (grantee_name == "PUBLIC")
        or (grantee_oid == role_oid) != (grantee_name == role_name)
        or (grantee_oid == owner_oid) != (grantee_name == owner_name)
    ):
        _fail("target_role_offline_authenticator.state_invalid")
    if grantable and grantee_oid != owner_oid:
        _fail("target_role_offline_authenticator.state_invalid")
    category = (
        "public"
        if grantee_oid == 0
        else "target"
        if grantee_oid == role_oid
        else "owner"
        if grantee_oid == owner_oid
        else "other"
    )
    return category, privilege, grantable


def _direct_privileges(
    rows: Rows,
    *,
    start: int,
    owner_oid: int,
    owner_name: str,
    role_oid: int,
    role_name: str,
    allowed: frozenset[str],
    public_allowed: frozenset[str] = frozenset(),
) -> tuple[frozenset[str], frozenset[str]]:
    target: set[str] = set()
    public: set[str] = set()
    for row in rows:
        category, privilege, _grantable = _acl(
            row,
            start=start,
            owner_oid=owner_oid,
            owner_name=owner_name,
            role_oid=role_oid,
            role_name=role_name,
            allowed=allowed,
        )
        destination = (
            target if category == "target" else public if category == "public" else None
        )
        if destination is not None:
            if privilege in destination:
                _fail("target_role_offline_authenticator.state_invalid")
            destination.add(privilege)
    if not public.issubset(public_allowed):
        _fail("target_role_offline_authenticator.state_invalid")
    return frozenset(target), frozenset(public)


def _role_and_session(
    rows: Rows, *, database_name: str, role_name: str
) -> tuple[int, str]:
    if len(rows) != 1:
        _fail("target_role_offline_authenticator.state_invalid")
    row = rows[0]
    role_oid = row[3]
    if (
        row[0] != database_name
        or row[1] != role_name
        or row[2] != role_name
        or type(role_oid) is not int
        or role_oid <= 0
        or row[4] != role_name
        or row[5:13] != (True, False, True, False, False, False, False, 1)
        or row[13] != "origin"
    ):
        _fail("target_role_offline_authenticator.state_invalid")
    return role_oid, role_name


def _schemas(rows: Rows, *, role_oid: int, role_name: str) -> None:
    grouped = _groups(rows, identity=(0, 1), stable=(0, 1, 2, 3, 10, 11))
    if len(grouped) != 1:
        _fail("target_role_offline_authenticator.state_invalid")
    ((_key, values),) = grouped.items()
    first = values[0]
    owner_oid, owner_name = _owner(
        first,
        oid_index=2,
        name_index=3,
        role_oid=role_oid,
        role_name=role_name,
    )
    if first[1] != "public" or first[10:12] != (True, False):
        _fail("target_role_offline_authenticator.state_invalid")
    direct, public = _direct_privileges(
        values,
        start=4,
        owner_oid=owner_oid,
        owner_name=owner_name,
        role_oid=role_oid,
        role_name=role_name,
        allowed=frozenset({"CREATE", "USAGE"}),
    )
    if direct != {"USAGE"} or public:
        _fail("target_role_offline_authenticator.state_invalid")


def _relations(
    rows: Rows,
    *,
    role_oid: int,
    role_name: str,
    policy: Mapping[str, Any],
) -> dict[str, int]:
    grouped = _groups(
        rows,
        identity=(0, 1, 2),
        stable=(0, 1, 2, 3, 4, 5, 12, 13, 14, 15, 16, 17, 18),
    )
    table_sets = policy["schema"]["table_sets"]
    application = frozenset(table_sets["application"]["tables"])
    control = frozenset(table_sets["control"]["tables"])
    rewrite = frozenset(table_sets["rewrite"]["tables"])
    expected_names = application | control
    names = [key[2] for key in grouped]
    oids = [key[1] for key in grouped]
    if (
        len(grouped) != len(expected_names)
        or frozenset(names) != expected_names
        or len(names) != len(set(names))
        or len(oids) != len(set(oids))
    ):
        _fail("target_role_offline_authenticator.state_invalid")
    output: dict[str, int] = {}
    allowed = frozenset(
        {"SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"}
    )
    for key, values in grouped.items():
        first = values[0]
        name = first[2]
        relation_oid = first[1]
        if type(name) is not str or type(relation_oid) is not int:
            _fail("target_role_offline_authenticator.state_invalid")
        expected = {"SELECT"}
        if name in application:
            expected.add("TRUNCATE")
        if name in rewrite:
            expected.add("INSERT")
        # Use a fixed ordering rather than set iteration for the seven booleans.
        effective = tuple(
            privilege in expected
            for privilege in (
                "SELECT",
                "INSERT",
                "UPDATE",
                "DELETE",
                "TRUNCATE",
                "REFERENCES",
                "TRIGGER",
            )
        )
        owner_oid, owner_name = _owner(
            first,
            oid_index=4,
            name_index=5,
            role_oid=role_oid,
            role_name=role_name,
        )
        if (
            first[0] != "public"
            or first[3] not in {"r", "p", "v", "m", "f"}
            or first[12:19] != effective
        ):
            _fail("target_role_offline_authenticator.state_invalid")
        direct, public = _direct_privileges(
            values,
            start=6,
            owner_oid=owner_oid,
            owner_name=owner_name,
            role_oid=role_oid,
            role_name=role_name,
            allowed=allowed,
        )
        if direct != expected or public:
            _fail("target_role_offline_authenticator.state_invalid")
        output[name] = relation_oid
    return output


def _routines(rows: Rows, *, role_oid: int, role_name: str, count: int) -> None:
    grouped = _groups(
        rows,
        identity=(0, 1, 2, 3, 4),
        stable=(0, 1, 2, 3, 4, 5, 6, 13),
    )
    if len(grouped) != count:
        _fail("target_role_offline_authenticator.state_invalid")
    for values in grouped.values():
        first = values[0]
        owner_oid, owner_name = _owner(
            first,
            oid_index=5,
            name_index=6,
            role_oid=role_oid,
            role_name=role_name,
        )
        if first[0] != "public" or first[13] is not False:
            _fail("target_role_offline_authenticator.state_invalid")
        direct, public = _direct_privileges(
            values,
            start=7,
            owner_oid=owner_oid,
            owner_name=owner_name,
            role_oid=role_oid,
            role_name=role_name,
            allowed=frozenset({"EXECUTE"}),
        )
        if direct or public:
            _fail("target_role_offline_authenticator.state_invalid")


def _empty_usage_objects(
    rows: Rows,
    *,
    identity: Sequence[int],
    stable: Sequence[int],
    owner_indices: tuple[int, int],
    acl_start: int,
    effective_index: int,
    role_oid: int,
    role_name: str,
) -> None:
    grouped = _groups(rows, identity=identity, stable=stable)
    for values in grouped.values():
        first = values[0]
        owner_oid, owner_name = _owner(
            first,
            oid_index=owner_indices[0],
            name_index=owner_indices[1],
            role_oid=role_oid,
            role_name=role_name,
        )
        if first[effective_index] is not False:
            _fail("target_role_offline_authenticator.state_invalid")
        direct, public = _direct_privileges(
            values,
            start=acl_start,
            owner_oid=owner_oid,
            owner_name=owner_name,
            role_oid=role_oid,
            role_name=role_name,
            allowed=frozenset({"USAGE"}),
        )
        if direct or public:
            _fail("target_role_offline_authenticator.state_invalid")


def _types(
    rows: Rows,
    *,
    role_oid: int,
    role_name: str,
    policy: Mapping[str, Any],
    relation_oids: Mapping[str, int],
) -> None:
    grouped = _groups(
        rows,
        identity=(0, 1, 2),
        stable=(0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 17),
    )
    type_policy = policy["privileges"]["types"]["public_schema"]
    enums = frozenset(type_policy["acl_manageable_custom_enums"]["types"])
    row_types = frozenset(relation_oids)
    arrays = frozenset(f"_{name}" for name in enums | row_types)
    names = [key[2] for key in grouped]
    if (
        len(grouped) != len(enums) + len(row_types) + len(arrays)
        or frozenset(names) != enums | row_types | arrays
        or len(names) != len(set(names))
    ):
        _fail("target_role_offline_authenticator.state_invalid")
    for values in grouped.values():
        first = values[0]
        name = first[2]
        if type(name) is not str:
            _fail("target_role_offline_authenticator.state_invalid")
        owner_oid, owner_name = _owner(
            first,
            oid_index=9,
            name_index=10,
            role_oid=role_oid,
            role_name=role_name,
        )
        public_allowed = frozenset({"USAGE"})
        if name in enums:
            expected = ("e", "E", None, None, None, None, False)
            public_allowed = frozenset()
        elif name in row_types:
            expected = (
                "c",
                "C",
                None,
                None,
                relation_oids[name],
                name,
                True,
            )
        else:
            element = name[1:]
            expected = (
                "b",
                "A",
                "public",
                element,
                None,
                None,
                element in row_types,
            )
        if first[0] != "public" or first[3:9] + (first[17],) != expected:
            _fail("target_role_offline_authenticator.state_invalid")
        direct, _public = _direct_privileges(
            values,
            start=11,
            owner_oid=owner_oid,
            owner_name=owner_name,
            role_oid=role_oid,
            role_name=role_name,
            allowed=frozenset({"USAGE"}),
            public_allowed=public_allowed,
        )
        if direct:
            _fail("target_role_offline_authenticator.state_invalid")


def _authenticate_frames(
    raw: frames.DecodedFrames,
    *,
    database_name: str,
    role_name: str,
    policy: Mapping[str, Any],
) -> dict[str, int]:
    frame_map = _frame_map(raw)
    role_oid, exact_role_name = _role_and_session(
        frame_map["session_and_role"],
        database_name=database_name,
        role_name=role_name,
    )
    if frame_map["memberships"] or frame_map["role_settings"]:
        _fail("target_role_offline_authenticator.state_invalid")
    _schemas(frame_map["schemas"], role_oid=role_oid, role_name=exact_role_name)
    relation_oids = _relations(
        frame_map["relations"],
        role_oid=role_oid,
        role_name=exact_role_name,
        policy=policy,
    )
    if frame_map["columns"] or frame_map["sequences"]:
        _fail("target_role_offline_authenticator.state_invalid")
    counts = _expected_counts(policy)
    _routines(
        frame_map["routines"],
        role_oid=role_oid,
        role_name=exact_role_name,
        count=counts["public_schema_routine_count"],
    )
    _types(
        frame_map["types"],
        role_oid=role_oid,
        role_name=exact_role_name,
        policy=policy,
        relation_oids=relation_oids,
    )
    _empty_usage_objects(
        frame_map["languages"],
        identity=(0, 1),
        stable=(0, 1, 2, 3, 4, 5, 12),
        owner_indices=(4, 5),
        acl_start=6,
        effective_index=12,
        role_oid=role_oid,
        role_name=exact_role_name,
    )
    _empty_usage_objects(
        frame_map["foreign_data_wrappers"],
        identity=(0, 1),
        stable=(0, 1, 2, 3, 10),
        owner_indices=(2, 3),
        acl_start=4,
        effective_index=10,
        role_oid=role_oid,
        role_name=exact_role_name,
    )
    _empty_usage_objects(
        frame_map["foreign_servers"],
        identity=(0, 1, 2),
        stable=(0, 1, 2, 3, 4, 11),
        owner_indices=(3, 4),
        acl_start=5,
        effective_index=11,
        role_oid=role_oid,
        role_name=exact_role_name,
    )
    if frame_map["user_mappings"]:
        _fail("target_role_offline_authenticator.state_invalid")
    return counts


def _serializable(public: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(public)
    for name in ("authenticated_at", "expires_at"):
        value = result.get(name)
        if type(value) is not datetime:
            _fail("target_role_offline_authenticator.output_invalid")
        result[name] = value.strftime("%Y-%m-%dT%H:%M:%SZ")
    return result


def _public_projection(
    *,
    observation: observer.SanitizerTargetRoleDatabaseObservation,
    authenticated_at: datetime,
    counts: Mapping[str, int],
) -> dict[str, Any]:
    return {
        "kind": AUTHORITY_KIND,
        "offline_authenticator_contract_sha256": f"sha256:{CONTRACT_SHA256}",
        "database_capability_contract_sha256": (f"sha256:{CAPABILITY_CONTRACT_SHA256}"),
        "database_observer_contract_sha256": (
            f"sha256:{DATABASE_OBSERVER_CONTRACT_SHA256}"
        ),
        "observation_plan_sha256": observation.observation_plan_sha256,
        "frame_set_sha256": observation.frame_set_sha256,
        "rehearsal_id": observation.rehearsal_id,
        "authenticated_at": authenticated_at,
        "expires_at": observation.expires_at,
        "requirement_count": 22,
        "requirements_sha256": f"sha256:{REQUIREMENTS_SHA256}",
        **counts,
        **CLAIMS,
    }


def _proof(
    *,
    authority_identity: int,
    public: Mapping[str, Any],
    observation: observer.SanitizerTargetRoleDatabaseObservation,
    observation_guard: bytes,
) -> str:
    material = {
        "authority_identity": authority_identity,
        "authority_seal_identity": id(_AUTHORITY_SEAL),
        "public": _serializable(public),
        "observation_identity": id(observation),
        "observation_type_identity": id(type(observation)),
        "observation_seal_identity": id(observation._seal),  # noqa: SLF001
        "observation_proof_sha256": observation._proof_sha256,  # noqa: SLF001
        "frames_identity": id(observation._frames),  # noqa: SLF001
        "frames_seal_identity": id(observation._frames._seal),  # noqa: SLF001
        "frames_proof_sha256": observation._frames._proof_sha256,  # noqa: SLF001
        "observation_guard_sha256": observation_guard.hex(),
    }
    return hashlib.sha256(_canonical(material)).hexdigest()


class AuthenticatedSanitizerTargetRoleObservation:
    """Opaque authority for only the 22 target-role observer requirements."""

    __slots__ = (
        *PUBLIC_FIELDS,
        "_observation",
        "_observation_guard_sha256",
        "_proof_sha256",
        "_seal",
    )

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        _fail("target_role_offline_authenticator.output_invalid")

    def __setattr__(self, name: str, value: object) -> NoReturn:
        del name, value
        _fail("target_role_offline_authenticator.output_invalid")

    def __delattr__(self, name: str) -> NoReturn:
        del name
        _fail("target_role_offline_authenticator.output_invalid")

    def __repr__(self) -> str:
        return "<AuthenticatedSanitizerTargetRoleObservation sealed partition-only>"

    def __copy__(self) -> NoReturn:
        _fail("target_role_offline_authenticator.output_invalid")

    def __deepcopy__(self, memo: object) -> NoReturn:
        del memo
        _fail("target_role_offline_authenticator.output_invalid")

    def __reduce__(self) -> NoReturn:
        _fail("target_role_offline_authenticator.output_invalid")

    def __reduce_ex__(self, protocol: object) -> NoReturn:
        del protocol
        _fail("target_role_offline_authenticator.output_invalid")

    def _is_authenticated(self) -> bool:
        try:
            document, _raw = _read_contract()
            policy = _validate_contract(document)
            observation = self._observation
            target = observation._plan._target_binding  # noqa: SLF001
            raw = observation._frames._frames  # noqa: SLF001
            counts = _authenticate_frames(
                raw,
                database_name=target.database_name,
                role_name=target.approved_database_role,
                policy=policy,
            )
            public = _public_projection(
                observation=observation,
                authenticated_at=self.authenticated_at,
                counts=counts,
            )
            actual_public = {name: getattr(self, name) for name in PUBLIC_FIELDS}
            return (
                type(self) is AuthenticatedSanitizerTargetRoleObservation
                and self._seal is _AUTHORITY_SEAL
                and type(observation) is observer.SanitizerTargetRoleDatabaseObservation
                and observation._seal is observer._OBSERVATION_SEAL  # noqa: SLF001
                and hmac.compare_digest(
                    self._observation_guard_sha256,
                    _observation_guard(observation),
                )
                and tuple(public) == PUBLIC_FIELDS
                and all(
                    type(actual_public[name]) is type(value)
                    and actual_public[name] == value
                    for name, value in public.items()
                )
                and hmac.compare_digest(
                    self._proof_sha256,
                    _proof(
                        authority_identity=id(self),
                        public=actual_public,
                        observation=observation,
                        observation_guard=self._observation_guard_sha256,
                    ),
                )
            )
        except Exception:
            return False


def _mint_authority(
    *,
    observation: observer.SanitizerTargetRoleDatabaseObservation,
    authenticated_at: datetime,
    counts: Mapping[str, int],
    observation_guard: bytes,
) -> AuthenticatedSanitizerTargetRoleObservation:
    public = _public_projection(
        observation=observation,
        authenticated_at=authenticated_at,
        counts=counts,
    )
    if tuple(public) != PUBLIC_FIELDS:
        _fail("target_role_offline_authenticator.output_invalid")
    value = object.__new__(AuthenticatedSanitizerTargetRoleObservation)
    for name, item in public.items():
        object.__setattr__(value, name, item)
    object.__setattr__(value, "_observation", observation)
    object.__setattr__(value, "_observation_guard_sha256", bytes(observation_guard))
    object.__setattr__(
        value,
        "_proof_sha256",
        _proof(
            authority_identity=id(value),
            public=public,
            observation=observation,
            observation_guard=observation_guard,
        ),
    )
    object.__setattr__(value, "_seal", _AUTHORITY_SEAL)
    if not value._is_authenticated():
        _fail("target_role_offline_authenticator.output_invalid")
    return value


class SanitizerTargetRoleOfflineAuthenticator:
    """Reviewed-clock, offline-only authenticator for one sealed observation."""

    __slots__ = ("_clock", "_last")

    def __init__(self) -> None:
        _fail("target_role_offline_authenticator.configuration_invalid")

    def __setattr__(self, name: str, value: object) -> NoReturn:
        del name, value
        _fail("target_role_offline_authenticator.configuration_invalid")

    def __delattr__(self, name: str) -> NoReturn:
        del name
        _fail("target_role_offline_authenticator.configuration_invalid")

    def __repr__(self) -> str:
        return "<SanitizerTargetRoleOfflineAuthenticator dormant offline-only>"

    @classmethod
    def _from_reviewed_configuration(
        cls, *, clock: Callable[[], datetime]
    ) -> SanitizerTargetRoleOfflineAuthenticator:
        if cls is not SanitizerTargetRoleOfflineAuthenticator or not callable(clock):
            _fail("target_role_offline_authenticator.configuration_invalid")
        value = object.__new__(cls)
        object.__setattr__(value, "_clock", clock)
        object.__setattr__(value, "_last", None)
        return value

    def _read_clock(self) -> datetime:
        now = _trusted_utc(self._clock)
        if self._last is not None and now < self._last:
            _fail("target_role_offline_authenticator.clock_regressed")
        object.__setattr__(self, "_last", now)
        return now

    def authenticate(
        self,
        *,
        observation: observer.SanitizerTargetRoleDatabaseObservation,
    ) -> AuthenticatedSanitizerTargetRoleObservation:
        code = "target_role_offline_authenticator.configuration_invalid"
        if type(self) is SanitizerTargetRoleOfflineAuthenticator:
            try:
                document, _raw = _read_contract()
                policy = _validate_contract(document)
                before = _observation_guard(observation)
                started_at = self._read_clock()
                database_name, role_name, raw = _context(observation, now=started_at)
                counts = _authenticate_frames(
                    raw,
                    database_name=database_name,
                    role_name=role_name,
                    policy=policy,
                )
                completed_at = self._read_clock()
                if not completed_at < observation.expires_at or not hmac.compare_digest(
                    before, _observation_guard(observation)
                ):
                    _fail("target_role_offline_authenticator.observation_invalid")
                return _mint_authority(
                    observation=observation,
                    authenticated_at=completed_at,
                    counts=counts,
                    observation_guard=before,
                )
            except Exception as error:
                code = _bounded_code(
                    error, "target_role_offline_authenticator.authority_invalid"
                )
        del self, observation
        _fail(code)

    def validate(self, authority: AuthenticatedSanitizerTargetRoleObservation) -> None:
        if (
            type(self) is not SanitizerTargetRoleOfflineAuthenticator
            or type(authority) is not AuthenticatedSanitizerTargetRoleObservation
            or not authority._is_authenticated()
        ):
            _fail("target_role_offline_authenticator.authority_invalid")


def build_sanitizer_target_role_offline_authenticator() -> NoReturn:
    """Refuse public construction until all remaining authorities are joined."""

    load_target_role_offline_authenticator_contract()
    _fail(ACTIVATION_FAILURE)


if __name__ == "__main__":
    load_target_role_offline_authenticator_contract(
        executable="--executable" in sys.argv
    )
    print("sanitizer target-role offline authenticator contract valid (dormant)")
