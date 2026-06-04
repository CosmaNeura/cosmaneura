# pyright: reportAttributeAccessIssue=false, reportOptionalMemberAccess=false, reportArgumentType=false

from unittest.mock import patch

from app.engagement.cosmas_engagement.llm_client import HeuristicLLMClient
from app.engagement.validation import (
    CPS_CONCORDANCE_BENCHMARK,
    compare_engine_vs_self_report,
)
from app.jobs.score_engagement import perform

from app.models.ai_visit_transcript import AIVisitTranscript, TranscriptEntry
from app.models.appointment import Appointment
from app.models.cps_self_report import CpsRole, CpsSelfReport
from app.models.engagement_role import EngagementRole
from app.models.patient import Patient

SELF_DIRECTED_TURNS = [
    ("Patient", "I want to see all the options and the evidence; I'll decide myself."),
]


def _samwise():
    patient = Patient.where(Patient.name == "Samwise Gamgee").one()
    appt = Appointment.where(Appointment.patient_id == patient.id).one()
    return patient, appt


def test_compare_returns_structured_summary(global_session):
    summary = compare_engine_vs_self_report()
    assert isinstance(summary["n_pairs"], int)
    assert summary["benchmark"] == CPS_CONCORDANCE_BENCHMARK


def test_matching_self_report_counts_as_agreement(global_session):
    patient, appt = _samwise()

    # Score a transcript -> an engine role for this appointment.
    transcript = AIVisitTranscript(
        doctor_id=appt.doctor_id,
        appointment_id=appt.id,
        patient_id=patient.id,
        duration=120,
        transcript_data=[
            TranscriptEntry(speaker=s, content=c, startTime=0, endTime=1000)
            for s, c in SELF_DIRECTED_TURNS
        ],
    ).save()
    with patch("app.engagement.service.AzureOpenAILLMClient", HeuristicLLMClient):
        perform(transcript.id)
    engine_role = EngagementRole.get(ai_transcript_id=transcript.id)
    assert engine_role is not None

    # Patient self-reports the SAME role -> ground-truth agreement.
    CpsSelfReport(
        appointment_id=appt.id,
        doctor_id=appt.doctor_id,
        preferred_cps_role=CpsRole(engine_role.cps_role),
    ).save()

    summary = compare_engine_vs_self_report(doctor_id=appt.doctor_id)
    assert summary["n_pairs"] == 1
    assert summary["agreement_rate"] == 1.0
    assert summary["delta_vs_benchmark"] == round(1.0 - CPS_CONCORDANCE_BENCHMARK, 3)
