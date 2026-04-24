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
    assert int(clean_redis.get(_key("patient_count"))) == 1


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
    assert clean_redis.get(_key("recording_active")) == b"1"


def test_last_patient_leaves_closes_appointment_and_leaves_bot(
    clean_redis, doctor_with_zoom, mock_recall, mock_zoom,
):
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
    assert mock_recall["leave_call"] == [BOT_ID]
    assert clean_redis.get(_key("recording_active")) is None
    assert clean_redis.get(_key("bot_id")) is None
    assert clean_redis.llen(_key("participants_order")) == 0


def test_sequential_patients_produce_two_bots(
    clean_redis, doctor_with_zoom, mock_recall, mock_zoom,
):
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

    # Patient B enters — should create a brand-new bot
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload("patient-b", "Sofia")
    )

    assert len(mock_recall["create_bot"]) == 2
    assert mock_recall["start_recording"] == [BOT_ID, BOT_ID]
    assert mock_recall["stop_recording"] == [BOT_ID]
    assert mock_recall["leave_call"] == [BOT_ID]


def test_accompanying_family_leaves_keeps_recording(
    clean_redis, doctor_with_zoom, mock_recall, mock_zoom,
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


def test_doctor_leaves_during_recording_schedules_grace(
    clean_redis, doctor_with_zoom, mock_recall, mock_zoom, monkeypatch,
):
    scheduled = {}

    class FakeAsyncResult:
        id = "celery-task-42"

    def fake_apply_async(args, countdown):
        scheduled["args"] = args
        scheduled["countdown"] = countdown
        return FakeAsyncResult()

    from app.jobs import recording_guard
    monkeypatch.setattr(
        recording_guard.stop_recording_if_absent, "apply_async", fake_apply_async
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

    assert scheduled["args"] == (MEETING_ID,)
    assert scheduled["countdown"] == 90
    assert clean_redis.get(_key("grace_task_id")).decode() == "celery-task-42"
    # Recording is still active — grace hasn't expired
    assert clean_redis.get(_key("recording_active")) == b"1"


def test_doctor_returns_within_grace_revokes_task(
    clean_redis, doctor_with_zoom, mock_recall, mock_zoom, monkeypatch,
):
    revoked = []

    class FakeAsyncResult:
        id = "celery-task-99"

    from app.jobs import recording_guard
    monkeypatch.setattr(
        recording_guard.stop_recording_if_absent, "apply_async",
        lambda args, countdown: FakeAsyncResult(),
    )

    from app.celery import celery_app
    monkeypatch.setattr(
        celery_app.control, "revoke",
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
    # Doctor returns
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload(HOST_ID, "Test Doctor")
    )

    assert revoked == ["celery-task-99"]
    assert clean_redis.get(_key("grace_task_id")) is None
    assert clean_redis.get(_key("doctor_present")) == b"1"
    assert clean_redis.get(_key("recording_active")) == b"1"
    # Only one bot should have been created across the whole flow
    assert len(mock_recall["create_bot"]) == 1


def test_grace_period_expires_stops_and_leaves(
    clean_redis, doctor_with_zoom, mock_recall, mock_zoom, monkeypatch,
):
    """Stub apply_async so handle_participant_left only SCHEDULES
    the grace task. Then call recording_guard.perform directly — this
    is the sole driver of the assertions below, matching the 90s-later
    production scenario."""
    class FakeAsyncResult:
        id = "celery-task-grace"

    from app.jobs import recording_guard
    monkeypatch.setattr(
        recording_guard.stop_recording_if_absent, "apply_async",
        lambda args, countdown: FakeAsyncResult(),
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

    # Sanity: the webhook only scheduled — nothing has been stopped yet.
    assert mock_recall["stop_recording"] == []
    assert mock_recall["leave_call"] == []
    assert clean_redis.get(_key("recording_active")) == b"1"

    # Simulate the grace task firing after 90s with doctor still absent
    recording_guard.perform(MEETING_ID)

    assert mock_recall["stop_recording"] == [BOT_ID]
    assert mock_recall["leave_call"] == [BOT_ID]
    assert clean_redis.get(_key("recording_active")) is None
    assert clean_redis.get(_key("bot_id")) is None
    assert clean_redis.get(_key("grace_task_id")) is None


def test_meeting_ended_with_no_bot_logs_no_recording(
    clean_redis, doctor_with_zoom, mock_recall, mock_zoom,
):
    from app.services import zoom_webhooks

    zoom_webhooks.handle_meeting_started(_started_payload())
    # Doctor never joins
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload("patient-a", "Juan")
    )
    zoom_webhooks.handle_meeting_ended(_meeting_ended_payload())

    assert mock_recall["create_bot"] == []
    assert mock_recall["stop_recording"] == []
    assert mock_recall["leave_call"] == []
    # All keys cleared
    for suffix in ["doctor_id", "host_id", "participants_order", "participants_names"]:
        assert clean_redis.get(_key(suffix)) is None


def test_meeting_ended_with_active_recording_stops_and_leaves(
    clean_redis, doctor_with_zoom, mock_recall, mock_zoom,
):
    from app.services import zoom_webhooks

    zoom_webhooks.handle_meeting_started(_started_payload())
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload(HOST_ID, "Test Doctor")
    )
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload("patient-a", "Juan")
    )
    zoom_webhooks.handle_meeting_ended(_meeting_ended_payload())

    assert mock_recall["stop_recording"] == [BOT_ID]
    assert mock_recall["leave_call"] == [BOT_ID]
    # All keys cleared
    assert clean_redis.get(_key("bot_id")) is None
    assert clean_redis.get(_key("recording_active")) is None


def test_meeting_ended_revokes_pending_grace_task(
    clean_redis, doctor_with_zoom, mock_recall, mock_zoom, monkeypatch,
):
    revoked = []

    class FakeAsyncResult:
        id = "celery-task-77"

    from app.jobs import recording_guard
    monkeypatch.setattr(
        recording_guard.stop_recording_if_absent, "apply_async",
        lambda args, countdown: FakeAsyncResult(),
    )

    from app.celery import celery_app
    monkeypatch.setattr(
        celery_app.control, "revoke",
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
    zoom_webhooks.handle_meeting_ended(_meeting_ended_payload())

    assert "celery-task-77" in revoked
