"""Display only saved Core evidence; admission is not recording progress."""

from app.models.ai_visit_note import AIVisitNote
from app.models.ai_visit_transcript import AIVisitTranscript
from sqlmodel import col, select


def saved_encounter_statuses(session, doctor_id, appointment_ids):
    if not appointment_ids:
        return {}
    # Select references only. Catalog discovery does not load transcript/note text.
    transcripts = session.exec(
        select(col(AIVisitTranscript.appointment_id)).where(
            AIVisitTranscript.doctor_id == doctor_id,
            col(AIVisitTranscript.appointment_id).in_(appointment_ids),
            col(AIVisitTranscript.deleted_at).is_(None),
        )
    ).all()
    drafts = session.exec(
        select(col(AIVisitNote.appointment_id)).where(
            AIVisitNote.doctor_id == doctor_id,
            col(AIVisitNote.appointment_id).in_(appointment_ids),
            col(AIVisitNote.superseded_at).is_(None),
        )
    ).all()
    return {
        **{str(ref): "recording_saved" for ref in transcripts},
        **{str(ref): "draft_ready" for ref in drafts},
    }
