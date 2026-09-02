from datetime import datetime, timedelta, timezone
from itertools import count
from typing import Any, cast

import pytest

from app.configuration.redis import get_redis
from app.services.external_webhook_inbox import digest_provider_reference

from app.models.appointment import Appointment
from app.models.doctor_video_integration import (
    DoctorVideoIntegration,
    VideoProvider,
)
from app.models.zoom_meeting import ZoomMeeting, ZoomMeetingStatus

from tests.factories import AppointmentFactory, DoctorFactory

MEETING_ID = "99911112222"
HOST_ID = "zoom-host-abc"
BOT_ID = "recall-bot-abc"
OCCURRENCE_UUID = "zoom-occurrence-current"
_EVENT_TIMESTAMPS = count(1_700_000_000_000)


def _key(suffix: str) -> str:
    return f"recall:meeting:{MEETING_ID}:{suffix}"


@pytest.fixture
def clean_redis():
    redis = get_redis()
    suffixes = [
        "bot_id",
        "doctor_id",
        "host_id",
        "doctor_present",
        "recording_active",
        "participants_order",
        "participants_names",
        "participants_present",
        "participant_event_order",
        "patient_count",
        "capture_started",
        "starting_lock",
        "merge_task_id",
        "paused_at",
        "merge_generation",
        "merge_release_intent_id",
        "occurrence_digest",
        "capture_authorization_pending",
        "lifecycle_lock",
    ]
    for s in suffixes:
        redis.delete(_key(s))
    for key in (
        f"recall:bot:{BOT_ID}:doctor_id",
        f"recall:bot:{BOT_ID}:appointment_id",
        f"recall:bot:{BOT_ID}:patient_id",
        f"recall:recording:recording-{BOT_ID}:doctor_id",
        f"recall:recording:recording-{BOT_ID}:appointment_id",
        f"recall:recording:recording-{BOT_ID}:patient_id",
    ):
        redis.delete(key)
    for key in cast(list[str | bytes], redis.keys("recall:zak_url_token:*")):
        redis.delete(key)
    yield redis
    for s in suffixes:
        redis.delete(_key(s))
    for key in (
        f"recall:bot:{BOT_ID}:doctor_id",
        f"recall:bot:{BOT_ID}:appointment_id",
        f"recall:bot:{BOT_ID}:patient_id",
        f"recall:recording:recording-{BOT_ID}:doctor_id",
        f"recall:recording:recording-{BOT_ID}:appointment_id",
        f"recall:recording:recording-{BOT_ID}:patient_id",
    ):
        redis.delete(key)
    for key in cast(list[str | bytes], redis.keys("recall:zak_url_token:*")):
        redis.delete(key)


@pytest.fixture
def allow_doctor_access(monkeypatch):
    from app.services import zoom_capture_authorization, zoom_webhooks

    monkeypatch.setattr(zoom_webhooks, "doctor_access_in_force", lambda _doctor: True)
    monkeypatch.setattr(
        zoom_capture_authorization,
        "doctor_access_in_force",
        lambda _doctor: True,
    )


@pytest.fixture
def doctor_with_zoom(allow_doctor_access):
    doctor = DoctorFactory.build().save()
    DoctorVideoIntegration(
        doctor_id=doctor.id,
        provider=VideoProvider.zoom,
        provider_user_id=HOST_ID,
        access_token="tok",
        refresh_token="rtok",
        token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    ).save()
    _bind_zoom_meeting(doctor)
    return doctor


def _bind_zoom_meeting(doctor):
    appointment = AppointmentFactory.build(
        doctor_id=doctor.id,
        appointment_date=datetime.now(timezone.utc),
    ).save()
    ZoomMeeting(
        doctor_id=doctor.id,
        zoom_meeting_id=MEETING_ID,
        topic="Bound visit",
        start_time=datetime.now(timezone.utc),
        duration=30,
        join_url=f"https://zoom.us/j/{MEETING_ID}",
        status=ZoomMeetingStatus.scheduled,
        appointment_id=appointment.id,
        capture_authorization_occurrence_digest=digest_provider_reference(
            f"zoom-occurrence:{OCCURRENCE_UUID}"
        ),
        capture_authorization_expires_at=datetime.now(timezone.utc)
        + timedelta(hours=1),
    ).save()
    return appointment


@pytest.fixture
def mock_recall(monkeypatch):
    calls = {
        "create_bot": [],
        "start_recording": [],
        "stop_recording": [],
        "leave_call": [],
        "send_chat_message": [],
    }

    from app.services import recall

    def fake_create_bot(
        meeting_url,
        zak_url,
        bot_name="Cosmas AI Note-Taker",
        manual_recording=True,
        metadata=None,
    ):
        calls["create_bot"].append(
            {
                "meeting_url": meeting_url,
                "zak_url": zak_url,
                "metadata": metadata,
                "manual_recording": manual_recording,
            }
        )
        return {"id": BOT_ID}

    def fake_start_recording(bot_id, wait_for_ready=True):
        calls["start_recording"].append(bot_id)
        return {"id": f"recording-{bot_id}"}

    def fake_stop_recording(bot_id):
        calls["stop_recording"].append(bot_id)

    def fake_leave_call(bot_id):
        calls["leave_call"].append(bot_id)

    def fake_send_chat_message(bot_id, message, to="everyone"):
        calls["send_chat_message"].append((bot_id, message, to))

    monkeypatch.setattr(recall, "create_bot", fake_create_bot)
    monkeypatch.setattr(recall, "start_recording", fake_start_recording)
    monkeypatch.setattr(recall, "stop_recording", fake_stop_recording)
    monkeypatch.setattr(recall, "leave_call", fake_leave_call)
    monkeypatch.setattr(recall, "send_chat_message", fake_send_chat_message)
    return calls


@pytest.fixture
def mock_zoom(monkeypatch):
    from app.services import zoom as zoom_service

    monkeypatch.setattr(zoom_service, "get_zak_token", lambda doctor: "zak-fake")
    monkeypatch.setattr(
        zoom_service,
        "get_meeting_join_url",
        lambda doctor, meeting_id: f"https://zoom.us/j/{meeting_id}",
    )
    monkeypatch.setattr(
        zoom_service,
        "get_user_display_name",
        lambda doctor: "Test Doctor",
    )


def _started_payload(occurrence_uuid: str | None = OCCURRENCE_UUID) -> dict:
    payload = {
        "payload": {
            "object": {
                "id": MEETING_ID,
                "host_id": HOST_ID,
            }
        }
    }
    if occurrence_uuid is not None:
        payload["payload"]["object"]["uuid"] = occurrence_uuid
    return payload


def _participant_joined_payload(
    participant_id: str,
    user_name: str,
    *,
    user_id: str | None = None,
    occurrence_uuid: str | None = OCCURRENCE_UUID,
    event_ts: int | None = None,
) -> dict:
    participant: dict = {"id": participant_id, "user_name": user_name}
    if user_id is not None:
        participant["user_id"] = user_id
    payload = {
        "event_ts": next(_EVENT_TIMESTAMPS) if event_ts is None else event_ts,
        "payload": {
            "object": {
                "id": MEETING_ID,
                "participant": participant,
            }
        },
    }
    if occurrence_uuid is not None:
        payload["payload"]["object"]["uuid"] = occurrence_uuid
    return payload


def _participant_left_payload(
    participant_id: str,
    user_name: str,
    *,
    user_id: str | None = None,
    occurrence_uuid: str | None = OCCURRENCE_UUID,
    event_ts: int | None = None,
) -> dict:
    return _participant_joined_payload(
        participant_id,
        user_name,
        user_id=user_id,
        occurrence_uuid=occurrence_uuid,
        event_ts=event_ts,
    )


def _meeting_ended_payload(
    occurrence_uuid: str | None = OCCURRENCE_UUID,
) -> dict:
    payload = {"payload": {"object": {"id": MEETING_ID}}}
    if occurrence_uuid is not None:
        payload["payload"]["object"]["uuid"] = occurrence_uuid
    return payload


# --------- Scenario 1: meeting.started ---------


def test_meeting_started_does_not_create_bot(
    clean_redis,
    doctor_with_zoom,
    mock_recall,
    mock_zoom,
):
    from app.services import zoom_webhooks

    zoom_webhooks.handle_meeting_started(_started_payload())

    assert mock_recall["create_bot"] == []
    assert clean_redis.get(_key("bot_id")) is None
    assert clean_redis.get(_key("doctor_id")) is not None
    assert clean_redis.get(_key("host_id")).decode() == HOST_ID


def test_meeting_started_denied_access_does_not_register_or_dispatch(
    clean_redis,
    doctor_with_zoom,
    mock_recall,
    mock_zoom,
    monkeypatch,
):
    from app.services import zoom_webhooks

    monkeypatch.setattr(zoom_webhooks, "doctor_access_in_force", lambda _doctor: False)

    zoom_webhooks.handle_meeting_started(_started_payload())

    assert clean_redis.get(_key("doctor_id")) is None
    assert clean_redis.get(_key("host_id")) is None
    assert mock_recall["create_bot"] == []


def test_meeting_started_retains_unreleased_prior_bot(
    clean_redis,
    doctor_with_zoom,
    mock_recall,
    mock_zoom,
):
    """A new delivery cannot erase a provider handle without confirmed leave."""
    from app.services import zoom_webhooks

    clean_redis.set(_key("bot_id"), "prior-bot")
    clean_redis.set(_key("capture_started"), "1")
    clean_redis.set(_key("merge_task_id"), "prior-release-task")

    zoom_webhooks.handle_meeting_started(_started_payload())

    assert clean_redis.get(_key("bot_id")) == b"prior-bot"
    assert clean_redis.get(_key("capture_started")) == b"1"
    assert clean_redis.get(_key("merge_task_id")) == b"prior-release-task"
    assert clean_redis.get(_key("doctor_id")) is None
    assert mock_recall["leave_call"] == []


def test_first_capture_fails_closed_without_server_appointment_binding(
    clean_redis,
    doctor_with_zoom,
    mock_recall,
    mock_zoom,
):
    from app.services import zoom_webhooks

    zoom_mtg = ZoomMeeting.get(
        doctor_id=doctor_with_zoom.id,
        zoom_meeting_id=MEETING_ID,
    )
    assert zoom_mtg is not None
    zoom_mtg.appointment_id = None
    zoom_mtg.capture_authorization_occurrence_digest = None
    zoom_mtg.capture_authorization_expires_at = None
    zoom_mtg.capture_authorization_consumed_at = None
    zoom_mtg.save()

    zoom_webhooks.handle_meeting_started(_started_payload())
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload(HOST_ID, "Test Doctor")
    )
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload("patient-a", "Patient A")
    )

    assert mock_recall["create_bot"] == []
    assert mock_recall["start_recording"] == []
    assert clean_redis.get(_key("capture_started")) is None


