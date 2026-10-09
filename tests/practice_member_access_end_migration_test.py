import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

MIGRATION_PATH = (
    Path(__file__).parents[1]
    / "migrations/versions/2026_09_27_9a23eb377df7_add_practice_member_access_end.py"
)


def _migration_module():
    spec = importlib.util.spec_from_file_location(
        "practice_member_access_end_migration",
        MIGRATION_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Result:
    def __init__(self, value: int) -> None:
        self.value = value

    def scalar_one(self) -> int:
        return self.value


class _Connection:
    def __init__(self, populated: int = 0) -> None:
        self.populated = populated
        self.statements: list[str] = []

    def execute(self, statement) -> _Result:
        rendered = str(statement)
        self.statements.append(rendered)
        return _Result(self.populated if rendered.startswith("SELECT count") else 0)


def _op(connection: _Connection, dropped: list[str], *, offline: bool = False):
    return SimpleNamespace(
        get_context=lambda: SimpleNamespace(as_sql=offline),
        get_bind=lambda: connection,
        drop_constraint=lambda name, *_args, **_kwargs: dropped.append(name),
        drop_column=lambda _table, column: dropped.append(column),
    )


def test_access_end_downgrade_fails_closed_offline() -> None:
    migration = _migration_module()
    connection = _Connection()
    dropped: list[str] = []
    setattr(migration, "op", _op(connection, dropped, offline=True))

    with pytest.raises(RuntimeError, match="unsupported offline"):
        migration.downgrade()
    assert connection.statements == []
    assert dropped == []


def test_access_end_downgrade_refuses_scheduled_access_ends() -> None:
    migration = _migration_module()
    connection = _Connection(populated=3)
    dropped: list[str] = []
    setattr(migration, "op", _op(connection, dropped))

    with pytest.raises(RuntimeError, match="memberships_with_access_end=3"):
        migration.downgrade()
    assert connection.statements[0].startswith("LOCK TABLE practice_member")
    assert "access_ends_at IS NOT NULL" in connection.statements[1]
    assert dropped == []


def test_access_end_downgrade_proceeds_when_unused() -> None:
    migration = _migration_module()
    connection = _Connection(populated=0)
    dropped: list[str] = []
    setattr(migration, "op", _op(connection, dropped))

    migration.downgrade()

    assert dropped == [
        "ck_practice_member_access_end_requires_inactive_seat",
        "access_ends_at",
    ]
