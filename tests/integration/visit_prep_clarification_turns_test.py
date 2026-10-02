"""Actual patient-word custody; model replies remain invented fixtures.

Uses current Core commands and independent synthetic PostgreSQL connections.
No service grant, browser/model qualification, identity or consent is implied.
"""

import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from threading import Barrier, Event
from uuid import uuid4

import pytest
from typeid import TypeID

import app.services.visit_prep_clarification_decisions as decisions
import app.services.visit_prep_clarification_proposals as proposals
import app.services.visit_prep_clarification_turns as commands
from app.services import visit_prep_interviews as interviews
from app.services.visit_prep_clarification_contract import (
    PatientClarificationDecision,
    PatientClarificationReply,
    PatientQuoteSpan,
    VisitPrepClarificationChanged,
    VisitPrepClarificationInvalid,
    append_patient_clarification,
    select_clarification_context,
)
from app.services.visit_prep_clarification_evidence import read_clarification_proposal
from app.services.visit_prep_clarification_turn_evidence import read_clarification_turn
from app.services.visit_prep_clarification_view import get_patient_clarification_view
from app.services.visit_preps import rotate_visit_prep_credential

from activemodel.session_manager import get_session, global_session
from app.models.authority import AuthorityAuditEvent
from app.models.practice import Practice
from app.models.visit_prep import VisitPrep, VisitPrepLegalHold
from app.models.visit_prep_clarification import VisitPrepClarificationProposal
from app.models.visit_prep_clarification_turn import VisitPrepClarificationTurn
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlmodel import select

from tests.integration.visit_prep_contribution_lock_test import _observe_waiting_lock
from tests.integration.visit_prep_interviews_lock_test import _fixture as lock_fixture
from tests.services.visit_prep_clarification_contract_test import _context, _organize
from tests.services.visit_prep_contribution_commands_test import _correct, _review
from tests.services.visit_prep_contribution_projection_test import (
    _prepared,
    _projection,
    _topic,
)
from tests.services.visit_prep_interviews_test import _Fixture
from tests.services.visit_prep_packages_test import _read

WORDS = "Invented patient's follow-up words."


def _save(fixture, context, *, turn=None, reply=None, operation=None):
    arguments = dict(
        access_token=fixture.access_token,
        first_name="José",
        role=context.actor_role,
        operation_id=operation or str(uuid4()),
        context=context,
        reply=reply
        or PatientClarificationReply(
            kind="clarify", prompt_code="detail", statements=()
        ),
        model_profile="synthetic.patient-turn-v1",
    )
    if turn is not None:
        return proposals.record_followup_clarification_proposal(
            **arguments, source_turn_id=turn.turn_id
        )
    return proposals.record_initial_clarification_proposal(**arguments)


def _start(monkeypatch, *, role="patient", question="story", complete=True):
    if not complete or question == "medications":
        fixture = _prepared(monkeypatch, "patient-turn", role=role, complete=complete)
        if question == "medications":
            _correct(
                fixture,
                question="medications",
                answer="Invented medication report.",
                role=role,
            )
        context = select_clarification_context(_read(fixture), question_key=question)
    else:
        fixture, context = _context(monkeypatch, role=role, question=question)
    proposal = _save(fixture, context)
    return fixture, context, proposal


def _submit(
    fixture, proposal, *, operation=None, words=WORDS, first_name="José", role="patient"
):
    return commands.record_patient_clarification_turn(
        access_token=fixture.access_token,
        first_name=first_name,
        role=role,
        proposal_id=proposal.proposal_id,
        operation_id=operation or str(uuid4()),
        patient_words=words,
    )


def _counts(fixture):
    with get_session() as session:
        turns = session.exec(
            select(VisitPrepClarificationTurn).where(
                VisitPrepClarificationTurn.visit_prep_id == fixture.prep_id
            )
        ).all()
        audits = [
            a
            for a in session.exec(
                select(AuthorityAuditEvent).where(
                    AuthorityAuditEvent.event_type == "visit_prep.clarification_turn"
                )
            ).all()
            if {"kind": "visit_prep", "ref": str(fixture.prep_id)} in a.platform_refs
        ]
        return len(turns), len(audits)


