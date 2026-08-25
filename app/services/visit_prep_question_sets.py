"""Physician-owned Visit Prep question-set lifecycle operations.

Routes are responsible for eligibility and view-as enforcement.  This module
owns content validation, tenant scoping, idempotency, version lineage, and the
database locking order for question-set mutations.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, Literal, Sequence, cast
from uuid import UUID

from activemodel.session_manager import get_session
from activemodel.types import TypeIDType
from app.models.practice import PracticeMember
from app.models.visit_prep_question_set import (
    VisitPrepQuestion,
    VisitPrepQuestionChoice,
    VisitPrepQuestionSet,
    VisitPrepQuestionSetVersion,
)
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

QuestionKind = Literal["story", "list", "choice"]
MutationDisposition = Literal["created", "updated", "replayed"]

DEFAULT_QUESTION_SET_SEED_KEY = "default.v1"
DEFAULT_QUESTION_SET_NAME = "Visit Prep default questions"

_SAFE_KEY = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_MAX_NAME_LENGTH = 120
_MAX_PROMPT_LENGTH = 1_000
_MAX_HELPER_LENGTH = 500
_MAX_CHOICE_LABEL_LENGTH = 120
_MAX_CHOICE_HINT_LENGTH = 500


class VisitPrepQuestionSetError(Exception):
    """Base class for safe question-set domain failures."""


class VisitPrepQuestionSetNotFound(VisitPrepQuestionSetError):
    """The requested owner-scoped resource does not exist."""


class VisitPrepQuestionSetValidation(VisitPrepQuestionSetError, ValueError):
    """An authored definition does not satisfy the question contract."""


class VisitPrepQuestionSetConflict(VisitPrepQuestionSetError):
    """The requested mutation conflicts with durable state."""


@dataclass(frozen=True)
class QuestionChoiceInput:
    value: str
    label: str
    hint: str | None = None


@dataclass(frozen=True)
class QuestionConditionInput:
    question_key: str
    values: tuple[str, ...]


@dataclass(frozen=True)
class QuestionDraftInput:
    key: str
    prompt: str
    kind: QuestionKind
    helper_text: str | None = None
    choices: tuple[QuestionChoiceInput, ...] = ()
    show_if: QuestionConditionInput | None = None


@dataclass(frozen=True)
class QuestionSetVersionInput:
    name: str
    questions: tuple[QuestionDraftInput, ...] = ()


@dataclass(frozen=True)
class QuestionChoiceResult:
    value: str
    label: str
    hint: str | None


@dataclass(frozen=True)
class QuestionConditionResult:
    question_id: TypeIDType
    question_key: str
    values: tuple[str, ...]


@dataclass(frozen=True)
class QuestionResult:
    id: TypeIDType
    question_set_version_id: TypeIDType
    key: str
    position: int
    prompt: str
    helper_text: str | None
    kind: QuestionKind
    choices: tuple[QuestionChoiceResult, ...]
    show_if: QuestionConditionResult | None
    created_at: datetime | None
    updated_at: datetime | None


@dataclass(frozen=True)
class QuestionSetVersionResult:
    id: TypeIDType
    question_set_id: TypeIDType
    owner_practice_member_id: TypeIDType
    version_number: int
    name: str
    supersedes_version_id: TypeIDType | None
    published_at: datetime | None
    is_default: bool
    created_at: datetime | None
    updated_at: datetime | None
    questions: tuple[QuestionResult, ...]


@dataclass(frozen=True)
class QuestionSetResult:
    id: TypeIDType
    owner_practice_member_id: TypeIDType
    seed_key: str | None
    created_at: datetime | None
    updated_at: datetime | None
    versions: tuple[QuestionSetVersionResult, ...]


@dataclass(frozen=True)
class QuestionSetMutationResult:
    question_set: QuestionSetResult
    version: QuestionSetVersionResult
    disposition: MutationDisposition


CANONICAL_DEFAULT_QUESTION_SET = QuestionSetVersionInput(
    name=DEFAULT_QUESTION_SET_NAME,
    questions=(
        QuestionDraftInput(
            key="reason",
            prompt="What is the main reason for this visit?",
            kind="story",
            helper_text=(
                "In your own words. The thing you most want understood "
                "before you sit down."
            ),
        ),
        QuestionDraftInput(
            key="medications",
            prompt="What medications are you prescribed right now?",
            kind="list",
            helper_text=(
                "Include the dose if you know it. Over-the-counter and "
                "supplements count too."
            ),
        ),
        QuestionDraftInput(
            key="adherence",
            prompt="Are you taking them as prescribed?",
            kind="choice",
            helper_text="There is no wrong answer. Honesty here saves the visit.",
            choices=(
                QuestionChoiceInput(
                    value="yes",
                    label="Yes, as prescribed",
                    hint="Most days, as written",
                ),
                QuestionChoiceInput(
                    value="some",
                    label="Some of them",
                    hint="A few get skipped",
                ),
                QuestionChoiceInput(
                    value="no",
                    label="Not really",
                    hint="Something got in the way",
                ),
                QuestionChoiceInput(
                    value="none",
                    label="I don't take any",
                ),
            ),
        ),
        QuestionDraftInput(
            key="adherence_barrier",
            prompt="If not — what got in the way?",
            kind="story",
            helper_text=(
                "Cost, side effects, a chaotic morning, forgetting, not being "
                "sure it helps. All of that is useful."
            ),
            show_if=QuestionConditionInput(
                question_key="adherence",
                values=("some", "no"),
            ),
        ),
        QuestionDraftInput(
            key="story",
            prompt="Tell me your health story — whatever feels relevant.",
            kind="story",
            helper_text=(
                "What has been happening in your body, and in your life, that "
                "belongs in this visit?"
            ),
        ),
    ),
)


def _normalized_text(
    value: object,
    *,
    maximum: int,
    field_message: str,
) -> str:
    if not isinstance(value, str):
        raise VisitPrepQuestionSetValidation(field_message)
    normalized = unicodedata.normalize("NFC", value.replace("\r\n", "\n")).strip()
    if (
        not normalized
        or len(normalized) > maximum
        or any(
            character == "\x00"
            or (
                unicodedata.category(character) == "Cc"
                and character not in {"\n", "\t"}
            )
            for character in normalized
        )
    ):
        raise VisitPrepQuestionSetValidation(field_message)
    return normalized


def _optional_text(
    value: object,
    *,
    maximum: int,
    field_message: str,
) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise VisitPrepQuestionSetValidation(field_message)
    normalized = unicodedata.normalize("NFC", value.replace("\r\n", "\n")).strip()
    if not normalized:
        return None
    return _normalized_text(
        normalized,
        maximum=maximum,
        field_message=field_message,
    )


def _safe_key(value: object, *, message: str) -> str:
    if not isinstance(value, str) or _SAFE_KEY.fullmatch(value) is None:
        raise VisitPrepQuestionSetValidation(message)
    return value


def _normalize_version_input(
    definition: QuestionSetVersionInput,
    *,
    for_publish: bool,
) -> QuestionSetVersionInput:
    if not isinstance(definition, QuestionSetVersionInput):
        raise VisitPrepQuestionSetValidation("question-set payload is invalid")

    raw_questions: Sequence[QuestionDraftInput] = definition.questions
    expected_count = 5 if for_publish else None
    if (
        isinstance(raw_questions, (str, bytes))
        or len(raw_questions) > 5
        or (expected_count is not None and len(raw_questions) != expected_count)
    ):
        raise VisitPrepQuestionSetValidation("question count is invalid")

    normalized_questions: list[QuestionDraftInput] = []
    seen_keys: set[str] = set()
    choices_by_key: dict[str, frozenset[str]] = {}

    for raw_question in raw_questions:
        if not isinstance(raw_question, QuestionDraftInput):
            raise VisitPrepQuestionSetValidation("question payload is invalid")
        key = _safe_key(raw_question.key, message="question key is invalid")
        if key in seen_keys:
            raise VisitPrepQuestionSetValidation("question keys must be unique")
        seen_keys.add(key)

        if raw_question.kind not in {"story", "list", "choice"}:
            raise VisitPrepQuestionSetValidation("question kind is invalid")
        kind = cast(QuestionKind, raw_question.kind)

        if isinstance(raw_question.choices, (str, bytes)):
            raise VisitPrepQuestionSetValidation("question choices are invalid")
        normalized_choices: list[QuestionChoiceInput] = []
        seen_values: set[str] = set()
        seen_labels: set[str] = set()
        for raw_choice in raw_question.choices:
            if not isinstance(raw_choice, QuestionChoiceInput):
                raise VisitPrepQuestionSetValidation("question choice is invalid")
            choice_value = _safe_key(
                raw_choice.value,
                message="question choice value is invalid",
            )
            if choice_value in seen_values:
                raise VisitPrepQuestionSetValidation(
                    "question choice values must be unique"
                )
            seen_values.add(choice_value)
            choice_label = _normalized_text(
                raw_choice.label,
                maximum=_MAX_CHOICE_LABEL_LENGTH,
                field_message="question choice label is invalid",
            )
            if choice_label in seen_labels:
                raise VisitPrepQuestionSetValidation(
                    "question choice labels must be unique"
                )
            seen_labels.add(choice_label)
            normalized_choices.append(
                QuestionChoiceInput(
                    value=choice_value,
                    label=choice_label,
                    hint=_optional_text(
                        raw_choice.hint,
                        maximum=_MAX_CHOICE_HINT_LENGTH,
                        field_message="question choice hint is invalid",
                    ),
                )
            )

        if kind == "choice":
            if not 2 <= len(normalized_choices) <= 8:
                raise VisitPrepQuestionSetValidation(
                    "choice question option count is invalid"
                )
        elif normalized_choices:
            raise VisitPrepQuestionSetValidation(
                "non-choice question cannot have choices"
            )

        normalized_condition: QuestionConditionInput | None = None
        if raw_question.show_if is not None:
            condition = raw_question.show_if
            if not isinstance(condition, QuestionConditionInput):
                raise VisitPrepQuestionSetValidation("question condition is invalid")
            target_key = _safe_key(
                condition.question_key,
                message="question condition target is invalid",
            )
            if target_key not in choices_by_key:
                raise VisitPrepQuestionSetValidation(
                    "question condition must target an earlier choice question"
                )
            if isinstance(condition.values, (str, bytes)) or not condition.values:
                raise VisitPrepQuestionSetValidation(
                    "question condition values are invalid"
                )
            condition_values: list[str] = []
            seen_condition_values: set[str] = set()
            for raw_value in condition.values:
                condition_value = _safe_key(
                    raw_value,
                    message="question condition value is invalid",
                )
                if condition_value in seen_condition_values:
                    raise VisitPrepQuestionSetValidation(
                        "question condition values must be unique"
                    )
                if condition_value not in choices_by_key[target_key]:
                    raise VisitPrepQuestionSetValidation(
                        "question condition value is not a target choice"
                    )
                seen_condition_values.add(condition_value)
                condition_values.append(condition_value)
            normalized_condition = QuestionConditionInput(
                question_key=target_key,
                values=tuple(condition_values),
            )

        normalized_questions.append(
            QuestionDraftInput(
                key=key,
                prompt=_normalized_text(
                    raw_question.prompt,
                    maximum=_MAX_PROMPT_LENGTH,
                    field_message="question prompt is invalid",
                ),
                kind=kind,
                helper_text=_optional_text(
                    raw_question.helper_text,
                    maximum=_MAX_HELPER_LENGTH,
                    field_message="question helper text is invalid",
                ),
                choices=tuple(normalized_choices),
                show_if=normalized_condition,
            )
        )
        choices_by_key[key] = (
            frozenset(seen_values) if kind == "choice" else frozenset()
        )

    return QuestionSetVersionInput(
        name=_normalized_text(
            definition.name,
            maximum=_MAX_NAME_LENGTH,
            field_message="question-set name is invalid",
        ),
        questions=tuple(normalized_questions),
    )


def validate_question_set_draft(
    definition: QuestionSetVersionInput,
) -> QuestionSetVersionInput:
    """Return the normalized form of a zero-to-five-question draft."""

    return _normalize_version_input(definition, for_publish=False)


def validate_question_set_for_publish(
    definition: QuestionSetVersionInput,
) -> QuestionSetVersionInput:
    """Return the normalized form of an exactly-five-question definition."""

    return _normalize_version_input(definition, for_publish=True)


def _canonical_client_seed_key(idempotency_key: str) -> str:
    if not isinstance(idempotency_key, str):
        raise VisitPrepQuestionSetValidation("idempotency key is invalid")
    try:
        canonical = str(UUID(idempotency_key))
    except (AttributeError, TypeError, ValueError) as exc:
        raise VisitPrepQuestionSetValidation("idempotency key is invalid") from exc
    if idempotency_key != canonical:
        raise VisitPrepQuestionSetValidation("idempotency key is not canonical")
    return f"client.{canonical}"


def _creation_payload_sha256(definition: QuestionSetVersionInput) -> str:
    """Fingerprint one normalized create request without retaining extra content."""

    encoded = json.dumps(
        asdict(definition),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _aware_utc(value: datetime | None, *, message: str) -> datetime:
    current = value or datetime.now(UTC)
    if current.tzinfo is None or current.utcoffset() is None:
        raise VisitPrepQuestionSetValidation(message)
    return current.astimezone(UTC)


def _same_instant(left: datetime | None, right: datetime) -> bool:
    return bool(
        left is not None
        and left.tzinfo is not None
        and left.utcoffset() is not None
        and left.astimezone(UTC) == right.astimezone(UTC)
    )


def _lock_owner(session: Session, owner_id: TypeIDType) -> PracticeMember:
    owner = session.exec(
        select(PracticeMember).where(PracticeMember.id == owner_id).with_for_update()
    ).one_or_none()
    if owner is None or owner.access_revoked_at is not None:
        raise VisitPrepQuestionSetNotFound("question-set owner was not found")
    return owner


def _owned_set(
    session: Session,
    *,
    owner_id: TypeIDType,
    question_set_id: TypeIDType,
    lock: bool,
) -> VisitPrepQuestionSet:
    statement = select(VisitPrepQuestionSet).where(
        VisitPrepQuestionSet.id == question_set_id,
        VisitPrepQuestionSet.owner_practice_member_id == owner_id,
    )
    if lock:
        statement = statement.with_for_update()
    question_set = session.exec(statement).one_or_none()
    if question_set is None:
        raise VisitPrepQuestionSetNotFound("question set was not found")
    return question_set


def _discover_owned_version(
    session: Session,
    *,
    owner_id: TypeIDType,
    version_id: TypeIDType,
) -> VisitPrepQuestionSetVersion:
    version = session.exec(
        select(VisitPrepQuestionSetVersion).where(
            VisitPrepQuestionSetVersion.id == version_id,
            VisitPrepQuestionSetVersion.owner_practice_member_id == owner_id,
        )
    ).one_or_none()
    if version is None:
        raise VisitPrepQuestionSetNotFound("question-set version was not found")
    return version


def _lock_owned_version(
    session: Session,
    *,
    owner_id: TypeIDType,
    version_id: TypeIDType,
) -> tuple[VisitPrepQuestionSet, VisitPrepQuestionSetVersion]:
    discovered = _discover_owned_version(
        session,
        owner_id=owner_id,
        version_id=version_id,
    )
    question_set = _owned_set(
        session,
        owner_id=owner_id,
        question_set_id=discovered.question_set_id,
        lock=True,
    )
    version = session.exec(
        select(VisitPrepQuestionSetVersion)
        .where(
            VisitPrepQuestionSetVersion.id == version_id,
            VisitPrepQuestionSetVersion.question_set_id == question_set.id,
            VisitPrepQuestionSetVersion.owner_practice_member_id == owner_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()
    if version is None:
        raise VisitPrepQuestionSetNotFound("question-set version was not found")
    return question_set, version


def _question_rows(
    session: Session,
    version_id: TypeIDType,
    *,
    lock: bool,
) -> list[VisitPrepQuestion]:
    statement = select(VisitPrepQuestion).where(
        VisitPrepQuestion.question_set_version_id == version_id
    )
    if lock:
        statement = statement.with_for_update()
    rows = list(session.exec(statement).all())
    return sorted(rows, key=lambda row: (row.position, str(row.id)))


def _choice_input(value: object) -> QuestionChoiceInput:
    if isinstance(value, VisitPrepQuestionChoice):
        return QuestionChoiceInput(
            value=value.value,
            label=value.label,
            hint=value.hint,
        )
    if isinstance(value, dict):
        choice_value = value.get("value")
        choice_label = value.get("label")
        choice_hint = value.get("hint")
        if (
            not isinstance(choice_value, str)
            or not isinstance(choice_label, str)
            or (choice_hint is not None and not isinstance(choice_hint, str))
        ):
            raise VisitPrepQuestionSetValidation("stored question choice is invalid")
        return QuestionChoiceInput(
            value=choice_value,
            label=choice_label,
            hint=choice_hint,
        )
    raise VisitPrepQuestionSetValidation("stored question choice is invalid")


def _definition_from_rows(
    version: VisitPrepQuestionSetVersion,
    questions: Sequence[VisitPrepQuestion],
    *,
    for_publish: bool,
) -> QuestionSetVersionInput:
    by_id = {question.id: question for question in questions}
    definition_questions: list[QuestionDraftInput] = []
    for question in sorted(questions, key=lambda row: (row.position, str(row.id))):
        condition: QuestionConditionInput | None = None
        if question.show_if_question_id is not None:
            target = by_id.get(question.show_if_question_id)
            if target is None or question.show_if_values is None:
                raise VisitPrepQuestionSetValidation(
                    "stored question condition is invalid"
                )
            condition = QuestionConditionInput(
                question_key=target.question_key,
                values=tuple(question.show_if_values),
            )
        definition_questions.append(
            QuestionDraftInput(
                key=question.question_key,
                prompt=question.prompt_text,
                helper_text=question.helper_text,
                kind=cast(QuestionKind, question.kind),
                choices=tuple(
                    _choice_input(choice) for choice in question.choice_options
                ),
                show_if=condition,
            )
        )
    return _normalize_version_input(
        QuestionSetVersionInput(
            name=version.name,
            questions=tuple(definition_questions),
        ),
        for_publish=for_publish,
    )


def _insert_questions(
    session: Session,
    *,
    version: VisitPrepQuestionSetVersion,
    definition: QuestionSetVersionInput,
) -> list[VisitPrepQuestion]:
    rows: list[VisitPrepQuestion] = []
    for position, question in enumerate(definition.questions, start=1):
        row = VisitPrepQuestion(
            question_set_version_id=version.id,
            question_key=question.key,
            position=position,
            prompt_text=question.prompt,
            helper_text=question.helper_text,
            kind=question.kind,
            choice_options=[
                VisitPrepQuestionChoice(
                    value=choice.value,
                    label=choice.label,
                    hint=choice.hint,
                )
                for choice in question.choices
            ],
            show_if_question_id=None,
            show_if_values=None,
        )
        session.add(row)
        rows.append(row)
    session.flush()

    by_key = {row.question_key: row for row in rows}
    for row, question in zip(rows, definition.questions, strict=True):
        if question.show_if is None:
            continue
        row.show_if_question_id = by_key[question.show_if.question_key].id
        row.show_if_values = list(question.show_if.values)
        session.add(row)
    session.flush()
    return rows


def _replace_questions(
    session: Session,
    *,
    version: VisitPrepQuestionSetVersion,
    existing: Sequence[VisitPrepQuestion],
    definition: QuestionSetVersionInput,
) -> list[VisitPrepQuestion]:
    for question in reversed(existing):
        session.delete(question)
    session.flush()
    return _insert_questions(session, version=version, definition=definition)


def _choice_result(value: object) -> QuestionChoiceResult:
    choice = _choice_input(value)
    return QuestionChoiceResult(
        value=choice.value,
        label=choice.label,
        hint=choice.hint,
    )


def _snapshot_version(
    session: Session,
    version: VisitPrepQuestionSetVersion,
    *,
    questions: Sequence[VisitPrepQuestion] | None = None,
) -> QuestionSetVersionResult:
    rows = (
        list(questions)
        if questions is not None
        else _question_rows(
            session,
            version.id,
            lock=False,
        )
    )
    rows.sort(key=lambda row: (row.position, str(row.id)))
    by_id = {question.id: question for question in rows}
    question_results: list[QuestionResult] = []
    for question in rows:
        condition: QuestionConditionResult | None = None
        if question.show_if_question_id is not None:
            target = by_id.get(question.show_if_question_id)
            if target is None or question.show_if_values is None:
                raise VisitPrepQuestionSetValidation(
                    "stored question condition is invalid"
                )
            condition = QuestionConditionResult(
                question_id=target.id,
                question_key=target.question_key,
                values=tuple(question.show_if_values),
            )
        question_results.append(
            QuestionResult(
                id=question.id,
                question_set_version_id=question.question_set_version_id,
                key=question.question_key,
                position=question.position,
                prompt=question.prompt_text,
                helper_text=question.helper_text,
                kind=cast(QuestionKind, question.kind),
                choices=tuple(
                    _choice_result(choice) for choice in question.choice_options
                ),
                show_if=condition,
                created_at=question.created_at,
                updated_at=question.updated_at,
            )
        )
    return QuestionSetVersionResult(
        id=version.id,
        question_set_id=version.question_set_id,
        owner_practice_member_id=version.owner_practice_member_id,
        version_number=version.version_number,
        name=version.name,
        supersedes_version_id=version.supersedes_version_id,
        published_at=version.published_at,
        is_default=version.is_default,
        created_at=version.created_at,
        updated_at=version.updated_at,
        questions=tuple(question_results),
    )


def _snapshot_set(
    session: Session,
    question_set: VisitPrepQuestionSet,
) -> QuestionSetResult:
    versions = list(
        session.exec(
            select(VisitPrepQuestionSetVersion).where(
                VisitPrepQuestionSetVersion.question_set_id == question_set.id,
                VisitPrepQuestionSetVersion.owner_practice_member_id
                == question_set.owner_practice_member_id,
            )
        ).all()
    )
    versions.sort(key=lambda row: (row.version_number, str(row.id)))
    return QuestionSetResult(
        id=question_set.id,
        owner_practice_member_id=question_set.owner_practice_member_id,
        seed_key=question_set.seed_key,
        created_at=question_set.created_at,
        updated_at=question_set.updated_at,
        versions=tuple(_snapshot_version(session, version) for version in versions),
    )


def _mutation_result(
    session: Session,
    *,
    question_set: VisitPrepQuestionSet,
    version: VisitPrepQuestionSetVersion,
    disposition: MutationDisposition,
) -> QuestionSetMutationResult:
    session.flush()
    session.refresh(question_set)
    session.refresh(version)
    question_set_result = _snapshot_set(session, question_set)
    version_result = next(
        item for item in question_set_result.versions if item.id == version.id
    )
    return QuestionSetMutationResult(
        question_set=question_set_result,
        version=version_result,
        disposition=disposition,
    )


def _commit(session: Session) -> None:
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise VisitPrepQuestionSetConflict(
            "question-set state changed concurrently"
        ) from exc


def list_question_sets(
    *,
    owner_practice_member_id: TypeIDType,
) -> tuple[QuestionSetResult, ...]:
    """List only one owner's sets in deterministic creation order."""

    with get_session() as session:
        question_sets = list(
            session.exec(
                select(VisitPrepQuestionSet).where(
                    VisitPrepQuestionSet.owner_practice_member_id
                    == owner_practice_member_id
                )
            ).all()
        )
        question_sets.sort(
            key=lambda row: (
                row.created_at or datetime.min.replace(tzinfo=UTC),
                str(row.id),
            )
        )
        return tuple(_snapshot_set(session, row) for row in question_sets)


