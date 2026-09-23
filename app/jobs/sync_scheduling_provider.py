from datetime import datetime, timedelta, timezone

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
from app.models.patient_external_identity import SchedulingProviderSource

_MAX_LOG_COUNT = 10_000
# Same window docs/zoom-flow-implementation.md's calendar binding (2.3) looks
# back/forward from a meeting's actual start time, applied here as how far
# back a forward sync re-checks for edits to already-synced appointments.
_FORWARD_SYNC_LOOKBACK = timedelta(days=1)


def _bounded_count(value: int) -> int:
    return min(value, _MAX_LOG_COUNT)


def perform(doctor_id: str, source: str) -> None:
    """Forward-sync one doctor's upcoming appointments from one SchedulingProvider."""
    doctor = Doctor.get(id=doctor_id)
    doctor_ref = provider_log_ref(f"doctor:{doctor_id}")
    provider_source = SchedulingProviderSource(source)

    if not doctor or not doctor_access_in_force(doctor):
        log.info(
            "scheduling sync skipped",
            event_code="scheduling_sync_access_not_in_force",
            doctor_ref=doctor_ref,
            source=source,
        )
        return

    provider = build_scheduling_provider(doctor, provider_source)
    if provider is None:
        log.info(
            "scheduling sync skipped",
            event_code="scheduling_sync_integration_missing",
            doctor_ref=doctor_ref,
            source=source,
        )
        return

    since = datetime.now(timezone.utc) - _FORWARD_SYNC_LOOKBACK
    try:
        events = provider.fetch_upcoming(doctor, since)
    except SCHEDULING_INTEGRATION_INVALID_EXCEPTIONS:
        log.info(
            "scheduling sync skipped",
            event_code="scheduling_sync_integration_invalid",
            doctor_ref=doctor_ref,
            source=source,
        )
        return

    matched = 0
    queued = 0
    skipped_malformed = 0
    for event in events:
        try:
            outcome = resolve_event(doctor, event)
        except Exception:
            # One bad event must never truncate the rest of the sync — see
            # the inline history in sync_zoom_meetings.py for why this
            # matters (a single KeyError there once silently dropped the
            # remainder of a doctor's agenda).
            skipped_malformed += 1
            log.warning(
                "scheduling event skipped",
                event_code="scheduling_event_skipped_malformed",
                doctor_ref=doctor_ref,
                source=source,
            )
            continue
        if outcome.queued is not None:
            queued += 1
        else:
            matched += 1

    log.info(
        "scheduling sync completed",
        event_code="scheduling_sync_completed",
        doctor_ref=doctor_ref,
        source=source,
        fetched=_bounded_count(len(events)),
        matched=_bounded_count(matched),
        queued=_bounded_count(queued),
        skipped_malformed=_bounded_count(skipped_malformed),
    )


perform_celery = celery_app.task(perform)


def queue(doctor_id: str, source: str) -> None:
    "always use this method to queue the sync job"
    perform_celery.delay(doctor_id, source)
