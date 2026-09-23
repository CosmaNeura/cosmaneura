"""One-time historical roster import for a newly connected SchedulingProvider.

Satisfies John's "both" decision (2026-09-08): the forward sync in
sync_scheduling_provider.py only covers appointments from now on, so this
job separately imports everything before that point. Triggered once when a
doctor connects an integration (see acuity_auth.py / google_calendar_auth.py
TODOs on wiring), not cron-scheduled.

Mirrors sync_clerk.py's reconcile-the-whole-collection idiom: a per-record
try/except so one bad historical record never aborts the run, and a single
summary log line at the end.
"""

from datetime import datetime, timezone

from app import log
from app.celery import celery_app
from app.services.doctor_access import doctor_access_in_force
from app.services.external_webhook_inbox import provider_log_ref
from app.services.scheduling_provider import resolve_event
from app.services.scheduling_provider_factory import (
    SCHEDULING_INTEGRATION_INVALID_EXCEPTIONS,
    build_scheduling_provider,
)

from app.models.doctor import Doctor
from app.models.doctor_scheduling_integration import DoctorSchedulingIntegration
from app.models.patient_external_identity import SchedulingProviderSource

_MAX_LOG_COUNT = 10_000
# Hard ceiling on historical pages per run, independent of the provider's
# own pagination — a misbehaving provider (endless nextPageToken loop, a
# very old practice) must not turn one Celery task into an unbounded job.
_MAX_HISTORICAL_PAGES = 200


def _bounded_count(value: int) -> int:
    return min(value, _MAX_LOG_COUNT)


def perform(doctor_id: str, source: str) -> None:
    """Import a newly connected doctor's full historical roster, once."""
    doctor = Doctor.get(id=doctor_id)
    doctor_ref = provider_log_ref(f"doctor:{doctor_id}")
    provider_source = SchedulingProviderSource(source)

    if not doctor or not doctor_access_in_force(doctor):
        log.info(
            "scheduling backfill skipped",
            event_code="scheduling_backfill_access_not_in_force",
            doctor_ref=doctor_ref,
            source=source,
        )
        return

    provider = build_scheduling_provider(doctor, provider_source)
    if provider is None:
        log.info(
            "scheduling backfill skipped",
            event_code="scheduling_backfill_integration_missing",
            doctor_ref=doctor_ref,
            source=source,
        )
        return

    integration = DoctorSchedulingIntegration.get(
        doctor_id=doctor.id, source=provider_source
    )
    assert integration is not None  # build_scheduling_provider already checked
    if integration.last_backfilled_at is not None:
        log.info(
            "scheduling backfill skipped",
            event_code="scheduling_backfill_already_completed",
            doctor_ref=doctor_ref,
            source=source,
        )
        return

    before = datetime.now(timezone.utc)
    cursor: str | None = None
    fetched = 0
    matched = 0
    queued = 0
    skipped_malformed = 0
    pages = 0

    while True:
        pages += 1
        if pages > _MAX_HISTORICAL_PAGES:
            log.warning(
                "scheduling backfill page limit reached",
                event_code="scheduling_backfill_page_limit_reached",
                doctor_ref=doctor_ref,
                source=source,
                pages=pages,
            )
            break

        try:
            events, cursor = provider.fetch_historical(doctor, before, cursor)
        except SCHEDULING_INTEGRATION_INVALID_EXCEPTIONS:
            log.info(
                "scheduling backfill stopped",
                event_code="scheduling_backfill_integration_invalid",
                doctor_ref=doctor_ref,
                source=source,
            )
            return

        fetched += len(events)
        for event in events:
            try:
                outcome = resolve_event(doctor, event)
            except Exception:
                skipped_malformed += 1
                log.warning(
                    "scheduling backfill record skipped",
                    event_code="scheduling_backfill_record_skipped_malformed",
                    doctor_ref=doctor_ref,
                    source=source,
                )
                continue
            if outcome.queued is not None:
                queued += 1
            else:
                matched += 1

        if cursor is None:
            break

    # Re-fetch rather than reuse the object from the top of this function: a
    # long backfill run may span a token refresh, and saving the stale copy
    # would silently revert it.
    fresh_integration = DoctorSchedulingIntegration.get(
        doctor_id=doctor.id, source=provider_source
    )
    if fresh_integration is not None:
        fresh_integration.last_backfilled_at = datetime.now(timezone.utc)
        fresh_integration.save()

    log.info(
        "scheduling backfill completed",
        event_code="scheduling_backfill_completed",
        doctor_ref=doctor_ref,
        source=source,
        pages=pages,
        fetched=_bounded_count(fetched),
        matched=_bounded_count(matched),
        queued=_bounded_count(queued),
        skipped_malformed=_bounded_count(skipped_malformed),
    )


perform_celery = celery_app.task(perform)


def queue(doctor_id: str, source: str) -> None:
    "always use this method to queue the backfill job"
    perform_celery.delay(doctor_id, source)
