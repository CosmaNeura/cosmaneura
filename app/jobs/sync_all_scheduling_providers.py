"""Fan-out: queue a forward sync for every connected SchedulingProvider integration.

Beat-scheduled (see app/celery.py). Zoom's equivalent sync has no cron
because it's triggered per-doctor on the /schedule page load; scheduling
sync has no such natural page-load trigger (a doctor's roster should stay
current whether or not they open the app), so it runs on a fixed cadence
instead. Mirrors sync_clerk.py's "loop the whole table, one bad row never
aborts the rest" idiom.
"""

from app import log
from app.celery import celery_app
from app.jobs import sync_scheduling_provider
from app.services.external_webhook_inbox import provider_log_ref

from app.models.doctor_scheduling_integration import DoctorSchedulingIntegration


def perform() -> None:
    integrations = DoctorSchedulingIntegration.all()
    queued = 0
    skipped = 0
    for integration in integrations:
        try:
            sync_scheduling_provider.queue(
                str(integration.doctor_id), integration.source.value
            )
            queued += 1
        except Exception:
            skipped += 1
            log.warning(
                "scheduling provider sync fan-out skipped one integration",
                event_code="scheduling_sync_fanout_skipped",
                doctor_ref=provider_log_ref(f"doctor:{integration.doctor_id}"),
                source=integration.source.value,
            )
            continue

    log.info(
        "scheduling provider sync fan-out completed",
        event_code="scheduling_sync_fanout_completed",
        queued=queued,
        skipped=skipped,
    )


perform_celery = celery_app.task(perform)
