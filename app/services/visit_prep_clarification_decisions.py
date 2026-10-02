"""Dormant patient decision command; no conversational or service authority.

The future patient route passes only a saved proposal ID and explicit choice.
This command reacquires ordinary public/recipient authority and acknowledges a
single commit of decision, exact text correction and privacy-minimized audit.
It never accepts replacement proposal/context/model evidence from the client.
Choice supplementation and model qualification remain separate unfinished work.
"""

from __future__ import annotations

import hmac
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Iterator, Literal, cast
from uuid import UUID

from typeid import TypeID

from app.services.visit_prep_clarification_contract import (
    DecisionAction,
    PatientClarificationDecision,
    VisitPrepClarificationChanged,
    VisitPrepClarificationInvalid,
    _ref,
    confirm_patient_clarification,
    select_clarification_context,
    validate_clarification_reply,
)
from app.services.visit_prep_clarification_evidence import (
    ClarificationDecisionEvidence,
    ClarificationProposalEvidence,
    read_clarification_decision,
    read_clarification_proposal,
    seal_clarification_decision,
)
from app.services.visit_prep_contribution_contract import (
    ContributionBasis,
    ContributionRole,
    PatientContribution,
)
from app.services.visit_prep_contributions import (
    _load_contribution,
    _require_recipient,
    correct_contribution_in_session,
)
from app.services.visit_prep_interview_contract import (
    VisitPrepInterviewConflict,
    VisitPrepInterviewUnavailable,
    VisitPrepInterviewValidation,
)
from app.services.visit_prep_interviews import (
    _AuthorizedInterview,
    _commit,
    _lock_authorized_interview,
    _public_question,
    _questions,
    _visit_prep_session,
)
from app.services.visit_prep_packages import _source_package_from_locked_state

from activemodel.types import TypeIDType
from app.models.authority import AuthorityAuditEvent
from app.models.visit_prep_clarification import (
    VisitPrepClarificationDecision,
    VisitPrepClarificationProposal,
)
from app.models.visit_prep_contribution import VisitPrepContributionEvent
from sqlalchemy.exc import DBAPIError
from sqlmodel import Session, select


@dataclass(frozen=True)
class PatientClarificationReceipt:
    """Value-free committed decision receipt, not clinician review or consent."""

    proposal_id: str
    decision_id: str
    action: DecisionAction
    disposition: Literal["accepted", "replayed"]
    basis: ContributionBasis
    current_answers_complete: bool
    current_answers_reviewed: bool


@contextmanager
def _decision_session() -> Iterator[Session]:
    # get_session can reuse an enclosing request/global session. A denied command
    # must discard its staged correction even when that caller catches the error
    # and keeps its session open. This command owns commit and failure rollback.
    with _visit_prep_session() as session:
        try:
            yield session
        except BaseException:
            session.rollback()
            raise


def _operation(value: object) -> str:
    if type(value) is not str:
        raise VisitPrepClarificationInvalid()
    try:
        parsed = UUID(value)
    except ValueError:
        raise VisitPrepClarificationInvalid() from None
    if str(parsed) != value or parsed.version != 4:
        raise VisitPrepClarificationInvalid()
    return value


def _authorized(
    session: Session, *, access_token: str, first_name: str, role: ContributionRole
) -> _AuthorizedInterview:
    authorized = _lock_authorized_interview(session, access_token=access_token)
    _require_recipient(authorized, first_name=first_name, role=role)
    return authorized


