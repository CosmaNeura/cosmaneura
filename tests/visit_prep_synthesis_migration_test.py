"""Generated additive synthesis ancestry and preservation of retained artifacts."""

import ast
import importlib.util
from pathlib import Path

import pytest

MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "migrations/versions/2026_10_01_1cc41ced28aa_add_visit_prep_synthesis_artifacts.py"
)


def _migration():
    spec = importlib.util.spec_from_file_location(
        "vp_synthesis_migration", MIGRATION_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_generated_expand_is_additive_only_and_forward_recovery():
    migration = _migration()
    assert (
        migration.revision == "1cc41ced28aa"
        and migration.down_revision == "fe53eb110e32"
    )
    source = MIGRATION_PATH.read_text()
    assert "op.alter_column" not in source and "op.drop_" not in source
    assert "stripe_" not in source and "scribe_" not in source
    with pytest.raises(RuntimeError, match="unsupported; use a forward fix"):
        migration.downgrade()


def test_cleanup_knows_exact_guards_without_loading_global_conftest():
    assignments = {}
    for node in ast.parse(Path(__file__).with_name("conftest.py").read_text()).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name) and target.id in {
                "_APPEND_ONLY_TEST_TRIGGERS",
                "_TRUNCATE_TEST_TRIGGERS",
            }:
                assignments[target.id] = ast.literal_eval(node.value)
    assert (
        assignments["_APPEND_ONLY_TEST_TRIGGERS"]["visit_prep_synthesis_artifact"]
        == "visit_prep_brief_guard_mutation"
    )
    assert (
        assignments["_TRUNCATE_TEST_TRIGGERS"]["visit_prep_synthesis_artifact"]
        == "visit_prep_brief_guard_truncate"
    )
