from __future__ import annotations

import inspect
import json
import re
from collections.abc import Iterator
from contextlib import contextmanager
from copy import deepcopy
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

from scripts import observe_staging_controller_receipt_v2_database as database
from scripts import validate_staging_restore_runtime_binding as runtime_binding

from tests.infrastructure import (
    test_staging_controller_receipt_v2_arm_observation as arm_helpers,
)
from tests.infrastructure import test_staging_runtime_record_private_readers as wire

TOKEN = "database-observation-token"
LOCAL_ADDRESS = "10.76.0.40"
Failure = database.ControllerReceiptV2DatabaseObservationFailure
SECRET_MARKERS = (TOKEN, "postgresql://", "detail must not escape")


@contextmanager
def _no_deadline(_seconds: int) -> Iterator[None]:
    yield


class Deadlines:
    def __init__(self, fail_on: int = 0) -> None:
        self.calls: list[int] = []
        self.fail_on = fail_on

    @contextmanager
    def __call__(self, seconds: int) -> Iterator[None]:
        self.calls.append(seconds)
        call = len(self.calls)
        yield
        if call == self.fail_on:
            raise TimeoutError("deadline detail must not escape")


def _assert_sanitized_exception(error: BaseException) -> None:
    assert all(marker not in repr(error) for marker in SECRET_MARKERS)
    assert error.__cause__ is None
    assert error.__context__ is None


class Cursor:
    def __init__(self, connection: Connection) -> None:
        self.connection = connection
        self.statement = ""

    def __enter__(self) -> Cursor:
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def execute(self, statement: str) -> None:
        self.statement = statement
        self.connection.statements.append(statement)
        if self.connection.fail_statement == statement:
            raise RuntimeError("postgresql://role:secret@private/database")

    def fetchmany(self, size: int) -> list[tuple[Any, ...]]:
        assert size == 2
        return self.connection.rows[self.statement][:size]


class Connection:
    def __init__(
        self,
        rows: dict[str, list[tuple[Any, ...]]],
        *,
        fail_statement: str = "",
        rollback_fails: bool = False,
        close_fails: bool = False,
        autocommit: bool = False,
    ) -> None:
        self.rows = rows
        self.fail_statement = fail_statement
        self.rollback_fails = rollback_fails
        self.close_fails = close_fails
        self.autocommit = autocommit
        self.isolation_level: Any = None
        self.read_only: bool | None = None
        self.statements: list[str] = []
        self.rollbacks = 0
        self.closes = 0

    def cursor(self) -> Cursor:
        return Cursor(self)

    def rollback(self) -> None:
        self.rollbacks += 1
        if self.rollback_fails:
            raise RuntimeError("rollback detail must not escape")

    def close(self) -> None:
        self.closes += 1
        if self.close_fails:
            raise RuntimeError("close detail must not escape")


class Connector:
    def __init__(
        self,
        connection: Connection,
        *,
        failure: Exception | None = None,
        on_connect: Any = None,
    ) -> None:
        self.connection = connection
        self.failure = failure
        self.on_connect = on_connect
        self.calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def __call__(self, *args: Any, **kwargs: Any) -> Connection:
        self.calls.append((args, kwargs))
        if self.on_connect is not None:
            self.on_connect()
        if self.failure is not None:
            raise self.failure
        return self.connection


def _clear_libpq_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    from psycopg import pq

    names = {
        option.envvar.decode("ascii")
        for option in pq.Conninfo.get_defaults()
        if option.envvar
    }
    for name in names | database.ADDITIONAL_LIBPQ_ENVIRONMENT:
        monkeypatch.delenv(name, raising=False)


def _authority_pair(monkeypatch: pytest.MonkeyPatch) -> tuple[Any, Any]:
    observer, authority, _identity, _case = arm_helpers._run(monkeypatch)
    return authority, observer.observe(production_authority=authority)


