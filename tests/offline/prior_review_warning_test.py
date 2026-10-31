"""Real source selection and discovery against isolated SQLite fixtures.

Run with --confcutdir=tests/offline. Core's bootstrap, credentials, and database
conftest are never imported; only the named local source AST is executed.
"""

import ast
import hashlib
import hmac
import json
import socket
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any, Literal, cast

import pytest
from pydantic import TypeAdapter

from sqlalchemy import JSON, Column, Table, and_, exists, inspect, or_, tuple_
from sqlmodel import Field, Session, SQLModel, col, create_engine, select


class WarningDoctor(SQLModel, table=True):
    id: str = Field(primary_key=True)
    deleted_at: datetime | None = None


class WarningPatient(SQLModel, table=True):
    id: str = Field(primary_key=True)
    doctor_id: str


class WarningAppointment(SQLModel, table=True):
    id: str = Field(primary_key=True)
    doctor_id: str
    patient_id: str
    appointment_date: datetime


class WarningNote(SQLModel, table=True):
    id: str = Field(primary_key=True)
    doctor_id: str
    appointment_id: str
    note_type: str = "Synthetic SOAP"
    note: dict[str, str] = Field(sa_column=Column(JSON))
    superseded_at: datetime | None = None


class WarningReview(SQLModel, table=True):
    id: str = Field(primary_key=True)
    doctor_id: str
    patient_id: str
    appointment_id: str
    note_id: str
    content_sha256: str
    source_version: int = 1
    finalized_at: datetime
    reviewed_content: dict = Field(sa_column=Column(JSON))


class WarningIdentity(SQLModel, table=True):
    id: str = Field(primary_key=True)
    doctor_id: str
    appointment_id: str
    source_patient_id: str
    canonical_patient_id: str
    state: str


def load_source():
    root = Path(__file__).resolve().parents[2]
    tree = ast.parse((root / "app/services/session_prep_note_sources.py").read_text())
    names = {
        "NoteSourceDenied",
        "ReviewedNoteSource",
        "ReviewedNoteSelection",
        "PriorNotePreview",
        "PriorNoteOverview",
        "canonical_note_content",
        "note_content_sha256",
        "_snapshot_content",
        "_utc",
        "_owned_appointment",
        "_prior_patient_scope",
        "load_reviewed_note_sources",
        "prior_note_overview",
    }
    constants = {
        "MAX_SOURCE_CHARACTERS",
        "MAX_FINALIZED_NOTES",
        "MAX_REVIEW_CANDIDATES",
        "SOURCE_WINDOW",
        "PRIOR_NOTE_PREVIEW_LIMIT",
    }
    nodes = [
        node
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef))
        and node.name in names
        or isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id in constants
            for target in node.targets
        )
    ]
    assert {
        n.name for n in nodes if isinstance(n, (ast.ClassDef, ast.FunctionDef))
    } == names
    module = ModuleType("isolated_prior_review_source")
    module.__dict__.update(
        dataclass=dataclass,
        field=field,
        datetime=datetime,
        UTC=UTC,
        timedelta=timedelta,
        hashlib=hashlib,
        hmac=hmac,
        json=json,
        Any=Any,
        Literal=Literal,
        cast=cast,
        TypeIDType=str,
        Session=Session,
        select=select,
        col=col,
        and_=and_,
        exists=exists,
        or_=or_,
        tuple_=tuple_,
        Doctor=WarningDoctor,
        Patient=WarningPatient,
        Appointment=WarningAppointment,
        AIVisitNote=WarningNote,
        SessionPrepNoteFinalization=WarningReview,
        ScribePatientIdentity=WarningIdentity,
        # These fixtures are web-upload notes without a capture correction.
        note_has_saved_capture_correction=lambda *_args: False,
    )
    sys.modules[module.__name__] = module
    exec(  # noqa: S102 — exact named local source, without application bootstrap.
        compile(
            ast.Module(body=list[ast.stmt](nodes), type_ignores=[]),
            "isolated_prior_review_source",
            "exec",
        ),
        module.__dict__,
    )
    return module


SOURCE = load_source()
NOW = datetime(2026, 10, 30, 12, tzinfo=UTC)