def test_first_capture_fails_closed_without_occurrence_authorization(
    clean_redis,
    doctor_with_zoom,
    mock_recall,
    mock_zoom,
):
    from app.services import zoom_webhooks

    zoom_mtg = ZoomMeeting.get(
        doctor_id=doctor_with_zoom.id,
        zoom_meeting_id=MEETING_ID,
    )
    assert zoom_mtg is not None
    expected_appointment_id = zoom_mtg.appointment_id
    zoom_mtg.capture_authorization_occurrence_digest = None
    zoom_mtg.capture_authorization_expires_at = None
    zoom_mtg.capture_authorization_consumed_at = None
    zoom_mtg.save()

    zoom_webhooks.handle_meeting_started(_started_payload())
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload(HOST_ID, "Test Doctor")
    )
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload("patient-a", "Patient A")
    )

    assert mock_recall["create_bot"] == []
    persisted = ZoomMeeting.get(id=zoom_mtg.id)
    assert persisted is not None
    assert persisted.appointment_id == expected_appointment_id
    assert persisted.capture_authorization_consumed_at is None


def test_clinician_binding_is_armed_by_signed_start_and_consumed_once(
    clean_redis,
    doctor_with_zoom,
    mock_recall,
    mock_zoom,
):
    from app.services import zoom_capture_authorization, zoom_webhooks

    zoom_mtg = ZoomMeeting.get(
        doctor_id=doctor_with_zoom.id,
        zoom_meeting_id=MEETING_ID,
    )
    assert zoom_mtg is not None
    assert zoom_mtg.appointment_id is not None
    appointment = Appointment.one(zoom_mtg.appointment_id)
    zoom_mtg.capture_authorization_occurrence_digest = None
    zoom_mtg.capture_authorization_expires_at = None
    zoom_mtg.capture_authorization_consumed_at = None
    zoom_mtg.save()

    authorization = zoom_capture_authorization.authorize_capture_for_appointment(
        doctor_id=doctor_with_zoom.id,
        zoom_meeting_id=MEETING_ID,
        appointment_id=str(appointment.id),
    )
    assert authorization.armed is False

    zoom_webhooks.handle_meeting_started(_started_payload())

    armed = ZoomMeeting.one(zoom_mtg.id)
    assert armed.capture_authorization_occurrence_digest == (
        digest_provider_reference(f"zoom-occurrence:{OCCURRENCE_UUID}")
    )
    assert armed.capture_authorization_expires_at is not None
    assert armed.capture_authorization_consumed_at is None

    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload(HOST_ID, "Test Doctor")
    )
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload("patient-a", "Patient A")
    )

    assert len(mock_recall["create_bot"]) == 1
    consumed = ZoomMeeting.one(zoom_mtg.id)
    assert consumed.capture_authorization_consumed_at is not None


def test_duplicate_signed_start_retries_capture_when_participants_already_present(
    clean_redis,
    doctor_with_zoom,
    mock_recall,
    mock_zoom,
    monkeypatch,
):
    from app.jobs import start_zoom_capture
    from app.services import zoom_capture_authorization, zoom_webhooks

    zoom_mtg = ZoomMeeting.get(
        doctor_id=doctor_with_zoom.id,
        zoom_meeting_id=MEETING_ID,
    )
    assert zoom_mtg is not None
    assert zoom_mtg.appointment_id is not None
    appointment = Appointment.one(zoom_mtg.appointment_id)
    zoom_mtg.capture_authorization_occurrence_digest = None
    zoom_mtg.capture_authorization_expires_at = None
    zoom_mtg.capture_authorization_consumed_at = None
    zoom_mtg.save()

    authorization = zoom_capture_authorization.authorize_capture_for_appointment(
        doctor_id=doctor_with_zoom.id,
        zoom_meeting_id=MEETING_ID,
        appointment_id=str(appointment.id),
    )
    assert authorization.armed is False

    occurrence_digest = digest_provider_reference(f"zoom-occurrence:{OCCURRENCE_UUID}")
    clean_redis.set(_key("occurrence_digest"), occurrence_digest)
    clean_redis.set(_key("doctor_id"), str(doctor_with_zoom.id))
    clean_redis.set(_key("doctor_present"), "1")
    clean_redis.set(_key("patient_count"), "1")
    queued: list[str] = []
    monkeypatch.setattr(start_zoom_capture, "queue", queued.append)

    zoom_webhooks.handle_meeting_started(_started_payload())

    assert queued == [str(zoom_mtg.id)]
    assert (
        ZoomMeeting.one(zoom_mtg.id).capture_authorization_occurrence_digest
        == occurrence_digest
    )


def test_future_pending_binding_cannot_arm_an_earlier_pmr_occurrence(
    clean_redis,
    doctor_with_zoom,
    mock_recall,
    mock_zoom,
):
    from app.services import zoom_capture_authorization, zoom_webhooks

    zoom_mtg = ZoomMeeting.get(
        doctor_id=doctor_with_zoom.id,
        zoom_meeting_id=MEETING_ID,
    )
    assert zoom_mtg is not None
    assert zoom_mtg.appointment_id is not None
    appointment = Appointment.one(zoom_mtg.appointment_id)
    zoom_mtg.start_time = datetime.now(timezone.utc) + timedelta(hours=2)
    zoom_mtg.capture_authorization_occurrence_digest = None
    zoom_mtg.capture_authorization_expires_at = None
    zoom_mtg.capture_authorization_consumed_at = None
    zoom_mtg.save()

    authorization = zoom_capture_authorization.authorize_capture_for_appointment(
        doctor_id=doctor_with_zoom.id,
        zoom_meeting_id=MEETING_ID,
        appointment_id=str(appointment.id),
    )
    assert authorization.armed is False

    zoom_webhooks.handle_meeting_started(_started_payload("earlier-pmr-occurrence"))
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload(HOST_ID, "Test Doctor")
    )
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload("patient-a", "Patient A")
    )

    persisted = ZoomMeeting.one(zoom_mtg.id)
    assert persisted.capture_authorization_occurrence_digest is None
    assert persisted.capture_authorization_expires_at is None
    assert persisted.capture_authorization_consumed_at is None
    assert mock_recall["create_bot"] == []


@pytest.mark.parametrize("authorization_state", ["wrong_occurrence", "expired"])
def test_first_capture_fails_closed_for_invalid_occurrence_authorization(
    clean_redis,
    doctor_with_zoom,
    mock_recall,
    mock_zoom,
    authorization_state,
):
    from app.services import zoom_webhooks

    zoom_mtg = ZoomMeeting.get(
        doctor_id=doctor_with_zoom.id,
        zoom_meeting_id=MEETING_ID,
    )
    assert zoom_mtg is not None
    if authorization_state == "wrong_occurrence":
        zoom_mtg.capture_authorization_occurrence_digest = digest_provider_reference(
            "zoom-occurrence:different-occurrence"
        )
    else:
        zoom_mtg.capture_authorization_expires_at = datetime.now(timezone.utc) - (
            timedelta(seconds=1)
        )
    zoom_mtg.save()

    zoom_webhooks.handle_meeting_started(_started_payload())
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload(HOST_ID, "Test Doctor")
    )
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload("patient-a", "Patient A")
    )

    assert mock_recall["create_bot"] == []
    persisted = ZoomMeeting.get(id=zoom_mtg.id)
    assert persisted is not None
    assert persisted.capture_authorization_consumed_at is None


def test_concurrent_capture_authorization_is_consumed_once_under_row_lock():
    from threading import Barrier, Thread

    from app.services import zoom_webhooks

    from activemodel.session_manager import SessionManager, get_engine
    from app.models.doctor import Doctor
    from app.models.patient import Patient
    from sqlalchemy import delete

    manager = SessionManager.get_instance()
    test_connection = manager.session_connection
    manager.session_connection = None
    doctor = DoctorFactory.build().save()
    appointment = AppointmentFactory.build(doctor_id=doctor.id).save()
    meeting_id = f"row-lock-{str(appointment.id)[-12:]}"
    occurrence_digest = digest_provider_reference("zoom-occurrence:row-lock")
    zoom_mtg = ZoomMeeting(
        doctor_id=doctor.id,
        zoom_meeting_id=meeting_id,
        topic="Authorization row lock",
        start_time=datetime.now(timezone.utc),
        duration=30,
        join_url=f"https://zoom.us/j/{meeting_id}",
        status=ZoomMeetingStatus.started,
        appointment_id=appointment.id,
        capture_authorization_occurrence_digest=occurrence_digest,
        capture_authorization_expires_at=datetime.now(timezone.utc)
        + timedelta(hours=1),
    ).save()
    barrier = Barrier(2)
    results: list[Appointment | None] = []
    errors: list[BaseException] = []

    def consume() -> None:
        try:
            barrier.wait(timeout=5)
            results.append(
                zoom_webhooks._consume_bound_appointment(
                    str(doctor.id),
                    meeting_id,
                    occurrence_digest=occurrence_digest,
                )
            )
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    workers = [Thread(target=consume), Thread(target=consume)]
    try:
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=10)

        assert not any(worker.is_alive() for worker in workers)
        assert errors == []
        assert len([result for result in results if result is not None]) == 1
        persisted = ZoomMeeting.get(id=zoom_mtg.id)
        assert persisted is not None
        assert persisted.appointment_id == appointment.id
        assert persisted.capture_authorization_consumed_at is not None
    finally:
        with get_engine().begin() as connection:
            connection.execute(
                delete(ZoomMeeting).where(cast(Any, ZoomMeeting.id) == zoom_mtg.id)
            )
            connection.execute(
                delete(Appointment).where(cast(Any, Appointment.id) == appointment.id)
            )
            connection.execute(
                delete(Patient).where(cast(Any, Patient.id) == appointment.patient_id)
            )
            connection.execute(delete(Doctor).where(cast(Any, Doctor.id) == doctor.id))
        manager.session_connection = test_connection


