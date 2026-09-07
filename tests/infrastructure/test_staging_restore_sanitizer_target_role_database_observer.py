from __future__ import annotations

import copy
import hashlib
import inspect
import json
import pickle
from contextlib import contextmanager
from datetime import timedelta
from types import SimpleNamespace
from typing import Any, Iterator, cast
from urllib.parse import parse_qs, urlsplit

import pytest

from scripts import (
    staging_restore_sanitizer_target_role_database_observer as observer,
)
from scripts import staging_restore_sanitizer_target_role_query_grammar as grammar

from tests.infrastructure import (
    test_staging_restore_sanitizer_target_role_observation_frames as frame_helpers,
)
from tests.infrastructure import (
    test_staging_restore_sanitizer_target_role_observation_plan as plan_helpers,
)
from tests.infrastructure import test_staging_runtime_record_private_readers as wire
from tests.infrastructure._staging_restore_fixed_catalog_source_state_helpers import (
    NOW,
    RealChain,
    real_chain,
)

__all__ = ("real_chain",)

ROOT = observer.ROOT
CONTRACT = (
    ROOT
    / "contracts/staging-rehearsal/v1/"
    "restore-sanitizer-target-role-database-observer-contract.json"
)
SCRIPT = ROOT / "scripts/staging_restore_sanitizer_target_role_database_observer.py"
TOKEN = "target-role-observer-token"
LOCAL_ADDRESS = "10.76.0.40"
Failure = observer.SanitizerTargetRoleDatabaseObserverFailure
_PLAN_CACHE: dict[
    int,
    tuple[
        Any,
        observer._ConnectionPlan,
    ],
] = {}


@contextmanager
def _no_deadline(_seconds: int) -> Iterator[None]:
    yield


class Column:
    def __init__(self, name: str, type_code: int) -> None:
        self.name = name
        self.type_code = type_code


class PgConn:
    ssl_in_use = True

    def __init__(self, *, protocol: bytes = b"TLSv1.3", cipher: bytes = b"TLS_AES_256_GCM_SHA384") -> None:
        self.protocol = protocol
        self.cipher = cipher

    def ssl_attribute(self, name: bytes) -> bytes | None:
        return {b"protocol": self.protocol, b"cipher": self.cipher}.get(name)


class Cursor:
    def __init__(self, connection: Connection) -> None:
        self.connection = connection
        self.frame = ""
        self.description: list[Column] = []
        self.closed = 0

    def execute(self, statement: str, *, prepare: bool) -> None:
        if self.connection.execute_failure:
            raise RuntimeError("postgresql://role:secret@private/database")
        assert prepare is False
        self.frame = self.connection.statement_frames[statement]
        template = self.connection.templates[self.frame]
        self.description = [
            Column(column["name"], observer.TYPE_OIDS[column["type"]])
            for column in template["exact_columns"]
        ]
        if self.connection.bad_description_frame == self.frame:
            self.description[0].name = "hostile"
        self.connection.executions.append((self.frame, statement, prepare))

    def fetchmany(self, size: int) -> list[tuple[object, ...]]:
        self.connection.fetch_sizes.append((self.frame, size))
        return self.connection.rows[self.frame][:size]

    def close(self) -> None:
        self.closed += 1
        if self.connection.cursor_close_failure:
            raise RuntimeError("cursor detail must not escape")


class Connection:
    def __init__(
        self,
        *,
        plan: observer._ConnectionPlan,
        rows: dict[str, list[tuple[object, ...]]],
        templates: dict[str, dict[str, Any]],
        statements: dict[str, str],
        bad_description_frame: str = "",
        execute_failure: bool = False,
        rollback_failure: bool = False,
        close_failure: bool = False,
        cursor_close_failure: bool = False,
        tls_protocol: bytes = b"TLSv1.3",
    ) -> None:
        self.autocommit = False
        self.isolation_level: Any = None
        self.read_only: bool | None = None
        self.info = SimpleNamespace(
            dbname=plan.database_name,
            user=plan.role_name,
            host=plan.target_fqdn,
            hostaddr=plan.hostaddr,
            port=plan.port,
        )
        self.pgconn = PgConn(protocol=tls_protocol)
        self.rows = rows
        self.templates = templates
        self.statement_frames = {value: name for name, value in statements.items()}
        self.bad_description_frame = bad_description_frame
        self.execute_failure = execute_failure
        self.rollback_failure = rollback_failure
        self.close_failure = close_failure
        self.cursor_close_failure = cursor_close_failure
        self.executions: list[tuple[str, str, bool]] = []
        self.fetch_sizes: list[tuple[str, int]] = []
        self.cursors: list[Cursor] = []
        self.rollbacks = 0
        self.closes = 0

    def cursor(self) -> Cursor:
        value = Cursor(self)
        self.cursors.append(value)
        return value

    def rollback(self) -> None:
        self.rollbacks += 1
        if self.rollback_failure:
            raise RuntimeError("rollback secret must not escape")

    def close(self) -> None:
        self.closes += 1
        if self.close_failure:
            raise RuntimeError("close secret must not escape")


