from datetime import datetime

import app.jobs.anonymize
from app import log

from activemodel.types import TypeIDType
from app.models.ai_visit_transcript import AIVisitTranscript, TranscriptEntry
from app.models.ai_visit_transcript_non_phi import AIVisitTranscriptNonPHI


def perform(transcript_id: TypeIDType) -> None:
    """
    De-identify an AIVisitTranscript and create a non-PHI version.

    This command:
    1. Retrieves the transcript by ID
    2. De-identifies the transcript content using Azure's de-identification service
    3. Creates a new AIVisitTranscriptNonPHI record with the de-identified data
    4. Marks the original transcript as deleted and clears its data
    """
    log.info("starting transcript deletion", transcript_id=transcript_id)

    transcript = AIVisitTranscript.get(id=transcript_id)
    if not transcript:
        log.error("transcript not found", transcript_id=transcript_id)
        raise ValueError(f"Transcript {transcript_id} not found")

    if transcript.deleted_at:
        log.info("transcript already deleted", transcript_id=transcript_id)
        return

    # De-identify each transcript entry
    deidentified_entries = []

    for entry in transcript.transcript_data:
        if entry.content.strip():  # Only process non-empty content
            deidentified_content = app.jobs.anonymize.deidentify(entry.content)
            deidentified_entries.append(
                TranscriptEntry(
                    # Speaker labels (Doctor/Patient) are not PHI
                    speaker=entry.speaker,
                    content=deidentified_content,
                    startTime=entry.startTime,
                    endTime=entry.endTime,
                )
            )
        else:
            # Keep empty entries as-is
            deidentified_entries.append(entry)

    # Create non-PHI transcript record
    non_phi_transcript = AIVisitTranscriptNonPHI(data=deidentified_entries).save()

    log.info(
        "created non-phi transcript",
        original_transcript_id=transcript_id,
        non_phi_transcript_id=non_phi_transcript.id,
        entry_count=len(deidentified_entries),
    )

    # Mark original transcript as deleted and clear data
    transcript.transcript_data = []
    transcript.deleted_at = datetime.now()
    transcript.save()

    log.info("deleted original transcript", transcript_id=transcript_id)