def test_first_capture_fails_closed_for_cross_tenant_appointment_binding(
    clean_redis,
    doctor_with_zoom,
    mock_recall,
    mock_zoom,
):
    from app.services import zoom_webhooks

    other_doctor = DoctorFactory.build().save()
    other_appointment = AppointmentFactory.build(doctor_id=other_doctor.id).save()
    zoom_mtg = ZoomMeeting.get(
        doctor_id=doctor_with_zoom.id,
        zoom_meeting_id=MEETING_ID,
    )
    assert zoom_mtg is not None
    zoom_mtg.appointment_id = other_appointment.id
    zoom_mtg.save()

    zoom_webhooks.handle_meeting_started(_started_payload())
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload(HOST_ID, "Test Doctor")
    )
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload("patient-a", "Patient A")
    )

    assert mock_recall["create_bot"] == []
    assert mock_recall["start_recording"] == []
    assert clean_redis.get(_key("capture_started")) is None


def test_doctor_identified_by_participant_id_not_name(
    clean_redis,
    doctor_with_zoom,
    mock_recall,
    mock_zoom,
):
    """A guest whose user_name matches the doctor's display name but
    whose participant.id is different must NOT be treated as doctor."""
    from app.services import zoom_webhooks

    zoom_webhooks.handle_meeting_started(_started_payload())

    # Impersonator: same display name, different id
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload("guest-spoof-id", "Test Doctor")
    )

    assert clean_redis.get(_key("doctor_present")) is None
    # They got added to the waiting-room hash, no bot yet
    assert clean_redis.get(_key("host_id")).decode() == HOST_ID


def test_doctor_identified_marks_present(
    clean_redis,
    doctor_with_zoom,
    mock_recall,
    mock_zoom,
):
    from app.services import zoom_webhooks

    zoom_webhooks.handle_meeting_started(_started_payload())
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload(HOST_ID, "Test Doctor")
    )

    assert clean_redis.get(_key("doctor_present")) == b"1"
    # no non-doctors, so no bot created
    assert mock_recall["create_bot"] == []


def test_participant_cannot_seed_occurrence_before_meeting_started(
    clean_redis,
    doctor_with_zoom,
    mock_recall,
    mock_zoom,
):
    """Only authoritative meeting.started may establish an occurrence."""
    from app.services import zoom_webhooks

    assert clean_redis.get(_key("host_id")) is None

    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload(HOST_ID, "Test Doctor")
    )

    assert clean_redis.get(_key("occurrence_digest")) is None
    assert clean_redis.get(_key("doctor_present")) is None
    assert clean_redis.llen(_key("participants_order")) == 0


def test_durable_participant_waits_for_meeting_mapping_before_side_effects(
    clean_redis,
    doctor_with_zoom,
) -> None:
    from app.services import external_webhook_inbox as inbox
    from app.services import zoom_webhooks

    from app.models.external_webhook_operation import ExternalWebhookOperation

    event_type = "meeting.participant_joined"
    digest = inbox.digest_provider_reference("participant-before-meeting-started")
    payload = _participant_joined_payload("guest-before-start", "Patient Private")

    with pytest.raises(inbox.RetryableWebhookError) as unavailable:
        zoom_webhooks.dispatch(
            event_type,
            payload,
            provider_event_digest=digest,
        )

    assert unavailable.value.code == "meeting_mapping_unavailable"
    assert clean_redis.get(_key("occurrence_digest")) is None
    assert clean_redis.llen(_key("participants_order")) == 0
    operation = ExternalWebhookOperation.get(
        provider="zoom",
        provider_resource_digest=digest,
    )
    assert operation is not None
    assert operation.state == "reserved"
    assert operation.execution_lease_expires_at is None

    zoom_webhooks.handle_meeting_started(_started_payload())
    zoom_webhooks.dispatch(
        event_type,
        payload,
        provider_event_digest=digest,
    )

    assert clean_redis.llen(_key("participants_order")) == 1
    operation = ExternalWebhookOperation.get(id=operation.id)
    assert operation is not None
    assert operation.state == "succeeded"


def test_duplicate_participant_join_is_idempotent_and_membership_is_hmac_only(
    clean_redis,
    doctor_with_zoom,
    mock_recall,
    mock_zoom,
):
    from app.services import zoom_webhooks

    zoom_webhooks.handle_meeting_started(_started_payload())
    payload = _participant_joined_payload(
        "raw-patient-id",
        "Patient Private",
        event_ts=2_000,
    )
    zoom_webhooks.handle_participant_joined(payload)
    zoom_webhooks.handle_participant_joined(payload)
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload(
            "raw-patient-id",
            "Patient Private",
            event_ts=2_001,
        )
    )

    occurrence_digest = clean_redis.get(_key("occurrence_digest")).decode()
    expected_ref = digest_provider_reference(
        f"zoom-participant:{MEETING_ID}:{occurrence_digest}:raw-patient-id"
    )
    assert clean_redis.get(_key("patient_count")) == b"1"
    assert clean_redis.smembers(_key("participants_present")) == {expected_ref.encode()}
    assert clean_redis.lrange(_key("participants_order"), 0, -1) == [
        expected_ref.encode()
    ]
    assert clean_redis.hkeys(_key("participant_event_order")) == [expected_ref.encode()]
    membership_state = b" ".join(
        [
            *clean_redis.smembers(_key("participants_present")),
            *clean_redis.lrange(_key("participants_order"), 0, -1),
            *clean_redis.hkeys(_key("participant_event_order")),
        ]
    )
    assert b"raw-patient-id" not in membership_state


def test_out_of_order_participant_join_cannot_reverse_newer_leave(
    clean_redis,
    doctor_with_zoom,
    mock_recall,
    mock_zoom,
):
    from app.services import zoom_webhooks

    zoom_webhooks.handle_meeting_started(_started_payload())
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload("patient-a", "Patient A", event_ts=3_000)
    )
    zoom_webhooks.handle_participant_left(
        _participant_left_payload("patient-a", "Patient A", event_ts=3_100)
    )
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload("patient-a", "Patient A", event_ts=3_050)
    )

    assert clean_redis.get(_key("patient_count")) == b"0"
    assert clean_redis.scard(_key("participants_present")) == 0
    assert clean_redis.llen(_key("participants_order")) == 0
    assert mock_recall["create_bot"] == []


def test_concurrent_participant_joined_survives_meeting_started_cleanup(
    clean_redis,
    doctor_with_zoom,
    mock_recall,
    mock_zoom,
    monkeypatch,
):
    """Reproduces the production race: Zoom fires participant_joined for the
    host ~50ms after meeting.started, and the two run as concurrent celery
    tasks. We fire the join from inside the window between meeting.started's
    stale-key cleanup and its host_id write — the interleaving that used to
    drop doctor_present and park the visit in "holding" with no bot."""
    from app.services import zoom as zoom_service
    from app.services import zoom_webhooks

    real_get_integration = zoom_service.get_zoom_integration
    fired = {"joined": False}

    def get_integration_then_race(doctor):
        integration = real_get_integration(doctor)
        if not fired["joined"]:
            fired["joined"] = True
            zoom_webhooks.handle_participant_joined(
                _participant_joined_payload(HOST_ID, "Test Doctor")
            )
        return integration

    monkeypatch.setattr(zoom_service, "get_zoom_integration", get_integration_then_race)

    zoom_webhooks.handle_meeting_started(_started_payload())

    assert fired["joined"]
    # The concurrent handler's state must not have been wiped by the cleanup.
    assert clean_redis.get(_key("doctor_present")) == b"1"
    assert clean_redis.get(_key("host_id")).decode() == HOST_ID

    # ...and the visit proceeds normally: the patient's arrival starts the bot.
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload("patient-1", "Patient One")
    )

    assert len(mock_recall["create_bot"]) == 1
    assert clean_redis.get(_key("recording_active")) == b"1"


def test_doctor_leaving_recognized_when_host_id_absent(
    clean_redis,
    doctor_with_zoom,
    mock_recall,
    mock_zoom,
):
    "participant_left must resolve the host the same way participant_joined does."
    from app.services import zoom_webhooks

    zoom_webhooks.handle_participant_left(
        _participant_left_payload(HOST_ID, "Test Doctor")
    )

    # Classified as the doctor leaving, not as a departing patient.
    assert clean_redis.get(_key("patient_count")) is None


def test_patient_joins_first_then_doctor_starts_session(
    clean_redis,
    doctor_with_zoom,
    mock_recall,
    mock_zoom,
):
    from app.services import zoom_webhooks

    zoom_webhooks.handle_meeting_started(_started_payload())
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload("patient-1", "Flor Guerrero")
    )
    # No bot yet (doctor absent)
    assert mock_recall["create_bot"] == []

    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload(HOST_ID, "Test Doctor")
    )

    assert len(mock_recall["create_bot"]) == 1
    assert mock_recall["create_bot"][0]["metadata"] is None
    assert mock_recall["create_bot"][0]["manual_recording"] is True
    assert mock_recall["start_recording"] == [BOT_ID]
    assert clean_redis.get(_key("bot_id")).decode() == BOT_ID
    assert clean_redis.get(_key("recording_active")) == b"1"
    assert clean_redis.get(_key("participants_names")) is None
    zak_url = mock_recall["create_bot"][0]["zak_url"]
    zak_token = zak_url.rsplit("/", maxsplit=1)[-1]
    zak_ttl = clean_redis.ttl(zoom_webhooks.zak_token_redis_key(zak_token))
    assert 0 < zak_ttl <= zoom_webhooks._ZAK_URL_TOKEN_TTL_SECONDS <= 5 * 60


def test_projected_bot_boolean_is_recognized_without_persisting_display_name(
    clean_redis,
    doctor_with_zoom,
    mock_recall,
    mock_zoom,
):
    from app.services import zoom_webhooks

    zoom_webhooks.handle_meeting_started(_started_payload())
    payload = _participant_joined_payload(BOT_ID, "Ignored before projection")
    participant = payload["payload"]["object"]["participant"]
    participant.pop("user_name")
    participant["is_bot"] = True

    zoom_webhooks.handle_participant_joined(payload)

    assert clean_redis.get(_key("patient_count")) is None
    assert clean_redis.get(_key("participants_names")) is None


def test_guest_patient_with_empty_id_still_starts_session(
    clean_redis,
    doctor_with_zoom,
    mock_recall,
    mock_zoom,
):
    """Regression: Zoom omits `participant.id` for guests (only signed-in
    Zoom users get one). If we keyed off id alone, the guest got pushed to
    the participants queue as an empty string, and when the doctor joined
    the `if first_id:` truthy check skipped session start — so no bot ever
    appeared. The fix is to fall back to `user_id`, which Zoom always sets.
    """
    from app.services import zoom_webhooks

    zoom_webhooks.handle_meeting_started(_started_payload())

    # Guest patient: empty id, only user_id (the actual Zoom payload shape
    # for unauthenticated joiners).
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload("", "John's Fathom Notetaker", user_id="16778240")
    )
    assert mock_recall["create_bot"] == []  # doctor not yet present

    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload(HOST_ID, "Test Doctor")
    )

    assert len(mock_recall["create_bot"]) == 1, (
        "doctor arriving with a guest already waiting must spawn a bot"
    )
    assert clean_redis.get(_key("recording_active")) == b"1"


