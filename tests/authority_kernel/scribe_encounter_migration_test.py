"""One additive migration against an explicitly empty local rehearsal database."""

import importlib
import importlib.util
import os
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.models.scribe_encounter_creation import ScribeEncounterCreation
from sqlalchemy import inspect
from sqlalchemy.engine import make_url
from sqlmodel import SQLModel, create_engine


def test_additive_receipt_migration_matches_model_and_retains_on_rollback():
    raw = os.environ.get("SCRIBE_ENCOUNTER_MIGRATION_DATABASE_URL")
    if not raw:
        pytest.skip("Explicit empty local migration database required")
    url = make_url(raw)
    assert url.database == "scribe_encounter_migration_test" and url.host is None
    assert (
        url.query["host"]
        == "/private/tmp/scribe-provider-receiver-opening-20260928/socket"
    )
    root = Path(__file__).parents[2]
    for source in (root / "app/models").glob("*.py"):
        if source.stem != "__init__":
            importlib.import_module("app.models." + source.stem)
    engine = create_engine(url)
    try:
        name = ScribeEncounterCreation.__tablename__
        assert name not in inspect(engine).get_table_names(), (
            "Use an empty owned rehearsal DB"
        )
        SQLModel.metadata.create_all(
            engine,
            tables=[t for t in SQLModel.metadata.sorted_tables if t.name != name],
        )
        path = (
            root
            / "migrations/versions/2026_09_29_de0c6b95f69f_add_scribe_encounter_creation_receipt.py"
        )
        spec = importlib.util.spec_from_file_location(
            "scribe_encounter_migration", path
        )
        module = importlib.util.module_from_spec(spec)
        assert spec is not None
        spec.loader.exec_module(module)
        with engine.begin() as connection:
            context = MigrationContext.configure(
                connection,
                opts={
                    "include_object": lambda obj, n, kind, reflected, compare_to: n
                    == name
                    if kind == "table"
                    else True,
                },
            )
            with Operations.context(context):
                module.upgrade()
            assert compare_metadata(context, SQLModel.metadata) == []
            with (
                Operations.context(context),
                pytest.raises(RuntimeError, match="retain"),
            ):
                module.downgrade()
            assert name in inspect(connection).get_table_names()
    finally:
        engine.dispose()
