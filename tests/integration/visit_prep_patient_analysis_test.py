"""Real patient request, protected wire, central usage and accepted contribution.

Only the external Sovereign/model transport and clinical de-id response are
invented. No service credentials, patient data or live model calls are used.
"""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest

from app.configuration import service_auth
from app.configuration import visit_prep_clarification as configuration
from app.services import visit_prep_patient_analysis as analysis
from app.services.visit_prep_clarification_contract import (
    PatientClarificationDecision,
    VisitPrepClarificationChanged,
)
from app.services.visit_prep_clarification_decisions import (
    record_patient_clarification_decision,
)
from app.services.visit_prep_clarification_view import get_patient_clarification_view
from app.services.visit_prep_contribution_contract import ContributionBasis

from activemodel.session_manager import get_session
from app.models.authority_usage import AgentModelUsageEvent
from sqlmodel import select

from tests.integration.visit_prep_protected_analysis_test import _deid
from tests.services.visit_prep_contribution_commands_test import _correct
from tests.services.visit_prep_contribution_projection_test import (
    _prepared,
    _projection,
    _topic,
)


def _enable(monkeypatch, *, before_claim=None, after_call=None, record_usage=True):
    client_id = str(uuid4())
    policy = replace(
        service_auth.SOVEREIGN_TOOL_AUTH_SETTINGS,
        enabled=True,
        tenant_id=str(uuid4()),
        audience=str(uuid4()),
        sovereign_client_ids=frozenset({client_id}),
    )
    monkeypatch.setattr(service_auth, "SOVEREIGN_TOOL_AUTH_SETTINGS", policy)
    monkeypatch.setattr(
        configuration,
        "SETTINGS",
        configuration.PatientAnalysisSettings(
            True,
            "https://sovereign.test.invalid/service/v1/visit-prep/clarification",
            str(uuid4()),
            str(uuid4()),
            "synthetic-deployment",
        ),
    )
    monkeypatch.setattr(
        configuration.PatientAnalysisSettings, "token", lambda _: "synthetic-token"
    )
    _deid(monkeypatch, lambda body: body.replace("Fictional_Name_Alpha", "Person"))
    now = int(datetime.now(UTC).timestamp())
    workload = service_auth.VisitPrepWorkloadPrincipal(
        client_id, service_auth.VerifiedCredentialWindow(now - 60, now - 60, now + 300)
    )
    calls = []
    real_client = httpx.Client

    def receiver(request):
        topic = json.loads(request.content)
        calls.append((topic, request.headers["x-visit-prep-analysis-grant"]))
        grant = request.headers["x-visit-prep-analysis-grant"]
        if before_claim:
            before_claim()
        claimed = analysis.claim_patient_analysis(
            grant=grant, topic_digest=analysis._digest(topic), workload=workload
        )
        with pytest.raises(analysis.PatientAnalysisUnavailable):
            analysis.claim_patient_analysis(
                grant=grant, topic_digest=analysis._digest(topic), workload=workload
            )
        words = "my invented sleep concern comes first."
        start = topic["reported_words"].index(words)
        at = datetime.now(UTC)
        usage = analysis.PatientUsage(
            client_event_id=uuid4(),
            call_kind="drafting",
            call_sequence=1,
            workstream="visit_prep",
            provider="azure_openai",
            model="synthetic-deployment",
            deployment="synthetic-deployment",
            purpose="visit_prep_patient_clarification",
            started_at=at,
            completed_at=at + timedelta(milliseconds=8),
            latency_ms=8,
            status="succeeded",
            usage_availability="partial",
            usage_source="provider_response",
            token_usage=dict(
                input_tokens=30,
                output_tokens=12,
                cached_input_tokens=None,
                reasoning_tokens=None,
            ),
        )
        if after_call:
            after_call()
        receipt = (
            analysis.record_patient_analysis_usage(
                grant=grant, usage=usage, workload=workload
            )
            if record_usage
            else {"record_ref": "model_usage_01j00000000000000000000000"}
        )
        if record_usage:
            assert (
                analysis.record_patient_analysis_usage(
                    grant=grant, usage=usage, workload=workload
                )
                == receipt
            )
        return httpx.Response(
            200,
            json={
                "analysis_id": claimed["analysis_id"],
                "usage_ref": receipt["record_ref"],
                "reply": {
                    "kind": "organize",
                    "prompt_code": None,
                    "statements": [
                        {
                            "source": "answer",
                            "start": start,
                            "end": start + len(words),
                            "quote": words,
                        }
                    ],
                },
            },
        )

    monkeypatch.setattr(
        analysis.httpx,
        "Client",
        lambda **options: real_client(
            **options, transport=httpx.MockTransport(receiver)
        ),
    )
    return calls


