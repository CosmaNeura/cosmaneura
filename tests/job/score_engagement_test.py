# pyright: reportAttributeAccessIssue=false, reportOptionalMemberAccess=false, reportArgumentType=false

from unittest.mock import patch

from app.engagement.cosmas_engagement.llm_client import HeuristicLLMClient
from app.engagement.service import reconstruct_patient_text, score_patient_text
from app.jobs.score_engagement import perform

from app.models.ai_visit_transcript import AIVisitTranscript, TranscriptEntry
from app.models.appointment import Appointment
from app.models.engagement_role import EngagementRole
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
