from __future__ import annotations

import ast
import json
import sqlite3
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import pytest

from scripts import production_migration_precheck as precheck

ROOT = Path(__file__).resolve().parents[2]
ZERO = "00000000-0000-0000-0000-000000000000"
CONTRACT = precheck.house.repository_migration_contract(ROOT)


class FixtureDatabase:
    """In-memory data only; protocol/TLS responses are synthetic, not sockets."""

    def __init__(self) -> None:
        self.db = sqlite3.connect(":memory:")
        self.db.execute("ATTACH DATABASE ':memory:' AS public")
        self.db.executescript("""
            CREATE TABLE public.alembic_version (version_num TEXT);
            INSERT INTO public.alembic_version VALUES ('49126bf99c72');
            CREATE TABLE public.scribe_capture_provider_link (
                practice_id TEXT, scribe_tenant_id TEXT, revoked_at TEXT
            );
            CREATE TABLE public.provider_practice_authority (practice_id TEXT);
        """)
        self.boundary: tuple[Any, ...] = (
            "production",
            "public",
            "on",
            "repeatable read",
        )
        self.tls: tuple[Any, ...] | None = (True, "TLSv1.3")
        self.queries: list[str] = []
        self.rolled_back = False

    def execute(self, query: str, params: tuple[Any, ...] = ()) -> Any:
        self.queries.append(query)
        if query == precheck.TRANSACTION:
            return None
        if query == precheck.BOUNDARY:
            return Rows([self.boundary])
        if query == precheck.house.TLS_STATUS_QUERY:
            return Rows([] if self.tls is None else [self.tls])
        return self.db.execute(query, params)

    def rollback(self) -> None:
        self.rolled_back = True

    def __enter__(self) -> FixtureDatabase:
        return self

    def __exit__(self, *args: Any) -> None:
        self.db.close()


class Rows:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self.rows = rows

    def fetchone(self) -> tuple[Any, ...] | None:
        return self.rows[0] if self.rows else None

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self.rows


@pytest.fixture
def database():
    connection = FixtureDatabase()
    yield connection
    connection.db.close()


@pytest.fixture
def url(tmp_path, monkeypatch):
    for key in list(precheck.os.environ):
        if key.startswith("PG"):
            monkeypatch.delenv(key)
    ca = tmp_path / "synthetic-ca.pem"
    ca.write_text("synthetic fixture; never used for a TLS connection")
    return (
        "postgresql://synthetic:synthetic-password@"
        + precheck.house.EXPECTED_DATABASE_HOST
        + "/production?"
        + urlencode({"sslmode": "verify-full", "sslrootcert": str(ca)})
    )


def test_clear_fixture_reports_only_fixed_fields(database):
    database.db.executemany(
        "INSERT INTO public.scribe_capture_provider_link VALUES (?, ?, ?)",
        [("synthetic-practice-a", "synthetic-tenant-a", None)] * 2,
    )
    database.db.executemany(
        "INSERT INTO public.provider_practice_authority VALUES (?)",
        [(None,), ("synthetic-practice-a",)],
    )
    report = precheck.collect(database, CONTRACT)
    assert report == {
        "migration_head": "49126bf99c72",
        "repository_head": "69bad2e072e2",
        "is_ancestor_of_repository_head": True,
        "e78845cecca6_would_abort": False,
        "abort_counts": {"practice_conflicts": 0, "reused_uuids": 0, "zero_uuids": 0},
        "provider_practice_authority_null_practice_id_count": 1,
    }
    assert "synthetic" not in json.dumps(report)
    assert database.queries == [
        precheck.BOUNDARY,
        precheck.house.TLS_STATUS_QUERY,
        precheck.HEADS,
        precheck.ABORT_COUNTS,
        precheck.NULL_PRACTICE_COUNT,
    ]