@pytest.mark.parametrize("role", ["patient", "caregiver"])
@pytest.mark.parametrize("question", ["reason", "medications", "adherence", "story"])
def test_actual_patient_turn_is_encrypted_atomic_and_exactly_replayable(
    monkeypatch, role, question
):
    fixture, context, proposal = _start(monkeypatch, role=role, question=question)
    before = _projection(fixture)
    operation = str(uuid4())
    turn = _submit(fixture, proposal, role=role, operation=operation)
    replay = _submit(fixture, proposal, role=role, operation=operation)
    assert turn.disposition == "accepted" and replay.disposition == "replayed"
    assert replace(turn, disposition="replayed") == replay
    assert set(asdict(turn)) == {
        "turn_id",
        "proposal_id",
        "disposition",
        "turn_sequence",
        "basis",
    }
    assert turn.turn_sequence == 1 and turn.basis == context.basis
    assert _counts(fixture) == (1, 1) and _projection(fixture) == before
    with get_session() as session:
        row = session.get(VisitPrepClarificationTurn, TypeID.from_string(turn.turn_id))
        source = session.get(
            VisitPrepClarificationProposal, TypeID.from_string(proposal.proposal_id)
        )
        assert row is not None and source is not None
        evidence = read_clarification_turn(
            row, proposal=read_clarification_proposal(source)
        )
        assert evidence.patient_words == WORDS
        assert (
            evidence.context(read_clarification_proposal(source)).turns[0].patient_words
            == WORDS
        )
        assert WORDS not in row.evidence_ciphertext and WORDS not in repr(row)
        assert (
            "patient_words" not in row.model_dump()
            and "evidence_ciphertext" not in row.model_dump()
        )
        assert row.created_at == row.recorded_at
    if question == "adherence":
        assert _topic(_projection(fixture), "adherence").answer == "yes"


@pytest.mark.parametrize("role", ["patient", "caregiver"])
@pytest.mark.parametrize("action", ["confirm", "edit", "keep_original", "dismiss"])
def test_followup_organization_uses_saved_turn_then_existing_patient_decision(
    monkeypatch, role, action
):
    fixture, context, initial = _start(monkeypatch, role=role)
    turn = _submit(fixture, initial, role=role)
    expanded = append_patient_clarification(
        context,
        PatientClarificationReply(kind="clarify", prompt_code="detail", statements=()),
        patient_words=WORDS,
    )
    reply = PatientClarificationReply(
        kind="organize",
        prompt_code=None,
        statements=(
            PatientQuoteSpan(
                source="answer", start=0, end=len(context.answer), quote=context.answer
            ),
            PatientQuoteSpan(
                source="clarification_1", start=0, end=len(WORDS), quote=WORDS
            ),
        ),
    )
    operation = str(uuid4())
    saved = _save(fixture, expanded, turn=turn, reply=reply, operation=operation)
    replay = _save(fixture, expanded, turn=turn, reply=reply, operation=operation)
    assert replay.proposal_id == saved.proposal_id and replay.disposition == "replayed"
    view = get_patient_clarification_view(
        access_token=fixture.access_token,
        first_name="José",
        role=role,
        question_key="story",
    )
    assert (
        view.status == "available"
        and view.organized_words == context.answer + "\n" + WORDS
    )
    assert view.turns[0].patient_words == WORDS
    key = str(uuid4())
    choice = PatientClarificationDecision(
        action, "Invented patient edit." if action == "edit" else None
    )
    receipt = decisions.record_patient_clarification_decision(
        access_token=fixture.access_token,
        first_name="José",
        role=role,
        proposal_id=saved.proposal_id,
        operation_id=key,
        decision=choice,
    )
    repeated = decisions.record_patient_clarification_decision(
        access_token=fixture.access_token,
        first_name="José",
        role=role,
        proposal_id=saved.proposal_id,
        operation_id=key,
        decision=choice,
    )
    assert (
        repeated.decision_id == receipt.decision_id
        and repeated.disposition == "replayed"
    )
    topic = _topic(_projection(fixture), "story")
    assert topic.original_answer == context.answer
    assert topic.answer == (
        view.organized_words
        if action == "confirm"
        else choice.edited_words
        if action == "edit"
        else context.answer
    )
    with pytest.raises((VisitPrepClarificationInvalid, VisitPrepClarificationChanged)):
        _submit(fixture, initial, role=role)


