from datetime import datetime, timedelta, timezone
from typing import cast

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
        "bot_id",
        "doctor_id",
        "host_id",
        "doctor_present",
        "recording_active",
        "participants_order",
        "participants_names",
        "patient_count",
        "starting_lock",
        "merge_task_id",
        "paused_at",
    ]
    for s in suffixes:
        redis.delete(_key(s))
    for key in cast(list[str | bytes], redis.keys("recall:zak_url_token:*")):
        redis.delete(key)
    yield redis
    for s in suffixes:
        redis.delete(_key(s))
    for key in cast(list[str | bytes], redis.keys("recall:zak_url_token:*")):
        redis.delete(key)


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


def _started_payload() -> dict:
    return {
        "payload": {
            "object": {
                "id": MEETING_ID,
                "host_id": HOST_ID,
            }
        }
    }


def _participant_joined_payload(
    participant_id: str,
    user_name: str,
    *,
    user_id: str | None = None,
) -> dict:
    participant: dict = {"id": participant_id, "user_name": user_name}
    if user_id is not None:
        participant["user_id"] = user_id
    return {
        "payload": {
            "object": {
                "id": MEETING_ID,
                "participant": participant,
            }
        }
    }


def _participant_left_payload(
    participant_id: str,
    user_name: str,
    *,
    user_id: str | None = None,
) -> dict:
    return _participant_joined_payload(participant_id, user_name, user_id=user_id)


def _meeting_ended_payload() -> dict:
    return {"payload": {"object": {"id": MEETING_ID}}}


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


def test_doctor_recognized_when_host_id_not_yet_in_redis(
    clean_redis,
    doctor_with_zoom,
    mock_recall,
    mock_zoom,
):
    """participant_joined can be handled before meeting.started has written
    host_id — they are concurrent celery tasks. The host must still be
    classified as the doctor, resolved from the DB."""
    from app.services import zoom_webhooks

    assert clean_redis.get(_key("host_id")) is None

    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload(HOST_ID, "Test Doctor")
    )

    assert clean_redis.get(_key("doctor_present")) == b"1"
    # The doctor must never be queued as a patient.
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
    assert clean_redis.llen(_key("participants_order")) == 0
    operation = ExternalWebhookOperation.get(
        provider="zoom",
        provider_resource_digest=digest,
    )
    assert operation is not None
    assert operation.state == "reserved"
    assert operation.execution_lease_expires_at is None

    clean_redis.set(_key("doctor_id"), str(doctor_with_zoom.id))
    clean_redis.set(_key("host_id"), HOST_ID)
    zoom_webhooks.dispatch(
        event_type,
        payload,
        provider_event_digest=digest,
    )

    assert clean_redis.llen(_key("participants_order")) == 1
    operation = ExternalWebhookOperation.get(id=operation.id)
    assert operation is not None
    assert operation.state == "succeeded"


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
    zak_url = mock_recall["create_bot"][0]["zak_url"]
    zak_token = zak_url.rsplit("/", maxsplit=1)[-1]
    zak_ttl = clean_redis.ttl(zoom_webhooks.zak_token_redis_key(zak_token))
    assert 0 < zak_ttl <= zoom_webhooks._ZAK_URL_TOKEN_TTL_SECONDS <= 5 * 60


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
    scheduled. The bot is kept connected so the next patient can resume
    on the same bot inside the merge window."""

    class FakeAsyncResult:
        id = "celery-task-mw"

    from app.jobs import merge_window

    monkeypatch.setattr(
        merge_window.expire,
        "apply_async",
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
    # Now recording is active
    zoom_webhooks.handle_participant_left(
        _participant_left_payload("patient-a", "Juan")
    )

    assert mock_recall["stop_recording"] == [BOT_ID]
    assert mock_recall["leave_call"] == []
    assert clean_redis.get(_key("recording_active")) is None
    # bot is preserved for potential resume within the merge window
    assert clean_redis.get(_key("bot_id")).decode() == BOT_ID
    assert clean_redis.get(_key("merge_task_id")).decode() == "celery-task-mw"
    assert clean_redis.llen(_key("participants_order")) == 0


def test_sequential_patients_within_merge_window_share_bot(
    clean_redis,
    doctor_with_zoom,
    mock_recall,
    mock_zoom,
    monkeypatch,
):
    """Back-to-back patients (Mode A) within the 10-min merge window must
    reuse the same bot via start_recording; we do NOT create a new bot or
    leave_call between them. The merge_window task scheduled when the last
    patient left is revoked when the next patient arrives."""
    revoked = []

    class FakeAsyncResult:
        id = "celery-task-mw"

    from app.jobs import merge_window

    monkeypatch.setattr(
        merge_window.expire,
        "apply_async",
        lambda args, countdown: FakeAsyncResult(),
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

    # Patient B enters within the merge window — same bot resumes
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload("patient-b", "Sofia")
    )

    assert len(mock_recall["create_bot"]) == 1
    assert mock_recall["start_recording"] == [BOT_ID, BOT_ID]
    assert mock_recall["stop_recording"] == [BOT_ID]
    assert mock_recall["leave_call"] == []
    assert "celery-task-mw" in revoked
    assert clean_redis.get(_key("merge_task_id")) is None
    assert clean_redis.get(_key("recording_active")) == b"1"
    assert clean_redis.get(_key("bot_id")).decode() == BOT_ID


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
    arms a 10-min merge_window task. bot_id is preserved so doctor can
    resume on the same bot if they return within the window."""
    scheduled = {}

    class FakeAsyncResult:
        id = "celery-task-42"

    def fake_apply_async(args, countdown):
        scheduled["args"] = args
        scheduled["countdown"] = countdown
        return FakeAsyncResult()

    from app.jobs import merge_window

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

    assert scheduled["args"] == (MEETING_ID,)
    assert scheduled["countdown"] == 600
    assert clean_redis.get(_key("merge_task_id")).decode() == "celery-task-42"
    # Recording is paused, not active
    assert clean_redis.get(_key("recording_active")) is None
    # stop_recording fired but leave_call did not — bot stays connected
    assert mock_recall["stop_recording"] == [BOT_ID]
    assert mock_recall["leave_call"] == []
    assert clean_redis.get(_key("bot_id")).decode() == BOT_ID
    assert clean_redis.get(_key("paused_at")) is not None