def _rows(plan: database._DatabasePlan) -> dict[str, list[tuple[Any, ...]]]:
    session = (plan.database_name, plan.role_name, plan.role_name) + tuple(
        json.loads(
            '[160012,"controller-receipt-v2-db-observer",false,"on",'
            '"repeatable read","pg_catalog","5s","1s","10s","on","repeatable read"]'
        )
    )
    role = (plan.role_name,) + tuple(
        json.loads(
            "[true,false,true,false,false,false,false,1,true,true,false,false,"
            'true,false,false,"[]"]'
        )
    )
    return {
        database.SESSION_QUERY: [session],
        database.TLS_QUERY: [(True, "TLSv1.3", "TLS_AES_256_GCM_SHA384", 256)],
        database.ROLE_QUERY: [role],
    }


def _case(
    monkeypatch: pytest.MonkeyPatch,
    *,
    rows_mutation: Any = None,
    socket_addresses: tuple[str, int, str] | None = None,
    connection_options: dict[str, Any] | None = None,
    connector_failure: Exception | None = None,
    on_connect: Any = None,
    deadline_factory: Any = _no_deadline,
) -> dict[str, Any]:
    _clear_libpq_environment(monkeypatch)
    authority, arm_observation = _authority_pair(monkeypatch)
    policy = database.load_controller_receipt_v2_database_observation_policy()
    plan = database._derive_plan(
        authority, arm_observation, policy=policy, now=arm_helpers.NOW
    )
    rows = _rows(plan)
    if rows_mutation is not None:
        rows_mutation(rows)
    connection = Connection(rows, **(connection_options or {}))
    connector = Connector(connection, failure=connector_failure, on_connect=on_connect)
    runtime = runtime_binding._parse_policy(authority._runtime_policy)
    identity = wire.Opener(
        [wire.Response(wire._token(runtime, database.POSTGRES_OAUTH_RESOURCE, TOKEN))],
        name="database-identity",
    )
    addresses = socket_addresses or (plan.hostaddr, plan.port, LOCAL_ADDRESS)
    observer = database.ManagedIdentityControllerReceiptV2DatabaseObserver._from_reviewed_configuration(
        clock=lambda: arm_helpers.NOW,
        identity_opener=identity,
        connect=connector,
        socket_inspector=lambda _connection: addresses,
        epoch_clock=lambda: wire.EPOCH_NOW,
        monotonic=lambda: 10.0,
        deadline_factory=deadline_factory,
    )
    return locals() | {"arm": arm_observation}


def _observe(case: dict[str, Any]) -> Any:
    return case["observer"].observe(
        production_authority=case["authority"], arm_observation=case["arm"]
    )


def _replace_row(
    rows: dict[str, list[tuple[Any, ...]]], statement: str, index: int, value: Any
) -> None:
    row = list(rows[statement][0])
    row[index] = value
    rows[statement][0] = tuple(row)


def test_exact_token_connection_and_queries_mint_narrow_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case(monkeypatch)
    result = _observe(case)
    plan = case["plan"]
    document = result.authority
    assert result._is_authenticated()
    assert len(case["identity"].calls) == 1
    identity_request, identity_timeout = case["identity"].calls[0]
    assert identity_request.method == "GET"
    assert identity_timeout == 10
    assert parse_qs(urlsplit(identity_request.full_url).query) == {
        "api-version": ["2019-08-01"],
        "client_id": [plan.provision_client_id],
        "resource": [database.POSTGRES_OAUTH_RESOURCE],
    }
    assert len(case["connector"].calls) == 1
    args, parameters = case["connector"].calls[0]
    assert args == ()
    assert parameters == {
        "application_name": "controller-receipt-v2-db-observer",
        "autocommit": False,
        "connect_timeout": 10,
        "dbname": plan.database_name,
        "gssencmode": "disable",
        "host": plan.target_fqdn,
        "hostaddr": plan.hostaddr,
        "options": "-c default_transaction_read_only=on -c default_transaction_isolation=repeatable\\ read -c statement_timeout=5s -c lock_timeout=1s -c idle_in_transaction_session_timeout=10s -c search_path=pg_catalog",
        "password": TOKEN,
        "port": 5432,
        "prepare_threshold": None,
        "ssl_max_protocol_version": "TLSv1.3",
        "ssl_min_protocol_version": "TLSv1.2",
        "sslcertmode": "disable",
        "sslmode": "verify-full",
        "sslrootcert": "/etc/ssl/certs/ca-certificates.crt",
        "user": plan.role_name,
    }
    assert case["connection"].statements == [
        database.SESSION_QUERY,
        database.TLS_QUERY,
        database.ROLE_QUERY,
    ]
    assert case["connection"].isolation_level.name == "REPEATABLE_READ"
    assert case["connection"].read_only is True
    assert (case["connection"].rollbacks, case["connection"].closes) == (1, 1)
    assert document["target"] == {
        "approved_database_role": plan.role_name,
        "database_name": plan.database_name,
        "executor_private_endpoint_ip": "10.76.0.70",
        "infrastructure_subnet_cidr": "10.76.0.32/27",
        "infrastructure_subnet_id": plan.infrastructure_subnet_id,
        "port": 5432,
        "target_fqdn": plan.target_fqdn,
    }
    assert document["facts"]["network"]["peer_address"] == "10.76.0.70"
    assert document["facts"]["network"]["local_address"] == LOCAL_ADDRESS
    assert document["facts"]["role"]["memberships"] == []
    assert document["facts"]["role"]["connection_limit"] == 1
    for claim in "arm_network_facts_authenticated database_role_authenticated private_route_reachability_authenticated tls_transport_authenticated".split():
        assert document["claims"][claim] is True
    for claim in (
        "blob_or_redis_copy_absence_proven controller_receipt_exists "
        "create_only_behavior_proven database_mutation_authority executable "
        "password_authentication_absence_proven provider_facts_authenticated "
        "runtime_authority sanitizer_authority writer_identity_use_authenticated"
    ).split():
        assert document["claims"][claim] is False
    assert TOKEN not in repr(result)
    assert TOKEN.encode() not in result.authority_bytes
    assert "postgresql://" not in repr(result)


