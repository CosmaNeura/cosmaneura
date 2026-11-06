"""Link-only Visit Prep create, interview, package, and cited synthesis."""
from __future__ import annotations
import base64
import hashlib
import hmac
import json
import re
import secrets
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal
from urllib.parse import quote
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from activemodel.session_manager import get_session
from activemodel.types import TypeIDType
from email_validator import EmailNotValidError, validate_email
from sqlmodel import select
from app.configuration.visit_prep import VISIT_PREP_SETTINGS, VisitPrepSettings
from app.constants import INTAKE_CHAT_DOMAIN
from app.models.practice import Practice, PracticeMember
from app.models.visit_prep import VisitPrep, VisitPrepQuestionResponse
from app.models.visit_prep_question_set import VisitPrepQuestion
from app.services.visit_prep_questions import get_published_version
from app.utils.encryption import EncryptionError, decrypt_value, encrypt_value, pseudonymous_digest
RecipientRole = Literal["patient", "caregiver"]
InterviewStatus = Literal["confirmation_required", "in_progress", "ready_to_complete", "completed"]
ResponseKind = Literal["answered", "skipped"]
_TOKEN_DOMAIN = b"cosmaneura:visit-prep-token:v1\x00"
_TOKEN_PREFIX = "vp1_"
_E164 = re.compile(r"^\+[1-9][0-9]{7,14}$")
_NANP_555 = re.compile(r"^\+1[2-9]\d\d555\d{4}$")
_CREATION_HMAC = "visit-prep-creation-payload-v1"
_RESPONSE_HMAC = "visit-prep-response-payload-v1"
_CONFIRM_HMAC = "visit-prep-confirmation-name-v1"
_CREDENTIAL_AFTER, _MAX_ANSWER = timedelta(hours=72), 2000
class VisitPrepError(Exception): pass
class VisitPrepNotFound(VisitPrepError): pass
class VisitPrepValidation(VisitPrepError, ValueError): pass
class VisitPrepConflict(VisitPrepError): pass
class VisitPrepUnavailable(VisitPrepError): pass

@dataclass(frozen=True)
class RecipientInput:
    first_name: str = field(repr=False)
    role: RecipientRole = "patient"
    email: str | None = field(default=None, repr=False)
    phone: str | None = field(default=None, repr=False)

@dataclass(frozen=True)
class PrepOut:
    id: TypeIDType; question_set_version_id: TypeIDType; lifecycle_state: str
    physician_display_name: str; practice_display_name: str; scheduled_at: datetime
    timezone_name: str; credential_generation: int; credential_expires_at: datetime
    credential_active: bool
    access_url: str | None = field(default=None, repr=False)
    access_token: str | None = field(default=None, repr=False)

@dataclass(frozen=True)
class InterviewQuestion:
    key: str; position: int; prompt: str; helper_text: str | None; kind: str
    choices: tuple[dict[str, str | None], ...]

@dataclass(frozen=True)
class InterviewState:
    status: InterviewStatus; physician_display_name: str | None; practice_display_name: str | None
    scheduled_at: datetime | None; timezone_name: str | None; submitted_questions: int
    maximum_questions: int; current_question: InterviewQuestion | None

@dataclass(frozen=True)
class TopicSource:
    question_key: str; prompt: str; kind: str; choices: tuple[dict[str, str | None], ...]
    response_kind: ResponseKind | None; answer: str | None; recorded_at: datetime | None
    response_id: TypeIDType | None

@dataclass(frozen=True)
class PackageOut:
    package_id: TypeIDType; contract: str; preparation: PrepOut; actor_role: RecipientRole | None
    current_answers_complete: bool; topics: tuple[TopicSource, ...]; source_version: str; synthesis: dict
def _text(value: object, *, maximum: int, message: str) -> str:
    if not isinstance(value, str):
        raise VisitPrepValidation(message)
    normalized = unicodedata.normalize("NFC", value).strip()
    if (not normalized or len(normalized) > maximum
            or any(unicodedata.category(c).startswith("C") for c in normalized)):
        raise VisitPrepValidation(message)
    return normalized
def _uuid(value: object) -> str:
    if not isinstance(value, str):
        raise VisitPrepValidation("idempotency key is invalid")
    try:
        canonical = str(UUID(value))
    except (AttributeError, TypeError, ValueError) as exc:
        raise VisitPrepValidation("idempotency key is invalid") from exc
    if canonical != value:
        raise VisitPrepValidation("idempotency key is invalid")
    return canonical
