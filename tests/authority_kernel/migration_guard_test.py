import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

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
