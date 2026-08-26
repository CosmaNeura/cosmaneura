from datetime import datetime, timezone

from azure.health.deidentification import DeidentificationClient
from azure.health.deidentification.models import (
    DeidentificationContent,
    DeidentificationResult,
)
from azure.identity import ClientSecretCredential, ManagedIdentityCredential

from app import log
from app.celery import celery_app
from app.configuration.azure import (
    AZURE_DEID_AUTH_MODE,
    AZURE_DEID_CLIENT_ID,
    AZURE_DEID_CLIENT_SECRET,
    AZURE_DEID_ENDPOINT,
    AZURE_DEID_MANAGED_IDENTITY_CLIENT_ID,
    AZURE_DEID_TENANT_ID,
)
from app.services.external_webhook_inbox import provider_log_ref

from activemodel.types import TypeIDType
from app.models.ai_visit_transcript import AIVisitTranscript, TranscriptEntry
from app.models.ai_visit_transcript_non_phi import AIVisitTranscriptNonPHI

_MAX_LOG_COUNT = 100_000


def _transcript_ref(transcript_id: object) -> str:
    return provider_log_ref(f"transcript:{transcript_id}")


def perform(transcript_id: str | TypeIDType) -> None:
    """De-identify and tombstone an AIVisitTranscript.

    Idempotent: if the transcript is already soft-deleted, returns
    without making any DB or Azure calls. Celery retries and any
    rare double-queueing from the daily sweeper hit this guard.
    """
    transcript = AIVisitTranscript.get(id=transcript_id)
    if not transcript or transcript.deleted_at:
        log.info(
            "transcript deidentification skipped",
            event_code="transcript_already_deleted",
            transcript_ref=_transcript_ref(transcript_id),
        )
        return

    log.info(
        "transcript deidentification started",
        event_code="transcript_deidentification_started",
        transcript_ref=_transcript_ref(transcript_id),
        entry_count=min(len(transcript.transcript_data), _MAX_LOG_COUNT),
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
    log.info(
        "transcript deidentification queued",
        event_code="transcript_deidentification_queued",
        transcript_ref=_transcript_ref(transcript_id),
    )
    perform_celery.delay(transcript_id)


# https://github.com/Azure/azure-sdk-for-python/blob/main/sdk/healthdataaiservices/azure-health-deidentification/samples/sample_realtime_deidentification.py
def deidentify(phi_text: str) -> str:
    text = phi_text.strip()
    if not text:
        return text

    if AZURE_DEID_AUTH_MODE == "managed_identity":
        # Pin the exact user-assigned identity. Do not silently fall through to
        # another developer or workload credential if its attachment is lost.
        credential = ManagedIdentityCredential(
            client_id=AZURE_DEID_MANAGED_IDENTITY_CLIENT_ID
        )
    else:
        # Temporary rollback path retained until the managed-identity
        # observation window closes and the app-registration secret is revoked.
        credential = ClientSecretCredential(
            client_id=AZURE_DEID_CLIENT_ID,
            tenant_id=AZURE_DEID_TENANT_ID,
            client_secret=AZURE_DEID_CLIENT_SECRET,
        )

    log.info(
        "deidentification provider request started",
        event_code="deidentification_request_started",
        credential_mode=AZURE_DEID_AUTH_MODE,
    )

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
    except Exception:
        # Exception messages from provider SDKs can contain request bodies or
        # URLs. Keep the event useful for alerting without serializing either.
        log.error(
            "deidentification provider request failed",
            event_code="deidentification_request_failed",
            credential_mode=AZURE_DEID_AUTH_MODE,
        )
        raise

    log.info(
        "deidentification provider request completed",
        event_code="deidentification_request_completed",
        credential_mode=AZURE_DEID_AUTH_MODE,
    )

    return result.output_text
