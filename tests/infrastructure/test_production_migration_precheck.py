from __future__ import annotations

import ast
import json
import sqlite3
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import psycopg
import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from psycopg.conninfo import make_conninfo

from scripts import production_migration_precheck as precheck

ROOT = Path(__file__).resolve().parents[2]
ZERO = "00000000-0000-0000-0000-000000000000"
CONTRACT = precheck.house.repository_migration_contract(ROOT)


@pytest.fixture(scope="module")
def repository_scripts(tmp_path_factory):
    config = Config(str(ROOT / "alembic.ini"))
    script_location = config.get_main_option("script_location")
    if script_location is None:
        raise RuntimeError("Missing script_location in alembic.ini")
    config.set_main_option("script_location", str(ROOT / script_location))
    source = ScriptDirectory.from_config(config)
    fixture = tmp_path_factory.mktemp("migration-metadata")
    versions = fixture / "versions"
    versions.mkdir()
    # Copy only graph metadata: historical migration imports load app models.
    # Alembic orders the real repository graph without executing migration code.
    for path in Path(source.versions).glob("*.py"):
        metadata = {}
        for node in ast.parse(path.read_text()).body:
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, ast.AnnAssign):
                targets = [node.target]
            else:
                continue
            for target in targets:
                if isinstance(target, ast.Name) and target.id in {
                    "revision",
                    "down_revision",
                    "branch_labels",
                    "depends_on",
                }:
                    if node.value is None:
                        raise RuntimeError(f"Missing value for {target.id} in {path}")
                    metadata[target.id] = ast.literal_eval(node.value)
        if "revision" in metadata:
            (versions / path.name).write_text(
                "\n".join(f"{key} = {value!r}" for key, value in metadata.items())
            )
    config.set_main_option("script_location", str(fixture))
    return ScriptDirectory.from_config(config)


def expected_pending(repository_scripts, head):
    return [
        revision.revision
        for revision in reversed(list(repository_scripts.walk_revisions(base=head)))
        if revision.revision != head
    ]


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
        if query == precheck.GUARD_TABLES:
            available = []
            for name in ("scribe_capture_provider_link", "provider_practice_authority"):
                count = self.db.execute(
                    "SELECT count(*) FROM public.sqlite_master WHERE type='table' AND name=?",
                    (name,),
                ).fetchone()[0]
                available.append(bool(count))
            return Rows([tuple(available)])
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


