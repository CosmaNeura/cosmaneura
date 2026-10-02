"""Generated authority expand ancestry and forward-only recovery."""

import importlib.util
from pathlib import Path

import pytest

MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "migrations/versions/2026_10_02_10b7993b911d_add_visit_prep_email_authority.py"
)


def test_authority_expand_has_generated_parent_and_changes_only_new_streams():
    spec = importlib.util.spec_from_file_location(
        "vp_email_authority_expand", MIGRATION
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert (
        migration.revision == "10b7993b911d"
        and migration.down_revision == "ed9dec654c8c"
    )
    source = MIGRATION.read_text()
    assert "op.alter_column" not in source and "op.drop_" not in source
    assert "stripe_" not in source and "scribe_" not in source
    with pytest.raises(RuntimeError, match="unsupported; use a forward fix"):
        migration.downgrade()
