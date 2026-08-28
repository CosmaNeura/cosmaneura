import json
from datetime import datetime, timedelta, timezone
from unittest.mock import ANY, patch
from zoneinfo import ZoneInfo

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from structlog.testing import capture_logs

from app.configuration.redis import get_redis
from app.server import api_app
from app.services.external_webhook_inbox import digest_provider_reference

from app.models.ai_visit_transcript import AIVisitTranscript, TranscriptEntry
from app.models.appointment import Appointment
from app.models.doctor import Doctor
from app.models.doctor_video_integration import DoctorVideoIntegration, VideoProvider
from app.models.superadmin_capability import (
    SuperadminCapabilityEnvironment,
    SuperadminCapabilityKind,
)
from app.models.zoom_meeting import ZoomMeeting, ZoomMeetingStatus

from tests.factories import AppointmentFactory, DoctorFactory
from tests.routes.utils import (
    MOCK_CLERK_ISSUER,
    get_clerk_dev_user,
    grant_test_superadmin_capability,
    provision_practice_in_force,
)

CLERK_DEV_USER_EMAIL = "dev@test.com"


def _get_or_create_doctor():
    """Get the dev doctor, ensuring email is set to avoid Clerk API calls."""
    _, _, clerk_user = get_clerk_dev_user()
    doctor = Doctor.find_or_create_by(clerk_id=clerk_user.id)
    if not doctor.email:
        doctor.email = CLERK_DEV_USER_EMAIL
        doctor.save()
    return doctor


def _create_zoom_integration(doctor):
    return DoctorVideoIntegration(
        doctor_id=doctor.id,
        provider=VideoProvider.zoom,
        provider_user_id="zoom_user_abc",
        access_token="access_token",
        refresh_token="refresh_token",
        token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    ).save()


def _make_meeting(doctor, zoom_meeting_id="111222333", **kwargs):
    defaults: dict = dict(
        doctor_id=doctor.id,
        zoom_meeting_id=zoom_meeting_id,
        topic="Test Meeting",
        start_time=datetime.now(timezone.utc) + timedelta(hours=1),
        duration=30,
        join_url="https://zoom.us/j/111222333",
    )
    defaults.update(kwargs)
    return ZoomMeeting(**defaults).save()


def test_schedule_list_no_zoom(authenticated_client: TestClient):
    """Doctor with no zoom integration returns zoom_connected=False and empty meetings; sync not called."""
    _get_or_create_doctor()

    with patch("app.routes.internal.sync_zoom_meetings") as mock_sync:
        response = authenticated_client.get(api_app.url_path_for("schedule_list"))

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["zoom_connected"] is False
    assert data["meetings"] == []
    mock_sync.queue.assert_not_called()


def test_schedule_list_with_zoom(authenticated_client: TestClient):
    """Doctor with zoom integration: sync is queued and meetings are returned."""
    doctor = _get_or_create_doctor()
    _create_zoom_integration(doctor)
    _make_meeting(doctor)

    with patch("app.routes.internal.sync_zoom_meetings") as mock_sync:
        response = authenticated_client.get(api_app.url_path_for("schedule_list"))

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["zoom_connected"] is True
    assert data["capture_available"] is True
    assert len(data["meetings"]) == 1
    assert data["meetings"][0]["join_url"] == "https://zoom.us/j/111222333"
    mock_sync.queue.assert_called_once_with(str(doctor.id))


