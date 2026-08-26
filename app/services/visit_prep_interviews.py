"""Patient-facing Visit Prep interview domain operations.

The bearer is only a locator.  Every operation reacquires the authoritative
practice, aggregate, schedule, and credential rows in a stable lock order,
then rechecks rollout eligibility and expiry using database time.  Public
results intentionally contain no canonical or patient identifiers and never
return prior answer plaintext.
"""

from __future__ import annotations

import hmac
import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, cast
from uuid import UUID

import app.services.visit_prep_access as visit_prep_access
from app.services.visit_preps import (
    VisitPrepValidation as VisitPrepCredentialValidation,
)
from app.services.visit_preps import access_token_hash
from app.utils.encryption import (
    DecryptionError,
    EncryptionError,
    decrypt_value,
    encrypt_value,
    pseudonymous_digest,
)

from activemodel.session_manager import get_session
from activemodel.types import TypeIDType
from app.models.doctor import Doctor
from app.models.practice import Practice, PracticeMember
from app.models.visit_prep import (
    VisitPrep,
    VisitPrepCredentialGeneration,
    VisitPrepScheduleGeneration,
)
from app.models.visit_prep_question_set import (
    VisitPrepQuestion,
    VisitPrepQuestionChoice,
    VisitPrepQuestionSetVersion,
)
from app.models.visit_prep_response import VisitPrepQuestionResponse
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

InterviewStatus = Literal[
    "confirmation_required",
    "in_progress",
    "ready_to_complete",
    "completed",
]
ResponseKind = Literal["answered", "skipped"]
QuestionKind = Literal["story", "list", "choice"]
InterviewDisposition = Literal["confirmed", "accepted", "replayed", "completed"]

CLINICAL_RETENTION_POLICY_VERSION = "clinical.v1"
CLINICAL_RETENTION_YEARS = 7
MAX_ANSWER_CHARACTERS = 2_000

_QUESTION_KEY = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_RESPONSE_HMAC_CONTEXT = "visit-prep-response-payload-v1"


class VisitPrepInterviewError(Exception):
    """Base class for bounded patient-interview failures."""


class VisitPrepInterviewUnavailable(VisitPrepInterviewError):
    """The public link or an internal prerequisite is unavailable."""


class VisitPrepInterviewValidation(VisitPrepInterviewError, ValueError):
    """A public request does not satisfy the interview contract."""


class VisitPrepInterviewConflict(VisitPrepInterviewError):
    """A valid request conflicts with durable interview state."""


@dataclass(frozen=True)
class InterviewChoice:
    value: str
    label: str
    hint: str | None


@dataclass(frozen=True)
class InterviewQuestion:
    key: str
    position: int
    prompt: str
    helper_text: str | None
    kind: QuestionKind
    choices: tuple[InterviewChoice, ...]


@dataclass(frozen=True)
class InterviewState:
    status: InterviewStatus
    physician_display_name: str | None
    practice_display_name: str | None
    scheduled_at: datetime | None
    timezone_name: str | None
    submitted_questions: int
    maximum_questions: int
    current_question: InterviewQuestion | None


@dataclass(frozen=True)
class InterviewMutationResult:
    interview: InterviewState
    disposition: InterviewDisposition


@dataclass(frozen=True)
class ResponseInput:
    question_key: str
    response_kind: ResponseKind
    answer: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class _CredentialLocator:
    credential_id: TypeIDType
    visit_prep_id: TypeIDType
    owner_practice_member_id: TypeIDType
    owner_doctor_id: TypeIDType
    practice_id: TypeIDType


@dataclass(frozen=True)
class _AuthorizedInterview:
    visit_prep: VisitPrep
    schedule: VisitPrepScheduleGeneration
    credential: VisitPrepCredentialGeneration
    event_at: datetime


@dataclass(frozen=True)
class _CanonicalResponse:
    question_key: str
    response_kind: ResponseKind
    answer: str | None = field(default=None, repr=False)