def test_doctor_joins_first_no_patients_no_bot(
    clean_redis,
    doctor_with_zoom,
    mock_recall,
    mock_zoom,
):
    from app.services import zoom_webhooks

    zoom_webhooks.handle_meeting_started(_started_payload())
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload(HOST_ID, "Test Doctor")
    )

    assert mock_recall["create_bot"] == []
    assert clean_redis.get(_key("doctor_present")) == b"1"
    assert clean_redis.get(_key("bot_id")) is None


def test_doctor_present_then_patient_joins_starts_session(
    clean_redis,
    doctor_with_zoom,
    mock_recall,
    mock_zoom,
):
    from app.services import zoom_webhooks

    bound_meeting = ZoomMeeting.get(
        doctor_id=doctor_with_zoom.id,
        zoom_meeting_id=MEETING_ID,
    )
    assert bound_meeting is not None
    assert bound_meeting.appointment_id is not None
    appointment = Appointment.get(id=bound_meeting.appointment_id)
    assert appointment is not None

    zoom_webhooks.handle_meeting_started(_started_payload())
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload(HOST_ID, "Test Doctor")
    )
    assert mock_recall["create_bot"] == []  # no patient yet

    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload("patient-a", "Juan Perez")
    )

    assert len(mock_recall["create_bot"]) == 1
    assert mock_recall["start_recording"] == [BOT_ID]
    assert clean_redis.get(_key("recording_active")) == b"1"
    assert int(clean_redis.get(_key("patient_count"))) == 1
    recording_prefix = f"recall:recording:recording-{BOT_ID}"
    assert (
        clean_redis.get(f"{recording_prefix}:doctor_id")
        == str(doctor_with_zoom.id).encode()
    )
    assert (
        clean_redis.get(f"{recording_prefix}:appointment_id")
        == str(appointment.id).encode()
    )
    assert (
        clean_redis.get(f"{recording_prefix}:patient_id")
        == str(appointment.patient_id).encode()
    )
    assert clean_redis.get(f"recall:bot:{BOT_ID}:doctor_id") is None
    assert clean_redis.get(f"recall:bot:{BOT_ID}:appointment_id") is None
    assert clean_redis.get(f"recall:bot:{BOT_ID}:patient_id") is None
    consumed_meeting = ZoomMeeting.get(
        doctor_id=doctor_with_zoom.id,
        zoom_meeting_id=MEETING_ID,
    )
    assert consumed_meeting is not None
    assert consumed_meeting.appointment_id == appointment.id
    assert consumed_meeting.capture_authorization_consumed_at is not None


def test_consumed_binding_blocks_second_capture_after_redis_state_loss(
    clean_redis,
    doctor_with_zoom,
    mock_recall,
    mock_zoom,
):
    from app.services import zoom_webhooks

    zoom_webhooks.handle_meeting_started(_started_payload())
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload(HOST_ID, "Test Doctor")
    )
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload("patient-a", "Patient A")
    )
    assert mock_recall["start_recording"] == [BOT_ID]

    # Simulate total loss of the ephemeral per-meeting state. The durable,
    # consumed server binding must still prevent patient B from starting a
    # second recording against patient A's appointment.
    for suffix in (
        "bot_id",
        "doctor_id",
        "host_id",
        "doctor_present",
        "recording_active",
        "participants_order",
        "participants_names",
        "participants_present",
        "participant_event_order",
        "patient_count",
        "capture_started",
        "starting_lock",
        "merge_task_id",
        "merge_release_intent_id",
        "paused_at",
        "occurrence_digest",
    ):
        clean_redis.delete(_key(suffix))

    zoom_webhooks.handle_meeting_started(_started_payload())
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload(HOST_ID, "Test Doctor")
    )
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload("patient-b", "Patient B")
    )

    assert len(mock_recall["create_bot"]) == 1
    assert mock_recall["start_recording"] == [BOT_ID]


def test_additional_participant_during_active_session_no_new_bot(
    clean_redis,
    doctor_with_zoom,
    mock_recall,
    mock_zoom,
):
    from app.services import zoom_webhooks

    zoom_webhooks.handle_meeting_started(_started_payload())
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload(HOST_ID, "Test Doctor")
    )
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload("patient-a", "Juan Perez")
    )
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload("family-b", "Maria Perez (mamá)")
    )

    assert len(mock_recall["create_bot"]) == 1  # still just one bot
    assert mock_recall["start_recording"] == [BOT_ID]  # one start
    assert int(clean_redis.get(_key("patient_count"))) == 2
    assert clean_redis.get(_key("recording_active")) == b"1"


def test_last_patient_leaves_pauses_and_schedules_merge_window(
    clean_redis,
    doctor_with_zoom,
    mock_recall,
    mock_zoom,
    monkeypatch,
):
    """When the last non-doctor leaves while the doctor is still present,
    the recording is paused (stop_recording) and a merge_window task is
    scheduled. The bot handle is retained only so expiry can confirm leave;
    the capture fence prevents any participant from resuming it."""

    class FakeAsyncResult:
        id = "celery-task-mw"

    from app.jobs import merge_window

    monkeypatch.setattr(
        merge_window.expire,
        "apply_async",
        lambda *args, **kwargs: FakeAsyncResult(),
    )

    from app.services import zoom_webhooks

    zoom_webhooks.handle_meeting_started(_started_payload())
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload(HOST_ID, "Test Doctor")
    )
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload("patient-a", "Juan")
    )
    # Now recording is active
    zoom_webhooks.handle_participant_left(
        _participant_left_payload("patient-a", "Juan")
    )

    assert mock_recall["stop_recording"] == [BOT_ID]
    assert mock_recall["leave_call"] == []
    assert clean_redis.get(_key("recording_active")) is None
    # The bot is preserved until Recall confirms leave, not for reuse.
    assert clean_redis.get(_key("bot_id")).decode() == BOT_ID
    assert clean_redis.get(_key("capture_started")) == b"1"
    assert clean_redis.get(_key("merge_task_id")).decode() == "celery-task-mw"
    assert clean_redis.llen(_key("participants_order")) == 0


def test_sequential_patients_cannot_share_bot_or_appointment(
    clean_redis,
    doctor_with_zoom,
    mock_recall,
    mock_zoom,
    monkeypatch,
):
    """A second patient cannot resume patient A's bot or create a segment
    that would inherit patient A's appointment through bot-scoped state."""
    revoked = []

    class FakeAsyncResult:
        id = "celery-task-mw"

    from app.jobs import merge_window

    monkeypatch.setattr(
        merge_window.expire,
        "apply_async",
        lambda *args, **kwargs: FakeAsyncResult(),
    )
    from app.celery import celery_app

    monkeypatch.setattr(
        celery_app.control,
        "revoke",
        lambda task_id, terminate=False: revoked.append(task_id),
    )

    from app.services import zoom_webhooks

    zoom_webhooks.handle_meeting_started(_started_payload())
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload(HOST_ID, "Test Doctor")
    )

    # Patient A cycle
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload("patient-a", "Juan")
    )
    zoom_webhooks.handle_participant_left(
        _participant_left_payload("patient-a", "Juan")
    )

    recording_id = f"recording-{BOT_ID}"
    patient_a_appointment = clean_redis.get(
        f"recall:recording:{recording_id}:appointment_id"
    )
    assert patient_a_appointment is not None

    # Patient B enters within the merge window. Without an exact encounter
    # authorization, the only safe action is to leave capture stopped.
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload("patient-b", "Sofia")
    )

    assert len(mock_recall["create_bot"]) == 1
    assert mock_recall["start_recording"] == [BOT_ID]
    assert mock_recall["stop_recording"] == [BOT_ID]
    assert mock_recall["leave_call"] == []
    assert revoked == []
    assert clean_redis.get(_key("merge_task_id")) == b"celery-task-mw"
    assert clean_redis.get(_key("recording_active")) is None
    assert clean_redis.get(_key("capture_started")) == b"1"
    assert clean_redis.get(_key("bot_id")).decode() == BOT_ID
    assert (
        clean_redis.get(f"recall:recording:{recording_id}:appointment_id")
        == patient_a_appointment
    )
    assert clean_redis.get(f"recall:bot:{BOT_ID}:appointment_id") is None


def test_accompanying_family_leaves_keeps_recording(
    clean_redis,
    doctor_with_zoom,
    mock_recall,
    mock_zoom,
):
    from app.services import zoom_webhooks

    zoom_webhooks.handle_meeting_started(_started_payload())
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload(HOST_ID, "Test Doctor")
    )
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload("patient-a", "Juan")
    )
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload("family-b", "Maria")
    )
    zoom_webhooks.handle_participant_left(
        _participant_left_payload("family-b", "Maria")
    )

    # family left but patient still there: keep recording
    assert mock_recall["stop_recording"] == []
    assert mock_recall["leave_call"] == []
    assert clean_redis.get(_key("recording_active")) == b"1"


def test_doctor_leaves_pauses_recording_and_schedules_merge_window(
    clean_redis,
    doctor_with_zoom,
    mock_recall,
    mock_zoom,
    monkeypatch,
):
    """Doctor leaving while recording pauses the bot (stop_recording) and
    arms a 10-min merge_window task. bot_id is preserved only until Recall
    confirms leave; doctor return cannot resume the recording."""
    scheduled = {}

    class FakeAsyncResult:
        id = "celery-task-42"

    def fake_apply_async(*, kwargs, countdown):
        scheduled["kwargs"] = kwargs
        scheduled["countdown"] = countdown
        return FakeAsyncResult()

    from app.jobs import merge_window

    from app.models.external_webhook_delivery import ExternalWebhookDelivery

    monkeypatch.setattr(merge_window.expire, "apply_async", fake_apply_async)

    from app.services import zoom_webhooks

    zoom_webhooks.handle_meeting_started(_started_payload())
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload(HOST_ID, "Test Doctor")
    )
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload("patient-a", "Juan")
    )

    zoom_webhooks.handle_participant_left(
        _participant_left_payload(HOST_ID, "Test Doctor")
    )

    assert set(scheduled["kwargs"]) == {"release_delivery_id"}
    release_delivery_id = scheduled["kwargs"]["release_delivery_id"]
    assert release_delivery_id.startswith("webhook_delivery_")
    assert MEETING_ID not in str(scheduled)
    assert BOT_ID not in str(scheduled)
    assert scheduled["countdown"] == 600
    assert clean_redis.get(_key("merge_task_id")).decode() == "celery-task-42"
    # Recording is paused, not active
    assert clean_redis.get(_key("recording_active")) is None
    # stop_recording fired but leave_call did not — bot stays connected
    assert mock_recall["stop_recording"] == [BOT_ID]
    assert mock_recall["leave_call"] == []
    assert clean_redis.get(_key("bot_id")).decode() == BOT_ID
    assert clean_redis.get(_key("paused_at")) is not None
    intent = cast(
        ExternalWebhookDelivery | None,
        ExternalWebhookDelivery.where(
            ExternalWebhookDelivery.event_type == "internal.zoom_bot_pause"
        ).first(),
    )
    assert intent is not None
    assert intent.state == "completed"
    assert intent.encrypted_payload is None