def _utc(value: datetime | None = None) -> datetime:
    current = value or datetime.now(UTC)
    if current.tzinfo is None or current.utcoffset() is None:
        raise VisitPrepValidation("timestamp is invalid")
    return current.astimezone(UTC)

def is_synthetic_recipient(email: str | None, phone: str | None) -> bool:
    email_ok = email is not None and (
        email.endswith(".test") or email.endswith(".example")
        or email.endswith("@example.com") or email.endswith("@example.net")
        or email.endswith("@example.org"))
    return bool(email_ok or (phone is not None and _NANP_555.fullmatch(phone) is not None))
def _normalize_recipient(recipient: RecipientInput, *, settings: VisitPrepSettings):
    first_name = _text(recipient.first_name, maximum=120, message="recipient first name is invalid")
    if recipient.role not in {"patient", "caregiver"}:
        raise VisitPrepValidation("recipient role is invalid")
    email = phone = None
    if recipient.email:
        try:
            email = validate_email(recipient.email.strip(), check_deliverability=False).normalized.lower()
        except EmailNotValidError as exc:
            raise VisitPrepValidation("recipient email is invalid") from exc
    if recipient.phone:
        raw = recipient.phone.strip()
        if raw and _E164.fullmatch(raw) is None:
            raise VisitPrepValidation("recipient phone is invalid")
        phone = raw or None
    if email is None and phone is None:
        raise VisitPrepValidation("recipient contact is required")
    if settings.require_synthetic_recipients and not is_synthetic_recipient(email, phone):
        raise VisitPrepValidation("recipient must be synthetic allowlist only")
    return first_name, recipient.role, email, phone
def _new_access_token() -> str:
    return f"{_TOKEN_PREFIX}{base64.urlsafe_b64encode(secrets.token_bytes(32)).decode('ascii').rstrip('=')}"

def access_token_hash(token: str) -> str:
    return hashlib.sha256(_TOKEN_DOMAIN + token.encode("ascii")).hexdigest()

def is_canonical_token(token: str) -> bool:
    return bool(re.fullmatch(r"vp1_[A-Za-z0-9_-]{43}", token))

def access_url_for_token(token: str) -> str:
    return f"https://{INTAKE_CHAT_DOMAIN}/visit-prep#access_token={quote(token, safe='')}"
def _schedule(scheduled_at: datetime, timezone_name: object, *, now: datetime) -> tuple[datetime, str]:
    if scheduled_at.tzinfo is None or scheduled_at.utcoffset() is None:
        raise VisitPrepValidation("scheduled time is invalid")
    zone_name = _text(timezone_name, maximum=64, message="timezone is invalid")
    try:
        zone = ZoneInfo(zone_name)
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise VisitPrepValidation("timezone is invalid") from exc
    scheduled_utc = scheduled_at.astimezone(UTC)
    represented = scheduled_utc.astimezone(zone)
    if (represented.replace(tzinfo=None) != scheduled_at.replace(tzinfo=None)
            or represented.utcoffset() != scheduled_at.utcoffset()):
        raise VisitPrepValidation("scheduled time does not match timezone")
    if scheduled_utc <= now:
        raise VisitPrepValidation("scheduled time must be in the future")
    return scheduled_utc, zone_name
def _prep_out(prep: VisitPrep, *, access_token: str | None = None, at: datetime | None = None) -> PrepOut:
    now = _utc(at)
    active = (prep.credential_revoked_at is None and prep.credential_expires_at > now
              and prep.lifecycle_state != "completed")
    return PrepOut(
        prep.id, prep.question_set_version_id, prep.lifecycle_state, prep.physician_display_name,
        prep.practice_display_name, prep.scheduled_at, prep.timezone_name, prep.credential_generation,
        prep.credential_expires_at, active,
        None if access_token is None else access_url_for_token(access_token), access_token)
def _json_compact(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)