def get_question_set(
    *,
    owner_practice_member_id: TypeIDType,
    question_set_id: TypeIDType,
) -> QuestionSetResult:
    """Read one set without allowing an ID to cross its owner boundary."""

    with get_session() as session:
        question_set = _owned_set(
            session,
            owner_id=owner_practice_member_id,
            question_set_id=question_set_id,
            lock=False,
        )
        return _snapshot_set(session, question_set)


def get_question_set_version(
    *,
    owner_practice_member_id: TypeIDType,
    version_id: TypeIDType,
) -> QuestionSetVersionResult:
    """Read one exact version without allowing cross-owner discovery."""

    with get_session() as session:
        version = _discover_owned_version(
            session,
            owner_id=owner_practice_member_id,
            version_id=version_id,
        )
        return _snapshot_version(session, version)


def ensure_default_question_set(
    *,
    owner_practice_member_id: TypeIDType,
    now: datetime | None = None,
) -> QuestionSetMutationResult:
    """Lazily materialize the immutable five-question starting set."""

    definition = validate_question_set_for_publish(CANONICAL_DEFAULT_QUESTION_SET)
    payload_sha256 = _creation_payload_sha256(definition)
    requested_publication_at = (
        None if now is None else _aware_utc(now, message="publication time is invalid")
    )
    with get_session() as session:
        _lock_owner(session, owner_practice_member_id)
        existing = session.exec(
            select(VisitPrepQuestionSet)
            .where(
                VisitPrepQuestionSet.owner_practice_member_id
                == owner_practice_member_id,
                VisitPrepQuestionSet.seed_key == DEFAULT_QUESTION_SET_SEED_KEY,
            )
            .with_for_update()
        ).one_or_none()
        if existing is not None:
            if existing.creation_payload_sha256 != payload_sha256:
                raise VisitPrepQuestionSetConflict(
                    "seeded question-set payload fingerprint does not match"
                )
            versions = list(
                session.exec(
                    select(VisitPrepQuestionSetVersion)
                    .where(
                        VisitPrepQuestionSetVersion.question_set_id == existing.id,
                        VisitPrepQuestionSetVersion.owner_practice_member_id
                        == owner_practice_member_id,
                    )
                    .with_for_update()
                ).all()
            )
            initial = next(
                (version for version in versions if version.version_number == 1),
                None,
            )
            if initial is None:
                raise VisitPrepQuestionSetConflict(
                    "seeded question-set lineage is invalid"
                )
            questions = _question_rows(session, initial.id, lock=True)
            stored = _definition_from_rows(
                initial,
                questions,
                for_publish=True,
            )
            if stored != definition or initial.published_at is None:
                raise VisitPrepQuestionSetConflict(
                    "seeded question-set payload does not match"
                )
            result = _mutation_result(
                session,
                question_set=existing,
                version=initial,
                disposition="replayed",
            )
            _commit(session)
            return result

        current_default = session.exec(
            select(VisitPrepQuestionSetVersion)
            .where(
                VisitPrepQuestionSetVersion.owner_practice_member_id
                == owner_practice_member_id,
                cast(Any, VisitPrepQuestionSetVersion.is_default).is_(True),
            )
            .with_for_update()
        ).one_or_none()
        question_set = VisitPrepQuestionSet(
            owner_practice_member_id=owner_practice_member_id,
            seed_key=DEFAULT_QUESTION_SET_SEED_KEY,
            creation_payload_sha256=payload_sha256,
        )
        session.add(question_set)
        session.flush()
        version = VisitPrepQuestionSetVersion(
            question_set_id=question_set.id,
            owner_practice_member_id=owner_practice_member_id,
            version_number=1,
            name=definition.name,
            published_at=None,
            is_default=False,
        )
        session.add(version)
        session.flush()
        _insert_questions(session, version=version, definition=definition)
        effective_at = requested_publication_at or datetime.now(UTC)
        if version.created_at is not None and effective_at < version.created_at:
            raise VisitPrepQuestionSetValidation("publication time is invalid")
        version.published_at = effective_at
        version.is_default = current_default is None
        session.add(version)
        result = _mutation_result(
            session,
            question_set=question_set,
            version=version,
            disposition="created",
        )
        _commit(session)
        return result


