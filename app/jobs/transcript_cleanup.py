"""Daily sweep that queues TranscriptDeletionJob for any
AIVisitTranscript older than CLEANUP_AGE_DAYS with deleted_at IS NULL.

Owned by issue #29. The 14-day cutoff is a product decision, not
HIPAA-mandated. The cutoff lives here as a module-level constant
because changing it requires reasoning about the de-identified
dataset's coverage."""

from datetime import datetime, timedelta, timezone

from app import log
from app.celery import celery_app
from app.jobs.anonymize import queue as queue_deletion
from app.models.ai_visit_transcript import AIVisitTranscript


CLEANUP_AGE_DAYS = 14


def perform() -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(days=CLEANUP_AGE_DAYS)
    candidates = list(
        AIVisitTranscript.where(
            (AIVisitTranscript.created_at < cutoff)
            & (AIVisitTranscript.deleted_at == None)  # noqa: E711
        ).all()
    )

    log.info(
        "queuing transcript deletion job",
        count=len(candidates),
        cutoff=cutoff.isoformat(),
    )

    for transcript in candidates:
        queue_deletion(transcript.id)


perform_celery = celery_app.task(name="app.jobs.transcript_cleanup.perform")(perform)