def _canonical_token_hash(access_token: str) -> str:
    try:
        return access_token_hash(access_token)
    except VisitPrepCredentialValidation as exc:
        raise VisitPrepInterviewUnavailable("Visit Prep link is unavailable") from exc


def _credential_locator(session: Session, *, token_hash: str) -> _CredentialLocator:
    credential = session.exec(
        select(VisitPrepCredentialGeneration).where(
            VisitPrepCredentialGeneration.token_hash == token_hash
        )
    ).one_or_none()
    if credential is None:
        raise VisitPrepInterviewUnavailable("Visit Prep link is unavailable")
    visit_prep = session.exec(
        select(VisitPrep).where(
            VisitPrep.id == credential.visit_prep_id,
            VisitPrep.owner_practice_member_id == credential.owner_practice_member_id,
        )
    ).one_or_none()
    if visit_prep is None:
        raise VisitPrepInterviewUnavailable("Visit Prep link is unavailable")
    return _CredentialLocator(
        credential_id=credential.id,
        visit_prep_id=credential.visit_prep_id,
        owner_practice_member_id=credential.owner_practice_member_id,
        owner_doctor_id=visit_prep.owner_doctor_id,
        practice_id=visit_prep.practice_id,
    )


def _lock_authorized_interview(
    session: Session,
    *,
    access_token: str,
) -> _AuthorizedInterview:
    """Resolve and recheck a bearer using the canonical public lock order."""

    token_hash = _canonical_token_hash(access_token)
    locator = _credential_locator(session, token_hash=token_hash)

    # Practice -> Doctor -> PracticeMember matches physician Visit Prep commands
    # and organization offboarding. The locator reads above are intentionally
    # unlocked and are revalidated under these locks.
    practice = session.exec(
        select(Practice)
        .where(Practice.id == locator.practice_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()
    if practice is None:
        raise VisitPrepInterviewUnavailable("Visit Prep link is unavailable")

    doctor = session.exec(
        select(Doctor)
        .where(Doctor.id == locator.owner_doctor_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()
    if doctor is None:
        raise VisitPrepInterviewUnavailable("Visit Prep link is unavailable")

    owner = session.exec(
        select(PracticeMember)
        .where(
            PracticeMember.id == locator.owner_practice_member_id,
            PracticeMember.practice_id == practice.id,
            PracticeMember.doctor_id == doctor.id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()
    if owner is None:
        raise VisitPrepInterviewUnavailable("Visit Prep link is unavailable")

    visit_prep = session.exec(
        select(VisitPrep)
        .where(
            VisitPrep.id == locator.visit_prep_id,
            VisitPrep.owner_practice_member_id == owner.id,
            VisitPrep.practice_id == practice.id,
            VisitPrep.owner_doctor_id == owner.doctor_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()
    if visit_prep is None:
        raise VisitPrepInterviewUnavailable("Visit Prep link is unavailable")

    schedule = session.exec(
        select(VisitPrepScheduleGeneration)
        .where(
            VisitPrepScheduleGeneration.visit_prep_id == visit_prep.id,
            VisitPrepScheduleGeneration.owner_practice_member_id == owner.id,
        )
        .order_by(cast(Any, VisitPrepScheduleGeneration.generation).desc())
        .limit(1)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()
    credential = session.exec(
        select(VisitPrepCredentialGeneration)
        .where(
            VisitPrepCredentialGeneration.visit_prep_id == visit_prep.id,
            VisitPrepCredentialGeneration.owner_practice_member_id == owner.id,
        )
        .order_by(cast(Any, VisitPrepCredentialGeneration.generation).desc())
        .limit(1)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()
    if schedule is None or credential is None:
        raise VisitPrepInterviewUnavailable("Visit Prep link is unavailable")

    event_at = cast(datetime, session.exec(select(func.clock_timestamp())).one())
    if event_at.tzinfo is None or event_at.utcoffset() is None:
        raise VisitPrepInterviewUnavailable("Visit Prep link is unavailable")
    event_at = event_at.astimezone(UTC)

    decision = visit_prep_access.resolve_visit_prep_access(
        doctor,
        settings=visit_prep_access.VISIT_PREP_SETTINGS,
        at=event_at,
    )
    if (
        not decision.eligible
        or decision.doctor_id != visit_prep.owner_doctor_id
        or decision.practice_id != visit_prep.practice_id
        or decision.practice_member_id != visit_prep.owner_practice_member_id
        or owner.access_revoked_at is not None
        or not practice.baa_in_force(at=event_at)
        or not practice.entitled(at=event_at)
        or visit_prep.revoked_at is not None
        or visit_prep.lifecycle_state == "misdirected"
        or credential.id != locator.credential_id
        or credential.schedule_generation_id != schedule.id
        or credential.revoked_at is not None
        or credential.token_format_version != 1
        or credential.issued_at > event_at
        or credential.expires_at <= event_at
        or not hmac.compare_digest(credential.token_hash, token_hash)
    ):
        raise VisitPrepInterviewUnavailable("Visit Prep link is unavailable")

    return _AuthorizedInterview(
        visit_prep=visit_prep,
        schedule=schedule,
        credential=credential,
        event_at=event_at,
    )


def _questions(
    session: Session,
    *,
    visit_prep: VisitPrep,
) -> list[VisitPrepQuestion]:
    version = session.exec(
        select(VisitPrepQuestionSetVersion).where(
            VisitPrepQuestionSetVersion.id == visit_prep.question_set_version_id,
            VisitPrepQuestionSetVersion.owner_practice_member_id
            == visit_prep.owner_practice_member_id,
            cast(Any, VisitPrepQuestionSetVersion.published_at).is_not(None),
        )
    ).one_or_none()
    if version is None:
        raise VisitPrepInterviewUnavailable("Visit Prep link is unavailable")
    questions = list(
        session.exec(
            select(VisitPrepQuestion)
            .where(VisitPrepQuestion.question_set_version_id == version.id)
            .order_by(cast(Any, VisitPrepQuestion.position))
        ).all()
    )
    if len(questions) != 5 or [question.position for question in questions] != list(
        range(1, 6)
    ):
        raise VisitPrepInterviewUnavailable("Visit Prep link is unavailable")
    return questions


def _responses(
    session: Session,
    *,
    visit_prep: VisitPrep,
) -> list[VisitPrepQuestionResponse]:
    responses = list(
        session.exec(
            select(VisitPrepQuestionResponse)
            .where(VisitPrepQuestionResponse.visit_prep_id == visit_prep.id)
            .order_by(cast(Any, VisitPrepQuestionResponse.submission_sequence))
        ).all()
    )
    if len(responses) > 5 or [row.submission_sequence for row in responses] != list(
        range(1, len(responses) + 1)
    ):
        raise VisitPrepInterviewUnavailable("Visit Prep link is unavailable")
    return responses


def _answer_envelope(*, kind: str, value: str) -> str:
    return json.dumps(
        {"kind": kind, "value": value, "version": 1},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _decrypted_answer(
    response: VisitPrepQuestionResponse,
    *,
    question: VisitPrepQuestion,
) -> str:
    if (
        response.response_kind != "answered"
        or response.answer_format_version != 1
        or response.answer_ciphertext is None
    ):
        raise VisitPrepInterviewUnavailable("Visit Prep link is unavailable")
    try:
        payload = json.loads(decrypt_value(response.answer_ciphertext))
    except (DecryptionError, json.JSONDecodeError, TypeError, UnicodeError) as exc:
        raise VisitPrepInterviewUnavailable("Visit Prep link is unavailable") from exc
    if (
        not isinstance(payload, dict)
        or set(payload) != {"kind", "value", "version"}
        or payload.get("kind") != question.kind
        or payload.get("version") != 1
        or not isinstance(payload.get("value"), str)
    ):
        raise VisitPrepInterviewUnavailable("Visit Prep link is unavailable")
    return cast(str, payload["value"])


def _question_is_visible(
    question: VisitPrepQuestion,
    *,
    questions_by_id: dict[TypeIDType, VisitPrepQuestion],
    responses_by_question: dict[TypeIDType, VisitPrepQuestionResponse],
) -> bool:
    if question.show_if_question_id is None:
        return True
    parent = responses_by_question.get(question.show_if_question_id)
    if parent is None or parent.response_kind == "skipped":
        return False
    parent_question = questions_by_id.get(question.show_if_question_id)
    if parent_question is None or question.show_if_values is None:
        raise VisitPrepInterviewUnavailable("Visit Prep link is unavailable")
    return _decrypted_answer(parent, question=parent_question) in set(
        question.show_if_values
    )


def _validate_stored_response(
    response: VisitPrepQuestionResponse,
    *,
    question: VisitPrepQuestion,
    visit_prep: VisitPrep,
) -> None:
    if question.kind not in {"story", "list", "choice"}:
        raise VisitPrepInterviewUnavailable("Visit Prep link is unavailable")

    if response.response_kind == "answered":
        answer = _decrypted_answer(response, question=question)
        try:
            normalized_answer = _normalized_answer(answer)
        except VisitPrepInterviewValidation as exc:
            raise VisitPrepInterviewUnavailable(
                "Visit Prep link is unavailable"
            ) from exc
        if normalized_answer != answer:
            raise VisitPrepInterviewUnavailable("Visit Prep link is unavailable")
        if question.kind == "choice":
            allowed_values = {
                (
                    choice.value
                    if isinstance(choice, VisitPrepQuestionChoice)
                    else VisitPrepQuestionChoice.model_validate(choice).value
                )
                for choice in question.choice_options
            }
            if answer not in allowed_values:
                raise VisitPrepInterviewUnavailable("Visit Prep link is unavailable")
    elif (
        response.response_kind == "skipped"
        and response.answer_ciphertext is None
        and response.answer_format_version is None
    ):
        answer = None
    else:
        raise VisitPrepInterviewUnavailable("Visit Prep link is unavailable")

    canonical = _CanonicalResponse(
        question_key=question.question_key,
        response_kind=cast(ResponseKind, response.response_kind),
        answer=answer,
    )
    expected_hmac = _response_payload_hmac(
        canonical,
        visit_prep_id=visit_prep.id,
    )
    if not hmac.compare_digest(response.payload_hmac_sha256, expected_hmac):
        raise VisitPrepInterviewUnavailable("Visit Prep link is unavailable")


def _public_question(question: VisitPrepQuestion) -> InterviewQuestion:
    if question.kind not in {"story", "list", "choice"}:
        raise VisitPrepInterviewUnavailable("Visit Prep link is unavailable")
    choices: list[InterviewChoice] = []
    for raw_choice in question.choice_options:
        choice = (
            raw_choice
            if isinstance(raw_choice, VisitPrepQuestionChoice)
            else VisitPrepQuestionChoice.model_validate(raw_choice)
        )
        choices.append(
            InterviewChoice(
                value=choice.value,
                label=choice.label,
                hint=choice.hint,
            )
        )
    return InterviewQuestion(
        key=question.question_key,
        position=question.position,
        prompt=question.prompt_text,
        helper_text=question.helper_text,
        kind=cast(QuestionKind, question.kind),
        choices=tuple(choices),
    )


def _interview_state(
    session: Session,
    *,
    authorized: _AuthorizedInterview,
) -> InterviewState:
    visit_prep = authorized.visit_prep
    if visit_prep.lifecycle_state == "invited":
        return InterviewState(
            status="confirmation_required",
            physician_display_name=None,
            practice_display_name=None,
            scheduled_at=None,
            timezone_name=None,
            submitted_questions=0,
            maximum_questions=5,
            current_question=None,
        )

    questions = _questions(session, visit_prep=visit_prep)
    responses = _responses(session, visit_prep=visit_prep)
    if visit_prep.lifecycle_state not in {"confirmed", "in_progress", "completed"}:
        raise VisitPrepInterviewUnavailable("Visit Prep link is unavailable")

    questions_by_id = {question.id: question for question in questions}
    responses_by_question = {response.question_id: response for response in responses}
    if len(responses_by_question) != len(responses):
        raise VisitPrepInterviewUnavailable("Visit Prep link is unavailable")

    current_question: VisitPrepQuestion | None = None
    visible_response_count = 0
    for question in questions:
        visible = _question_is_visible(
            question,
            questions_by_id=questions_by_id,
            responses_by_question=responses_by_question,
        )
        response = responses_by_question.get(question.id)
        if not visible:
            if response is not None:
                raise VisitPrepInterviewUnavailable("Visit Prep link is unavailable")
            continue
        if response is None:
            if current_question is None:
                current_question = question
            continue
        if current_question is not None:
            raise VisitPrepInterviewUnavailable("Visit Prep link is unavailable")
        if response.submission_sequence != visible_response_count + 1:
            raise VisitPrepInterviewUnavailable("Visit Prep link is unavailable")
        _validate_stored_response(
            response,
            question=question,
            visit_prep=visit_prep,
        )
        visible_response_count += 1

    if visible_response_count != len(responses):
        raise VisitPrepInterviewUnavailable("Visit Prep link is unavailable")

    if visit_prep.lifecycle_state == "completed":
        if current_question is not None:
            raise VisitPrepInterviewUnavailable("Visit Prep link is unavailable")
        return InterviewState(
            status="completed",
            physician_display_name=visit_prep.physician_display_name,
            practice_display_name=visit_prep.practice_display_name,
            scheduled_at=authorized.schedule.scheduled_at,
            timezone_name=authorized.schedule.timezone_name,
            submitted_questions=visible_response_count,
            maximum_questions=5,
            current_question=None,
        )

    if visit_prep.lifecycle_state == "confirmed" and responses:
        raise VisitPrepInterviewUnavailable("Visit Prep link is unavailable")
    if visit_prep.lifecycle_state == "in_progress" and not responses:
        raise VisitPrepInterviewUnavailable("Visit Prep link is unavailable")

    return InterviewState(
        status=("in_progress" if current_question is not None else "ready_to_complete"),
        physician_display_name=visit_prep.physician_display_name,
        practice_display_name=visit_prep.practice_display_name,
        scheduled_at=authorized.schedule.scheduled_at,
        timezone_name=authorized.schedule.timezone_name,
        submitted_questions=visible_response_count,
        maximum_questions=5,
        current_question=(
            _public_question(current_question) if current_question is not None else None
        ),
    )


def _normalized_confirmation_name(value: object) -> str:
    if not isinstance(value, str):
        raise VisitPrepInterviewValidation("confirmation is invalid")
    normalized = unicodedata.normalize("NFC", value).strip()
    if (
        not normalized
        or len(normalized) > 120
        or any(
            unicodedata.category(character).startswith("C") for character in normalized
        )
    ):
        raise VisitPrepInterviewValidation("confirmation is invalid")
    return normalized.casefold()


def _recipient_identity(visit_prep: VisitPrep) -> tuple[str, str]:
    if visit_prep.recipient_snapshot_format_version != 1:
        raise VisitPrepInterviewUnavailable("Visit Prep link is unavailable")
    try:
        payload = json.loads(decrypt_value(visit_prep.recipient_snapshot_ciphertext))
    except (DecryptionError, json.JSONDecodeError, TypeError, UnicodeError) as exc:
        raise VisitPrepInterviewUnavailable("Visit Prep link is unavailable") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("version") != 1
        or payload.get("role") not in {"patient", "caregiver"}
        or not isinstance(payload.get("first_name"), str)
    ):
        raise VisitPrepInterviewUnavailable("Visit Prep link is unavailable")
    try:
        first_name = _normalized_confirmation_name(payload["first_name"])
    except VisitPrepInterviewValidation as exc:
        raise VisitPrepInterviewUnavailable("Visit Prep link is unavailable") from exc
    return first_name, cast(str, payload["role"])


def _calendar_years_after(value: datetime, years: int) -> datetime:
    """Match PostgreSQL's leap-day behavior for an exact calendar-year term."""

    try:
        return value.replace(year=value.year + years)
    except ValueError:
        return value.replace(year=value.year + years, day=28)


def _canonical_idempotency_key(value: object) -> str:
    if not isinstance(value, str):
        raise VisitPrepInterviewValidation("idempotency key is invalid")
    try:
        canonical = str(UUID(value))
    except (AttributeError, TypeError, ValueError) as exc:
        raise VisitPrepInterviewValidation("idempotency key is invalid") from exc
    if canonical != value:
        raise VisitPrepInterviewValidation("idempotency key is invalid")
    return canonical


def _normalized_answer(value: object) -> str:
    if not isinstance(value, str):
        raise VisitPrepInterviewValidation("answer is invalid")
    normalized = unicodedata.normalize(
        "NFC",
        value.replace("\r\n", "\n").replace("\r", "\n"),
    ).strip()
    if (
        not normalized
        or len(normalized) > MAX_ANSWER_CHARACTERS
        or any(
            character != "\n" and unicodedata.category(character).startswith("C")
            for character in normalized
        )
    ):
        raise VisitPrepInterviewValidation("answer is invalid")
    return normalized


def _validated_response_input(value: ResponseInput) -> ResponseInput:
    if not isinstance(value, ResponseInput):
        raise VisitPrepInterviewValidation("response is invalid")
    if (
        not isinstance(value.question_key, str)
        or _QUESTION_KEY.fullmatch(value.question_key) is None
    ):
        raise VisitPrepInterviewValidation("question is invalid")
    if value.response_kind == "answered" and not isinstance(value.answer, str):
        raise VisitPrepInterviewValidation("response is invalid")
    if value.response_kind == "skipped" and value.answer is not None:
        raise VisitPrepInterviewValidation("response is invalid")
    if value.response_kind not in {"answered", "skipped"}:
        raise VisitPrepInterviewValidation("response is invalid")
    return value


def _canonical_response(
    value: ResponseInput,
    *,
    question: VisitPrepQuestion,
) -> _CanonicalResponse:
    if question.kind not in {"story", "list", "choice"}:
        raise VisitPrepInterviewUnavailable("Visit Prep link is unavailable")
    if value.response_kind == "answered":
        assert value.answer is not None
        if question.kind == "choice":
            allowed_values = {
                (
                    choice.value
                    if isinstance(choice, VisitPrepQuestionChoice)
                    else VisitPrepQuestionChoice.model_validate(choice).value
                )
                for choice in question.choice_options
            }
            if value.answer not in allowed_values:
                raise VisitPrepInterviewValidation("answer is invalid")
            answer = value.answer
        else:
            answer = _normalized_answer(value.answer)
    else:
        answer = None
    return _CanonicalResponse(
        question_key=value.question_key,
        response_kind=value.response_kind,
        answer=answer,
    )


def _response_payload_hmac(
    value: _CanonicalResponse,
    *,
    visit_prep_id: TypeIDType,
) -> str:
    payload = json.dumps(
        {
            "answer": value.answer,
            "question_key": value.question_key,
            "response_kind": value.response_kind,
            "visit_prep_id": str(visit_prep_id),
            "version": 1,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    try:
        return pseudonymous_digest(payload, context=_RESPONSE_HMAC_CONTEXT)
    except EncryptionError as exc:
        raise VisitPrepInterviewUnavailable("Visit Prep link is unavailable") from exc


def _commit(session: Session) -> None:
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise VisitPrepInterviewConflict("Visit Prep state changed") from exc


def get_interview(*, access_token: str) -> InterviewState:
    """Resume one currently authorized Visit Prep without returning prior answers."""

    with get_session() as session:
        authorized = _lock_authorized_interview(session, access_token=access_token)
        state = _interview_state(session, authorized=authorized)
        _commit(session)
        return state


def confirm_interview(
    *,
    access_token: str,
    first_name: str,
    role: Literal["patient", "caregiver"],
) -> InterviewMutationResult:
    """Confirm the intended recipient and snapshot clinical.v1 retention."""

    normalized_name = _normalized_confirmation_name(first_name)
    if role not in {"patient", "caregiver"}:
        raise VisitPrepInterviewValidation("confirmation is invalid")

    with get_session() as session:
        authorized = _lock_authorized_interview(session, access_token=access_token)
        visit_prep = authorized.visit_prep
        expected_name, expected_role = _recipient_identity(visit_prep)
        if not hmac.compare_digest(
            normalized_name.encode("utf-8"), expected_name.encode("utf-8")
        ) or not hmac.compare_digest(role, expected_role):
            raise VisitPrepInterviewUnavailable("Visit Prep link is unavailable")

        if visit_prep.lifecycle_state == "invited":
            visit_prep.lifecycle_state = "confirmed"
            visit_prep.confirmed_at = authorized.event_at
            visit_prep.confirmed_by_role = role
            visit_prep.retention_policy_version = CLINICAL_RETENTION_POLICY_VERSION
            visit_prep.retain_until = _calendar_years_after(
                authorized.schedule.scheduled_at,
                CLINICAL_RETENTION_YEARS,
            )
            visit_prep.updated_at = authorized.event_at
            session.add(visit_prep)
            try:
                session.flush()
            except IntegrityError as exc:
                session.rollback()
                raise VisitPrepInterviewConflict("Visit Prep state changed") from exc
        elif visit_prep.lifecycle_state not in {
            "confirmed",
            "in_progress",
            "completed",
        }:
            raise VisitPrepInterviewUnavailable("Visit Prep link is unavailable")
        elif (
            visit_prep.confirmed_by_role != role
            or visit_prep.retention_policy_version != CLINICAL_RETENTION_POLICY_VERSION
            or visit_prep.retain_until is None
        ):
            raise VisitPrepInterviewUnavailable("Visit Prep link is unavailable")

        state = _interview_state(session, authorized=authorized)
        _commit(session)
        return InterviewMutationResult(interview=state, disposition="confirmed")


def submit_response(
    *,
    access_token: str,
    idempotency_key: str,
    response: ResponseInput,
) -> InterviewMutationResult:
    """Append one answer or explicit skip with replay-safe semantics."""

    canonical_key = _canonical_idempotency_key(idempotency_key)
    submitted_response = _validated_response_input(response)
    with get_session() as session:
        authorized = _lock_authorized_interview(session, access_token=access_token)
        visit_prep = authorized.visit_prep
        questions = _questions(session, visit_prep=visit_prep)
        question = next(
            (
                candidate
                for candidate in questions
                if candidate.question_key == submitted_response.question_key
            ),
            None,
        )
        if question is None:
            raise VisitPrepInterviewConflict("question is not current")
        canonical_response = _canonical_response(
            submitted_response,
            question=question,
        )
        payload_hmac = _response_payload_hmac(
            canonical_response,
            visit_prep_id=visit_prep.id,
        )

        # This lookup deliberately precedes current-question and lifecycle
        # validation. Exact retries remain successful after progress,
        # completion, or a credential rotation; divergent reuse is fixed 409.
        existing = session.exec(
            select(VisitPrepQuestionResponse).where(
                VisitPrepQuestionResponse.visit_prep_id == visit_prep.id,
                VisitPrepQuestionResponse.idempotency_key == canonical_key,
            )
        ).one_or_none()
        if existing is not None:
            if not hmac.compare_digest(existing.payload_hmac_sha256, payload_hmac):
                raise VisitPrepInterviewConflict(
                    "idempotency key belongs to a different response"
                )
            state = _interview_state(session, authorized=authorized)
            _commit(session)
            return InterviewMutationResult(interview=state, disposition="replayed")

        state = _interview_state(session, authorized=authorized)
        if state.status not in {"in_progress"} or state.current_question is None:
            raise VisitPrepInterviewConflict("Visit Prep cannot accept a response")
        if state.current_question.key != canonical_response.question_key:
            raise VisitPrepInterviewConflict("question is not current")

        if canonical_response.response_kind == "answered":
            assert canonical_response.answer is not None
            try:
                answer_ciphertext = encrypt_value(
                    _answer_envelope(
                        kind=question.kind,
                        value=canonical_response.answer,
                    )
                )
            except EncryptionError as exc:
                raise VisitPrepInterviewUnavailable(
                    "Visit Prep link is unavailable"
                ) from exc
            answer_format_version: int | None = 1
        else:
            answer_ciphertext = None
            answer_format_version = None

        existing_responses = _responses(session, visit_prep=visit_prep)
        if visit_prep.lifecycle_state == "confirmed":
            visit_prep.lifecycle_state = "in_progress"
            visit_prep.started_at = authorized.event_at
            visit_prep.updated_at = authorized.event_at
            session.add(visit_prep)
            try:
                session.flush()
            except IntegrityError as exc:
                session.rollback()
                raise VisitPrepInterviewConflict("Visit Prep state changed") from exc

        response_row = VisitPrepQuestionResponse(
            visit_prep_id=visit_prep.id,
            owner_practice_member_id=visit_prep.owner_practice_member_id,
            question_set_version_id=visit_prep.question_set_version_id,
            question_id=question.id,
            credential_generation=authorized.credential.generation,
            submission_sequence=len(existing_responses) + 1,
            response_kind=canonical_response.response_kind,
            answer_ciphertext=answer_ciphertext,
            answer_format_version=answer_format_version,
            idempotency_key=canonical_key,
            payload_hmac_sha256=payload_hmac,
        )
        session.add(response_row)
        try:
            session.flush()
        except IntegrityError as exc:
            session.rollback()
            raise VisitPrepInterviewConflict("Visit Prep state changed") from exc
        state = _interview_state(session, authorized=authorized)
        _commit(session)
        return InterviewMutationResult(interview=state, disposition="accepted")


def complete_interview(*, access_token: str) -> InterviewMutationResult:
    """Explicitly complete an interview only after all applicable questions."""

    with get_session() as session:
        authorized = _lock_authorized_interview(session, access_token=access_token)
        visit_prep = authorized.visit_prep
        if visit_prep.lifecycle_state == "completed":
            state = _interview_state(session, authorized=authorized)
            _commit(session)
            return InterviewMutationResult(interview=state, disposition="completed")

        state = _interview_state(session, authorized=authorized)
        if state.status != "ready_to_complete":
            raise VisitPrepInterviewConflict("Visit Prep is incomplete")
        visit_prep.lifecycle_state = "completed"
        visit_prep.completed_at = authorized.event_at
        visit_prep.updated_at = authorized.event_at
        session.add(visit_prep)
        try:
            session.flush()
        except IntegrityError as exc:
            session.rollback()
            raise VisitPrepInterviewConflict("Visit Prep state changed") from exc
        state = _interview_state(session, authorized=authorized)
        _commit(session)
        return InterviewMutationResult(interview=state, disposition="completed")
