from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Any, Literal, TypeVar, cast

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, create_model
from typeid import TypeID
from typeid.errors import TypeIDException

from app.configuration.visit_prep import VISIT_PREP_SETTINGS
from app.routes.dependencies.view_as import deny_view_as_browser_activity
from app.routes.dependencies.visit_prep import (
    require_visit_prep_read,
    require_visit_prep_write,
)
from app.services.visit_prep_access import VisitPrepAccessDecision
from app.services.visit_prep_journey import (
    RecipientInput,
    VisitPrepConflict,
    VisitPrepError,
    VisitPrepNotFound,
    VisitPrepUnavailable,
    VisitPrepValidation,
    complete_interview,
    confirm_interview,
    copy_visit_prep_link,
    create_visit_prep,
    get_interview,
    list_visit_preps,
    read_package,
    submit_response,
)
from app.services.visit_prep_questions import (
    ChoiceIn,
    ConditionIn,
    QuestionIn,
    VersionIn,
    VisitPrepQuestionConflict,
    VisitPrepQuestionError,
    VisitPrepQuestionNotFound,
    VisitPrepQuestionValidation,
    ensure_default_question_set,
    list_question_sets,
    publish_question_set_version,
)

from activemodel.session_manager import aglobal_session
from activemodel.types import TypeIDType

_BEARER = re.compile(r"^Bearer (vp1_[A-Za-z0-9_-]{43})$")
_DOCUMENTED_BEARER = HTTPBearer(auto_error=False, scheme_name="VisitPrepBearer")
Role = Literal["patient", "caregiver"]
_X = ConfigDict(extra="forbid")
# fmt: off
ChoiceBody = cast(type[BaseModel], create_model("ChoiceBody", __config__=_X, value=(str, ...), label=(str, ...), hint=(str | None, None)))
ConditionBody = cast(type[BaseModel], create_model("ConditionBody", __config__=_X, question_key=(str, ...), values=(list[str], ...)))
QuestionBody = cast(type[BaseModel], create_model("QuestionBody", __config__=_X, key=(str, ...), prompt=(str, ...), kind=(Literal["story", "list", "choice"], ...), helper_text=(str | None, None), choices=(list[ChoiceBody], Field(default_factory=list)), show_if=(ConditionBody | None, None)))
VersionBody = cast(type[BaseModel], create_model("VersionBody", __config__=_X, name=(str, ...), questions=(list[QuestionBody], ...), make_default=(bool, True)))
RecipientBody = cast(type[BaseModel], create_model("RecipientBody", __config__=_X, first_name=(str, ...), role=(Role, "patient"), email=(str | None, None), phone=(str | None, None)))
CreatePrepBody = cast(type[BaseModel], create_model("CreatePrepBody", __config__=_X, idempotency_key=(str, ...), recipient=(RecipientBody, ...), physician_display_name=(str, ...), scheduled_at=(datetime, ...), timezone_name=(str, ...), question_set_version_id=(str, ...)))
ConfirmBody = cast(type[BaseModel], create_model("ConfirmBody", __config__=_X, first_name=(str, ...), role=(Role, ...)))
ResponseBody = cast(type[BaseModel], create_model("ResponseBody", __config__=_X, question_key=(str, ...), response_kind=(Literal["answered", "skipped"], ...), answer=(str | None, None)))
T = TypeVar("T")
def _type_id(value: str, *, prefix: str) -> TypeIDType:
    try:
        p = TypeID.from_string(value)
    except TypeIDException as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid id") from exc
    if p.prefix != prefix or str(p) != value:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid id")
    return cast(TypeIDType, p)
def _http(exc: BaseException) -> HTTPException:
    if isinstance(exc, (VisitPrepQuestionNotFound, VisitPrepNotFound, VisitPrepUnavailable)):
        return HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    if isinstance(exc, (VisitPrepQuestionConflict, VisitPrepConflict)):
        return HTTPException(status.HTTP_409_CONFLICT, "conflict")
    if isinstance(exc, (VisitPrepQuestionValidation, VisitPrepValidation)):
        return HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    return HTTPException(status.HTTP_400_BAD_REQUEST, "invalid")
def _qcall(f: Callable[[], T]) -> T:
    try:
        return f()
    except VisitPrepQuestionError as exc:
        raise _http(exc) from exc
def _jcall(f: Callable[[], T]) -> T:
    try:
        return f()
    except VisitPrepError as exc:
        raise _http(exc) from exc