def test_public_surface_has_only_sealed_inputs_and_factory_is_dormant() -> None:
    assert set(
        inspect.signature(
            database.ManagedIdentityControllerReceiptV2DatabaseObserver.observe
        ).parameters
    ) == {"self", "production_authority", "arm_observation"}
    assert not inspect.signature(
        database.build_controller_receipt_v2_database_observer
    ).parameters
    with pytest.raises(Failure, match="policy_disabled"):
        database.build_controller_receipt_v2_database_observer()
    with pytest.raises(Failure, match="configuration_invalid"):
        database.ManagedIdentityControllerReceiptV2DatabaseObserver()
    with pytest.raises(Failure, match="authority_unsealed"):
        database.ControllerReceiptV2DatabaseObservationAuthority()


def test_policy_is_exact_closed_world_and_live_paths_stay_dark() -> None:
    policy = database.load_controller_receipt_v2_database_observation_policy()
    assert policy["activation"]["database_observer_implemented"] is True
    assert all(
        value is False
        for name, value in policy["activation"].items()
        if name != "database_observer_implemented"
    )
    assert policy["activation_ready"] is False
    assert policy["observation"]["connection"]["maximum_connections"] == 1
    assert policy["observation"]["queries"]["maximum_queries"] == 3
    assert policy["observation"]["transport"]["maximum_token_requests"] == 1
    changed = deepcopy(policy)
    changed["observation"]["connection"]["retry_permitted"] = True
    with pytest.raises(Failure, match="policy_invalid"):
        database.validate_controller_receipt_v2_database_observation_policy(changed)
    with pytest.raises(Failure, match="policy_disabled"):
        database.validate_controller_receipt_v2_database_observation_policy(
            policy, executable=True
        )


def test_queries_are_fixed_catalog_only_and_application_data_is_forbidden() -> None:
    policy = database.load_controller_receipt_v2_database_observation_policy()
    statements = policy["observation"]["queries"]["statements"]
    assert statements == {
        "role": database.ROLE_QUERY,
        "session": database.SESSION_QUERY,
        "tls": database.TLS_QUERY,
    }
    forbidden = (" insert ", " update ", " delete ", " alter ", " drop ", " copy ")
    for statement in statements.values():
        normalized = f" {statement.casefold()} "
        assert statement.startswith("SELECT ")
        assert ";" not in statement
        assert all(word not in normalized for word in forbidden)
    transaction = policy["observation"]["transaction"]
    assert transaction["connection_autocommit"] is False
    assert transaction["driver_isolation_level"] == "REPEATABLE_READ"
    assert transaction["driver_read_only"] is True
    combined = " ".join(statements.values())
    assert set(re.findall(r"pg_catalog\.[a-z_]+", combined)) == set(
        database.CATALOG_ALLOWLIST + database.FUNCTION_ALLOWLIST
    )
    assert transaction["read_only"] is True
    assert transaction["commit_permitted"] is False
    assert transaction["application_data_queries_permitted"] is False


