import importlib.util
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

import sqlalchemy as sa
from app.models.authority import AuthorityAuditEvent, AuthorityIdempotencyRecord
from app.models.authority_usage import AgentModelUsageEvent
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql.elements import TextClause

MIGRATION_PATH = (
    Path(__file__).parents[2]
    / "migrations/versions/2026_08_19_d4e5f6a7b8c9_add_v7_authority_audit.py"
)


def _migration_module():
    spec = importlib.util.spec_from_file_location(
        "v7_authority_migration", MIGRATION_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_v7_model_metadata_matches_deployed_audit_contract() -> None:
    usage_table = cast(sa.Table, sa.inspect(AgentModelUsageEvent).local_table)
    audit_table = cast(sa.Table, sa.inspect(AuthorityAuditEvent).local_table)
    idempotency_table = cast(
        sa.Table,
        sa.inspect(AuthorityIdempotencyRecord).local_table,
    )

    assert usage_table.comment == (
        "Append-only provider-reported model call without content or PHI."
    )
    assert audit_table.comment == (
        "Append-only, privacy-minimized authoritative platform audit fact."
    )
    assert idempotency_table.comment == (
        "Twenty-four-hour receipt binding for Agent API writes."
    )
    platform_refs = audit_table.c.platform_refs
    assert isinstance(platform_refs.type, JSONB)
    assert platform_refs.type.compile(dialect=postgresql.dialect()) == "JSONB"
    assert platform_refs.type.compile(dialect=sqlite.dialect()) == "JSON"
    server_default = platform_refs.server_default
    assert isinstance(server_default, sa.DefaultClause)
    default_arg = server_default.arg
    assert isinstance(default_arg, TextClause)
    assert str(default_arg) == "'[]'"
    assert str(default_arg.compile(dialect=postgresql.dialect())) == "'[]'"
    assert str(default_arg.compile(dialect=sqlite.dialect())) == "'[]'"


class _Result:
    def __init__(self, value: bool) -> None:
        self.value = value

    def scalar(self) -> bool:
        return self.value


class _Connection:
    def __init__(self, populated_table: str | None = None) -> None:
        self.populated_table = populated_table
        self.statements: list[str] = []

    def execute(self, statement) -> _Result:
        rendered = str(statement)
        self.statements.append(rendered)
        return _Result(
            self.populated_table is not None
            and f'FROM "{self.populated_table}"' in rendered
        )


def test_downgrade_guard_fails_closed_offline() -> None:
    migration = _migration_module()
    setattr(
        migration,
        "op",
        SimpleNamespace(
            get_context=lambda: SimpleNamespace(as_sql=True),
            get_bind=lambda: pytest.fail("offline guard must not request a connection"),
        ),
    )

    with pytest.raises(RuntimeError, match="unsupported in offline mode"):
        migration._refuse_populated_or_offline_downgrade()


def test_downgrade_guard_refuses_any_populated_durable_table() -> None:
    migration = _migration_module()
    connection = _Connection(populated_table="agent_model_usage_event")
    setattr(
        migration,
        "op",
        SimpleNamespace(
            get_context=lambda: SimpleNamespace(as_sql=False),
            get_bind=lambda: connection,
        ),
    )

    with pytest.raises(RuntimeError, match="durable records exist"):
        migration._refuse_populated_or_offline_downgrade()
    assert len(connection.statements) == 2 * len(migration._DURABLE_V7_TABLES)
    assert all(
        statement.startswith("LOCK TABLE")
        for statement in connection.statements[: len(migration._DURABLE_V7_TABLES)]
    )


def test_downgrade_guard_allows_only_an_empty_online_schema() -> None:
    migration = _migration_module()
    connection = _Connection()
    setattr(
        migration,
        "op",
        SimpleNamespace(
            get_context=lambda: SimpleNamespace(as_sql=False),
            get_bind=lambda: connection,
        ),
    )

    migration._refuse_populated_or_offline_downgrade()
    assert len(connection.statements) == 2 * len(migration._DURABLE_V7_TABLES)
