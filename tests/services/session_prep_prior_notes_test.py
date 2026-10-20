"""Prior-visit review navigation follows actual bounded source eligibility."""

from dataclasses import asdict
from datetime import timedelta
from typing import cast

import pytest

from app.services.session_prep_note_sources import NoteSourceDenied, prior_note_overview

from activemodel.session_manager import get_session
from app.models.ai_visit_note import AIVisitNote
from app.models.appointment import Appointment
from app.models.session_prep_note_finalization import SessionPrepNoteFinalization
from sqlalchemy.orm.attributes import flag_modified, set_committed_value
from sqlmodel import Session

from tests.services.session_prep_note_sources_test import (
    NOW,
    _finalize,
    _note,
    _scope,
    _sources,
)


def overview(session, doctor, target):
    return prior_note_overview(
        session=session,
        doctor_id=doctor.id,
        target_appointment_id=target.id,
        observed_at=NOW,
    )


def test_review_then_correction_changes_preview_without_implicit_review():
    doctor, patient, target = _scope()
    note = _note(doctor, patient)
    with get_session() as managed:
        session = cast(Session, managed)
        before = overview(session, doctor, target)
        assert before.included_count == 0
        assert before.notes[0].status == "needs_review"
        assert SessionPrepNoteFinalization.count() == 0
        assert "documented synthetic goals" not in str(asdict(before))
        _finalize(session, doctor, note)
        after = overview(session, doctor, target)
        assert after.included_count == 1 and after.notes[0].status == "included"
        assert after.selected_source_fingerprint != before.selected_source_fingerprint
        assert overview(session, doctor, target).selected_source_fingerprint == (
            after.selected_source_fingerprint
        )
        current = session.get(AIVisitNote, note.id)
        assert current is not None
        current.note = {"Plan": "Synthetic corrected plan"}
        flag_modified(current, "note")
        session.flush()
        corrected = overview(session, doctor, target)
        assert corrected.included_count == 0
        assert corrected.notes[0].status == "needs_review"
        assert (
            corrected.selected_source_fingerprint != after.selected_source_fingerprint
        )
        _finalize(session, doctor, current)
        reviewed_correction = overview(session, doctor, target)
        assert reviewed_correction.included_count == 1
        assert reviewed_correction.notes[0].note_id == after.notes[0].note_id
        assert reviewed_correction.selected_source_fingerprint not in {
            before.selected_source_fingerprint,
            after.selected_source_fingerprint,
        }
        visit = session.get(Appointment, current.appointment_id)
        assert visit is not None
        visit.appointment_date -= timedelta(minutes=1)
        session.flush()
        date_corrected = overview(session, doctor, target)
        assert date_corrected.included_count == reviewed_correction.included_count
        assert date_corrected.notes[0].note_id == reviewed_correction.notes[0].note_id
        assert date_corrected.selected_source_fingerprint != (
            reviewed_correction.selected_source_fingerprint
        )


def test_old_selected_note_stays_visible_beyond_recent_preview():
    doctor, patient, target = _scope()
    oldest = _note(doctor, patient, days_ago=100)
    for days_ago in range(1, 22):
        _note(doctor, patient, days_ago=days_ago)
    with get_session() as managed:
        session = cast(Session, managed)
        _finalize(session, doctor, oldest)
        result = overview(session, doctor, target)
        assert result.more_notes and len(result.notes) == 21
        assert result.notes[-1].note_id == str(oldest.id)
        assert result.notes[-1].status == "included"
        assert result.included_count == 1


def test_reviewed_notes_outside_actual_selection_are_not_claimed_as_included():
    doctor, patient, target = _scope()
    notes = [_note(doctor, patient, days_ago=day) for day in range(1, 7)]
    with get_session() as managed:
        session = cast(Session, managed)
        for note in notes:
            _finalize(session, doctor, note)
        result = overview(session, doctor, target)
        assert result.selection_limited and result.included_count == 5
        assert {n.note_id for n in result.notes if n.status == "included"} == {
            n.source_ref for n in _sources(session, doctor, target).sources
        }
        assert result.notes[-1].status == "reviewed"


def test_preview_excludes_other_patients_current_future_old_and_superseded_notes():
    doctor, patient, target = _scope()
    allowed = _note(doctor, patient)
    _note(doctor, patient, days_ago=-1)
    _note(doctor, patient, days_ago=600)
    current = _note(doctor, patient, days_ago=0)
    other, other_patient, foreign_target = _scope()
    foreign = _note(other, other_patient)
    _note(doctor, other_patient)
    superseded = _note(doctor, patient, days_ago=2)
    superseded.superseded_at = NOW
    superseded.supersession_reason = "explicit_regeneration"
    superseded.save()
    with get_session() as managed:
        session = cast(Session, managed)
        # Make a future target: sources after observation time still stay out.
        session_target = session.get(Appointment, target.id)
        assert session_target is not None
        session_target.appointment_date = (NOW + timedelta(days=3)).replace(tzinfo=None)
        session.flush()
        result = overview(session, doctor, target)
        assert [n.note_id for n in result.notes] == [str(allowed.id)]
        assert str(current.id) not in str(asdict(result))
        assert str(foreign.id) not in str(asdict(result))
        with pytest.raises(NoteSourceDenied):
            overview(session, doctor, foreign_target)


def test_unusable_or_corrupted_review_is_not_presented_as_ready():
    doctor, patient, target = _scope()
    note = _note(doctor, patient)
    large = _note(doctor, patient, days_ago=2, content={"Plan": "x" * 18001})
    with get_session() as managed:
        session = cast(Session, managed)
        review = _finalize(session, doctor, note)
        # Simulate unreadable loaded evidence without persisting a fabricated
        # review or bypassing the database's immutable-review guard.
        set_committed_value(
            review,
            "reviewed_content",
            {"sections": [["Plan", "Altered synthetic evidence"]]},
        )
        result = overview(session, doctor, target)
        assert result.included_count == 0
        assert {n.note_id: n.status for n in result.notes} == {
            str(note.id): "needs_review",
            str(large.id): "unavailable",
        }
