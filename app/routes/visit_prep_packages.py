"""Provider-only source package; explicit projection, no bearer or model output."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, status
from pydantic import Field

from app.routes.dependencies.visit_prep import require_visit_prep_package_read
from app.routes.visit_prep_preparations import (
    _COMMON_ERROR_RESPONSES,
    VisitPrepCommandStateResponse,
    _invoke,
    _scope,
    _SensitiveVisitPrepRoute,
    _state_response,
    _StrictModel,
    _type_id,
)
from app.services import visit_prep_access
from app.services import visit_prep_packages as packages
from app.services.visit_prep_access import VisitPrepAccessDecision
from app.services.visit_prep_email_contract import VisitPrepEmailInvalid
from app.services.visit_prep_email_status import (
    EmailOutcomeCode,
    EmailStatus,
    get_email_status,
)


class VisitPrepPackageBasisResponse(_StrictModel):
    original_response_count: int = Field(ge=0, le=5, strict=True)
    event_sequence: int = Field(ge=0, strict=True)


class VisitPrepPackageChoiceResponse(_StrictModel):
    value: str
    label: str


class VisitPrepPackageTopicResponse(_StrictModel):
    question_key: str
    position: int = Field(ge=1, le=5)
    prompt: str
    kind: Literal["story", "list", "choice"]
    choices: list[VisitPrepPackageChoiceResponse]
    response_kind: Literal["answered", "skipped"] | None
    answer: str | None = Field(repr=False)
    initial_response_kind: Literal["answered", "skipped"] | None
    initial_answer: str | None = Field(repr=False)
    changed: bool
    original_response_id: str | None
    additional_answer_source_id: str | None
    current_revision_id: str | None
    recorded_at: datetime | None
    initial_recorded_at: datetime | None
    explanation_words: str | None = Field(default=None, repr=False)
    explanation_source_id: str | None = None
    explanation_recorded_at: datetime | None = None


class VisitPrepPatientSourcesResponse(_StrictModel):
    contract: Literal["visit_prep.patient_sources.v1"] = "visit_prep.patient_sources.v1"
    package_id: str
    source_version: str = Field(pattern=r"^[0-9a-f]{64}$")
    preparation: VisitPrepCommandStateResponse
    basis: VisitPrepPackageBasisResponse
    actor_role: Literal["patient", "caregiver"]
    original_interview_completed: bool
    current_answers_complete: bool
    current_answers_reviewed: bool
    current_review_id: str | None
    reviewed_at: datetime | None
    missing_question_keys: list[str]
    skipped_question_keys: list[str]
    topics: list[VisitPrepPackageTopicResponse] = Field(repr=False)


class VisitPrepPackageResponse(VisitPrepPatientSourcesResponse):
    # Retain the existing source-only endpoint's wire contract. Synthesis has
    # its own atomic source/brief projection, not a second read of this route.
    synthesis_status: Literal["not_generated"] = "not_generated"


class VisitPrepListResponse(_StrictModel):
    preparations: list[VisitPrepCommandStateResponse]
    next_before_id: str | None


class VisitPrepEmailStatusResponse(_StrictModel):
    contract: Literal["visit_prep.email_status.v1"] = "visit_prep.email_status.v1"
    preparation_id: str
    status: EmailStatus
    outcome_code: EmailOutcomeCode | None
    current_credential_generation: int = Field(gt=0, strict=True)
    credential_generation: int | None
    context_current: bool | None
    invitation_expired: bool | None
    submission_window_elapsed: bool | None
    requested_at: datetime | None
    expires_at: datetime | None
    submitted_at: datetime | None
    finalized_at: datetime | None
    checked_at: datetime
    dispatch_integration: Literal["default_off_worker"] = "default_off_worker"


visit_prep_packages_api_app = APIRouter(
    prefix="/visit-prep/preps",
    tags=["visit-prep"],
    route_class=_SensitiveVisitPrepRoute,
    responses=_COMMON_ERROR_RESPONSES,
)


def _query_keys(request: Request, allowed: frozenset[str]) -> None:
    keys = list(request.query_params.keys())
    if any(
        key not in allowed or len(request.query_params.getlist(key)) != 1
        for key in keys
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Visit Prep request is invalid.",
        )


@visit_prep_packages_api_app.get(
    "/{prep_id}/email-status",
    name="visit_prep_email_status",
    response_model=VisitPrepEmailStatusResponse,
)
def visit_prep_email_status(
    request: Request,
    prep_id: Annotated[str, Path(max_length=80)],
    decision: Annotated[
        VisitPrepAccessDecision, Depends(require_visit_prep_package_read)
    ],
) -> VisitPrepEmailStatusResponse:
    _query_keys(request, frozenset())
    owner, doctor, practice = _scope(decision)
    identifier = _type_id(prep_id, prefix="visit_prep", path=True)
    value = None
    try:
        value = get_email_status(
            owner_practice_member_id=owner,
            owner_doctor_id=doctor,
            practice_id=practice,
            expected_clerk_subject=request.state.doctor.clerk_id,
            visit_prep_id=identifier,
            settings=visit_prep_access.VISIT_PREP_SETTINGS,
            view_as_active=getattr(request.state, "view_as", None) is not None,
        )
    except VisitPrepEmailInvalid:
        pass
    if value is None:
        raise HTTPException(404, "Not found")
    return VisitPrepEmailStatusResponse(**asdict(value))


@visit_prep_packages_api_app.get(
    "", name="visit_prep_list", response_model=VisitPrepListResponse
)
def visit_prep_list(
    request: Request,
    decision: Annotated[
        VisitPrepAccessDecision, Depends(require_visit_prep_package_read)
    ],
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    before_id: Annotated[str | None, Query(max_length=80)] = None,
) -> VisitPrepListResponse:
    _query_keys(request, frozenset({"limit", "before_id"}))
    owner_id, doctor_id, practice_id = _scope(decision)
    expected_subject = request.state.doctor.clerk_id
    result = _invoke(
        lambda: packages.list_visit_preps(
            owner_practice_member_id=owner_id,
            owner_doctor_id=doctor_id,
            practice_id=practice_id,
            expected_clerk_subject=expected_subject,
            limit=limit,
            before_id=_type_id(before_id, prefix="visit_prep", path=True)
            if before_id
            else None,
        )
    )
    return VisitPrepListResponse(
        preparations=[_state_response(prep) for prep in result.preparations],
        next_before_id=str(result.next_before_id) if result.next_before_id else None,
    )


@visit_prep_packages_api_app.get(
    "/{prep_id}/package",
    name="visit_prep_package",
    response_model=VisitPrepPackageResponse,
)
def visit_prep_package(
    request: Request,
    prep_id: Annotated[str, Path(max_length=80)],
    decision: Annotated[
        VisitPrepAccessDecision, Depends(require_visit_prep_package_read)
    ],
) -> VisitPrepPackageResponse:
    _query_keys(request, frozenset())
    owner_id, doctor_id, practice_id = _scope(decision)
    expected_subject = request.state.doctor.clerk_id
    result = _invoke(
        lambda: packages.get_visit_prep_package(
            owner_practice_member_id=owner_id,
            owner_doctor_id=doctor_id,
            practice_id=practice_id,
            expected_clerk_subject=expected_subject,
            visit_prep_id=_type_id(prep_id, prefix="visit_prep", path=True),
        )
    )
    return VisitPrepPackageResponse(**_source_response(result).model_dump())


def _source_response(
    result: packages.VisitPrepSourcePackage,
) -> VisitPrepPatientSourcesResponse:
    """Serialize an already-authorized snapshot without a second DB read."""
    contribution = result.contribution
    # Hidden topics are retained internally but never enter this wire response,
    # including their original wording, revisions or source identifiers.
    topics = [topic for topic in contribution.topics if topic.applicable]
    return VisitPrepPatientSourcesResponse(
        package_id=str(result.preparation.id),
        source_version=result.source_version,
        preparation=_state_response(result.preparation),
        basis=VisitPrepPackageBasisResponse(
            original_response_count=contribution.basis.original_response_count,
            event_sequence=contribution.basis.event_sequence,
        ),
        actor_role=contribution.actor_role,
        original_interview_completed=contribution.original_interview_completed,
        current_answers_complete=contribution.current_answers_complete,
        current_answers_reviewed=contribution.current_answers_reviewed,
        current_review_id=str(contribution.current_review_id)
        if contribution.current_review_id
        else None,
        reviewed_at=contribution.reviewed_at,
        missing_question_keys=list(contribution.missing_question_keys),
        skipped_question_keys=[
            topic.question.key for topic in topics if topic.response_kind == "skipped"
        ],
        topics=[
            VisitPrepPackageTopicResponse(
                question_key=topic.question.key,
                position=topic.question.position,
                prompt=topic.question.prompt,
                kind=topic.question.kind,
                choices=[
                    VisitPrepPackageChoiceResponse(
                        value=choice.value, label=choice.label
                    )
                    for choice in topic.question.choices
                ],
                response_kind=topic.response_kind,
                answer=topic.answer,
                initial_response_kind=topic.original_response_kind
                if topic.original_response_id
                else topic.additional_response_kind,
                initial_answer=topic.original_answer
                if topic.original_response_id
                else topic.additional_answer,
                changed=topic.current_correction_id is not None
                and topic.current_correction_id != topic.additional_answer_source_id,
                original_response_id=str(topic.original_response_id)
                if topic.original_response_id
                else None,
                additional_answer_source_id=str(topic.additional_answer_source_id)
                if topic.additional_answer_source_id
                else None,
                current_revision_id=str(topic.current_correction_id)
                if topic.current_correction_id
                else None,
                recorded_at=topic.recorded_at,
                initial_recorded_at=topic.original_recorded_at
                if topic.original_response_id
                else topic.additional_recorded_at,
                explanation_words=topic.explanation_words,
                explanation_source_id=str(topic.explanation_id)
                if topic.explanation_words is not None
                else None,
                explanation_recorded_at=topic.explanation_recorded_at,
            )
            for topic in topics
        ],
    )