def _proposal(
    session: Session, *, authorized: _AuthorizedInterview, proposal_id: str
) -> tuple[VisitPrepClarificationProposal, ClarificationProposalEvidence]:
    prep = authorized.visit_prep
    row = session.exec(
        select(VisitPrepClarificationProposal).where(
            VisitPrepClarificationProposal.id == TypeID.from_string(proposal_id),
            VisitPrepClarificationProposal.visit_prep_id == prep.id,
        )
    ).one_or_none()
    if row is None:
        raise VisitPrepClarificationInvalid()
    evidence = read_clarification_proposal(row)
    question = next(
        (q for q in _questions(session, visit_prep=prep) if q.id == row.question_id),
        None,
    )
    if (
        row.owner_practice_member_id != prep.owner_practice_member_id
        or row.question_set_version_id != prep.question_set_version_id
        or row.credential_generation != authorized.credential.generation
        or row.actor_role != prep.confirmed_by_role
        or question is None
        or question.question_key != evidence.context.question_key
        or question.position != evidence.context.position
        or question.kind != evidence.context.kind
        or _public_question(question).prompt != evidence.context.prompt
    ):
        raise VisitPrepClarificationChanged()
    latest = session.exec(
        select(VisitPrepClarificationProposal)
        .where(
            VisitPrepClarificationProposal.visit_prep_id == prep.id,
            VisitPrepClarificationProposal.question_id == row.question_id,
            VisitPrepClarificationProposal.credential_generation
            == row.credential_generation,
            VisitPrepClarificationProposal.source_version == row.source_version,
            VisitPrepClarificationProposal.basis_response_count
            == row.basis_response_count,
            VisitPrepClarificationProposal.basis_event_sequence
            == row.basis_event_sequence,
        )
        .order_by(
            cast(Any, VisitPrepClarificationProposal.recorded_at).desc(),
            cast(Any, VisitPrepClarificationProposal.id).desc(),
        )
        .limit(1)
    ).one()
    if latest.id != row.id:
        raise VisitPrepClarificationChanged()
    if row.source_turn_id is not None:
        from app.services.visit_prep_clarification_turns import _verified_context

        _verified_context(session, authorized, row, evidence, require_current=False)
    return row, evidence


def _refs(
    authorized: _AuthorizedInterview,
    proposal: VisitPrepClarificationProposal,
    decision: VisitPrepClarificationDecision,
) -> list[dict[str, str]]:
    prep = authorized.visit_prep
    values = [
        ("doctor", prep.owner_doctor_id),
        ("practice", prep.practice_id),
        ("practice_member", prep.owner_practice_member_id),
        ("visit_prep", prep.id),
        ("visit_prep_question_set_version", prep.question_set_version_id),
        ("visit_prep_credential", authorized.credential.id),
        ("visit_prep_clarification", proposal.id),
    ]
    if decision.correction_id is not None:
        values.append(("visit_prep_contribution", decision.correction_id))
    return [{"kind": kind, "ref": str(ref)} for kind, ref in values]


def _audit(
    session: Session,
    *,
    authorized: _AuthorizedInterview,
    proposal: VisitPrepClarificationProposal,
    decision: VisitPrepClarificationDecision,
    at: datetime,
) -> None:
    if decision.recorded_at is None:
        raise VisitPrepClarificationInvalid()
    session.add(
        AuthorityAuditEvent(
            event_type="visit_prep.clarification_decision",
            outcome=decision.action,
            occurred_at=decision.recorded_at,
            recorded_at=at,
            client_event_id=UUID(decision.operation_id),
            resource_type="visit_prep_clarification_decision",
            resource_ref=str(decision.id),
            request_digest=proposal.payload_hmac_sha256,
            route="visit_prep_patient_clarification",
            intent="confirmed_bearer_clarification_choice",
            platform_refs=_refs(authorized, proposal, decision),
        )
    )
    session.flush()


def _check_audit(
    session: Session,
    *,
    authorized: _AuthorizedInterview,
    proposal: VisitPrepClarificationProposal,
    decision: VisitPrepClarificationDecision,
) -> None:
    rows = session.exec(
        select(AuthorityAuditEvent).where(
            AuthorityAuditEvent.resource_type == "visit_prep_clarification_decision",
            AuthorityAuditEvent.resource_ref == str(decision.id),
        )
    ).all()
    if len(rows) != 1:
        raise VisitPrepClarificationInvalid()
    audit = rows[0]
    if (
        audit.event_source != "platform_authoritative"
        or audit.event_type != "visit_prep.clarification_decision"
        or audit.outcome != decision.action
        or audit.client_event_id != UUID(decision.operation_id)
        or audit.request_digest != proposal.payload_hmac_sha256
        or audit.route != "visit_prep_patient_clarification"
        or audit.intent != "confirmed_bearer_clarification_choice"
        or audit.platform_refs != _refs(authorized, proposal, decision)
        or audit.occurred_at != decision.recorded_at
        or audit.recorded_at < audit.occurred_at
        or any(
            value is not None
            for value in (
                audit.workload_client_id,
                audit.tenant_id,
                audit.actor_id,
                audit.person_id,
                audit.interaction_id,
                audit.content_sha256,
            )
        )
    ):
        raise VisitPrepClarificationInvalid()