@pytest.fixture(autouse=True)
def no_external_clients(monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("external operation forbidden")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(
        engine,
        tables=[
            cast(Table, inspect(model).local_table)
            for model in (
                WarningDoctor,
                WarningPatient,
                WarningAppointment,
                WarningNote,
                WarningReview,
                WarningIdentity,
            )
        ],
    )
    with Session(engine) as value:
        value.add_all(
            [
                WarningDoctor(id="fictional_doctor"),
                WarningPatient(id="fictional_bob", doctor_id="fictional_doctor"),
                WarningAppointment(
                    id="F05",
                    doctor_id="fictional_doctor",
                    patient_id="fictional_bob",
                    appointment_date=NOW.replace(tzinfo=None),
                ),
            ]
        )
        value.commit()
        yield value
    engine.dispose()


def add_note(session, case, days_ago, *, reviewed=False, note_id=None, content=None):
    visit = WarningAppointment(
        id=case,
        doctor_id="fictional_doctor",
        patient_id="fictional_bob",
        appointment_date=(NOW - timedelta(days=days_ago)).replace(tzinfo=None),
    )
    note = WarningNote(
        id=note_id or case,
        doctor_id=visit.doctor_id,
        appointment_id=case,
        note={"History": "Fictional dated source."} if content is None else content,
    )
    session.add_all([visit, note])
    session.commit()
    if reviewed:
        review(session, note)
    return note


def review(session, note):
    session.add(
        WarningReview(
            id="review_" + note.id,
            doctor_id=note.doctor_id,
            patient_id="fictional_bob",
            appointment_id=note.appointment_id,
            note_id=note.id,
            content_sha256=SOURCE.note_content_sha256(note.note),
            finalized_at=NOW - timedelta(minutes=1),
            reviewed_content={"sections": list(map(list, note.note.items()))},
        )
    )
    session.commit()


def overview(session):
    return SOURCE.prior_note_overview(
        session=session,
        doctor_id="fictional_doctor",
        target_appointment_id="F05",
        observed_at=NOW,
    )


def test_newer_f02_correction_warns_keeps_f01_and_clears_only_after_explicit_review(
    session,
):
    add_note(session, "F01", 3, reviewed=True)
    prior = add_note(session, "F02", 2, reviewed=True, note_id="F02_prior")
    assert overview(session).newer_prior_note_awaiting_review is False
    prior.superseded_at = NOW - timedelta(minutes=2)
    replacement = WarningNote(
        id="F02_corrected",
        doctor_id=prior.doctor_id,
        appointment_id=prior.appointment_id,
        note={"History": "Fictional corrected dated source."},
    )
    session.add_all([prior, replacement])
    session.commit()
    pending = overview(session)
    assert pending.newer_prior_note_awaiting_review is True
    assert pending.included_count == 1
    assert [(n.note_id, n.status) for n in pending.notes] == [
        ("F02_corrected", "needs_review"),
        ("F01", "included"),
    ]
    selected = SOURCE.load_reviewed_note_sources(
        session=session,
        doctor_id="fictional_doctor",
        target_appointment_id="F05",
        observed_at=NOW,
    )
    assert [source.source_ref for source in selected.sources] == ["F01"]
    assert session.get(WarningReview, "review_F02_prior") is not None
    assert session.get(WarningReview, "review_F02_corrected") is None
    review(session, replacement)
    cleared = overview(session)
    assert cleared.newer_prior_note_awaiting_review is False
    assert cleared.included_count == 2
    assert cleared.notes[0].status == "included"
    assert cleared.selected_source_fingerprint != pending.selected_source_fingerprint


@pytest.mark.parametrize("days_ago", [3, 2])
def test_older_or_same_date_unreviewed_prior_does_not_claim_newer_history(
    session, days_ago
):
    add_note(session, "F01", 2, reviewed=True)
    add_note(session, "F02", days_ago)
    assert overview(session).newer_prior_note_awaiting_review is False


def test_open_visit_draft_and_unavailable_prior_do_not_trigger_warning(session):
    add_note(session, "F01", 3, reviewed=True)
    add_note(session, "F02", 2, content={})
    session.add(
        WarningNote(
            id="open_visit_draft",
            doctor_id="fictional_doctor",
            appointment_id="F05",
            note={"History": "Fictional open visit draft."},
        )
    )
    session.commit()
    result = overview(session)
    assert result.newer_prior_note_awaiting_review is False
    assert [(n.note_id, n.status) for n in result.notes] == [
        ("F02", "unavailable"),
        ("F01", "included"),
    ]


def test_no_selected_reviewed_history_does_not_claim_an_older_ready_brief(session):
    add_note(session, "F02", 2)
    result = overview(session)
    assert result.included_count == 0
    assert result.newer_prior_note_awaiting_review is False


def test_pending_discovery_warning_does_not_replace_or_invalidate_reviewed_history(
    session,
):
    add_note(session, "F01", 3, reviewed=True)
    before = overview(session)
    add_note(session, "F02", 2)
    pending = overview(session)
    assert pending.newer_prior_note_awaiting_review is True
    assert pending.included_count == before.included_count == 1
    assert pending.selected_source_fingerprint == before.selected_source_fingerprint
    adapter = TypeAdapter(SOURCE.PriorNoteOverview)
    assert adapter.dump_python(pending)["newer_prior_note_awaiting_review"] is True
    assert adapter.json_schema()["properties"]["newer_prior_note_awaiting_review"] == {
        "default": False,
        "title": "Newer Prior Note Awaiting Review",
        "type": "boolean",
    }


def test_foreign_patient_future_and_superseded_notes_do_not_supply_warning(session):
    add_note(session, "F01", 3, reviewed=True)
    future = add_note(session, "future", -1)
    superseded = add_note(session, "superseded", 1)
    superseded.superseded_at = NOW - timedelta(minutes=1)
    foreign = add_note(session, "foreign", 1)
    session.add(
        WarningPatient(id="different_fictional_patient", doctor_id="fictional_doctor")
    )
    visit = session.get(WarningAppointment, foreign.appointment_id)
    assert visit is not None
    visit.patient_id = "different_fictional_patient"
    session.add_all([visit, superseded])
    session.commit()
    result = overview(session)
    assert result.newer_prior_note_awaiting_review is False
    assert [n.note_id for n in result.notes] == ["F01"]
    assert future.id not in [n.note_id for n in result.notes]
