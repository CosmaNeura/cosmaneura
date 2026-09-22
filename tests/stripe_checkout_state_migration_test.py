import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

MIGRATION_PATH = (
    Path(__file__).parents[1]
    / "migrations/versions/2026_08_27_da6e199668a4_persist_stripe_checkout_state.py"
)


def _migration_module():
    spec = importlib.util.spec_from_file_location(
        "stripe_checkout_state_migration",
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


def test_checkout_state_downgrade_fails_closed_offline() -> None:
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
        migration._refuse_populated_downgrade()


def test_checkout_state_downgrade_refuses_material_state() -> None:
    migration = _migration_module()
    connection = _Connection(populated=2)
    dropped_columns: list[tuple[str, str]] = []
    setattr(
        migration,
        "op",
        SimpleNamespace(
            get_context=lambda: SimpleNamespace(as_sql=False),
            get_bind=lambda: connection,
            drop_column=lambda table, column: dropped_columns.append((table, column)),
        ),
    )

    with pytest.raises(RuntimeError, match="practices_with_checkout_state=2"):
        migration.downgrade()
    assert connection.statements[0].startswith("LOCK TABLE practice")
    assert dropped_columns == []
    evidence_query = connection.statements[1]
    assert "stripe_checkout_request_json IS NOT NULL" in evidence_query
    assert "stripe_checkout_request_created_at IS NOT NULL" in evidence_query
    assert "stripe_checkout_entitlement_contract IS NOT NULL" in evidence_query
    assert "stripe_subscription_entitlement_contract IS NOT NULL" in evidence_query
    assert "stripe_trial_clock_policy IS NOT NULL" in evidence_query


def test_checkout_state_downgrade_allows_only_empty_online_schema() -> None:
    migration = _migration_module()
    connection = _Connection()
    dropped_columns: list[tuple[str, str]] = []
    altered_columns: list[tuple[tuple[object, ...], dict[str, object]]] = []
    setattr(
        migration,
        "op",
        SimpleNamespace(
            get_context=lambda: SimpleNamespace(as_sql=False),
            get_bind=lambda: connection,
            drop_column=lambda table, column: dropped_columns.append((table, column)),
            alter_column=lambda *args, **kwargs: altered_columns.append((args, kwargs)),
        ),
    )

    migration.downgrade()
    assert connection.statements[0].startswith("LOCK TABLE practice")
    assert len(connection.statements) == 2
    assert dropped_columns == [
        ("practice", "stripe_subscription_entitlement_contract"),
        ("practice", "stripe_trial_clock_policy"),
        ("practice", "stripe_checkout_entitlement_contract"),
        ("practice", "stripe_checkout_request_created_at"),
        ("practice", "stripe_checkout_request_json"),
    ]
    assert altered_columns[0][0] == ("practice", "trial_ends_at")


def test_checkout_state_upgrade_marks_preexisting_bindings_explicitly() -> None:
    migration = _migration_module()
    added_columns: list[str] = []
    statements: list[str] = []
    setattr(
        migration,
        "op",
        SimpleNamespace(
            alter_column=lambda *_args, **_kwargs: None,
            add_column=lambda _table, column: added_columns.append(column.name),
            execute=lambda statement: statements.append(str(statement)),
        ),
    )

    migration.upgrade()

    assert "stripe_checkout_entitlement_contract" in added_columns
    assert "stripe_subscription_entitlement_contract" in added_columns
    assert any(
        "stripe_checkout_entitlement_contract = " in statement
        and "stripe-legacy-unversioned.v0" in statement
        and "stripe_checkout_session_id IS NOT NULL" in statement
        for statement in statements
    )
    assert any(
        "stripe_subscription_entitlement_contract = " in statement
        and "stripe-legacy-unversioned.v0" in statement
        and "stripe_subscription_id IS NOT NULL" in statement
        for statement in statements
    )
