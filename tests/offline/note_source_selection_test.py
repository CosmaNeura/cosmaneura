"""Exercise the real route body with ActiveModel and isolated SQLite row fixtures.

Run with --confcutdir=tests/offline to avoid Core's live-service test bootstrap.
This does not import Core, authenticate Clerk, or qualify PostgreSQL installation.
"""

import ast
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Annotated, Any, cast

import pytest
from fastapi import Path as ApiPath
from fastapi import Request
from pydantic import BaseModel, Field, computed_field

import activemodel.base_model as active_base
import activemodel.query_wrapper as active_query
from activemodel import BaseModel as ActiveModel
from sqlalchemy import JSON, Column, Table, inspect
from sqlalchemy.exc import NoResultFound
from sqlmodel import Field as SqlField
from sqlmodel import Session, SQLModel, create_engine


class SourcePatient(BaseModel):
    id: str


class SourceAppointment(ActiveModel, table=True):
    id: str = SqlField(primary_key=True)
    doctor_id: str
    patient_id: str

    @property
    def patient(self) -> SourcePatient:
        return SourcePatient(id=self.patient_id)


class SourceTranscript(ActiveModel, table=True):
    id: str = SqlField(primary_key=True)
    doctor_id: str = "doctor_a"
    patient_id: str = "patient_a"
    appointment_id: str = "appointment_a"
    deleted_at: datetime | None = None
    transcript_data: list[str] = SqlField(default_factory=list, sa_column=Column(JSON))


class SourceCaptureBinding(ActiveModel, table=True):
    id: str = SqlField(primary_key=True)
    doctor_id: str
    appointment_id: str


class SourceNote(ActiveModel, table=True):
    id: str = SqlField(primary_key=True)
    doctor_id: str = "doctor_a"
    appointment_id: str = "appointment_a"
    ai_transcript_id: str
    superseded_at: datetime | None = SqlField(default=None, exclude=True)
    restore_frontier_transcript_id: str | None = SqlField(default=None, exclude=True)


def load_route(root: Path):
    tree = ast.parse((root / "app/routes/internal.py").read_text())
    selected = [
        node
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef))
        and node.name in {"NoteDetail", "note_detail"}
    ]
    assert len(selected) == 2
    for node in selected:
        node.decorator_list = []
    namespace = {
        "Annotated": Annotated,
        "Any": Any,
        "cast": cast,
        "Request": Request,
        "Path": ApiPath,
        "TypeIDType": str,
        "BaseModel": BaseModel,
        "Field": Field,
        "computed_field": computed_field,
        "Doctor": SimpleNamespace,
        "Appointment": SourceAppointment,
        "Patient": SourcePatient,
        "AIVisitNote": SourceNote,
        "AIVisitTranscript": SourceTranscript,
        "ScribeCaptureBinding": SourceCaptureBinding,
        # This suite isolates transcript selection; integrated route tests
        # exercise the authenticated Session Prep review metadata separately.
        "SessionPrepNoteReview": BaseModel,
        "note_review_statuses": lambda *_args: None,
        "EngagementRole": type(
            "Role", (BaseModel,), {"get": classmethod(lambda cls, **kw: None)}
        ),
    }
    exec(  # noqa: S102 — only the named, local source AST; no app bootstrap.
        compile(
            ast.Module(body=list[ast.stmt](selected), type_ignores=[]),
            "note_detail",
            "exec",
        ),
        namespace,
    )
    return namespace["note_detail"], namespace["NoteDetail"]


@pytest.fixture
def rows(monkeypatch):
    engine = create_engine("sqlite://")
    tables = [
        cast(Table, inspect(model).local_table)
        for model in (
            SourceAppointment,
            SourceNote,
            SourceTranscript,
            SourceCaptureBinding,
        )
    ]
    SQLModel.metadata.create_all(engine, tables=tables)

    def session_factory():
        return Session(engine)

    monkeypatch.setattr(active_base, "get_session", session_factory)
    monkeypatch.setattr(active_query, "get_session", session_factory)

    def insert(*models):
        with Session(engine) as session:
            session.add_all(models)
            session.commit()

    insert(
        SourceAppointment(
            id="appointment_a", doctor_id="doctor_a", patient_id="patient_a"
        )
    )
    yield insert
    engine.dispose()


@pytest.fixture
def detail():
    route, _ = load_route(Path(__file__).resolve().parents[2])
    request = SimpleNamespace(
        state=SimpleNamespace(doctor=SimpleNamespace(id="doctor_a"))
    )
    return lambda appointment_id="appointment_a": route(request, appointment_id)


def test_current_note_uses_its_source_not_first_retained_transcript(rows, detail):
    rows(
        SourceTranscript(id="old"),
        SourceTranscript(id="reviewed"),
        SourceNote(id="current", ai_transcript_id="reviewed"),
    )
    result = detail()
    assert result.transcript.id == "reviewed"
    assert [source.id for source in result.note_transcripts] == ["reviewed"]