@pytest.mark.parametrize(
    ("statement", "index", "value"),
    (
        *(
            (database.SESSION_QUERY, index, value)
            for index, value in json.loads(
                '[[1,"other_role"],[3,150015],[4,"other-observer"],[5,true],'
                ' [6,"off"],[7,"read committed"],[8,"pg_catalog, public"],'
                ' [9,"0"],[12,"off"],[13,"read committed"]]'
            )
        ),
        *(
            (database.TLS_QUERY, *item)
            for item in ((0, False), (1, "TLSv1.1"), (3, 64))
        ),
        *(
            (database.ROLE_QUERY, index, value)
            for index, value in json.loads(
                "[[1,false],[2,true],[3,false],[4,true],[5,true],[6,true],[7,true],"
                "[8,-1],[8,true],[8,1.0],[9,false],[10,false],[11,true],[12,true],"
                '[13,false],[14,true],[15,true],[16,"[[\\"pg_monitor\\",true,false,true]]"]]'
            )
        ),
    ),
)
def test_session_tls_and_role_variations_fail_closed(
    monkeypatch: pytest.MonkeyPatch, statement: str, index: int, value: Any
) -> None:
    case = _case(
        monkeypatch,
        rows_mutation=lambda rows: _replace_row(rows, statement, index, value),
    )
    with pytest.raises(Failure, match="facts_invalid"):
        _observe(case)
    assert (case["connection"].rollbacks, case["connection"].closes) == (1, 1)


