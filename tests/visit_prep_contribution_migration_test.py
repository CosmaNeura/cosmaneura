"""Generated additive ancestry and forward-only recovery for corrections."""

import ast
import importlib.util
from pathlib import Path

import pytest

MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "migrations/versions/2026_10_01_4ad34f59136c_add_visit_prep_contribution_events.py"
)


def _migration():
    spec = importlib.util.spec_from_file_location(
        "vp_contribution_migration", MIGRATION_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_generated_ancestry_is_additive_and_excludes_unrelated_metadata_drift():
    module = _migration()
    assert module.revision == "4ad34f59136c"
    assert module.down_revision == "e161ca7c8448"
    source = MIGRATION_PATH.read_text()
    assert "op.alter_column" not in source
    assert "op.drop_" not in source
    assert "op.create_table_comment" not in source
    assert "stripe_" not in source and "scribe_" not in source


def test_downgrade_never_drops_clinical_history_or_requests_a_connection():
    with pytest.raises(RuntimeError, match="unsupported; use a forward fix"):
        _migration().downgrade()
    assert "op." not in MIGRATION_PATH.read_text().split("def downgrade() -> None:")[1]


def test_real_guard_fences_attribution_lineage_exact_basis_and_database_time():
    source = " ".join(MIGRATION_PATH.read_text().split())
    for required in (
        "fk_visit_prep_contribution_root",
        "fk_visit_prep_contribution_response",
        "fk_visit_prep_contribution_credential",
        "uq_visit_prep_contribution_sequence",
        "uq_visit_prep_contribution_key",
        "response_kind IS NOT NULL",
        "SET search_path = pg_catalog, public",
        "ORDER BY generation DESC LIMIT 1 FOR UPDATE;",
        "event_at := clock_timestamp();",
        "root_role IS DISTINCT FROM NEW.actor_role",
        "NEW.basis_response_count <> response_count",
        "NEW.basis_event_sequence <> latest_sequence",
        "current_credential.revoked_at IS NOT NULL",
        "current_credential.expires_at <= event_at",
        "NEW.created_at := event_at",
        "NEW.recorded_at := event_at",
        "BEFORE TRUNCATE ON public.visit_prep_contribution_event",
    ):
        assert required in source


def test_named_test_cleanup_guards_are_registered_without_loading_conftest():
    assignments = {}
    for node in ast.parse(Path(__file__).with_name("conftest.py").read_text()).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name) and target.id in {
                "_APPEND_ONLY_TEST_TRIGGERS",
                "_TRUNCATE_TEST_TRIGGERS",
            }:
                assignments[target.id] = ast.literal_eval(node.value)
    assert assignments["_APPEND_ONLY_TEST_TRIGGERS"][
        "visit_prep_contribution_event"
    ] == ("visit_prep_contribution_guard_mutation")
    assert assignments["_TRUNCATE_TEST_TRIGGERS"]["visit_prep_contribution_event"] == (
        "visit_prep_contribution_guard_truncate"
    )