def test_pause_stop_failure_retains_active_bot_and_does_not_arm_window(
    clean_redis,
    doctor_with_zoom,
    mock_recall,
    mock_zoom,
    monkeypatch,
):
    """An ambiguous provider stop must never be rewritten as a safe pause."""
    from app.services import recall, zoom_webhooks

    from app.models.external_webhook_delivery import ExternalWebhookDelivery

    zoom_webhooks.handle_meeting_started(_started_payload())
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload(HOST_ID, "Test Doctor")
    )
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload("patient-a", "Juan")
    )

    def fail_stop(bot_id):
        mock_recall["stop_recording"].append(bot_id)
        raise RuntimeError("provider timeout")

    monkeypatch.setattr(recall, "stop_recording", fail_stop)

    with pytest.raises(RuntimeError, match="provider timeout"):
        zoom_webhooks.handle_participant_left(
            _participant_left_payload(HOST_ID, "Test Doctor")
        )

    assert mock_recall["stop_recording"] == [BOT_ID]
    assert clean_redis.get(_key("bot_id")) == BOT_ID.encode()
    assert clean_redis.get(_key("recording_active")) == b"1"
    assert clean_redis.get(_key("paused_at")) is None
    assert clean_redis.get(_key("merge_task_id")) is None
    assert clean_redis.get(_key("merge_generation")) is None
    intent = cast(
        ExternalWebhookDelivery | None,
        ExternalWebhookDelivery.where(
            ExternalWebhookDelivery.event_type == "internal.zoom_bot_pause"
        ).first(),
    )
    assert intent is not None
    assert intent.state == "pending"
    assert intent.encrypted_payload is not None
    assert BOT_ID not in intent.encrypted_payload


def test_pause_queue_failure_leaves_durable_reconcilable_intent(
    clean_redis,
    doctor_with_zoom,
    mock_recall,
    mock_zoom,
    monkeypatch,
):
    from app.jobs import merge_window
    from app.services import zoom_webhooks

    from app.models.external_webhook_delivery import ExternalWebhookDelivery

    zoom_webhooks.handle_meeting_started(_started_payload())
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload(HOST_ID, "Test Doctor")
    )
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload("patient-a", "Patient A")
    )
    monkeypatch.setattr(
        merge_window.expire,
        "apply_async",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("queue down")),
    )

    with pytest.raises(RuntimeError, match="queue down"):
        zoom_webhooks.handle_participant_left(
            _participant_left_payload(HOST_ID, "Test Doctor")
        )

    intent = cast(
        ExternalWebhookDelivery | None,
        ExternalWebhookDelivery.where(
            ExternalWebhookDelivery.event_type == "internal.zoom_bot_pause"
        ).first(),
    )
    assert intent is not None
    assert intent.state == "pending"
    assert clean_redis.get(_key("bot_id")) == BOT_ID.encode()
    assert clean_redis.get(_key("recording_active")) is None
    assert clean_redis.get(_key("merge_generation")) == b"1"


def test_pause_release_intent_survives_total_redis_loss(
    clean_redis,
    doctor_with_zoom,
    mock_recall,
    mock_zoom,
    monkeypatch,
):
    from typeid import TypeID

    from app.jobs import merge_window
    from app.services import zoom_webhooks

    from app.models.external_webhook_delivery import ExternalWebhookDelivery

    scheduled: dict[str, object] = {}

    class FakeAsyncResult:
        id = "celery-release-only-internal-id"

    def fake_apply_async(*, kwargs, countdown):
        scheduled["kwargs"] = kwargs
        scheduled["countdown"] = countdown
        return FakeAsyncResult()

    monkeypatch.setattr(merge_window.expire, "apply_async", fake_apply_async)

    zoom_webhooks.handle_meeting_started(_started_payload())
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload(HOST_ID, "Test Doctor")
    )
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload("patient-a", "Patient A")
    )
    zoom_webhooks.handle_participant_left(
        _participant_left_payload(HOST_ID, "Test Doctor")
    )

    assert set(cast(dict, scheduled["kwargs"])) == {"release_delivery_id"}
    delivery_id = cast(dict, scheduled["kwargs"])["release_delivery_id"]
    assert isinstance(delivery_id, str)
    assert MEETING_ID not in str(scheduled)
    assert BOT_ID not in str(scheduled)
    delivery = ExternalWebhookDelivery.get(id=TypeID.from_string(delivery_id))
    assert delivery is not None
    assert delivery.state == "pending"
    assert delivery.encrypted_payload is not None
    assert BOT_ID not in delivery.encrypted_payload
    delivery.next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    delivery.save()

    for key in cast(
        list[str | bytes], clean_redis.keys(f"recall:meeting:{MEETING_ID}:*")
    ):
        clean_redis.delete(key)

    merge_window.perform(release_delivery_id=delivery_id)

    assert mock_recall["stop_recording"] == [BOT_ID, BOT_ID]
    assert mock_recall["leave_call"] == [BOT_ID]
    completed = ExternalWebhookDelivery.get(id=TypeID.from_string(delivery_id))
    assert completed is not None
    assert completed.state == "completed"
    assert completed.encrypted_payload is None


def test_doctor_return_with_patient_does_not_resume_same_bot(
    clean_redis,
    doctor_with_zoom,
    mock_recall,
    mock_zoom,
    monkeypatch,
):
    """Redis participant identity cannot authorize a second segment."""
    revoked = []

    class FakeAsyncResult:
        id = "celery-task-99"

    from app.jobs import merge_window

    monkeypatch.setattr(
        merge_window.expire,
        "apply_async",
        lambda *args, **kwargs: FakeAsyncResult(),
    )

    from app.celery import celery_app

    monkeypatch.setattr(
        celery_app.control,
        "revoke",
        lambda task_id, terminate=False: revoked.append(task_id),
    )

    from app.services import zoom_webhooks

    zoom_webhooks.handle_meeting_started(_started_payload())
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload(HOST_ID, "Test Doctor")
    )
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload("patient-a", "Juan")
    )
    zoom_webhooks.handle_participant_left(
        _participant_left_payload(HOST_ID, "Test Doctor")
    )
    # Doctor returns while the prior participant remains in Redis. That is not
    # strong enough identity to authorize recording another segment.
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload(HOST_ID, "Test Doctor")
    )

    assert revoked == []
    assert clean_redis.get(_key("merge_task_id")) == b"celery-task-99"
    assert clean_redis.get(_key("doctor_present")) == b"1"
    assert clean_redis.get(_key("recording_active")) is None
    # Only the explicitly started first segment exists.
    assert len(mock_recall["create_bot"]) == 1
    assert mock_recall["start_recording"] == [BOT_ID]
    assert mock_recall["leave_call"] == []


def test_doctor_returns_to_empty_room_stays_paused(
    clean_redis,
    doctor_with_zoom,
    mock_recall,
    mock_zoom,
    monkeypatch,
):
    """Doctor leaves while a patient is present (pause #1), then the patient
    leaves too (pause #2 — but recording is already paused). Doctor comes
    back to an empty room: there's no non-doctor to resume against, so we
    DO NOT call start_recording. The merge_window task remains armed."""
    revoked = []

    class FakeAsyncResult:
        id = "celery-task-empty"

    from app.jobs import merge_window

    monkeypatch.setattr(
        merge_window.expire,
        "apply_async",
        lambda *args, **kwargs: FakeAsyncResult(),
    )
    from app.celery import celery_app

    monkeypatch.setattr(
        celery_app.control,
        "revoke",
        lambda task_id, terminate=False: revoked.append(task_id),
    )

    from app.services import zoom_webhooks

    zoom_webhooks.handle_meeting_started(_started_payload())
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload(HOST_ID, "Test Doctor")
    )
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload("patient-a", "Juan")
    )
    # Doctor leaves first → pause
    zoom_webhooks.handle_participant_left(
        _participant_left_payload(HOST_ID, "Test Doctor")
    )
    # Patient leaves too; still paused
    zoom_webhooks.handle_participant_left(
        _participant_left_payload("patient-a", "Juan")
    )
    # Doctor returns alone
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload(HOST_ID, "Test Doctor")
    )

    # No resume because no patient is in the room
    assert mock_recall["start_recording"] == [BOT_ID]  # only the original
    assert clean_redis.get(_key("recording_active")) is None
    # merge_task still armed — doctor showing up alone doesn't end the visit
    assert revoked == []
    assert clean_redis.get(_key("merge_task_id")) is not None
    assert clean_redis.get(_key("bot_id")).decode() == BOT_ID


