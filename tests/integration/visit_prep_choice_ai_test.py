"""Patient-approved AI explanations keep the code and shared package intact.

Owned PostgreSQL; model and de-id responses are invented. This exercises the
real request, usage, stored proposal, decision and explanation streams.
"""

from uuid import uuid4

import pytest
from typeid import TypeID

from app.routes import companion_visit_prep
from app.routes.visit_prep_synthesis import _synthesis_response
from app.services import visit_prep_clarification_decisions as decisions
from app.services import visit_prep_contributions as contributions
from app.services.visit_prep_clarification_contract import (
    PatientClarificationDecision,
    VisitPrepClarificationChanged,
    VisitPrepClarificationInvalid,
)
from app.services.visit_prep_clarification_evidence import read_clarification_proposal
from app.services.visit_prep_clarification_turns import (
    record_patient_clarification_turn,
)
from app.services.visit_prep_clarification_view import (
    get_patient_clarification_view,
    get_patient_followup_view,
)
from app.services.visit_prep_interview_contract import ResponseInput
from app.services.visit_prep_patient_analysis import request_patient_clarification

from activemodel.session_manager import get_session
from app.models.visit_prep_clarification import VisitPrepClarificationProposal
from app.models.visit_prep_contribution import VisitPrepContributionEvent
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.authority_kernel.visit_prep_companion_auth_test import (
    signed as signed_identity,
)
from tests.integration.visit_prep_companion_http_test import _client
from tests.integration.visit_prep_explanations_test import _explain, _package
from tests.integration.visit_prep_patient_analysis_test import _enable
from tests.services.visit_prep_contribution_projection_test import (
    _prepared,
    _projection,
    _topic,
)
from tests.services.visit_prep_synthesis_publication_test import _view

WORDS = "my invented sleep concern comes first."
signed = signed_identity


def choice_reply(topic):
    if "explanation_words" in topic:
        source, text = "explanation", topic["explanation_words"]
    elif topic["turns"]:
        source, text = "clarification_1", topic["turns"][0]["patient_words"]
    else:
        return {"kind": "clarify", "prompt_code": "background", "statements": []}
    start = text.index(WORDS)
    return {
        "kind": "organize",
        "prompt_code": None,
        "statements": [
            {
                "source": source,
                "start": start,
                "end": start + len(WORDS),
                "quote": WORDS,
            }
        ],
    }


def _request(fixture, *, role="patient", turn=None, key=None):
    return request_patient_clarification(
        access_token=fixture.access_token,
        first_name="José",
        role=role,
        question_key="adherence",
        expected_basis=_projection(fixture).basis,
        operation_id=key or str(uuid4()),
        source_turn_id=turn,
    )


def _decide(
    fixture, proposal, *, role="patient", action="confirm", words=None, key=None
):
    return decisions.record_patient_clarification_decision(
        access_token=fixture.access_token,
        first_name="José",
        role=role,
        proposal_id=proposal.proposal_id,
        operation_id=key or str(uuid4()),
        decision=PatientClarificationDecision(action, words),
    )


@pytest.mark.parametrize("role,action", [("patient", "confirm"), ("caregiver", "edit")])
def test_protected_explanation_acceptance_preserves_choice_history_and_exact_replay(
    monkeypatch, role, action
):
    fixture = _prepared(monkeypatch, "choice-ai-" + role, role=role)
    _explain(fixture, role=role, words="Fictional_Name_Alpha: " + WORDS)
    before = _package(fixture)
    earlier = _topic(_projection(fixture), "adherence")
    calls = _enable(monkeypatch, reply_factory=choice_reply)
    operation = str(uuid4())
    proposal = _request(fixture, role=role, key=operation)
    assert _request(fixture, role=role, key=operation).disposition == "replayed"
    assert len(calls) == 1
    payload = calls[0][0]
    assert payload["explanation_words"] == "Person: " + WORDS
    assert "Fictional_Name_Alpha" not in str(payload)
    assert "explanation_source_ref" not in payload
    assert (
        _topic(_projection(fixture), "adherence") == earlier
    )  # no automatic acceptance
    view = get_patient_clarification_view(
        access_token=fixture.access_token,
        first_name="José",
        role=role,
        question_key="adherence",
    )
    assert (
        view.status == "available"
        and view.current_words == "yes"
        and view.organized_words == WORDS
    )
    key = str(uuid4())
    edited = "My explicitly edited invented explanation." if action == "edit" else None
    receipt = _decide(
        fixture, proposal, role=role, action=action, words=edited, key=key
    )
    assert (
        _decide(
            fixture, proposal, role=role, action=action, words=edited, key=key
        ).disposition
        == "replayed"
    )
    current = _projection(fixture)
    topic = _topic(current, "adherence")
    assert (
        topic.answer == topic.original_answer == "yes"
        and topic.current_correction_id is None
    )
    assert (
        topic.explanation_words == (edited or WORDS)
        and topic.explanation_id != earlier.explanation_id
    )
    assert receipt.basis.event_sequence == before.contribution.basis.event_sequence + 1
    package = _package(fixture)
    assert package.source_version != before.source_version
    assert (
        _topic(package.contribution, "adherence").explanation_words
        == topic.explanation_words
    )
    with get_session() as session:
        event = session.get(VisitPrepContributionEvent, topic.explanation_id)
        assert event.event_kind == "explanation" and event.actor_role == role
        assert (
            event.response_id == earlier.original_response_id
            and event.target_revision_id is None
        )
        assert (
            session.get(VisitPrepContributionEvent, earlier.explanation_id) is not None
        )


