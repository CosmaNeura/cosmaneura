import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

MIGRATION_PATH = (
    Path(__file__).parents[1]
    / "migrations/versions/2026_08_29_9a2399212488_publish_baa_v2_3.py"
)


def _migration_module():
    spec = importlib.util.spec_from_file_location("baa_v23_migration", MIGRATION_PATH)
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
    def __init__(self, material_references: int = 0) -> None:
        self.material_references = material_references
        self.statements: list[str] = []

    def execute(self, statement) -> _Result:
        rendered = str(statement)
        self.statements.append(rendered)
        value = self.material_references if "FROM baa_acceptance" in rendered else 0
        return _Result(value)


def test_upgrade_publishes_v23_then_makes_it_current() -> None:
    migration = _migration_module()
    statements: list[str] = []
    setattr(
        migration,
        "op",
        SimpleNamespace(execute=lambda statement: statements.append(str(statement))),
    )

    migration.upgrade()

    assert migration.BAA_V23_SHA256 == (
        "9bdc110b6b2ffcc9f5e7aee653af499357d2f35dccf43c57d8bbcf70094ed34e"
    )
    assert migration.BAA_V23_PUBLISHED_AT.isoformat() == "2026-08-29T00:00:00+00:00"
    assert "INSERT INTO baa_version" in statements[0]
    assert "SET is_current = (version = :version)" in statements[1]


def test_downgrade_refuses_to_discard_v23_acceptance_evidence() -> None:
    migration = _migration_module()
    connection = _Connection(material_references=2)
    mutations: list[str] = []
    setattr(
        migration,
        "op",
        SimpleNamespace(
            get_context=lambda: SimpleNamespace(as_sql=False),
            get_bind=lambda: connection,
            execute=lambda statement: mutations.append(str(statement)),
        ),
    )

    with pytest.raises(RuntimeError, match="material_references=2"):
        migration.downgrade()

    assert connection.statements[0].startswith("LOCK TABLE baa_acceptance")
    assert mutations == []


def test_downgrade_restores_v22_only_before_v23_is_accepted() -> None:
    migration = _migration_module()
    connection = _Connection()
    mutations: list[str] = []
    setattr(
        migration,
        "op",
        SimpleNamespace(
            get_context=lambda: SimpleNamespace(as_sql=False),
            get_bind=lambda: connection,
            execute=lambda statement: mutations.append(str(statement)),
        ),
    )

    migration.downgrade()

    assert "DELETE FROM baa_version" in mutations[0]
    assert "SET is_current = (version = :version)" in mutations[1]


def test_downgrade_fails_closed_offline() -> None:
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
        migration._refuse_material_downgrade()
