"""Generated expand migration for newly revealed topics; no destructive recovery."""

import importlib.util
from pathlib import Path

import pytest

MIGRATION_PATH = Path(__file__).resolve().parents[1] / (
    "migrations/versions/2026_10_01_fe53eb110e32_add_visit_prep_revealed_topic_answers.py"
)


def _migration():
    spec = importlib.util.spec_from_file_location(
        "vp_revealed_migration", MIGRATION_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_revealed_expand_has_generated_ancestry_and_no_unrelated_changes():
    module = _migration()
    assert module.revision == "fe53eb110e32"
    assert module.down_revision == "4ad34f59136c"
    source = MIGRATION_PATH.read_text()
    assert "stripe_" not in source and "scribe_" not in source
    assert "op.drop_column" not in source and "op.drop_table" not in source
    assert "op.alter_column" not in source
    assert "op.add_column('visit_prep_contribution_event'" in source
    assert "fk_visit_prep_contribution_question" in source
    assert "op.f('visit_prep_contribution_event_event_shape_check')" in source
    assert "visit_prep_contribution_guard_topic" in source
    assert "SET search_path = pg_catalog, public" in source


def test_revealed_downgrade_never_requests_a_connection_or_deletes_sources():
    with pytest.raises(RuntimeError, match="unsupported; use a forward fix"):
        _migration().downgrade()
    assert "op." not in MIGRATION_PATH.read_text().split("def downgrade() -> None:")[1]
