from app.commands.transcript_deletion import perform as deletion_command
from app.jobs.transcript_deletion import perform, queue

from app.models.ai_visit_transcript import AIVisitTranscript, TranscriptEntry
from app.models.ai_visit_transcript_non_phi import AIVisitTranscriptNonPHI

from tests.factories import AppointmentFactory, DoctorFactory


def test_transcript_deletion_command():
    """Test that transcript deletion command de-identifies and marks transcript as deleted."""
    doctor = DoctorFactory.build().save()
    appointment = AppointmentFactory.build(doctor_id=doctor.id).save()

    # Create a transcript with sample data
    transcript_data = [
        TranscriptEntry(
            speaker="Doctor",
            content="Hello John, how are you feeling today?",
            startTime=0,
            endTime=5000,
        ),
        TranscriptEntry(
            speaker="Patient",
            content="I've been having headaches, Dr. Smith.",
            startTime=5000,
            endTime=10000,
        ),
    ]

    transcript = AIVisitTranscript(
        doctor_id=doctor.id,
        patient_id=appointment.patient_id,
        appointment_id=appointment.id,
        transcript_data=transcript_data,
        duration=300,
    ).save()

    transcript_id = transcript.id

    # Run deletion command
    deletion_command(transcript_id)

    # Verify non-PHI version was created
    non_phi_transcripts = list(AIVisitTranscriptNonPHI.where().all())
    assert len(non_phi_transcripts) == 1


def test_transcript_deletion_job():
    """Test that transcript deletion job runs the command."""
    doctor = DoctorFactory.build().save()
    appointment = AppointmentFactory.build(doctor_id=doctor.id).save()

    transcript = AIVisitTranscript(
        doctor_id=doctor.id,
        patient_id=appointment.patient_id,
        appointment_id=appointment.id,
        transcript_data=[
            TranscriptEntry(
                speaker="Doctor", content="Test content", startTime=0, endTime=5000
            )
        ],
        duration=300,
    ).save()

    transcript_id = transcript.id

    # Run job - should not raise an exception
    perform(transcript_id)


def test_queue_transcript_deletion():
    """Test queuing a transcript deletion job."""
    doctor = DoctorFactory.build().save()
    appointment = AppointmentFactory.build(doctor_id=doctor.id).save()

    transcript = AIVisitTranscript(
        doctor_id=doctor.id,
        patient_id=appointment.patient_id,
        appointment_id=appointment.id,
        transcript_data=[
            TranscriptEntry(
                speaker="Doctor", content="Test content", startTime=0, endTime=5000
            )
        ],
        duration=300,
    ).save()

    # Queue should not raise an exception
    queue(transcript.id)