class Connector:
    def __init__(self, connection: Connection, *, failure: bool = False) -> None:
        self.connection = connection
        self.failure = failure
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **parameters: Any) -> Connection:
        self.calls.append(parameters)
        if self.failure:
            raise RuntimeError("postgresql://role:secret@private/database")
        return self.connection


def _clear_libpq_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    from psycopg import pq

    names = {
        option.envvar.decode("ascii")
        for option in pq.Conninfo.get_defaults()
        if option.envvar
    }
    for name in names | observer.database.ADDITIONAL_LIBPQ_ENVIRONMENT:
        monkeypatch.delenv(name, raising=False)


def _replace_session(
    raw: tuple[tuple[str, tuple[tuple[object, ...], ...]], ...],
    plan: observer._ConnectionPlan,
) -> tuple[tuple[str, tuple[tuple[object, ...], ...]], ...]:
    output = []
    for name, rows in raw:
        if name == "session_and_role":
            row = list(rows[0])
            row[0] = plan.database_name
            row[1] = plan.role_name
            row[2] = plan.role_name
            row[4] = plan.role_name
            row[13] = "origin"
            rows = (tuple(row),)
        output.append((name, rows))
    return tuple(output)


def _case(
    monkeypatch: pytest.MonkeyPatch,
    real_chain: RealChain,
    *,
    connection_changes: dict[str, Any] | None = None,
    socket_addresses: tuple[str, int, str] | None = None,
    row_mutation: Any = None,
    connector_failure: bool = False,
) -> dict[str, Any]:
    _clear_libpq_environment(monkeypatch)
    monkeypatch.setenv("IDENTITY_ENDPOINT", wire.IDENTITY_ENDPOINT)
    monkeypatch.setenv("IDENTITY_HEADER", wire.IDENTITY_HEADER)
    cache_key = id(real_chain)
    if cache_key not in _PLAN_CACHE:
        plan_value = plan_helpers._make(real_chain)
        _PLAN_CACHE[cache_key] = (
            plan_value,
            observer._derive_connection_plan(plan_value, now=NOW),
        )
    plan_value, connection_plan = _PLAN_CACHE[cache_key]
    query_contract = grammar.load_target_role_query_grammar_contract()
    templates = {
        item["frame"]: item for item in query_contract["grammar"]["templates"]
    }
    statements, _raw = grammar._read_templates()
    raw_frames = _replace_session(frame_helpers._raw_frames(), connection_plan)
    rows = {name: list(values) for name, values in raw_frames}
    if row_mutation is not None:
        row_mutation(rows)
    connection = Connection(
        plan=connection_plan,
        rows=rows,
        templates=templates,
        statements=statements,
        **(connection_changes or {}),
    )
    connector = Connector(connection, failure=connector_failure)
    token_body = json.dumps(
        {
            "access_token": TOKEN,
            "client_id": connection_plan.provision_client_id,
            "expires_on": str(wire.EPOCH_NOW + 3600),
            "resource": observer.POSTGRES_OAUTH_RESOURCE,
            "token_type": "Bearer",
        },
        separators=(",", ":"),
    ).encode()
    identity = wire.Opener([wire.Response(token_body)], name="identity")
    addresses = socket_addresses or (
        connection_plan.hostaddr,
        connection_plan.port,
        LOCAL_ADDRESS,
    )
    worker = observer.ManagedIdentitySanitizerTargetRoleDatabaseObserver._from_reviewed_configuration(
        clock=lambda: NOW,
        identity_opener=identity,
        connect=connector,
        socket_inspector=lambda _connection: addresses,
        epoch_clock=lambda: wire.EPOCH_NOW,
        monotonic=lambda: 10.0,
        deadline_factory=_no_deadline,
    )
    return locals()


