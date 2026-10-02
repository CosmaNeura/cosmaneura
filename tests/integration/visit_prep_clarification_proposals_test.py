"""Initial proposal command over real synthetic PostgreSQL sources.

Replies are invented fixtures, not a qualified Sovereign call. These tests
prove current-source persistence/retry and the existing patient review path,
not conversational transport, model quality or clinical acceptance.
"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from threading import Barrier, Event
from typing import Any
from uuid import UUID, uuid4

import pytest
from typeid import TypeID

import app.services.visit_prep_clarification_decisions as decisions
import app.services.visit_prep_clarification_proposals as commands
from app.configuration.visit_prep import VisitPrepSettings
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
from app.services.visit_prep_clarification_view import get_patient_clarification_view
from app.services.visit_prep_contribution_contract import ContributionBasis
from app.services.visit_preps import rotate_visit_prep_credential

from activemodel.session_manager import get_session, global_session
from app.models.authority import AuthorityAuditEvent
from app.models.practice import Practice
from app.models.visit_prep import VisitPrep
from app.models.visit_prep_clarification import (
    VisitPrepClarificationDecision,
    VisitPrepClarificationProposal,
)
from sqlalchemy import text, update
from sqlmodel import SQLModel, select

from tests.integration import visit_prep_clarification_http_test as http
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


def _submit(fixture, context, *, reply=None, operation=None, **changes):
    arguments: dict[str, Any] = dict(
        access_token=fixture.access_token,
        first_name="José",
        role=context.actor_role,
        operation_id=operation if operation is not None else str(uuid4()),
        context=context,
        reply=reply if reply is not None else _organize(context),
        model_profile="synthetic.proposal-v1",
    )
    return commands.record_initial_clarification_proposal(**(arguments | changes))


def _rows(session, fixture):
    return session.exec(
        select(VisitPrepClarificationProposal).where(
            VisitPrepClarificationProposal.visit_prep_id == fixture.prep_id
        )
    ).all()


def _audits(session, fixture):
    return [
        row
        for row in session.exec(
            select(AuthorityAuditEvent).where(
                AuthorityAuditEvent.event_type == "visit_prep.clarification_proposed"
            )
        ).all()
        if {"kind": "visit_prep", "ref": str(fixture.prep_id)} in row.platform_refs
    ]


def _counts(session, fixture):
    return len(_rows(session, fixture)), len(_audits(session, fixture))


@pytest.mark.parametrize("role", ["patient", "caregiver"])
@pytest.mark.parametrize("kind", ["organize", "clarify", "unavailable"])
def test_initial_reply_is_encrypted_committed_and_exactly_replayable(
    monkeypatch, role, kind
):
    fixture, context = _context(monkeypatch, role=role)
    reply = (
        _organize(context)
        if kind == "organize"
        else PatientClarificationReply(
            kind=kind,
            prompt_code="detail" if kind == "clarify" else None,
            statements=(),
        )
    )
    operation = str(uuid4())
    before = _projection(fixture)
    receipt = _submit(fixture, context, reply=reply, operation=operation)
    replay = _submit(fixture, context, reply=reply, operation=operation)
    assert receipt.disposition == "accepted" and replay.disposition == "replayed"
    assert receipt.proposal_id == replay.proposal_id and receipt.basis == context.basis
    assert set(asdict(receipt)) == {"proposal_id", "disposition", "basis"}
    assert "invented" not in repr(receipt).lower()
    assert _projection(fixture) == before
    with get_session() as session:
        assert _counts(session, fixture) == (1, 1)
        row = _rows(session, fixture)[0]
        evidence = read_clarification_proposal(row)
        assert evidence.context == context and evidence.reply == reply
        assert evidence.context.turns == ()
        assert evidence.operation_id == operation
        assert evidence.model_profile == "synthetic.proposal-v1"
        assert context.answer not in row.evidence_ciphertext
        assert row.created_at == row.recorded_at
        audit = _audits(session, fixture)[0]
        assert audit.occurred_at == row.recorded_at
        assert audit.recorded_at >= audit.occurred_at
        assert audit.client_event_id == UUID(operation)
        assert "invented" not in repr(audit).lower()
        assert all(
            value is None
            for value in (
                audit.actor_id,
                audit.person_id,
                audit.tenant_id,
                audit.workload_client_id,
                audit.interaction_id,
                audit.content_sha256,
                audit.request_digest,
            )
        )
    view = get_patient_clarification_view(
        access_token=fixture.access_token,
        first_name="José",
        role=role,
        question_key=context.question_key,
    )
    assert view.status == ("available" if kind == "organize" else "not_available")
    assert view.turns == ()
    if kind == "organize":
        assert view.proposal_id == receipt.proposal_id
        assert view.current_words == view.organized_words == context.answer


@pytest.mark.parametrize("role", ["patient", "caregiver"])
@pytest.mark.parametrize("action", ["confirm", "edit", "keep_original", "dismiss"])
def test_saved_initial_proposal_connects_to_existing_explicit_patient_choice(
    monkeypatch, role, action
):
    fixture, context = _context(monkeypatch, role=role)
    saved = _submit(fixture, context)
    words = "Invented explicit patient revision." if action == "edit" else None
    receipt = decisions.record_patient_clarification_decision(
        access_token=fixture.access_token,
        first_name="José",
        role=role,
        proposal_id=saved.proposal_id,
        operation_id=str(uuid4()),
        decision=PatientClarificationDecision(action, words),
    )
    topic = _topic(_projection(fixture), "story")
    assert topic.answer == (words if action == "edit" else context.answer)
    assert topic.original_answer == context.answer
    assert receipt.basis.event_sequence == int(action in {"confirm", "edit"})
    with pytest.raises(VisitPrepClarificationChanged):
        _submit(fixture, context, operation=_saved_operation(fixture))


def _saved_operation(fixture):
    with get_session() as session:
        return _rows(session, fixture)[0].operation_id


@pytest.mark.parametrize("question", ["reason", "story", "medications"])
def test_initial_proposal_uses_latest_text_or_list_source(monkeypatch, question):
    fixture = _prepared(monkeypatch, "initial-text-source")
    _correct(fixture, question=question, answer="Invented current source.")
    context = select_clarification_context(_read(fixture), question_key=question)
    receipt = _submit(fixture, context)
    with get_session() as session:
        row = session.get(
            VisitPrepClarificationProposal, TypeID.from_string(receipt.proposal_id)
        )
        assert row is not None and row.source_response_id is None
        assert str(row.source_contribution_id) == context.current_source_ref
        assert (
            read_clarification_proposal(row).context.answer
            == "Invented current source."
        )


def test_choice_can_request_clarification_but_cannot_organize_over_coded_value(
    monkeypatch,
):
    fixture, context = _context(monkeypatch, question="adherence")
    assert context.answer == "yes" and context.reported_words != context.answer
    with pytest.raises(VisitPrepClarificationInvalid):
        _submit(fixture, context)
    _submit(
        fixture,
        context,
        reply=PatientClarificationReply(
            kind="clarify", prompt_code="detail", statements=()
        ),
    )
    assert _topic(_projection(fixture), "adherence").answer == "yes"
    with get_session() as session:
        assert _counts(session, fixture) == (1, 1)


@pytest.mark.parametrize(
    "field",
    [
        "package_id",
        "source_version",
        "question_set_version_id",
        "basis",
        "actor_role",
        "question_key",
        "position",
        "kind",
        "prompt",
        "answer",
        "current_source_ref",
    ],
)
def test_valid_but_substituted_context_cannot_be_saved(monkeypatch, field):
    fixture, context = _context(monkeypatch)
    values = {
        "package_id": str(TypeID(prefix="visit_prep")),
        "source_version": "0" * 64,
        "question_set_version_id": str(
            TypeID(prefix="visit_prep_question_set_version")
        ),
        "basis": ContributionBasis(4, 1),
        "actor_role": "caregiver",
        "question_key": "reason",
        "position": 1,
        "kind": "list",
        "prompt": "Invented substitute prompt.",
        "answer": "Invented substituted answer.",
        "current_source_ref": str(TypeID(prefix="visit_prep_response")),
    }
    changes = {field: values[field]}
    if field == "answer":
        changes["reported_words"] = values[field]
    altered = replace(context, **changes)
    with pytest.raises(VisitPrepClarificationChanged) as caught:
        _submit(fixture, altered, role="patient")
    assert caught.value.__cause__ is caught.value.__context__ is None
    with get_session() as session:
        assert _counts(session, fixture) == (0, 0)


@pytest.mark.parametrize("change", ["correction", "review"])
@pytest.mark.parametrize("replay", [False, True])
def test_changed_source_denies_new_save_and_replay(monkeypatch, change, replay):
    fixture, context = _context(monkeypatch)
    operation = str(uuid4())
    if replay:
        _submit(fixture, context, operation=operation)
    _correct(fixture) if change == "correction" else _review(fixture)
    with pytest.raises(VisitPrepClarificationChanged):
        _submit(fixture, context, operation=operation)
    with get_session() as session:
        assert _counts(session, fixture) == (int(replay), int(replay))


def test_initial_caller_cannot_claim_that_patient_supplied_a_clarification_turn(
    monkeypatch,
):
    fixture, original = _context(monkeypatch)
    context = append_patient_clarification(
        original,
        PatientClarificationReply(kind="clarify", prompt_code="detail", statements=()),
        patient_words="Invented words not captured by a patient command.",
    )
    with pytest.raises(VisitPrepClarificationInvalid):
        _submit(fixture, context)
    with get_session() as session:
        assert _counts(session, fixture) == (0, 0)


@pytest.mark.parametrize("change", ["model", "reply", "question"])
def test_reused_operation_cannot_replace_saved_evidence(monkeypatch, change):
    fixture, context = _context(monkeypatch)
    operation = str(uuid4())
    _submit(fixture, context, operation=operation)
    arguments: dict[str, Any] = (
        {"model_profile": "synthetic.other-v2"} if change == "model" else {}
    )
    if change == "reply":
        arguments["reply"] = PatientClarificationReply(
            kind="unavailable", prompt_code=None, statements=()
        )
    if change == "question":
        context = select_clarification_context(_read(fixture), question_key="reason")
    with pytest.raises(VisitPrepClarificationChanged):
        _submit(fixture, context, operation=operation, **arguments)
    with get_session() as session:
        assert _counts(session, fixture) == (1, 1)
        assert (
            read_clarification_proposal(_rows(session, fixture)[0]).reply.kind
            == "organize"
        )


def test_later_explicit_request_supersedes_but_does_not_mutate_prior_proposal(
    monkeypatch,
):
    fixture, context = _context(monkeypatch)
    operation = str(uuid4())
    first = _submit(fixture, context, operation=operation)
    second = _submit(fixture, context)
    assert first.proposal_id != second.proposal_id
    with pytest.raises(VisitPrepClarificationChanged):
        _submit(fixture, context, operation=operation)
    view = get_patient_clarification_view(
        access_token=fixture.access_token,
        first_name="José",
        role="patient",
        question_key="story",
    )
    assert view.proposal_id == second.proposal_id
    with get_session() as session:
        assert _counts(session, fixture) == (2, 2)
        assert all(
            read_clarification_proposal(row).context == context
            for row in _rows(session, fixture)
        )


@pytest.mark.parametrize("action", ["confirm", "edit", "keep_original", "dismiss"])
def test_superseded_proposal_cannot_be_decided_from_an_old_review(monkeypatch, action):
    fixture, context = _context(monkeypatch)
    first = _submit(fixture, context)
    second = _submit(fixture, context)
    with pytest.raises(VisitPrepClarificationChanged):
        decisions.record_patient_clarification_decision(
            access_token=fixture.access_token,
            first_name="José",
            role="patient",
            proposal_id=first.proposal_id,
            operation_id=str(uuid4()),
            decision=PatientClarificationDecision(
                action, "Invented old screen edit." if action == "edit" else None
            ),
        )
    assert _projection(fixture).basis == context.basis
    with get_session() as session:
        assert _counts(session, fixture) == (2, 2)
        assert not session.exec(
            select(VisitPrepClarificationDecision).where(
                VisitPrepClarificationDecision.visit_prep_id == fixture.prep_id
            )
        ).all()
    view = get_patient_clarification_view(
        access_token=fixture.access_token,
        first_name="José",
        role="patient",
        question_key="story",
    )
    assert view.proposal_id == second.proposal_id


@pytest.mark.parametrize(
    "failure",
    [
        "name",
        "role",
        "public",
        "cohort",
        "baa",
        "entitlement",
        "membership",
        "doctor",
        "quarantine",
        "rotation",
    ],
)
@pytest.mark.parametrize("replay", [False, True])
def test_authority_is_rechecked_before_decrypting_or_saving(
    monkeypatch, failure, replay
):
    fixture = _prepared(monkeypatch, "initial-authority", complete=False)
    context = select_clarification_context(_read(fixture), question_key="reason")
    operation = str(uuid4())
    if replay:
        _submit(fixture, context, operation=operation)
    changes = {}
    if failure in {"name", "role"}:
        changes = (
            {"first_name": "Incorrect"} if failure == "name" else {"role": "caregiver"}
        )
    elif failure in {"public", "cohort"}:
        monkeypatch.setattr(
            interviews.visit_prep_access,
            "VISIT_PREP_SETTINGS",
            VisitPrepSettings(
                enabled=True,
                public_api_enabled=failure != "public",
                enabled_doctor_ids=frozenset()
                if failure == "cohort"
                else frozenset({str(fixture.doctor.id)}),
            ),
        )
    elif failure == "quarantine":
        interviews.report_wrong_recipient(access_token=fixture.access_token)
    elif failure == "rotation":
        rotate_visit_prep_credential(
            owner_practice_member_id=fixture.owner.id,
            owner_doctor_id=fixture.doctor.id,
            practice_id=fixture.practice.id,
            visit_prep_id=fixture.prep_id,
            expected_credential_generation=1,
        )
    else:
        with get_session() as session:
            now = datetime.now(UTC)
            if failure == "membership":
                table = SQLModel.metadata.tables["practice_member"]
                values = dict(
                    access_revoked_at=now,
                    access_revoked_by_ref="synthetic-proposal-offboarding",
                    access_revocation_reason="Synthetic proposal authority check",
                    access_records_retain_until=now + timedelta(days=365 * 8),
                )
                target = fixture.owner.id
            elif failure == "doctor":
                table, values, target = (
                    SQLModel.metadata.tables["doctor"],
                    {"deleted_at": now},
                    fixture.doctor.id,
                )
            else:
                table, target = (
                    SQLModel.metadata.tables["practice"],
                    fixture.practice.id,
                )
                values = (
                    {"negotiated_baa_on_file": False}
                    if failure == "baa"
                    else {"billing_exempt": False}
                )
            session.execute(update(table).where(table.c.id == target).values(**values))
            session.commit()
    decoded = []
    monkeypatch.setattr(
        commands, "_load_contribution", lambda *_a, **_k: decoded.append("source")
    )
    monkeypatch.setattr(
        commands,
        "read_clarification_proposal",
        lambda *_a, **_k: decoded.append("evidence"),
    )
    with pytest.raises(VisitPrepClarificationInvalid) as caught:
        _submit(fixture, context, operation=operation, **changes)
    assert decoded == []
    assert caught.value.__cause__ is caught.value.__context__ is None
    with get_session() as session:
        assert _counts(session, fixture) == (int(replay), int(replay))


@pytest.mark.parametrize("failure", ["operation", "profile", "quote"])
def test_invalid_inputs_are_private_and_leave_no_rows(monkeypatch, failure):
    fixture, context = _context(monkeypatch)
    changes = (
        {"operation": "private-invalid-operation"}
        if failure == "operation"
        else {"model_profile": "private profile text"}
        if failure == "profile"
        else {
            "reply": PatientClarificationReply(
                kind="organize",
                prompt_code=None,
                statements=(
                    PatientQuoteSpan(
                        source="answer", start=0, end=1, quote="Invented false quote."
                    ),
                ),
            )
        }
    )
    with pytest.raises(VisitPrepClarificationInvalid) as caught:
        _submit(fixture, context, **changes)
    assert caught.value.__cause__ is caught.value.__context__ is None
    assert "private" not in str(caught.value) and "Invented" not in str(caught.value)
    with get_session() as session:
        assert _counts(session, fixture) == (0, 0)


@pytest.mark.parametrize(
    "failure", ["seal", "readback", "audit", "final_authority", "commit_acknowledgment"]
)
def test_failed_save_rolls_back_or_replays_an_acknowledgment_loss(monkeypatch, failure):
    fixture, context = _context(monkeypatch)
    operation = str(uuid4())
    original_commit, original_check = commands._commit, commands._audit_valid

    def fail(*_a, **_k):
        raise VisitPrepClarificationInvalid()

    if failure in {"seal", "readback"}:
        monkeypatch.setattr(
            commands,
            "seal_clarification_proposal"
            if failure == "seal"
            else "read_clarification_proposal",
            fail,
        )
    elif failure == "audit":
        monkeypatch.setattr(commands, "_audit_valid", lambda *_a, **_k: False)
    elif failure == "final_authority":

        def check_then_disable(*args):
            result = original_check(*args)
            monkeypatch.setattr(
                interviews.visit_prep_access, "VISIT_PREP_SETTINGS", VisitPrepSettings()
            )
            return result

        monkeypatch.setattr(commands, "_audit_valid", check_then_disable)
    else:

        def commit_without_ack(session):
            original_commit(session)
            raise VisitPrepClarificationInvalid()

        monkeypatch.setattr(commands, "_commit", commit_without_ack)
    with pytest.raises(VisitPrepClarificationInvalid):
        _submit(fixture, context, operation=operation)
    durable = int(failure == "commit_acknowledgment")
    with get_session() as session:
        assert _counts(session, fixture) == (durable, durable)
    if durable:
        monkeypatch.setattr(commands, "_commit", original_commit)
        assert _submit(fixture, context, operation=operation).disposition == "replayed"


def test_proposal_and_audit_are_not_visible_until_one_commit(monkeypatch):
    fixture, context = _context(monkeypatch)
    original = commands._commit
    observed = []

    def inspect_then_commit(session):
        assert _counts(session, fixture) == (1, 1)
        with get_session() as observer:
            assert _counts(observer, fixture) == (0, 0)
        original(session)
        with get_session() as observer:
            observed.append(_counts(observer, fixture))

    monkeypatch.setattr(commands, "_commit", inspect_then_commit)
    _submit(fixture, context)
    assert observed == [(1, 1)]


def test_failed_proposal_discards_staged_rows_in_an_enclosing_session(monkeypatch):
    fixture, context = _context(monkeypatch)
    monkeypatch.setattr(commands, "_audit_valid", lambda *_a, **_k: False)
    with global_session() as request_session:
        with pytest.raises(VisitPrepClarificationInvalid):
            _submit(fixture, context)
        assert _counts(request_session, fixture) == (0, 0)
        request_session.commit()
    with get_session() as observer:
        assert _counts(observer, fixture) == (0, 0)


def test_concurrent_identical_proposals_commit_one_row_and_audit(monkeypatch):
    fixture, context = _context(monkeypatch)
    operation, barrier = str(uuid4()), Barrier(2)
    original = decisions._visit_prep_session

    @contextmanager
    def simultaneous_session():
        with original() as session:
            barrier.wait(timeout=5)
            yield session

    monkeypatch.setattr(decisions, "_visit_prep_session", simultaneous_session)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(_submit, fixture, context, operation=operation)
            for _ in range(2)
        ]
        receipts = [f.result(timeout=15) for f in futures]
    assert sorted(r.disposition for r in receipts) == ["accepted", "replayed"]
    assert len({r.proposal_id for r in receipts}) == 1
    with get_session() as session:
        assert _counts(session, fixture) == (1, 1)


@pytest.mark.parametrize("change", ["correction", "quarantine", "baa", "rotation"])
def test_actual_lock_wait_rechecks_changed_source_or_authority(monkeypatch, change):
    fixture = _prepared(monkeypatch, "initial-command-wait", complete=False)
    context = select_clarification_context(_read(fixture), question_key="reason")
    started, original = Event(), decisions._visit_prep_session

    @contextmanager
    def named_session():
        with original() as session:
            session.execute(
                text("SET LOCAL application_name = 'vp_initial_proposal_wait'")
            )
            started.set()
            yield session

    with global_session() as parent:
        parent.exec(
            select(Practice).where(Practice.id == fixture.practice.id).with_for_update()
        ).one()
        parent.exec(
            select(VisitPrep).where(VisitPrep.id == fixture.prep_id).with_for_update()
        ).one()
        with monkeypatch.context() as patch:
            patch.setattr(decisions, "_visit_prep_session", named_session)
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(_submit, fixture, context)
                try:
                    assert started.wait(timeout=5)
                    _observe_waiting_lock(parent, "vp_initial_proposal_wait")
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
                    parent.commit()
                with pytest.raises(
                    VisitPrepClarificationChanged
                    if change == "correction"
                    else VisitPrepClarificationInvalid
                ):
                    future.result(timeout=10)
    with get_session() as session:
        assert _counts(session, fixture) == (0, 0)


def test_final_database_time_check_denies_entitlement_expired_during_save(monkeypatch):
    fixture, context = _context(monkeypatch)
    entitlement_end = datetime.now(UTC) + timedelta(seconds=3)
    fixture.practice.billing_exempt_until = entitlement_end
    fixture.practice.save()
    original = commands._audit_valid

    def check_then_wait(session, *args):
        result = original(session, *args)
        session.execute(
            text(
                "SELECT pg_sleep(GREATEST(0, EXTRACT(EPOCH FROM (:end - clock_timestamp()))) + 0.1)"
            ),
            {"end": entitlement_end},
        )
        return result

    monkeypatch.setattr(commands, "_audit_valid", check_then_wait)
    with pytest.raises(VisitPrepClarificationInvalid):
        _submit(fixture, context)
    with get_session() as session:
        assert _counts(session, fixture) == (0, 0)


@pytest.mark.parametrize("role", ["patient", "caregiver"])
@pytest.mark.parametrize("action", ["confirm", "edit", "keep_original", "dismiss"])
def test_initial_command_saved_proposal_is_reviewable_through_actual_public_api(
    monkeypatch, owned_rate_limit_redis, role, action
):
    fixture, context = _context(monkeypatch, role=role)
    saved = _submit(fixture, context)
    client = http._client(monkeypatch, owned_rate_limit_redis)
    response = http._read(client, fixture, role)
    assert response.status_code == 200
    view = response.json()
    assert view["proposal_id"] == saved.proposal_id and view["turns"] == []
    assert view["current_words"] == view["organized_words"] == context.answer
    payload = dict(
        first_name="José",
        role=role,
        proposal_id=saved.proposal_id,
        action=action,
        edited_words="Invented public patient edit." if action == "edit" else None,
    )
    operation = str(uuid4())
    result = client.post(
        http.PREFIX + "/decision",
        headers=http._headers(fixture, operation),
        json=payload,
    )
    replay = client.post(
        http.PREFIX + "/decision",
        headers=http._headers(fixture, operation),
        json=payload,
    )
    assert result.status_code == replay.status_code == 200
    assert (
        result.json()["disposition"] == "accepted"
        and replay.json()["disposition"] == "replayed"
    )
    assert result.json()["decision_id"] == replay.json()["decision_id"]
    topic = _topic(_projection(fixture), "story")
    assert topic.original_answer == context.answer
    assert topic.answer == (
        payload["edited_words"] if action == "edit" else context.answer
    )
    for acknowledged in (result, replay):
        http._private(acknowledged, fixture)
        assert "words" not in acknowledged.text


def test_link_expiry_after_real_authority_lock_wait_cannot_save_initial_proposal(
    monkeypatch,
):
    expiry = datetime.now(UTC) + timedelta(seconds=6)
    scheduled = expiry - timedelta(hours=72)
    source = lock_fixture(
        monkeypatch,
        "initial-proposal-expiry",
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
            "reason", "answered", "Invented expiring proposal source."
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
    started, original = Event(), decisions._visit_prep_session

    @contextmanager
    def named_session():
        with original() as session:
            session.execute(
                text("SET LOCAL application_name = 'vp_initial_proposal_expiry'")
            )
            started.set()
            yield session

    with get_session() as parent:
        parent.exec(
            select(Practice).where(Practice.id == fixture.practice.id).with_for_update()
        ).one()
        with monkeypatch.context() as patch:
            patch.setattr(decisions, "_visit_prep_session", named_session)
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(
                    _submit, fixture, context, first_name="Synthetic"
                )
                try:
                    assert started.wait(timeout=5)
                    _observe_waiting_lock(parent, "vp_initial_proposal_expiry")
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
        assert _counts(session, fixture) == (0, 0)