@pytest.mark.parametrize(
    ("links", "expected"),
    [
        ([("p", "a", None), ("p", "b", "revoked")], (1, 0, 0)),
        ([("p", "a", None), ("q", "a", "revoked")], (0, 1, 0)),
        ([("p", ZERO, "revoked")], (0, 0, 1)),
        ([("p", None, None)], (1, 0, 0)),
        ([(None, "a", None)], (0, 1, 0)),
        ([("p", "a", None), ("p", None, None)], (0, 0, 0)),
        ([], (0, 0, 0)),
    ],
)
def test_exact_abort_conditions_including_revoked_and_null_semantics(
    database, links, expected
):
    database.db.executemany(
        "INSERT INTO public.scribe_capture_provider_link VALUES (?, ?, ?)", links
    )
    report = precheck.collect(database, CONTRACT)
    assert tuple(report["abort_counts"].values()) == expected
    assert report["e78845cecca6_would_abort"] == any(expected)


def test_query_matches_source_guard_without_importing_migration():
    tree = ast.parse(
        next((ROOT / "migrations/versions").glob("*e78845cecca6*.py")).read_text()
    )
    upgrade = next(
        n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "upgrade"
    )
    first = upgrade.body[0]
    assert isinstance(first, ast.Expr) and isinstance(first.value, ast.Call)
    sql = ast.literal_eval(first.value.args[0])
    source_select = sql[
        sql.index("WITH historical AS") : sql.index("INTO practice_conflicts")
    ]

    def normalize(value: str) -> str:
        return " ".join(value.replace("public.", "").split())

    assert normalize(precheck.ABORT_COUNTS) == normalize(source_select)


@pytest.mark.parametrize("read_only", ["off", "false", None])
def test_refuses_non_read_only_session_before_data_queries(database, read_only):
    database.boundary = ("production", "public", read_only, "repeatable read")
    with pytest.raises(precheck.house.InventoryFailure):
        precheck.collect(database, CONTRACT)
    assert database.queries == [precheck.BOUNDARY]


@pytest.mark.parametrize(
    "boundary",
    [
        ("staging", "public", "on", "repeatable read"),
        ("production", "other", "on", "repeatable read"),
        ("production", "public", "on", "read committed"),
    ],
)
def test_refuses_wrong_database_schema_or_isolation(database, boundary):
    database.boundary = boundary
    with pytest.raises(precheck.house.InventoryFailure):
        precheck.collect(database, CONTRACT)
    assert database.queries == [precheck.BOUNDARY]


@pytest.mark.parametrize("tls", [(False, None), (True, "TLSv1.1"), None])
def test_refuses_unverified_tls_before_data_queries(database, tls):
    database.tls = tls
    with pytest.raises(precheck.house.InventoryFailure):
        precheck.collect(database, CONTRACT)
    assert precheck.HEADS not in database.queries


@pytest.mark.parametrize(
    "heads", [[], ["49126bf99c72", "ddd6261fa36f"], ["synthetic-private-text"], [None]]
)
def test_refuses_ambiguous_or_non_revision_heads(database, heads):
    database.db.execute("DELETE FROM public.alembic_version")
    database.db.executemany(
        "INSERT INTO public.alembic_version VALUES (?)", [(h,) for h in heads]
    )
    with pytest.raises(precheck.house.InventoryFailure):
        precheck.collect(database, CONTRACT)
    assert precheck.ABORT_COUNTS not in database.queries


@pytest.mark.parametrize(
    "head",
    ["49126bf99c72", "ddd6261fa36f", "e78845cecca6", "69bad2e072e2", "aaaaaaaaaaaa"],
)
def test_repo_ancestry_includes_equal_and_reports_unknown_as_stop(database, head):
    database.db.execute("UPDATE public.alembic_version SET version_num = ?", (head,))
    assert precheck.collect(database, CONTRACT)["is_ancestor_of_repository_head"] == (
        head != "aaaaaaaaaaaa"
    )


@pytest.mark.parametrize("row", [None, (), (True,), (-1,), ("0",), (0.0,), (0, 0)])
def test_count_values_must_be_exact_nonnegative_integers(row):
    with pytest.raises(precheck.house.InventoryFailure):
        precheck.integer_counts(row, 1)


def install_connection(monkeypatch, url, database):
    monkeypatch.setenv("DATABASE_URL", url)
    calls = []

    def connect(**parameters):
        calls.append(parameters)
        return database

    monkeypatch.setattr(precheck.psycopg, "connect", connect)
    return calls


