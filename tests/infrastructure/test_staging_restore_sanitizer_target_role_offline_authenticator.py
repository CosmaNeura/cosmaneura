from __future__ import annotations

import ast
import copy
import hashlib
import inspect
import json
import pickle
import subprocess
import sys
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, cast

import pytest

import scripts.staging_restore_sanitizer_target_role_database_observer as observer
import scripts.staging_restore_sanitizer_target_role_observation_frames as observation_frames
import scripts.staging_restore_sanitizer_target_role_offline_authenticator as authenticator
from scripts import staging_restore_sanitizer_database_capability as capability

from tests.infrastructure import (
    test_staging_restore_sanitizer_target_role_observation_plan as plan_helpers,
)
from tests.infrastructure._staging_restore_fixed_catalog_source_state_helpers import (
    NOW,
    RealChain,
    real_chain,
)

__all__ = ("real_chain",)

ROOT = Path(__file__).parents[2]
CONTRACT = (
    ROOT / "contracts/staging-rehearsal/v1/"
    "restore-sanitizer-target-role-offline-authenticator-contract.json"
)
SCRIPT = ROOT / "scripts/staging_restore_sanitizer_target_role_offline_authenticator.py"
Failure = authenticator.SanitizerTargetRoleOfflineAuthenticatorFailure
ROLE_OID = 50_000
OWNER_OID = 10
OWNER_NAME = "database_owner"


def _acl(
    privilege: str,
    *,
    grantee_oid: int,
    grantee_name: str,
    grantable: bool,
) -> tuple[object, ...]:
    return (
        OWNER_OID,
        OWNER_NAME,
        grantee_oid,
        grantee_name,
        privilege,
        grantable,
    )


def _with_acls(
    prefix: tuple[object, ...],
    specs: tuple[tuple[int, str, str, bool], ...],
    suffix: tuple[object, ...],
) -> tuple[tuple[object, ...], ...]:
    return tuple(
        (
            *prefix,
            *_acl(
                privilege,
                grantee_oid=grantee_oid,
                grantee_name=grantee_name,
                grantable=grantable,
            ),
            *suffix,
        )
        for grantee_oid, grantee_name, privilege, grantable in specs
    )


