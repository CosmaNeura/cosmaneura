from datetime import datetime, timedelta, timezone

import pytest

from app.configuration.redis import get_redis

from app.models.doctor_video_integration import (
    DoctorVideoIntegration,
    VideoProvider,
)

from tests.factories import DoctorFactory

MEETING_ID = "99911112222"
HOST_ID = "zoom-host-abc"
BOT_ID = "recall-bot-abc"


def _key(suffix: str) -> str:
    return f"recall:meeting:{MEETING_ID}:{suffix}"


@pytest.fixture
def clean_redis():
    redis = get_redis()
    suffixes = [
        "bot_id", "doctor_id", "host_id", "doctor_present", "recording_active",
        "participants_order", "participants_names", "patient_count",
        "starting_lock", "grace_task_id",
    ]
    for s in suffixes:
        redis.delete(_key(s))
    yield redis
    for s in suffixes:
        redis.delete(_key(s))


@pytest.fixture
def doctor_with_zoom():
    doctor = DoctorFactory.build().save()
    DoctorVideoIntegration(
        doctor_id=doctor.id,
        provider=VideoProvider.zoom,
        provider_user_id=HOST_ID,
        access_token="tok",
        refresh_token="rtok",
        token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    ).save()
    return doctor


@pytest.fixture
def mock_recall(monkeypatch):
    calls = {"create_bot": [], "start_recording": [], "stop_recording": [], "leave_call": []}

    from app.services import recall

    def fake_create_bot(meeting_url, zak_token, bot_name="Cosma", manual_recording=False, metadata=None):
        calls["create_bot"].append(
            {"meeting_url": meeting_url, "metadata": metadata, "manual_recording": manual_recording}
        )
        return {"id": BOT_ID}

    def fake_start_recording(bot_id, wait_for_ready=True):
        calls["start_recording"].append(bot_id)
        return {"id": f"recording-{bot_id}"}

    def fake_stop_recording(bot_id):
        calls["stop_recording"].append(bot_id)

    def fake_leave_call(bot_id):
        calls["leave_call"].append(bot_id)

    monkeypatch.setattr(recall, "create_bot", fake_create_bot)
    monkeypatch.setattr(recall, "start_recording", fake_start_recording)
    monkeypatch.setattr(recall, "stop_recording", fake_stop_recording)
    monkeypatch.setattr(recall, "leave_call", fake_leave_call)
    return calls


@pytest.fixture
def mock_zoom(monkeypatch):
    from app.services import zoom as zoom_service

    monkeypatch.setattr(zoom_service, "get_zak_token", lambda doctor: "zak-fake")
    monkeypatch.setattr(
        zoom_service, "get_meeting_join_url",
        lambda doctor, meeting_id: f"https://zoom.us/j/{meeting_id}",
    )
    monkeypatch.setattr(
        zoom_service, "get_user_display_name", lambda doctor: "Test Doctor",
    )


def _started_payload() -> dict:
    return {
        "payload": {
            "object": {
                "id": MEETING_ID,
                "host_id": HOST_ID,
            }
        }
    }


def _participant_joined_payload(participant_id: str, user_name: str) -> dict:
    return {
        "payload": {
            "object": {
                "id": MEETING_ID,
                "participant": {
                    "id": participant_id,
                    "user_name": user_name,
                },
            }
        }
    }


def _participant_left_payload(participant_id: str, user_name: str) -> dict:
    return _participant_joined_payload(participant_id, user_name)


def _meeting_ended_payload() -> dict:
    return {"payload": {"object": {"id": MEETING_ID}}}


# --------- Scenario 1: meeting.started ---------

def test_meeting_started_does_not_create_bot(
    clean_redis, doctor_with_zoom, mock_recall, mock_zoom,
):
    from app.services import zoom_webhooks

    zoom_webhooks.handle_meeting_started(_started_payload())

    assert mock_recall["create_bot"] == []
    assert clean_redis.get(_key("bot_id")) is None
    assert clean_redis.get(_key("doctor_id")) is not None
    assert clean_redis.get(_key("host_id")).decode() == HOST_ID


def test_doctor_identified_by_participant_id_not_name(
    clean_redis, doctor_with_zoom, mock_recall, mock_zoom,
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
    clean_redis, doctor_with_zoom, mock_recall, mock_zoom,
):
    from app.services import zoom_webhooks

    zoom_webhooks.handle_meeting_started(_started_payload())
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload(HOST_ID, "Test Doctor")
    )

    assert clean_redis.get(_key("doctor_present")) == b"1"
    # no non-doctors, so no bot created
    assert mock_recall["create_bot"] == []


def test_patient_joins_first_then_doctor_starts_session(
    clean_redis, doctor_with_zoom, mock_recall, mock_zoom,
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
    assert mock_recall["create_bot"][0]["metadata"]["meeting_id"] == MEETING_ID
    assert mock_recall["start_recording"] == [BOT_ID]
    assert clean_redis.get(_key("bot_id")).decode() == BOT_ID
    assert clean_redis.get(_key("recording_active")) == b"1"


def test_doctor_joins_first_no_patients_no_bot(
    clean_redis, doctor_with_zoom, mock_recall, mock_zoom,
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
    clean_redis, doctor_with_zoom, mock_recall, mock_zoom,
):
    from app.services import zoom_webhooks

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


def test_additional_participant_during_active_session_no_new_bot(
    clean_redis, doctor_with_zoom, mock_recall, mock_zoom,
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
