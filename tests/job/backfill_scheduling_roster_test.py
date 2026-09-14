import json
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

from structlog.testing import capture_logs

from app.jobs.backfill_scheduling_roster import _MAX_HISTORICAL_PAGES, perform
from app.services.scheduling_acuity import AcuityIntegrationInvalid
from app.services.scheduling_provider import NormalizedAppointmentEvent, resolve_event

from app.models.ambiguous_patient_match import AmbiguousPatientMatch
from app.models.doctor_scheduling_integration import DoctorSchedulingIntegration
from app.models.patient_external_identity import SchedulingProviderSource

from tests.factories import DoctorFactory
from tests.routes.utils import provision_practice_in_force


def _authorized_doctor():
    doctor = DoctorFactory.build().save()
    provision_practice_in_force(doctor)
    return doctor


def _integration(
    doctor,
    *,
    source: SchedulingProviderSource = SchedulingProviderSource.acuity,
    last_backfilled_at: datetime | None = None,
) -> DoctorSchedulingIntegration:
    return DoctorSchedulingIntegration(
        doctor_id=doctor.id,
        source=source,
        provider_calendar_ref="calendar-1",
        access_token="access-token",
        last_backfilled_at=last_backfilled_at,
    ).save()


def _event(**overrides) -> NormalizedAppointmentEvent:
    base = NormalizedAppointmentEvent(
        source=SchedulingProviderSource.acuity,
        external_appointment_ref="acuity-appt-1",
        external_patient_ref=None,
        doctor_external_calendar_ref="calendar-1",
        start_at=datetime.now(timezone.utc) - timedelta(days=30),
        end_at=datetime.now(timezone.utc) - timedelta(days=30) + timedelta(hours=1),
        patient_name="Jane Cosmo",
        patient_email="jane.cosmo@example.com",
        patient_phone="4061234567",
    )
    return base.model_copy(update=overrides)


def _fake_provider(pages):
    """pages: list of (events, next_cursor) tuples, or exceptions, returned in order."""
    provider = Mock()
    provider.fetch_historical = Mock(side_effect=pages)
    return provider


def test_backfill_imports_all_pages_and_marks_completed(global_session):
    doctor = _authorized_doctor()
    _integration(doctor)
    page_one = ([_event(external_appointment_ref="appt-1")], "2026-08-01")
    page_two = ([_event(external_appointment_ref="appt-2")], None)

    with (
        patch(
            "app.jobs.backfill_scheduling_roster.build_scheduling_provider",
            return_value=_fake_provider([page_one, page_two]),
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
    integration = DoctorSchedulingIntegration.get(
        doctor_id=doctor.id, source=SchedulingProviderSource.acuity
    )
    assert integration is not None
    assert integration.last_backfilled_at is not None

    serialized = json.dumps(logs, sort_keys=True)
    for private_value in (str(doctor.id), "Jane Cosmo", "jane.cosmo@example.com"):
        assert private_value not in serialized
    assert "hmac:" in serialized
    assert any(
        entry.get("event_code") == "scheduling_backfill_completed" for entry in logs
    )


def test_backfill_skips_if_already_completed(global_session):
    doctor = _authorized_doctor()
    _integration(
        doctor, last_backfilled_at=datetime.now(timezone.utc) - timedelta(days=1)
    )
    provider = _fake_provider([])

    with (
        patch(
            "app.jobs.backfill_scheduling_roster.build_scheduling_provider",
            return_value=provider,
        ),
        capture_logs() as logs,
    ):
        perform(str(doctor.id), "acuity")

    provider.fetch_historical.assert_not_called()
    assert any(
        entry.get("event_code") == "scheduling_backfill_already_completed"
        for entry in logs
    )


def test_backfill_respects_page_limit(global_session):
    doctor = _authorized_doctor()
    _integration(doctor)
    provider = Mock()
    provider.fetch_historical = Mock(return_value=([], "same-cursor"))

    with (
        patch(
            "app.jobs.backfill_scheduling_roster.build_scheduling_provider",
            return_value=provider,
        ),
        capture_logs() as logs,
    ):
        perform(str(doctor.id), "acuity")

    assert provider.fetch_historical.call_count == _MAX_HISTORICAL_PAGES
    assert any(
        entry.get("event_code") == "scheduling_backfill_page_limit_reached"
        for entry in logs
    )


def test_backfill_stops_on_integration_invalid_mid_run(global_session):
    doctor = _authorized_doctor()
    _integration(doctor)
    provider = _fake_provider(
        [
            ([_event(external_appointment_ref="appt-1")], "cursor-1"),
            AcuityIntegrationInvalid("revoked"),
        ]
    )

    with (
        patch(
            "app.jobs.backfill_scheduling_roster.build_scheduling_provider",
            return_value=provider,
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
    integration = DoctorSchedulingIntegration.get(
        doctor_id=doctor.id, source=SchedulingProviderSource.acuity
    )
    assert integration is not None
    assert integration.last_backfilled_at is None
    assert any(
        entry.get("event_code") == "scheduling_backfill_integration_invalid"
        for entry in logs
    )


def test_backfill_continues_past_malformed_record(global_session):
    doctor = _authorized_doctor()
    _integration(doctor)
    good_event = _event(external_appointment_ref="appt-good")
    bad_event = _event(external_appointment_ref="appt-bad")
    provider = _fake_provider([([good_event, bad_event], None)])

    def fake_resolve(doctor_arg, event):
        if event.external_appointment_ref == "appt-bad":
            raise ValueError("boom")
        return resolve_event(doctor_arg, event)

    with (
        patch(
            "app.jobs.backfill_scheduling_roster.build_scheduling_provider",
            return_value=provider,
        ),
        patch(
            "app.jobs.backfill_scheduling_roster.resolve_event",
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
        entry.get("event_code") == "scheduling_backfill_record_skipped_malformed"
        for entry in logs
    )