def test_merge_window_expires_leaves_bot_and_clears_state(
    clean_redis,
    doctor_with_zoom,
    mock_recall,
    mock_zoom,
    monkeypatch,
):
    """Stub apply_async so handle_participant_left only SCHEDULES the
    merge_window task. Then call merge_window.perform directly to simulate
    the task firing 10 min later with no resume in between. The bot is
    already paused (stop_recording fired on doctor-left), so this only
    needs to call leave_call and clear keys."""

    class FakeAsyncResult:
        id = "celery-task-mw-expires"

    from app.jobs import merge_window

    monkeypatch.setattr(
        merge_window.expire,
        "apply_async",
        lambda *args, **kwargs: FakeAsyncResult(),
    )

    from app.services import zoom_webhooks

    zoom_webhooks.handle_meeting_started(_started_payload())
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload(HOST_ID, "Test Doctor")
    )
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload("patient-a", "Juan")
    )
    zoom_webhooks.handle_participant_left(
        _participant_left_payload(HOST_ID, "Test Doctor")
    )

    # Pause-state sanity: stop_recording fired but leave_call did not.
    assert mock_recall["stop_recording"] == [BOT_ID]
    assert mock_recall["leave_call"] == []
    assert clean_redis.get(_key("recording_active")) is None
    assert clean_redis.get(_key("bot_id")).decode() == BOT_ID

    # Simulate the merge_window task firing after 10 min with no resume
    occurrence_digest = clean_redis.get(_key("occurrence_digest")).decode()
    merge_window.perform(MEETING_ID, 1, occurrence_digest)

    assert mock_recall["stop_recording"] == [BOT_ID]  # not called again
    assert mock_recall["leave_call"] == [BOT_ID]
    assert clean_redis.get(_key("bot_id")) is None
    assert clean_redis.get(_key("merge_task_id")) is None


def test_merge_window_perform_noop_when_recording_resumed(
    clean_redis,
    doctor_with_zoom,
    mock_recall,
    mock_zoom,
    monkeypatch,
):
    """If recording resumed before the task fires (recording_active=1),
    merge_window.perform must be a no-op — the bot stays connected."""
    from app.jobs import merge_window

    clean_redis.set(_key("bot_id"), BOT_ID)
    clean_redis.set(_key("recording_active"), "1")
    clean_redis.set(_key("merge_task_id"), "celery-task-stale")

    merge_window.perform(MEETING_ID)

    assert mock_recall["leave_call"] == []
    assert clean_redis.get(_key("bot_id")).decode() == BOT_ID
    assert clean_redis.get(_key("recording_active")) == b"1"


def test_prior_occurrence_meeting_ended_is_fenced_during_new_pmr_session(
    clean_redis,
    doctor_with_zoom,
    mock_recall,
    mock_zoom,
):
    """A delayed PMR end cannot erase a new occurrence's lifecycle state."""
    from app.services import zoom_webhooks

    zoom_webhooks.handle_meeting_started(_started_payload())
    zoom_webhooks.handle_meeting_ended(_meeting_ended_payload("zoom-occurrence-prior"))

    # State must survive — host_id intact so the doctor is recognized.
    assert clean_redis.get(_key("host_id")) is not None
    assert clean_redis.get(_key("doctor_id")) is not None
    assert clean_redis.get(_key("starting_lock")) is not None
    assert mock_recall["stop_recording"] == []
    assert mock_recall["leave_call"] == []


def test_matching_short_meeting_ended_is_not_blocked_by_starting_lock(
    clean_redis,
    doctor_with_zoom,
    mock_recall,
    mock_zoom,
):
    from app.services import zoom_webhooks

    zoom_webhooks.handle_meeting_started(_started_payload())
    assert clean_redis.get(_key("starting_lock")) is not None

    zoom_webhooks.handle_meeting_ended(_meeting_ended_payload())

    assert clean_redis.get(_key("doctor_id")) is None
    assert clean_redis.get(_key("occurrence_digest")) is None


def test_delayed_prior_meeting_started_cannot_replace_current_idle_occurrence(
    clean_redis,
    doctor_with_zoom,
    mock_recall,
    mock_zoom,
):
    from app.services import zoom_webhooks

    zoom_webhooks.handle_meeting_started(_started_payload("occurrence-current"))
    current_digest = clean_redis.get(_key("occurrence_digest"))
    current_doctor_id = clean_redis.get(_key("doctor_id"))
    clean_redis.set(_key("patient_count"), "1")
    clean_redis.rpush(_key("participants_order"), "current-patient")

    zoom_webhooks.handle_meeting_started(_started_payload("occurrence-prior"))

    assert clean_redis.get(_key("occurrence_digest")) == current_digest
    assert clean_redis.get(_key("doctor_id")) == current_doctor_id
    assert clean_redis.get(_key("patient_count")) == b"1"
    assert clean_redis.lrange(_key("participants_order"), 0, -1) == [b"current-patient"]


def test_delayed_prior_occurrence_participant_events_cannot_mutate_new_session(
    clean_redis,
    doctor_with_zoom,
    mock_recall,
    mock_zoom,
):
    from app.services import zoom_webhooks

    zoom_webhooks.handle_meeting_started(_started_payload())
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload(HOST_ID, "Test Doctor")
    )
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload("patient-a", "Patient A")
    )

    zoom_webhooks.handle_participant_left(
        _participant_left_payload(
            "patient-a",
            "Patient A",
            occurrence_uuid="zoom-occurrence-prior",
        )
    )

    assert clean_redis.get(_key("patient_count")) == b"1"
    assert clean_redis.get(_key("recording_active")) == b"1"
    assert mock_recall["stop_recording"] == []


def test_uuid_less_event_cannot_mutate_uuid_bound_session(
    clean_redis,
    doctor_with_zoom,
    mock_recall,
    mock_zoom,
):
    from app.services import zoom_webhooks

    zoom_webhooks.handle_meeting_started(_started_payload())
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload(
            "patient-old", "Old Participant", occurrence_uuid=None
        )
    )

    assert clean_redis.get(_key("patient_count")) is None
    assert mock_recall["create_bot"] == []


def test_legacy_uuid_less_session_retains_starting_lock_cutover_guard(
    clean_redis,
    doctor_with_zoom,
    mock_recall,
    mock_zoom,
):
    from app.services import zoom_webhooks

    zoom_webhooks.handle_meeting_started(_started_payload(None))
    zoom_webhooks.handle_meeting_ended(_meeting_ended_payload(None))

    assert clean_redis.get(_key("doctor_id")) is not None
    clean_redis.delete(_key("starting_lock"))
    zoom_webhooks.handle_meeting_ended(_meeting_ended_payload(None))
    assert clean_redis.get(_key("doctor_id")) is None


def test_meeting_cleanup_preserves_generation_against_prior_session_tasks(
    clean_redis,
):
    from app.services import zoom_webhooks

    occurrence_digest = digest_provider_reference(f"zoom-occurrence:{OCCURRENCE_UUID}")
    clean_redis.set(_key("bot_id"), BOT_ID)
    clean_redis.set(_key("merge_generation"), "7")
    clean_redis.set(_key("occurrence_digest"), occurrence_digest)
    clean_redis.set(_key(f"watchdog_alerted:{occurrence_digest}"), "alerted")

    zoom_webhooks._clear_meeting_keys(clean_redis, MEETING_ID)

    assert clean_redis.get(_key("bot_id")) is None
    assert clean_redis.get(_key("merge_generation")) == b"7"
    assert clean_redis.get(_key(f"watchdog_alerted:{occurrence_digest}")) is None


def test_meeting_ended_with_no_bot_logs_no_recording(
    clean_redis,
    doctor_with_zoom,
    mock_recall,
    mock_zoom,
):
    from app.services import zoom_webhooks

    zoom_webhooks.handle_meeting_started(_started_payload())
    # Doctor never joins
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload("patient-a", "Juan")
    )
    # Simulate the lock having expired so the legitimate end gets through
    # (the lock is a 60s TTL guard against stale redeliveries).
    clean_redis.delete(_key("starting_lock"))
    zoom_webhooks.handle_meeting_ended(_meeting_ended_payload())

    assert mock_recall["create_bot"] == []
    assert mock_recall["stop_recording"] == []
    assert mock_recall["leave_call"] == []
    # All keys cleared
    for suffix in ["doctor_id", "host_id", "participants_order", "participants_names"]:
        assert clean_redis.get(_key(suffix)) is None


def test_meeting_ended_with_active_recording_stops_and_leaves(
    clean_redis,
    doctor_with_zoom,
    mock_recall,
    mock_zoom,
):
    from app.services import zoom_webhooks

    zoom_webhooks.handle_meeting_started(_started_payload())
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload(HOST_ID, "Test Doctor")
    )
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload("patient-a", "Juan")
    )
    clean_redis.delete(_key("starting_lock"))
    zoom_webhooks.handle_meeting_ended(_meeting_ended_payload())

    assert mock_recall["stop_recording"] == [BOT_ID]
    assert mock_recall["leave_call"] == [BOT_ID]
    # All keys cleared
    assert clean_redis.get(_key("bot_id")) is None
    assert clean_redis.get(_key("recording_active")) is None


def test_meeting_ended_attempts_leave_after_stop_failure_and_then_clears(
    clean_redis,
    doctor_with_zoom,
    mock_recall,
    mock_zoom,
    monkeypatch,
):
    from app.services import recall, zoom_webhooks

    zoom_webhooks.handle_meeting_started(_started_payload())
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload(HOST_ID, "Test Doctor")
    )
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload("patient-a", "Juan")
    )
    clean_redis.delete(_key("starting_lock"))

    def fail_stop(bot_id):
        mock_recall["stop_recording"].append(bot_id)
        raise RuntimeError("stop failed")

    monkeypatch.setattr(recall, "stop_recording", fail_stop)

    zoom_webhooks.handle_meeting_ended(_meeting_ended_payload())

    assert mock_recall["stop_recording"] == [BOT_ID]
    assert mock_recall["leave_call"] == [BOT_ID]
    assert clean_redis.get(_key("bot_id")) is None
    assert clean_redis.get(_key("recording_active")) is None


def test_meeting_ended_leave_failure_retains_state_and_propagates(
    clean_redis,
    doctor_with_zoom,
    mock_recall,
    mock_zoom,
    monkeypatch,
):
    from app.celery import celery_app
    from app.services import recall, zoom_webhooks

    from app.models.external_webhook_delivery import ExternalWebhookDelivery

    zoom_webhooks.handle_meeting_started(_started_payload())
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload(HOST_ID, "Test Doctor")
    )
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload("patient-a", "Juan")
    )
    zoom_mtg = ZoomMeeting.get(
        doctor_id=doctor_with_zoom.id,
        zoom_meeting_id=MEETING_ID,
    )
    assert zoom_mtg is not None
    clean_redis.delete(_key("starting_lock"))
    clean_redis.set(_key("merge_task_id"), "merge-still-recoverable")
    revoked = []
    monkeypatch.setattr(
        celery_app.control,
        "revoke",
        lambda task_id, terminate=False: revoked.append(task_id),
    )

    def fail_leave(bot_id):
        mock_recall["leave_call"].append(bot_id)
        raise RuntimeError("leave failed")

    monkeypatch.setattr(recall, "leave_call", fail_leave)

    with pytest.raises(RuntimeError, match="leave failed"):
        zoom_webhooks.handle_meeting_ended(_meeting_ended_payload())

    assert mock_recall["stop_recording"] == [BOT_ID]
    assert mock_recall["leave_call"] == [BOT_ID]
    assert clean_redis.get(_key("bot_id")) == BOT_ID.encode()
    assert clean_redis.get(_key("recording_active")) == b"1"
    assert clean_redis.get(_key("merge_task_id")) == b"merge-still-recoverable"
    assert revoked == []
    persisted_zoom_mtg = ZoomMeeting.get(id=zoom_mtg.id)
    assert persisted_zoom_mtg is not None
    assert persisted_zoom_mtg.status == ZoomMeetingStatus.started
    intent = cast(
        ExternalWebhookDelivery | None,
        ExternalWebhookDelivery.where(
            (ExternalWebhookDelivery.event_type == "internal.zoom_bot_release")
            & (ExternalWebhookDelivery.state == "pending")
        ).first(),
    )
    assert intent is not None
    assert intent.state == "pending"
    assert intent.encrypted_payload is not None
    assert BOT_ID not in intent.encrypted_payload


