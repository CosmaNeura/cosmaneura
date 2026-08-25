import importlib.util
import re
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

MIGRATION_PATH = (
    Path(__file__).parent
    / "../migrations/versions/2026_08_25_593ea9c376f4_add_visit_prep_aggregate.py"
).resolve()
TEST_CONFTEST_PATH = Path(__file__).with_name("conftest.py")


def _migration_module():
    spec = importlib.util.spec_from_file_location(
        "visit_prep_aggregate_migration",
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


class _CandidateKeyOperations:
    def __init__(
        self,
        *,
        invalid_indexes: set[str] | None = None,
        offline: bool = False,
    ) -> None:
        self.events: list[str] = []
        self.invalid_indexes = invalid_indexes or set()
        self.offline = offline
        self.bind_requested = False

    def get_bind(self):
        self.bind_requested = True
        operations = self

        class _Connection:
            def execute(self, _statement, parameters: dict[str, str]):
                qualified_name = parameters["qualified_index_name"]
                index_name = qualified_name.removeprefix('public."').removesuffix('"')
                return _Result(index_name in operations.invalid_indexes)

        return _Connection()

    def get_context(self):
        operations = self

        class _Context:
            as_sql = operations.offline

            @contextmanager
            def autocommit_block(self):
                operations.events.append("autocommit:start")
                try:
                    yield
                finally:
                    operations.events.append("autocommit:end")

        return _Context()

    def execute(self, statement) -> None:
        self.events.append(" ".join(str(statement).split()))


def _source() -> str:
    return MIGRATION_PATH.read_text(encoding="utf-8")


def _normalized_source() -> str:
    return " ".join(_source().split())


def test_aggregate_migration_has_literal_stacked_ancestry() -> None:
    migration = _migration_module()
    migration_source = _source()

    assert migration.revision == "593ea9c376f4"
    assert migration.down_revision == "271e4439bff6"
    assert 'revision: str = "593ea9c376f4"' in migration_source
    assert 'down_revision: Union[str, None] = "271e4439bff6"' in migration_source


def test_aggregate_downgrade_fails_closed_offline() -> None:
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
        "visit_prep_credential_generation",
        "visit_prep_schedule_generation",
        "visit_prep_legal_hold",
        "visit_prep",
    ),
)
def test_aggregate_downgrade_refuses_any_populated_table(
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
        'LOCK TABLE public."visit_prep", public."visit_prep_legal_hold", '
        'public."visit_prep_schedule_generation", '
        'public."visit_prep_credential_generation" IN ACCESS EXCLUSIVE MODE'
    )
    assert len(connection.statements) == 1 + len(
        migration._AGGREGATE_TABLES_CHILD_FIRST
    )


def test_aggregate_downgrade_allows_only_empty_online_schema() -> None:
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

    assert migration._AGGREGATE_TABLES_ROOT_FIRST == (
        "visit_prep",
        "visit_prep_legal_hold",
        "visit_prep_schedule_generation",
        "visit_prep_credential_generation",
    )
    assert migration._AGGREGATE_TABLES_CHILD_FIRST == (
        "visit_prep_credential_generation",
        "visit_prep_schedule_generation",
        "visit_prep_legal_hold",
        "visit_prep",
    )
    assert len(connection.statements) == 1 + len(
        migration._AGGREGATE_TABLES_CHILD_FIRST
    )
    assert connection.statements[1:] == [
        f'SELECT EXISTS (SELECT 1 FROM public."{table_name}" LIMIT 1)'
        for table_name in migration._AGGREGATE_TABLES_CHILD_FIRST
    ]


def test_candidate_keys_are_built_concurrently_before_their_constraints() -> None:
    migration = _migration_module()
    operations = _CandidateKeyOperations()
    setattr(migration, "op", operations)

    migration._add_existing_table_candidate_keys()

    assert operations.events[0] == "autocommit:start"
    assert operations.events[1].startswith(
        "CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS "
        "uq_appointment_id_doctor_patient"
    )
    assert operations.events[2].startswith(
        "CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS "
        "uq_practice_member_id_practice_doctor"
    )
    assert operations.events[3] == "autocommit:end"
    assert "UNIQUE USING INDEX uq_appointment_id_doctor_patient" in operations.events[4]
    assert (
        "UNIQUE USING INDEX uq_practice_member_id_practice_doctor"
        in operations.events[5]
    )
    assert len(operations.events) == 6


def test_candidate_key_retry_removes_only_invalid_unattached_index() -> None:
    migration = _migration_module()
    operations = _CandidateKeyOperations(
        invalid_indexes={"uq_appointment_id_doctor_patient"}
    )
    setattr(migration, "op", operations)

    migration._add_existing_table_candidate_keys()

    assert operations.events[:3] == [
        "autocommit:start",
        'DROP INDEX CONCURRENTLY public."uq_appointment_id_doctor_patient"',
        "CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS "
        "uq_appointment_id_doctor_patient "
        "ON public.appointment (id, doctor_id, patient_id)",
    ]
    assert not any(
        "DROP INDEX" in event and "uq_practice_member_id_practice_doctor" in event
        for event in operations.events
    )


def test_candidate_key_offline_sql_does_not_query_runtime_catalogs() -> None:
    migration = _migration_module()
    operations = _CandidateKeyOperations(offline=True)
    setattr(migration, "op", operations)

    migration._add_existing_table_candidate_keys()

    assert operations.bind_requested is False
    assert not any("DROP INDEX" in event for event in operations.events)
    assert (
        sum("CREATE UNIQUE INDEX CONCURRENTLY" in event for event in operations.events)
        == 2
    )


