"""Core capture of patient-supplied follow-up words, never model-authored turns.

An explicit patient action answers one saved neutral request. It does not accept
replacement context or change the original answer. Success requires one commit
of immutable evidence and an authority audit; current source and recipient
checks apply to new work and exact retries alike.
"""

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Literal, cast
from uuid import UUID

from typeid import TypeID

from app.services.visit_prep_clarification_contract import (
    VisitPrepClarificationChanged,
    VisitPrepClarificationContext,
    VisitPrepClarificationInvalid,
    _ref,
)
from app.services.visit_prep_clarification_decisions import (
    _authorized,
    _decision_session,
    _operation,
    _proposal,
)
from app.services.visit_prep_clarification_evidence import (
    ClarificationProposalEvidence,
    read_clarification_proposal,
)
from app.services.visit_prep_clarification_proposals import (
    _audit_valid,
    _current_context,
)
from app.services.visit_prep_clarification_turn_evidence import (
    ClarificationTurnEvidence,
    read_clarification_turn,
    seal_clarification_turn,
)
from app.services.visit_prep_contribution_contract import (
    ContributionBasis,
    ContributionRole,
)
from app.services.visit_prep_interview_contract import (
    VisitPrepInterviewUnavailable,
    VisitPrepInterviewValidation,
)
from app.services.visit_prep_interviews import _AuthorizedInterview, _commit
from app.services.visit_prep_patient_continuation import ConfirmedVisitPrepAccess

from activemodel.types import TypeIDType
from app.models.authority import AuthorityAuditEvent
from app.models.visit_prep_clarification import (
    VisitPrepClarificationDecision,
    VisitPrepClarificationProposal,
)
from app.models.visit_prep_clarification_turn import VisitPrepClarificationTurn
from sqlalchemy.exc import DBAPIError
from sqlmodel import Session, select


@dataclass(frozen=True)
class PatientClarificationTurnReceipt:
    turn_id: str
    proposal_id: str
    disposition: Literal["accepted", "replayed"]
    turn_sequence: int
    basis: ContributionBasis


def _refs(
    authorized: _AuthorizedInterview, row: VisitPrepClarificationTurn
) -> list[dict[str, str]]:
    prep = authorized.visit_prep
    return [
        {"kind": kind, "ref": str(ref)}
        for kind, ref in (
            ("doctor", prep.owner_doctor_id),
            ("practice", prep.practice_id),
            ("practice_member", prep.owner_practice_member_id),
            ("visit_prep", prep.id),
            ("visit_prep_question_set_version", prep.question_set_version_id),
            ("visit_prep_credential", authorized.credential.id),
            ("visit_prep_clarification", row.proposal_id),
            ("visit_prep_clarification_turn", row.id),
        )
    ]