def test_restored_note_uses_old_source_not_restore_frontier(rows, detail):
    rows(
        SourceTranscript(id="new"),
        SourceTranscript(id="old"),
        SourceNote(
            id="restored", ai_transcript_id="old", restore_frontier_transcript_id="new"
        ),
    )
    result = detail()
    assert result.transcript.id == "old"
    assert [source.id for source in result.note_transcripts] == ["old"]
    assert "restore_frontier_transcript_id" not in result.model_dump()["notes"][0]


def test_distinct_current_note_sources_have_no_misleading_shared_transcript(
    rows, detail
):
    rows(
        SourceTranscript(id="a"),
        SourceTranscript(id="b"),
        SourceTranscript(id="unreferenced"),
        SourceNote(id="soap", ai_transcript_id="a"),
        SourceNote(id="summary", ai_transcript_id="b"),
    )
    result = detail()
    assert result.transcript is None
    assert {source.id for source in result.note_transcripts} == {"a", "b"}


def test_several_notes_can_share_one_source(rows, detail):
    rows(
        SourceTranscript(id="a"),
        SourceNote(id="soap", ai_transcript_id="a"),
        SourceNote(id="summary", ai_transcript_id="a"),
    )
    result = detail()
    assert result.transcript.id == "a"
    assert len(result.note_transcripts) == 1


@pytest.mark.parametrize("scope", ["doctor_id", "patient_id", "appointment_id"])
def test_foreign_scope_source_is_not_returned(rows, detail, scope):
    foreign = SourceTranscript(id="foreign")
    setattr(foreign, scope, "foreign_scope")
    rows(
        foreign,
        SourceTranscript(id="unreferenced"),
        SourceNote(id="current", ai_transcript_id="foreign"),
    )
    result = detail()
    assert result.transcript is None
    assert result.note_transcripts == []


def test_foreign_doctor_and_superseded_notes_do_not_expose_sources(rows, detail):
    rows(
        SourceTranscript(id="current"),
        SourceTranscript(id="old"),
        SourceTranscript(id="foreign"),
        SourceNote(id="current_note", ai_transcript_id="current"),
        SourceNote(
            id="old_note",
            ai_transcript_id="old",
            superseded_at=datetime(2026, 1, 1, tzinfo=UTC),
        ),
        SourceNote(id="foreign_note", doctor_id="doctor_b", ai_transcript_id="foreign"),
    )
    result = detail()
    assert [note.id for note in result.notes] == ["current_note"]
    assert [source.id for source in result.note_transcripts] == ["current"]


def test_missing_source_does_not_fall_back_to_unrelated_transcript(rows, detail):
    rows(
        SourceTranscript(id="unrelated"),
        SourceNote(id="current", ai_transcript_id="missing"),
    )
    result = detail()
    assert result.transcript is None
    assert result.note_transcripts == []


def test_missing_one_of_two_sources_does_not_make_other_source_shared(rows, detail):
    rows(
        SourceTranscript(id="a"),
        SourceNote(id="soap", ai_transcript_id="a"),
        SourceNote(id="summary", ai_transcript_id="missing"),
    )
    result = detail()
    assert result.transcript is None
    assert [source.id for source in result.note_transcripts] == ["a"]


@pytest.mark.parametrize("count", [0, 1, 2])
def test_legacy_pre_note_view_only_has_an_unambiguous_transcript(rows, detail, count):
    rows(*(SourceTranscript(id=f"source_{i}") for i in range(count)))
    result = detail()
    assert (result.transcript.id if result.transcript else None) == (
        "source_0" if count == 1 else None
    )
    assert result.note_transcripts == []


def test_deleted_source_metadata_and_empty_content_are_preserved(rows, detail):
    rows(
        SourceTranscript(
            id="deleted",
            deleted_at=datetime(2026, 1, 1, tzinfo=UTC),
            transcript_data=[],
        ),
        SourceNote(id="current", ai_transcript_id="deleted"),
    )
    result = detail()
    assert result.transcript.deleted_at is not None
    assert result.transcript.transcript_data == []
    assert result.note_transcripts[0].deleted_at is not None


def test_appointment_owner_lookup_still_denies_other_provider(rows, detail):
    rows(
        SourceAppointment(
            id="appointment_b", doctor_id="doctor_b", patient_id="patient_b"
        )
    )
    with pytest.raises(NoResultFound):
        detail("appointment_b")


@pytest.mark.parametrize(
    "doctor,appointment,expected",
    [
        ("doctor_a", "appointment_a", True),
        ("doctor_b", "appointment_a", False),
        ("doctor_a", "appointment_b", False),
    ],
)
def test_capture_preparation_feedback_is_bound_to_owned_visit(
    rows, detail, doctor, appointment, expected
):
    rows(
        SourceCaptureBinding(id="binding", doctor_id=doctor, appointment_id=appointment)
    )
    assert detail().capture_workflow_required is expected