def test_meeting_ended_status_failure_is_repaired_before_intent_completion(
    clean_redis,
    doctor_with_zoom,
    mock_recall,
    mock_zoom,
    monkeypatch,
):
    from app.jobs import merge_window
    from app.services import external_webhook_inbox as inbox
    from app.services import zoom_webhooks

    from app.models.external_webhook_delivery import ExternalWebhookDelivery

    zoom_webhooks.handle_meeting_started(_started_payload())
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload(HOST_ID, "Test Doctor")
    )
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload("patient-a", "Patient A")
    )
    zoom_mtg = ZoomMeeting.get(
        doctor_id=doctor_with_zoom.id,
        zoom_meeting_id=MEETING_ID,
    )
    assert zoom_mtg is not None

    real_mark_meeting_ended = merge_window.mark_meeting_ended
    monkeypatch.setattr(
        merge_window,
        "mark_meeting_ended",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("status persistence failed")
        ),
    )

    with pytest.raises(RuntimeError, match="status persistence failed"):
        zoom_webhooks.handle_meeting_ended(_meeting_ended_payload())

    after_failure = ZoomMeeting.get(id=zoom_mtg.id)
    assert after_failure is not None
    assert after_failure.status == ZoomMeetingStatus.started
    assert clean_redis.get(_key("bot_id")) == BOT_ID.encode()
    intent = cast(
        ExternalWebhookDelivery | None,
        ExternalWebhookDelivery.where(
            (ExternalWebhookDelivery.event_type == "internal.zoom_bot_release")
            & (ExternalWebhookDelivery.state == "pending")
        ).first(),
    )
    assert intent is not None
    intent.next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    intent.save()

    monkeypatch.setattr(
        merge_window,
        "mark_meeting_ended",
        real_mark_meeting_ended,
    )
    inbox.process_delivery(str(intent.id))

    repaired = ZoomMeeting.get(id=zoom_mtg.id)
    assert repaired is not None
    assert repaired.status == ZoomMeetingStatus.ended
    assert clean_redis.get(_key("bot_id")) is None
    completed = ExternalWebhookDelivery.get(id=intent.id)
    assert completed is not None
    assert completed.state == "completed"
    assert completed.encrypted_payload is None


def test_meeting_ended_revokes_pending_merge_window_task(
    clean_redis,
    doctor_with_zoom,
    mock_recall,
    mock_zoom,
    monkeypatch,
):
    revoked = []

    class FakeAsyncResult:
        id = "celery-task-77"

    from app.jobs import merge_window

    monkeypatch.setattr(
        merge_window.expire,
        "apply_async",
        lambda *args, **kwargs: FakeAsyncResult(),
    )

    from app.celery import celery_app

    monkeypatch.setattr(
        celery_app.control,
        "revoke",
        lambda task_id, terminate=False: revoked.append(task_id),
    )

    from app.services import zoom_webhooks

    zoom_webhooks.handle_meeting_started(_started_payload())
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload(HOST_ID, "Test Doctor")
    )
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload("patient-a", "Juan")
    )
    zoom_webhooks.handle_participant_left(
        _participant_left_payload(HOST_ID, "Test Doctor")
    )
    clean_redis.delete(_key("starting_lock"))
    zoom_webhooks.handle_meeting_ended(_meeting_ended_payload())

    assert "celery-task-77" in revoked


def test_first_capture_rejects_bound_appointment_with_existing_transcript(
    clean_redis,
    doctor_with_zoom,
    mock_recall,
    mock_zoom,
):
    """An exact binding is still invalid once the appointment is complete."""
    from app.services import zoom_webhooks

    from app.models.ai_visit_transcript import AIVisitTranscript, TranscriptEntry
    from app.models.appointment import Appointment

    zoom_mtg = ZoomMeeting.get(
        doctor_id=doctor_with_zoom.id,
        zoom_meeting_id=MEETING_ID,
    )
    assert zoom_mtg is not None
    assert zoom_mtg.appointment_id is not None
    old_appt = Appointment.one(zoom_mtg.appointment_id)
    AIVisitTranscript(
        doctor_id=doctor_with_zoom.id,
        appointment_id=old_appt.id,
        patient_id=old_appt.patient_id,
        duration=120,
        transcript_data=[
            TranscriptEntry(
                speaker="doctor",
                content="prior visit",
                startTime=0,
                endTime=0,
            )
        ],
    ).save()

    zoom_webhooks.handle_meeting_started(_started_payload())
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload(HOST_ID, "Test Doctor")
    )
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload("patient-b", "Sofia Lopez")
    )

    assert mock_recall["create_bot"] == []
    assert mock_recall["start_recording"] == []
    assert clean_redis.get(_key("capture_started")) is None


def test_first_capture_uses_exact_binding_not_closer_time_window_appointment(
    clean_redis,
    doctor_with_zoom,
    mock_recall,
    mock_zoom,
):
    """A nearby untranscribed appointment may belong to another patient."""
    from datetime import datetime as _dt
    from datetime import timedelta as _td

    from app.services import zoom_webhooks

    from app.models.appointment import Appointment
    from app.models.patient import Patient

    zoom_mtg = ZoomMeeting.get(
        doctor_id=doctor_with_zoom.id,
        zoom_meeting_id=MEETING_ID,
    )
    assert zoom_mtg is not None
    assert zoom_mtg.appointment_id is not None
    explicitly_bound_id = zoom_mtg.appointment_id

    patient = Patient.find_or_create_by(
        doctor_id=doctor_with_zoom.id,
        name="Calendar Patient",
        date_of_birth=_dt(1990, 1, 1),
    )
    pre_existing = Appointment.find_or_create_by(
        doctor_id=doctor_with_zoom.id,
        patient_id=patient.id,
        appointment_date=_dt.utcnow() - _td(minutes=5),
        created_by_system="recall",
    )

    zoom_webhooks.handle_meeting_started(_started_payload())
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload(HOST_ID, "Test Doctor")
    )
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload("patient-c", "Sofia Lopez")
    )

    bound_appointment_id = clean_redis.get(
        f"recall:recording:recording-{BOT_ID}:appointment_id"
    ).decode()
    assert bound_appointment_id == str(explicitly_bound_id)
    assert bound_appointment_id != str(pre_existing.id)
    consumed_meeting = ZoomMeeting.get(
        doctor_id=doctor_with_zoom.id,
        zoom_meeting_id=MEETING_ID,
    )
    assert consumed_meeting is not None
    assert consumed_meeting.appointment_id == explicitly_bound_id
    assert consumed_meeting.capture_authorization_consumed_at is not None


# --------- Scenario: staging Zoom bot dispatch allowlist ---------


def _doctor_with_zoom_email(email: str):
    doctor = DoctorFactory.build(email=email).save()
    DoctorVideoIntegration(
        doctor_id=doctor.id,
        provider=VideoProvider.zoom,
        provider_user_id=HOST_ID,
        access_token="tok",
        refresh_token="rtok",
        token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    ).save()
    _bind_zoom_meeting(doctor)
    return doctor


def test_staging_blocks_bot_for_non_allowlisted_doctor(
    clean_redis,
    mock_recall,
    mock_zoom,
    allow_doctor_access,
    monkeypatch,
):
    """In staging, a doctor whose email is not in the staging allowlist must
    never get a Recall bot. This prevents a second bot joining a real meeting
    when the same Zoom account is also connected in production."""
    from app.services import zoom_webhooks

    _doctor_with_zoom_email("real.doctor@example.com")
    monkeypatch.setattr(zoom_webhooks, "is_staging", lambda: True, raising=False)

    zoom_webhooks.handle_meeting_started(_started_payload())
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload("patient-1", "Flor Guerrero")
    )
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload(HOST_ID, "Test Doctor")
    )

    assert mock_recall["create_bot"] == []
    assert clean_redis.get(_key("bot_id")) is None


def test_staging_allows_bot_for_allowlisted_doctor(
    clean_redis,
    mock_recall,
    mock_zoom,
    allow_doctor_access,
    monkeypatch,
):
    """The dedicated staging test account (zoom-reviewer@) is allowlisted, so
    it still gets a bot in staging."""
    from app.services import zoom_webhooks

    _doctor_with_zoom_email("zoom-reviewer@cosmaneura.com")
    monkeypatch.setattr(zoom_webhooks, "is_staging", lambda: True, raising=False)

    zoom_webhooks.handle_meeting_started(_started_payload())
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload("patient-1", "Flor Guerrero")
    )
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload(HOST_ID, "Test Doctor")
    )

    assert len(mock_recall["create_bot"]) == 1


def _run_meeting_flow(zoom_webhooks):
    zoom_webhooks.handle_meeting_started(_started_payload())
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload("patient-1", "Flor Guerrero")
    )
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload(HOST_ID, "Test Doctor")
    )


def test_prod_allows_allowlisted_doctor(
    clean_redis,
    mock_recall,
    mock_zoom,
    allow_doctor_access,
    monkeypatch,
):
    """In production, an email on the prod allowlist gets a bot."""
    from app.services import zoom_webhooks

    _doctor_with_zoom_email("greg@mycatholicdoctor.com")
    monkeypatch.setattr(zoom_webhooks, "is_production", lambda: True, raising=False)

    _run_meeting_flow(zoom_webhooks)

    assert len(mock_recall["create_bot"]) == 1


