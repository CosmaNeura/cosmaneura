from app.models.ai_visit_transcript import TranscriptEntry
from app.models.ai_visit_transcript_non_phi import AIVisitTranscriptNonPHI


def test_ai_visit_transcript_non_phi_creation():
    """Test that AIVisitTranscriptNonPHI can be created with de-identified data."""
    # Create de-identified transcript data
    de_identified_data = [
        TranscriptEntry(
            speaker="Doctor",
            content="Hello, how are you feeling today?",
            startTime=0,
            endTime=5000,
        ),
        TranscriptEntry(
            speaker="Patient",
            content="I've been having some issues.",
            startTime=5000,
            endTime=10000,
        ),
    ]

    # Create non-PHI transcript
    non_phi_transcript = AIVisitTranscriptNonPHI(data=de_identified_data).save()

    # Verify the transcript was created
    assert non_phi_transcript.id is not None
    assert len(non_phi_transcript.data) == 2
    assert non_phi_transcript.data[0].speaker == "Doctor"
    assert non_phi_transcript.data[1].speaker == "Patient"

    # Verify it can be retrieved from database
    retrieved = AIVisitTranscriptNonPHI.get(non_phi_transcript.id)
    assert retrieved is not None
    assert len(retrieved.data) == 2