def create_question_set(
    *,
    owner_practice_member_id: TypeIDType,
    idempotency_key: str,
    definition: QuestionSetVersionInput,
) -> QuestionSetMutationResult:
    """Create a custom draft, or replay the exact prior client operation."""

    normalized = validate_question_set_draft(definition)
    seed_key = _canonical_client_seed_key(idempotency_key)
    payload_sha256 = _creation_payload_sha256(normalized)
    with get_session() as session:
        _lock_owner(session, owner_practice_member_id)
        existing = session.exec(
            select(VisitPrepQuestionSet)
            .where(
                VisitPrepQuestionSet.owner_practice_member_id
                == owner_practice_member_id,
                VisitPrepQuestionSet.seed_key == seed_key,
            )
            .with_for_update()
        ).one_or_none()
        if existing is not None:
            if existing.creation_payload_sha256 != payload_sha256:
                raise VisitPrepQuestionSetConflict(
                    "idempotency key belongs to a different payload"
                )
            initial = session.exec(
                select(VisitPrepQuestionSetVersion)
                .where(
                    VisitPrepQuestionSetVersion.question_set_id == existing.id,
                    VisitPrepQuestionSetVersion.owner_practice_member_id
                    == owner_practice_member_id,
                    VisitPrepQuestionSetVersion.version_number == 1,
                )
                .with_for_update()
            ).one_or_none()
            if initial is None:
                raise VisitPrepQuestionSetConflict(
                    "idempotent question-set lineage is invalid"
                )
            questions = _question_rows(session, initial.id, lock=True)
            _definition_from_rows(
                initial,
                questions,
                for_publish=initial.published_at is not None,
            )
            # Validate the current resource before returning it, but bind the
            # key to the immutable create fingerprint above. Version 1 may have
            # been edited legitimately since the original POST.
            result = _mutation_result(
                session,
                question_set=existing,
                version=initial,
                disposition="replayed",
            )
            _commit(session)
            return result

        question_set = VisitPrepQuestionSet(
            owner_practice_member_id=owner_practice_member_id,
            seed_key=seed_key,
            creation_payload_sha256=payload_sha256,
        )
        session.add(question_set)
        session.flush()
        version = VisitPrepQuestionSetVersion(
            question_set_id=question_set.id,
            owner_practice_member_id=owner_practice_member_id,
            version_number=1,
            name=normalized.name,
            published_at=None,
            is_default=False,
        )
        session.add(version)
        session.flush()
        _insert_questions(session, version=version, definition=normalized)
        result = _mutation_result(
            session,
            question_set=question_set,
            version=version,
            disposition="created",
        )
        _commit(session)
        return result


