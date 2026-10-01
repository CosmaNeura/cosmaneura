"""Generated ancestry, forward recovery and hardened answer storage."""

import ast
import importlib.util
from pathlib import Path

import pytest

MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "migrations/versions/2026_10_01_e161ca7c8448_add_visit_prep_responses.py"
)


def _migration():
    spec = importlib.util.spec_from_file_location(
        "vp_response_migration", MIGRATION_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_generated_revision_uses_current_core_ancestry():
    module = _migration()
    assert module.revision == "e161ca7c8448"
    assert module.down_revision == "0b6ed6c49dd4"
    source = MIGRATION_PATH.read_text()
    assert "593ea9c376f4" not in source
    assert "_add_root_candidate_key" not in source  # Already in the Core aggregate.
    assert "op.alter_column" not in source  # Do not import unrelated metadata drift.


def test_downgrade_never_requests_a_connection_or_drops_storage():
    with pytest.raises(RuntimeError, match="unsupported; use a forward fix"):
        _migration().downgrade()
    downgrade = MIGRATION_PATH.read_text().split("def downgrade() -> None:", 1)[1]
    assert "op." not in downgrade


def test_answer_shape_lineage_and_database_time_are_fenced():
    source = " ".join(MIGRATION_PATH.read_text().split())
    for required in (
        "fk_visit_prep_response_root_lineage",
        "fk_visit_prep_response_question_lineage",
        "fk_visit_prep_response_credential_generation",
        "uq_visit_prep_response_question",
        "uq_visit_prep_response_sequence",
        "uq_visit_prep_response_idempotency",
        "answer_format_version IS NOT DISTINCT FROM 1",
        "submission_sequence BETWEEN 1 AND 5",
        "CREATE FUNCTION public.cosmas_guard_visit_prep_response()",
        "SET search_path = pg_catalog, public",
        "BEFORE INSERT OR UPDATE OR DELETE ON public.visit_prep_question_response",
        "BEFORE TRUNCATE ON public.visit_prep_question_response",
        "FOR EACH STATEMENT",
        "LIMIT 1 FOR UPDATE; event_at := clock_timestamp();",
        "current_credential_expires_at <= event_at",
        "root_state <> 'in_progress'",
        "NEW.created_at := event_at",
        "NEW.recorded_at := event_at",
    ):
        assert required in source
    for forbidden in (
        "answer_plaintext",
        "choice_value",
        "answer_length",
        "conditional_result",
    ):
        assert forbidden not in source


def test_general_test_cleanup_restores_named_guards_without_importing_conftest():
    # Parse only: importing the normal bootstrap would touch shared services.
    path = Path(__file__).with_name("conftest.py")
    assignments = {
        node.targets[0].id: ast.literal_eval(node.value)
        for node in ast.parse(path.read_text()).body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id
        in {"_APPEND_ONLY_TEST_TRIGGERS", "_TRUNCATE_TEST_TRIGGERS"}
    }
    assert assignments["_APPEND_ONLY_TEST_TRIGGERS"][
        "visit_prep_question_response"
    ] == ("visit_prep_response_guard_mutation")
    assert assignments["_TRUNCATE_TEST_TRIGGERS"]["visit_prep_question_response"] == (
        "visit_prep_response_guard_truncate"
    )
    source = path.read_text().split("def database_reset_truncate_for_tests()", 1)[1]
    assert source.count("*_TRUNCATE_TEST_TRIGGERS.items()") == 2
    assert "finally:" in source and "ENABLE TRIGGER" in source