def test_two_turns_are_bound_to_exact_prompts_and_third_turn_is_rejected(monkeypatch):
    fixture, context, initial = _start(monkeypatch)
    first = _submit(fixture, initial)
    expanded = append_patient_clarification(
        context,
        PatientClarificationReply(kind="clarify", prompt_code="detail", statements=()),
        patient_words=WORDS,
    )
    next_reply = PatientClarificationReply(
        kind="clarify", prompt_code="priority", statements=()
    )
    second_prompt = _save(fixture, expanded, turn=first, reply=next_reply)
    second = _submit(fixture, second_prompt, words="Invented second answer.")
    final = append_patient_clarification(
        expanded, next_reply, patient_words="Invented second answer."
    )
    assert second.turn_sequence == 2
    with pytest.raises(VisitPrepClarificationInvalid):
        _save(
            fixture,
            final,
            turn=second,
            reply=PatientClarificationReply(
                kind="clarify", prompt_code="sequence", statements=()
            ),
        )
    organized = _save(fixture, final, turn=second, reply=_organize(final))
    assert organized.proposal_id != second_prompt.proposal_id and _counts(fixture) == (
        2,
        2,
    )
    with pytest.raises((VisitPrepClarificationInvalid, VisitPrepClarificationChanged)):
        _submit(fixture, second_prompt)


@pytest.mark.parametrize("words", ["", " ", " altered ", "x" * 2001, "\x00", None, 42])
def test_invalid_words_are_private_and_do_not_write(monkeypatch, words):
    fixture, _, proposal = _start(monkeypatch)
    with pytest.raises(VisitPrepClarificationInvalid) as caught:
        _submit(fixture, proposal, words=words)
    assert caught.value.__cause__ is caught.value.__context__ is None
    assert _counts(fixture) == (0, 0)


@pytest.mark.parametrize("kind", ["organize", "unavailable"])
def test_patient_cannot_answer_a_nonclarification_proposal(monkeypatch, kind):
    fixture, context = _context(monkeypatch)
    proposal = _save(
        fixture,
        context,
        reply=_organize(context)
        if kind == "organize"
        else PatientClarificationReply(
            kind="unavailable", prompt_code=None, statements=()
        ),
    )
    with pytest.raises(VisitPrepClarificationInvalid):
        _submit(fixture, proposal)
    assert _counts(fixture) == (0, 0)


@pytest.mark.parametrize(
    "change", ["correction", "review", "supersede", "decide", "rotate", "quarantine"]
)
@pytest.mark.parametrize("replay", [False, True])
def test_changed_source_or_authority_cannot_capture_or_replay(
    monkeypatch, change, replay
):
    fixture, context, proposal = _start(
        monkeypatch,
        question="reason" if change == "rotate" else "story",
        complete=change != "rotate",
    )
    key = str(uuid4())
    if replay:
        _submit(fixture, proposal, operation=key)
    if change == "correction":
        _correct(fixture)
    elif change == "review":
        _review(fixture)
    elif change == "supersede":
        _save(fixture, context)
    elif change == "decide":
        decisions.record_patient_clarification_decision(
            access_token=fixture.access_token,
            first_name="José",
            role="patient",
            proposal_id=proposal.proposal_id,
            operation_id=str(uuid4()),
            decision=PatientClarificationDecision("dismiss"),
        )
    elif change == "rotate":
        rotate_visit_prep_credential(
            owner_practice_member_id=fixture.owner.id,
            owner_doctor_id=fixture.doctor.id,
            practice_id=fixture.practice.id,
            visit_prep_id=fixture.prep_id,
            expected_credential_generation=1,
        )
    else:
        interviews.report_wrong_recipient(access_token=fixture.access_token)
    with pytest.raises((VisitPrepClarificationInvalid, VisitPrepClarificationChanged)):
        _submit(fixture, proposal, operation=key)
    assert _counts(fixture) == (int(replay), int(replay))


