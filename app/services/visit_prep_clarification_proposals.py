"""Core's bounded-proposal persistence, not a model runtime or service grant.

A future qualified conversational caller supplies a bounded reply after model
work outside this transaction. Core freshly checks ordinary patient authority
and the exact current single-topic source before saving it. No browser route,
model call or claimed patient clarification turn is accepted here.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, cast
from uuid import UUID

from typeid import TypeID

from app.services.visit_prep_clarification_contract import (
    PatientClarificationReply,
    VisitPrepClarificationChanged,
    VisitPrepClarificationContext,
    VisitPrepClarificationInvalid,
    _ref,
    select_clarification_context,
    validate_clarification_context,
    validate_clarification_reply,
)
from app.services.visit_prep_clarification_decisions import (
    _authorized,
    _decision_session,
    _operation,
)
from app.services.visit_prep_clarification_evidence import (
    ClarificationProposalEvidence,
    read_clarification_proposal,
    seal_clarification_proposal,
)
from app.services.visit_prep_contribution_contract import (
    ContributionBasis,
    ContributionRole,
)
from app.services.visit_prep_contributions import _load_contribution
from app.services.visit_prep_interview_contract import (
    VisitPrepInterviewUnavailable,
    VisitPrepInterviewValidation,
)
from app.services.visit_prep_interviews import _AuthorizedInterview, _commit, _questions
from app.services.visit_prep_packages import _source_package_from_locked_state
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
class InitialClarificationProposalReceipt:
    proposal_id: str
    disposition: Literal["accepted", "replayed"]
    basis: ContributionBasis


def _current_context(
    session: Session, authorized: _AuthorizedInterview, question_key: str
) -> VisitPrepClarificationContext:
    contribution = _load_contribution(session, visit_prep=authorized.visit_prep)
    package = _source_package_from_locked_state(
        prep=authorized.visit_prep,
        schedule=authorized.schedule,
        credential=authorized.credential,
        contribution=contribution,
        at=authorized.event_at,
    )
    return select_clarification_context(package, question_key=question_key)


def _refs(
    authorized: _AuthorizedInterview, row: VisitPrepClarificationProposal
) -> list[dict[str, str]]:
    prep = authorized.visit_prep
    values = [
        {"kind": kind, "ref": str(ref)}
        for kind, ref in (
            ("doctor", prep.owner_doctor_id),
            ("practice", prep.practice_id),
            ("practice_member", prep.owner_practice_member_id),
            ("visit_prep", prep.id),
            ("visit_prep_question_set_version", prep.question_set_version_id),
            ("visit_prep_credential", authorized.credential.id),
            ("visit_prep_clarification", row.id),
        )
    ]
    if row.source_turn_id is not None:
        values.append(
            {"kind": "visit_prep_clarification_turn", "ref": str(row.source_turn_id)}
        )
    return values


def _audit_valid(
    session: Session,
    authorized: _AuthorizedInterview,
    row: VisitPrepClarificationProposal,
) -> bool:
    audits = session.exec(
        select(AuthorityAuditEvent).where(
            AuthorityAuditEvent.event_type == "visit_prep.clarification_proposed",
            AuthorityAuditEvent.resource_ref == str(row.id),
        )
    ).all()
    if len(audits) != 1 or row.recorded_at is None:
        return False
    audit = audits[0]
    return (
        audit.event_source == "platform_authoritative"
        and audit.resource_type == "visit_prep_clarification"
        and audit.outcome == "retained"
        and audit.client_event_id == UUID(row.operation_id)
        and audit.route
        == (
            "visit_prep_followup_clarification_proposal"
            if row.source_turn_id is not None
            else "visit_prep_initial_clarification_proposal"
        )
        and audit.intent == "current_confirmed_bearer_source"
        and audit.occurred_at == row.recorded_at
        and audit.recorded_at >= audit.occurred_at
        and audit.platform_refs == _refs(authorized, row)
        and all(
            value is None
            for value in (
                audit.workload_client_id,
                audit.tenant_id,
                audit.actor_id,
                audit.person_id,
                audit.interaction_id,
                audit.content_sha256,
                audit.request_digest,
            )
        )
    )


def _record_clarification_proposal(
    *,
    access_token: str | ConfirmedVisitPrepAccess,
    first_name: str,
    role: ContributionRole,
    operation_id: str,
    context: VisitPrepClarificationContext,
    reply: PatientClarificationReply,
    model_profile: str,
    source_turn_id: str | None = None,
) -> InitialClarificationProposalReceipt:
    """Save a reply to the exact current answer, then acknowledge the commit.

    Initial context has zero turns. Follow-up context must exactly match
    independently recorded patient words; the service cannot invent them.
    The model profile is provenance, not qualification or permission.
    This private domain function does not authorize its future
    conversational transport or expose a route for caller-supplied model output.
    """
    result = None
    changed = False
    try:
        operation = _operation(operation_id)
        validate_clarification_context(context)
        if source_turn_id is None and context.turns:
            raise VisitPrepClarificationInvalid()
        if source_turn_id is not None and not _ref(
            source_turn_id, {"visit_prep_clarification_turn"}
        ):
            raise VisitPrepClarificationInvalid()
        with _decision_session() as session:
            authorized = _authorized(
                session, access_token=access_token, first_name=first_name, role=role
            )
            if context != _proposal_context(
                session, authorized, context.question_key, source_turn_id
            ):
                raise VisitPrepClarificationChanged()
            validate_clarification_reply(context, reply)
            if context.kind == "choice" and reply.kind == "organize":
                raise VisitPrepClarificationInvalid()
            prep = authorized.visit_prep
            question = next(
                q
                for q in _questions(session, visit_prep=prep)
                if q.question_key == context.question_key
            )
            row = session.exec(
                select(VisitPrepClarificationProposal).where(
                    VisitPrepClarificationProposal.visit_prep_id == prep.id,
                    VisitPrepClarificationProposal.operation_id == operation,
                )
            ).one_or_none()
            existing = row is not None
            evidence = ClarificationProposalEvidence(
                str(row.id)
                if row is not None
                else str(TypeID(prefix="visit_prep_clarification")),
                str(prep.owner_practice_member_id),
                str(question.id),
                authorized.credential.generation,
                operation,
                model_profile,
                context,
                reply,
                source_turn_id,
            )
            # Validate all closed provenance fields, after recipient authority.
            evidence.manifest()
            if row is not None:
                if read_clarification_proposal(row) != evidence or str(
                    row.source_turn_id
                ) != str(source_turn_id):
                    raise VisitPrepClarificationChanged()
            else:
                sealed = seal_clarification_proposal(evidence)
                source = TypeID.from_string(context.current_source_ref)
                row = VisitPrepClarificationProposal(
                    visit_prep_id=prep.id,
                    owner_practice_member_id=prep.owner_practice_member_id,
                    question_set_version_id=prep.question_set_version_id,
                    question_id=question.id,
                    credential_generation=authorized.credential.generation,
                    actor_role=role,
                    basis_response_count=context.basis.original_response_count,
                    basis_event_sequence=context.basis.event_sequence,
                    source_version=context.source_version,
                    source_response_id=cast(TypeIDType, source)
                    if source.prefix == "visit_prep_response"
                    else None,
                    source_contribution_id=cast(TypeIDType, source)
                    if source.prefix == "visit_prep_contribution"
                    else None,
                    operation_id=operation,
                    source_turn_id=cast(TypeIDType, TypeID.from_string(source_turn_id))
                    if source_turn_id is not None
                    else None,
                    evidence_ciphertext=sealed.ciphertext,
                    payload_hmac_sha256=sealed.payload_hmac_sha256,
                )
                row.id = cast(TypeIDType, TypeID.from_string(evidence.proposal_id))
                session.add(row)
                session.flush()
                session.refresh(row)
                final = _authorized(
                    session, access_token=access_token, first_name=first_name, role=role
                )
                session.add(
                    AuthorityAuditEvent(
                        event_type="visit_prep.clarification_proposed",
                        outcome="retained",
                        occurred_at=cast(datetime, row.recorded_at),
                        recorded_at=final.event_at,
                        client_event_id=UUID(operation),
                        resource_type="visit_prep_clarification",
                        resource_ref=str(row.id),
                        route="visit_prep_followup_clarification_proposal"
                        if source_turn_id is not None
                        else "visit_prep_initial_clarification_proposal",
                        intent="current_confirmed_bearer_source",
                        platform_refs=_refs(final, row),
                    )
                )
                session.flush()
            final = _authorized(
                session, access_token=access_token, first_name=first_name, role=role
            )
            if context != _proposal_context(
                session, final, context.question_key, source_turn_id
            ):
                raise VisitPrepClarificationChanged()
            latest = session.exec(
                select(VisitPrepClarificationProposal)
                .where(
                    VisitPrepClarificationProposal.visit_prep_id == prep.id,
                    VisitPrepClarificationProposal.question_id == question.id,
                    VisitPrepClarificationProposal.source_version
                    == context.source_version,
                    VisitPrepClarificationProposal.credential_generation
                    == final.credential.generation,
                    VisitPrepClarificationProposal.basis_response_count
                    == context.basis.original_response_count,
                    VisitPrepClarificationProposal.basis_event_sequence
                    == context.basis.event_sequence,
                )
                .order_by(
                    cast(Any, VisitPrepClarificationProposal.recorded_at).desc(),
                    cast(Any, VisitPrepClarificationProposal.id).desc(),
                )
                .limit(1)
            ).one()
            decided = session.exec(
                select(VisitPrepClarificationDecision).where(
                    VisitPrepClarificationDecision.proposal_id == row.id
                )
            ).first()
            if latest.id != row.id or decided is not None:
                raise VisitPrepClarificationChanged()
            if read_clarification_proposal(row) != evidence or not _audit_valid(
                session, final, row
            ):
                raise VisitPrepClarificationInvalid()
            _authorized(
                session, access_token=access_token, first_name=first_name, role=role
            )
            receipt = InitialClarificationProposalReceipt(
                str(row.id), "replayed" if existing else "accepted", context.basis
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


def _proposal_context(
    session: Session,
    authorized: _AuthorizedInterview,
    question_key: str,
    source_turn_id: str | None,
) -> VisitPrepClarificationContext:
    if source_turn_id is None:
        return _current_context(session, authorized, question_key)
    # Local import avoids a module cycle; patient-turn capture reuses only the
    # original current-source selector above, not this model-reply writer.
    from app.services.visit_prep_clarification_turn_evidence import (
        read_clarification_turn,
    )
    from app.services.visit_prep_clarification_turns import (
        _check_audit,
        _verified_context,
    )

    turn = session.exec(
        select(VisitPrepClarificationTurn).where(
            VisitPrepClarificationTurn.id == TypeID.from_string(source_turn_id),
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
    ).one()
    evidence = read_clarification_proposal(prior)
    _verified_context(session, authorized, prior, evidence)
    _check_audit(session, authorized, turn)
    latest = session.exec(
        select(VisitPrepClarificationProposal)
        .where(
            VisitPrepClarificationProposal.visit_prep_id == authorized.visit_prep.id,
            VisitPrepClarificationProposal.question_id == prior.question_id,
            VisitPrepClarificationProposal.source_version == prior.source_version,
            VisitPrepClarificationProposal.credential_generation
            == prior.credential_generation,
            VisitPrepClarificationProposal.basis_response_count
            == prior.basis_response_count,
            VisitPrepClarificationProposal.basis_event_sequence
            == prior.basis_event_sequence,
        )
        .order_by(
            cast(Any, VisitPrepClarificationProposal.recorded_at).desc(),
            cast(Any, VisitPrepClarificationProposal.id).desc(),
        )
        .limit(1)
    ).one()
    if latest.id != prior.id and latest.source_turn_id != turn.id:
        raise VisitPrepClarificationChanged()
    if (
        session.exec(
            select(VisitPrepClarificationDecision).where(
                VisitPrepClarificationDecision.proposal_id == latest.id
            )
        ).first()
        is not None
    ):
        raise VisitPrepClarificationChanged()
    if evidence.context.question_key != question_key:
        raise VisitPrepClarificationChanged()
    return read_clarification_turn(turn, proposal=evidence).context(evidence)


def record_initial_clarification_proposal(
    *,
    access_token: str | ConfirmedVisitPrepAccess,
    first_name: str,
    role: ContributionRole,
    operation_id: str,
    context: VisitPrepClarificationContext,
    reply: PatientClarificationReply,
    model_profile: str,
) -> InitialClarificationProposalReceipt:
    """Retain an initial reply; no model-supplied patient turns are permitted."""
    return _record_clarification_proposal(
        access_token=access_token,
        first_name=first_name,
        role=role,
        operation_id=operation_id,
        context=context,
        reply=reply,
        model_profile=model_profile,
    )


def record_followup_clarification_proposal(
    *,
    access_token: str | ConfirmedVisitPrepAccess,
    first_name: str,
    role: ContributionRole,
    operation_id: str,
    source_turn_id: str,
    context: VisitPrepClarificationContext,
    reply: PatientClarificationReply,
    model_profile: str,
) -> InitialClarificationProposalReceipt:
    """Retain a bounded reply only against independently recorded patient words.

    This private operation grants no Sovereign/service boundary or model access.
    The caller's context must exactly match Core's current source and saved turns.
    """
    return _record_clarification_proposal(
        access_token=access_token,
        first_name=first_name,
        role=role,
        operation_id=operation_id,
        context=context,
        reply=reply,
        model_profile=model_profile,
        source_turn_id=source_turn_id,
    )
