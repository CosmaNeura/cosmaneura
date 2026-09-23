"""Real scoped Core records, with no admission, transcription or model request."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.services import encounter_prep as prep
from app.services.authority import AuthorityRequestDeniedError
from app.services.scribe_capture_scope import CaptureScopeDenied

from app.models.ai_visit_note import AIVisitNote
from app.models.ai_visit_transcript import AIVisitTranscript
from app.models.appointment import Appointment
from app.models.doctor_note_schema import DoctorNoteSchema
from app.models.intake.intake import Intake
from app.models.scribe_capture_admission import ScribeCaptureAdmission
from sqlmodel import Session, select

from tests.authority_kernel.scribe_capture_admission_test import (
    admission_case as _admission_case,
)
from tests.authority_kernel.scribe_capture_scope_test import (
    capture_engine as _capture_engine,
)

admission_case = _admission_case
capture_engine = _capture_engine


def read(f, **changes):
    caller = replace(
        f.caller, capture_settings=replace(f.caller.capture_settings, prep_enabled=True)
    )
    return prep.read_encounter_prep(
        caller=caller,
        binding_ref=f.binding.binding_ref,
        workspace_id=changes.get("workspace_id", f.binding.workspace_id),
    )


def intake(f, *, text="Invented patient reports poor sleep.", deleted=False):
    item = Intake(
        doctor_id=f.case.doctor.id,
        appointment_id=f.case.appointment.id,
        external_intake_data={},
        intake_questions=[],
        summary=text,
    )
    if deleted:
        item.deleted_at = datetime.now(UTC)
    f.session.add(item)
    f.session.commit()
    return item


def note(f, days=3, *, content="Invented prior plan", superseded=False, current=False):
    appointment = (
        f.case.appointment
        if current
        else Appointment(
            doctor_id=f.case.doctor.id,
            patient_id=f.case.patient.id,
            appointment_date=datetime.now(UTC) - timedelta(days=days),
            external_id=None,
        )
    )
    f.session.add(appointment)
    f.session.flush()
    transcript = AIVisitTranscript(
        doctor_id=f.case.doctor.id,
        patient_id=f.case.patient.id,
        appointment_id=appointment.id,
        duration=1,
        transcript_data=[],
    )
    schema = getattr(f, "prep_schema", None)
    if schema is None:
        schema = DoctorNoteSchema(
            doctor_id=f.case.doctor.id, note_type="SOAP", fields={"Plan": "Plan"}
        )
        f.prep_schema = schema
    f.session.add_all([transcript, schema])
    f.session.flush()
    result = AIVisitNote(
        doctor_id=f.case.doctor.id,
        appointment_id=appointment.id,
        ai_transcript_id=transcript.id,
        doctor_note_schema_id=schema.id,
        note={"Plan": content},
        superseded_at=datetime.now(UTC) if superseded else None,
        supersession_reason="regenerated_from_newer_transcript" if superseded else None,
    )
    f.session.add(result)
    f.session.commit()
    return result


def test_current_intake_and_prior_drafts_without_recording(admission_case):
    f = admission_case
    saved = intake(f)
    prior = note(f)
    result = read(f)
    assert [s.source_ref for s in result.sources] == [str(saved.id), str(prior.id)]
    assert result.sources[0].kind == "patient_intake_summary"
    assert result.sources[1].label == "SOAP · generated draft"
    assert read(f) == result and f.client.calls == []
    with Session(f.engine) as session:
        assert (
            session.exec(
                select(ScribeCaptureAdmission).where(
                    ScribeCaptureAdmission.binding_id == f.binding.binding_ref
                )
            ).first()
            is None
        )
    saved.summary = "Changed invented intake"
    f.session.add(saved)
    f.session.commit()
    changed = read(f)
    assert changed.snapshot_sha256 != result.snapshot_sha256
    assert changed.sources[0].content_sha256 != result.sources[0].content_sha256


def test_excludes_current_future_superseded_old_and_deleted_sources(admission_case):
    f = admission_case
    intake(f, deleted=True)
    for values in [
        {"current": True},
        {"days": -2},
        {"superseded": True},
        {"days": 600},
    ]:
        note(f, **values)
    assert read(f).sources == ()


def test_visible_bounds_never_claim_complete_source(admission_case):
    f = admission_case
    intake(f, text="x" * 9000)
    for i in range(7):
        note(f, days=i + 1)
    result = read(f)
    assert len(result.sources) == 6 and result.additional_notes_available
    assert result.sources[0].truncated and len(result.sources[0].content) == 8000


@pytest.mark.parametrize(
    "fault", ["workspace", "revoked_identity", "disabled", "foreign_note"]
)
def test_scope_denial_carries_no_source(admission_case, fault):
    f = admission_case
    intake(f)
    if fault == "workspace":
        with pytest.raises(CaptureScopeDenied):
            read(f, workspace_id=uuid4())
    elif fault == "disabled":
        with pytest.raises(CaptureScopeDenied):
            prep.read_encounter_prep(
                caller=f.caller,
                binding_ref=f.binding.binding_ref,
                workspace_id=f.binding.workspace_id,
            )
    elif fault == "revoked_identity":
        f.case.identity.revoked_at = datetime.now(UTC)
        f.session.add(f.case.identity)
        f.session.commit()
        with pytest.raises(AuthorityRequestDeniedError):
            read(f)
    else:
        # A historical malformed note must not inherit another appointment's ownership.
        prior = note(f)
        from app.models.patient import Patient

        patient = Patient(
            doctor_id=f.case.doctor.id,
            name="Different invented person",
            date_of_birth=datetime(1990, 1, 1, tzinfo=UTC),
        )
        f.session.add(patient)
        f.session.flush()
        appointment = f.session.get(Appointment, prior.appointment_id)
        appointment.patient_id = patient.id
        f.session.add(appointment)
        f.session.commit()
        assert len(read(f).sources) == 1
