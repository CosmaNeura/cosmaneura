import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

MIGRATION_PATH = (
    Path(__file__).parents[1]
    / "migrations/versions/2026_09_28_ed76434b6308_add_stripe_paid_seat_high_water.py"
)


def _migration_module():
    spec = importlib.util.spec_from_file_location(
        "stripe_paid_seat_high_water_migration",
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


def _online_op(connection: _Connection, dropped: list[tuple[str, str]]):
    return SimpleNamespace(
        get_context=lambda: SimpleNamespace(as_sql=False),
        get_bind=lambda: connection,
        drop_constraint=lambda name, table, type_: dropped.append((table, name)),
        drop_column=lambda table, column: dropped.append((table, column)),
    )


def test_paid_seat_high_water_downgrade_fails_closed_offline() -> None:
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
        migration.downgrade()


def test_paid_seat_high_water_downgrade_refuses_recorded_periods() -> None:
    migration = _migration_module()
    connection = _Connection(populated=3)
    dropped: list[tuple[str, str]] = []
    setattr(migration, "op", _online_op(connection, dropped))

    with pytest.raises(RuntimeError, match="practices_with_paid_seat_high_water=3"):
        migration.downgrade()
    assert connection.statements[0].startswith("LOCK TABLE practice")
    assert "stripe_paid_seat_high_water IS NOT NULL" in connection.statements[1]
    assert "stripe_paid_seat_period_start IS NOT NULL" in connection.statements[1]
    assert dropped == []


def test_paid_seat_high_water_downgrade_allows_only_empty_columns() -> None:
    migration = _migration_module()
    connection = _Connection()
    dropped: list[tuple[str, str]] = []
    setattr(migration, "op", _online_op(connection, dropped))

    migration.downgrade()

    assert dropped == [
        ("practice", "ck_practice_stripe_paid_seat_period_pairing"),
        ("practice", "ck_practice_stripe_paid_seat_high_water_bounds"),
        ("practice", "stripe_paid_seat_period_start"),
        ("practice", "stripe_paid_seat_high_water"),
    ]


def test_paid_seat_high_water_upgrade_adds_nullable_bounded_paired_columns() -> None:
    migration = _migration_module()
    columns: list[tuple[str, bool]] = []
    checks: dict[str, str] = {}
    setattr(
        migration,
        "op",
        SimpleNamespace(
            add_column=lambda _table, column: columns.append(
                (column.name, column.nullable)
            ),
            create_check_constraint=lambda name, _table, condition: checks.update(
                {name: condition}
            ),
        ),
    )

    migration.upgrade()

    assert columns == [
        ("stripe_paid_seat_high_water", True),
        ("stripe_paid_seat_period_start", True),
    ]
    assert (
        "BETWEEN 1 AND 20" in checks["ck_practice_stripe_paid_seat_high_water_bounds"]
    )
    assert checks["ck_practice_stripe_paid_seat_period_pairing"] == (
        "(stripe_paid_seat_high_water IS NULL) "
        "= (stripe_paid_seat_period_start IS NULL)"
    )
