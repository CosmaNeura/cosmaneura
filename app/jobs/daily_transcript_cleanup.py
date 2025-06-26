from datetime import datetime, timedelta

import app.jobs.transcript_deletion
from app import log
from app.celery import celery_app

from app.models.ai_visit_transcript import AIVisitTranscript


def perform() -> None:
    """
    Daily job to clean up old transcripts by de-identifying them.

    Finds all AIVisitTranscripts that are more than 2 weeks old and not yet deleted,
    then queues transcript deletion jobs for each of them.
    """
    log.info("starting daily transcript cleanup")

    # Calculate cutoff date (2 weeks ago)
    cutoff_date = datetime.now() - timedelta(days=14)

    # Find transcripts older than 2 weeks that haven't been deleted
    old_transcripts = list(
        AIVisitTranscript.where(
            (AIVisitTranscript.created_at < cutoff_date)
            & (AIVisitTranscript.deleted_at == None)
        ).all()
    )

    log.info(
        "found old transcripts for cleanup",
        count=len(old_transcripts),
        cutoff_date=cutoff_date.isoformat(),
    )

    # Queue deletion jobs for each transcript
    for transcript in old_transcripts:
        app.jobs.transcript_deletion.queue(transcript.id)
        log.info("queued transcript for deletion", transcript_id=transcript.id)

    log.info(
        "completed daily transcript cleanup", transcripts_queued=len(old_transcripts)
    )


perform_celery = celery_app.task(perform)


def queue() -> None:
    """Queue the daily transcript cleanup job."""
    log.info("queuing daily transcript cleanup")
    perform_celery.delay()
