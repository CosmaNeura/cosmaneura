import json
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

from structlog.testing import capture_logs

from app.jobs.sync_scheduling_provider import perform
from app.services.scheduling_acuity import AcuityIntegrationInvalid
from app.services.scheduling_provider import NormalizedAppointmentEvent, resolve_event

from app.models.ambiguous_patient_match import AmbiguousPatientMatch
from app.models.appointment import Appointment
from app.models.patient_external_identity import SchedulingProviderSource

from tests.factories import DoctorFactory
from tests.routes.utils import provision_practice_in_force


def _authorized_doctor():
    doctor = DoctorFactory.build().save()
    provision_practice_in_force(doctor)
    return doctor


def _event(**overrides) -> NormalizedAppointmentEvent:
    base = NormalizedAppointmentEvent(
        source=SchedulingProviderSource.acuity,
        external_appointment_ref="acuity-appt-1",
        external_patient_ref=None,
        doctor_external_calendar_ref="calendar-1",
        start_at=datetime.now(timezone.utc) + timedelta(days=1),
        end_at=datetime.now(timezone.utc) + timedelta(days=1, hours=1),
        patient_name="Jane Cosmo",
        patient_email="jane.cosmo@example.com",
        patient_phone="4061234567",
    )
    return base.model_copy(update=overrides)


def _fake_provider(events):
    provider = Mock()
    provider.fetch_upcoming = Mock(return_value=events)
    return provider


def test_sync_matches_and_queues_events(global_session):
    doctor = _authorized_doctor()
    events = [
        _event(external_appointment_ref="appt-1"),
        _event(
            external_appointment_ref="appt-2",
            patient_name="John Other",
            patient_email="john.other@example.com",
        ),
    ]

    with (
        patch(
            "app.jobs.sync_scheduling_provider.build_scheduling_provider",
            return_value=_fake_provider(events),
        ),
        capture_logs() as logs,
    ):
        perform(str(doctor.id), "acuity")

    assert (
        AmbiguousPatientMatch.where(
            AmbiguousPatientMatch.doctor_id == doctor.id
        ).count()
        == 2
    )
    assert Appointment.where(Appointment.doctor_id == doctor.id).count() == 0

    serialized = json.dumps(logs, sort_keys=True)
    for private_value in (
        str(doctor.id),
        "Jane Cosmo",
        "jane.cosmo@example.com",
        "John Other",
        "john.other@example.com",
    ):
        assert private_value not in serialized
    assert "hmac:" in serialized
    assert any(entry.get("event_code") == "scheduling_sync_completed" for entry in logs)


def test_sync_skips_when_access_not_in_force():
    doctor = DoctorFactory.build().save()

    with patch(
        "app.jobs.sync_scheduling_provider.build_scheduling_provider"
    ) as build:
        perform(str(doctor.id), "acuity")
        build.assert_not_called()


def test_sync_skips_when_integration_missing(global_session):
    doctor = _authorized_doctor()

    with patch(
        "app.jobs.sync_scheduling_provider.build_scheduling_provider",
        return_value=None,
    ):
        perform(str(doctor.id), "acuity")

    assert (
        AmbiguousPatientMatch.where(
            AmbiguousPatientMatch.doctor_id == doctor.id
        ).count()
        == 0
    )


def test_sync_stops_quietly_on_integration_invalid(global_session):
    doctor = _authorized_doctor()
    provider = Mock()
    provider.fetch_upcoming = Mock(side_effect=AcuityIntegrationInvalid("revoked"))

    with (
        patch(
            "app.jobs.sync_scheduling_provider.build_scheduling_provider",
            return_value=provider,
        ),
        capture_logs() as logs,
    ):
        perform(str(doctor.id), "acuity")

    assert any(
        entry.get("event_code") == "scheduling_sync_integration_invalid"
        for entry in logs
    )


def test_sync_continues_past_one_malformed_event(global_session):
    """One event failing resolve_event must not drop the rest of the sync —
    the same truncation bug sync_zoom_meetings.py's own tests guard against."""
    doctor = _authorized_doctor()
    good_event = _event(external_appointment_ref="appt-good")
    bad_event = _event(external_appointment_ref="appt-bad")

    def fake_resolve(doctor_arg, event):
        if event.external_appointment_ref == "appt-bad":
            raise ValueError("boom")
        return resolve_event(doctor_arg, event)

    with (
        patch(
            "app.jobs.sync_scheduling_provider.build_scheduling_provider",
            return_value=_fake_provider([good_event, bad_event]),
        ),
        patch(
            "app.jobs.sync_scheduling_provider.resolve_event",
            side_effect=fake_resolve,
        ),
        capture_logs() as logs,
    ):
        perform(str(doctor.id), "acuity")

    assert (
        AmbiguousPatientMatch.where(
            AmbiguousPatientMatch.doctor_id == doctor.id
        ).count()
        == 1
    )
    assert any(
        entry.get("event_code") == "scheduling_event_skipped_malformed"
        for entry in logs
    )
