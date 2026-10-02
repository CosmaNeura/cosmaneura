"""Exact generated expand ancestry and non-destructive recovery contract."""

import importlib.util
from pathlib import Path

import pytest

MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "migrations/versions/2026_10_02_ed9dec654c8c_add_visit_prep_email_outbox.py"
)


def test_email_outbox_expand_has_exact_generated_parent_and_forward_recovery():
    spec = importlib.util.spec_from_file_location("vp_email_expand", MIGRATION)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.revision == "ed9dec654c8c"
    assert migration.down_revision == "54bedaad381a"
    source = MIGRATION.read_text()
    assert "op.alter_column" not in source and "op.drop_" not in source
    assert "scribe_" not in source and "stripe_" not in source
    with pytest.raises(RuntimeError, match="unsupported; use a forward fix"):
        migration.downgrade()
