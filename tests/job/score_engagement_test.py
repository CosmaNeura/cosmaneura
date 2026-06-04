# pyright: reportAttributeAccessIssue=false, reportOptionalMemberAccess=false, reportArgumentType=false

from unittest.mock import patch

from app.engagement.cosmas_engagement.llm_client import HeuristicLLMClient
from app.engagement.service import (
    build_doctor_baseline,
    reconstruct_patient_text,
    score_patient_text,
)
from app.jobs.score_engagement import perform

from app.models.ai_visit_transcript import AIVisitTranscript, TranscriptEntry
from app.models.appointment import Appointment
from app.models.engagement_role import EngagementRole
from app.models.intake.intake import Intake
from app.models.patient import Patient

SELF_DIRECTED_TURNS = [
    ("Patient", "I want to understand all the options and the evidence."),
    ("Doctor", "Sure, let me walk you through it."),
    (
        "Patient",
        "I researched this myself and I'd prefer to decide. Why does this happen, "
        "and what are the alternatives?",
    ),
]


def _make_transcript() -> AIVisitTranscript:
    patient = Patient.where(Patient.name == "Samwise Gamgee").one()
    appt = Appointment.where(Appointment.patient_id == patient.id).one()
    return AIVisitTranscript(
        doctor_id=appt.doctor_id,
        appointment_id=appt.id,
        patient_id=patient.id,
        duration=120,
        transcript_data=[
            TranscriptEntry(speaker=s, content=c, startTime=0, endTime=1000)
            for s, c in SELF_DIRECTED_TURNS
        ],
    ).save()


def test_reconstruct_patient_text_keeps_only_patient_turns():
    text = reconstruct_patient_text(
        [
            {"speaker": "Patient", "content": "first"},
            {"speaker": "Doctor", "content": "ignored"},
            {"speaker": "system", "content": "ignored"},
            {"speaker": "Patient", "content": "second"},
        ]
    )
    assert text == "first\nsecond"


def test_score_patient_text_too_short_returns_none():
    assert score_patient_text("hi there", client=HeuristicLLMClient()) is None


def test_score_patient_text_returns_a_role():
    text = " ".join(c for s, c in SELF_DIRECTED_TURNS if s == "Patient")
    result = score_patient_text(text, client=HeuristicLLMClient())
    assert result is not None
    assert result.role.role_key in {"self_directed", "collaborative", "directed"}
    assert result.role.channel == "enacted"


def test_perform_persists_role_and_is_idempotent(global_session):
    transcript = _make_transcript()

    # Run the job offline by swapping the Azure client for the heuristic one.
    with patch("app.engagement.service.AzureOpenAILLMClient", HeuristicLLMClient):
        perform(transcript.id)
        role = EngagementRole.get(ai_transcript_id=transcript.id)
        assert role is not None
        assert role.appointment_id == transcript.appointment_id
        assert role.doctor_id == transcript.doctor_id
        assert role.channel == "enacted"
        assert 0.0 <= role.confidence <= 1.0
        assert set(role.distribution.keys()) == {
            "self_directed",
            "collaborative",
            "directed",
        }

        # Idempotent: a second run must not create a duplicate or raise.
        perform(transcript.id)

    assert (
        EngagementRole.where(EngagementRole.ai_transcript_id == transcript.id).count()
        == 1
    )


def test_build_doctor_baseline_neutral_until_enough_data(global_session):
    patient = Patient.where(Patient.name == "Samwise Gamgee").one()
    doctor_id = Appointment.where(Appointment.patient_id == patient.id).one().doctor_id

    # Below threshold -> neutral (absolute) scoring, regardless of how many rows exist.
    assert (
        build_doctor_baseline(doctor_id, min_samples=10_000).source == "neutral_default"
    )

    # Accrue a few scored encounters for this doctor, then a per-practice baseline
    # is built and standardization kicks in.
    with patch("app.engagement.service.AzureOpenAILLMClient", HeuristicLLMClient):
        for _ in range(3):
            perform(_make_transcript().id)

    fitted = build_doctor_baseline(doctor_id, min_samples=1)
    assert fitted.source.startswith(f"doctor:{doctor_id}")
    assert "decision_control" in fitted.axis_mean


def test_gap_persisted_when_intake_present(global_session):
    patient = Patient.where(Patient.name == "Samwise Gamgee").one()
    appt = Appointment.where(Appointment.patient_id == patient.id).one()

    # Ensure an intake with the patient's own words exists (the preferred read).
    intake = Intake.get(appointment_id=appt.id)
    questions = [
        {
            "order": 1,
            "question": "What brings you in and what are you hoping for?",
            "required_details": "",
            "answer": "I want all the options and the evidence; I'll decide myself.",
        }
    ]
    if intake is None:
        intake = Intake(
            doctor_id=appt.doctor_id,
            appointment_id=appt.id,
            external_intake_data={},
            intake_questions=questions,
        ).save()
    else:
        intake.intake_questions = questions
        intake.save()

    # A visit transcript where the patient defers -> a gap vs the intake.
    transcript = AIVisitTranscript(
        doctor_id=appt.doctor_id,
        appointment_id=appt.id,
        patient_id=patient.id,
        duration=120,
        transcript_data=[
            TranscriptEntry(
                speaker="Patient",
                content="Whatever you think is best, doctor. Just tell me what to do.",
                startTime=0,
                endTime=1000,
            )
        ],
    ).save()

    with patch("app.engagement.service.AzureOpenAILLMClient", HeuristicLLMClient):
        perform(transcript.id)

    role = EngagementRole.get(ai_transcript_id=transcript.id)
    assert role is not None
    assert role.preferred_role_key is not None
    assert role.preferred_confidence is not None
    assert role.gap_direction in {"suppressed", "over_enacted", "concordant"}