@pytest.mark.parametrize("change", ["wrong_name", "wrong_role", "rollout_off"])
@pytest.mark.parametrize("replay", [False, True])
def test_recipient_or_rollout_denial_precedes_patient_word_decoding(
    monkeypatch, change, replay
):
    from app.configuration.visit_prep import VisitPrepSettings

    fixture, _, proposal = _start(monkeypatch)
    key = str(uuid4())
    if replay:
        _submit(fixture, proposal, operation=key)
    calls = []

    def no_decrypt(*args, **kwargs):
        calls.append(True)
        raise AssertionError("unauthorized patient evidence was decoded")

    monkeypatch.setattr(decisions, "read_clarification_proposal", no_decrypt)
    if change == "rollout_off":
        monkeypatch.setattr(
            interviews.visit_prep_access, "VISIT_PREP_SETTINGS", VisitPrepSettings()
        )
    with pytest.raises(VisitPrepClarificationInvalid):
        _submit(
            fixture,
            proposal,
            operation=key,
            first_name="Wrong synthetic name" if change == "wrong_name" else "José",
            role="caregiver" if change == "wrong_role" else "patient",
        )
    assert not calls and _counts(fixture) == (int(replay), int(replay))


def test_divergent_operation_and_second_response_cannot_overwrite(monkeypatch):
    fixture, _, proposal = _start(monkeypatch)
    key = str(uuid4())
    _submit(fixture, proposal, operation=key)
    for operation in (key, str(uuid4())):
        with pytest.raises(VisitPrepClarificationChanged):
            _submit(
                fixture,
                proposal,
                operation=operation,
                words="Invented different answer.",
            )
    assert _counts(fixture) == (1, 1)


@pytest.mark.parametrize(
    "change",
    ["words", "prompt", "missing", "other_turn", "superseded", "audit_unavailable"],
)
def test_followup_model_cannot_substitute_or_invent_patient_turns(monkeypatch, change):
    fixture, context, proposal = _start(monkeypatch)
    turn = _submit(fixture, proposal)
    expanded = append_patient_clarification(
        context,
        PatientClarificationReply(kind="clarify", prompt_code="detail", statements=()),
        patient_words=WORDS,
    )
    source = turn
    if change == "words":
        expanded = replace(
            expanded,
            turns=(
                replace(
                    expanded.turns[0], patient_words="Invented model substitution."
                ),
            ),
        )
    elif change == "prompt":
        expanded = replace(
            expanded, turns=(replace(expanded.turns[0], prompt_code="background"),)
        )
    elif change == "missing":
        source = replace(
            turn, turn_id=str(TypeID(prefix="visit_prep_clarification_turn"))
        )
    elif change == "other_turn":
        other, _, other_proposal = _start(monkeypatch)
        source = _submit(other, other_proposal)
    elif change == "superseded":
        _save(fixture, context)
    else:

        def no_audit(*args):
            raise VisitPrepClarificationInvalid()

        monkeypatch.setattr(commands, "_check_audit", no_audit)
    with pytest.raises((VisitPrepClarificationInvalid, VisitPrepClarificationChanged)):
        _save(fixture, expanded, turn=source, reply=_organize(expanded))
    with get_session() as session:
        assert len(
            session.exec(
                select(VisitPrepClarificationProposal).where(
                    VisitPrepClarificationProposal.visit_prep_id == fixture.prep_id
                )
            ).all()
        ) == (2 if change == "superseded" else 1)