def replace_question_set_draft(
    *,
    owner_practice_member_id: TypeIDType,
    version_id: TypeIDType,
    expected_updated_at: datetime,
    definition: QuestionSetVersionInput,
    now: datetime | None = None,
) -> QuestionSetMutationResult:
    """Replace a whole draft with optimistic concurrency and exact replay."""

    normalized = validate_question_set_draft(definition)
    expected = _aware_utc(
        expected_updated_at,
        message="expected update time is invalid",
    )
    effective_at = _aware_utc(now, message="update time is invalid")
    with get_session() as session:
        _lock_owner(session, owner_practice_member_id)
        question_set, version = _lock_owned_version(
            session,
            owner_id=owner_practice_member_id,
            version_id=version_id,
        )
        if version.published_at is not None:
            raise VisitPrepQuestionSetConflict(
                "published question-set versions cannot be edited"
            )
        questions = _question_rows(session, version.id, lock=True)
        current = _definition_from_rows(version, questions, for_publish=False)
        if current == normalized:
            result = _mutation_result(
                session,
                question_set=question_set,
                version=version,
                disposition="replayed",
            )
            _commit(session)
            return result
        if not _same_instant(version.updated_at, expected):
            raise VisitPrepQuestionSetConflict("question-set draft is stale")
        if version.updated_at is not None and effective_at <= version.updated_at:
            effective_at = datetime.now(UTC)
        if version.updated_at is not None and effective_at <= version.updated_at:
            raise VisitPrepQuestionSetConflict("question-set update clock is stale")

        version.name = normalized.name
        version.updated_at = effective_at
        session.add(version)
        _replace_questions(
            session,
            version=version,
            existing=questions,
            definition=normalized,
        )
        result = _mutation_result(
            session,
            question_set=question_set,
            version=version,
            disposition="updated",
        )
        _commit(session)
        return result


