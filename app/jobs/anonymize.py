from datetime import datetime, timezone

from azure.health.deidentification import DeidentificationClient
from azure.health.deidentification.models import (
    DeidentificationContent,
    DeidentificationResult,
)
from azure.identity import ClientSecretCredential

from app import log
from app.celery import celery_app
from app.configuration.azure import (
    AZURE_DEID_CLIENT_ID,
    AZURE_DEID_CLIENT_SECRET,
    AZURE_DEID_ENDPOINT,
    AZURE_DEID_TENANT_ID,
)

from activemodel.types import TypeIDType
from app.models.ai_visit_transcript import AIVisitTranscript, TranscriptEntry
from app.models.ai_visit_transcript_non_phi import AIVisitTranscriptNonPHI


def perform(transcript_id: str | TypeIDType) -> None:
    """De-identify and tombstone an AIVisitTranscript.

    Idempotent: if the transcript is already soft-deleted, returns
    without making any DB or Azure calls. Celery retries and any
    rare double-queueing from the daily sweeper hit this guard.
    """
    transcript = AIVisitTranscript.get(id=transcript_id)
    if not transcript or transcript.deleted_at:
        log.info(
            "transcript already deleted, skipping",
            transcript_id=str(transcript_id),
        )
        return

    log.info(
        "deidentifying transcript for deletion",
        transcript_id=str(transcript_id),
        entries=len(transcript.transcript_data),
    )

    deidentified_entries = [
        TranscriptEntry(
            speaker=entry.speaker,
            content=deidentify(entry.content),
            startTime=entry.startTime,
            endTime=entry.endTime,
        )
        for entry in transcript.transcript_data
    ]

    AIVisitTranscriptNonPHI(data=deidentified_entries).save()

    transcript.transcript_data = []
    transcript.deleted_at = datetime.now(timezone.utc)
    transcript.save()


perform_celery = celery_app.task(name="app.jobs.anonymize.perform")(perform)


def queue(transcript_id: str | TypeIDType) -> None:
    log.info("queuing transcript deletion", transcript_id=str(transcript_id))
    perform_celery.delay(transcript_id)


# https://github.com/Azure/azure-sdk-for-python/blob/main/sdk/healthdataaiservices/azure-health-deidentification/samples/sample_realtime_deidentification.py
def deidentify(phi_text: str) -> str:
    text = phi_text.strip()
    if not text:
        return text

    # The Deidentification Service requires a service principal to authenticate.
    # Create one through the Entra portal.
    #   https://learn.microsoft.com/en-us/entra/identity-platform/howto-create-service-principal-portal
    # Make sure that your application has the "DeID Data Owner" role.
    credential = ClientSecretCredential(
        client_id=AZURE_DEID_CLIENT_ID,
        tenant_id=AZURE_DEID_TENANT_ID,
        client_secret=AZURE_DEID_CLIENT_SECRET,
    )

    log.info("running deid request")

    # The supported API versions are: 2024-11-15, 2025-07-15-preview
    # Using the stable version 2024-11-15
    client = DeidentificationClient(
        AZURE_DEID_ENDPOINT, credential, api_version="2024-11-15"
    )
    body = DeidentificationContent(input_text=text, operation="Surrogate")

    result: DeidentificationResult
    try:
        result = client.deidentify(body)
        assert result.output_text
    except Exception as e:
        # Note that we're deliberately not logging the text here, as it is PHI.
        log.error("error deidentifying text", error=str(e))
        raise

    log.debug("deid Text", output=result.output_text)

    return result.output_text