@pytest.mark.parametrize(
    "failure", ["seal", "audit", "readback", "final", "acknowledgment"]
)
def test_failure_rolls_back_or_exactly_recovers_a_committed_turn(monkeypatch, failure):
    fixture, _, proposal = _start(monkeypatch)
    key = str(uuid4())
    original_commit = commands._commit
    calls = 0

    def fail(*args, **kwargs):
        raise VisitPrepClarificationInvalid()

    if failure == "seal":
        monkeypatch.setattr(commands, "seal_clarification_turn", fail)
    elif failure == "audit":
        monkeypatch.setattr(commands, "_check_audit", fail)
    elif failure == "readback":
        monkeypatch.setattr(commands, "read_clarification_turn", fail)
    elif failure == "final":
        original = commands._authorized

        def authorize(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls >= 3:
                raise VisitPrepClarificationInvalid()
            return original(*args, **kwargs)

        monkeypatch.setattr(commands, "_authorized", authorize)
    else:

        def lost(session):
            original_commit(session)
            raise VisitPrepClarificationInvalid()

        monkeypatch.setattr(commands, "_commit", lost)
    with global_session() as parent:
        with pytest.raises(VisitPrepClarificationInvalid):
            _submit(fixture, proposal, operation=key)
        parent.commit()
    assert _counts(fixture) == (int(failure == "acknowledgment"),) * 2
    if failure == "acknowledgment":
        monkeypatch.setattr(commands, "_commit", original_commit)
        assert _submit(fixture, proposal, operation=key).disposition == "replayed"


def test_turn_and_audit_are_invisible_until_actual_commit(monkeypatch):
    fixture, _, proposal = _start(monkeypatch)
    commit = commands._commit

    def observe(session):
        assert _counts(fixture) == (0, 0)
        commit(session)

    monkeypatch.setattr(commands, "_commit", observe)
    _submit(fixture, proposal)
    assert _counts(fixture) == (1, 1)


def test_concurrent_same_action_retains_one_patient_turn_and_audit(monkeypatch):
    fixture, _, proposal = _start(monkeypatch)
    barrier = Barrier(2)
    key = str(uuid4())

    def submit():
        barrier.wait(timeout=5)
        return _submit(fixture, proposal, operation=key)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(submit) for _ in range(2)]
        results = [future.result(timeout=10) for future in futures]
    assert sorted(x.disposition for x in results) == ["accepted", "replayed"]
    assert results[0].turn_id == results[1].turn_id and _counts(fixture) == (1, 1)


@pytest.mark.parametrize("change", ["correction", "baa", "quarantine", "rotate"])
def test_patient_capture_after_actual_lock_wait_sees_committed_change(
    monkeypatch, change
):
    fixture, _, proposal = _start(monkeypatch, question="reason", complete=False)
    started = Event()

    @contextmanager
    def waiting_session():
        with get_session() as session:
            session.execute(text("SET LOCAL application_name = 'vp_patient_turn_wait'"))
            started.set()
            yield session

    monkeypatch.setattr(decisions, "_visit_prep_session", waiting_session)
    with global_session() as parent:
        parent.exec(
            select(Practice).where(Practice.id == fixture.practice.id).with_for_update()
        ).one()
        parent.exec(
            select(VisitPrep).where(VisitPrep.id == fixture.prep_id).with_for_update()
        ).one()
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(_submit, fixture, proposal)
            try:
                assert started.wait(timeout=5)
                _observe_waiting_lock(parent, "vp_patient_turn_wait")
                if change == "correction":
                    _correct(
                        fixture,
                        question="reason",
                        answer="Invented changed while waiting.",
                    )
                elif change == "baa":
                    fixture.practice.negotiated_baa_on_file = False
                    fixture.practice.save()
                elif change == "quarantine":
                    interviews.report_wrong_recipient(access_token=fixture.access_token)
                else:
                    rotate_visit_prep_credential(
                        owner_practice_member_id=fixture.owner.id,
                        owner_doctor_id=fixture.doctor.id,
                        practice_id=fixture.practice.id,
                        visit_prep_id=fixture.prep_id,
                        expected_credential_generation=1,
                    )
            finally:
                parent.commit()
            with pytest.raises(
                (VisitPrepClarificationInvalid, VisitPrepClarificationChanged)
            ):
                future.result(timeout=10)
    assert _counts(fixture) == (0, 0)


