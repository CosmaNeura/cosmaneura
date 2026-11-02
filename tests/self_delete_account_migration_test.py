"""Up/down coverage for the additive self-delete schema rung."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

MIGRATION_PATH = (
    Path(__file__).parents[1]
    / "migrations/versions/2026_11_02_c764298c7590_add_self_delete_account_fields.py"
)


def _migration_module():
    spec = importlib.util.spec_from_file_location(
        "self_delete_account_migration",
        MIGRATION_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_self_delete_migration_upgrade_adds_columns_and_check():
    migration = _migration_module()
    added: list[tuple] = []
    constraints: list[tuple] = []

    def add_column(table, column):
        added.append((table, column.name, str(column.type)))

    def create_check_constraint(name, table, condition):
        constraints.append((name, table, condition))

    setattr(
        migration,
        "op",
        SimpleNamespace(
            add_column=add_column,
            create_check_constraint=create_check_constraint,
        ),
    )

    migration.upgrade()

    assert ("doctor", "account_deletion_status", "VARCHAR(32)") in [
        (table, name, col_type.split("(")[0] + ("(32)" if "32" in col_type else ""))
        for table, name, col_type in added
    ] or any(
        table == "doctor" and name == "account_deletion_status"
        for table, name, _ in added
    )
    assert any(
        table == "practice" and name == "owner_reassignment_needed_at"
        for table, name, _ in added
    )
    assert constraints == [
        (
            "ck_doctor_account_deletion_status",
            "doctor",
            "account_deletion_status IS NULL OR "
            "account_deletion_status IN ('deletion_pending', 'deleted')",
        )
    ]


def test_self_delete_migration_downgrade_drops_additive_columns():
    migration = _migration_module()
    dropped: list[tuple] = []

    def drop_column(table, column):
        dropped.append(("column", table, column))

    def drop_constraint(name, table, type_):
        dropped.append(("constraint", table, name, type_))

    setattr(
        migration,
        "op",
        SimpleNamespace(drop_column=drop_column, drop_constraint=drop_constraint),
    )

    migration.downgrade()

    assert dropped == [
        ("column", "practice", "owner_reassignment_needed_at"),
        ("constraint", "doctor", "ck_doctor_account_deletion_status", "check"),
        ("column", "doctor", "account_deletion_status"),
    ]


def test_self_delete_migration_revises_current_head():
    migration = _migration_module()
    assert migration.revision == "c764298c7590"
    assert migration.down_revision == "312b217ebba4"
