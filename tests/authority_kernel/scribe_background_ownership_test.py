"""Legacy queued notes cannot take over an explicitly captured appointment."""

import pytest

from app.jobs import summarize
from app.services import scribe_capture_notes as notes

from app.models.ai_visit_note import AIVisitNote
from app.models.ai_visit_transcript import AIVisitTranscript, TranscriptEntry
from app.models.doctor_note_schema import DoctorNoteSchema
from sqlmodel import Session

from tests.authority_kernel.scribe_capture_admission_test import (
    admission_case as _admission_case,
)
from tests.authority_kernel.scribe_capture_admission_test import (
    capture_engine as _capture_engine,
)
from tests.authority_kernel.scribe_capture_notes_test import setup as raw_note_setup
from tests.authority_kernel.scribe_configured_capture_test import note_setup

admission_case = _admission_case
capture_engine = _capture_engine


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["binding_only", "raw", "reviewed_english"])
async def test_background_job_and_late_publication_preserve_capture_owner(
    admission_case, monkeypatch, source
):
    f = admission_case
    existing = None
    if source == "binding_only":
        schema = DoctorNoteSchema(
            doctor_id=f.case.doctor.id,
            note_type="Synthetic capture",
            fields={"CC": "Concern", "Plan": "Follow-up"},
        )
        with Session(f.engine) as session:
            session.add(schema)
            session.commit()
            session.refresh(schema)
    else:
        kwargs = (
            await note_setup(f)
            if source == "reviewed_english"
            else await raw_note_setup(f, monkeypatch)
        )
        prepared = await notes.capture_note(**kwargs)
        existing = AIVisitNote.one(id=prepared.note_ref)
        schema = DoctorNoteSchema.one(id=kwargs["schema_ref"])
    extra = AIVisitTranscript(
        doctor_id=f.case.doctor.id,
        patient_id=f.case.patient.id,
        appointment_id=f.case.appointment.id,
        duration=1,
        transcript_data=[
            TranscriptEntry(
                speaker="Doctor",
                content="Synthetic late Recall segment.",
                startTime=0,
                endTime=1000,
            )
        ],
    ).save()
    monkeypatch.setattr(
        summarize,
        "generate_clinical_note",
        lambda **_: pytest.fail("background model called"),
    )
    summarize.perform(extra.id, schema.id)
    # Also represent a job whose model call started before capture ownership.
    assert (
        summarize._finalize_generated_note(
            trigger=extra,
            schema=schema,
            generated_through_transcript_id=extra.id,
            note={"CC": "Unreviewed background replacement", "Plan": "Synthetic"},
            appointment_name="Synthetic background note",
        )
        is False
    )
    with Session(f.engine) as session:
        if existing is not None:
            preserved = session.get(AIVisitNote, existing.id)
            assert preserved is not None
            assert preserved.superseded_at is None and preserved.note == existing.note
        else:
            assert (
                AIVisitNote.where(
                    AIVisitNote.appointment_id == f.case.appointment.id
                ).count()
                == 0
            )