def _q_show(sf: Any) -> dict | None:
    return None if sf is None else {"question_key": sf.question_key, "values": list(sf.values)}
def _version_payload(v: Any) -> dict:
    return {"id": str(v.id), "question_set_id": str(v.question_set_id), "version_number": v.version_number, "name": v.name, "published_at": v.published_at, "is_default": v.is_default, "questions": [{"id": str(q.id), "key": q.key, "position": q.position, "prompt": q.prompt, "helper_text": q.helper_text, "kind": q.kind, "choices": [{"value": c.value, "label": c.label, "hint": c.hint} for c in q.choices], "show_if": _q_show(q.show_if)} for q in v.questions]}
def _prep_payload(p: Any) -> dict:
    return {"id": str(p.id), "question_set_version_id": str(p.question_set_version_id), "lifecycle_state": p.lifecycle_state, "physician_display_name": p.physician_display_name, "practice_display_name": p.practice_display_name, "scheduled_at": p.scheduled_at, "timezone_name": p.timezone_name, "credential_generation": p.credential_generation, "credential_expires_at": p.credential_expires_at, "credential_active": p.credential_active, "access_url": p.access_url, "has_access_token": p.access_token is not None, "email_send_enabled": False, "sms_send_enabled": False, "can_send_email": False, "can_send_sms": False}
def _interview_payload(s: Any) -> dict:
    q = s.current_question
    cur = None if q is None else {"key": q.key, "position": q.position, "prompt": q.prompt, "helper_text": q.helper_text, "kind": q.kind, "choices": list(q.choices)}
    return {"status": s.status, "physician_display_name": s.physician_display_name, "practice_display_name": s.practice_display_name, "scheduled_at": s.scheduled_at, "timezone_name": s.timezone_name, "submitted_questions": s.submitted_questions, "maximum_questions": s.maximum_questions, "current_question": cur}
def _to_version_in(body: Any) -> VersionIn:
    return VersionIn(body.name, tuple(QuestionIn(q.key, q.prompt, q.kind, q.helper_text, tuple(ChoiceIn(c.value, c.label, c.hint) for c in q.choices), None if q.show_if is None else ConditionIn(q.show_if.question_key, tuple(q.show_if.values))) for q in body.questions))
def _member(access: VisitPrepAccessDecision) -> TypeIDType:
    assert access.practice_member_id is not None
    return access.practice_member_id
def require_public_visit_prep_bearer(request: Request, credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_DOCUMENTED_BEARER)] = None) -> str:
    deny_view_as_browser_activity(request)
    m = _BEARER.fullmatch(request.headers.get("authorization", ""))
    if m is None or not VISIT_PREP_SETTINGS.public_api_enabled or not VISIT_PREP_SETTINGS.enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return m.group(1)
visit_prep_provider_api = APIRouter(prefix="/visit-prep", dependencies=[Depends(aglobal_session)], include_in_schema=False)
visit_prep_public_api = APIRouter(prefix="/external/v1/visit-prep", dependencies=[Depends(aglobal_session)], include_in_schema=False)
def visit_prep_question_set_list(access: Annotated[VisitPrepAccessDecision, Depends(require_visit_prep_read)]):
    mid = _member(access)
    return {"question_sets": [_version_payload(v) for v in _qcall(lambda: list_question_sets(owner_practice_member_id=mid))]}
def visit_prep_question_set_default(access: Annotated[VisitPrepAccessDecision, Depends(require_visit_prep_write)]):
    mid = _member(access)
    return _version_payload(_qcall(lambda: ensure_default_question_set(owner_practice_member_id=mid)))
def visit_prep_question_set_publish(question_set_id: str, body: VersionBody, access: Annotated[VisitPrepAccessDecision, Depends(require_visit_prep_write)]):  # type: ignore[valid-type]
    mid, b = _member(access), cast(Any, body)
    return _version_payload(_qcall(lambda: publish_question_set_version(owner_practice_member_id=mid, question_set_id=_type_id(question_set_id, prefix="visit_prep_question_set"), definition=_to_version_in(b), make_default=b.make_default)))
def visit_prep_list(access: Annotated[VisitPrepAccessDecision, Depends(require_visit_prep_read)]):
    mid = _member(access)
    return {"preparations": [_prep_payload(p) for p in list_visit_preps(owner_practice_member_id=mid)]}
