import app.commands.transcript_deletion
from app import log
from app.celery import celery_app

from activemodel.types import TypeIDType


def perform(transcript_id: TypeIDType | str) -> None:
    """
    Celery job to de-identify and delete a transcript.

    This job calls the transcript deletion command to process a single transcript.
    """
    log.info("starting transcript deletion job", transcript_id=transcript_id)

    try:
        app.commands.transcript_deletion.perform(str(transcript_id))
        log.info("completed transcript deletion job", transcript_id=transcript_id)
    except Exception as e:
        log.error(
            "transcript deletion job failed", transcript_id=transcript_id, error=str(e)
        )
        raise


perform_celery = celery_app.task(perform)


def queue(transcript_id: TypeIDType | str) -> None:
    """Queue a transcript deletion job."""
    log.info("queuing transcript deletion job", transcript_id=transcript_id)
    perform_celery.delay(transcript_id)
