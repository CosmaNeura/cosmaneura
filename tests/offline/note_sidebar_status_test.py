"""Run the real notes-list body against isolated synthetic SQLite rows.

Use --confcutdir=tests/offline. Like note_source_selection_test.py, this loads
only the route/model AST so no Core bootstrap, Clerk or external DB runs.
"""

import ast
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal, cast

import pytest
from fastapi import Request
from pydantic import BaseModel, Field, computed_field

import activemodel.base_model as active_base
import activemodel.query_wrapper as active_query
from activemodel import BaseModel as ActiveModel
from sqlalchemy import Table, event, inspect, update
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import selectinload
from sqlalchemy.sql import Select
from sqlmodel import Field as SqlField
from sqlmodel import Relationship, Session, SQLModel, create_engine, select


class SidebarPatient(SQLModel, table=True):
    id: str = SqlField(primary_key=True)
    name: str = "Synthetic sidebar patient"


class SidebarIntake(SQLModel, table=True):
    id: str = SqlField(primary_key=True)
    appointment_id: str = SqlField(foreign_key="sidebar_appointment.id")


class SidebarAppointment(ActiveModel, table=True):
    id: str = SqlField(primary_key=True)
    doctor_id: str = "doctor_a"
    patient_id: str = SqlField(default="patient_a", foreign_key="sidebarpatient.id")
    appointment_date: datetime = datetime(2026, 9, 20, 12)
    appointment_name: str | None = None
    patient: SidebarPatient = Relationship()
    intake: SidebarIntake | None = Relationship()

    @computed_field
    @property
    def patient_name(self) -> str:
        return self.patient.name

    @computed_field
    @property
    def intake_status(self) -> str:
        return "complete" if self.intake else "none"


class SidebarNote(SQLModel, table=True):
    id: str = SqlField(primary_key=True)
    doctor_id: str = "doctor_a"
    appointment_id: str
    superseded_at: datetime | None = None
    note: str = "Synthetic note body must not be selected for sidebar status"


def load_route(session_factory):
    root = Path(__file__).resolve().parents[2]
    tree = ast.parse((root / "app/routes/internal.py").read_text())
    selected = [
        node
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef))
        and node.name in {"NotetakingData", "note_list"}
    ]
    assert len(selected) == 2
    for node in selected:
        node.decorator_list = []
    namespace = {
        "Any": Any,
        "Literal": Literal,
        "cast": cast,
        "Request": Request,
        "BaseModel": BaseModel,
        "Field": Field,
        "Doctor": SimpleNamespace,
        "Appointment": SidebarAppointment,
        "AIVisitNote": SidebarNote,
        "selectinload": selectinload,
        "select": select,
        "get_session": session_factory,
        "SCRIBE_ENCOUNTER_SETTINGS": SimpleNamespace(enabled=False),
    }
    exec(  # noqa: S102 — named local source AST, no application bootstrap.
        compile(
            ast.Module(body=list[ast.stmt](selected), type_ignores=[]),
            "note_list",
            "exec",
        ),
        namespace,
    )
    return namespace["note_list"], namespace["NotetakingData"]


@pytest.fixture
def sidebar(monkeypatch):
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(
        engine,
        tables=[
            cast(Table, inspect(model).local_table)
            for model in (
                SidebarPatient,
                SidebarAppointment,
                SidebarIntake,
                SidebarNote,
            )
        ],
    )

    def session_factory():
        return Session(engine)

    monkeypatch.setattr(active_base, "get_session", session_factory)
    monkeypatch.setattr(active_query, "get_session", session_factory)
    route, response_model = load_route(session_factory)
    statements = []
    note_statements = []

    def record(_conn, _cursor, statement, _params, _context, _many):
        sql = " ".join(statement.lower().split())
        statements.append(sql)

    def record_query(_conn, statement, _multiparams, _params, _options):
        # Capture before compilation caching so the current expanding-bind
        # values are preserved, rather than the first cached query's values.
        if isinstance(statement, Select) and "from sidebarnote" in " ".join(
            str(statement).lower().split()
        ):
            note_statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    event.listen(engine, "before_execute", record_query)

    def insert(*rows):
        with Session(engine) as session:
            session.add_all(rows)
            session.commit()

    def read():
        statements.clear()
        note_statements.clear()
        result = route(
            SimpleNamespace(
                state=SimpleNamespace(doctor=SimpleNamespace(id="doctor_a"))
            )
        )
        # Include serialization so any accidentally lazy per-row reads count.
        return result.model_dump(mode="json")

    insert(SidebarPatient(id="patient_a"))
    yield SimpleNamespace(
        insert=insert,
        read=read,
        statements=statements,
        note_statements=note_statements,
        response_model=response_model,
        session=session_factory,
        route=route,
    )
    event.remove(engine, "before_cursor_execute", record)
    event.remove(engine, "before_execute", record_query)
    engine.dispose()


def test_response_compatibility_defaults_to_empty_statuses(sidebar):
    assert sidebar.response_model(appointments=[]).model_dump() == {
        "appointments": [],
        "note_statuses": {},
        "patient_names": {},
    }