def _accepted_words(
    proposal: ClarificationProposalEvidence, decision: PatientClarificationDecision
) -> str | None:
    organized = validate_clarification_reply(proposal.context, proposal.reply)
    if decision.action in {"keep_original", "dismiss"}:
        if decision.edited_words is not None:
            raise VisitPrepClarificationInvalid()
        return None
    if proposal.context.kind == "choice" or organized is None:
        raise VisitPrepClarificationInvalid()
    # The codec validates exact normalized edited words and the closed action.
    return decision.edited_words if decision.action == "edit" else organized


def _check_result(
    session: Session,
    *,
    authorized: _AuthorizedInterview,
    proposal: ClarificationProposalEvidence,
    row: VisitPrepClarificationDecision,
    decision: PatientClarificationDecision,
) -> PatientContribution:
    saved = read_clarification_decision(row, proposal=proposal)
    if saved.decision != decision:
        raise VisitPrepClarificationChanged()
    current = _load_contribution(session, visit_prep=authorized.visit_prep)
    accepted = _accepted_words(proposal, decision)
    expected = replace(
        proposal.context.basis,
        event_sequence=proposal.context.basis.event_sequence
        + int(accepted is not None),
    )
    if current.basis != expected:
        raise VisitPrepClarificationChanged()
    historical_basis = _source_package_from_locked_state(
        prep=authorized.visit_prep,
        schedule=authorized.schedule,
        credential=authorized.credential,
        contribution=replace(current, basis=proposal.context.basis),
        at=authorized.event_at,
    )
    if historical_basis.source_version != proposal.context.source_version:
        raise VisitPrepClarificationChanged()
    if accepted is None:
        package = _source_package_from_locked_state(
            prep=authorized.visit_prep,
            schedule=authorized.schedule,
            credential=authorized.credential,
            contribution=current,
            at=authorized.event_at,
        )
        if replace(proposal.context, turns=()) != select_clarification_context(
            package, question_key=proposal.context.question_key
        ):
            raise VisitPrepClarificationChanged()
    else:
        event = session.get(VisitPrepContributionEvent, row.correction_id)
        topic = next(
            t for t in current.topics if t.question.key == proposal.context.question_key
        )
        if (
            event is None
            or event.event_kind != "correction"
            or event.idempotency_key != row.operation_id
            or event.actor_role != proposal.context.actor_role
            or event.credential_generation != proposal.credential_generation
            or event.basis_response_count
            != proposal.context.basis.original_response_count
            or event.basis_event_sequence != proposal.context.basis.event_sequence
            or event.recorded_at is None
            or row.recorded_at is None
            or event.recorded_at > row.recorded_at
            or not topic.applicable
            or topic.current_correction_id != row.correction_id
            or topic.response_kind != "answered"
            or topic.answer != accepted
        ):
            raise VisitPrepClarificationChanged()
    return current