def _observe(case: dict[str, Any]) -> observer.SanitizerTargetRoleDatabaseObservation:
    return case["worker"].observe(plan=case["plan_value"])


def _assert_sanitized(error: BaseException) -> None:
    assert "postgresql://" not in repr(error)
    assert TOKEN not in repr(error)
    assert "secret" not in repr(error)
    assert error.__cause__ is None
    assert error.__context__ is None


def test_contract_is_hash_pinned_closed_world_and_dormant() -> None:
    assert hashlib.sha256(CONTRACT.read_bytes()).hexdigest() == observer.CONTRACT_SHA256
    contract = observer.load_target_role_database_observer_contract()
    assert contract["closed_world"] is True
    assert contract["claims"] == observer.CLAIMS
    assert contract["status"]["connection_adapter_implemented"] is True
    assert contract["status"]["query_executor_implemented"] is True
    assert contract["status"]["observation_authority_implemented"] is True
    assert contract["status"]["live_database_calls_enabled"] is False
    assert contract["status"]["activation_ready"] is False
    assert "target_entra_authentication_and_role_provisioning_unimplemented" in contract[
        "status"
    ]["activation_blockers"]
    with pytest.raises(Failure, match=observer.ACTIVATION_FAILURE):
        observer.load_target_role_database_observer_contract(executable=True)


def test_exact_one_shot_managed_identity_connection_executes_and_seals(
    monkeypatch: pytest.MonkeyPatch, real_chain: RealChain
) -> None:
    case = _case(monkeypatch, real_chain)
    clock_values = iter(NOW + timedelta(seconds=index) for index in range(64))
    object.__setattr__(case["worker"], "_clock", lambda: next(clock_values))
    result = _observe(case)
    plan = case["connection_plan"]
    assert result._is_authenticated()
    assert result.kind == observer.OBSERVATION_KIND
    assert result._plan is case["plan_value"]
    assert result._frames._is_valid()
    assert result.frame_set_sha256 == result._frames.frame_set_sha256
    assert result.observation_plan_sha256 == plan.observation_plan_sha256
    assert result.query_count == 13
    assert result.observed_at > result._plan.planned_at
    assert result.row_count == sum(len(value) for value in case["rows"].values())
    assert all(getattr(result, name) is value for name, value in observer.CLAIMS.items())

    assert len(case["identity"].calls) == 1
    request, timeout = case["identity"].calls[0]
    assert timeout == 10
    assert parse_qs(urlsplit(request.full_url).query) == {
        "api-version": ["2019-08-01"],
        "client_id": [plan.provision_client_id],
        "resource": [observer.POSTGRES_OAUTH_RESOURCE],
    }
    assert len(case["connector"].calls) == 1
    parameters = case["connector"].calls[0]
    assert parameters["host"] == plan.target_fqdn
    assert parameters["hostaddr"] == plan.hostaddr
    assert parameters["user"] == plan.role_name
    assert parameters["dbname"] == plan.database_name
    assert parameters["password"] == TOKEN
    assert parameters["sslmode"] == "verify-full"
    assert parameters["prepare_threshold"] is None
    assert [item[0] for item in case["connection"].executions] == list(
        grammar.FRAME_NAMES
    )
    assert all(item[2] is False for item in case["connection"].executions)
    assert case["connection"].fetch_sizes == [
        (name, grammar.MAXIMUM_ROWS[name] + 1) for name in grammar.FRAME_NAMES
    ]
    assert all(cursor.closed == 1 for cursor in case["connection"].cursors)
    assert (case["connection"].rollbacks, case["connection"].closes) == (1, 1)
    assert TOKEN not in repr(result)
    assert "café" not in repr(result)

    with pytest.raises(Failure, match="already_used"):
        _observe(case)