def create_visit_prep(
    *, owner_practice_member_id: TypeIDType, owner_doctor_id: TypeIDType, practice_id: TypeIDType,
    idempotency_key: str, recipient: RecipientInput, physician_display_name: str,
    scheduled_at: datetime, timezone_name: str, question_set_version_id: TypeIDType,
    settings: VisitPrepSettings = VISIT_PREP_SETTINGS, now: datetime | None = None,
) -> PrepOut:
    if settings.email_send_enabled or settings.sms_send_enabled:
        raise VisitPrepUnavailable("Visit Prep send channels stay unreachable")
    effective = _utc(now); key = _uuid(idempotency_key)
    first_name, role, email, phone = _normalize_recipient(recipient, settings=settings)
    physician = _text(physician_display_name, maximum=160, message="physician display name is invalid")
    scheduled_utc, zone = _schedule(scheduled_at, timezone_name, now=effective)
    version = get_published_version(
        owner_practice_member_id=owner_practice_member_id, question_set_version_id=question_set_version_id)
    member, practice = PracticeMember.get(id=owner_practice_member_id), Practice.get(id=practice_id)
    if (member is None or practice is None or member.practice_id != practice_id
            or member.doctor_id != owner_doctor_id):
        raise VisitPrepNotFound("owner scope not found")
    practice_name = _text(practice.legal_name, maximum=200, message="practice display name is invalid")
    payload = _json_compact({
        "email": email, "first_name": first_name, "phone": phone, "physician_display_name": physician,
        "practice_display_name": practice_name, "question_set_version_id": str(version.id), "role": role,
        "scheduled_at": scheduled_utc.isoformat(), "timezone_name": zone, "version": 1})
    try:
        payload_hmac = pseudonymous_digest(payload, context=_CREATION_HMAC)
        recipient_cipher = encrypt_value(_json_compact(
            {"first_name": first_name, "role": role, "email": email, "phone": phone}))
    except EncryptionError as exc:
        raise VisitPrepUnavailable("encryption unavailable") from exc
    with get_session() as session:
        existing = session.exec(select(VisitPrep).where(
            VisitPrep.owner_practice_member_id == owner_practice_member_id,
            VisitPrep.creation_idempotency_key == key).with_for_update()).one_or_none()
        if existing is not None:
            if not hmac.compare_digest(existing.creation_payload_hmac_sha256, payload_hmac):
                raise VisitPrepConflict("idempotency key belongs to a different payload")
            return _prep_out(existing, access_token=None, at=effective)
        token = _new_access_token()
        prep = VisitPrep(
            practice_id=practice_id, owner_practice_member_id=owner_practice_member_id,
            owner_doctor_id=owner_doctor_id, question_set_version_id=version.id,
            creation_idempotency_key=key, creation_payload_hmac_sha256=payload_hmac,
            recipient_snapshot_ciphertext=recipient_cipher, has_email_contact=email is not None,
            has_sms_contact=phone is not None, physician_display_name=physician,
            practice_display_name=practice_name, lifecycle_state="invited", scheduled_at=scheduled_utc,
            timezone_name=zone, schedule_generation=1, credential_generation=1,
            token_hash=access_token_hash(token), credential_expires_at=scheduled_utc + _CREDENTIAL_AFTER)
        session.add(prep); session.commit(); session.refresh(prep)
        return _prep_out(prep, access_token=token, at=effective)

def list_visit_preps(*, owner_practice_member_id: TypeIDType) -> list[PrepOut]:
    rows = VisitPrep.where(owner_practice_member_id=owner_practice_member_id).all()
    rows.sort(key=lambda row: row.created_at or datetime.min.replace(tzinfo=UTC), reverse=True)
    return [_prep_out(row) for row in rows]

def copy_visit_prep_link(
    *, owner_practice_member_id: TypeIDType, visit_prep_id: TypeIDType, now: datetime | None = None,
) -> PrepOut:
    """Rotate credential and return a fresh one-time HTTPS link."""
    effective = _utc(now)
    with get_session() as session:
        prep = session.exec(select(VisitPrep).where(
            VisitPrep.id == visit_prep_id, VisitPrep.owner_practice_member_id == owner_practice_member_id,
        ).with_for_update()).one_or_none()
        if prep is None:
            raise VisitPrepNotFound("Visit Prep not found")
        if prep.lifecycle_state == "completed":
            raise VisitPrepConflict("completed Visit Prep cannot replace links")
        if prep.credential_expires_at <= effective:
            raise VisitPrepConflict("Visit Prep schedule has already expired")
        token = _new_access_token()
        prep.credential_generation += 1
        prep.token_hash = access_token_hash(token)
        prep.credential_revoked_at = None
        session.add(prep); session.commit(); session.refresh(prep)
        return _prep_out(prep, access_token=token, at=effective)
