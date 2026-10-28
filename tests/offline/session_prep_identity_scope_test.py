"""Real selector/digest on synthetic SQLite rows, without Core service bootstrap.

Run with --confcutdir=tests/offline. Named source AST loading follows the
existing offline route harness; capture freshness has separate integrated tests.
"""

import ast
import hashlib
import hmac
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Integer,
    String,
    and_,
    create_engine,
    exists,
    or_,
    select,
)
from sqlalchemy.orm import DeclarativeBase, Session


class Base(DeclarativeBase):
    pass


class Doctor(Base):
    __tablename__ = "identity_scope_doctor"
    id = Column(String, primary_key=True)
    deleted_at = Column(DateTime, nullable=True)


class Patient(Base):
    __tablename__ = "identity_scope_patient"
    id = Column(String, primary_key=True)
    doctor_id = Column(String)


class Appointment(Base):
    __tablename__ = "identity_scope_appointment"
    id = Column(String, primary_key=True)
    doctor_id = Column(String)
    patient_id = Column(String)
    appointment_date = Column(DateTime)


class Note(Base):
    __tablename__ = "identity_scope_note"
    id = Column(String, primary_key=True)
    appointment_id = Column(String)
    doctor_id = Column(String)
    superseded_at = Column(DateTime, nullable=True)
    note = Column(JSON)


class Review(Base):
    __tablename__ = "identity_scope_review"
    id = Column(String, primary_key=True)
    note_id = Column(String)
    appointment_id = Column(String)
    doctor_id = Column(String)
    patient_id = Column(String)
    source_version = Column(Integer)
    finalized_at = Column(DateTime)
    content_sha256 = Column(String)
    reviewed_content = Column(JSON)


class Identity(Base):
    __tablename__ = "identity_scope_decision"
    id = Column(String, primary_key=True)
    appointment_id = Column(String)
    doctor_id = Column(String)
    source_patient_id = Column(String)
    canonical_patient_id = Column(String)
    state = Column(String)


class SourceSession:
    def __init__(self, session):
        self.session = session

    def get(self, model, key):
        return self.session.get(model, key)

    def exec(self, statement):
        result = self.session.execute(statement)
        return result.scalars() if len(statement.column_descriptions) == 1 else result


def source_functions():
    root = Path(__file__).resolve().parents[2]
    selected_names = {
        "NoteSourceDenied",
        "canonical_note_content",
        "note_content_sha256",
        "_snapshot_content",
        "_utc",
        "_owned_appointment",
        "_prior_patient_scope",
        "ReviewedNoteSource",
        "ReviewedNoteSelection",
        "load_reviewed_note_sources",
    }
    tree = ast.parse((root / "app/services/session_prep_note_sources.py").read_text())
    selected = [
        node
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef))
        and node.name in selected_names
    ]
    assert len(selected) == len(selected_names)
    runtime = ast.parse((root / "app/services/session_prep_runtime.py").read_text())
    selected += [
        node
        for node in runtime.body
        if isinstance(node, ast.FunctionDef)
        and node.name in {"_digest", "source_manifest", "source_digest"}
    ]
    namespace = {
        "hashlib": hashlib,
        "hmac": hmac,
        "json": json,
        "datetime": datetime,
        "UTC": UTC,
        "timedelta": timedelta,
        "dataclass": dataclass,
        "field": field,
        "Any": Any,
        "cast": cast,
        "TypeIDType": str,
        "Session": SourceSession,
        "Doctor": Doctor,
        "Patient": Patient,
        "Appointment": Appointment,
        "AIVisitNote": Note,
        "SessionPrepNoteFinalization": Review,
        "ScribePatientIdentity": Identity,
        "select": select,
        "col": lambda value: value,
        "and_": and_,
        "or_": or_,
        "exists": exists,
        "MAX_SOURCE_CHARACTERS": 18_000,
        "MAX_FINALIZED_NOTES": 5,
        "MAX_REVIEW_CANDIDATES": 128,
        "SOURCE_WINDOW": timedelta(days=548),
        "GENERATOR_VERSION": "reviewed_notes_v1",
        "PROMPT_VERSION": "source_only_prep_v6",
        "MODEL_PROFILE": "gpt-4.1-2025-04-14",
        "note_has_saved_capture_correction": lambda _session, _note: False,
        "SCRIBE_ENCOUNTER_SETTINGS": SimpleNamespace(enabled=False),
    }
    exec(  # noqa: S102 — named local source only; no app/service bootstrap.
        compile(
            ast.Module(body=list(selected), type_ignores=[]),
            "identity_source_selection",
            "exec",
            flags=__import__("__future__").annotations.compiler_flag,
        ),
        namespace,
    )
    return namespace


