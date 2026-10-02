"""Generated expand-only clarification migration and honest recovery."""

import importlib.util
from pathlib import Path

import pytest

MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "migrations/versions/2026_10_02_4f6c6893dbe7_add_visit_prep_clarification_evidence.py"
)


def test_generated_expand_changes_only_new_clarification_streams():
    spec = importlib.util.spec_from_file_location("vp_clarification_expand", MIGRATION)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert (
        migration.revision == "4f6c6893dbe7"
        and migration.down_revision == "10b7993b911d"
    )
    source = MIGRATION.read_text()
    assert "op.alter_" not in source and "op.drop_" not in source
    assert "stripe_" not in source and "scribe_" not in source
    with pytest.raises(RuntimeError, match="unsupported; use a forward fix"):
        migration.downgrade()
