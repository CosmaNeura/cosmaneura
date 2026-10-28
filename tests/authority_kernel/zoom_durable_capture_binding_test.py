"""Real PostgreSQL authority after Redis loss; provider calls are stand-ins."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.services import recall_webhooks, zoom_capture_status
from app.services import zoom_capture_bindings as bindings
from app.services.external_webhook_inbox import RetryableWebhookError

from app.models.external_webhook_operation import ExternalWebhookOperation
from app.models.zoom_capture_binding import ZoomCaptureBinding
from app.models.zoom_meeting import ZoomMeeting
from sqlmodel import select

from tests.authority_kernel.scribe_capture_scope_test import capture_engine as _engine
from tests.authority_kernel.zoom_bot_note_policy_test import isolated_session
from tests.factories import AppointmentFactory, DoctorFactory, PatientFactory

engine = _engine


@pytest.fixture
def capture(engine, monkeypatch):
    with isolated_session(engine) as session:
        doctor = DoctorFactory.build().save()
        appointment = AppointmentFactory.build(doctor_id=doctor.id).save()
        at = datetime.now(UTC)
        meeting = ZoomMeeting(
            doctor_id=doctor.id,
            zoom_meeting_id="synthetic-" + uuid4().hex,
            topic="Synthetic durable capture",
            start_time=at,
            duration=5,
            join_url="https://zoom.invalid/synthetic",
            appointment_id=appointment.id,
            capture_authorization_occurrence_digest="a" * 64,
            capture_authorization_expires_at=at + timedelta(minutes=30),
            capture_authorization_consumed_at=at,
        ).save()
        bot, recording = uuid4().hex, uuid4().hex
        args = dict(
            doctor_id=doctor.id,
            meeting_id=meeting.zoom_meeting_id,
            appointment_id=appointment.id,
            patient_id=appointment.patient_id,
            occurrence="a" * 64,
            bot_id=bot,
            require=lambda: None,
        )
        bindings.bind_bot(**args)
        bindings.bind_recording(
            bot_id=bot, recording_id=recording, require=lambda: None
        )
        monkeypatch.setattr(
            recall_webhooks, "ZOOM_DURABLE_CAPTURE_BINDINGS_ENABLED", True
        )
        monkeypatch.setattr(
            zoom_capture_status, "ZOOM_DURABLE_CAPTURE_BINDINGS_ENABLED", True
        )
        monkeypatch.setattr(recall_webhooks, "doctor_access_in_force", lambda _: True)

        def no_redis():
            raise AssertionError("Durable authority must not need Redis")

        monkeypatch.setattr(recall_webhooks, "get_redis", no_redis)
        monkeypatch.setattr(zoom_capture_status, "get_redis", no_redis)
        yield session, doctor, appointment, meeting, bot, recording, args


def test_confirmed_capture_survives_loss_of_all_redis_binding_keys(capture):
    session, doctor, appointment, meeting, bot, recording, _ = capture
    result = bindings.for_recording(recording, bot_id=bot)
    assert result == bindings.BoundCapture(
        meeting.zoom_meeting_id,
        str(doctor.id),
        str(appointment.id),
        str(appointment.patient_id),
        "a" * 64,
    )
    stored = session.exec(select(ZoomCaptureBinding)).one()
    assert stored.bot_digest != bot and stored.recording_digest != recording
    assert len(stored.bot_digest) == len(stored.recording_digest) == 64
    assert bindings.for_recording(recording, bot_id="foreign-bot") is None
    assert bindings.for_recording("foreign-recording", bot_id=bot) is None


def test_confirmed_binding_cannot_be_replaced_or_authorize_second_recording(capture):
    *_, bot, recording, args = capture
    with pytest.raises(ValueError, match="already_consumed"):
        bindings.bind_bot(**args)
    with pytest.raises(ValueError, match="recording_binding_unavailable"):
        bindings.bind_recording(
            bot_id=bot, recording_id="another", require=lambda: None
        )
    assert bindings.for_recording(recording, bot_id=bot) is not None


@pytest.mark.parametrize("change", ["patient", "occurrence", "authorization"])
def test_changed_tenant_or_consumed_authority_never_inherits_old_recording(
    capture, change
):
    session, doctor, appointment, meeting, bot, recording, _ = capture
    if change == "patient":
        appointment.patient_id = PatientFactory.build(doctor_id=doctor.id).save().id
        session.add(appointment)
    elif change == "occurrence":
        meeting.capture_authorization_occurrence_digest = "b" * 64
        session.add(meeting)
    else:
        meeting.capture_authorization_consumed_at = None
        session.add(meeting)
    session.flush()
    assert bindings.for_recording(recording, bot_id=bot) is None


def test_normal_recording_done_has_exact_durable_authority_and_one_provider_operation(
    capture, monkeypatch
):
    _, _, _, _, _, recording, _ = capture
    calls = []
    monkeypatch.setattr(
        recall_webhooks.recall_service, "create_async_transcript", calls.append
    )
    payload = {"data": {"recording": {"id": recording}}}
    recall_webhooks.handle_recording_done(payload)
    recall_webhooks.handle_recording_done(payload)
    assert calls == [recording]


def test_normal_transcript_done_persists_same_visit_without_redis(capture, monkeypatch):
    session, doctor, appointment, _, bot, recording, _ = capture
    transcript = uuid4().hex
    monkeypatch.setattr(
        recall_webhooks.recall_service,
        "get_transcript",
        lambda _: {"data": {"download_url": "https://synthetic.invalid/transcript"}},
    )
    monkeypatch.setattr(
        recall_webhooks.recall_service,
        "download_transcript_data",
        lambda _: [
            {
                "participant": {"name": "Synthetic Patient"},
                "words": [
                    {
                        "text": "Synthetic pain.",
                        "start_timestamp": {"relative": 0},
                        "end_timestamp": {"relative": 1},
                    }
                ],
            }
        ],
    )
    monkeypatch.setattr(
        recall_webhooks.transcribe_job, "label_speakers", lambda entries: entries
    )
    dispatched = []
    monkeypatch.setattr(
        recall_webhooks.recall_webhook_operations,
        "dispatch_pipeline",
        dispatched.append,
    )
    payload = {
        "data": {
            "bot": {"id": bot},
            "recording": {"id": recording},
            "transcript": {"id": transcript},
        }
    }
    recall_webhooks.handle_transcript_done(payload)
    receipt = session.exec(
        select(ExternalWebhookOperation).where(
            ExternalWebhookOperation.operation_type == "ingest_transcript"
        )
    ).one()
    saved = recall_webhooks.recall_webhook_operations.find_ingested_transcript(
        transcript
    )
    assert saved.transcript.appointment_id == appointment.id
    assert saved.transcript.doctor_id == doctor.id
    assert saved.transcript.patient_id == appointment.patient_id
    assert saved.transcript.transcript_data[0].content == "Synthetic pain."
    assert receipt.state == "succeeded" and dispatched == [str(receipt.id)]


def test_missing_durable_mapping_does_not_use_provider_metadata_or_redis(capture):
    with pytest.raises(RetryableWebhookError):
        recall_webhooks.handle_recording_done(
            {"data": {"recording": {"id": "unknown"}}}
        )


def test_signed_terminal_projection_survives_redis_cleanup_and_preserves_denial(
    capture,
):
    _, _, _, meeting, bot, *_ = capture
    at = datetime.now(UTC)

    def event(code, stamp, sub_code=None):
        detail = {"code": code, "updated_at": stamp.isoformat()}
        if sub_code:
            detail["sub_code"] = sub_code
        return {"event": "bot." + code, "data": {"bot": {"id": bot}, "data": detail}}

    zoom_capture_status.handle_bot_terminal(
        event(
            "recording_permission_denied", at, "zoom_local_recording_request_disabled"
        )
    )
    assert zoom_capture_status.for_meeting(meeting) is None
    zoom_capture_status.handle_bot_terminal(event("recording_permission_denied", at))
    zoom_capture_status.handle_bot_terminal(event("done", at + timedelta(seconds=1)))
    assert zoom_capture_status.for_meeting(meeting) == "permission_denied"
    meeting.capture_authorization_occurrence_digest = "b" * 64
    assert zoom_capture_status.for_meeting(meeting) is None
