import importlib.util
from pathlib import Path

import pytest

MIGRATION_PATH = (
    Path(__file__).parent / "../migrations/versions/"
    "2026_08_26_2c611e1d91aa_enforce_visit_prep_clinical_retention.py"
).resolve()


def _migration_module():
    spec = importlib.util.spec_from_file_location(
        "visit_prep_clinical_retention_migration",
        MIGRATION_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _source() -> str:
    return MIGRATION_PATH.read_text(encoding="utf-8")


def _normalized_source() -> str:
    return " ".join(_source().split())


def test_clinical_retention_migration_has_literal_stacked_ancestry() -> None:
    migration = _migration_module()

    assert migration.revision == "2c611e1d91aa"
    assert migration.down_revision == "bc4f96a3a5cc"


def test_clinical_retention_trigger_is_schema_hardened_and_schedule_anchored() -> None:
    source = _normalized_source()

    assert (
        "CREATE FUNCTION public.cosmas_enforce_visit_prep_clinical_retention()"
        in source
    )
    assert "SET search_path = pg_catalog, public" in source
    assert "BEFORE UPDATE ON public.visit_prep" in source
    assert "IF OLD.confirmed_at IS NOT NULL THEN" in source
    assert "Visit Prep retention snapshot is immutable" in source
    assert "IF NEW.confirmed_at IS NULL THEN" in source
    assert "NEW.retention_policy_version IS DISTINCT FROM 'clinical.v1'" in source
    assert "NEW.retain_until IS NULL" in source
    assert "FROM public.visit_prep_schedule_generation" in source
    assert "ORDER BY generation DESC LIMIT 1 FOR KEY SHARE" in source
    assert (
        "NEW.retain_until IS DISTINCT FROM ( "
        "((current_scheduled_at AT TIME ZONE 'UTC') + interval '7 years') "
        "AT TIME ZONE 'UTC' )" in source
    )


def test_clinical_retention_validation_refuses_any_historical_confirmation() -> None:
    source = _normalized_source()

    assert "prep.confirmed_at IS NOT NULL" in source
    assert "LEFT JOIN LATERAL" not in source
    assert "schedule.created_at <= prep.confirmed_at" not in source
    assert (
        "unexpected confirmed Visit Prep requires explicit migration review" in source
    )


def test_clinical_retention_migration_does_not_weaken_response_guards() -> None:
    source = _normalized_source()

    assert "visit_prep_question_response" not in source
    assert "cosmas_guard_visit_prep_response" not in source
    assert "DROP TABLE" not in source
    assert "DROP COLUMN" not in source


def test_clinical_retention_downgrade_is_always_forward_only() -> None:
    migration = _migration_module()

    with pytest.raises(RuntimeError, match="forward-fix only"):
        migration.downgrade()

    downgrade = _source().split("def downgrade() -> None:", 1)[1]
    assert "_refuse_downgrade()" in downgrade
    assert "DROP " not in downgrade
