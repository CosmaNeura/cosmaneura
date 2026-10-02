"""Current invented patient context, Core de-id transport and proposal grounding."""

from dataclasses import replace
from uuid import uuid4

import pytest

from app.services.visit_prep_clarification_contract import (
    PatientClarificationDecision,
    PatientClarificationReply,
    PatientClarificationTurn,
    PatientQuoteSpan,
    VisitPrepClarificationChanged,
    VisitPrepClarificationInvalid,
    select_clarification_context,
)
from app.services.visit_prep_clarification_decisions import (
    record_patient_clarification_decision,
)
from app.services.visit_prep_clarification_proposals import (
    record_initial_clarification_proposal,
    select_patient_clarification_context,
)
from app.services.visit_prep_clarification_protection import (
    ground_protected_reply,
    protect_clarification_context,
)
from app.services.visit_prep_protected_analysis import (
    VisitPrepProtectionFailed,
    protect_analysis_texts,
)

from tests.integration.visit_prep_protected_analysis_test import _deid
from tests.services.visit_prep_contribution_commands_test import _correct
from tests.services.visit_prep_contribution_projection_test import (
    _prepared,
    _projection,
    _topic,
)
from tests.services.visit_prep_packages_test import _read


def _context(monkeypatch):
    fixture = _prepared(monkeypatch, "clarification-protection")
    _correct(
        fixture, answer="Fictional_Name_Alpha: my invented sleep concern comes first."
    )
    context = select_clarification_context(_read(fixture), question_key="story")
    return fixture, context


def _quote(protected, words):
    text = protected.protected.reported_words
    start = text.index(words)
    return PatientClarificationReply(
        kind="organize",
        prompt_code=None,
        statements=(
            PatientQuoteSpan(
                source="answer",
                start=start,
                end=start + len(words),
                quote=words,
            ),
        ),
    )


def test_one_core_protection_call_excludes_contacts_ids_and_other_topics(monkeypatch):
    fixture, context = _context(monkeypatch)
    context = replace(
        context, turns=(PatientClarificationTurn("priority", "Invented priority."),)
    )
    calls = _deid(
        monkeypatch, lambda body: body.replace("Fictional_Name_Alpha", "Person")
    )
    protected = protect_clarification_context(context)
    payload = protected.model_payload()
    assert len(calls) == 1 and calls[0][1] == {"timeout_seconds": 12.0}
    assert context.prompt in calls[0][0] and "Invented priority." in calls[0][0]
    assert "Fictional_Name_Alpha" not in str(payload)
    assert fixture.access_token not in str(payload) and str(fixture.prep_id) not in str(
        payload
    )
    assert "My original invented reason." not in calls[0][0]
    assert "Fictional_Name_Alpha" not in repr(protected)
    assert payload["turns"] == [
        {"prompt_code": "priority", "patient_words": "Invented priority."}
    ]


def test_protected_excerpt_maps_shifted_offsets_and_patient_can_confirm_it(monkeypatch):
    fixture, context = _context(monkeypatch)
    _deid(monkeypatch, lambda body: body.replace("Fictional_Name_Alpha", "Person"))
    protected = protect_clarification_context(context)
    words = "my invented sleep concern comes first."
    reply = ground_protected_reply(protected, _quote(protected, words))
    assert reply.statements[0].start == context.reported_words.index(words)
    receipt = record_initial_clarification_proposal(
        access_token=fixture.access_token,
        first_name="José",
        role="patient",
        operation_id=str(uuid4()),
        context=context,
        reply=reply,
        model_profile="synthetic-deployment",
    )
    record_patient_clarification_decision(
        access_token=fixture.access_token,
        first_name="José",
        role="patient",
        proposal_id=receipt.proposal_id,
        operation_id=str(uuid4()),
        decision=PatientClarificationDecision("confirm"),
    )
    topic = _topic(_projection(fixture), "story")
    assert (
        topic.answer == words and topic.original_answer == "My invented health story."
    )
    assert topic.current_correction_id is not None


def test_surrogate_and_spanning_changed_identifier_never_become_patient_words(
    monkeypatch,
):
    _, context = _context(monkeypatch)
    _deid(monkeypatch, lambda body: body.replace("Fictional_Name_Alpha", "Person"))
    protected = protect_clarification_context(context)
    for words in ("Person", protected.protected.reported_words):
        with pytest.raises(VisitPrepClarificationInvalid):
            ground_protected_reply(protected, _quote(protected, words))


def test_private_context_selector_rechecks_patient_authority_and_current_basis(
    monkeypatch,
):
    fixture, context = _context(monkeypatch)
    selected = select_patient_clarification_context(
        access_token=fixture.access_token,
        first_name="José",
        role="patient",
        question_key="story",
        expected_basis=context.basis,
    )
    assert selected == context
    _correct(fixture, answer="Invented newer story.")
    with pytest.raises(VisitPrepClarificationChanged):
        select_patient_clarification_context(
            access_token=fixture.access_token,
            first_name="José",
            role="patient",
            question_key="story",
            expected_basis=context.basis,
        )


def test_shared_identifier_fragment_is_not_a_restored_patient_excerpt(monkeypatch):
    _, context = _context(monkeypatch)
    _deid(
        monkeypatch,
        lambda body: body.replace("Fictional_Name_Alpha", "Replacement_Alpha"),
    )
    protected = protect_clarification_context(context)
    with pytest.raises(VisitPrepClarificationInvalid):
        ground_protected_reply(protected, _quote(protected, "Alpha"))


@pytest.mark.parametrize(
    "transform",
    [
        lambda body: body.replace("_BEGIN", "_BROKEN"),
        lambda body: body + "Invented trailing text.",
        lambda body: None,
    ],
)
def test_bad_protection_result_has_no_raw_fallback(monkeypatch, transform):
    _, context = _context(monkeypatch)
    _deid(monkeypatch, transform)
    with pytest.raises(VisitPrepProtectionFailed):
        protect_clarification_context(context)


def test_shared_protection_preserves_batch_capacity_for_ten_source_pairs(monkeypatch):
    _deid(monkeypatch)
    texts = tuple(f"Invented frame {i}." for i in range(20))
    assert protect_analysis_texts(texts) == texts
