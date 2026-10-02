"""Generated additive turn custody; no schema rollback or unrelated drift."""

import importlib.util
from pathlib import Path

import pytest

MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "migrations/versions/2026_10_02_65dee3dec2d0_add_visit_prep_patient_clarification_.py"
)


def test_generated_turn_expand_has_exact_parent_and_forward_only_recovery():
    spec = importlib.util.spec_from_file_location("vp_turn_expand", MIGRATION)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert (
        migration.revision == "65dee3dec2d0"
        and migration.down_revision == "4f6c6893dbe7"
    )
    source = MIGRATION.read_text()
    assert "op.alter_column" not in source and "op.drop_" not in source
    assert "stripe_" not in source and "scribe_" not in source
    assert "source_turn_id" in source and "nullable=True" in source
    for required in (
        "FOR UPDATE",
        "clock_timestamp()",
        "current proposal authority",
        "sequence does not match",
        "source turn lineage differs",
        "source turn is superseded",
        "source turn is decided",
        "aggregate cleanup",
        "visit_prep_clarification_turn_guard_truncate",
        "visit_prep_clarification_proposal_guard_source_turn",
    ):
        assert required in source
    with pytest.raises(RuntimeError, match="forward fix; retain the expand"):
        migration.downgrade()