def create_successor_draft(
    *,
    owner_practice_member_id: TypeIDType,
    source_version_id: TypeIDType,
    name: str | None = None,
) -> QuestionSetMutationResult:
    """Create the one linear draft successor of a published version."""

    normalized_name = (
        None
        if name is None
        else _normalized_text(
            name,
            maximum=_MAX_NAME_LENGTH,
            field_message="question-set name is invalid",
        )
    )
    with get_session() as session:
        _lock_owner(session, owner_practice_member_id)
        discovered = _discover_owned_version(
            session,
            owner_id=owner_practice_member_id,
            version_id=source_version_id,
        )
        question_set = _owned_set(
            session,
            owner_id=owner_practice_member_id,
            question_set_id=discovered.question_set_id,
            lock=True,
        )
        versions = list(
            session.exec(
                select(VisitPrepQuestionSetVersion)
                .where(
                    VisitPrepQuestionSetVersion.question_set_id == question_set.id,
                    VisitPrepQuestionSetVersion.owner_practice_member_id
                    == owner_practice_member_id,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            ).all()
        )
        versions.sort(key=lambda row: (row.version_number, str(row.id)))
        source = next(
            (version for version in versions if version.id == source_version_id),
            None,
        )
        if source is None:
            raise VisitPrepQuestionSetNotFound("question-set version was not found")
        successors = [
            version
            for version in versions
            if version.supersedes_version_id == source.id
        ]
        if len(successors) > 1:
            raise VisitPrepQuestionSetConflict(
                "question-set version lineage is not linear"
            )
        if source.published_at is None:
            raise VisitPrepQuestionSetConflict(
                "only a published question-set version can be superseded"
            )
        if successors:
            successor = successors[0]
            if successor.published_at is not None or versions[-1].id != successor.id:
                raise VisitPrepQuestionSetConflict(
                    "question-set successor is no longer an editable lineage tip"
                )
            requested_name = normalized_name or source.name
            if successor.name != requested_name:
                raise VisitPrepQuestionSetConflict(
                    "question-set successor already exists with a different name"
                )
            result = _mutation_result(
                session,
                question_set=question_set,
                version=successor,
                disposition="replayed",
            )
            _commit(session)
            return result
        if versions[-1].id != source.id:
            raise VisitPrepQuestionSetConflict(
                "question-set successor must extend the current lineage tip"
            )

        source_questions = _question_rows(session, source.id, lock=True)
        source_definition = _definition_from_rows(
            source,
            source_questions,
            for_publish=True,
        )
        successor_definition = QuestionSetVersionInput(
            name=normalized_name or source_definition.name,
            questions=source_definition.questions,
        )
        successor = VisitPrepQuestionSetVersion(
            question_set_id=question_set.id,
            owner_practice_member_id=owner_practice_member_id,
            version_number=source.version_number + 1,
            name=successor_definition.name,
            supersedes_version_id=source.id,
            published_at=None,
            is_default=False,
        )
        session.add(successor)
        session.flush()
        _insert_questions(
            session,
            version=successor,
            definition=successor_definition,
        )
        result = _mutation_result(
            session,
            question_set=question_set,
            version=successor,
            disposition="created",
        )
        _commit(session)
        return result


def _clear_other_defaults(
    session: Session,
    *,
    owner_id: TypeIDType,
    target_version_id: TypeIDType,
) -> bool:
    current_defaults = list(
        session.exec(
            select(VisitPrepQuestionSetVersion)
            .where(
                VisitPrepQuestionSetVersion.owner_practice_member_id == owner_id,
                cast(Any, VisitPrepQuestionSetVersion.is_default).is_(True),
            )
            .with_for_update()
        ).all()
    )
    changed = False
    for version in current_defaults:
        if version.id == target_version_id:
            continue
        version.is_default = False
        session.add(version)
        changed = True
    if changed:
        session.flush()
    return changed


def publish_question_set_version(
    *,
    owner_practice_member_id: TypeIDType,
    version_id: TypeIDType,
    make_default: bool = False,
    now: datetime | None = None,
) -> QuestionSetMutationResult:
    """Validate and publish an exact version while holding its row lock."""

    effective_at = _aware_utc(now, message="publication time is invalid")
    with get_session() as session:
        _lock_owner(session, owner_practice_member_id)
        question_set, version = _lock_owned_version(
            session,
            owner_id=owner_practice_member_id,
            version_id=version_id,
        )
        # The version lock above deliberately precedes this child read.  It is
        # the publication fence shared with draft replacement and DB triggers.
        questions = _question_rows(session, version.id, lock=True)
        _definition_from_rows(version, questions, for_publish=True)

        changed = False
        if version.published_at is None:
            if version.created_at is not None and effective_at < version.created_at:
                raise VisitPrepQuestionSetValidation("publication time is invalid")
            version.published_at = effective_at
            changed = True
        if make_default:
            changed = (
                _clear_other_defaults(
                    session,
                    owner_id=owner_practice_member_id,
                    target_version_id=version.id,
                )
                or changed
            )
            if not version.is_default:
                version.is_default = True
                changed = True
        session.add(version)
        result = _mutation_result(
            session,
            question_set=question_set,
            version=version,
            disposition="updated" if changed else "replayed",
        )
        _commit(session)
        return result


def set_default_question_set_version(
    *,
    owner_practice_member_id: TypeIDType,
    version_id: TypeIDType,
) -> QuestionSetMutationResult:
    """Select exactly one published owner-scoped version as the default."""

    with get_session() as session:
        _lock_owner(session, owner_practice_member_id)
        question_set, version = _lock_owned_version(
            session,
            owner_id=owner_practice_member_id,
            version_id=version_id,
        )
        if version.published_at is None:
            raise VisitPrepQuestionSetConflict(
                "only a published question-set version can be selected"
            )
        changed = _clear_other_defaults(
            session,
            owner_id=owner_practice_member_id,
            target_version_id=version.id,
        )
        if not version.is_default:
            version.is_default = True
            session.add(version)
            changed = True
        result = _mutation_result(
            session,
            question_set=question_set,
            version=version,
            disposition="updated" if changed else "replayed",
        )
        _commit(session)
        return result