def test_doctor_returns_within_merge_window_resumes_same_bot(
    clean_redis,
    doctor_with_zoom,
    mock_recall,
    mock_zoom,
    monkeypatch,
):
    """Doctor leaves with a patient still in the room, then returns within
    the merge window. The pending merge_window task is revoked and
    start_recording is issued on the SAME bot (no new create_bot)."""
    revoked = []

    class FakeAsyncResult:
        id = "celery-task-99"

    from app.jobs import merge_window

    monkeypatch.setattr(
        merge_window.expire,
        "apply_async",
        lambda args, countdown: FakeAsyncResult(),
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
    # Doctor returns; patient is still present so we resume
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload(HOST_ID, "Test Doctor")
    )

    assert "celery-task-99" in revoked
    assert clean_redis.get(_key("merge_task_id")) is None
    assert clean_redis.get(_key("doctor_present")) == b"1"
    assert clean_redis.get(_key("recording_active")) == b"1"
    # Only one create_bot across the whole flow; two start_recording calls
    # (initial + resume) on the same bot.
    assert len(mock_recall["create_bot"]) == 1
    assert mock_recall["start_recording"] == [BOT_ID, BOT_ID]
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
        lambda args, countdown: FakeAsyncResult(),
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

    # Pause-state sanity: stop_recording fired but leave_call did not.
    assert mock_recall["stop_recording"] == [BOT_ID]
    assert mock_recall["leave_call"] == []
    assert clean_redis.get(_key("recording_active")) is None
    assert clean_redis.get(_key("bot_id")).decode() == BOT_ID

    # Simulate the merge_window task firing after 10 min with no resume
    merge_window.perform(MEETING_ID)

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