def test_empty_owner_list_has_no_note_query_or_foreign_status(sidebar):
    sidebar.insert(
        SidebarAppointment(id="foreign", doctor_id="doctor_b"),
        SidebarNote(id="foreign-note", doctor_id="doctor_b", appointment_id="foreign"),
    )
    assert sidebar.read() == {
        "appointments": [], "note_statuses": {}, "patient_names": {}
    }
    assert not any("from sidebarnote" in sql for sql in sidebar.statements)


def test_status_uses_only_owned_current_notes_and_preserves_appointment_shape(sidebar):
    own_ids = [
        "named_empty",
        "named_current",
        "unnamed_current",
        "superseded",
        "foreign_note",
    ]
    sidebar.insert(
        *(
            SidebarAppointment(
                id=key,
                appointment_name=None
                if key == "unnamed_current"
                else "Same synthetic title",
            )
            for key in own_ids
        ),
        SidebarAppointment(id="foreign_visit", doctor_id="doctor_b"),
        SidebarNote(id="current1", appointment_id="named_current"),
        SidebarNote(id="current2", appointment_id="named_current"),
        SidebarNote(id="untitled_note", appointment_id="unnamed_current"),
        SidebarNote(
            id="old_note",
            appointment_id="superseded",
            superseded_at=datetime(2026, 9, 21),
        ),
        SidebarNote(
            id="wrong_doctor", doctor_id="doctor_b", appointment_id="foreign_note"
        ),
        SidebarNote(
            id="foreign_owner", doctor_id="doctor_b", appointment_id="foreign_visit"
        ),
        SidebarNote(id="crossed_appointment", appointment_id="foreign_visit"),
    )
    result = sidebar.read()
    assert result["note_statuses"] == {
        "named_empty": "not-started",
        "named_current": "completed",
        "unnamed_current": "completed",
        "superseded": "not-started",
        "foreign_note": "not-started",
    }
    assert {row["id"] for row in result["appointments"]} == set(own_ids)
    for row in result["appointments"]:
        assert set(row) == {
            "id",
            "doctor_id",
            "patient_id",
            "appointment_date",
            "appointment_name",
            "patient_name",
            "intake_status",
        }
    assert "Synthetic note body" not in str(result)


def test_supersession_and_replacement_change_status_without_title_changes(sidebar):
    sidebar.insert(
        SidebarAppointment(id="visit", appointment_name="Unchanged synthetic title"),
        SidebarNote(id="original", appointment_id="visit"),
    )
    assert sidebar.read()["note_statuses"] == {"visit": "completed"}
    with sidebar.session() as session:
        session.execute(update(SidebarNote).values(superseded_at=datetime(2026, 9, 21)))
        session.commit()
    assert sidebar.read()["note_statuses"] == {"visit": "not-started"}
    sidebar.insert(SidebarNote(id="replacement", appointment_id="visit"))
    assert sidebar.read()["note_statuses"] == {"visit": "completed"}


def test_status_keys_exclude_an_appointment_added_between_reads(sidebar, monkeypatch):
    sidebar.insert(SidebarAppointment(id="listed_visit"))

    def concurrent_status_session():
        # The owner list has already been materialized before this second read.
        sidebar.insert(
            SidebarAppointment(id="arrived_later"),
            SidebarNote(id="new_note", appointment_id="arrived_later"),
        )
        return sidebar.session()

    monkeypatch.setitem(
        sidebar.route.__globals__, "get_session", concurrent_status_session
    )
    result = sidebar.read()
    assert [row["id"] for row in result["appointments"]] == ["listed_visit"]
    assert result["note_statuses"] == {"listed_visit": "not-started"}


def test_postgresql_note_query_bind_count_does_not_expand_with_owner_list(sidebar):
    counts = []
    for start, stop in ((0, 3), (3, 17)):
        sidebar.insert(
            *(SidebarAppointment(id=f"visit_{index}") for index in range(start, stop))
        )
        assert len(sidebar.read()["appointments"]) == stop
        assert len(sidebar.note_statements) == 1
        # Compile the actual executed route query, including expanded IN binds.
        # A tiny fixture catches the PostgreSQL 65,535-parameter failure without
        # seeding tens of thousands of appointments or contacting PostgreSQL.
        compiled = sidebar.note_statements[0].compile(
            dialect=postgresql.dialect(), compile_kwargs={"render_postcompile": True}
        )
        counts.append(len(compiled.params))
    assert len(set(counts)) == 1
    assert max(counts) <= 2


@pytest.mark.parametrize("count", [1, 25, 501])
def test_one_scalar_note_query_regardless_of_appointment_count(sidebar, count):
    sidebar.insert(
        *(
            SidebarAppointment(
                id=f"visit_{index}",
                appointment_date=datetime(2026, 9, 20) + timedelta(seconds=index),
            )
            for index in range(count)
        ),
        *(
            SidebarNote(id=f"note_{index}", appointment_id=f"visit_{index}")
            for index in range(count)
        ),
    )
    result = sidebar.read()
    assert len(result["note_statuses"]) == count
    assert set(result["note_statuses"].values()) == {"completed"}
    note_queries = [sql for sql in sidebar.statements if "from sidebarnote" in sql]
    assert len(note_queries) == 1
    projection = note_queries[0].split(" from ", 1)[0]
    assert (
        projection.removeprefix("select ").removeprefix("distinct ")
        == "sidebarnote.appointment_id"
    )
    assert result["appointments"][0]["id"] == f"visit_{count - 1}"