def record_patient_clarification_decision(
    *,
    access_token: str,
    first_name: str,
    role: ContributionRole,
    proposal_id: str,
    operation_id: str,
    decision: PatientClarificationDecision,
) -> PatientClarificationReceipt:
    """Commit one explicit patient/caregiver choice against a saved proposal.

    No model call or route. The existing link/first-name evidence is not verified
    identity or consent. Replay is exact and fails closed if later contribution
    changes would make a saved receipt appear to describe the current answer.
    """
    receipt = None
    changed = False
    try:
        if (
            not _ref(proposal_id, {"visit_prep_clarification"})
            or type(decision) is not PatientClarificationDecision
            or type(decision.action) is not str
            or decision.action not in {"confirm", "edit", "keep_original", "dismiss"}
        ):
            raise VisitPrepClarificationInvalid()
        operation = _operation(operation_id)
        with _decision_session() as session:
            authorized = _authorized(
                session, access_token=access_token, first_name=first_name, role=role
            )
            proposal_row, proposal = _proposal(
                session, authorized=authorized, proposal_id=proposal_id
            )
            existing = session.exec(
                select(VisitPrepClarificationDecision).where(
                    VisitPrepClarificationDecision.visit_prep_id
                    == authorized.visit_prep.id,
                    (VisitPrepClarificationDecision.proposal_id == proposal_row.id)
                    | (VisitPrepClarificationDecision.operation_id == operation),
                )
            ).all()
            if existing:
                if len(existing) != 1:
                    raise VisitPrepClarificationChanged()
                row = existing[0]
                if row.proposal_id != proposal_row.id or not hmac.compare_digest(
                    row.operation_id, operation
                ):
                    raise VisitPrepClarificationChanged()
                disposition = "replayed"
            else:
                current = _load_contribution(session, visit_prep=authorized.visit_prep)
                package = _source_package_from_locked_state(
                    prep=authorized.visit_prep,
                    schedule=authorized.schedule,
                    credential=authorized.credential,
                    contribution=current,
                    at=authorized.event_at,
                )
                intent = confirm_patient_clarification(
                    proposal.context, proposal.reply, decision, current_package=package
                )
                correction_id = None
                if intent is not None:
                    if proposal.context.kind == "choice":
                        raise VisitPrepClarificationInvalid()
                    corrected = correct_contribution_in_session(
                        session,
                        access_token=access_token,
                        first_name=first_name,
                        role=role,
                        expected_basis=proposal.context.basis,
                        idempotency_key=operation,
                        response=intent.response,
                    )
                    if corrected.disposition != "accepted":
                        raise VisitPrepClarificationChanged()
                    event = session.exec(
                        select(VisitPrepContributionEvent).where(
                            VisitPrepContributionEvent.visit_prep_id
                            == authorized.visit_prep.id,
                            VisitPrepContributionEvent.idempotency_key == operation,
                        )
                    ).one()
                    correction_id = event.id
                evidence = ClarificationDecisionEvidence(
                    str(TypeID(prefix="visit_prep_clarification_decision")),
                    operation,
                    decision,
                    str(correction_id) if correction_id is not None else None,
                )
                sealed = seal_clarification_decision(evidence, proposal=proposal)
                row = VisitPrepClarificationDecision(
                    visit_prep_id=authorized.visit_prep.id,
                    proposal_id=proposal_row.id,
                    action=decision.action,
                    correction_id=correction_id,
                    operation_id=operation,
                    evidence_ciphertext=sealed.ciphertext,
                    payload_hmac_sha256=sealed.payload_hmac_sha256,
                )
                row.id = cast(TypeIDType, TypeID.from_string(evidence.decision_id))
                session.add(row)
                session.flush()
                session.refresh(row)
                final = _authorized(
                    session, access_token=access_token, first_name=first_name, role=role
                )
                _audit(
                    session,
                    authorized=final,
                    proposal=proposal_row,
                    decision=row,
                    at=final.event_at,
                )
                disposition = "accepted"
            final = _authorized(
                session, access_token=access_token, first_name=first_name, role=role
            )
            _check_audit(session, authorized=final, proposal=proposal_row, decision=row)
            current = _check_result(
                session, authorized=final, proposal=proposal, row=row, decision=decision
            )
            _authorized(
                session, access_token=access_token, first_name=first_name, role=role
            )
            result = PatientClarificationReceipt(
                proposal.proposal_id,
                str(row.id),
                decision.action,
                cast(Literal["accepted", "replayed"], disposition),
                current.basis,
                current.current_answers_complete,
                current.current_answers_reviewed,
            )
            _commit(session)
            receipt = result
    except (VisitPrepClarificationChanged, VisitPrepInterviewConflict):
        changed = True
    except (
        VisitPrepClarificationInvalid,
        VisitPrepInterviewUnavailable,
        VisitPrepInterviewValidation,
        DBAPIError,
    ):
        pass
    # Raise outside the handler so SQL/encrypted/private inputs are not chained.
    if receipt is None:
        if changed:
            raise VisitPrepClarificationChanged()
        raise VisitPrepClarificationInvalid()
    return receipt