def test_stale_meeting_ended_within_lock_window_is_skipped(
    clean_redis,
    doctor_with_zoom,
    mock_recall,
    mock_zoom,
):
    """Regression: Zoom occasionally redelivers meeting.ended from a prior
    PMR session within seconds of a fresh meeting.started. If we let it
    through, host_id is wiped and the doctor arriving moments later is
    misclassified as 'other' — no bot ever spawns. The starting_lock set
    by meeting.started is the signal we use to ignore those stale ends.
    """
    from app.services import zoom_webhooks

    zoom_webhooks.handle_meeting_started(_started_payload())
    # Lock is still active (60s TTL) → simulates a stale ended arriving
    # ~3s after a fresh started, which is what we observed in prod.
    zoom_webhooks.handle_meeting_ended(_meeting_ended_payload())

    # State must survive — host_id intact so the doctor is recognized.
    assert clean_redis.get(_key("host_id")) is not None
    assert clean_redis.get(_key("doctor_id")) is not None
    assert clean_redis.get(_key("starting_lock")) is not None
    assert mock_recall["stop_recording"] == []
    assert mock_recall["leave_call"] == []


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
        lambda args, countdown: FakeAsyncResult(),
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


def test_matcher_skips_appointment_that_already_has_a_transcript(
    clean_redis,
    doctor_with_zoom,
    mock_recall,
    mock_zoom,
):
    """Phase 1 band-aid for Mode A scenario #9 (back-to-back patients
    across the merge window): an existing appointment that already has
    a transcript must NOT be matched again. A fresh appointment is
    created from the patient's display name instead."""
    from datetime import datetime as _dt
    from datetime import timedelta as _td

    from app.services import zoom_webhooks

    from app.models.ai_visit_transcript import AIVisitTranscript, TranscriptEntry
    from app.models.appointment import Appointment
    from app.models.patient import Patient

    # Stale appointment from earlier in the day, already transcribed.
    old_patient = Patient.find_or_create_by(
        doctor_id=doctor_with_zoom.id,
        name="Stale Patient",
        date_of_birth=_dt(1990, 1, 1),
    )
    old_appt = Appointment.find_or_create_by(
        doctor_id=doctor_with_zoom.id,
        patient_id=old_patient.id,
        appointment_date=_dt.utcnow() - _td(minutes=30),
        created_by_system="recall",
    )
    AIVisitTranscript(
        doctor_id=doctor_with_zoom.id,
        appointment_id=old_appt.id,
        patient_id=old_patient.id,
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

    # A bot is created for the new patient
    assert len(mock_recall["create_bot"]) == 1
    # The recording is linked to a DIFFERENT appointment than the stale one
    new_appointment_id = clean_redis.get(f"recall:bot:{BOT_ID}:appointment_id").decode()
    assert new_appointment_id != str(old_appt.id)
    # And the new appointment's patient should be the one who actually joined
    new_appt = Appointment.one(new_appointment_id)
    assert new_appt.patient_id != old_patient.id
    new_patient = Patient.one(new_appt.patient_id)
    assert new_patient.name == "Sofia Lopez"


def test_matcher_still_binds_when_appointment_has_no_transcript(
    clean_redis,
    doctor_with_zoom,
    mock_recall,
    mock_zoom,
):
    """An existing appointment WITHOUT any transcript is still a valid
    match — that's the calendar-pre-creates-appointment case."""
    from datetime import datetime as _dt
    from datetime import timedelta as _td

    from app.services import zoom_webhooks

    from app.models.appointment import Appointment
    from app.models.patient import Patient

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
        f"recall:bot:{BOT_ID}:appointment_id"
    ).decode()
    assert bound_appointment_id == str(pre_existing.id)


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
    return doctor


def test_staging_blocks_bot_for_non_allowlisted_doctor(
    clean_redis,
    mock_recall,
    mock_zoom,
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
    monkeypatch,
):
    """In production, an email NOT on the prod allowlist never gets a bot."""
    from app.services import zoom_webhooks

    _doctor_with_zoom_email("random.doctor@clinic.com")
    monkeypatch.setattr(zoom_webhooks, "is_production", lambda: True, raising=False)

    _run_meeting_flow(zoom_webhooks)

    assert mock_recall["create_bot"] == []
    assert clean_redis.get(_key("bot_id")) is None


def test_prod_blocks_staging_test_account(
    clean_redis,
    mock_recall,
    mock_zoom,
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
    assert entry["has_uuid"] is False
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
