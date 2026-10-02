"""Audited patient read of a saved current wording proposal, not generation.

Core authorizes disclosure and owns evidence. This does not qualify the source
model/service, prove browser receipt, or grant identity, consent or bot access.
"""

from dataclasses import dataclass, field, replace
from typing import Any, Literal, cast

from app.services.visit_prep_clarification_contract import (
    PROMPTS,
    VisitPrepClarificationChanged,
    VisitPrepClarificationInvalid,
    select_clarification_context,
    validate_clarification_reply,
)
from app.services.visit_prep_clarification_decisions import (
    _authorized,
    _decision_session,
    _proposal,
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
from app.services.visit_prep_interviews import _commit, _questions
from app.services.visit_prep_packages import _source_package_from_locked_state

from app.models.authority import AuthorityAuditEvent
from app.models.visit_prep_clarification import (
    VisitPrepClarificationDecision,
    VisitPrepClarificationProposal,
)
from sqlalchemy.exc import DBAPIError
from sqlmodel import select


@dataclass(frozen=True)
class PatientClarificationTurnView:
    prompt: str = field(repr=False)
    patient_words: str = field(repr=False)


@dataclass(frozen=True)
class PatientClarificationView:
    status: Literal["available", "not_available"]
    question_key: str
    actor_role: ContributionRole
    basis: ContributionBasis
    proposal_id: str | None = None
    current_words: str | None = field(default=None, repr=False)
    organized_words: str | None = field(default=None, repr=False)
    turns: tuple[PatientClarificationTurnView, ...] = field(default=(), repr=False)


def get_patient_clarification_view(
    *, access_token: str, first_name: str, role: ContributionRole, question_key: str
) -> PatientClarificationView:
    """Return only a current undecided text organization after an audited commit.

    No proposal is created and no source/model profile supplied by a browser is
    accepted. Missing, stale, decided or non-text proposals leave manual entry
    usable. Recipient confirmation remains a bearer check, not verified identity.
    """
    result = None
    try:
        with _decision_session() as session:
            authorized = _authorized(
                session, access_token=access_token, first_name=first_name, role=role
            )
            prep = authorized.visit_prep
            question = next(
                (
                    q
                    for q in _questions(session, visit_prep=prep)
                    if q.question_key == question_key
                ),
                None,
            )
            current = _load_contribution(session, visit_prep=prep)
            topic = next(
                (t for t in current.topics if t.question.key == question_key), None
            )
            if question is None or topic is None or not topic.applicable:
                raise VisitPrepClarificationInvalid()
            package = _source_package_from_locked_state(
                prep=prep,
                schedule=authorized.schedule,
                credential=authorized.credential,
                contribution=current,
                at=authorized.event_at,
            )
            view = PatientClarificationView(
                "not_available", question_key, role, current.basis
            )
            proposal_row = None
            if topic.response_kind == "answered" and topic.question.kind != "choice":
                row = session.exec(
                    select(VisitPrepClarificationProposal)
                    .where(
                        VisitPrepClarificationProposal.visit_prep_id == prep.id,
                        VisitPrepClarificationProposal.question_id == question.id,
                        VisitPrepClarificationProposal.credential_generation
                        == authorized.credential.generation,
                        VisitPrepClarificationProposal.basis_response_count
                        == current.basis.original_response_count,
                        VisitPrepClarificationProposal.basis_event_sequence
                        == current.basis.event_sequence,
                        VisitPrepClarificationProposal.source_version
                        == package.source_version,
                    )
                    .order_by(
                        cast(Any, VisitPrepClarificationProposal.recorded_at).desc(),
                        cast(Any, VisitPrepClarificationProposal.id).desc(),
                    )
                    .limit(1)
                ).one_or_none()
                if row is not None:
                    decided = session.exec(
                        select(VisitPrepClarificationDecision).where(
                            VisitPrepClarificationDecision.proposal_id == row.id
                        )
                    ).first()
                    if decided is None:
                        proposal_row, evidence = _proposal(
                            session, authorized=authorized, proposal_id=str(row.id)
                        )
                        if replace(
                            evidence.context, turns=()
                        ) != select_clarification_context(
                            package, question_key=question_key
                        ):
                            raise VisitPrepClarificationChanged()
                        words = validate_clarification_reply(
                            evidence.context, evidence.reply
                        )
                        if evidence.reply.kind == "organize" and words is not None:
                            view = PatientClarificationView(
                                "available",
                                question_key,
                                role,
                                current.basis,
                                str(row.id),
                                evidence.context.reported_words,
                                words,
                                tuple(
                                    PatientClarificationTurnView(
                                        PROMPTS[t.prompt_code], t.patient_words
                                    )
                                    for t in evidence.context.turns
                                ),
                            )
            refs = [
                ("doctor", prep.owner_doctor_id),
                ("practice", prep.practice_id),
                ("practice_member", prep.owner_practice_member_id),
                ("visit_prep", prep.id),
                ("visit_prep_question_set_version", prep.question_set_version_id),
                ("visit_prep_credential", authorized.credential.id),
            ]
            if proposal_row is not None:
                refs.append(("visit_prep_clarification", proposal_row.id))
            session.add(
                AuthorityAuditEvent(
                    event_type="visit_prep.clarification_read",
                    outcome=view.status,
                    occurred_at=authorized.event_at,
                    recorded_at=authorized.event_at,
                    resource_type="visit_prep",
                    resource_ref=str(prep.id),
                    route="visit_prep_patient_clarification_read",
                    intent="confirmed_bearer_saved_wording_review",
                    platform_refs=[
                        {"kind": kind, "ref": str(ref)} for kind, ref in refs
                    ],
                )
            )
            session.flush()
            _authorized(
                session, access_token=access_token, first_name=first_name, role=role
            )
            _commit(session)
            result = view
    except (
        VisitPrepClarificationInvalid,
        VisitPrepClarificationChanged,
        VisitPrepInterviewUnavailable,
        VisitPrepInterviewValidation,
        DBAPIError,
    ):
        pass
    if result is None:
        raise VisitPrepClarificationInvalid()
    return result
