"""Actual PG selection of the narrowly scoped, non-authorizing note policy."""

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app.services import zoom_bot_note_policy as policy
from typeid import TypeID

from activemodel.session_manager import _session_context
from app.models.ai_visit_transcript import AIVisitTranscript
from app.models.external_webhook_operation import ExternalWebhookOperation
from app.models.zoom_meeting import ZoomMeeting
from sqlmodel import Session

from tests.authority_kernel.scribe_capture_scope_test import capture_engine as _engine
from tests.factories import AppointmentFactory, DoctorFactory, DoctorNoteSchemaFactory

engine = _engine


@contextmanager
def isolated_session(engine):
    # Model.save() commits; keep those commits inside savepoints so each test's
    # fixed synthetic cohort identity is removed by this outer rollback.
    with engine.connect() as connection:
        transaction = connection.begin()
        with Session(
            bind=connection,
            join_transaction_mode="create_savepoint",
            expire_on_commit=False,
        ) as session:
            token = _session_context.set(session)
            try:
                yield session
            finally:
                session.rollback()
                _session_context.reset(token)
        transaction.rollback()


@pytest.fixture(params=list(policy._COHORT), ids=["A", "B"])
def candidate(engine, monkeypatch, request):
    with isolated_session(engine) as session:
        doctor_id = request.param
        subject, email = policy._COHORT[doctor_id]
        doctor = DoctorFactory.build(
            id=TypeID.from_string(doctor_id), clerk_id=subject, email=email
        ).save()
        appointment = AppointmentFactory.build(doctor_id=doctor.id).save()
        schema = DoctorNoteSchemaFactory.build(doctor_id=doctor.id).save()
        transcript = AIVisitTranscript(
            doctor_id=doctor.id,
            appointment_id=appointment.id,
            patient_id=appointment.patient_id,
            duration=1,
            transcript_data=[
                {
                    "speaker": "Patient",
                    "content": "Synthetic wrist pain.",
                    "startTime": 0,
                    "endTime": 1000,
                }
            ],
        ).save()
        at = datetime.now(UTC)
        meeting = ZoomMeeting(
            doctor_id=doctor.id,
            zoom_meeting_id="synthetic-" + uuid4().hex,
            topic="Synthetic policy test",
            start_time=at,
            duration=1,
            join_url="https://zoom.invalid/synthetic",
            appointment_id=appointment.id,
            capture_authorization_occurrence_digest="a" * 64,
            capture_authorization_expires_at=at + timedelta(minutes=30),
            capture_authorization_consumed_at=at,
        ).save()
        operation = ExternalWebhookOperation(
            provider="recall",
            operation_type="ingest_transcript",
            provider_resource_digest=uuid4().hex * 2,
            state="succeeded",
            content_sha256="b" * 64,
            result_transcript_id=transcript.id,
        ).save()
        monkeypatch.setattr(policy, "is_staging", lambda: True)
        yield session, doctor, transcript, meeting, operation, schema


def test_actual_cohort_consumed_zoom_and_durable_recall_ingestion_select(candidate):
    _, doctor, transcript, *_ = candidate
    assert policy.eligible(transcript, doctor)


@pytest.mark.parametrize(
    "change",
    [
        "production",
        "subject",
        "email",
        "doctor",
        "deleted",
        "no_meeting",
        "unconsumed",
        "foreign_appointment",
        "no_ingestion",
        "failed_ingestion",
        "other_provider",
        "other_transcript",
        "two_meetings",
    ],
)
def test_every_scope_boundary_falls_back_to_unchanged_default(
    candidate, monkeypatch, change
):
    session, doctor, transcript, meeting, operation, _ = candidate
    if change == "production":
        monkeypatch.setattr(policy, "is_staging", lambda: False)
    elif change in {"subject", "email"}:
        setattr(
            doctor,
            "clerk_id" if change == "subject" else "email",
            "user_foreign_synthetic"
            if change == "subject"
            else "foreign-synthetic@example.invalid",
        )
    elif change == "doctor":
        transcript = transcript.model_copy(update={"doctor_id": TypeID("doctor")})
    elif change == "deleted":
        doctor.deleted_at = datetime.now(UTC)
    elif change == "no_meeting":
        session.delete(meeting)
    elif change == "unconsumed":
        meeting.capture_authorization_consumed_at = None
    elif change == "foreign_appointment":
        meeting.appointment_id = TypeID("appointment")
    elif change == "no_ingestion":
        session.delete(operation)
    elif change == "failed_ingestion":
        operation.state = "failed"
    elif change == "other_provider":
        operation.provider = "zoom"
    elif change == "other_transcript":
        operation.result_transcript_id = None
    else:
        session.add(
            ZoomMeeting(
                doctor_id=doctor.id,
                zoom_meeting_id="another-synthetic-" + uuid4().hex,
                topic="Synthetic ambiguous mapping",
                start_time=meeting.start_time,
                duration=1,
                join_url="https://zoom.invalid/synthetic",
                appointment_id=meeting.appointment_id,
                capture_authorization_occurrence_digest="c" * 64,
                capture_authorization_expires_at=meeting.capture_authorization_expires_at,
                capture_authorization_consumed_at=meeting.capture_authorization_consumed_at,
            )
        )
    session.flush()
    assert not policy.eligible(transcript, doctor)