def _load_by_token(token: str, *, settings: VisitPrepSettings) -> VisitPrep:
    if not settings.public_api_enabled or not settings.enabled or not is_canonical_token(token):
        raise VisitPrepUnavailable("Visit Prep link is unavailable")
    prep = VisitPrep.get(token_hash=access_token_hash(token))
    now = _utc()
    if prep is None or prep.credential_revoked_at is not None or prep.credential_expires_at <= now:
        raise VisitPrepUnavailable("Visit Prep link is unavailable")
    return prep
def _questions_for(prep: VisitPrep) -> list[VisitPrepQuestion]:
    return sorted(VisitPrepQuestion.where(question_set_version_id=prep.question_set_version_id).all(),
                  key=lambda q: q.position)
def _visible_questions(prep: VisitPrep, answers: dict[str, str | None]) -> list[VisitPrepQuestion]:
    questions = _questions_for(prep); by_id = {q.id: q for q in questions}; visible: list[VisitPrepQuestion] = []
    for question in questions:
        if question.show_if_question_id is None:
            visible.append(question); continue
        parent = by_id[question.show_if_question_id]
        if answers.get(parent.question_key) in set(question.show_if_values or ()):
            visible.append(question)
    return visible
def _decrypt_answers(prep: VisitPrep) -> dict[str, tuple[VisitPrepQuestionResponse, str | None]]:
    questions = {q.id: q for q in _questions_for(prep)}
    out: dict[str, tuple[VisitPrepQuestionResponse, str | None]] = {}
    for response in VisitPrepQuestionResponse.where(visit_prep_id=prep.id).all():
        question = questions[response.question_id]; answer = None
        if response.response_kind == "answered":
            assert response.answer_ciphertext is not None
            answer = json.loads(decrypt_value(response.answer_ciphertext))["value"]
        out[question.question_key] = (response, answer)
    return out
def _interview_question(question: VisitPrepQuestion) -> InterviewQuestion:
    return InterviewQuestion(
        question.question_key, question.position, question.prompt_text, question.helper_text, question.kind,
        tuple({"value": c.value, "label": c.label, "hint": c.hint} for c in question.choice_options))
def _state(status: InterviewStatus, prep: VisitPrep | None, submitted: int,
           current: InterviewQuestion | None = None) -> InterviewState:
    if prep is None:
        return InterviewState(status, None, None, None, None, submitted, 5, current)
    return InterviewState(status, prep.physician_display_name, prep.practice_display_name,
                          prep.scheduled_at, prep.timezone_name, submitted, 5, current)
def _interview_state(prep: VisitPrep) -> InterviewState:
    decrypted = _decrypt_answers(prep)
    answers = {key: value for key, (_row, value) in decrypted.items()}
    visible = _visible_questions(prep, answers)
    submitted = sum(1 for q in visible if q.question_key in decrypted)
    if prep.lifecycle_state == "invited":
        return _state("confirmation_required", None, 0)
    if prep.lifecycle_state == "completed":
        return _state("completed", prep, submitted)
    pending = next((q for q in visible if q.question_key not in decrypted), None)
    if pending is None:
        return _state("ready_to_complete", prep, submitted)
    return _state("in_progress", prep, submitted, _interview_question(pending))

def get_interview(token: str, *, settings: VisitPrepSettings = VISIT_PREP_SETTINGS) -> InterviewState:
    return _interview_state(_load_by_token(token, settings=settings))