def test_candidate_keys_precede_dependent_tables_and_drop_after_them() -> None:
    migration_source = _source()
    upgrade_source, downgrade_source = migration_source.split("def downgrade()", 1)
    upgrade_source = upgrade_source.split("def upgrade()", 1)[1]

    candidate_key_call = upgrade_source.index("_add_existing_table_candidate_keys()")
    created_tables = re.findall(r"op\.create_table\(['\"]([^'\"]+)", upgrade_source)
    assert created_tables == [
        "visit_prep",
        "visit_prep_legal_hold",
        "visit_prep_schedule_generation",
        "visit_prep_credential_generation",
    ]
    root_table = upgrade_source.index("op.create_table('visit_prep'")
    legal_hold_table = upgrade_source.index("op.create_table('visit_prep_legal_hold'")
    schedule_table = upgrade_source.index(
        "op.create_table('visit_prep_schedule_generation'"
    )
    credential_table = upgrade_source.index(
        "op.create_table('visit_prep_credential_generation'"
    )
    assert candidate_key_call < root_table < legal_hold_table < schedule_table
    assert schedule_table < credential_table

    root_drop = downgrade_source.index('op.drop_table("visit_prep", schema="public")')
    practice_key_drop = downgrade_source.index(
        '"uq_practice_member_id_practice_doctor"'
    )
    appointment_key_drop = downgrade_source.index('"uq_appointment_id_doctor_patient"')
    assert root_drop < practice_key_drop < appointment_key_drop
    assert re.findall(r"op\.drop_table\(['\"]([^'\"]+)", downgrade_source) == [
        "visit_prep_credential_generation",
        "visit_prep_schedule_generation",
        "visit_prep_legal_hold",
        "visit_prep",
    ]


def test_migration_installs_and_removes_every_visit_prep_trigger() -> None:
    migration_source = _normalized_source()
    required_triggers = {
        "visit_prep_require_published_version": (
            "BEFORE INSERT ON public.visit_prep",
            "cosmas_require_visit_prep_published_version",
        ),
        "visit_prep_guard_mutation": (
            "BEFORE UPDATE OR DELETE ON public.visit_prep",
            "cosmas_guard_visit_prep_mutation",
        ),
        "visit_prep_schedule_guard_mutation": (
            "BEFORE INSERT OR UPDATE OR DELETE ON "
            "public.visit_prep_schedule_generation",
            "cosmas_guard_visit_prep_schedule",
        ),
        "visit_prep_credential_guard_mutation": (
            "BEFORE INSERT OR UPDATE OR DELETE ON "
            "public.visit_prep_credential_generation",
            "cosmas_guard_visit_prep_credential",
        ),
        "visit_prep_legal_hold_guard_mutation": (
            "BEFORE INSERT OR UPDATE OR DELETE ON public.visit_prep_legal_hold",
            "cosmas_guard_visit_prep_legal_hold",
        ),
    }

    for trigger_name, (trigger_timing, function_name) in required_triggers.items():
        assert f"CREATE TRIGGER {trigger_name} {trigger_timing}" in migration_source
        assert f"EXECUTE FUNCTION public.{function_name}()" in migration_source
        assert f"DROP TRIGGER IF EXISTS {trigger_name}" in migration_source
        assert f"DROP FUNCTION IF EXISTS public.{function_name}()" in migration_source


def test_migration_keeps_token_and_cleanup_policy_at_the_database_boundary() -> None:
    migration_source = _normalized_source()

    assert "current_scheduled_at + interval '72 hours'" in migration_source
    assert "Visit Prep credential expiry must be schedule plus 72 hours" in (
        migration_source
    )
    assert "Visit Prep with an active legal hold cannot be deleted" in migration_source
    assert "quarantined Visit Prep is not eligible for deletion" in migration_source
    assert "clinical Visit Prep is still retained" in migration_source
    assert "unused Visit Prep is not yet eligible for deletion" in migration_source
    assert "Visit Prep schedule must be deleted through aggregate cleanup" in (
        migration_source
    )
    assert "Visit Prep credential must be deleted through aggregate cleanup" in (
        migration_source
    )
    assert "Visit Prep legal hold must be deleted through aggregate cleanup" in (
        migration_source
    )
    assert "Visit Prep quarantine requires pending review first" in migration_source
    assert "Visit Prep legal-hold release cannot be future-dated" in migration_source
    assert migration_source.count("ondelete='CASCADE'") == 3


def test_trigger_queries_cannot_be_shadowed_by_temporary_tables() -> None:
    migration_source = _normalized_source()

    assert migration_source.count("SET search_path = pg_catalog, public") == 5
    guarded_relations = {
        "public.visit_prep_question_set_version",
        "public.visit_prep_legal_hold",
        "public.visit_prep_schedule_generation",
        "public.visit_prep_credential_generation",
        "public.visit_prep",
    }
    for relation in guarded_relations:
        assert f"FROM {relation}" in migration_source


def test_test_database_cleanup_knows_every_delete_guard() -> None:
    conftest_source = " ".join(TEST_CONFTEST_PATH.read_text(encoding="utf-8").split())
    guarded_tables = {
        "visit_prep": "visit_prep_guard_mutation",
        "visit_prep_schedule_generation": "visit_prep_schedule_guard_mutation",
        "visit_prep_credential_generation": "visit_prep_credential_guard_mutation",
        "visit_prep_legal_hold": "visit_prep_legal_hold_guard_mutation",
    }

    for table_name, trigger_name in guarded_tables.items():
        assert f'"{table_name}": "{trigger_name}"' in conftest_source
