"""Nullable expand rehearsal in a fresh schema on an owned local PostgreSQL DB."""

import importlib
import importlib.util
import os
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.models.doctor import Doctor
from app.models.patient import Patient
from sqlalchemy import MetaData, inspect
from sqlalchemy.engine import make_url
from sqlalchemy.schema import CreateSchema
from sqlmodel import Session, SQLModel, create_engine


def test_nullable_expand_preserves_known_dates_and_retains_unknown_dates():
    raw = os.environ.get("SCRIBE_OPTIONAL_DOB_MIGRATION_DATABASE_URL")
    if not raw:
        pytest.skip("Explicit owned local migration database required")
    url = make_url(raw)
    assert url.database == "scribe_optional_dob_migration_test" and url.host is None
    assert (
        url.query["host"]
        == "/private/tmp/scribe-provider-receiver-opening-20260928/socket"
    )
    root = Path(__file__).parents[2]
    for source in (root / "app/models").glob("*.py"):
        if source.stem != "__init__":
            importlib.import_module("app.models." + source.stem)
    # Retain each run's synthetic rows and allow a fresh rehearsal without resets.
    schema = "scribe_dob_" + uuid4().hex
    bootstrap = create_engine(url)
    try:
        with bootstrap.begin() as connection:
            connection.execute(CreateSchema(schema))
    finally:
        bootstrap.dispose()
    engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    try:
        assert inspect(engine).get_table_names() == [], (
            "Use an empty owned rehearsal schema"
        )
        previous = MetaData()
        for table in SQLModel.metadata.sorted_tables:
            table.to_metadata(previous)
        previous.tables["patient"].c.date_of_birth.nullable = False
        previous.create_all(engine)
        birthday = datetime(1990, 2, 3)
        with Session(engine) as session:
            doctor = Doctor(
                clerk_id="user_synthetic_dob_migration",
                email="dob-migration@example.test",
            )
            session.add(doctor)
            session.flush()
            patient = Patient(
                doctor_id=doctor.id,
                name="Invented known birthday",
                date_of_birth=birthday,
            )
            session.add(patient)
            session.commit()
            doctor_id, patient_id = doctor.id, patient.id
        path = (
            root
            / "migrations/versions/2026_09_30_16a6bf2d1539_allow_unknown_patient_date_of_birth.py"
        )
        spec = importlib.util.spec_from_file_location("optional_dob_migration", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with engine.begin() as connection:
            context = MigrationContext.configure(
                connection,
                opts={
                    "include_object": lambda obj,
                    name,
                    kind,
                    reflected,
                    compare_to: name == "patient" if kind == "table" else True,
                },
            )
            assert (
                next(
                    c
                    for c in inspect(connection).get_columns("patient")
                    if c["name"] == "date_of_birth"
                )["nullable"]
                is False
            )
            with Operations.context(context):
                module.upgrade()
            assert compare_metadata(context, SQLModel.metadata) == []
        with Session(engine) as session:
            assert session.get(Patient, patient_id).date_of_birth == birthday
            unknown = Patient(
                doctor_id=doctor_id,
                name="Invented unknown birthday",
                date_of_birth=None,
            )
            session.add(unknown)
            session.commit()
            unknown_id = unknown.id
        with engine.begin() as connection:
            with (
                Operations.context(MigrationContext.configure(connection)),
                pytest.raises(RuntimeError, match="Retain"),
            ):
                module.downgrade()
            assert (
                next(
                    c
                    for c in inspect(connection).get_columns("patient")
                    if c["name"] == "date_of_birth"
                )["nullable"]
                is True
            )
        with Session(engine) as session:
            assert session.get(Patient, patient_id).date_of_birth == birthday
            assert session.get(Patient, unknown_id).date_of_birth is None
    finally:
        engine.dispose()
