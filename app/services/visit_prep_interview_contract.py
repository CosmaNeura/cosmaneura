"""Bounded patient response inputs and public state; no database mutations."""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from app.utils.encryption import EncryptionError, pseudonymous_digest

from activemodel.types import TypeIDType

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
class _CanonicalResponse:
    question_key: str
    response_kind: ResponseKind
    answer: str | None = field(default=None, repr=False)


class VisitPrepInterviewError(Exception):
    """Base class for bounded patient-interview failures."""


class VisitPrepInterviewUnavailable(VisitPrepInterviewError):
    """The public link or an internal prerequisite is unavailable."""


class VisitPrepInterviewValidation(VisitPrepInterviewError, ValueError):
    """A public request does not satisfy the interview contract."""


class VisitPrepInterviewConflict(VisitPrepInterviewError):
    """A valid request conflicts with durable interview state."""


def _answer_envelope(*, kind: str, value: str) -> str:
    return json.dumps(
        {"kind": kind, "value": value, "version": 1},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
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


def _calendar_years_after(value: datetime, years: int) -> datetime:
    """Match the database's UTC-anchored calendar-year retention term."""

    value = value.astimezone(UTC)
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
    if not isinstance(value.response_kind, str):
        raise VisitPrepInterviewValidation("response is invalid")
    if value.response_kind == "answered" and not isinstance(value.answer, str):
        raise VisitPrepInterviewValidation("response is invalid")
    if value.response_kind == "skipped" and value.answer is not None:
        raise VisitPrepInterviewValidation("response is invalid")
    if value.response_kind not in {"answered", "skipped"}:
        raise VisitPrepInterviewValidation("response is invalid")
    return value


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