def test_prod_blocks_non_allowlisted_doctor(
    clean_redis,
    mock_recall,
    mock_zoom,
    allow_doctor_access,
    monkeypatch,
):
    """In production, an email NOT on the prod allowlist never gets a bot."""
    from app.services import zoom_webhooks

    doctor = _doctor_with_zoom_email("random.doctor@clinic.com")
    monkeypatch.setattr(zoom_webhooks, "is_production", lambda: True, raising=False)

    _run_meeting_flow(zoom_webhooks)

    assert mock_recall["create_bot"] == []
    assert clean_redis.get(_key("bot_id")) is None
    meeting = ZoomMeeting.get(doctor_id=doctor.id, zoom_meeting_id=MEETING_ID)
    assert meeting is not None
    assert meeting.capture_authorization_occurrence_digest is not None
    assert meeting.capture_authorization_consumed_at is None


def test_prod_blocks_staging_test_account(
    clean_redis,
    mock_recall,
    mock_zoom,
    allow_doctor_access,
    monkeypatch,
):
    """The staging test account (zoom-reviewer@) must NOT get a bot in
    production -- it is connected only in staging, and letting it bot in prod
    too would put two bots in one real meeting."""
    from app.services import zoom_webhooks

    _doctor_with_zoom_email("zoom-reviewer@cosmaneura.com")
    monkeypatch.setattr(zoom_webhooks, "is_production", lambda: True, raising=False)

    _run_meeting_flow(zoom_webhooks)

    assert mock_recall["create_bot"] == []
    assert clean_redis.get(_key("bot_id")) is None


# --------- Payload-shape probe (temporary, see _log_payload_shape) ---------


def test_payload_shape_probe_logs_fixed_booleans_only():
    """The probe exists to answer whether Zoom sends uuid/host_id on every
    event. It must never log payload *values* — those carry PHI."""
    from structlog.testing import capture_logs

    from app.services import zoom_webhooks

    payload = _participant_joined_payload(HOST_ID, "Jane Patient")
    payload["payload"]["object"]["clinical-key-sentinel"] = "private-value"
    payload["payload"]["object"]["participant"]["patient-key-sentinel"] = (
        "private-value"
    )

    with capture_logs() as logs:
        zoom_webhooks._log_payload_shape("meeting.participant_joined", payload)

    entry = next(e for e in logs if e["event"] == "zoom payload shape")
    assert entry["has_uuid"] is True
    assert entry["has_host_id"] is False
    assert entry["has_participant"] is True
    assert entry["participant_has_id"] is True
    assert entry["participant_has_user_id"] is False
    # No provider-controlled key or value may appear anywhere in the event.
    for sentinel in (
        "Jane Patient",
        HOST_ID,
        "clinical-key-sentinel",
        "patient-key-sentinel",
        "private-value",
    ):
        assert sentinel not in str(entry)


def test_zoom_session_logs_hash_provider_and_clinical_identifiers(
    clean_redis,
    doctor_with_zoom,
    mock_recall,
    mock_zoom,
):
    import json

    from structlog.testing import capture_logs

    from app.services import zoom_webhooks

    patient_id = "patient-provider-private"
    patient_name = "Clinical Name Sentinel"
    with capture_logs() as logs:
        zoom_webhooks.handle_meeting_started(_started_payload())
        zoom_webhooks.handle_participant_joined(
            _participant_joined_payload(HOST_ID, "Provider Name Sentinel")
        )
        zoom_webhooks.handle_participant_joined(
            _participant_joined_payload(patient_id, patient_name)
        )

    serialized = json.dumps(logs, sort_keys=True)
    for raw_value in (
        MEETING_ID,
        HOST_ID,
        BOT_ID,
        patient_id,
        patient_name,
        "Provider Name Sentinel",
        str(doctor_with_zoom.id),
    ):
        assert raw_value not in serialized
    assert "hmac:" in serialized


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"payload": {}},
        {"payload": {"object": None}},
        {"payload": {"object": {}}},
        {"payload": {"object": {"participant": "not-a-dict"}}},
    ],
)
def test_payload_shape_probe_tolerates_malformed_payloads(payload):
    """The probe runs on every zoom webhook before dispatch, so raising here
    would take down all zoom processing."""
    from app.services import zoom_webhooks

    zoom_webhooks._log_payload_shape("meeting.started", payload)


def test_dispatch_logs_shape_even_for_unhandled_event_types():
    from structlog.testing import capture_logs

    from app.services import zoom_webhooks

    with capture_logs() as logs:
        zoom_webhooks.dispatch("meeting.sharing_started", _started_payload())

    events = [e["event"] for e in logs]
    assert "zoom payload shape" in events
    assert "no handler for zoom event type" in events


# --------- Monitoring is armed from capture-expected, and never blocks ---------


def _doctor_with_unbound_meeting(allow_doctor_access):
    "A tracked Zoom meeting that nothing bound to an appointment."
    doctor = DoctorFactory.build().save()
    DoctorVideoIntegration(
        doctor_id=doctor.id,
        provider=VideoProvider.zoom,
        provider_user_id=HOST_ID,
        access_token="tok",
        refresh_token="rtok",
        token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    ).save()
    ZoomMeeting(
        doctor_id=doctor.id,
        zoom_meeting_id=MEETING_ID,
        topic="Unbound visit",
        start_time=datetime.now(timezone.utc),
        duration=30,
        join_url=f"https://zoom.us/j/{MEETING_ID}",
        status=ZoomMeetingStatus.scheduled,
        appointment_id=None,
    ).save()
    return doctor


def test_watchdog_is_armed_when_capture_is_expected(
    clean_redis, doctor_with_zoom, mock_recall, mock_zoom, monkeypatch
):
    """Arming keys off the authorization actually arming, not off the session
    merely being registered."""
    from app.jobs import verify_bot_joined
    from app.services import zoom_webhooks

    monkeypatch.setattr(zoom_webhooks, "ZOOM_WATCHDOG_ENABLED", True)
    tracked = ZoomMeeting.one(doctor_id=doctor_with_zoom.id, zoom_meeting_id=MEETING_ID)
    expected_occurrence = digest_provider_reference(
        f"zoom-occurrence:{OCCURRENCE_UUID}"
    )
    assert verify_bot_joined._capture_still_expected(
        str(tracked.id), MEETING_ID, expected_occurrence
    ), "the authorization is durable before the start-status save"
    armed: list[tuple] = []
    monkeypatch.setattr(
        verify_bot_joined,
        "queue_durably",
        lambda meeting_record_id, meeting_id, occurrence: armed.append(
            (meeting_record_id, meeting_id, occurrence)
        ),
    )

    zoom_webhooks.handle_meeting_started(_started_payload())

    assert len(armed) == 1
    meeting_record_id, meeting_id, occurrence = armed[0]
    assert meeting_id == MEETING_ID
    assert meeting_record_id == str(tracked.id)
    # The occurrence travels with the task so a check that fires after this
    # visit ends cannot judge the next patient's.
    assert occurrence is not None
    tracked.status = ZoomMeetingStatus.ended
    tracked.save()
    assert not verify_bot_joined._capture_still_expected(
        str(tracked.id), MEETING_ID, expected_occurrence
    )


def test_watchdog_producer_is_dormant_until_enabled(monkeypatch):
    from app.services import zoom_webhooks

    armed: list[tuple] = []
    monkeypatch.setattr(
        zoom_webhooks.verify_bot_joined,
        "queue_durably",
        lambda *args: armed.append(args),
    )
    monkeypatch.setattr(zoom_webhooks, "ZOOM_WATCHDOG_ENABLED", False)
    zoom_webhooks.arm_join_watchdog(MEETING_ID, "a" * 64, "zmtg_invalid")

    assert armed == []


def test_unbound_start_waits_for_supported_late_authorization(
    clean_redis,
    allow_doctor_access,
    mock_recall,
    mock_zoom,
    monkeypatch,
    captured_alerts,
):
    """An unbound start remains quiet while supported late authorization waits."""
    from app.jobs import verify_bot_joined
    from app.services import zoom_webhooks

    monkeypatch.setattr(zoom_webhooks, "ZOOM_WATCHDOG_ENABLED", True)
    _doctor_with_unbound_meeting(allow_doctor_access)

    armed: list = []
    monkeypatch.setattr(
        verify_bot_joined,
        "queue_durably",
        lambda meeting_id, occurrence: armed.append(meeting_id),
    )

    zoom_webhooks.handle_meeting_started(_started_payload())

    assert armed == []
    assert captured_alerts == []


def test_watchdog_publish_failure_is_durable_and_does_not_break_dispatch(
    clean_redis, doctor_with_zoom, mock_recall, mock_zoom, monkeypatch, captured_alerts
):
    """Monitoring must never take down what it monitors. If Redis or the broker
    is unavailable, arming fails, the session still registers, and the failure
    to watch is itself reported."""
    from app.services import alerting, external_webhook_inbox, zoom_webhooks

    from app.models.external_webhook_delivery import ExternalWebhookDelivery

    monkeypatch.setattr(zoom_webhooks, "ZOOM_WATCHDOG_ENABLED", True)

    def explode(_delivery_id, **_kwargs):
        raise ConnectionError("broker unavailable")

    monkeypatch.setattr(external_webhook_inbox, "publish_delivery", explode)

    zoom_webhooks.handle_meeting_started(_started_payload())

    assert clean_redis.get(_key("doctor_id")) is not None, "dispatch state was lost"
    assert alerting.WatchdogArmingFailed in [c["type"] for c in captured_alerts]
    intent = cast(
        ExternalWebhookDelivery | None,
        ExternalWebhookDelivery.where(
            ExternalWebhookDelivery.event_type
            == external_webhook_inbox.ZOOM_WATCHDOG_ARM_EVENT
        ).first(),
    )
    assert intent is not None and intent.state == "pending"
    assert intent.encrypted_payload is not None
    assert MEETING_ID not in intent.encrypted_payload


def test_blocked_continuation_is_reported(
    clean_redis, doctor_with_zoom, mock_recall, mock_zoom, captured_alerts
):
    """Refusing to continue without an exact encounter binding is correct, but
    the second visit still goes unrecorded. The watchdog cannot surface it --
    capture_started makes the session look healthy to it -- so the refusal
    reports itself."""
    from app.services import alerting, zoom_webhooks

    clean_redis.set(_key("bot_id"), "recall-bot-existing")

    zoom_webhooks._resume_or_start_recording_locked(
        clean_redis, MEETING_ID, "patient-b", "Segunda Paciente"
    )

    assert mock_recall["create_bot"] == []
    assert [c["type"] for c in captured_alerts] == [
        alerting.RecordingContinuationBlocked
    ]