def test_mutation_truncate_and_standalone_delete_fail_but_aggregate_purge_works(
    monkeypatch,
):
    fixture, context, proposal = _start(monkeypatch)
    turn = _submit(fixture, proposal)
    expanded = append_patient_clarification(
        context,
        PatientClarificationReply(kind="clarify", prompt_code="detail", statements=()),
        patient_words=WORDS,
    )
    _save(fixture, expanded, turn=turn, reply=_organize(expanded))
    with get_session() as session:
        with pytest.raises(DBAPIError, match="still retained"):
            session.execute(
                text("DELETE FROM public.visit_prep WHERE id = :id"),
                {"id": fixture.prep_id.uuid},
            )
        session.rollback()
    for sql in (
        "UPDATE public.visit_prep_clarification_turn SET operation_id = operation_id WHERE id = :id",
        "DELETE FROM public.visit_prep_clarification_turn WHERE id = :id",
        "TRUNCATE public.visit_prep_clarification_turn CASCADE",
    ):
        with get_session() as session:
            with pytest.raises(
                DBAPIError, match="immutable|aggregate cleanup|cannot be truncated"
            ):
                session.execute(
                    text(sql), {"id": TypeID.from_string(turn.turn_id).uuid}
                )
            session.rollback()
    interviews.report_wrong_recipient(access_token=fixture.access_token)
    with get_session() as session:
        root = session.get(VisitPrep, fixture.prep_id)
        root.misdirected_disposition = "purge_approved"
        root.misdirected_disposition_at = datetime.now(UTC)
        root.misdirected_disposition_until = root.misdirected_disposition_at
        root.misdirected_disposition_by_ref = "synthetic-security-operator"
        session.add(root)
        session.commit()
    with get_session() as session:
        session.delete(session.get(VisitPrep, fixture.prep_id))
        session.commit()
    with get_session() as session:
        assert (
            session.get(VisitPrepClarificationTurn, TypeID.from_string(turn.turn_id))
            is None
        )


def test_legal_hold_protects_retained_patient_turns(monkeypatch):
    fixture, _, proposal = _start(monkeypatch)
    turn = _submit(fixture, proposal)
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
            session.execute(
                text("DELETE FROM public.visit_prep WHERE id = :id"),
                {"id": fixture.prep_id.uuid},
            )
        session.rollback()
        assert (
            session.get(VisitPrepClarificationTurn, TypeID.from_string(turn.turn_id))
            is not None
        )


def test_final_entitlement_expiry_rolls_back_patient_turn_and_audit(monkeypatch):
    fixture, _, proposal = _start(monkeypatch)
    end = datetime.now(UTC) + timedelta(seconds=3)
    fixture.practice.billing_exempt_until = end
    fixture.practice.save()
    check = commands._check_audit

    def expires(session, *args):
        check(session, *args)
        session.execute(
            text(
                "SELECT pg_sleep(GREATEST(0, EXTRACT(EPOCH FROM (:end - clock_timestamp()))) + 0.1)"
            ),
            {"end": end},
        )

    monkeypatch.setattr(commands, "_check_audit", expires)
    with pytest.raises(VisitPrepClarificationInvalid):
        _submit(fixture, proposal)
    assert _counts(fixture) == (0, 0)