def test_output_is_immutable_nonserializable_and_tamper_evident(
    monkeypatch: pytest.MonkeyPatch, real_chain: RealChain
) -> None:
    result = _observe(_case(monkeypatch, real_chain))
    for action in (
        lambda: copy.copy(result),
        lambda: copy.deepcopy(result),
        lambda: pickle.dumps(result),
        lambda: json.dumps(result),
    ):
        with pytest.raises((Failure, TypeError)):
            action()
    with pytest.raises(Failure):
        result.row_count = 0
    object.__setattr__(result, "row_count", 0)
    assert not result._is_authenticated()


@pytest.mark.parametrize(
    ("changes", "addresses", "code"),
    (
        ({"bad_description_frame": "schemas"}, None, "query_invalid"),
        ({"tls_protocol": b"TLSv1.1"}, None, "tls_invalid"),
        ({}, ("10.76.0.71", 5432, LOCAL_ADDRESS), "socket_invalid"),
        ({}, ("10.76.0.70", 5432, "10.75.0.5"), "socket_invalid"),
    ),
)
def test_description_tls_and_socket_mismatches_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    real_chain: RealChain,
    changes: dict[str, Any],
    addresses: tuple[str, int, str] | None,
    code: str,
) -> None:
    case = _case(
        monkeypatch,
        real_chain,
        connection_changes=changes,
        socket_addresses=addresses,
    )
    with pytest.raises(Failure, match=code) as caught:
        _observe(case)
    _assert_sanitized(caught.value)
    assert (case["connection"].rollbacks, case["connection"].closes) == (1, 1)


def test_identity_role_and_row_bounds_fail_without_leaking(
    monkeypatch: pytest.MonkeyPatch, real_chain: RealChain
) -> None:
    def wrong_role(rows: dict[str, list[tuple[object, ...]]]) -> None:
        row = list(rows["session_and_role"][0])
        row[2] = "attacker"
        rows["session_and_role"][0] = tuple(row)

    case = _case(monkeypatch, real_chain, row_mutation=wrong_role)
    with pytest.raises(Failure, match="query_invalid") as caught:
        _observe(case)
    _assert_sanitized(caught.value)
    assert len(case["connection"].executions) == 1

    def too_many(rows: dict[str, list[tuple[object, ...]]]) -> None:
        rows["memberships"] = [rows["memberships"][0]] * (
            grammar.MAXIMUM_ROWS["memberships"] + 1
        )

    case = _case(monkeypatch, real_chain, row_mutation=too_many)
    with pytest.raises(Failure, match="query_invalid") as caught:
        _observe(case)
    _assert_sanitized(caught.value)

    def wrong_type(rows: dict[str, list[tuple[object, ...]]]) -> None:
        row = list(rows["schemas"][0])
        row[0] = False
        rows["schemas"][0] = tuple(row)

    case = _case(monkeypatch, real_chain, row_mutation=wrong_type)
    with pytest.raises(Failure, match="query_invalid") as caught:
        _observe(case)
    _assert_sanitized(caught.value)

    def duplicate(rows: dict[str, list[tuple[object, ...]]]) -> None:
        rows["memberships"] = [rows["memberships"][0]] * 2

    case = _case(monkeypatch, real_chain, row_mutation=duplicate)
    with pytest.raises(Failure, match="query_invalid") as caught:
        _observe(case)
    _assert_sanitized(caught.value)


@pytest.mark.parametrize(
    ("failure", "code"),
    (("identity", "identity_failed"), ("connection", "connection_failed")),
)
def test_identity_and_connection_failures_are_bounded(
    monkeypatch: pytest.MonkeyPatch,
    real_chain: RealChain,
    failure: str,
    code: str,
) -> None:
    case = _case(
        monkeypatch,
        real_chain,
        connector_failure=failure == "connection",
    )
    if failure == "identity":

        def reject_identity() -> None:
            raise RuntimeError("identity secret must not escape")

        case["identity"].effects.append(reject_identity)
    with pytest.raises(Failure, match=code) as caught:
        _observe(case)
    _assert_sanitized(caught.value)
    assert case["connection"].closes == 0
    if failure == "identity":
        case = _case(monkeypatch, real_chain)
        monkeypatch.setenv("PGHOST", "attacker.invalid")
        with pytest.raises(Failure, match="environment_override"):
            _observe(case)
        assert case["identity"].calls == []


