"""Real database regressions for reviewed-note source isolation."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest

from app.services.session_prep_note_sources import (
    NoteSourceChanged,
    NoteSourceDenied,
    canonical_note_content,
    finalize_note_for_session_prep,
    load_reviewed_note_sources,
    note_content_sha256,
)

from activemodel.session_manager import get_session
from app.models.ai_visit_note import AIVisitNote
from app.models.ai_visit_transcript import AIVisitTranscript
from app.models.appointment import Appointment
from app.models.doctor_note_schema import DoctorNoteSchema
from app.models.session_prep_note_finalization import SessionPrepNoteFinalization
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm.attributes import flag_modified
from sqlmodel import Session, select

from tests.factories import AppointmentFactory, DoctorFactory, PatientFactory

NOW = datetime.now(UTC)


def _scope():
    doctor = DoctorFactory.build().save()
    patient = PatientFactory.build(doctor_id=doctor.id).save()
    target = AppointmentFactory.build(
        doctor_id=doctor.id, patient_id=patient.id, appointment_date=NOW
    ).save()
    return doctor, patient, target


def _note(doctor, patient, *, days_ago: float = 1, content=None):
    appointment = AppointmentFactory.build(
        doctor_id=doctor.id,
        patient_id=patient.id,
        appointment_date=NOW - timedelta(days=days_ago),
    ).save()
    schema = DoctorNoteSchema(
        doctor_id=doctor.id,
        note_type=f"Prep source {str(appointment.id)[-8:]}",
        fields={"Plan": "Synthetic follow-up"},
    ).save()
    transcript = AIVisitTranscript(
        doctor_id=doctor.id,
        appointment_id=appointment.id,
        patient_id=patient.id,
        duration=1,
        transcript_data=[],
    ).save()
    return AIVisitNote(
        doctor_id=doctor.id,
        appointment_id=appointment.id,
        ai_transcript_id=transcript.id,
        doctor_note_schema_id=schema.id,
        note=content or {"Plan": "Review the documented synthetic goals."},
    ).save()


def _finalize(session, doctor, note, *, digest=None):
    return finalize_note_for_session_prep(
        session=session,
        doctor_id=doctor.id,
        note_id=note.id,
        expected_content_sha256=digest or note_content_sha256(note.note),
        observed_at=NOW,
    )


def _sources(session, doctor, target):
    return load_reviewed_note_sources(
        session=session,
        doctor_id=doctor.id,
        target_appointment_id=target.id,
        observed_at=NOW,
    )


@pytest.mark.parametrize(
    "content",
    [
        None,
        [],
        {},
        {"": "text"},
        {"Plan": []},
        {"Plan": " "},
        {"Plan": "x" * 18_001},
        {"Plan": "\ud800"},
        {"Plan": "unrepresentable\x00text"},
        {"Section\x00": "text"},
    ],
)
def test_invalid_or_oversized_content_is_not_reviewable(content):
    with pytest.raises(NoteSourceDenied):
        canonical_note_content(content)


def test_generated_note_is_ineligible_until_explicit_revision_review():
    doctor, patient, target = _scope()
    note = _note(doctor, patient)
    with get_session() as managed:
        session = cast(Session, managed)
        assert _sources(session, doctor, target).sources == ()
        finalized = _finalize(session, doctor, note)
        selected = _sources(session, doctor, target)
        assert len(selected.sources) == 1
        source = selected.sources[0]
        assert source.clinician_action_ref == str(finalized.id)
        assert source.content == note.note
        assert source.content_sha256 == note_content_sha256(note.note)
        assert "synthetic goals" not in repr(source)
        assert "reviewed_content" not in finalized.model_dump()


def test_stale_review_is_rejected_and_repeat_review_is_idempotent():
    doctor, patient, _target = _scope()
    note = _note(doctor, patient)
    with get_session() as managed:
        session = cast(Session, managed)
        with pytest.raises(NoteSourceChanged):
            _finalize(session, doctor, note, digest="0" * 64)
        assert session.exec(select(SessionPrepNoteFinalization)).all() == []
        first = _finalize(session, doctor, note)
        again = _finalize(session, doctor, note)
        assert again.id == first.id
        assert again.source_version == 1


def test_review_preserves_section_order_after_database_round_trip():
    doctor, patient, target = _scope()
    sections = {
        "Subjective": "Synthetic reported history.",
        "Objective": "Synthetic observations.",
        "Assessment": "Synthetic clinician assessment.",
        "Plan": "Synthetic documented follow-up.",
    }
    note = _note(doctor, patient, content=sections)
    with get_session() as managed:
        session = cast(Session, managed)
        finalized = _finalize(session, doctor, note)
        session.expire(finalized, ["reviewed_content"])
        selected = _sources(session, doctor, target).sources
        assert list(selected[0].content.items()) == list(sections.items())
        assert finalized.reviewed_content == {
            "sections": [[key, value] for key, value in sections.items()]
        }
        assert _finalize(session, doctor, note).id == finalized.id

        # A reorder alone changes the revision that the clinician approved.
        current = session.get(AIVisitNote, note.id)
        assert current is not None
        current.note = dict(reversed(list(sections.items())))
        flag_modified(current, "note")
        session.flush()
        session.expire(current, ["note"])
        assert list(current.note) == list(reversed(sections))
        assert _sources(session, doctor, target).sources == ()
        refreshed = _finalize(session, doctor, current)
        assert refreshed.id != finalized.id
        assert refreshed.source_version == finalized.source_version + 1
        assert list(_sources(session, doctor, target).sources[0].content) == list(
            current.note
        )


def test_source_selection_uses_utc_independent_of_database_session_timezone():
    doctor, patient, target = _scope()
    note = _note(doctor, patient, days_ago=1 / 24)
    with get_session() as managed:
        session = cast(Session, managed)
        _finalize(session, doctor, note)
        session.execute(text("SET LOCAL TIME ZONE 'America/New_York'"))
        try:
            assert [
                s.source_ref for s in _sources(session, doctor, target).sources
            ] == [str(note.id)]
        finally:
            session.execute(text("SET LOCAL TIME ZONE 'UTC'"))


def test_earlier_unordered_snapshot_requires_new_explicit_review():
    doctor, patient, target = _scope()
    note = _note(doctor, patient)
    old_digest = hashlib.sha256(
        json.dumps(note.note, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    with get_session() as managed:
        session = cast(Session, managed)
        earlier = SessionPrepNoteFinalization(
            note_id=note.id,
            doctor_id=doctor.id,
            appointment_id=note.appointment_id,
            patient_id=patient.id,
            source_version=1,
            content_sha256=old_digest,
            reviewed_content=cast(Any, note.note.copy()),
            finalized_at=NOW,
        )
        session.add(earlier)
        session.flush()
        assert _sources(session, doctor, target).sources == ()
        current = _finalize(session, doctor, note)
        assert current.source_version == 2
        assert current.id != earlier.id
        assert len(_sources(session, doctor, target).sources) == 1


def test_another_doctor_cannot_review_or_select_the_record():
    doctor, patient, target = _scope()
    outsider = DoctorFactory.build().save()
    note = _note(doctor, patient)
    with get_session() as managed:
        session = cast(Session, managed)
        with pytest.raises(NoteSourceDenied):
            _finalize(session, outsider, note)
        _finalize(session, doctor, note)
        with pytest.raises(NoteSourceDenied):
            _sources(session, outsider, target)
        other_patient = PatientFactory.build(doctor_id=doctor.id).save()
        other_target = AppointmentFactory.build(
            doctor_id=doctor.id, patient_id=other_patient.id, appointment_date=NOW
        ).save()
        assert _sources(session, doctor, other_target).sources == ()


@pytest.mark.parametrize("change", ["content", "supersession", "doctor", "patient"])
def test_revoking_source_changes_immediately_suppress_prior_review(change):
    doctor, patient, target = _scope()
    note = _note(doctor, patient)
    with get_session() as managed:
        session = cast(Session, managed)
        _finalize(session, doctor, note)
        assert len(_sources(session, doctor, target).sources) == 1
        current = session.get(AIVisitNote, note.id)
        assert current is not None
        appointment = session.get(Appointment, note.appointment_id)
        assert appointment is not None
        if change == "content":
            current.note = {"Plan": "Unreviewed replacement must not be selected."}
        elif change == "supersession":
            current.superseded_at = NOW
            current.supersession_reason = "regenerated_from_newer_transcript"
        elif change == "doctor":
            current.doctor_id = DoctorFactory.build().save().id
        else:
            appointment.patient_id = PatientFactory.build(doctor_id=doctor.id).save().id
        session.flush()
        assert _sources(session, doctor, target).sources == ()


def test_selection_has_five_source_limit_and_548_day_window():
    doctor, patient, target = _scope()
    notes = [_note(doctor, patient, days_ago=i) for i in range(1, 8)]
    outside = _note(doctor, patient, days_ago=549)
    future = _note(doctor, patient, days_ago=-1)
    with get_session() as managed:
        session = cast(Session, managed)
        for note in [*notes, outside, future]:
            _finalize(session, doctor, note)
        selected = _sources(session, doctor, target)
        assert [source.source_ref for source in selected.sources] == [
            str(note.id) for note in notes[:5]
        ]
        assert selected.truncated is True


def test_correction_requires_new_review_and_keeps_previous_evidence():
    doctor, patient, target = _scope()
    note = _note(doctor, patient)
    with get_session() as managed:
        session = cast(Session, managed)
        original = _finalize(session, doctor, note)
        original_content = original.reviewed_content.copy()
        current = session.get(AIVisitNote, note.id)
        assert current is not None
        current.note = {"Plan": "New synthetic plan reviewed by the clinician."}
        session.flush()
        assert _sources(session, doctor, target).sources == ()
        corrected = _finalize(session, doctor, current)
        assert corrected.source_version == original.source_version + 1
        assert corrected.id != original.id
        assert original.reviewed_content == original_content
        assert _sources(session, doctor, target).sources[0].source_version == 2


def test_finalization_evidence_cannot_be_rewritten_in_database():
    doctor, patient, _target = _scope()
    note = _note(doctor, patient)
    with get_session() as managed:
        session = cast(Session, managed)
        _finalize(session, doctor, note)
        with pytest.raises(DBAPIError):
            with session.begin_nested():
                session.execute(
                    text(
                        "UPDATE session_prep_note_finalization SET source_version = 99"
                    )
                )
