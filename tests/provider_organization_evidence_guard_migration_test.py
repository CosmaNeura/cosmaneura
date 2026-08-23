import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

MIGRATION_PATH = (
    Path(__file__).parents[1]
    / "migrations/versions/2026_08_23_4af72b94781e_guard_provider_practice_bootstrap.py"
)


def _migration_module():
    spec = importlib.util.spec_from_file_location(
        "provider_organization_evidence_guard_migration",
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
    def __init__(self, populated_query_fragment: str | None = None) -> None:
        self.populated_query_fragment = populated_query_fragment
        self.statements: list[str] = []

    def execute(self, statement) -> _Result:
        rendered = str(statement)
        self.statements.append(rendered)
        return _Result(
            int(
                self.populated_query_fragment is not None
                and self.populated_query_fragment in rendered
            )
        )


def test_evidence_guard_downgrade_fails_closed_offline() -> None:
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


def test_evidence_guard_downgrade_refuses_live_organization_data() -> None:
    migration = _migration_module()
    connection = _Connection("FROM organization_roster_approval")
    setattr(
        migration,
        "op",
        SimpleNamespace(
            get_context=lambda: SimpleNamespace(as_sql=False),
            get_bind=lambda: connection,
        ),
    )

    with pytest.raises(RuntimeError, match="roster_approvals=1"):
        migration._refuse_populated_downgrade()
    assert connection.statements[0].startswith("LOCK TABLE")


def test_evidence_guard_downgrade_allows_only_empty_online_schema() -> None:
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

    migration._refuse_populated_downgrade()
    assert connection.statements[0].startswith("LOCK TABLE")
    assert len(connection.statements) == 6
