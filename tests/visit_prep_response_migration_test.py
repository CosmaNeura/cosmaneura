import importlib.util
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

MIGRATION_PATH = (
    Path(__file__).parent
    / "../migrations/versions/2026_08_25_bc4f96a3a5cc_add_visit_prep_responses.py"
).resolve()
TEST_CONFTEST_PATH = Path(__file__).with_name("conftest.py")


def _migration_module():
    spec = importlib.util.spec_from_file_location(
        "visit_prep_response_migration",
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
    def __init__(self, *, populated: bool = False) -> None:
        self.populated = populated
        self.statements: list[str] = []

    def execute(self, statement) -> _Result:
        self.statements.append(str(statement))
        return _Result(self.populated)


class _CandidateKeyOperations:
    def __init__(self, *, invalid: bool = False, offline: bool = False) -> None:
        self.events: list[str] = []
        self.invalid = invalid
        self.offline = offline
        self.bind_requested = False

    def get_bind(self):
        self.bind_requested = True
        operations = self

        class _Connection:
            def execute(self, _statement):
                return _Result(operations.invalid)

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


def test_response_migration_has_generated_stacked_ancestry() -> None:
    migration = _migration_module()

    assert migration.revision == "bc4f96a3a5cc"
    assert migration.down_revision == "593ea9c376f4"


def test_response_downgrade_fails_closed_offline_or_when_populated() -> None:
    migration = _migration_module()
    setattr(
        migration,
        "op",
        SimpleNamespace(
            get_context=lambda: SimpleNamespace(as_sql=True),
            get_bind=lambda: pytest.fail("offline guard requested a connection"),
        ),
    )
    with pytest.raises(RuntimeError, match="unsupported offline"):
        migration._refuse_populated_or_offline_downgrade()

    connection = _Connection(populated=True)
    setattr(
        migration,
        "op",
        SimpleNamespace(
            get_context=lambda: SimpleNamespace(as_sql=False),
            get_bind=lambda: connection,
        ),
    )
    with pytest.raises(RuntimeError, match="durable responses exist"):
        migration._refuse_populated_or_offline_downgrade()
    assert connection.statements == [
        'LOCK TABLE public."visit_prep" IN ACCESS EXCLUSIVE MODE',
        'LOCK TABLE public."visit_prep_question_response" IN ACCESS EXCLUSIVE MODE',
        'SELECT EXISTS (SELECT 1 FROM public."visit_prep_question_response" LIMIT 1)',
    ]


def test_response_downgrade_allows_only_empty_online_storage() -> None:
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

    assert len(connection.statements) == 3


def test_root_candidate_key_is_concurrent_retry_safe_and_precedes_table() -> None:
    migration = _migration_module()
    operations = _CandidateKeyOperations(invalid=True)
    setattr(migration, "op", operations)

    migration._add_root_candidate_key()

    assert operations.events[:4] == [
        "autocommit:start",
        'DROP INDEX CONCURRENTLY public."uq_visit_prep_id_owner_question_version"',
        "CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS "
        "uq_visit_prep_id_owner_question_version ON public.visit_prep "
        "(id, owner_practice_member_id, question_set_version_id)",
        "autocommit:end",
    ]
    assert (
        "UNIQUE USING INDEX uq_visit_prep_id_owner_question_version"
        in (operations.events[4])
    )

    source = _source().split("def upgrade() -> None:", 1)[1]
    assert source.index("_add_root_candidate_key()") < source.index(
        'op.create_table(\n        "visit_prep_question_response"'
    )


def test_root_candidate_key_offline_sql_avoids_runtime_catalog_queries() -> None:
    migration = _migration_module()
    operations = _CandidateKeyOperations(offline=True)
    setattr(migration, "op", operations)

    migration._add_root_candidate_key()

    assert operations.bind_requested is False
    assert not any("DROP INDEX" in event for event in operations.events)


def test_response_schema_has_private_lineage_and_uniqueness_fences() -> None:
    source = _normalized_source()

    for required in (
        "fk_visit_prep_response_root_lineage",
        "fk_visit_prep_response_question_lineage",
        "fk_visit_prep_response_credential_generation",
        "uq_visit_prep_response_question",
        "uq_visit_prep_response_sequence",
        "uq_visit_prep_response_idempotency",
        "visit_prep_response_question_lineage_idx",
        "answer_ciphertext",
        "payload_hmac_sha256",
        "answer_format_version IS NOT DISTINCT FROM 1",
        "submission_sequence BETWEEN 1 AND 5",
    ):
        assert required in source

    for forbidden in (
        "choice_value",
        "answer_length",
        "conditional_result",
        "answer_plaintext",
    ):
        assert forbidden not in source


def test_response_trigger_is_schema_hardened_and_append_only() -> None:
    source = _normalized_source()

    assert "CREATE FUNCTION public.cosmas_guard_visit_prep_response()" in source
    assert "SET search_path = pg_catalog, public" in source
    assert (
        "BEFORE INSERT OR UPDATE OR DELETE ON public.visit_prep_question_response"
        in source
    )
    assert (
        "CREATE FUNCTION public.cosmas_reject_visit_prep_response_truncate()" in source
    )
    assert "BEFORE TRUNCATE ON public.visit_prep_question_response" in source
    assert "FOR EACH STATEMENT" in source
    assert "FOR UPDATE" in source
    assert "root_state <> 'in_progress'" in source
    assert "LIMIT 1 FOR UPDATE; event_at := clock_timestamp();" in source
    assert "current_credential_expires_at <= event_at" in source
    assert "NEW.created_at := event_at" in source
    assert "NEW.updated_at := event_at" in source
    assert "NEW.recorded_at := event_at" in source
    assert "statement_timestamp()" not in source
    assert "Visit Prep response sequence is not current" in source
    assert "Visit Prep responses are immutable" in source
    assert "deleted through aggregate cleanup" in source

    conftest_source = TEST_CONFTEST_PATH.read_text(encoding="utf-8")
    assert (
        '"visit_prep_question_response": "visit_prep_response_guard_mutation"'
        in conftest_source
    )
    assert (
        '"visit_prep_question_response": "visit_prep_response_guard_truncate"'
        in conftest_source
    )


def test_response_downgrade_qualifies_objects_and_drops_dependency_first() -> None:
    downgrade = _source().split("def downgrade() -> None:", 1)[1]

    truncate_trigger = downgrade.index(
        "DROP TRIGGER IF EXISTS visit_prep_response_guard_truncate"
    )
    mutation_trigger = downgrade.index(
        "DROP TRIGGER IF EXISTS visit_prep_response_guard_mutation"
    )
    table = downgrade.index(
        'op.drop_table("visit_prep_question_response", schema="public")'
    )
    root_key = downgrade.index('"uq_visit_prep_id_owner_question_version"')
    assert truncate_trigger < mutation_trigger < table < root_key
    assert "public.cosmas_reject_visit_prep_response_truncate()" in downgrade
    assert "public.cosmas_guard_visit_prep_response()" in downgrade
