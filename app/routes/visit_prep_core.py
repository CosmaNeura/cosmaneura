"""Gated Visit Prep provider APIs and public patient interview."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Literal

from activemodel.session_manager import aglobal_session
from activemodel.types import TypeIDType
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from typeid import TypeID
from typeid.errors import TypeIDException

from app.configuration.visit_prep import VISIT_PREP_SETTINGS
from app.routes.dependencies.view_as import deny_view_as_browser_activity
from app.routes.dependencies.visit_prep import require_visit_prep_read, require_visit_prep_write
from app.services.visit_prep_access import VisitPrepAccessDecision
from app.services.visit_prep_journey import (
    RecipientInput, VisitPrepConflict, VisitPrepError, VisitPrepNotFound, VisitPrepUnavailable,
    VisitPrepValidation, complete_interview, confirm_interview, copy_visit_prep_link,
    create_visit_prep, get_interview, list_visit_preps, read_package, submit_response,
)
from app.services.visit_prep_questions import (
    ChoiceIn, ConditionIn, QuestionIn, VersionIn, VisitPrepQuestionConflict, VisitPrepQuestionError,
    VisitPrepQuestionNotFound, VisitPrepQuestionValidation, ensure_default_question_set,
    list_question_sets, publish_question_set_version,
)

_BEARER = re.compile(r"^Bearer (vp1_[A-Za-z0-9_-]{43})$")
_DOCUMENTED_BEARER = HTTPBearer(auto_error=False, scheme_name="VisitPrepBearer")

class ChoiceBody(BaseModel, extra="forbid"):
    value: str; label: str; hint: str | None = None

class ConditionBody(BaseModel, extra="forbid"):
    question_key: str; values: list[str]

class QuestionBody(BaseModel, extra="forbid"):
    key: str; prompt: str; kind: Literal["story", "list", "choice"]
    helper_text: str | None = None; choices: list[ChoiceBody] = Field(default_factory=list)
    show_if: ConditionBody | None = None

class VersionBody(BaseModel, extra="forbid"):
    name: str; questions: list[QuestionBody]; make_default: bool = True

class RecipientBody(BaseModel, extra="forbid"):
    first_name: str; role: Literal["patient", "caregiver"] = "patient"
    email: str | None = None; phone: str | None = None

class CreatePrepBody(BaseModel, extra="forbid"):
    idempotency_key: str; recipient: RecipientBody; physician_display_name: str
    scheduled_at: datetime; timezone_name: str; question_set_version_id: str

class ConfirmBody(BaseModel, extra="forbid"):
    first_name: str; role: Literal["patient", "caregiver"]

class ResponseBody(BaseModel, extra="forbid"):
    question_key: str; response_kind: Literal["answered", "skipped"]; answer: str | None = None

def _type_id(value: str, *, prefix: str) -> TypeIDType:
    try:
        parsed = TypeID.from_string(value)
    except TypeIDException as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid id") from exc
    if parsed.prefix != prefix or str(parsed) != value:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid id")
    return parsed  # type: ignore[return-value]

def _map_question_error(exc: VisitPrepQuestionError) -> HTTPException:
    if isinstance(exc, VisitPrepQuestionNotFound):
        return HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    if isinstance(exc, VisitPrepQuestionConflict):
        return HTTPException(status.HTTP_409_CONFLICT, "conflict")
    if isinstance(exc, VisitPrepQuestionValidation):
        return HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    return HTTPException(status.HTTP_400_BAD_REQUEST, "invalid")

def _map_journey_error(exc: VisitPrepError) -> HTTPException:
    if isinstance(exc, (VisitPrepNotFound, VisitPrepUnavailable)):
        return HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    if isinstance(exc, VisitPrepConflict):
        return HTTPException(status.HTTP_409_CONFLICT, "conflict")
    if isinstance(exc, VisitPrepValidation):
        return HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    return HTTPException(status.HTTP_400_BAD_REQUEST, "invalid")

def _version_payload(version) -> dict:
    return {
        "id": str(version.id), "question_set_id": str(version.question_set_id),
        "version_number": version.version_number, "name": version.name,
        "published_at": version.published_at, "is_default": version.is_default,
        "questions": [{
            "id": str(q.id), "key": q.key, "position": q.position, "prompt": q.prompt,
            "helper_text": q.helper_text, "kind": q.kind,
            "choices": [{"value": c.value, "label": c.label, "hint": c.hint} for c in q.choices],
            "show_if": None if q.show_if is None else {
                "question_key": q.show_if.question_key, "values": list(q.show_if.values)},
        } for q in version.questions]}

def _prep_payload(prep) -> dict:
    return {
        "id": str(prep.id), "question_set_version_id": str(prep.question_set_version_id),
        "lifecycle_state": prep.lifecycle_state, "physician_display_name": prep.physician_display_name,
        "practice_display_name": prep.practice_display_name, "scheduled_at": prep.scheduled_at,
        "timezone_name": prep.timezone_name, "credential_generation": prep.credential_generation,
        "credential_expires_at": prep.credential_expires_at, "credential_active": prep.credential_active,
        "access_url": prep.access_url, "has_access_token": prep.access_token is not None,
        "email_send_enabled": False, "sms_send_enabled": False,
        "can_send_email": False, "can_send_sms": False}

def _interview_payload(state) -> dict:
    current = None
    if state.current_question is not None:
        q = state.current_question
        current = {"key": q.key, "position": q.position, "prompt": q.prompt,
                   "helper_text": q.helper_text, "kind": q.kind, "choices": list(q.choices)}
    return {
        "status": state.status, "physician_display_name": state.physician_display_name,
        "practice_display_name": state.practice_display_name, "scheduled_at": state.scheduled_at,
        "timezone_name": state.timezone_name, "submitted_questions": state.submitted_questions,
        "maximum_questions": state.maximum_questions, "current_question": current}

def _to_version_in(body: VersionBody) -> VersionIn:
    return VersionIn(name=body.name, questions=tuple(QuestionIn(
        key=q.key, prompt=q.prompt, kind=q.kind, helper_text=q.helper_text,
        choices=tuple(ChoiceIn(c.value, c.label, c.hint) for c in q.choices),
        show_if=None if q.show_if is None else ConditionIn(q.show_if.question_key, tuple(q.show_if.values)),
    ) for q in body.questions))

visit_prep_provider_api = APIRouter(prefix="/visit-prep", dependencies=[Depends(aglobal_session)])

@visit_prep_provider_api.get("/question-sets", name="visit_prep_question_set_list")
def visit_prep_question_set_list(access: Annotated[VisitPrepAccessDecision, Depends(require_visit_prep_read)]):
    assert access.practice_member_id is not None
    try:
        versions = list_question_sets(owner_practice_member_id=access.practice_member_id)
    except VisitPrepQuestionError as exc:
        raise _map_question_error(exc) from exc
    return {"question_sets": [_version_payload(v) for v in versions]}

@visit_prep_provider_api.post("/question-sets/default", name="visit_prep_question_set_default")
def visit_prep_question_set_default(access: Annotated[VisitPrepAccessDecision, Depends(require_visit_prep_write)]):
    assert access.practice_member_id is not None
    try:
        version = ensure_default_question_set(owner_practice_member_id=access.practice_member_id)
    except VisitPrepQuestionError as exc:
        raise _map_question_error(exc) from exc
    return _version_payload(version)

@visit_prep_provider_api.post("/question-sets/{question_set_id}/versions", name="visit_prep_question_set_publish")
def visit_prep_question_set_publish(
    question_set_id: str, body: VersionBody,
    access: Annotated[VisitPrepAccessDecision, Depends(require_visit_prep_write)],
):
    assert access.practice_member_id is not None
    try:
        version = publish_question_set_version(
            owner_practice_member_id=access.practice_member_id,
            question_set_id=_type_id(question_set_id, prefix="visit_prep_question_set"),
            definition=_to_version_in(body), make_default=body.make_default)
    except VisitPrepQuestionError as exc:
        raise _map_question_error(exc) from exc
    return _version_payload(version)

@visit_prep_provider_api.get("/preps", name="visit_prep_list")
def visit_prep_list(access: Annotated[VisitPrepAccessDecision, Depends(require_visit_prep_read)]):
    assert access.practice_member_id is not None
    return {"preparations": [
        _prep_payload(p) for p in list_visit_preps(owner_practice_member_id=access.practice_member_id)]}

@visit_prep_provider_api.post("/preps", name="visit_prep_create")
def visit_prep_create(
    body: CreatePrepBody, access: Annotated[VisitPrepAccessDecision, Depends(require_visit_prep_write)],
):
    assert access.practice_member_id is not None and access.practice_id is not None
    try:
        prep = create_visit_prep(
            owner_practice_member_id=access.practice_member_id, owner_doctor_id=access.doctor_id,
            practice_id=access.practice_id, idempotency_key=body.idempotency_key,
            recipient=RecipientInput(first_name=body.recipient.first_name, role=body.recipient.role,
                                     email=body.recipient.email, phone=body.recipient.phone),
            physician_display_name=body.physician_display_name, scheduled_at=body.scheduled_at,
            timezone_name=body.timezone_name,
            question_set_version_id=_type_id(body.question_set_version_id, prefix="visit_prep_question_set_version"))
    except VisitPrepError as exc:
        raise _map_journey_error(exc) from exc
    payload = _prep_payload(prep); payload["access_token"] = prep.access_token
    return payload

@visit_prep_provider_api.post("/preps/{prep_id}/copy-link", name="visit_prep_copy_link")
def visit_prep_copy_link_route(
    prep_id: str, access: Annotated[VisitPrepAccessDecision, Depends(require_visit_prep_write)],
):
    assert access.practice_member_id is not None
    try:
        prep = copy_visit_prep_link(
            owner_practice_member_id=access.practice_member_id,
            visit_prep_id=_type_id(prep_id, prefix="visit_prep"))
    except VisitPrepError as exc:
        raise _map_journey_error(exc) from exc
    payload = _prep_payload(prep); payload["access_token"] = prep.access_token
    return payload

@visit_prep_provider_api.get("/preps/{prep_id}/package", name="visit_prep_package")
def visit_prep_package(
    prep_id: str, access: Annotated[VisitPrepAccessDecision, Depends(require_visit_prep_read)],
):
    assert access.practice_member_id is not None
    try:
        package = read_package(
            owner_practice_member_id=access.practice_member_id,
            visit_prep_id=_type_id(prep_id, prefix="visit_prep"))
    except VisitPrepError as exc:
        raise _map_journey_error(exc) from exc
    return {
        "contract": package.contract, "package_id": str(package.package_id),
        "source_version": package.source_version, "actor_role": package.actor_role,
        "current_answers_complete": package.current_answers_complete,
        "preparation": _prep_payload(package.preparation),
        "topics": [{
            "question_key": t.question_key, "prompt": t.prompt, "kind": t.kind,
            "choices": list(t.choices), "response_kind": t.response_kind, "answer": t.answer,
            "recorded_at": t.recorded_at,
            "response_id": None if t.response_id is None else str(t.response_id),
        } for t in package.topics],
        "synthesis": package.synthesis, "raw_answers_always_available": True}

def require_public_visit_prep_bearer(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_DOCUMENTED_BEARER)] = None,
) -> str:
    deny_view_as_browser_activity(request)
    match = _BEARER.fullmatch(request.headers.get("authorization", ""))
    if match is None or not VISIT_PREP_SETTINGS.public_api_enabled or not VISIT_PREP_SETTINGS.enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return match.group(1)

visit_prep_public_api = APIRouter(prefix="/external/v1/visit-prep", dependencies=[Depends(aglobal_session)])

@visit_prep_public_api.get("", name="visit_prep_interview_state")
def visit_prep_interview_state(access_token: Annotated[str, Depends(require_public_visit_prep_bearer)]):
    try:
        return _interview_payload(get_interview(access_token))
    except VisitPrepError as exc:
        raise _map_journey_error(exc) from exc

@visit_prep_public_api.post("/confirm", name="visit_prep_interview_confirm")
def visit_prep_interview_confirm(
    body: ConfirmBody, access_token: Annotated[str, Depends(require_public_visit_prep_bearer)],
):
    try:
        state = confirm_interview(access_token, first_name=body.first_name, role=body.role)
    except VisitPrepError as exc:
        raise _map_journey_error(exc) from exc
    return {"interview": _interview_payload(state), "disposition": "confirmed"}

@visit_prep_public_api.post("/responses", name="visit_prep_interview_response")
def visit_prep_interview_response(
    body: ResponseBody, access_token: Annotated[str, Depends(require_public_visit_prep_bearer)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
):
    if idempotency_key is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "idempotency required")
    try:
        state = submit_response(
            access_token, question_key=body.question_key, response_kind=body.response_kind,
            answer=body.answer, idempotency_key=idempotency_key)
    except VisitPrepError as exc:
        raise _map_journey_error(exc) from exc
    return {"interview": _interview_payload(state), "disposition": "accepted"}

@visit_prep_public_api.post("/complete", name="visit_prep_interview_complete")
def visit_prep_interview_complete(access_token: Annotated[str, Depends(require_public_visit_prep_bearer)]):
    try:
        state = complete_interview(access_token)
    except VisitPrepError as exc:
        raise _map_journey_error(exc) from exc
    return {"interview": _interview_payload(state), "disposition": "completed"}