def test_link_expiry_after_real_lock_wait_cannot_retain_patient_words(monkeypatch):
    expiry = datetime.now(UTC) + timedelta(seconds=6)
    scheduled = expiry - timedelta(hours=72)
    source = lock_fixture(
        monkeypatch,
        "patient-turn-expiry",
        created_at=scheduled - timedelta(minutes=1),
        scheduled_at=scheduled,
    )
    interviews.confirm_interview(
        access_token=source.access_token, first_name="Synthetic", role="patient"
    )
    interviews.submit_response(
        access_token=source.access_token,
        idempotency_key=str(uuid4()),
        response=interviews.ResponseInput(
            "reason", "answered", "Invented expiry answer."
        ),
    )
    fixture = _Fixture(
        source.doctor,
        source.practice,
        source.owner,
        source.visit_prep_id,
        source.access_token,
        scheduled,
    )
    context = select_clarification_context(_read(fixture), question_key="reason")
    proposal = proposals.record_initial_clarification_proposal(
        access_token=fixture.access_token,
        first_name="Synthetic",
        role="patient",
        operation_id=str(uuid4()),
        context=context,
        reply=PatientClarificationReply(
            kind="clarify", prompt_code="detail", statements=()
        ),
        model_profile="synthetic.expiry",
    )
    original = decisions._visit_prep_session
    started = Event()

    @contextmanager
    def named():
        with original() as session:
            session.execute(
                text("SET LOCAL application_name = 'vp_patient_turn_expiry'")
            )
            started.set()
            yield session

    monkeypatch.setattr(decisions, "_visit_prep_session", named)
    with get_session() as parent:
        parent.exec(
            select(VisitPrep).where(VisitPrep.id == fixture.prep_id).with_for_update()
        ).one()
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(_submit, fixture, proposal, first_name="Synthetic")
            try:
                assert started.wait(timeout=5)
                _observe_waiting_lock(parent, "vp_patient_turn_expiry")
                parent.execute(
                    text(
                        "SELECT pg_sleep(GREATEST(0, EXTRACT(EPOCH FROM (:end - clock_timestamp()))) + 0.1)"
                    ),
                    {"end": expiry},
                )
            finally:
                parent.rollback()
            with pytest.raises(VisitPrepClarificationInvalid):
                future.result(timeout=10)
    assert _counts(fixture) == (0, 0)


def test_populated_expand_preserves_original_evidence_and_adds_nullable_turn_binding(
    monkeypatch,
):
    if os.environ.get("VISIT_PREP_MIGRATION_TARGET") != "4f6c6893dbe7":
        pytest.skip("requires explicit owned pre-expand rehearsal")
    from alembic import command
    from alembic.config import Config

    from tests.services.visit_prep_clarification_evidence_test import (
        _proposal as stored_proposal,
    )

    fixture, _, row = stored_proposal(monkeypatch)
    # The ORM knows the new nullable column; pre-expand writes use existing columns.
    columns = {
        c.name: getattr(row, c.name)
        for c in row.__table__.columns
        if c.name not in {"source_turn_id", "created_at", "updated_at", "recorded_at"}
    }
    with get_session() as session:
        session.execute(
            text(
                "INSERT INTO public.visit_prep_clarification_proposal ("
                + ",".join(columns)
                + ") VALUES ("
                + ",".join(":" + name for name in columns)
                + ")"
            ),
            {k: v.uuid if isinstance(v, TypeID) else v for k, v in columns.items()},
        )
        session.commit()
    tables = (
        "visit_prep",
        "visit_prep_question_response",
        "visit_prep_contribution_event",
        "visit_prep_clarification_proposal",
        "visit_prep_clarification_decision",
    )

    def snapshot():
        with get_session() as session:
            return {
                table: session.execute(
                    text(
                        f"SELECT (to_jsonb(t) - 'source_turn_id')::text FROM public.{table} t ORDER BY id"
                    )
                )
                .scalars()
                .all()
                for table in tables
            }

    before = snapshot()
    command.upgrade(Config("alembic.ini"), "65dee3dec2d0")
    assert snapshot() == before
    with get_session() as session:
        assert (
            session.get(VisitPrepClarificationProposal, row.id).source_turn_id is None
        )
        assert (
            session.execute(
                text("SELECT COUNT(*) FROM public.visit_prep_clarification_turn")
            ).scalar_one()
            == 0
        )