def _check_audit(
    session: Session, authorized: _AuthorizedInterview, row: VisitPrepClarificationTurn
) -> None:
    audits = session.exec(
        select(AuthorityAuditEvent).where(
            AuthorityAuditEvent.resource_type == "visit_prep_clarification_turn",
            AuthorityAuditEvent.resource_ref == str(row.id),
        )
    ).all()
    if len(audits) != 1 or row.recorded_at is None:
        raise VisitPrepClarificationInvalid()
    audit = audits[0]
    if not (
        audit.event_type == "visit_prep.clarification_turn"
        and audit.event_source == "platform_authoritative"
        and audit.outcome == "retained"
        and audit.route == "visit_prep_patient_clarification_turn"
        and audit.intent == "confirmed_bearer_patient_words"
        and audit.client_event_id == UUID(row.operation_id)
        and audit.occurred_at == row.recorded_at
        and audit.recorded_at >= row.recorded_at
        and audit.platform_refs == _refs(authorized, row)
        and all(
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
    ):
        raise VisitPrepClarificationInvalid()


def _verified_context(
    session: Session,
    authorized: _AuthorizedInterview,
    row: VisitPrepClarificationProposal,
    evidence: ClarificationProposalEvidence,
    *,
    depth: int = 0,
    require_current: bool = True,
) -> VisitPrepClarificationContext:
    """Reconstruct at most two turns from actual patient-command evidence.

    Ancestors need not still be the latest proposal. Their immutable words and
    audit must bind to the same current source and credential. No context supplied
    by a model is accepted as evidence of a patient's response.
    """
    if (
        depth > 2
        or require_current
        and replace(evidence.context, turns=())
        != _current_context(session, authorized, evidence.context.question_key)
    ):
        raise VisitPrepClarificationChanged()
    if (
        row.owner_practice_member_id != authorized.visit_prep.owner_practice_member_id
        or row.credential_generation != authorized.credential.generation
        or row.question_set_version_id != authorized.visit_prep.question_set_version_id
        or not _audit_valid(session, authorized, row)
    ):
        raise VisitPrepClarificationInvalid()
    if row.source_turn_id is None:
        if evidence.context.turns:
            raise VisitPrepClarificationInvalid()
        return evidence.context
    turn = session.exec(
        select(VisitPrepClarificationTurn).where(
            VisitPrepClarificationTurn.id == row.source_turn_id,
            VisitPrepClarificationTurn.visit_prep_id == authorized.visit_prep.id,
        )
    ).one_or_none()
    if turn is None:
        raise VisitPrepClarificationInvalid()
    prior = session.exec(
        select(VisitPrepClarificationProposal).where(
            VisitPrepClarificationProposal.id == turn.proposal_id,
            VisitPrepClarificationProposal.visit_prep_id == authorized.visit_prep.id,
        )
    ).one_or_none()
    if (
        prior is None
        or prior.recorded_at is None
        or turn.recorded_at is None
        or row.recorded_at is None
        or not prior.recorded_at <= turn.recorded_at <= row.recorded_at
    ):
        raise VisitPrepClarificationInvalid()
    prior_evidence = read_clarification_proposal(prior)
    _verified_context(
        session,
        authorized,
        prior,
        prior_evidence,
        depth=depth + 1,
        require_current=require_current,
    )
    _check_audit(session, authorized, turn)
    updated = read_clarification_turn(turn, proposal=prior_evidence).context(
        prior_evidence
    )
    if updated != evidence.context:
        raise VisitPrepClarificationInvalid()
    return updated


def record_patient_clarification_turn(
    *,
    access_token: str | ConfirmedVisitPrepAccess,
    first_name: str,
    role: ContributionRole,
    proposal_id: str,
    operation_id: str,
    patient_words: str,
) -> PatientClarificationTurnReceipt:
    result = None
    changed = False
    try:
        operation = _operation(operation_id)
        if not _ref(proposal_id, {"visit_prep_clarification"}):
            raise VisitPrepClarificationInvalid()
        with _decision_session() as session:
            authorized = _authorized(
                session, access_token=access_token, first_name=first_name, role=role
            )
            proposal, source = _proposal(
                session, authorized=authorized, proposal_id=proposal_id
            )
            _verified_context(session, authorized, proposal, source)
            if (
                session.exec(
                    select(VisitPrepClarificationDecision).where(
                        VisitPrepClarificationDecision.proposal_id == proposal.id
                    )
                ).first()
                is not None
            ):
                raise VisitPrepClarificationChanged()
            row = session.exec(
                select(VisitPrepClarificationTurn).where(
                    VisitPrepClarificationTurn.visit_prep_id
                    == authorized.visit_prep.id,
                    VisitPrepClarificationTurn.operation_id == operation,
                )
            ).one_or_none()
            existing = row is not None
            evidence = ClarificationTurnEvidence(
                str(row.id)
                if row
                else str(TypeID(prefix="visit_prep_clarification_turn")),
                operation,
                patient_words,
            )
            updated = evidence.context(source)
            evidence.manifest(source)
            if row is not None:
                if read_clarification_turn(row, proposal=source) != evidence:
                    raise VisitPrepClarificationChanged()
            else:
                if (
                    session.exec(
                        select(VisitPrepClarificationTurn).where(
                            VisitPrepClarificationTurn.proposal_id == proposal.id
                        )
                    ).first()
                    is not None
                ):
                    raise VisitPrepClarificationChanged()
                sealed = seal_clarification_turn(evidence, proposal=source)
                row = VisitPrepClarificationTurn(
                    visit_prep_id=authorized.visit_prep.id,
                    proposal_id=proposal.id,
                    turn_sequence=len(updated.turns),
                    operation_id=operation,
                    evidence_ciphertext=sealed.ciphertext,
                    payload_hmac_sha256=sealed.payload_hmac_sha256,
                )
                row.id = cast(TypeIDType, TypeID.from_string(evidence.turn_id))
                session.add(row)
                session.flush()
                session.refresh(row)
                final = _authorized(
                    session, access_token=access_token, first_name=first_name, role=role
                )
                session.add(
                    AuthorityAuditEvent(
                        event_type="visit_prep.clarification_turn",
                        outcome="retained",
                        occurred_at=cast(datetime, row.recorded_at),
                        recorded_at=final.event_at,
                        client_event_id=UUID(operation),
                        resource_type="visit_prep_clarification_turn",
                        resource_ref=str(row.id),
                        route="visit_prep_patient_clarification_turn",
                        intent="confirmed_bearer_patient_words",
                        platform_refs=_refs(final, row),
                    )
                )
                session.flush()
            final = _authorized(
                session, access_token=access_token, first_name=first_name, role=role
            )
            fresh_row, fresh_source = _proposal(
                session, authorized=final, proposal_id=proposal_id
            )
            _verified_context(session, final, fresh_row, fresh_source)
            if (
                source != fresh_source
                or read_clarification_turn(row, proposal=source) != evidence
            ):
                raise VisitPrepClarificationChanged()
            _check_audit(session, final, row)
            _authorized(
                session, access_token=access_token, first_name=first_name, role=role
            )
            receipt = PatientClarificationTurnReceipt(
                str(row.id),
                proposal_id,
                "replayed" if existing else "accepted",
                row.turn_sequence,
                updated.basis,
            )
            _commit(session)
            result = receipt
    except VisitPrepClarificationChanged:
        changed = True
    except (
        VisitPrepClarificationInvalid,
        VisitPrepInterviewUnavailable,
        VisitPrepInterviewValidation,
        DBAPIError,
    ):
        pass
    if result is None:
        if changed:
            raise VisitPrepClarificationChanged()
        raise VisitPrepClarificationInvalid()
    return result
