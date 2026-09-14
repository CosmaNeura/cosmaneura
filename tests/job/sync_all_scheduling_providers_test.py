from unittest.mock import patch

from app.jobs.sync_all_scheduling_providers import perform

from app.models.doctor_scheduling_integration import DoctorSchedulingIntegration
from app.models.patient_external_identity import SchedulingProviderSource

from tests.factories import DoctorFactory


def _integration(
    doctor, *, source: SchedulingProviderSource = SchedulingProviderSource.acuity
) -> DoctorSchedulingIntegration:
    return DoctorSchedulingIntegration(
        doctor_id=doctor.id,
        source=source,
        provider_calendar_ref="calendar-1",
        access_token="access-token",
    ).save()


def test_fanout_queues_sync_for_every_integration(global_session):
    doctor_a = DoctorFactory.build().save()
    doctor_b = DoctorFactory.build().save()
    _integration(doctor_a)
    _integration(doctor_b, source=SchedulingProviderSource.google_calendar)

    with patch(
        "app.jobs.sync_all_scheduling_providers.sync_scheduling_provider.queue"
    ) as queue:
        perform()

    assert queue.call_count == 2
    queue.assert_any_call(str(doctor_a.id), "acuity")
    queue.assert_any_call(str(doctor_b.id), "google_calendar")


def test_fanout_continues_past_one_failing_queue_call(global_session):
    """One integration's queue() blowing up must not stop the fan-out from
    reaching the rest of the table — same resilience property as every
    other whole-table reconciliation job (sync_clerk.py, the sync/backfill
    jobs this fans out to)."""
    doctor_a = DoctorFactory.build().save()
    doctor_b = DoctorFactory.build().save()
    _integration(doctor_a)
    _integration(doctor_b, source=SchedulingProviderSource.google_calendar)

    def fake_queue(doctor_id: str, source: str) -> None:
        if doctor_id == str(doctor_a.id):
            raise RuntimeError("boom")

    with patch(
        "app.jobs.sync_all_scheduling_providers.sync_scheduling_provider.queue",
        side_effect=fake_queue,
    ) as queue:
        perform()

    assert queue.call_count == 2
    queue.assert_any_call(str(doctor_b.id), "google_calendar")


def test_fanout_noop_when_no_integrations(global_session):
    with patch(
        "app.jobs.sync_all_scheduling_providers.sync_scheduling_provider.queue"
    ) as queue:
        perform()

    queue.assert_not_called()