def _valid_frames(
    connection_plan: observer._ConnectionPlan,
) -> observation_frames.DecodedFrames:
    policy = capability.load_database_capability_contract()
    table_sets = policy["schema"]["table_sets"]
    application = frozenset(table_sets["application"]["tables"])
    control = frozenset(table_sets["control"]["tables"])
    rewrite = frozenset(table_sets["rewrite"]["tables"])
    relation_oids = {
        name: 10_000 + index for index, name in enumerate(sorted(application | control))
    }
    schema_rows = _with_acls(
        (2_200, "public", OWNER_OID, OWNER_NAME),
        (
            (OWNER_OID, OWNER_NAME, "CREATE", True),
            (OWNER_OID, OWNER_NAME, "USAGE", True),
            (ROLE_OID, connection_plan.role_name, "USAGE", False),
        ),
        (True, False),
    )
    relation_rows: list[tuple[object, ...]] = []
    relation_privileges = (
        "DELETE",
        "INSERT",
        "REFERENCES",
        "SELECT",
        "TRIGGER",
        "TRUNCATE",
        "UPDATE",
    )
    for name in sorted(relation_oids):
        direct = {"SELECT"}
        if name in application:
            direct.add("TRUNCATE")
        if name in rewrite:
            direct.add("INSERT")
        effective = tuple(
            privilege in direct
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
        relation_rows.extend(
            _with_acls(
                ("public", relation_oids[name], name, "r", OWNER_OID, OWNER_NAME),
                (
                    *(
                        (OWNER_OID, OWNER_NAME, privilege, True)
                        for privilege in relation_privileges
                    ),
                    *(
                        (ROLE_OID, connection_plan.role_name, privilege, False)
                        for privilege in sorted(direct)
                    ),
                ),
                effective,
            )
        )
    routine_rows = tuple(
        _with_acls(
            (
                "public",
                20_000 + index,
                f"sanitizer_routine_{index:02d}",
                "",
                "f",
                OWNER_OID,
                OWNER_NAME,
            ),
            ((OWNER_OID, OWNER_NAME, "EXECUTE", True),),
            (False,),
        )[0]
        for index in range(11)
    )
    type_policy = policy["privileges"]["types"]["public_schema"]
    enum_names = frozenset(type_policy["acl_manageable_custom_enums"]["types"])
    type_specs: list[
        tuple[str, str, str, str | None, str | None, int | None, str | None, bool]
    ] = []
    for name in enum_names:
        type_specs.append((name, "e", "E", None, None, None, None, False))
    for name, relation_oid in relation_oids.items():
        type_specs.append((name, "c", "C", None, None, relation_oid, name, True))
    for name in enum_names | frozenset(relation_oids):
        type_specs.append(
            (
                f"_{name}",
                "b",
                "A",
                "public",
                name,
                None,
                None,
                name in relation_oids,
            )
        )
    type_rows: list[tuple[object, ...]] = []
    for index, spec in enumerate(sorted(type_specs)):
        (
            name,
            kind,
            category,
            element_schema,
            element_name,
            relation_oid,
            relation_name,
            effective_usage,
        ) = spec
        acl_rows = [(OWNER_OID, OWNER_NAME, True)]
        if name not in enum_names:
            acl_rows.insert(0, (0, "PUBLIC", False))
        type_rows.extend(
            _with_acls(
                (
                    "public",
                    30_000 + index,
                    name,
                    kind,
                    category,
                    element_schema,
                    element_name,
                    relation_oid,
                    relation_name,
                    OWNER_OID,
                    OWNER_NAME,
                ),
                tuple((*item[:2], "USAGE", item[2]) for item in acl_rows),
                (effective_usage,),
            )
        )
    language_rows = _with_acls(
        (40_000, "plpgsql", True, True, OWNER_OID, OWNER_NAME),
        ((OWNER_OID, OWNER_NAME, "USAGE", True),),
        (False,),
    )
    values: dict[str, tuple[tuple[object, ...], ...]] = {
        "session_and_role": (
            (
                connection_plan.database_name,
                connection_plan.role_name,
                connection_plan.role_name,
                ROLE_OID,
                connection_plan.role_name,
                True,
                False,
                True,
                False,
                False,
                False,
                False,
                1,
                "origin",
            ),
        ),
        "memberships": (),
        "role_settings": (),
        "schemas": schema_rows,
        "relations": tuple(relation_rows),
        "columns": (),
        "sequences": (),
        "routines": routine_rows,
        "types": tuple(type_rows),
        "languages": language_rows,
        "foreign_data_wrappers": (),
        "foreign_servers": (),
        "user_mappings": (),
    }
    return cast(
        observation_frames.DecodedFrames,
        tuple((name, values[name]) for name in observer.grammar.FRAME_NAMES),
    )


def _replace_frame(
    raw: observation_frames.DecodedFrames,
    name: str,
    rows: tuple[tuple[object, ...], ...],
) -> observation_frames.DecodedFrames:
    return cast(
        observation_frames.DecodedFrames,
        tuple((frame, rows if frame == name else current) for frame, current in raw),
    )


def _replace_cell(
    raw: observation_frames.DecodedFrames,
    frame: str,
    row_index: int,
    column_index: int,
    value: object,
) -> observation_frames.DecodedFrames:
    rows = dict(raw)[frame]
    row = rows[row_index]
    changed = (*row[:column_index], value, *row[column_index + 1 :])
    return _replace_frame(
        raw, frame, (*rows[:row_index], changed, *rows[row_index + 1 :])
    )


def _observation(
    real_chain: RealChain,
    raw: observation_frames.DecodedFrames | None = None,
) -> tuple[
    observer.SanitizerTargetRoleDatabaseObservation,
    observer._ConnectionPlan,
    observation_frames.DecodedFrames,
]:
    plan_value = plan_helpers._make(real_chain)
    connection_plan = observer._derive_connection_plan(plan_value, now=NOW)
    selected = raw or _valid_frames(connection_plan)
    decoded = observation_frames._decode_target_role_observation_frames(selected)
    value = observer._mint_observation(
        plan_value=plan_value,
        connection_plan=connection_plan,
        decoded=decoded,
        observed_at=NOW,
        contract_bytes=observer.CONTRACT_PATH.read_bytes(),
    )
    return value, connection_plan, selected


def _remint(
    source: observer.SanitizerTargetRoleDatabaseObservation,
    connection_plan: observer._ConnectionPlan,
    raw: observation_frames.DecodedFrames,
) -> observer.SanitizerTargetRoleDatabaseObservation:
    decoded = observation_frames._decode_target_role_observation_frames(raw)
    return observer._mint_observation(
        plan_value=source._plan,
        connection_plan=connection_plan,
        decoded=decoded,
        observed_at=NOW,
        contract_bytes=observer.CONTRACT_PATH.read_bytes(),
    )


def _worker(
    clock: Callable[[], Any] = lambda: NOW,
) -> authenticator.SanitizerTargetRoleOfflineAuthenticator:
    return authenticator.SanitizerTargetRoleOfflineAuthenticator._from_reviewed_configuration(
        clock=clock
    )


@pytest.fixture(scope="module")
def valid_case(real_chain: RealChain) -> SimpleNamespace:
    observation, connection_plan, raw = _observation(real_chain)
    authority = _worker().authenticate(observation=observation)
    return SimpleNamespace(
        observation=observation,
        connection_plan=connection_plan,
        raw=raw,
        authority=authority,
    )


def _invalid_variant(
    raw: observation_frames.DecodedFrames,
    name: str,
    connection_plan: observer._ConnectionPlan,
) -> observation_frames.DecodedFrames:
    rows = dict(raw)
    if name == "database_name":
        return _replace_cell(raw, "session_and_role", 0, 0, "other")
    if name == "role_name":
        return _replace_cell(raw, "session_and_role", 0, 4, "other_role")
    if name == "role_flags":
        return _replace_cell(raw, "session_and_role", 0, 6, True)
    if name == "connection_limit":
        return _replace_cell(raw, "session_and_role", 0, 12, 2)
    if name in {"outbound_membership", "inbound_membership"}:
        membership = (
            name.removesuffix("_membership"),
            60_000,
            "other_role",
            ROLE_OID,
            connection_plan.role_name,
            False,
            False,
            False,
        )
        return _replace_frame(raw, "memberships", (membership,))
    if name == "role_settings":
        return _replace_frame(
            raw,
            "role_settings",
            ((0, None, ROLE_OID, connection_plan.role_name, "role_global"),),
        )
    if name == "schema_direct":
        kept = tuple(row for row in rows["schemas"] if row[6] != ROLE_OID)
        return _replace_frame(raw, "schemas", kept)
    if name == "schema_effective":
        return _replace_cell(raw, "schemas", 0, 11, True)
    if name == "table_direct":
        first_name = rows["relations"][0][2]
        kept = tuple(
            row
            for row in rows["relations"]
            if not (row[2] == first_name and row[8] == ROLE_OID and row[10] == "SELECT")
        )
        return _replace_frame(raw, "relations", kept)
    if name == "table_effective":
        return _replace_cell(raw, "relations", 0, 12, False)
    if name == "column_acl":
        relation = rows["relations"][0]
        column = (
            "public",
            relation[1],
            relation[2],
            1,
            "id",
            *_acl(
                "SELECT",
                grantee_oid=ROLE_OID,
                grantee_name=connection_plan.role_name,
                grantable=False,
            ),
            True,
            False,
            False,
            False,
        )
        return _replace_frame(raw, "columns", (column,))
    if name == "sequence":
        sequence = (
            "public",
            70_000,
            "unexpected_sequence",
            OWNER_OID,
            OWNER_NAME,
            *_acl(
                "USAGE",
                grantee_oid=OWNER_OID,
                grantee_name=OWNER_NAME,
                grantable=True,
            ),
            False,
            False,
            False,
        )
        return _replace_frame(raw, "sequences", (sequence,))
    if name == "routine_execute":
        return _replace_cell(raw, "routines", 0, 13, True)
    if name == "enum_missing":
        type_name = next(row[2] for row in rows["types"] if row[3] == "e")
        return _replace_frame(
            raw, "types", tuple(row for row in rows["types"] if row[2] != type_name)
        )
    if name == "enum_usage":
        index = next(index for index, row in enumerate(rows["types"]) if row[3] == "e")
        return _replace_cell(raw, "types", index, 17, True)
    if name == "row_type_usage":
        index = next(index for index, row in enumerate(rows["types"]) if row[3] == "c")
        return _replace_cell(raw, "types", index, 17, False)
    if name == "enum_array_usage":
        enums = capability.load_database_capability_contract()["privileges"]["types"][
            "public_schema"
        ]["acl_manageable_custom_enums"]["types"]
        index = next(
            index
            for index, row in enumerate(rows["types"])
            if row[3] == "b" and row[6] in enums
        )
        return _replace_cell(raw, "types", index, 17, True)
    if name == "language_usage":
        return _replace_cell(raw, "languages", 0, 12, True)
    if name == "fdw_usage":
        value = (
            80_000,
            "unexpected_fdw",
            OWNER_OID,
            OWNER_NAME,
            *_acl(
                "USAGE",
                grantee_oid=OWNER_OID,
                grantee_name=OWNER_NAME,
                grantable=True,
            ),
            True,
        )
        return _replace_frame(raw, "foreign_data_wrappers", (value,))
    if name == "server_usage":
        value = (
            81_000,
            "unexpected_server",
            "unexpected_fdw",
            OWNER_OID,
            OWNER_NAME,
            *_acl(
                "USAGE",
                grantee_oid=OWNER_OID,
                grantee_name=OWNER_NAME,
                grantable=True,
            ),
            True,
        )
        return _replace_frame(raw, "foreign_servers", (value,))
    if name == "user_mapping":
        return _replace_frame(
            raw,
            "user_mappings",
            (
                (
                    90_000,
                    81_000,
                    "unexpected_server",
                    ROLE_OID,
                    connection_plan.role_name,
                ),
            ),
        )
    if name == "grant_option":
        index = next(
            index for index, row in enumerate(rows["schemas"]) if row[6] == ROLE_OID
        )
        return _replace_cell(raw, "schemas", index, 9, True)
    if name == "public_acl":
        public = (
            *rows["schemas"][0][:4],
            *_acl(
                "USAGE",
                grantee_oid=0,
                grantee_name="PUBLIC",
                grantable=False,
            ),
            True,
            False,
        )
        return _replace_frame(raw, "schemas", (public, *rows["schemas"]))
    if name == "replication_role":
        return _replace_cell(raw, "session_and_role", 0, 13, "replica")
    raise AssertionError(name)


def test_contract_is_hash_pinned_closed_world_partition_only_and_dormant() -> None:
    raw = CONTRACT.read_bytes()
    contract = authenticator.load_target_role_offline_authenticator_contract()
    assert hashlib.sha256(raw).hexdigest() == authenticator.CONTRACT_SHA256
    assert contract["closed_world"] is True
    assert contract["scope"]["requirement_count"] == 22
    assert contract["scope"]["requirements_sha256"] == (
        f"sha256:{authenticator.REQUIREMENTS_SHA256}"
    )
    assert contract["claims"] == authenticator.CLAIMS
    assert contract["status"]["offline_authenticator_implemented"] is True
    assert contract["status"]["activation_ready"] is False
    assert contract["status"]["public_activation_failure"] == (
        authenticator.ACTIVATION_FAILURE
    )


def test_exact_database_observation_authenticates_only_the_target_partition(
    valid_case: SimpleNamespace,
) -> None:
    authority = valid_case.authority
    assert authority._is_authenticated()
    assert authority._observation is valid_case.observation
    assert authority.requirement_count == 22
    assert authority.relation_count == 63
    assert authority.acl_manageable_custom_enum_count == 14
    assert authority.relation_row_type_count == 63
    assert authority.generated_array_type_count == 77
    for name in authenticator._POSITIVE_CLAIMS:
        assert getattr(authority, name) is True
    for name in authenticator._NEGATIVE_CLAIMS:
        assert getattr(authority, name) is False
    _worker().validate(authority)


@pytest.mark.parametrize(
    "name",
    [
        "database_name",
        "role_name",
        "role_flags",
        "connection_limit",
        "outbound_membership",
        "inbound_membership",
        "role_settings",
        "schema_direct",
        "schema_effective",
        "table_direct",
        "table_effective",
        "column_acl",
        "sequence",
        "routine_execute",
        "enum_missing",
        "enum_usage",
        "row_type_usage",
        "enum_array_usage",
        "language_usage",
        "fdw_usage",
        "server_usage",
        "user_mapping",
        "grant_option",
        "public_acl",
        "replication_role",
    ],
)
def test_each_target_role_policy_deviation_refuses(
    valid_case: SimpleNamespace, name: str
) -> None:
    changed = _invalid_variant(valid_case.raw, name, valid_case.connection_plan)
    with pytest.raises(
        Failure, match="target_role_offline_authenticator.state_invalid"
    ):
        authenticator._authenticate_frames(
            changed,
            database_name=valid_case.connection_plan.database_name,
            role_name=valid_case.connection_plan.role_name,
            policy=capability.load_database_capability_contract(),
        )


def test_api_accepts_only_one_keyword_only_exact_sealed_observation(
    valid_case: SimpleNamespace,
) -> None:
    signature = inspect.signature(
        authenticator.SanitizerTargetRoleOfflineAuthenticator.authenticate
    )
    assert tuple(signature.parameters) == ("self", "observation")
    assert signature.parameters["observation"].kind is inspect.Parameter.KEYWORD_ONLY
    with pytest.raises(TypeError):
        cast(Any, _worker().authenticate)(valid_case.observation)
    with pytest.raises(Failure, match="observation_invalid"):
        _worker().authenticate(observation=cast(Any, object()))


def test_output_is_redacted_immutable_nonserializable_and_tamper_evident(
    valid_case: SimpleNamespace,
) -> None:
    observation = valid_case.observation
    authority = _worker().authenticate(observation=observation)
    assert "database_owner" not in repr(authority)
    assert observation._plan._target_binding.approved_database_role not in repr(
        authority
    )
    with pytest.raises(Failure):
        authority.relation_count = 0
    with pytest.raises(Failure):
        del authority.relation_count
    with pytest.raises(Failure):
        copy.copy(authority)
    with pytest.raises(Failure):
        copy.deepcopy(authority)
    with pytest.raises(Failure):
        pickle.dumps(authority)
    with pytest.raises(TypeError):
        json.dumps(authority)
    with pytest.raises(TypeError):
        vars(authority)
    object.__setattr__(authority, "relation_count", 0)
    assert not authority._is_authenticated()


def test_observation_identity_substitution_and_resigned_metadata_refuse(
    valid_case: SimpleNamespace,
) -> None:
    first = valid_case.observation
    second = _remint(first, valid_case.connection_plan, valid_case.raw)
    authority = _worker().authenticate(observation=first)
    object.__setattr__(authority, "_observation", second)
    assert not authority._is_authenticated()
    with pytest.raises(Failure, match="authority_invalid"):
        _worker().validate(authority)


@pytest.mark.parametrize(
    "clock",
    [
        lambda: NOW.replace(microsecond=1),
        lambda: NOW.replace(tzinfo=None),
        lambda: "2026-09-05T12:00:00Z",
        lambda: (_ for _ in ()).throw(RuntimeError("sensitive")),
    ],
)
def test_clock_shape_and_failures_are_bounded(
    valid_case: SimpleNamespace, clock: Callable[[], Any]
) -> None:
    with pytest.raises(Failure, match="clock_invalid") as captured:
        _worker(clock).authenticate(observation=valid_case.observation)
    assert captured.value.__cause__ is None
    assert "sensitive" not in repr(captured.value)


def test_future_expiry_regression_and_between_read_mutation_refuse(
    valid_case: SimpleNamespace,
) -> None:
    observation = _remint(
        valid_case.observation, valid_case.connection_plan, valid_case.raw
    )
    with pytest.raises(Failure, match="observation_invalid"):
        _worker(lambda: NOW - timedelta(seconds=1)).authenticate(
            observation=observation
        )
    with pytest.raises(Failure, match="target_expired"):
        _worker(lambda: observation.expires_at).authenticate(observation=observation)
    moments = iter((NOW, NOW - timedelta(seconds=1)))
    with pytest.raises(Failure, match="clock_regressed"):
        _worker(lambda: next(moments)).authenticate(observation=observation)

    calls = 0

    def mutating_clock() -> Any:
        nonlocal calls
        calls += 1
        if calls == 2:
            object.__setattr__(observation, "row_count", observation.row_count + 1)
        return NOW

    with pytest.raises(Failure, match="observation_invalid"):
        _worker(mutating_clock).authenticate(observation=observation)


def test_contract_mutations_and_malformed_json_fail_closed() -> None:
    contract = authenticator.load_target_role_offline_authenticator_contract()
    for path, value in (
        (("claims", "database_role_state_authenticated"), True),
        (("scope", "requirement_count"), 21),
        (("authentication", "database_or_provider_calls_permitted"), True),
        (("status", "activation_ready"), True),
    ):
        changed = copy.deepcopy(contract)
        changed[path[0]][path[1]] = value
        with pytest.raises(Failure, match="contract_invalid"):
            authenticator.validate_target_role_offline_authenticator_contract(changed)
    for raw in (
        CONTRACT.read_bytes() + b"\n",
        b'{"schema_version":1,"schema_version":1}',
        b'{"value":NaN}',
        b"[]",
    ):
        with pytest.raises(Failure, match="contract_invalid"):
            authenticator._parse(raw, expected_sha256=authenticator.CONTRACT_SHA256)


def test_module_has_no_database_network_provider_process_or_write_surface() -> None:
    tree = ast.parse(SCRIPT.read_text())
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert imported.isdisjoint(
        {"azure", "http", "psycopg", "requests", "socket", "subprocess", "urllib"}
    )
    calls = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert calls.isdisjoint(
        {"connect", "execute", "commit", "unlink", "write_bytes", "write_text"}
    )


def test_public_factory_and_executable_mode_remain_terminal() -> None:
    with pytest.raises(Failure, match=authenticator.ACTIVATION_FAILURE):
        authenticator.build_sanitizer_target_role_offline_authenticator()
    with pytest.raises(Failure, match=authenticator.ACTIVATION_FAILURE):
        authenticator.load_target_role_offline_authenticator_contract(executable=True)
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--executable"],
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
    )
    assert completed.returncode != 0
    assert authenticator.ACTIVATION_FAILURE in completed.stderr


def test_construction_subclassing_and_unexpected_inputs_fail_without_content(
    valid_case: SimpleNamespace,
) -> None:
    with pytest.raises(Failure, match="configuration_invalid"):
        authenticator.SanitizerTargetRoleOfflineAuthenticator()
    with pytest.raises(Failure, match="output_invalid"):
        authenticator.AuthenticatedSanitizerTargetRoleObservation()

    class Worker(authenticator.SanitizerTargetRoleOfflineAuthenticator):
        pass

    with pytest.raises(Failure, match="configuration_invalid"):
        Worker._from_reviewed_configuration(clock=lambda: NOW)
    worker = _worker()
    object.__setattr__(
        worker, "_clock", lambda: (_ for _ in ()).throw(ValueError("row"))
    )
    with pytest.raises(Failure) as captured:
        worker.authenticate(observation=valid_case.observation)
    assert captured.value.__cause__ is None
    assert "row" not in repr(captured.value)
