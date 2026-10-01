"""Generated additive build expand keeps prior data and forbids destructive rollback."""

import importlib.util
from pathlib import Path

import pytest

MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "migrations/versions/2026_10_01_2f87498bb9fa_add_visit_prep_synthesis_builds.py"
)


def test_generated_build_expand_has_exact_parent_and_forward_recovery():
    spec = importlib.util.spec_from_file_location("vp_build_expand", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.revision == "2f87498bb9fa"
    assert migration.down_revision == "1cc41ced28aa"
    source = MIGRATION_PATH.read_text()
    assert "op.alter_column" not in source and "op.drop_" not in source
    assert "stripe_" not in source and "scribe_" not in source
    with pytest.raises(RuntimeError, match="unsupported; use a forward fix"):
        migration.downgrade()
