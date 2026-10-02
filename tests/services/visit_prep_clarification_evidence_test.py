"""Synthetic encrypted evidence and actual PostgreSQL storage invariants."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, cast
from uuid import uuid4

import pytest
from typeid import TypeID

import app.services.visit_prep_clarification_evidence as codec
from app.services import visit_prep_interviews as interviews
from app.services.visit_prep_clarification_contract import (
    DecisionAction,
    PatientClarificationDecision,
    PatientClarificationReply,
    PatientQuoteSpan,
    VisitPrepClarificationInvalid,
    append_patient_clarification,
    select_clarification_context,
)
from app.services.visit_prep_clarification_evidence import (
    ClarificationDecisionEvidence,
    ClarificationProposalEvidence,
    read_clarification_decision,
    read_clarification_proposal,
    seal_clarification_decision,
    seal_clarification_proposal,
)
from app.services.visit_preps import rotate_visit_prep_credential
from app.utils.encryption import (
    EncryptionError,
    decrypt_value,
    encrypt_value,
    pseudonymous_digest,
)

from activemodel.session_manager import get_session
from activemodel.types import TypeIDType
from app.models.visit_prep import VisitPrepLegalHold
from app.models.visit_prep_clarification import (
    VisitPrepClarificationDecision,
    VisitPrepClarificationProposal,
)
from app.models.visit_prep_question_set import VisitPrepQuestion
from sqlalchemy import delete, update
from sqlalchemy.exc import DBAPIError
from sqlmodel import SQLModel, select

from tests.services.visit_prep_clarification_contract_test import _context, _organize
from tests.services.visit_prep_contribution_commands_test import _correct
from tests.services.visit_prep_contribution_projection_test import _prepared
from tests.services.visit_prep_packages_test import _read


def _proposal(
    monkeypatch,
    fixture=None,
    *,
    role: Literal["patient", "caregiver"] = "patient",
    question="story",
):
    if fixture is None:
        fixture, context = _context(monkeypatch, role=role, question=question)
    else:
        context = select_clarification_context(_read(fixture), question_key=question)
    context = append_patient_clarification(
        context,
        PatientClarificationReply(
            kind="clarify", prompt_code="priority", statements=()
        ),
        patient_words="Invented important detail.",
    )
    reply = PatientClarificationReply(
        kind="organize",
        prompt_code=None,
        statements=(
            PatientQuoteSpan(
                source="answer",
                start=0,
                end=len(context.reported_words),
                quote=context.reported_words,
            ),
            PatientQuoteSpan(
                source="clarification_1",
                start=0,
                end=len(context.turns[0].patient_words),
                quote=context.turns[0].patient_words,
            ),
        ),
    )
    with get_session() as session:
        question_row = session.exec(
            select(VisitPrepQuestion).where(
                VisitPrepQuestion.question_set_version_id
                == TypeID.from_string(context.question_set_version_id),
                VisitPrepQuestion.question_key == question,
            )
        ).one()
    evidence = ClarificationProposalEvidence(
        str(TypeID(prefix="visit_prep_clarification")),
        str(fixture.owner.id),
        str(question_row.id),
        1,
        str(uuid4()),
        "synthetic-model-v1",
        context,
        reply,
    )
    sealed = seal_clarification_proposal(evidence)
    source = TypeID.from_string(context.current_source_ref)
    row = cast(Any, VisitPrepClarificationProposal)(
        id=TypeID.from_string(evidence.proposal_id),
        visit_prep_id=fixture.prep_id,
        owner_practice_member_id=fixture.owner.id,
        question_set_version_id=TypeID.from_string(context.question_set_version_id),
        question_id=question_row.id,
        credential_generation=1,
        actor_role=context.actor_role,
        basis_response_count=context.basis.original_response_count,
        basis_event_sequence=context.basis.event_sequence,
        source_version=context.source_version,
        source_response_id=source if source.prefix == "visit_prep_response" else None,
        source_contribution_id=source
        if source.prefix == "visit_prep_contribution"
        else None,
        operation_id=evidence.operation_id,
        evidence_ciphertext=sealed.ciphertext,
        payload_hmac_sha256=sealed.payload_hmac_sha256,
    )
    return fixture, evidence, row


def _decision(
    proposal, *, action: DecisionAction = "keep_original", correction=None, words=None
):
    evidence = ClarificationDecisionEvidence(
        str(TypeID(prefix="visit_prep_clarification_decision")),
        str(uuid4()),
        PatientClarificationDecision(action, words),
        str(correction.id) if correction is not None else None,
    )
    sealed = seal_clarification_decision(evidence, proposal=proposal)
    row = cast(Any, VisitPrepClarificationDecision)(
        id=TypeID.from_string(evidence.decision_id),
        visit_prep_id=TypeID.from_string(proposal.context.package_id),
        proposal_id=TypeID.from_string(proposal.proposal_id),
        action=action,
        correction_id=correction.id if correction is not None else None,
        operation_id=evidence.operation_id,
        evidence_ciphertext=sealed.ciphertext,
        payload_hmac_sha256=sealed.payload_hmac_sha256,
    )
    return evidence, row


def _reject(row, match):
    with get_session() as session:
        with pytest.raises(DBAPIError, match=match):
            with session.begin_nested():
                session.add(row)
                session.flush()


def _private(operation):
    with pytest.raises(VisitPrepClarificationInvalid) as caught:
        operation()
    assert caught.value.__cause__ is None and caught.value.__context__ is None
    assert "invented" not in str(caught.value).lower()


@pytest.mark.parametrize("role", ["patient", "caregiver"])
@pytest.mark.parametrize("action", ["keep_original", "dismiss", "confirm", "edit"])
def test_exact_encrypted_proposal_and_decision_preserve_original(
    monkeypatch, role, action
):
    fixture, proposal, row = _proposal(monkeypatch, role=role)
    row = row.save()
    original = proposal.context.answer
    correction = None
    if action in {"confirm", "edit"}:
        _correct(fixture, answer="Invented accepted words.", role=role)
        from app.models.visit_prep_contribution import VisitPrepContributionEvent

        with get_session() as session:
            correction = session.exec(
                select(VisitPrepContributionEvent).where(
                    VisitPrepContributionEvent.visit_prep_id == fixture.prep_id
                )
            ).one()
    evidence, decision_row = _decision(
        proposal,
        action=action,
        correction=correction,
        words="Invented edited words." if action == "edit" else None,
    )
    decision_row = decision_row.save()
    assert read_clarification_proposal(row) == proposal
    assert read_clarification_decision(decision_row, proposal=proposal) == evidence
    for stored in (row, decision_row):
        assert stored.created_at == stored.recorded_at
        assert "invented" not in stored.evidence_ciphertext.lower()
        assert "evidence_ciphertext" not in stored.model_dump()
        assert "payload_hmac_sha256" not in stored.model_dump()
        assert "invented" not in repr(stored).lower()
    assert "invented" not in repr(proposal).lower()
    assert "Invented edited words." not in repr(evidence)
    assert json.loads(decrypt_value(row.evidence_ciphertext)) == proposal.manifest()
    topic = next(
        topic
        for topic in _read(fixture).contribution.topics
        if topic.question.key == "story"
    )
    assert topic.original_answer == original
    # Storage shape does not prove that this arbitrary synthetic correction
    # matches the proposed/edited words. The future atomic writer must do that.
    _, duplicate = _decision(
        proposal,
        action=action,
        correction=correction,
        words="Invented edited words." if action == "edit" else None,
    )
    _reject(duplicate, "uq_vp_clarification_decision_proposal")


@pytest.mark.parametrize(
    "field,value",
    [
        ("basis_event_sequence", 99),
        ("basis_response_count", 5),
        ("credential_generation", 2),
        ("actor_role", "caregiver"),
        ("question_id", None),
        ("evidence_ciphertext", ""),
        ("payload_hmac_sha256", "not keyed"),
        ("operation_id", "private text"),
    ],
)
def test_database_rejects_malformed_or_stale_proposals(monkeypatch, field, value):
    _, _, row = _proposal(monkeypatch)
    setattr(row, field, value)
    _reject(
        row, "basis is not current|lineage|envelope_shape|hmac_shape|operation_shape"
    )


def test_source_must_be_latest_answered_topic_not_another_or_old_source(monkeypatch):
    fixture, _, row = _proposal(monkeypatch)
    row.source_response_id = cast(
        TypeIDType,
        TypeID.from_string(
            select_clarification_context(
                _read(fixture), question_key="reason"
            ).current_source_ref
        ),
    )
    _reject(row, "source is not current answered topic")
    fixture, _, old = _proposal(monkeypatch, fixture)
    _correct(fixture, answer="Invented current story.")
    old.basis_event_sequence = 1
    _reject(old, "source is not current answered topic")
    _, current, fresh = _proposal(monkeypatch, fixture)
    assert fresh.source_contribution_id is not None
    assert read_clarification_proposal(fresh.save()) == current
    _, _, obsolete = _proposal(monkeypatch, fixture)
    _correct(fixture, answer=None)
    obsolete.basis_event_sequence = 2
    _reject(obsolete, "source is not current answered topic")


@pytest.mark.parametrize("change", ["rotate", "wrong_recipient", "correction"])
def test_old_proposal_cannot_accept_a_later_decision(monkeypatch, change):
    if change == "rotate":
        fixture = _prepared(monkeypatch, "clarification-rotation", complete=False)
        fixture, proposal, row = _proposal(monkeypatch, fixture, question="reason")
    else:
        fixture, proposal, row = _proposal(monkeypatch)
    row.save()
    _, decision = _decision(proposal)
    if change == "rotate":
        rotate_visit_prep_credential(
            owner_practice_member_id=fixture.owner.id,
            owner_doctor_id=fixture.doctor.id,
            practice_id=fixture.practice.id,
            visit_prep_id=fixture.prep_id,
            expected_credential_generation=1,
        )
    elif change == "wrong_recipient":
        interviews.report_wrong_recipient(access_token=fixture.access_token)
    else:
        _correct(fixture, answer="Invented competing correction.")
    _reject(decision, "stale|active current authority")


def test_decision_cannot_bind_another_preparation_or_topic(monkeypatch):
    fixture, proposal, row = _proposal(monkeypatch)
    row.save()
    _correct(fixture, question="reason", answer="Invented different topic.")
    from app.models.visit_prep_contribution import VisitPrepContributionEvent

    with get_session() as session:
        correction = session.exec(
            select(VisitPrepContributionEvent).where(
                VisitPrepContributionEvent.visit_prep_id == fixture.prep_id
            )
        ).one()
    _, wrong_topic = _decision(proposal, action="confirm", correction=correction)
    _reject(wrong_topic, "targets another topic")
    other, _, _ = _proposal(monkeypatch)
    _, wrong_root = _decision(proposal)
    wrong_root.visit_prep_id = other.prep_id
    _reject(wrong_root, "proposal lineage")


@pytest.mark.parametrize("stream", ["proposal", "decision"])
def test_orm_and_direct_sql_cannot_rewrite_or_delete_evidence(monkeypatch, stream):
    _, proposal, row = _proposal(monkeypatch)
    row = row.save()
    if stream == "decision":
        _, row = _decision(proposal)
        row = row.save()
    table = SQLModel.metadata.tables[str(type(row).__tablename__)]
    for statement, message in (
        (
            update(table).where(table.c.id == row.id).values(operation_id=str(uuid4())),
            "immutable",
        ),
        (delete(table).where(table.c.id == row.id), "aggregate cleanup"),
    ):
        with get_session() as session:
            with pytest.raises(DBAPIError, match=message):
                with session.begin_nested():
                    session.execute(statement)
    row.operation_id = str(uuid4())
    with get_session() as session:
        with pytest.raises(RuntimeError, match="append-only"):
            with session.begin_nested():
                session.add(row)
                session.flush()


def test_preparation_retention_and_hold_protect_both_streams(monkeypatch):
    fixture, proposal, row = _proposal(monkeypatch)
    row.save()
    _, decision = _decision(proposal)
    decision.save()
    table = SQLModel.metadata.tables["visit_prep"]
    with get_session() as session:
        with pytest.raises(DBAPIError, match="still retained"):
            with session.begin_nested():
                session.execute(delete(table).where(table.c.id == fixture.prep_id))
    VisitPrepLegalHold(
        visit_prep_id=fixture.prep_id,
        owner_practice_member_id=fixture.owner.id,
        generation=1,
        placed_at=datetime.now(UTC),
        placed_by_ref="actor:synthetic",
        placement_reason="synthetic_review",
    ).save()
    with get_session() as session:
        with pytest.raises(DBAPIError, match="active legal hold"):
            with session.begin_nested():
                session.execute(delete(table).where(table.c.id == fixture.prep_id))


@pytest.mark.parametrize(
    "field",
    [
        "id",
        "question_id",
        "source_version",
        "basis_event_sequence",
        "operation_id",
        "actor_role",
        "credential_generation",
        "recorded_at",
    ],
)
def test_ciphertext_cannot_be_rebound_to_changed_row_metadata(monkeypatch, field):
    _, _, row = _proposal(monkeypatch)
    row = row.save()
    assert row.recorded_at is not None
    values = {
        "id": TypeID(prefix="visit_prep_clarification"),
        "question_id": TypeID(prefix="visit_prep_question"),
        "source_version": "c" * 64,
        "basis_event_sequence": 99,
        "operation_id": str(uuid4()),
        "actor_role": "caregiver",
        "credential_generation": 2,
        "recorded_at": row.recorded_at + timedelta(seconds=1),
    }
    _private(
        lambda: read_clarification_proposal(
            row.model_copy(update={field: values[field]})
        )
    )


@pytest.mark.parametrize(
    "change",
    [
        "duplicate",
        "unknown",
        "forged_quote",
        "oversized",
        "context_key",
        "wrong_domain",
    ],
)
def test_authentic_envelopes_remain_closed_and_exactly_grounded(monkeypatch, change):
    _, _, row = _proposal(monkeypatch)
    row = row.save()
    payload = json.loads(decrypt_value(row.evidence_ciphertext))
    if change == "unknown":
        payload["extra"] = "Invented private field."
    elif change == "forged_quote":
        payload["reply"]["statements"][0]["quote"] = "Invented fabricated claim."
    elif change == "context_key":
        payload["context"]["unknown"] = "Invented private context."
    plaintext = codec._json(payload)
    if change == "duplicate":
        plaintext = '{"format_version":1,' + plaintext[1:]
    elif change == "oversized":
        plaintext = " " * 47_001
    row.evidence_ciphertext = encrypt_value(plaintext)
    row.payload_hmac_sha256 = pseudonymous_digest(
        codec._json(payload),
        context=codec._DECISION_DOMAIN
        if change == "wrong_domain"
        else codec._PROPOSAL_DOMAIN,
    )
    _private(lambda: read_clarification_proposal(row))


def test_decision_envelope_is_bound_to_exact_proposal_not_a_replacement(monkeypatch):
    _, proposal, row = _proposal(monkeypatch)
    row.save()
    _, decision = _decision(proposal)
    decision = decision.save()
    changed = replace(proposal, reply=_organize(proposal.context))
    _private(lambda: read_clarification_decision(decision, proposal=changed))
    for changes in (
        {"id": TypeID(prefix="visit_prep_clarification_decision")},
        {"action": "dismiss"},
        {"operation_id": str(uuid4())},
    ):
        _private(
            lambda: read_clarification_decision(
                decision.model_copy(update=changes), proposal=proposal
            )
        )


@pytest.mark.parametrize("kind", ["clarify", "unavailable"])
def test_neutral_or_unavailable_reply_can_be_retained_and_dismissed(monkeypatch, kind):
    _, proposal, row = _proposal(monkeypatch)
    context = replace(proposal.context, turns=())
    proposal = replace(
        proposal,
        context=context,
        reply=PatientClarificationReply(
            kind=kind,
            prompt_code="detail" if kind == "clarify" else None,
            statements=(),
        ),
    )
    sealed = seal_clarification_proposal(proposal)
    row.evidence_ciphertext, row.payload_hmac_sha256 = (
        sealed.ciphertext,
        sealed.payload_hmac_sha256,
    )
    assert read_clarification_proposal(row.save()) == proposal
    evidence, decision = _decision(proposal, action="dismiss")
    assert read_clarification_decision(decision.save(), proposal=proposal) == evidence


def test_database_controls_recording_time_not_caller_timestamps(monkeypatch):
    _, proposal, row = _proposal(monkeypatch)
    future = datetime.now(UTC) + timedelta(days=100)
    row.created_at = row.updated_at = row.recorded_at = future
    row = row.save()
    assert (
        row.created_at == row.updated_at == row.recorded_at and row.recorded_at < future
    )
    _, decision = _decision(proposal)
    decision.created_at = decision.updated_at = decision.recorded_at = future
    decision = decision.save()
    assert (
        decision.created_at == decision.updated_at == decision.recorded_at
        and decision.recorded_at < future
    )


@pytest.mark.parametrize("stream", ["proposal", "decision"])
def test_unreadable_encryption_is_a_private_error_without_original_chain(
    monkeypatch, stream
):
    _, proposal, row = _proposal(monkeypatch)
    row = row.save()
    if stream == "decision":
        _, row = _decision(proposal)
        row = row.save()

    def reader():
        if stream == "decision":
            return read_clarification_decision(row, proposal=proposal)
        return read_clarification_proposal(row)

    row.evidence_ciphertext = "not-decryptable"
    _private(reader)


def test_choice_supplement_is_not_a_text_correction_or_saved_confirmation(monkeypatch):
    _, proposal, row = _proposal(monkeypatch, question="adherence")
    assert read_clarification_proposal(row.save()) == proposal
    _private(
        lambda: seal_clarification_decision(
            ClarificationDecisionEvidence(
                str(TypeID(prefix="visit_prep_clarification_decision")),
                str(uuid4()),
                PatientClarificationDecision("confirm"),
                str(TypeID(prefix="visit_prep_contribution")),
            ),
            proposal=proposal,
        )
    )


@pytest.mark.parametrize(
    "mode", ["wrong_type", "bool_generation", "profile", "encrypt"]
)
def test_invalid_input_and_crypto_errors_have_no_private_exception_chain(
    monkeypatch, mode
):
    _, proposal, _ = _proposal(monkeypatch)
    if mode == "wrong_type":
        value = {"private": "Invented input."}
    elif mode == "bool_generation":
        value = replace(proposal, credential_generation=True)
    elif mode == "profile":
        value = replace(proposal, model_profile="Invented private words!")
    else:

        def fail(*_, **__):
            raise EncryptionError()

        monkeypatch.setattr(codec, "encrypt_value", fail)
        value = proposal
    _private(lambda: seal_clarification_proposal(cast(Any, value)))
