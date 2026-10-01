"""Visit Prep question definitions, publication rules and immutable defaults.

Pure authored-content contract; persistence and authorization remain in the
question-set lifecycle service.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Literal, Sequence, cast
from uuid import UUID

from activemodel.types import TypeIDType

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

    if for_publish:
        narrative = normalized_questions[-1]
        if narrative.kind != "story" or narrative.show_if is not None:
            raise VisitPrepQuestionSetValidation(
                "the final topic must be an unconditional free-form story"
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
    """Validate five topics, ending with the patient's open-ended narrative."""

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