def _request(fixture, *, operation=None):
    return analysis.request_patient_clarification(
        access_token=fixture.access_token,
        first_name="José",
        role="patient",
        question_key="story",
        expected_basis=_projection(fixture).basis,
        operation_id=operation or str(uuid4()),
    )


@pytest.mark.parametrize("role", ["patient", "caregiver"])
def test_patient_request_protected_suggestion_and_explicit_acceptance(
    monkeypatch, role
):
    fixture = _prepared(monkeypatch, "patient-ai-action", role=role)
    _correct(
        fixture,
        role=role,
        answer="Fictional_Name_Alpha: my invented sleep concern comes first.",
    )
    calls = _enable(monkeypatch)
    operation = str(uuid4())
    before = _projection(fixture)
    arguments = dict(
        access_token=fixture.access_token,
        first_name="José",
        role=role,
        question_key="story",
        expected_basis=before.basis,
        operation_id=operation,
    )
    saved = analysis.request_patient_clarification(**arguments)
    assert _projection(fixture) == before
    replay = analysis.request_patient_clarification(**arguments)
    assert replay.proposal_id == saved.proposal_id and replay.disposition == "replayed"
    assert len(calls) == 1 and "Fictional_Name_Alpha" not in str(calls[0][0])
    assert str(fixture.prep_id) not in str(
        calls[0][0]
    ) and fixture.access_token not in str(calls)
    with get_session() as session:
        events = session.exec(
            select(AgentModelUsageEvent).where(
                AgentModelUsageEvent.patient_analysis_ref == UUID(operation)
            )
        ).all()
    assert len(events) == 2 and {e.batch_phase for e in events} == {"intent", "result"}
    assert all(
        e.actor_id is e.person_id is e.interaction_id is e.tenant_id is None
        for e in events
    )
    view = get_patient_clarification_view(
        access_token=fixture.access_token,
        first_name="José",
        role=role,
        question_key="story",
    )
    assert view.organized_words == "my invented sleep concern comes first."
    record_patient_clarification_decision(
        access_token=fixture.access_token,
        first_name="José",
        role=role,
        proposal_id=saved.proposal_id,
        operation_id=str(uuid4()),
        decision=PatientClarificationDecision("confirm"),
    )
    assert _topic(_projection(fixture), "story").answer == view.organized_words


@pytest.mark.parametrize("stage", ["claim", "reply"])
def test_source_changed_during_analysis_never_saves_suggestion(monkeypatch, stage):
    fixture = _prepared(monkeypatch, "patient-ai-changed")
    _correct(fixture, answer="my invented sleep concern comes first.")

    def change():
        _correct(fixture, answer="My newer invented answer.")

    _enable(
        monkeypatch,
        before_claim=change if stage == "claim" else None,
        after_call=change if stage == "reply" else None,
    )
    with pytest.raises(
        (analysis.PatientAnalysisUnavailable, VisitPrepClarificationChanged)
    ):
        _request(fixture)
    assert _topic(_projection(fixture), "story").answer == "My newer invented answer."


def test_unrecorded_usage_blocks_proposal(monkeypatch):
    fixture = _prepared(monkeypatch, "patient-ai-usage")
    _correct(fixture, answer="my invented sleep concern comes first.")
    _enable(monkeypatch, record_usage=False)
    with pytest.raises(analysis.PatientAnalysisUnavailable):
        _request(fixture)


def test_default_off_has_no_protection_or_model_work(monkeypatch):
    monkeypatch.setattr(
        configuration, "SETTINGS", replace(configuration.SETTINGS, enabled=False)
    )
    with pytest.raises(analysis.PatientAnalysisUnavailable):
        analysis.request_patient_clarification(
            access_token="invalid",
            first_name="Invented",
            role="patient",
            question_key="story",
            expected_basis=ContributionBasis(0, 0),
            operation_id=str(uuid4()),
        )
