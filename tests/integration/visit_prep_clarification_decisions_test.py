"""Actual patient decision transactions over synthetic saved proposals.

The proposal fixtures are deliberately not qualified live model/service calls.
The real command, encrypted sources, SQL guards and audit are exercised here;
browser presentation, conversational qualification and choice supplements are
not implied by these storage/transaction checks.
"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from threading import Barrier, Event
from typing import Literal
from uuid import uuid4

import pytest
from typeid import TypeID

import app.services.visit_prep_clarification_decisions as commands
from app.configuration.visit_prep import VisitPrepSettings
from app.services import visit_prep_interviews as interviews
from app.services.visit_prep_clarification_contract import (
    DecisionAction,
    PatientClarificationDecision,
    PatientClarificationReply,
    VisitPrepClarificationChanged,
    VisitPrepClarificationInvalid,
    validate_clarification_reply,
)
from app.services.visit_prep_clarification_decisions import (
    record_patient_clarification_decision,
)
from app.services.visit_prep_clarification_evidence import (
    read_clarification_decision,
    seal_clarification_proposal,
)
from app.services.visit_preps import rotate_visit_prep_credential

from activemodel.session_manager import get_session, global_session
from app.models.authority import AuthorityAuditEvent
from app.models.practice import Practice
from app.models.visit_prep import VisitPrep
from app.models.visit_prep_clarification import VisitPrepClarificationDecision
from app.models.visit_prep_contribution import VisitPrepContributionEvent
from sqlalchemy import func, text
from sqlmodel import select

from tests.integration.visit_prep_contribution_lock_test import _observe_waiting_lock
from tests.integration.visit_prep_interviews_lock_test import _fixture as lock_fixture
from tests.services.visit_prep_clarification_evidence_test import _decision, _proposal
from tests.services.visit_prep_contribution_commands_test import _correct, _review
from tests.services.visit_prep_contribution_projection_test import (
    _prepared,
    _projection,
    _topic,
)
from tests.services.visit_prep_interviews_test import _Fixture
from tests.services.visit_prep_packages_test import _read


def _submit(
    fixture,
    proposal,
    *,
    action: DecisionAction = "confirm",
    words=None,
    operation=None,
    role: Literal["patient", "caregiver"] = "patient",
    name="José",
):
    return record_patient_clarification_decision(
        access_token=fixture.access_token,
        first_name=name,
        role=role,
        proposal_id=proposal.proposal_id,
        operation_id=operation or str(uuid4()),
        decision=PatientClarificationDecision(action, words),
    )


def _counts(session, fixture):
    decisions = session.exec(
        select(func.count())
        .select_from(VisitPrepClarificationDecision)
        .where(VisitPrepClarificationDecision.visit_prep_id == fixture.prep_id)
    ).one()
    events = session.exec(
        select(func.count())
        .select_from(VisitPrepContributionEvent)
        .where(VisitPrepContributionEvent.visit_prep_id == fixture.prep_id)
    ).one()
    audits = sum(
        {"kind": "visit_prep", "ref": str(fixture.prep_id)} in row.platform_refs
        for row in session.exec(
            select(AuthorityAuditEvent).where(
                AuthorityAuditEvent.event_type == "visit_prep.clarification_decision"
            )
        ).all()
    )
    return decisions, events, audits


@pytest.mark.parametrize("role", ["patient", "caregiver"])
@pytest.mark.parametrize("action", ["confirm", "edit", "keep_original", "dismiss"])
def test_one_committed_decision_exact_correction_and_audit(monkeypatch, role, action):
    fixture, proposal, row = _proposal(monkeypatch, role=role)
    row.save()
    before = _read(fixture)
    operation = str(uuid4())
    words = "Invented patient-edited wording." if action == "edit" else None
    receipt = _submit(
        fixture, proposal, action=action, words=words, role=role, operation=operation
    )
    replay = _submit(
        fixture, proposal, action=action, words=words, role=role, operation=operation
    )
    accepted = action in {"confirm", "edit"}
    assert receipt.disposition == "accepted" and replay.disposition == "replayed"
    assert receipt.decision_id == replay.decision_id
    assert receipt.action == action
    assert "Invented" not in repr(receipt)
    after = _read(fixture)
    topic = _topic(after.contribution, "story")
    expected = (
        words
        if action == "edit"
        else validate_clarification_reply(proposal.context, proposal.reply)
        if action == "confirm"
        else proposal.context.answer
    )
    assert topic.answer == expected
    assert topic.original_answer == proposal.context.answer
    assert after.contribution.actor_role == role
    assert (after.source_version != before.source_version) is accepted
    assert receipt.basis == after.contribution.basis
    with get_session() as session:
        assert _counts(session, fixture) == (1, int(accepted), 1)
        decision = session.get(
            VisitPrepClarificationDecision, TypeID.from_string(receipt.decision_id)
        )
        assert decision is not None
        evidence = read_clarification_decision(decision, proposal=proposal)
        assert evidence.decision == PatientClarificationDecision(action, words)
        audit = session.exec(
            select(AuthorityAuditEvent).where(
                AuthorityAuditEvent.resource_ref == receipt.decision_id
            )
        ).one()
        assert audit.actor_id is audit.person_id is audit.tenant_id is None
        assert "Invented" not in repr(audit)
        assert audit.request_digest == row.payload_hmac_sha256
        assert audit.occurred_at == decision.recorded_at


@pytest.mark.parametrize("question", ["reason", "story", "medications"])
def test_all_text_topic_kinds_save_exact_patient_words(monkeypatch, question):
    fixture = _prepared(monkeypatch, "clarification-text-kind")
    if question == "medications":
        _correct(fixture, question="medications", answer="Invented medication list.")
    fixture, proposal, row = _proposal(monkeypatch, fixture, question=question)
    row.save()
    _submit(fixture, proposal)
    current = _projection(fixture)
    assert _topic(current, question).answer == validate_clarification_reply(
        proposal.context, proposal.reply
    )
    assert _topic(current, question).original_answer == proposal.context.answer or (
        question == "medications" and _topic(current, question).original_answer is None
    )


@pytest.mark.parametrize("change", ["words", "action", "operation"])
def test_replay_requires_identical_patient_choice_and_operation(monkeypatch, change):
    fixture, proposal, row = _proposal(monkeypatch)
    row.save()
    operation = str(uuid4())
    _submit(
        fixture,
        proposal,
        action="edit",
        words="Invented accepted.",
        operation=operation,
    )
    with pytest.raises(VisitPrepClarificationChanged):
        _submit(
            fixture,
            proposal,
            action="keep_original" if change == "action" else "edit",
            words=None
            if change == "action"
            else "Different invented words."
            if change == "words"
            else "Invented accepted.",
            operation=str(uuid4()) if change == "operation" else operation,
        )
    with get_session() as session:
        assert _counts(session, fixture) == (1, 1, 1)


@pytest.mark.parametrize("stage", ["before", "after"])
@pytest.mark.parametrize("change", ["correction", "review"])
def test_source_changes_deny_new_or_replayed_decision(monkeypatch, stage, change):
    fixture, proposal, row = _proposal(monkeypatch)
    row.save()
    operation = str(uuid4())
    if stage == "after":
        _submit(fixture, proposal, operation=operation)
    if change == "correction":
        _correct(fixture, answer="Later invented patient correction.")
    else:
        _review(fixture)
    with pytest.raises(VisitPrepClarificationChanged):
        _submit(fixture, proposal, operation=operation)
    with get_session() as session:
        decisions, events, audits = _counts(session, fixture)
        assert decisions == audits == int(stage == "after")
        assert events == 1 + int(stage == "after")


@pytest.mark.parametrize(
    "failure", ["wrong_name", "wrong_role", "wrong_root", "disabled"]
)
def test_fresh_public_recipient_and_root_authority_required(monkeypatch, failure):
    fixture, proposal, row = _proposal(monkeypatch)
    row.save()
    arguments = {}
    if failure == "wrong_name":
        arguments["name"] = "Incorrect"
    elif failure == "wrong_role":
        arguments["role"] = "caregiver"
    elif failure == "wrong_root":
        fixture = _prepared(monkeypatch, "clarification-other-root")
    else:
        monkeypatch.setattr(
            interviews.visit_prep_access, "VISIT_PREP_SETTINGS", VisitPrepSettings()
        )
    with pytest.raises(VisitPrepClarificationInvalid) as caught:
        _submit(fixture, proposal, **arguments)
    assert caught.value.__context__ is caught.value.__cause__ is None
    with get_session() as session:
        assert _counts(session, fixture) == (0, 0, 0)


@pytest.mark.parametrize("kind", ["clarify", "unavailable"])
def test_nonorganized_reply_cannot_be_confirmed(monkeypatch, kind):
    fixture, proposal, row = _proposal(monkeypatch)
    reply = PatientClarificationReply(
        kind=kind,
        prompt_code="detail" if kind == "clarify" else None,
        statements=(),
    )
    proposal = replace(proposal, reply=reply)
    sealed = seal_clarification_proposal(proposal)
    row.evidence_ciphertext, row.payload_hmac_sha256 = (
        sealed.ciphertext,
        sealed.payload_hmac_sha256,
    )
    row.save()
    with pytest.raises(VisitPrepClarificationInvalid):
        _submit(fixture, proposal)
    _submit(fixture, proposal, action="dismiss")
    with get_session() as session:
        assert _counts(session, fixture) == (1, 0, 1)


def test_choice_supplement_is_not_disguised_as_coded_answer(monkeypatch):
    fixture, proposal, row = _proposal(monkeypatch, question="adherence")
    row.save()
    with pytest.raises(VisitPrepClarificationInvalid):
        _submit(fixture, proposal)
    _submit(fixture, proposal, action="keep_original")
    assert _topic(_projection(fixture), "adherence").answer == "yes"
    with get_session() as session:
        assert _counts(session, fixture) == (1, 0, 1)


def test_saved_storage_without_actual_command_audit_is_not_replayed(monkeypatch):
    fixture, proposal, row = _proposal(monkeypatch)
    row.save()
    evidence, decision_row = _decision(proposal)
    decision_row.save()
    with pytest.raises(VisitPrepClarificationInvalid):
        _submit(
            fixture, proposal, action="keep_original", operation=evidence.operation_id
        )
    with get_session() as session:
        assert _counts(session, fixture) == (1, 0, 0)


@pytest.mark.parametrize(
    "failure", ["seal", "audit", "final_authority", "commit_acknowledgment"]
)
def test_failure_never_acknowledges_or_partially_saves(monkeypatch, failure):
    fixture, proposal, row = _proposal(monkeypatch)
    row.save()
    operation = str(uuid4())
    original_audit, original_commit = commands._audit, commands._commit

    def fail(*_args, **_kwargs):
        raise VisitPrepClarificationInvalid()

    if failure == "seal":
        monkeypatch.setattr(commands, "seal_clarification_decision", fail)
    elif failure == "audit":
        monkeypatch.setattr(commands, "_audit", fail)
    elif failure == "final_authority":

        def audit_then_disable(*args, **kwargs):
            original_audit(*args, **kwargs)
            monkeypatch.setattr(
                interviews.visit_prep_access, "VISIT_PREP_SETTINGS", VisitPrepSettings()
            )

        monkeypatch.setattr(commands, "_audit", audit_then_disable)
    else:

        def commit_without_ack(session):
            original_commit(session)
            raise VisitPrepClarificationInvalid()

        monkeypatch.setattr(commands, "_commit", commit_without_ack)
    with pytest.raises(VisitPrepClarificationInvalid):
        _submit(fixture, proposal, operation=operation)
    with get_session() as session:
        durable = int(failure == "commit_acknowledgment")
        assert _counts(session, fixture) == (durable, durable, durable)
    if failure == "commit_acknowledgment":
        monkeypatch.setattr(commands, "_commit", original_commit)
        assert _submit(fixture, proposal, operation=operation).disposition == "replayed"


def test_independent_reader_sees_nothing_until_the_single_commit(monkeypatch):
    fixture, proposal, row = _proposal(monkeypatch)
    row.save()
    original = commands._commit
    observed = []

    def inspect_then_commit(session):
        assert _counts(session, fixture) == (1, 1, 1)
        with get_session() as observer:
            assert _counts(observer, fixture) == (0, 0, 0)
        original(session)
        with get_session() as observer:
            observed.append(_counts(observer, fixture))

    monkeypatch.setattr(commands, "_commit", inspect_then_commit)
    _submit(fixture, proposal)
    assert observed == [(1, 1, 1)]


def test_concurrent_identical_decisions_have_one_correction_and_audit(monkeypatch):
    fixture, proposal, row = _proposal(monkeypatch)
    row.save()
    operation, barrier = str(uuid4()), Barrier(2)
    original = commands._visit_prep_session

    @contextmanager
    def simultaneous_session():
        with original() as session:
            barrier.wait(timeout=5)
            yield session

    monkeypatch.setattr(commands, "_visit_prep_session", simultaneous_session)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(_submit, fixture, proposal, operation=operation)
            for _ in range(2)
        ]
        receipts = [f.result(timeout=15) for f in futures]
    assert sorted(r.disposition for r in receipts) == ["accepted", "replayed"]
    assert len({r.decision_id for r in receipts}) == 1
    with get_session() as session:
        assert _counts(session, fixture) == (1, 1, 1)


@pytest.mark.parametrize("change", ["correction", "quarantine", "baa", "rotate"])
def test_waiting_command_rechecks_committed_source_and_quarantine(monkeypatch, change):
    fixture = _prepared(monkeypatch, "clarification-command-wait", complete=False)
    fixture, proposal, row = _proposal(monkeypatch, fixture, question="reason")
    row.save()
    started = Event()
    original = commands._visit_prep_session

    @contextmanager
    def named_session():
        with original() as session:
            session.execute(
                text("SET LOCAL application_name = 'vp_clarification_decision_wait'")
            )
            started.set()
            yield session

    with global_session() as lock_session:
        # Own the first canonical authority lock before the root. Otherwise the
        # waiting command owns Practice while this thread owns Root: a fabricated
        # lock inversion, not the normal concurrent correction/revocation path.
        lock_session.exec(
            select(Practice).where(Practice.id == fixture.practice.id).with_for_update()
        ).one()
        lock_session.exec(
            select(VisitPrep).where(VisitPrep.id == fixture.prep_id).with_for_update()
        ).one()
        with monkeypatch.context() as patch:
            patch.setattr(commands, "_visit_prep_session", named_session)
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(_submit, fixture, proposal)
                try:
                    assert started.wait(timeout=5)
                    _observe_waiting_lock(
                        lock_session, "vp_clarification_decision_wait"
                    )
                    if change == "correction":
                        _correct(
                            fixture,
                            question="reason",
                            answer="Invented changed while waiting.",
                        )
                    elif change == "quarantine":
                        interviews.report_wrong_recipient(
                            access_token=fixture.access_token
                        )
                    elif change == "baa":
                        fixture.practice.negotiated_baa_on_file = False
                        fixture.practice.save()
                    else:
                        rotate_visit_prep_credential(
                            owner_practice_member_id=fixture.owner.id,
                            owner_doctor_id=fixture.doctor.id,
                            practice_id=fixture.practice.id,
                            visit_prep_id=fixture.prep_id,
                            expected_credential_generation=1,
                        )
                finally:
                    lock_session.commit()
                with pytest.raises(
                    VisitPrepClarificationChanged
                    if change == "correction"
                    else VisitPrepClarificationInvalid
                ):
                    future.result(timeout=10)
    with get_session() as session:
        decisions, events, audits = _counts(session, fixture)
        assert decisions == audits == 0
        assert events == int(change == "correction")


def test_link_expiry_after_actual_wait_cannot_confirm_saved_proposal(monkeypatch):
    expiry = datetime.now(UTC) + timedelta(seconds=6)
    scheduled = expiry - timedelta(hours=72)
    source = lock_fixture(
        monkeypatch,
        "clarification-command-expiry",
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
            "reason", "answered", "Invented expiring answer."
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
    fixture, proposal, row = _proposal(monkeypatch, fixture, question="reason")
    row.save()
    started = Event()
    original = commands._visit_prep_session

    @contextmanager
    def waiting_session():
        with original() as session:
            session.execute(
                text("SET LOCAL application_name = 'vp_clarification_command_expiry'")
            )
            started.set()
            yield session

    with get_session() as parent:
        parent.exec(
            select(Practice).where(Practice.id == fixture.practice.id).with_for_update()
        ).one()
        with monkeypatch.context() as patch:
            patch.setattr(commands, "_visit_prep_session", waiting_session)
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(_submit, fixture, proposal, name="Synthetic")
                try:
                    assert started.wait(timeout=5)
                    _observe_waiting_lock(parent, "vp_clarification_command_expiry")
                    assert parent.execute(
                        text("SELECT clock_timestamp() < :expiry"), {"expiry": expiry}
                    ).scalar_one()
                    parent.execute(
                        text(
                            "SELECT pg_sleep(GREATEST(0, EXTRACT(EPOCH FROM (:expiry - clock_timestamp()))) + 0.1)"
                        ),
                        {"expiry": expiry},
                    )
                finally:
                    parent.rollback()
                with pytest.raises(VisitPrepClarificationInvalid):
                    future.result(timeout=10)
    with get_session() as session:
        assert _counts(session, fixture) == (0, 0, 0)


def test_competing_different_decisions_do_not_double_append(monkeypatch):
    fixture, proposal, row = _proposal(monkeypatch)
    row.save()
    barrier = Barrier(2)
    original = commands._visit_prep_session

    @contextmanager
    def simultaneous_session():
        with original() as session:
            barrier.wait(timeout=5)
            yield session

    monkeypatch.setattr(commands, "_visit_prep_session", simultaneous_session)
    successes, failures = [], []
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(_submit, fixture, proposal, action=action)
            for action in ("confirm", "keep_original")
        ]
        for future in futures:
            try:
                successes.append(future.result(timeout=15))
            except VisitPrepClarificationChanged as error:
                failures.append(error)
    assert len(successes) == len(failures) == 1
    with get_session() as session:
        assert _counts(session, fixture) == (
            1,
            int(successes[0].action == "confirm"),
            1,
        )


def test_final_projection_detects_mismatched_correction_words_and_rolls_back(
    monkeypatch,
):
    fixture, proposal, row = _proposal(monkeypatch)
    row.save()
    original = commands.correct_contribution_in_session

    def substituted_correction(session, **kwargs):
        return original(
            session,
            **(
                kwargs
                | {
                    "response": interviews.ResponseInput(
                        "story", "answered", "Different invented correction."
                    )
                }
            ),
        )

    monkeypatch.setattr(
        commands, "correct_contribution_in_session", substituted_correction
    )
    with pytest.raises(VisitPrepClarificationChanged):
        _submit(fixture, proposal)
    with get_session() as session:
        assert _counts(session, fixture) == (0, 0, 0)
    assert _topic(_projection(fixture), "story").answer == proposal.context.answer


def test_final_check_detects_time_dependent_entitlement_loss_after_projection(
    monkeypatch,
):
    fixture, proposal, row = _proposal(monkeypatch)
    row.save()
    entitlement_end = datetime.now(UTC) + timedelta(seconds=5)
    fixture.practice.billing_exempt_until = entitlement_end
    fixture.practice.save()
    original = commands._check_result

    def check_then_wait(session, **kwargs):
        result = original(session, **kwargs)
        session.execute(
            text(
                "SELECT pg_sleep(GREATEST(0, EXTRACT(EPOCH FROM (:end - clock_timestamp()))) + 0.1)"
            ),
            {"end": entitlement_end},
        )
        return result

    monkeypatch.setattr(commands, "_check_result", check_then_wait)
    with pytest.raises(VisitPrepClarificationInvalid):
        _submit(fixture, proposal)
    with get_session() as session:
        assert _counts(session, fixture) == (0, 0, 0)


@pytest.mark.parametrize(
    "operation", ["not-a-uuid", "11111111-1111-1111-1111-111111111111", True]
)
def test_operation_validation_never_exposes_private_error_context(
    monkeypatch, operation
):
    fixture, proposal, row = _proposal(monkeypatch)
    row.save()
    with pytest.raises(VisitPrepClarificationInvalid) as caught:
        _submit(fixture, proposal, operation=operation)
    assert caught.value.__cause__ is caught.value.__context__ is None
    with get_session() as session:
        assert _counts(session, fixture) == (0, 0, 0)


def test_confirm_forbids_edited_words_even_after_correction_staging(monkeypatch):
    fixture, proposal, row = _proposal(monkeypatch)
    row.save()
    with pytest.raises(VisitPrepClarificationInvalid):
        _submit(fixture, proposal, words="Invented unexpected edit.")
    with get_session() as session:
        assert _counts(session, fixture) == (0, 0, 0)


def test_denial_discards_staged_rows_in_reused_request_session(monkeypatch):
    fixture, proposal, row = _proposal(monkeypatch)
    row.save()
    original = commands._audit

    def audit_then_deny(*args, **kwargs):
        original(*args, **kwargs)
        raise VisitPrepClarificationInvalid()

    monkeypatch.setattr(commands, "_audit", audit_then_deny)
    with global_session() as request_session:
        with pytest.raises(VisitPrepClarificationInvalid):
            _submit(fixture, proposal)
        assert _counts(request_session, fixture) == (0, 0, 0)
        request_session.commit()
    with get_session() as observer:
        assert _counts(observer, fixture) == (0, 0, 0)