def test_clear_fixture_reports_only_fixed_fields(database, repository_scripts):
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
        "scope": "core",
        "migration_head": "49126bf99c72",
        "repository_head": repository_scripts.get_current_head(),
        "is_ancestor_of_repository_head": True,
        "pending_migrations": expected_pending(repository_scripts, "49126bf99c72"),
        "e78845cecca6_would_abort": False,
        "abort_counts": {"practice_conflicts": 0, "reused_uuids": 0, "zero_uuids": 0},
        "provider_practice_authority_null_practice_id_count": 1,
        "refusal_reasons": ["pending_migrations"],
    }
    assert "synthetic" not in json.dumps(report)
    assert database.queries == [
        precheck.BOUNDARY,
        precheck.house.TLS_STATUS_QUERY,
        precheck.HEADS,
        precheck.GUARD_TABLES,
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
        == (precheck.PREFER_REFUSAL if mode == "prefer" else precheck.REFUSAL)
        + " Reason: configuration_refused.\n"
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
    database.db.execute(
        "UPDATE public.alembic_version SET version_num=?", (CONTRACT.head,)
    )
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
    assert f"lock_timeout={precheck.LOCK_TIMEOUT_MILLISECONDS}" in calls[0]["options"]
    assert (
        f"idle_in_transaction_session_timeout={precheck.IDLE_TRANSACTION_TIMEOUT_MILLISECONDS}"
        in calls[0]["options"]
    )
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
    assert (
        captured.out == ""
        and captured.err == precheck.REFUSAL + " Reason: snapshot_refused.\n"
    )


def test_cli_writable_session_refuses_without_a_partial_report(
    monkeypatch, url, capsys
):
    database = FixtureDatabase()
    database.boundary = ("production", "public", "off", "repeatable read")
    install_connection(monkeypatch, url, database)
    assert precheck.main() == 2
    captured = capsys.readouterr()
    assert (
        captured.out == ""
        and captured.err == precheck.REFUSAL + " Reason: snapshot_refused.\n"
    )
    assert database.queries == [precheck.TRANSACTION, precheck.BOUNDARY]


@pytest.mark.parametrize(
    "missing",
    [
        ("scribe_capture_provider_link",),
        ("provider_practice_authority",),
        ("scribe_capture_provider_link", "provider_practice_authority"),
    ],
)
def test_missing_schema_preserves_head_and_empty_counts_with_stop(
    monkeypatch, url, capsys, missing
):
    database = FixtureDatabase()
    for name in missing:
        database.db.execute(f"DROP TABLE public.{name}")
    install_connection(monkeypatch, url, database)
    assert precheck.main() == 1
    captured = capsys.readouterr()
    assert captured.err == ""
    report = json.loads(captured.out)
    assert report["migration_head"] == "49126bf99c72"
    assert report["abort_counts"] == {}
    assert report["e78845cecca6_would_abort"] is None
    assert report["provider_practice_authority_null_practice_id_count"] is None
    assert "guard_tables_missing" in report["refusal_reasons"]
    assert set(report["refusal_reasons"]) <= precheck.REFUSAL_REASONS
    assert precheck.ABORT_COUNTS not in database.queries
    assert precheck.NULL_PRACTICE_COUNT not in database.queries
    assert database.rolled_back


def test_missing_url_never_connects(monkeypatch, capsys):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(
        precheck.psycopg, "connect", lambda **kwargs: pytest.fail("must not connect")
    )
    assert precheck.main() == 2
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize(
    "head",
    [
        "49126bf99c72",
        "ddd6261fa36f",
        "e78845cecca6",
        "head-1",
        "head",
        "aaaaaaaaaaaa",
    ],
)
def test_cli_lists_pending_migrations_and_behind_is_stop(
    monkeypatch, url, capsys, head, repository_scripts
):
    if head != "aaaaaaaaaaaa":
        if head == "head-1":
            head = repository_scripts.get_revision("head").down_revision
        head = repository_scripts.get_revision(head).revision
        pending = expected_pending(repository_scripts, head)
        if head != repository_scripts.get_current_head():
            assert pending
    else:
        pending = []
    database = FixtureDatabase()
    database.db.execute("UPDATE public.alembic_version SET version_num=?", (head,))
    install_connection(monkeypatch, url, database)
    assert precheck.main() == int(head != CONTRACT.head)
    report = json.loads(capsys.readouterr().out)
    assert report["pending_migrations"] == pending
    assert report["refusal_reasons"] == (
        ["pending_migrations"]
        if pending
        else ["migration_head_unknown"]
        if head != CONTRACT.head
        else []
    )
    assert database.rolled_back


@pytest.mark.parametrize("query", [precheck.ABORT_COUNTS, precheck.NULL_PRACTICE_COUNT])
def test_table_disappearing_after_catalog_check_retains_head_not_partial_counts(
    monkeypatch, url, capsys, query
):
    database = FixtureDatabase()
    execute = database.execute

    def raced(statement, params=()):
        if statement == query:
            raise precheck.psycopg.errors.UndefinedTable("synthetic-private-row")
        return execute(statement, params)

    monkeypatch.setattr(database, "execute", raced)
    install_connection(monkeypatch, url, database)
    assert precheck.main() == 1
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert report["migration_head"] == "49126bf99c72"
    assert report["abort_counts"] == {}
    assert report["provider_practice_authority_null_practice_id_count"] is None
    assert report["e78845cecca6_would_abort"] is None
    assert report["refusal_reasons"] == ["pending_migrations", "guard_tables_missing"]
    assert captured.err == "" and "synthetic-private-row" not in captured.out
    assert database.rolled_back


@pytest.mark.parametrize("kind", ["relative", "missing", "directory", "unreadable"])
def test_cli_refuses_ca_bundle_path_without_connecting(
    monkeypatch, url, tmp_path, capsys, kind
):
    ca = tmp_path / "synthetic-ca.pem"
    if kind == "relative":
        path = "synthetic-ca.pem"
    elif kind == "missing":
        path = str(tmp_path / "missing.pem")
    elif kind == "directory":
        path = str(tmp_path)
    else:
        path = str(ca)
        monkeypatch.setattr(precheck.os, "access", lambda *args: False)
    parsed = urlsplit(url)
    query = dict(parse_qsl(parsed.query))
    query["sslrootcert"] = path
    changed = urlunsplit(parsed._replace(query=urlencode(query)))
    calls = install_connection(monkeypatch, changed, None)
    assert precheck.main() == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == precheck.REFUSAL + " Reason: configuration_refused.\n"
    assert calls == [] and path not in captured.err


@pytest.mark.parametrize(
    "shape",
    [
        "scheme",
        "no_user",
        "no_password",
        "extra_at",
        "wrong_port",
        "bad_port",
        "multi_host",
        "encoded_database",
        "empty_mode",
        "empty_ca",
        "bare_query",
        "hostaddr",
        "service",
        "client_key",
        "duplicate_ca",
        "no_url",
    ],
)
def test_cli_url_shape_refusals_are_fixed_and_never_connect(
    monkeypatch, url, capsys, shape
):
    host = precheck.house.EXPECTED_DATABASE_HOST
    changed = {
        "scheme": url.replace("postgresql:", "https:"),
        "no_user": url.replace("synthetic:synthetic-password@", ":synthetic-password@"),
        "no_password": url.replace("synthetic:synthetic-password@", "synthetic@"),
        "extra_at": url.replace("synthetic-password@", "synthetic@password@"),
        "wrong_port": url.replace(host, host + ":5433"),
        "bad_port": url.replace(host, host + ":bad"),
        "multi_host": url.replace(host, "synthetic.invalid," + host),
        "encoded_database": url.replace("/production?", "/%70roduction?"),
        "empty_mode": url.replace("sslmode=verify-full", "sslmode="),
        "empty_ca": url.split("&sslrootcert=")[0] + "&sslrootcert=",
        "bare_query": url + "&unsafe",
        "hostaddr": url + "&hostaddr=127.0.0.1",
        "service": url + "&service=synthetic",
        "client_key": url + "&sslkey=synthetic",
        "duplicate_ca": url + "&sslrootcert=synthetic",
        "no_url": "",
    }[shape]
    calls = install_connection(monkeypatch, changed, None)
    assert precheck.main() == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == precheck.REFUSAL + " Reason: configuration_refused.\n"
    assert calls == [] and "synthetic-password" not in captured.err


def test_operator_doc_uses_azure_no_echo_recipe_and_dynamic_head():
    doc = (ROOT / "docs/production-migration-precheck.md").read_text()
    assert "**inside Azure**" in doc and "**Core only**" in doc
    assert "`cosmaneura-os` #377" in doc and "separate database" in doc
    assert "set +x" in doc and "read -r -s -p" in doc
    assert "trap 'unset DATABASE_URL' EXIT" in doc
    assert CONTRACT.head not in doc
    assert "pending_migrations" in doc and "guard_tables_missing" in doc


def test_connection_parameters_disable_client_certificates(url):
    params = precheck.connection_parameters(url)
    assert params["sslcertmode"] == "disable"
    assert "sslcert" not in params and "sslkey" not in params
    assert params["sslmode"] == "verify-full"
    assert params["gssencmode"] == "disable"


def test_conninfo_builds_with_client_cert_disabled_on_libpq_16_or_later(url):
    conninfo = make_conninfo(**precheck.connection_parameters(url))
    assert "sslcertmode=disable" in conninfo
    assert psycopg.pq.version() >= 160000


def test_connection_parameters_refuse_libpq_older_than_16(url, monkeypatch):
    monkeypatch.setattr(psycopg.pq, "version", lambda: 150000)
    with pytest.raises(precheck.house.InventoryFailure):
        precheck.connection_parameters(url)
