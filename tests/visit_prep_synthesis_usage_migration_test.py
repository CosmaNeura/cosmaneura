"""Forward-only central-ledger expand with unchanged agent lineage requirements."""

import ast
import importlib.util
from pathlib import Path

import pytest

MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "migrations/versions/2026_10_01_54bedaad381a_add_visit_prep_batch_usage.py"
)


def test_generated_usage_expand_retains_exact_parent_and_one_ledger():
    spec = importlib.util.spec_from_file_location("vp_usage_expand", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.revision == "54bedaad381a"
    assert migration.down_revision == "2f87498bb9fa"
    source = MIGRATION_PATH.read_text()
    assert "op.create_table(" not in source and "op.drop_column" not in source
    assert "stripe_" not in source and "scribe_" not in source
    assert "visit_prep_usage_insert_guard" in source
    assert "visit_prep_usage_truncate_guard" in source
    assert "clock_timestamp() >= job.lease_until" in source
    # Existing SQLite-only authority fixtures mutate imported SQL metadata.
    # Compare the declared contract, not their transformed test table objects.
    model_path = MIGRATION_PATH.parents[2] / "app/models/authority_usage.py"
    constraints = {}
    model_ast = ast.parse(model_path.read_text())
    patient_shape = next(
        ast.literal_eval(node.value)
        for node in model_ast.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "PATIENT_USAGE_SHAPE"
            for target in node.targets
        )
    )
    for node in ast.walk(model_ast):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "CheckConstraint"
        ):
            name = next(
                keyword.value for keyword in node.keywords if keyword.arg == "name"
            )
            value = node.args[0]
            constraints[ast.literal_eval(name)] = (
                ast.literal_eval(value.left) + patient_shape
                if isinstance(value, ast.BinOp)
                else ast.literal_eval(value)
            )
    for name, expression in (
        ("ck_model_usage_origin_shape", migration._SHAPE),
        ("ck_agent_model_usage_event_status", migration._STATUS),
        ("ck_agent_model_usage_event_provider_surface", migration._SURFACE),
    ):
        current = constraints[name]
        if name == "ck_model_usage_origin_shape":
            current = current.removesuffix(" OR " + patient_shape).replace(
                "patient_analysis_ref IS NULL AND ", ""
            )
        elif name == "ck_agent_model_usage_event_status":
            current = current.replace(
                "event_origin IN ('visit_prep_batch', 'visit_prep_patient')",
                "event_origin = 'visit_prep_batch'",
            ).replace(
                "('succeeded', 'failed', 'cancelled'))))", "('succeeded', 'failed'))))"
            )
        elif name == "ck_agent_model_usage_event_provider_surface":
            current = current.removesuffix(
                " OR (event_origin = 'visit_prep_patient' AND surface = 'visit_prep_patient')"
            )
        assert "".join(current.split()) == "".join(expression.split())
    with pytest.raises(RuntimeError, match="forward fix"):
        migration.downgrade()