def confirm_interview(
    token: str, *, first_name: str, role: RecipientRole,
    settings: VisitPrepSettings = VISIT_PREP_SETTINGS, now: datetime | None = None,
) -> InterviewState:
    if role not in {"patient", "caregiver"}:
        raise VisitPrepValidation("confirmation is invalid")
    name = _text(first_name, maximum=120, message="confirmation is invalid")
    effective = _utc(now); prep = _load_by_token(token, settings=settings)
    try:
        digest = pseudonymous_digest(name.casefold(), context=_CONFIRM_HMAC)
        recipient = json.loads(decrypt_value(prep.recipient_snapshot_ciphertext))
    except (EncryptionError, json.JSONDecodeError, KeyError) as exc:
        raise VisitPrepUnavailable("Visit Prep link is unavailable") from exc
    expected = unicodedata.normalize("NFC", str(recipient["first_name"])).strip().casefold()
    if name.casefold() != expected or role != recipient.get("role", "patient"):
        raise VisitPrepValidation("confirmation is invalid")
    with get_session() as session:
        locked = session.exec(select(VisitPrep).where(VisitPrep.id == prep.id).with_for_update()).one()
        if locked.lifecycle_state == "invited":
            locked.lifecycle_state = "confirmed"; locked.confirmed_at = effective
            locked.confirmed_by_role = role; locked.confirmation_name_digest = digest
            session.add(locked); session.commit(); session.refresh(locked)
            return _interview_state(locked)
        if locked.confirmation_name_digest != digest or locked.confirmed_by_role != role:
            raise VisitPrepConflict("confirmation conflicts with stored state")
        return _interview_state(locked)
def _normalize_answer(value: object) -> str:
    if not isinstance(value, str):
        raise VisitPrepValidation("answer is invalid")
    normalized = unicodedata.normalize("NFC", value.replace("\r\n", "\n").replace("\r", "\n")).strip()
    if (not normalized or len(normalized) > _MAX_ANSWER
            or any(c != "\n" and unicodedata.category(c).startswith("C") for c in normalized)):
        raise VisitPrepValidation("answer is invalid")
    return normalized
def _response_hmac(prep_id, question_key: str, response_kind: str, answer: str | None) -> str:
    return pseudonymous_digest(_json_compact({
        "answer": answer, "question_key": question_key, "response_kind": response_kind,
        "visit_prep_id": str(prep_id), "version": 1}), context=_RESPONSE_HMAC)

def submit_response(
    token: str, *, question_key: str, response_kind: ResponseKind, answer: str | None,
    idempotency_key: str, settings: VisitPrepSettings = VISIT_PREP_SETTINGS, now: datetime | None = None,
) -> InterviewState:
    effective, key = _utc(now), _uuid(idempotency_key)
    prep = _load_by_token(token, settings=settings)
    if prep.lifecycle_state == "invited":
        raise VisitPrepConflict("confirmation required")
    if prep.lifecycle_state == "completed":
        raise VisitPrepConflict("interview already completed")
    decrypted = _decrypt_answers(prep)
    answers = {k: v for k, (_r, v) in decrypted.items()}
    visible = _visible_questions(prep, answers)
    pending = next((q for q in visible if q.question_key not in decrypted), None)
    if pending is None or pending.question_key != question_key:
        existing = decrypted.get(question_key)
        if existing is not None and existing[0].idempotency_key == key:
            return _interview_state(prep)
        raise VisitPrepConflict("question is not current")
    ciphertext: str | None
    try:
        if response_kind == "answered":
            value = _normalize_answer(answer)
            if pending.kind == "choice" and value not in {c.value for c in pending.choice_options}:
                raise VisitPrepValidation("answer is invalid")
            ciphertext = encrypt_value(_json_compact({"kind": pending.kind, "value": value, "version": 1}))
            payload_hmac = _response_hmac(prep.id, question_key, response_kind, value)
        elif response_kind == "skipped":
            if answer is not None:
                raise VisitPrepValidation("response is invalid")
            if pending.question_key == "story":
                raise VisitPrepValidation("story cannot be skipped")
            ciphertext, payload_hmac = None, _response_hmac(prep.id, question_key, response_kind, None)
        else:
            raise VisitPrepValidation("response is invalid")
    except EncryptionError as exc:
        raise VisitPrepUnavailable("Visit Prep link is unavailable") from exc
    with get_session() as session:
        locked = session.exec(select(VisitPrep).where(VisitPrep.id == prep.id).with_for_update()).one()
        prior = session.exec(select(VisitPrepQuestionResponse).where(
            VisitPrepQuestionResponse.visit_prep_id == locked.id,
            VisitPrepQuestionResponse.idempotency_key == key)).one_or_none()
        if prior is not None:
            return _interview_state(locked)
        sequence = session.exec(select(VisitPrepQuestionResponse).where(
            VisitPrepQuestionResponse.visit_prep_id == locked.id)).all()
        row = VisitPrepQuestionResponse(
            visit_prep_id=locked.id, question_set_version_id=locked.question_set_version_id,
            question_id=pending.id, submission_sequence=len(sequence) + 1, response_kind=response_kind,
            answer_ciphertext=ciphertext, idempotency_key=key, payload_hmac_sha256=payload_hmac,
            recorded_at=effective)
        if locked.lifecycle_state == "confirmed":
            locked.lifecycle_state = "in_progress"; locked.started_at = effective
        session.add(row); session.add(locked); session.commit(); session.refresh(locked)
        return _interview_state(locked)