def test_driver_read_only_transaction_cannot_be_silently_weakened(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case(monkeypatch, connection_options={"autocommit": True})
    with pytest.raises(Failure, match="transaction_invalid"):
        _observe(case)
    assert case["connection"].statements == []
    assert (case["connection"].rollbacks, case["connection"].closes) == (1, 1)


def test_driver_transaction_configuration_is_read_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case(monkeypatch)
    monkeypatch.setattr(
        Connection,
        "isolation_level",
        property(lambda _connection: 3, lambda _connection, _value: None),
        raising=False,
    )
    with pytest.raises(Failure, match="transaction_invalid") as error:
        _observe(case)
    assert "secret" not in str(error.value)
    assert case["connection"].statements == []
    assert (case["connection"].rollbacks, case["connection"].closes) == (1, 1)


@pytest.mark.parametrize(
    "addresses",
    (
        ("10.76.0.71", 5432, LOCAL_ADDRESS),
        ("10.76.0.70", 6432, LOCAL_ADDRESS),
        ("10.76.0.70", 5432, "10.76.0.65"),
        ("10.76.0.70", 5432, "203.0.113.9"),
    ),
)
def test_socket_peer_and_local_subnet_must_match_sealed_arm_facts(
    monkeypatch: pytest.MonkeyPatch, addresses: tuple[str, int, str]
) -> None:
    case = _case(monkeypatch, socket_addresses=addresses)
    with pytest.raises(Failure, match="facts_invalid"):
        _observe(case)
    assert len(case["connector"].calls) == 1
    assert (case["connection"].rollbacks, case["connection"].closes) == (1, 1)


def test_multiple_rows_and_query_failure_fail_closed_after_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case(
        monkeypatch,
        rows_mutation=lambda rows: rows[database.TLS_QUERY].append(
            rows[database.TLS_QUERY][0]
        ),
    )
    with pytest.raises(Failure, match="query_invalid"):
        _observe(case)
    assert (case["connection"].rollbacks, case["connection"].closes) == (1, 1)

    case = _case(
        monkeypatch,
        connection_options={"fail_statement": database.TLS_QUERY},
    )
    with pytest.raises(Failure, match="query_failed") as error:
        _observe(case)
    _assert_sanitized_exception(error.value)
    assert (case["connection"].rollbacks, case["connection"].closes) == (1, 1)


@pytest.mark.parametrize("fail_on", (2, 5, 8, 9))
def test_consumed_deadline_still_attempts_independently_bounded_cleanup(
    monkeypatch: pytest.MonkeyPatch, fail_on: int
) -> None:
    deadlines = Deadlines(fail_on=fail_on)
    case = _case(monkeypatch, deadline_factory=deadlines)
    failure = "observation_failed" if fail_on < 8 else "cleanup_failed"
    with pytest.raises(Failure, match=failure) as error:
        _observe(case)
    _assert_sanitized_exception(error.value)
    assert (case["connection"].rollbacks, case["connection"].closes) == (1, 1)
    assert deadlines.calls[-2:] == [1, 1]


@pytest.mark.parametrize("cleanup_failure", ("rollback_fails", "close_fails"))
def test_cleanup_failure_refuses_authority(
    monkeypatch: pytest.MonkeyPatch, cleanup_failure: str
) -> None:
    deadlines = Deadlines()
    case = _case(
        monkeypatch,
        connection_options={cleanup_failure: True},
        deadline_factory=deadlines,
    )
    with pytest.raises(Failure, match="cleanup_failed") as error:
        _observe(case)
    _assert_sanitized_exception(error.value)
    assert (case["connection"].rollbacks, case["connection"].closes) == (1, 1)
    assert deadlines.calls[-2:] == [1, 1]


@pytest.mark.parametrize("failure_type", (RuntimeError, Failure))
def test_connection_failure_has_no_retry_or_exception_graph_leak(
    monkeypatch: pytest.MonkeyPatch, failure_type: type[Exception]
) -> None:
    case = _case(
        monkeypatch,
        connector_failure=failure_type(
            "postgresql://approved:database-observation-token@private/staging"
        ),
    )
    with pytest.raises(Failure, match="observation_failed") as error:
        _observe(case)
    assert len(case["identity"].calls) == 1
    assert len(case["connector"].calls) == 1
    _assert_sanitized_exception(error.value)


def test_identity_failure_has_no_exception_graph_leak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case(monkeypatch)

    def fail() -> None:
        raise RuntimeError(f"identity {TOKEN} detail must not escape")

    case["identity"].effects.append(fail)
    with pytest.raises(Failure, match="identity_failed") as error:
        _observe(case)
    _assert_sanitized_exception(error.value)
    assert case["connector"].calls == []


def test_ambient_libpq_environment_refuses_before_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case(monkeypatch)
    monkeypatch.setenv("PGHOST", "attacker.example")
    with pytest.raises(Failure, match="environment_override"):
        _observe(case)
    assert case["identity"].calls == []
    assert case["connector"].calls == []


def test_stale_or_mismatched_authorities_refuse_before_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case(monkeypatch)
    other = _case(monkeypatch)
    with pytest.raises(Failure, match="authority_invalid"):
        case["observer"].observe(
            production_authority=other["authority"], arm_observation=case["arm"]
        )
    assert case["identity"].calls == []
    assert case["connector"].calls == []

    case = _case(monkeypatch)
    case["observer"]._clock = lambda: arm_helpers.NOW.replace(
        second=0
    ) + arm_helpers.timedelta(seconds=61)
    with pytest.raises(Failure, match="plan_invalid"):
        _observe(case)
    assert case["identity"].calls == []


def test_authorities_are_rechecked_after_connection_and_result_is_tamper_evident(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case(monkeypatch)
    result = _observe(case)
    object.__setattr__(result, "database_role_authenticated", False)
    assert not result._is_authenticated()
    with pytest.raises(Failure, match="authority_invalid"):
        _ = result.authority

    case = _case(monkeypatch)
    case["connector"].on_connect = lambda: object.__setattr__(
        case["arm"], "rehearsal_id", "tampered"
    )
    with pytest.raises(Failure, match="authority_invalid"):
        _observe(case)
    assert case["connection"].statements == []
    assert (case["connection"].rollbacks, case["connection"].closes) == (1, 1)


def test_observer_is_one_shot(monkeypatch: pytest.MonkeyPatch) -> None:
    case = _case(monkeypatch)
    _observe(case)
    with pytest.raises(Failure, match="already_used"):
        _observe(case)
    assert len(case["identity"].calls) == 1
    assert len(case["connector"].calls) == 1