@pytest.mark.parametrize("mode", ["prefer", "require", "allow", "disable", "verify-ca"])
def test_cli_refuses_tls_downgrades_without_connecting(monkeypatch, url, capsys, mode):
    calls = install_connection(monkeypatch, url.replace("verify-full", mode), None)
    assert precheck.main() == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert (
        captured.err
        == (precheck.PREFER_REFUSAL if mode == "prefer" else precheck.REFUSAL) + "\n"
    )
    assert calls == []
    assert "synthetic-password" not in captured.err


@pytest.mark.parametrize(
    "change", ["extra", "duplicate", "host", "database", "missing_ca", "fragment"]
)
def test_url_refuses_ambiguous_routing_and_trust(url, change):
    changed = {
        "extra": url + "&options=unsafe",
        "duplicate": url + "&sslmode=verify-full",
        "host": url.replace(precheck.house.EXPECTED_DATABASE_HOST, "synthetic.invalid"),
        "database": url.replace("/production?", "/staging?"),
        "missing_ca": url.split("&sslrootcert=")[0],
        "fragment": url + "#synthetic",
    }[change]
    with pytest.raises((precheck.house.InventoryFailure, ValueError)):
        precheck.connection_parameters(changed)


def test_ambient_libpq_parameters_refuse(monkeypatch, url):
    monkeypatch.setenv("PGHOSTADDR", "127.0.0.1")
    with pytest.raises(precheck.house.InventoryFailure):
        precheck.connection_parameters(url)


@pytest.mark.parametrize("blocked", [False, True])
def test_cli_one_read_only_snapshot_rolls_back_and_emits_aggregate_report(
    monkeypatch, url, capsys, blocked
):
    database = FixtureDatabase()
    if blocked:
        database.db.execute(
            "INSERT INTO public.scribe_capture_provider_link VALUES ('p', ?, NULL)",
            (ZERO,),
        )
    calls = install_connection(monkeypatch, url, database)
    assert precheck.main() == int(blocked)
    captured = capsys.readouterr()
    assert captured.err == ""
    assert json.loads(captured.out)["e78845cecca6_would_abort"] == blocked
    assert database.queries[0] == precheck.TRANSACTION
    assert database.queries.count(precheck.TRANSACTION) == 1
    assert database.rolled_back
    assert len(calls) == 1 and calls[0]["sslmode"] == "verify-full"
    assert "default_transaction_read_only=on" in calls[0]["options"]
    assert "synthetic-password" not in captured.out


def test_unknown_ancestor_returns_stop(monkeypatch, url, capsys):
    database = FixtureDatabase()
    database.db.execute("UPDATE public.alembic_version SET version_num='aaaaaaaaaaaa'")
    install_connection(monkeypatch, url, database)
    assert precheck.main() == 1
    assert (
        json.loads(capsys.readouterr().out)["is_ancestor_of_repository_head"] is False
    )


def test_cli_failure_never_emits_driver_details_or_partial_output(
    monkeypatch, url, capsys
):
    monkeypatch.setenv("DATABASE_URL", url)

    def broken(**kwargs):
        raise RuntimeError("synthetic-password synthetic-private-row " + url)

    monkeypatch.setattr(precheck.psycopg, "connect", broken)
    assert precheck.main() == 2
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == precheck.REFUSAL + "\n"


def test_cli_writable_session_refuses_without_a_partial_report(
    monkeypatch, url, capsys
):
    database = FixtureDatabase()
    database.boundary = ("production", "public", "off", "repeatable read")
    install_connection(monkeypatch, url, database)
    assert precheck.main() == 2
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == precheck.REFUSAL + "\n"
    assert database.queries == [precheck.TRANSACTION, precheck.BOUNDARY]


def test_missing_schema_refuses_without_replacing_counts_with_zero(
    monkeypatch, url, capsys
):
    database = FixtureDatabase()
    database.db.execute("DROP TABLE public.scribe_capture_provider_link")
    install_connection(monkeypatch, url, database)
    assert precheck.main() == 2
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == precheck.REFUSAL + "\n"


def test_missing_url_never_connects(monkeypatch, capsys):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(
        precheck.psycopg, "connect", lambda **kwargs: pytest.fail("must not connect")
    )
    assert precheck.main() == 2
    assert capsys.readouterr().out == ""