def test_choice_without_explanation_asks_and_retains_actual_followup_before_acceptance(
    monkeypatch,
):
    fixture = _prepared(monkeypatch, "choice-ai-followup")
    _enable(monkeypatch, reply_factory=choice_reply)
    prompt = _request(fixture)
    view = get_patient_followup_view(
        access_token=fixture.access_token,
        first_name="José",
        role="patient",
        question_key="adherence",
    )
    assert view.status == "prompt" and view.current_words == "yes"
    turn = record_patient_clarification_turn(
        access_token=fixture.access_token,
        first_name="José",
        role="patient",
        proposal_id=prompt.proposal_id,
        operation_id=str(uuid4()),
        patient_words=WORDS,
    )
    proposal = _request(fixture, turn=turn.turn_id)
    assert _topic(_projection(fixture), "adherence").explanation_words is None
    _decide(fixture, proposal)
    topic = _topic(_projection(fixture), "adherence")
    assert topic.answer == "yes" and topic.explanation_words == WORDS


@pytest.mark.parametrize("change", ["choice", "explanation"])
def test_same_code_revision_or_explanation_change_invalidates_old_proposal(
    monkeypatch, change
):
    fixture = _prepared(monkeypatch, "choice-ai-stale-" + change)
    _explain(fixture, words=WORDS)
    _enable(monkeypatch, reply_factory=choice_reply)
    proposal = _request(fixture)
    if change == "choice":
        contributions.correct_contribution(
            access_token=fixture.access_token,
            first_name="José",
            role="patient",
            expected_basis=_projection(fixture).basis,
            idempotency_key=str(uuid4()),
            response=ResponseInput("adherence", "answered", "yes"),
        )
    else:
        _explain(fixture, words="My changed invented explanation.")
    with pytest.raises(VisitPrepClarificationChanged):
        _decide(fixture, proposal)


def test_late_decision_failure_rolls_back_explanation_and_preserves_proposal(
    monkeypatch,
):
    fixture = _prepared(monkeypatch, "choice-ai-rollback")
    _explain(fixture, words=WORDS)
    _enable(monkeypatch, reply_factory=choice_reply)
    proposal = _request(fixture)
    before = _projection(fixture)

    def fail(*args, **kwargs):
        raise VisitPrepClarificationInvalid()

    monkeypatch.setattr(decisions, "_audit", fail)
    with pytest.raises(VisitPrepClarificationInvalid):
        _decide(fixture, proposal)
    assert _projection(fixture) == before


def test_database_guard_denies_code_correction_as_choice_explanation(monkeypatch):
    fixture = _prepared(monkeypatch, "choice-ai-sql-guard")
    _explain(fixture, words=WORDS)
    _enable(monkeypatch, reply_factory=choice_reply)
    proposal = _request(fixture)
    with get_session() as session:
        row = session.get(
            VisitPrepClarificationProposal, TypeID.from_string(proposal.proposal_id)
        )
        evidence = read_clarification_proposal(row)
    changed = contributions.correct_contribution(
        access_token=fixture.access_token,
        first_name="José",
        role="patient",
        expected_basis=evidence.context.basis,
        idempotency_key=str(uuid4()),
        response=ResponseInput("adherence", "answered", "yes"),
    )
    with get_session() as session:
        correction = _topic(_projection(fixture), "adherence").current_correction_id
        with pytest.raises(DBAPIError):
            session.execute(
                text("""INSERT INTO public.visit_prep_clarification_decision
              (id, visit_prep_id, proposal_id, action, correction_id, operation_id,
               envelope_format_version, evidence_ciphertext, payload_hmac_sha256)
              VALUES (:id, :prep, :proposal, 'confirm', :correction, :operation, 1, 'invented', :digest)"""),
                dict(
                    id=uuid4(),
                    prep=fixture.prep_id.uuid,
                    proposal=row.id.uuid,
                    correction=correction.uuid,
                    operation=str(uuid4()),
                    digest="a" * 64,
                ),
            )
        session.rollback()
    assert _projection(fixture).basis == changed.basis


def test_accepted_choice_explanation_is_same_source_on_dashboard_and_bot(
    monkeypatch, signed
):
    case, client, headers = _client(monkeypatch, signed)
    fixture = case.fixture
    _explain(fixture, words=WORDS)
    _enable(monkeypatch, reply_factory=choice_reply)
    _decide(fixture, _request(fixture))
    dashboard = _synthesis_response(_view(fixture)).model_dump(mode="json")
    response = client.post(
        companion_visit_prep.PATH,
        headers=headers,
        json=case.request.model_dump(mode="json"),
    )
    assert response.status_code == 200
    assert response.json()["package"] == dashboard
    assert (
        _topic(_package(fixture).contribution, "adherence").explanation_words == WORDS
    )
    assert WORDS in str(dashboard) and "yes" in str(dashboard)