@pytest.fixture
def case():
    functions = source_functions()
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    now = datetime(2026, 10, 23, 12, tzinfo=UTC)
    with Session(engine) as session:
        session.add_all([Doctor(id="doctor_a"), Doctor(id="doctor_b")])
        session.add_all(
            [
                Patient(id="canonical", doctor_id="doctor_a"),
                Patient(id="provisional", doctor_id="doctor_a"),
                Patient(id="other", doctor_id="doctor_a"),
                Patient(id="foreign", doctor_id="doctor_b"),
            ]
        )
        target = Appointment(
            id="target",
            doctor_id="doctor_a",
            patient_id="canonical",
            appointment_date=now,
        )
        session.add(target)
        for index, patient in enumerate(["canonical"] * 3 + ["provisional"]):
            visit = Appointment(
                id=f"visit_{index}",
                doctor_id="doctor_a",
                patient_id=patient,
                appointment_date=now - timedelta(days=4 - index),
            )
            content = {"Plan": f"Review synthetic visit {index}."}
            session.add_all(
                [
                    visit,
                    Note(
                        id=f"note_{index}",
                        appointment_id=visit.id,
                        doctor_id="doctor_a",
                        note=content,
                    ),
                    Review(
                        id=f"review_{index}",
                        note_id=f"note_{index}",
                        appointment_id=visit.id,
                        doctor_id="doctor_a",
                        patient_id=patient,
                        source_version=1,
                        finalized_at=now - timedelta(minutes=1),
                        content_sha256=functions["note_content_sha256"](content),
                        reviewed_content={"sections": list(map(list, content.items()))},
                    ),
                ]
            )
        identity = Identity(
            id="identity",
            appointment_id="visit_3",
            doctor_id="doctor_a",
            source_patient_id="provisional",
            canonical_patient_id="canonical",
            state="matched",
        )
        session.add(identity)
        session.commit()
        yield SimpleNamespace(
            functions=functions,
            session=session,
            identity=identity,
            now=now,
            target=target,
        )
    engine.dispose()


def selected(case, *, doctor="doctor_a"):
    return case.functions["load_reviewed_note_sources"](
        session=SourceSession(case.session),
        doctor_id=doctor,
        target_appointment_id=case.target.id,
        observed_at=case.now,
    )


@pytest.mark.parametrize("state", ["matched", "confirmed"])
def test_four_reviewed_sources_and_digest_agree_when_creation_is_disabled(case, state):
    case.identity.state = state
    case.session.commit()
    case.functions["SCRIBE_ENCOUNTER_SETTINGS"].enabled = True
    web = selected(case)
    case.functions["SCRIBE_ENCOUNTER_SETTINGS"].enabled = False
    worker = selected(case)
    assert [source.source_ref for source in web.sources] == [
        source.source_ref for source in worker.sources
    ]
    assert len(worker.sources) == 4
    assert "note_3" in {source.source_ref for source in worker.sources}
    assert case.functions["source_digest"](web) == case.functions["source_digest"](
        worker
    )
    assert worker.sources[0].content == {"Plan": "Review synthetic visit 3."}


@pytest.mark.parametrize(
    "field,value",
    [
        ("state", "provisional"),
        ("doctor_id", "doctor_b"),
        ("source_patient_id", "other"),
        ("canonical_patient_id", "other"),
    ],
)
def test_unconfirmed_or_foreign_decisions_do_not_admit_the_provisional_source(
    case, field, value
):
    setattr(case.identity, field, value)
    case.session.commit()
    assert len(selected(case).sources) == 3


def test_unmapped_source_stays_separate_and_foreign_provider_cannot_select(case):
    case.session.delete(case.identity)
    case.session.commit()
    assert len(selected(case).sources) == 3
    with pytest.raises(case.functions["NoteSourceDenied"]):
        selected(case, doctor="doctor_b")


def test_explicit_different_identity_blocks_same_patient_fallback(case):
    case.identity.appointment_id = "visit_0"
    case.identity.source_patient_id = "canonical"
    case.identity.canonical_patient_id = "other"
    case.session.commit()
    assert {source.source_ref for source in selected(case).sources} == {
        "note_1",
        "note_2",
    }


def test_foreign_canonical_target_is_denied(case):
    case.identity.appointment_id = "target"
    case.identity.source_patient_id = "canonical"
    case.identity.canonical_patient_id = "foreign"
    case.session.commit()
    with pytest.raises(case.functions["NoteSourceDenied"]):
        selected(case)