def complete_interview(
    token: str, *, settings: VisitPrepSettings = VISIT_PREP_SETTINGS, now: datetime | None = None,
) -> InterviewState:
    effective = _utc(now); prep = _load_by_token(token, settings=settings)
    state = _interview_state(prep)
    if state.status == "completed":
        return state
    if state.status != "ready_to_complete":
        raise VisitPrepConflict("interview is incomplete")
    with get_session() as session:
        locked = session.exec(select(VisitPrep).where(VisitPrep.id == prep.id).with_for_update()).one()
        locked.lifecycle_state = "completed"; locked.completed_at = effective
        locked.credential_revoked_at = effective
        session.add(locked); session.commit(); session.refresh(locked)
        return _interview_state(locked)
def _source_version(topics: list[TopicSource]) -> str:
    payload = [{
        "answer": t.answer, "key": t.question_key, "kind": t.response_kind,
        "recorded_at": None if t.recorded_at is None else t.recorded_at.isoformat(),
        "response_id": None if t.response_id is None else str(t.response_id),
    } for t in topics]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()).hexdigest()
def _synthesis_item(topic: TopicSource) -> dict:
    assert topic.answer is not None and topic.response_id is not None
    return {"text": topic.answer[:700],
            "evidence": [{"source_ref": str(topic.response_id), "quote": topic.answer[:300]}]}
def _synthesis_from_topics(topics: list[TopicSource]) -> dict:
    answered = [t for t in topics if t.response_kind == "answered" and t.answer]
    base = {
        "contract": "visit_prep.patient_synthesis.v1", "status": "ready", "build_status": "ready",
        "brief": {"headline": None, "summary": [], "visit_priorities": [],
                  "medication_and_adherence": [], "barriers": [], "uncertainties": []},
        "artifact_id": None, "version": 1, "recorded_at": datetime.now(UTC).isoformat()}
    if not answered:
        return base
    by_key = {t.question_key: t for t in answered}
    return {**base, "brief": {
        "headline": _synthesis_item(answered[0]),
        "summary": [_synthesis_item(t) for t in answered[:5]],
        "visit_priorities": [_synthesis_item(t) for t in answered if t.question_key in {"reason", "story"}][:3],
        "medication_and_adherence": [_synthesis_item(t) for t in answered
            if t.question_key in {"medications", "adherence", "adherence_barrier"}][:3],
        "barriers": [_synthesis_item(t) for t in answered if t.question_key == "adherence_barrier"][:2],
        "uncertainties": []},
        "artifact_id": f"synthetic:{answered[0].response_id}", "sources_order": list(by_key)}

def read_package(*, owner_practice_member_id: TypeIDType, visit_prep_id: TypeIDType) -> PackageOut:
    prep = VisitPrep.get(id=visit_prep_id, owner_practice_member_id=owner_practice_member_id)
    if prep is None or prep.lifecycle_state != "completed":
        raise VisitPrepNotFound("confirmed package not found")
    decrypted = _decrypt_answers(prep)
    answers = {k: v for k, (_r, v) in decrypted.items()}
    topics: list[TopicSource] = []
    for question in _visible_questions(prep, answers):
        row_answer = decrypted.get(question.question_key)
        topics.append(TopicSource(
            question.question_key, question.prompt_text, question.kind,
            tuple({"value": c.value, "label": c.label, "hint": c.hint} for c in question.choice_options),
            None if row_answer is None else row_answer[0].response_kind,  # type: ignore[arg-type]
            None if row_answer is None else row_answer[1],
            None if row_answer is None else row_answer[0].recorded_at,
            None if row_answer is None else row_answer[0].id))
    return PackageOut(
        prep.id, "visit_prep.patient_sources.v1", _prep_out(prep),
        None if prep.confirmed_by_role is None else prep.confirmed_by_role,  # type: ignore[arg-type]
        all(t.response_kind is not None for t in topics), tuple(topics),
        _source_version(topics), _synthesis_from_topics(topics))