def test_schedule_list_redacts_target_join_url_during_view_as(
    authenticated_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    def staging_environment():
        return SuperadminCapabilityEnvironment.staging

    for path in (
        "app.routes.dependencies.view_as.configured_superadmin_capability_environment",
        "app.routes.view_as.configured_superadmin_capability_environment",
        "app.services.superadmin_capability.configured_superadmin_capability_environment",
    ):
        monkeypatch.setattr(path, staging_environment)
    monkeypatch.setattr(
        "app.services.superadmin_capability.configured_superadmin_clerk_issuer",
        lambda: MOCK_CLERK_ISSUER,
    )

    def current_email(clerk_id: str) -> str | None:
        doctor = Doctor.get(clerk_id=clerk_id)
        return doctor.email.strip().lower() if doctor and doctor.email else None

    monkeypatch.setattr("app.services.view_as.verified_primary_email", current_email)

    actor = _get_or_create_doctor()
    grant_test_superadmin_capability(actor, SuperadminCapabilityKind.view_as)
    target = Doctor(
        clerk_id="user_schedule_view_as_target",
        email="schedule-view-as-target@example.net",
        biography="",
    ).save()
    provision_practice_in_force(target)
    _create_zoom_integration(target)
    meeting = _make_meeting(target)

    began = authenticated_client.post(
        api_app.url_path_for("begin_view_as"),
        json={"target_doctor_id": str(target.id)},
    )
    assert began.status_code == status.HTTP_200_OK, began.text
    authenticated_client.headers["X-Cosmaneura-View-As-Binding"] = began.json()[
        "request_binding"
    ]

    with patch("app.routes.internal.sync_zoom_meetings") as mock_sync:
        response = authenticated_client.get(api_app.url_path_for("schedule_list"))

    assert response.status_code == status.HTTP_200_OK, response.text
    assert response.json()["capture_available"] is False
    assert response.json()["meetings"] == [
        {
            "id": str(meeting.id),
            "zoom_meeting_id": "111222333",
            "topic": "Test Meeting",
            "start_time": response.json()["meetings"][0]["start_time"],
            "duration": 30,
            "join_url": None,
            "status": "scheduled",
            "appointment_id": None,
            "capture_authorized": False,
        }
    ]
    mock_sync.queue.assert_not_called()


def test_schedule_list_excludes_cancelled(authenticated_client: TestClient):
    """Cancelled meetings are excluded from results."""
    doctor = _get_or_create_doctor()
    _create_zoom_integration(doctor)

    _make_meeting(doctor, zoom_meeting_id="111222333")
    _make_meeting(
        doctor, zoom_meeting_id="999888777", status=ZoomMeetingStatus.cancelled
    )

    with patch("app.routes.internal.sync_zoom_meetings"):
        response = authenticated_client.get(api_app.url_path_for("schedule_list"))

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["zoom_connected"] is True
    assert len(data["meetings"]) == 1
    assert data["meetings"][0]["zoom_meeting_id"] == "111222333"


def test_schedule_cancel(authenticated_client: TestClient):
    """Cancelling a meeting calls zoom_service.delete_meeting and sets status to cancelled."""
    doctor = _get_or_create_doctor()
    _create_zoom_integration(doctor)
    _make_meeting(doctor, zoom_meeting_id="111222333")

    with patch("app.routes.internal.zoom_service.delete_meeting") as mock_delete:
        response = authenticated_client.delete(
            api_app.url_path_for("cancel_meeting", zoom_meeting_id="111222333")
        )

    assert response.status_code == status.HTTP_204_NO_CONTENT
    mock_delete.assert_called_once_with(ANY, "111222333")

    updated = ZoomMeeting.get(doctor_id=doctor.id, zoom_meeting_id="111222333")
    assert updated is not None
    assert updated.status == ZoomMeetingStatus.cancelled


def test_schedule_cancel_not_found(authenticated_client: TestClient):
    """Cancelling a non-existent meeting returns 404."""
    _get_or_create_doctor()

    with patch("app.routes.internal.zoom_service.delete_meeting"):
        response = authenticated_client.delete(
            api_app.url_path_for("cancel_meeting", zoom_meeting_id="000000000")
        )

    assert response.status_code == status.HTTP_404_NOT_FOUND


def test_authorize_zoom_capture_binds_existing_appointment_without_creating_rows(
    authenticated_client: TestClient,
):
    doctor = _get_or_create_doctor()
    _create_zoom_integration(doctor)
    meeting = _make_meeting(doctor)
    appointment = AppointmentFactory.build(doctor_id=doctor.id).save()
    appointment_count = Appointment.where(Appointment.doctor_id == doctor.id).count()

    pending_key = (
        f"recall:meeting:{meeting.zoom_meeting_id}:capture_authorization_pending"
    )
    try:
        with (
            patch("app.routes.internal.start_zoom_capture_job") as mock_start,
            capture_logs() as logs,
        ):
            response = authenticated_client.post(
                api_app.url_path_for(
                    "authorize_zoom_capture", zoom_meeting_id=meeting.zoom_meeting_id
                ),
                json={"appointment_id": str(appointment.id)},
            )

        assert response.status_code == status.HTTP_200_OK, response.text
        assert response.json()["state"] == "pending"
        mock_start.queue.assert_not_called()

        persisted = ZoomMeeting.one(meeting.id)
        assert persisted.appointment_id == appointment.id
        assert persisted.capture_authorization_occurrence_digest is None
        assert (
            Appointment.where(Appointment.doctor_id == doctor.id).count()
            == appointment_count
        )
        assert get_redis().get(pending_key) is not None
        serialized_logs = json.dumps(logs, sort_keys=True)
        for private_value in (
            str(doctor.id),
            meeting.zoom_meeting_id,
            str(appointment.id),
            meeting.join_url,
        ):
            assert private_value not in serialized_logs
        assert "hmac:" in serialized_logs
    finally:
        get_redis().delete(pending_key)


def test_authorize_zoom_capture_arms_active_occurrence_and_queues_retry(
    authenticated_client: TestClient,
):
    doctor = _get_or_create_doctor()
    _create_zoom_integration(doctor)
    meeting = _make_meeting(
        doctor,
        status=ZoomMeetingStatus.started,
        start_time=datetime.now(timezone.utc),
    )
    appointment = AppointmentFactory.build(doctor_id=doctor.id).save()
    occurrence_digest = digest_provider_reference("zoom-occurrence:active")
    redis = get_redis()
    doctor_key = f"recall:meeting:{meeting.zoom_meeting_id}:doctor_id"
    occurrence_key = f"recall:meeting:{meeting.zoom_meeting_id}:occurrence_digest"
    redis.set(doctor_key, str(doctor.id))
    redis.set(occurrence_key, occurrence_digest)

    try:
        before_authorize = datetime.now(timezone.utc)
        with patch("app.routes.internal.start_zoom_capture_job") as mock_start:
            response = authenticated_client.post(
                api_app.url_path_for(
                    "authorize_zoom_capture",
                    zoom_meeting_id=meeting.zoom_meeting_id,
                ),
                json={
                    "appointment_id": str(appointment.id),
                },
            )

        assert response.status_code == status.HTTP_200_OK, response.text
        assert response.json()["state"] == "armed"
        mock_start.queue.assert_called_once_with(str(meeting.id))
        persisted = ZoomMeeting.one(meeting.id)
        assert persisted.capture_authorization_occurrence_digest == occurrence_digest
        assert persisted.capture_authorization_expires_at is not None
        assert (
            before_authorize + timedelta(minutes=59)
            < persisted.capture_authorization_expires_at
            < datetime.now(timezone.utc) + timedelta(minutes=61)
        )
        assert persisted.capture_authorization_consumed_at is None

        from app.services.zoom_webhooks import _consume_bound_appointment

        consumed = _consume_bound_appointment(
            str(doctor.id),
            meeting.zoom_meeting_id,
            occurrence_digest=occurrence_digest,
        )
        assert consumed is not None
        assert consumed.id == appointment.id
        assert ZoomMeeting.one(meeting.id).capture_authorization_consumed_at is not None
    finally:
        redis.delete(doctor_key, occurrence_key)


def test_two_authorize_clicks_cannot_rebind_one_occurrence_to_another_appointment(
    authenticated_client: TestClient,
):
    doctor = _get_or_create_doctor()
    _create_zoom_integration(doctor)
    meeting = _make_meeting(
        doctor,
        status=ZoomMeetingStatus.started,
        start_time=datetime.now(timezone.utc),
    )
    first = AppointmentFactory.build(doctor_id=doctor.id).save()
    second = AppointmentFactory.build(doctor_id=doctor.id).save()
    occurrence_digest = digest_provider_reference("zoom-occurrence:active")
    redis = get_redis()
    doctor_key = f"recall:meeting:{meeting.zoom_meeting_id}:doctor_id"
    occurrence_key = f"recall:meeting:{meeting.zoom_meeting_id}:occurrence_digest"
    redis.set(doctor_key, str(doctor.id))
    redis.set(occurrence_key, occurrence_digest)

    try:
        route = api_app.url_path_for(
            "authorize_zoom_capture", zoom_meeting_id=meeting.zoom_meeting_id
        )
        with patch("app.routes.internal.start_zoom_capture_job"):
            first_response = authenticated_client.post(
                route,
                json={"appointment_id": str(first.id)},
            )
            second_response = authenticated_client.post(
                route,
                json={"appointment_id": str(second.id)},
            )

        assert first_response.status_code == status.HTTP_200_OK
        assert second_response.status_code == status.HTTP_409_CONFLICT
        persisted = ZoomMeeting.one(meeting.id)
        assert persisted.appointment_id == first.id
        assert persisted.capture_authorization_occurrence_digest == occurrence_digest
    finally:
        redis.delete(doctor_key, occurrence_key)


def test_started_meeting_without_signed_occurrence_fails_closed(
    authenticated_client: TestClient,
):
    doctor = _get_or_create_doctor()
    _create_zoom_integration(doctor)
    meeting = _make_meeting(
        doctor,
        status=ZoomMeetingStatus.started,
        start_time=datetime.now(timezone.utc),
    )
    appointment = AppointmentFactory.build(doctor_id=doctor.id).save()

    response = authenticated_client.post(
        api_app.url_path_for(
            "authorize_zoom_capture",
            zoom_meeting_id=meeting.zoom_meeting_id,
        ),
        json={"appointment_id": str(appointment.id)},
    )

    assert response.status_code == status.HTTP_409_CONFLICT
    persisted = ZoomMeeting.one(meeting.id)
    assert persisted.appointment_id is None
    assert persisted.capture_authorization_occurrence_digest is None


def test_pending_authorization_ttl_uses_absolute_time_across_dst_fall_back():
    from app.services.zoom_capture_authorization import _pending_ttl

    eastern = ZoneInfo("America/New_York")
    meeting = ZoomMeeting(
        doctor_id=DoctorFactory.build().id,
        zoom_meeting_id="dst-test",
        topic="DST test",
        start_time=datetime(2026, 11, 1, 1, 30, tzinfo=eastern, fold=0),
        duration=30,
        join_url="https://zoom.us/j/dst-test",
    )

    assert _pending_ttl(
        meeting,
        now=datetime(2026, 11, 1, 5, 30, tzinfo=timezone.utc),
    ) == timedelta(hours=1)


def test_authorize_zoom_capture_rejects_cross_tenant_appointment(
    authenticated_client: TestClient,
):
    doctor = _get_or_create_doctor()
    _create_zoom_integration(doctor)
    meeting = _make_meeting(doctor)
    other_doctor = DoctorFactory.build().save()
    other_appointment = AppointmentFactory.build(doctor_id=other_doctor.id).save()

    response = authenticated_client.post(
        api_app.url_path_for(
            "authorize_zoom_capture", zoom_meeting_id=meeting.zoom_meeting_id
        ),
        json={"appointment_id": str(other_appointment.id)},
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert ZoomMeeting.one(meeting.id).appointment_id is None


def test_authorize_zoom_capture_rejects_completed_appointment(
    authenticated_client: TestClient,
):
    doctor = _get_or_create_doctor()
    _create_zoom_integration(doctor)
    meeting = _make_meeting(doctor)
    appointment = AppointmentFactory.build(doctor_id=doctor.id).save()
    AIVisitTranscript(
        doctor_id=doctor.id,
        appointment_id=appointment.id,
        patient_id=appointment.patient_id,
        duration=1,
        transcript_data=[
            TranscriptEntry(
                speaker="clinician",
                content="Synthetic test transcript.",
                startTime=0,
                endTime=1,
            )
        ],
    ).save()

    response = authenticated_client.post(
        api_app.url_path_for(
            "authorize_zoom_capture", zoom_meeting_id=meeting.zoom_meeting_id
        ),
        json={"appointment_id": str(appointment.id)},
    )

    assert response.status_code == status.HTTP_409_CONFLICT
    assert ZoomMeeting.one(meeting.id).appointment_id is None


def test_authorize_zoom_capture_rejects_meeting_without_join_url(
    authenticated_client: TestClient,
):
    doctor = _get_or_create_doctor()
    _create_zoom_integration(doctor)
    meeting = _make_meeting(doctor, join_url="")
    appointment = AppointmentFactory.build(doctor_id=doctor.id).save()

    response = authenticated_client.post(
        api_app.url_path_for(
            "authorize_zoom_capture", zoom_meeting_id=meeting.zoom_meeting_id
        ),
        json={"appointment_id": str(appointment.id)},
    )

    assert response.status_code == status.HTTP_409_CONFLICT
    assert ZoomMeeting.one(meeting.id).appointment_id is None


def test_authorize_zoom_capture_rejects_non_allowlisted_production_doctor(
    authenticated_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    from app.services import zoom_webhooks

    doctor = _get_or_create_doctor()
    doctor.email = "outside-pilot@example.com"
    doctor.save()
    _create_zoom_integration(doctor)
    meeting = _make_meeting(doctor)
    appointment = AppointmentFactory.build(doctor_id=doctor.id).save()
    monkeypatch.setattr(zoom_webhooks, "is_staging", lambda: False)
    monkeypatch.setattr(zoom_webhooks, "is_production", lambda: True)

    with patch("app.routes.internal.sync_zoom_meetings"):
        assert (
            authenticated_client.get(api_app.url_path_for("schedule_list")).json()[
                "capture_available"
            ]
            is False
        )

    response = authenticated_client.post(
        api_app.url_path_for(
            "authorize_zoom_capture", zoom_meeting_id=meeting.zoom_meeting_id
        ),
        json={"appointment_id": str(appointment.id)},
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert ZoomMeeting.one(meeting.id).appointment_id is None