def visit_prep_create(body: CreatePrepBody, access: Annotated[VisitPrepAccessDecision, Depends(require_visit_prep_write)]):  # type: ignore[valid-type]
    mid, b = _member(access), cast(Any, body)
    assert access.practice_id is not None
    pid = access.practice_id
    prep = _jcall(lambda: create_visit_prep(owner_practice_member_id=mid, owner_doctor_id=access.doctor_id, practice_id=pid, idempotency_key=b.idempotency_key, recipient=RecipientInput(first_name=b.recipient.first_name, role=b.recipient.role, email=b.recipient.email, phone=b.recipient.phone), physician_display_name=b.physician_display_name, scheduled_at=b.scheduled_at, timezone_name=b.timezone_name, question_set_version_id=_type_id(b.question_set_version_id, prefix="visit_prep_question_set_version")))
    return {**_prep_payload(prep), "access_token": prep.access_token}
def visit_prep_copy_link(prep_id: str, access: Annotated[VisitPrepAccessDecision, Depends(require_visit_prep_write)]):
    mid = _member(access)
    prep = _jcall(lambda: copy_visit_prep_link(owner_practice_member_id=mid, visit_prep_id=_type_id(prep_id, prefix="visit_prep")))
    return {**_prep_payload(prep), "access_token": prep.access_token}
def visit_prep_package(prep_id: str, access: Annotated[VisitPrepAccessDecision, Depends(require_visit_prep_read)]):
    mid = _member(access)
    pkg = _jcall(lambda: read_package(owner_practice_member_id=mid, visit_prep_id=_type_id(prep_id, prefix="visit_prep")))
    return {"contract": pkg.contract, "package_id": str(pkg.package_id), "source_version": pkg.source_version, "actor_role": pkg.actor_role, "current_answers_complete": pkg.current_answers_complete, "preparation": _prep_payload(pkg.preparation), "topics": [{"question_key": t.question_key, "prompt": t.prompt, "kind": t.kind, "choices": list(t.choices), "response_kind": t.response_kind, "answer": t.answer, "recorded_at": t.recorded_at, "response_id": None if t.response_id is None else str(t.response_id)} for t in pkg.topics], "synthesis": pkg.synthesis, "raw_answers_always_available": True}
def visit_prep_interview_state(access_token: Annotated[str, Depends(require_public_visit_prep_bearer)]):
    return _interview_payload(_jcall(lambda: get_interview(access_token)))
def visit_prep_interview_confirm(body: ConfirmBody, access_token: Annotated[str, Depends(require_public_visit_prep_bearer)]):  # type: ignore[valid-type]
    b = cast(Any, body)
    return {"interview": _interview_payload(_jcall(lambda: confirm_interview(access_token, first_name=b.first_name, role=b.role))), "disposition": "confirmed"}
def visit_prep_interview_response(body: ResponseBody, access_token: Annotated[str, Depends(require_public_visit_prep_bearer)], idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None):  # type: ignore[valid-type]
    if idempotency_key is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "idempotency required")
    b = cast(Any, body)
    return {"interview": _interview_payload(_jcall(lambda: submit_response(access_token, question_key=b.question_key, response_kind=b.response_kind, answer=b.answer, idempotency_key=idempotency_key))), "disposition": "accepted"}
def visit_prep_interview_complete(access_token: Annotated[str, Depends(require_public_visit_prep_bearer)]):
    return {"interview": _interview_payload(_jcall(lambda: complete_interview(access_token))), "disposition": "completed"}
def _reg(r: APIRouter, rows: list[tuple[str, Callable[..., Any], list[str]]]) -> None:
    for path, fn, methods in rows:
        r.add_api_route(path, fn, methods=methods, name=fn.__name__)
_reg(visit_prep_provider_api, [("/question-sets", visit_prep_question_set_list, ["GET"]), ("/question-sets/default", visit_prep_question_set_default, ["POST"]), ("/question-sets/{question_set_id}/versions", visit_prep_question_set_publish, ["POST"]), ("/preps", visit_prep_list, ["GET"]), ("/preps", visit_prep_create, ["POST"]), ("/preps/{prep_id}/copy-link", visit_prep_copy_link, ["POST"]), ("/preps/{prep_id}/package", visit_prep_package, ["GET"])])
_reg(visit_prep_public_api, [("", visit_prep_interview_state, ["GET"]), ("/confirm", visit_prep_interview_confirm, ["POST"]), ("/responses", visit_prep_interview_response, ["POST"]), ("/complete", visit_prep_interview_complete, ["POST"])])
# fmt: on
