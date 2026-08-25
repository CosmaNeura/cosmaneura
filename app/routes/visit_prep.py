"""Internal physician API for versioned Visit Prep question sets.

Patient access and response collection deliberately do not live in this router.
Every operation is scoped by the server-resolved PracticeMember in the Visit
Prep access decision; caller-supplied ownership identifiers are never accepted.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from datetime import datetime
from typing import Annotated, Any, Literal, TypeVar, cast

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Path,
    Request,
    Response,
    status,
)
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import JSONResponse
from typeid import TypeID
from typeid.errors import TypeIDException

import app.services.visit_prep_question_sets as question_set_service
from app import log
from app.routes.dependencies.visit_prep import (
    require_visit_prep_read,
    require_visit_prep_write,
)
from app.services.visit_prep_access import VisitPrepAccessDecision
from app.services.visit_prep_question_sets import (
    QuestionChoiceInput,
    QuestionConditionInput,
    QuestionDraftInput,
    QuestionSetMutationResult,
    QuestionSetResult,
    QuestionSetVersionInput,
    QuestionSetVersionResult,
    VisitPrepQuestionSetConflict,
    VisitPrepQuestionSetNotFound,
    VisitPrepQuestionSetValidation,
)

from activemodel.types import TypeIDType
from sqlalchemy.exc import SQLAlchemyError

_NOT_FOUND_DETAIL = "Not found"
_VALIDATION_DETAIL = "Question-set definition is invalid."
_CONFLICT_DETAIL = "Question-set operation conflicts with its current state."
_UNAVAILABLE_DETAIL = "Visit Prep question sets are temporarily unavailable."
_VERSION_PREFIX = "visit_prep_question_set_version"

_ResultT = TypeVar("_ResultT")


class _VisitPrepRoute(APIRoute):
    """Keep rejected Visit Prep inputs out of response bodies.

    FastAPI's default request-validation response includes the rejected value.
    Question-set definitions can contain sensitive authored text, so this route
    class handles validation raised while resolving dependencies, headers, path
    parameters, or bodies before the application-wide handler can echo it.
    """

    def get_route_handler(
        self,
    ) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        route_handler = super().get_route_handler()

        async def redacted_route_handler(request: Request) -> Response:
            try:
                return await route_handler(request)
            except RequestValidationError:
                return JSONResponse(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    content={"detail": _VALIDATION_DETAIL},
                )

        return redacted_route_handler


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class VisitPrepQuestionSetErrorResponse(_StrictModel):
    detail: str


_COMMON_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    status.HTTP_403_FORBIDDEN: {
        "model": VisitPrepQuestionSetErrorResponse,
        "description": "The authenticated context cannot perform this operation.",
    },
    status.HTTP_404_NOT_FOUND: {
        "model": VisitPrepQuestionSetErrorResponse,
        "description": "Visit Prep is unavailable or the owner-scoped resource was not found.",
    },
    status.HTTP_409_CONFLICT: {
        "model": VisitPrepQuestionSetErrorResponse,
        "description": "The operation conflicts with the current question-set state.",
    },
    status.HTTP_422_UNPROCESSABLE_ENTITY: {
        "model": VisitPrepQuestionSetErrorResponse,
        "description": "The request or question-set definition is invalid.",
    },
    status.HTTP_503_SERVICE_UNAVAILABLE: {
        "model": VisitPrepQuestionSetErrorResponse,
        "description": "Question-set persistence is temporarily unavailable.",
    },
}


visit_prep_api_app = APIRouter(
    prefix="/visit-prep",
    tags=["visit-prep"],
    route_class=_VisitPrepRoute,
    responses=_COMMON_ERROR_RESPONSES,
)


class VisitPrepQuestionChoiceRequest(_StrictModel):
    value: str
    label: str
    hint: str | None = None


class VisitPrepQuestionConditionRequest(_StrictModel):
    question_key: str
    values: list[str]


class VisitPrepQuestionDraftRequest(_StrictModel):
    key: str
    prompt: str
    kind: Literal["story", "list", "choice"]
    helper_text: str | None = None
    choices: list[VisitPrepQuestionChoiceRequest] = Field(default_factory=list)
    show_if: VisitPrepQuestionConditionRequest | None = None


class VisitPrepQuestionSetCreateRequest(_StrictModel):
    name: str
    questions: list[VisitPrepQuestionDraftRequest] = Field(default_factory=list)


class VisitPrepQuestionSetReplaceRequest(VisitPrepQuestionSetCreateRequest):
    expected_updated_at: datetime


class VisitPrepQuestionSetSuccessorRequest(_StrictModel):
    name: str | None = None


class VisitPrepQuestionSetPublishRequest(_StrictModel):
    make_default: bool = False


class VisitPrepQuestionChoiceResponse(_StrictModel):
    value: str
    label: str
    hint: str | None


class VisitPrepQuestionConditionResponse(_StrictModel):
    question_id: str
    question_key: str
    values: list[str]


class VisitPrepQuestionResponse(_StrictModel):
    id: str
    question_set_version_id: str
    key: str
    position: int
    prompt: str
    helper_text: str | None
    kind: Literal["story", "list", "choice"]
    choices: list[VisitPrepQuestionChoiceResponse]
    show_if: VisitPrepQuestionConditionResponse | None
    created_at: datetime | None
    updated_at: datetime | None


class VisitPrepQuestionSetVersionResponse(_StrictModel):
    id: str
    question_set_id: str
    version_number: int
    name: str
    supersedes_version_id: str | None
    published_at: datetime | None
    is_default: bool
    created_at: datetime | None
    updated_at: datetime | None
    questions: list[VisitPrepQuestionResponse]


class VisitPrepQuestionSetResponse(_StrictModel):
    id: str
    created_at: datetime | None
    updated_at: datetime | None
    versions: list[VisitPrepQuestionSetVersionResponse]


class VisitPrepQuestionSetListResponse(_StrictModel):
    question_sets: list[VisitPrepQuestionSetResponse]


class VisitPrepQuestionSetMutationResponse(_StrictModel):
    question_set: VisitPrepQuestionSetResponse
    version: VisitPrepQuestionSetVersionResponse
    disposition: Literal["created", "updated", "replayed"]


def _owner_id(decision: VisitPrepAccessDecision) -> TypeIDType:
    owner_id = decision.practice_member_id
    if owner_id is None:
        # Eligibility requires a live PracticeMember. Preserve the route's
        # non-disclosing response if that invariant is ever violated.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_NOT_FOUND_DETAIL,
        )
    return owner_id


def _version_id(value: str) -> TypeIDType:
    try:
        parsed = TypeID.from_string(value)
        if parsed.prefix != _VERSION_PREFIX:
            raise ValueError
    except (TypeIDException, ValueError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_NOT_FOUND_DETAIL,
        ) from None
    return cast(TypeIDType, parsed)


def _invoke(operation: Callable[[], _ResultT]) -> _ResultT:
    try:
        return operation()
    except VisitPrepQuestionSetNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_NOT_FOUND_DETAIL,
        ) from exc
    except VisitPrepQuestionSetValidation as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=_VALIDATION_DETAIL,
        ) from exc
    except VisitPrepQuestionSetConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_CONFLICT_DETAIL,
        ) from exc
    except SQLAlchemyError as exc:
        # Never log authored prompts, constraint text, or the database error.
        log.warning(
            "Visit Prep question-set persistence unavailable",
            action="visit_prep.question_set_persistence_unavailable",
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_UNAVAILABLE_DETAIL,
        ) from exc


def _definition(
    data: VisitPrepQuestionSetCreateRequest | VisitPrepQuestionSetReplaceRequest,
) -> QuestionSetVersionInput:
    return QuestionSetVersionInput(
        name=data.name,
        questions=tuple(
            QuestionDraftInput(
                key=question.key,
                prompt=question.prompt,
                kind=question.kind,
                helper_text=question.helper_text,
                choices=tuple(
                    QuestionChoiceInput(
                        value=choice.value,
                        label=choice.label,
                        hint=choice.hint,
                    )
                    for choice in question.choices
                ),
                show_if=(
                    QuestionConditionInput(
                        question_key=question.show_if.question_key,
                        values=tuple(question.show_if.values),
                    )
                    if question.show_if is not None
                    else None
                ),
            )
            for question in data.questions
        ),
    )


def _version_response(
    result: QuestionSetVersionResult,
) -> VisitPrepQuestionSetVersionResponse:
    return VisitPrepQuestionSetVersionResponse(
        id=str(result.id),
        question_set_id=str(result.question_set_id),
        version_number=result.version_number,
        name=result.name,
        supersedes_version_id=(
            str(result.supersedes_version_id)
            if result.supersedes_version_id is not None
            else None
        ),
        published_at=result.published_at,
        is_default=result.is_default,
        created_at=result.created_at,
        updated_at=result.updated_at,
        questions=[
            VisitPrepQuestionResponse(
                id=str(question.id),
                question_set_version_id=str(question.question_set_version_id),
                key=question.key,
                position=question.position,
                prompt=question.prompt,
                helper_text=question.helper_text,
                kind=question.kind,
                choices=[
                    VisitPrepQuestionChoiceResponse(
                        value=choice.value,
                        label=choice.label,
                        hint=choice.hint,
                    )
                    for choice in question.choices
                ],
                show_if=(
                    VisitPrepQuestionConditionResponse(
                        question_id=str(question.show_if.question_id),
                        question_key=question.show_if.question_key,
                        values=list(question.show_if.values),
                    )
                    if question.show_if is not None
                    else None
                ),
                created_at=question.created_at,
                updated_at=question.updated_at,
            )
            for question in result.questions
        ],
    )


def _question_set_response(result: QuestionSetResult) -> VisitPrepQuestionSetResponse:
    return VisitPrepQuestionSetResponse(
        id=str(result.id),
        created_at=result.created_at,
        updated_at=result.updated_at,
        versions=[_version_response(version) for version in result.versions],
    )


def _mutation_response(
    result: QuestionSetMutationResult,
) -> VisitPrepQuestionSetMutationResponse:
    return VisitPrepQuestionSetMutationResponse(
        question_set=_question_set_response(result.question_set),
        version=_version_response(result.version),
        disposition=result.disposition,
    )


@visit_prep_api_app.get(
    "/question-sets",
    name="visit_prep_question_set_list",
    response_model=VisitPrepQuestionSetListResponse,
)
def question_set_list(
    decision: Annotated[VisitPrepAccessDecision, Depends(require_visit_prep_read)],
) -> VisitPrepQuestionSetListResponse:
    owner_id = _owner_id(decision)
    if decision.can_manage:
        _invoke(
            lambda: question_set_service.ensure_default_question_set(
                owner_practice_member_id=owner_id,
            )
        )
    question_sets = _invoke(
        lambda: question_set_service.list_question_sets(
            owner_practice_member_id=owner_id,
        )
    )
    return VisitPrepQuestionSetListResponse(
        question_sets=[_question_set_response(result) for result in question_sets]
    )


@visit_prep_api_app.get(
    "/question-set-versions/{version_id}",
    name="visit_prep_question_set_version",
    response_model=VisitPrepQuestionSetVersionResponse,
)
def question_set_version(
    version_id: Annotated[str, Path()],
    decision: Annotated[VisitPrepAccessDecision, Depends(require_visit_prep_read)],
) -> VisitPrepQuestionSetVersionResponse:
    result = _invoke(
        lambda: question_set_service.get_question_set_version(
            owner_practice_member_id=_owner_id(decision),
            version_id=_version_id(version_id),
        )
    )
    return _version_response(result)


@visit_prep_api_app.post(
    "/question-sets",
    name="visit_prep_question_set_create",
    response_model=VisitPrepQuestionSetMutationResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_200_OK: {
            "model": VisitPrepQuestionSetMutationResponse,
            "description": "An idempotent replay of the original creation.",
        }
    },
)
def question_set_create(
    data: VisitPrepQuestionSetCreateRequest,
    response: Response,
    decision: Annotated[VisitPrepAccessDecision, Depends(require_visit_prep_write)],
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=36, max_length=36),
    ],
) -> VisitPrepQuestionSetMutationResponse:
    result = _invoke(
        lambda: question_set_service.create_question_set(
            owner_practice_member_id=_owner_id(decision),
            idempotency_key=idempotency_key,
            definition=_definition(data),
        )
    )
    if result.disposition == "replayed":
        response.status_code = status.HTTP_200_OK
    return _mutation_response(result)


@visit_prep_api_app.put(
    "/question-set-versions/{version_id}",
    name="visit_prep_question_set_replace",
    response_model=VisitPrepQuestionSetMutationResponse,
)
def question_set_replace(
    version_id: Annotated[str, Path()],
    data: VisitPrepQuestionSetReplaceRequest,
    decision: Annotated[VisitPrepAccessDecision, Depends(require_visit_prep_write)],
) -> VisitPrepQuestionSetMutationResponse:
    result = _invoke(
        lambda: question_set_service.replace_question_set_draft(
            owner_practice_member_id=_owner_id(decision),
            version_id=_version_id(version_id),
            expected_updated_at=data.expected_updated_at,
            definition=_definition(data),
        )
    )
    return _mutation_response(result)


@visit_prep_api_app.post(
    "/question-set-versions/{version_id}/successor",
    name="visit_prep_question_set_successor",
    response_model=VisitPrepQuestionSetMutationResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_200_OK: {
            "model": VisitPrepQuestionSetMutationResponse,
            "description": "An idempotent replay of the existing successor.",
        }
    },
)
def question_set_successor(
    version_id: Annotated[str, Path()],
    response: Response,
    decision: Annotated[VisitPrepAccessDecision, Depends(require_visit_prep_write)],
    data: VisitPrepQuestionSetSuccessorRequest | None = None,
) -> VisitPrepQuestionSetMutationResponse:
    result = _invoke(
        lambda: question_set_service.create_successor_draft(
            owner_practice_member_id=_owner_id(decision),
            source_version_id=_version_id(version_id),
            name=data.name if data is not None else None,
        )
    )
    if result.disposition == "replayed":
        response.status_code = status.HTTP_200_OK
    return _mutation_response(result)


@visit_prep_api_app.post(
    "/question-set-versions/{version_id}/publish",
    name="visit_prep_question_set_publish",
    response_model=VisitPrepQuestionSetMutationResponse,
)
def question_set_publish(
    version_id: Annotated[str, Path()],
    decision: Annotated[VisitPrepAccessDecision, Depends(require_visit_prep_write)],
    data: VisitPrepQuestionSetPublishRequest | None = None,
) -> VisitPrepQuestionSetMutationResponse:
    result = _invoke(
        lambda: question_set_service.publish_question_set_version(
            owner_practice_member_id=_owner_id(decision),
            version_id=_version_id(version_id),
            make_default=data.make_default if data is not None else False,
        )
    )
    return _mutation_response(result)


@visit_prep_api_app.put(
    "/question-set-versions/{version_id}/default",
    name="visit_prep_question_set_default",
    response_model=VisitPrepQuestionSetMutationResponse,
)
def question_set_default(
    version_id: Annotated[str, Path()],
    decision: Annotated[VisitPrepAccessDecision, Depends(require_visit_prep_write)],
) -> VisitPrepQuestionSetMutationResponse:
    result = _invoke(
        lambda: question_set_service.set_default_question_set_version(
            owner_practice_member_id=_owner_id(decision),
            version_id=_version_id(version_id),
        )
    )
    return _mutation_response(result)
