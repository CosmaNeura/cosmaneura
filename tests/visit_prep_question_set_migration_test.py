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
            and f'FROM public."{self.populated_table}"' in rendered
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
    assert connection.statements[0] == (
        'LOCK TABLE public."visit_prep_question_set", '
        'public."visit_prep_question_set_version", '
        'public."visit_prep_question" IN ACCESS EXCLUSIVE MODE'
    )
    assert len(connection.statements) == 1 + len(migration._QUESTION_TABLES_CHILD_FIRST)
    assert all(
        f'FROM public."{table_name}"' in statement
        for table_name, statement in zip(
            migration._QUESTION_TABLES_CHILD_FIRST,
            connection.statements[1:],
            strict=True,
        )
    )


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

    assert connection.statements[0] == (
        'LOCK TABLE public."visit_prep_question_set", '
        'public."visit_prep_question_set_version", '
        'public."visit_prep_question" IN ACCESS EXCLUSIVE MODE'
    )
    assert len(connection.statements) == 1 + len(migration._QUESTION_TABLES_CHILD_FIRST)


def test_question_set_migration_defines_immutable_creation_payload_fingerprint() -> (
    None
):
    migration_source = MIGRATION_PATH.read_text()

    assert '"creation_payload_sha256"' in migration_source
    assert "creation_payload_sha256 ~ '^[0-9a-f]{64}$'" in migration_source
    assert (
        "NEW.creation_payload_sha256 IS DISTINCT FROM "
        "OLD.creation_payload_sha256" in migration_source
    )


def test_question_set_trigger_functions_are_schema_hardened() -> None:
    migration_source = MIGRATION_PATH.read_text()

    function_names = (
        "cosmas_guard_visit_prep_question_set_mutation",
        "cosmas_guard_visit_prep_question_set_version_mutation",
        "cosmas_guard_visit_prep_question_mutation",
    )
    for function_name in function_names:
        assert f"CREATE FUNCTION public.{function_name}()" in migration_source
        assert f"EXECUTE FUNCTION public.{function_name}()" in migration_source

    assert migration_source.count("SET search_path = pg_catalog, public") == 3
    assert migration_source.count("FROM public.visit_prep_question_set_version") == 2
    assert "BEFORE UPDATE ON public.visit_prep_question_set" in migration_source
    assert (
        "BEFORE UPDATE OR DELETE ON public.visit_prep_question_set_version"
        in migration_source
    )
    assert (
        "BEFORE INSERT OR UPDATE OR DELETE ON public.visit_prep_question"
        in migration_source
    )


def test_question_set_downgrade_qualifies_every_database_object() -> None:
    downgrade_source = MIGRATION_PATH.read_text().split("def downgrade() -> None:", 1)[
        1
    ]

    assert "ON public.visit_prep_question" in downgrade_source
    assert "ON public.visit_prep_question_set_version" in downgrade_source
    assert "ON public.visit_prep_question_set" in downgrade_source
    assert downgrade_source.count("DROP FUNCTION IF EXISTS") == 3
    for function_name in (
        "cosmas_guard_visit_prep_question_set_mutation",
        "cosmas_guard_visit_prep_question_set_version_mutation",
        "cosmas_guard_visit_prep_question_mutation",
    ):
        assert f"public.{function_name}()" in downgrade_source
    assert 'op.drop_table("visit_prep_question", schema="public")' in downgrade_source
    assert (
        'op.drop_table("visit_prep_question_set_version", schema="public")'
        in downgrade_source
    )
    assert (
        'op.drop_table("visit_prep_question_set", schema="public")' in downgrade_source
    )
    assert (
        "DROP INDEX IF EXISTS public.visit_prep_qset_version_supersedes_idx"
        in downgrade_source
    )
    assert 'schema="public"' in downgrade_source