def test_retained_plan_mutation_between_stages_is_detected(
    monkeypatch: pytest.MonkeyPatch, real_chain: RealChain
) -> None:
    case = _case(monkeypatch, real_chain)
    plan_value = case["plan_value"]
    original = plan_value.rehearsal_id

    def tamper() -> None:
        object.__setattr__(plan_value, "rehearsal_id", "0" * 36)

    case["identity"].effects.append(tamper)
    try:
        with pytest.raises(Failure, match="plan_invalid") as caught:
            _observe(case)
        _assert_sanitized(caught.value)
        assert case["connector"].calls == []
    finally:
        object.__setattr__(plan_value, "rehearsal_id", original)


def test_final_deadline_is_checked_after_output_authentication(
    monkeypatch: pytest.MonkeyPatch, real_chain: RealChain
) -> None:
    case = _case(monkeypatch, real_chain)
    expired = False
    original = observer._mint_observation

    def mint(**values: Any) -> observer.SanitizerTargetRoleDatabaseObservation:
        nonlocal expired
        result = original(**values)
        expired = True
        return result

    monkeypatch.setattr(observer, "_mint_observation", mint)
    object.__setattr__(case["worker"], "_monotonic", lambda: 56.0 if expired else 10.0)
    with pytest.raises(Failure, match="deadline"):
        _observe(case)


@pytest.mark.parametrize("failure", ("execute", "cursor_close", "rollback", "close"))
def test_transport_and_cleanup_failures_are_bounded(
    monkeypatch: pytest.MonkeyPatch, real_chain: RealChain, failure: str
) -> None:
    changes = {
        "execute_failure": failure == "execute",
        "cursor_close_failure": failure == "cursor_close",
        "rollback_failure": failure == "rollback",
        "close_failure": failure == "close",
    }
    case = _case(monkeypatch, real_chain, connection_changes=changes)
    expected = "query_failed" if failure in {"execute", "cursor_close"} else "cleanup_failed"
    with pytest.raises(Failure, match=expected) as caught:
        _observe(case)
    _assert_sanitized(caught.value)
    assert case["connection"].closes == 1
    if failure == "execute":
        case = _case(
            monkeypatch,
            real_chain,
            connection_changes={"execute_failure": True, "rollback_failure": True},
        )
        with pytest.raises(Failure, match="cleanup_failed"):
            _observe(case)


def test_public_surface_accepts_only_sealed_plan_and_factory_remains_dark(
    monkeypatch: pytest.MonkeyPatch, real_chain: RealChain
) -> None:
    parameters = inspect.signature(
        observer.ManagedIdentitySanitizerTargetRoleDatabaseObserver.observe
    ).parameters
    assert tuple(parameters) == ("self", "plan")
    assert parameters["plan"].kind is inspect.Parameter.KEYWORD_ONLY
    case = _case(monkeypatch, real_chain)
    with pytest.raises(TypeError):
        cast(Any, case["worker"].observe)(case["plan_value"])
    with pytest.raises(TypeError):
        cast(Any, case["worker"].observe)(plan=case["plan_value"], sql="SELECT 1")
    with pytest.raises(Failure, match="configuration_invalid"):
        observer.ManagedIdentitySanitizerTargetRoleDatabaseObserver()
    with pytest.raises(Failure, match="output_invalid"):
        observer.SanitizerTargetRoleDatabaseObservation()
    with pytest.raises(Failure, match=observer.ACTIVATION_FAILURE):
        observer.build_sanitizer_target_role_database_observer()


def test_contract_mutation_and_source_regressions_refuse() -> None:
    contract = observer.load_target_role_database_observer_contract()
    changed = copy.deepcopy(contract)
    changed["connection"]["retry_permitted"] = True
    with pytest.raises(Failure, match="contract_invalid"):
        observer.validate_target_role_database_observer_contract(changed)

    source = SCRIPT.read_text()
    assert ".commit(" not in source
    assert "DefaultAzureCredential" not in source
    assert source.count("connection_string") == 1
    assert "cursor.execute(statement, prepare=False)" in source
    assert 'parameters["password"] = ""' in source
    assert "token = \"\"" in source
