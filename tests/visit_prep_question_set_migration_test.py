import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

MIGRATION_PATH = (
    Path(__file__).parent
    / "../migrations/versions/2026_08_25_271e4439bff6_add_visit_prep_question_sets.py"
).resolve()


def _migration_module():
    spec = importlib.util.spec_from_file_location(
        "visit_prep_question_set_migration",
        MIGRATION_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Result:
    def __init__(self, value: bool) -> None:
        self.value = value

    def scalar_one(self) -> bool:
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


def test_question_set_downgrade_fails_closed_offline() -> None:
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


@pytest.mark.parametrize(
    "populated_table",
    (
        "visit_prep_question",
        "visit_prep_question_set_version",
        "visit_prep_question_set",
    ),
)
def test_question_set_downgrade_refuses_any_populated_table(
    populated_table: str,
) -> None:
    migration = _migration_module()
    connection = _Connection(populated_table=populated_table)
    setattr(
        migration,
        "op",
        SimpleNamespace(
            get_context=lambda: SimpleNamespace(as_sql=False),
            get_bind=lambda: connection,
        ),
    )

    with pytest.raises(RuntimeError, match=populated_table):
        migration._refuse_populated_or_offline_downgrade()
    assert connection.statements[0].startswith("LOCK TABLE")
    assert len(connection.statements) == 1 + len(migration._QUESTION_TABLES)


def test_question_set_downgrade_allows_only_empty_online_schema() -> None:
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

    assert connection.statements[0].startswith("LOCK TABLE")
    assert len(connection.statements) == 1 + len(migration._QUESTION_TABLES)
