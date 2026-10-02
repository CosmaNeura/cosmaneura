"""Dormant patient clarification rules, not a model runtime or permission grant.

The caller must freshly authorize the current contribution before selection and
again before disclosure/confirmation. These rules preserve one physician topic,
use only quoted patient words, and produce an uncommitted patient decision.
No route, database write, token, provider or cross-repository call is introduced.
Conversational model access remains outside Core's batch runtime.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from typeid import TypeID
from typeid.errors import TypeIDException

from app.services.visit_prep_contribution_contract import ContributionBasis
from app.services.visit_prep_interview_contract import (
    ResponseInput,
    VisitPrepInterviewValidation,
    _normalized_answer,
)
from app.services.visit_prep_packages import VisitPrepSourcePackage

MAX_CLARIFICATION_TURNS = 2
MAX_REPLY_BYTES = 4_096
MAX_QUOTED_STATEMENTS = 5
MAX_CONFIRMED_CHARACTERS = 2_000
PROMPT_VERSION = "patient_clarification_quotes_v1"

PromptCode = Literal["detail", "sequence", "priority", "background"]
WordSource = Literal["answer", "clarification_1", "clarification_2"]
DecisionAction = Literal["confirm", "edit", "keep_original", "dismiss"]

# Model-selected codes, never model-authored questions, treatment or reassurance.
PROMPTS: Mapping[PromptCode, str] = MappingProxyType(
    {
        "detail": "Is there anything you would like to make clearer about this answer?",
        "sequence": "Would you like to add the order in which these events happened?",
        "priority": "What part of this answer would you most like your provider to understand?",
        "background": "Is there any background you want your provider to have for this answer?",
    }
)


class VisitPrepClarificationInvalid(ValueError):
    def __init__(self) -> None:
        super().__init__("Visit Prep clarification is unavailable")


class VisitPrepClarificationChanged(ValueError):
    def __init__(self) -> None:
        super().__init__("Visit Prep contribution state changed")


@dataclass(frozen=True)
class PatientClarificationTurn:
    prompt_code: PromptCode
    patient_words: str = field(repr=False)


@dataclass(frozen=True)
class VisitPrepClarificationContext:
    """Private immutable observation, never proof of disclosure/write authority."""

    package_id: str
    source_version: str
    question_set_version_id: str
    basis: ContributionBasis
    actor_role: Literal["patient", "caregiver"]
    question_key: str
    position: int
    kind: Literal["story", "list", "choice"]
    prompt: str = field(repr=False)
    answer: str = field(repr=False)
    reported_words: str = field(repr=False)
    current_source_ref: str
    turns: tuple[PatientClarificationTurn, ...] = field(default=(), repr=False)


class PatientQuoteSpan(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, frozen=True, hide_input_in_errors=True
    )

    source: WordSource
    start: int = Field(ge=0, le=2_000)
    end: int = Field(gt=0, le=2_000)
    quote: str = Field(min_length=1, max_length=2_000, repr=False)


class PatientClarificationReply(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, frozen=True, hide_input_in_errors=True
    )

    kind: Literal["clarify", "organize", "unavailable"]
    prompt_code: PromptCode | None
    statements: tuple[PatientQuoteSpan, ...] = Field(
        max_length=MAX_QUOTED_STATEMENTS, repr=False
    )


@dataclass(frozen=True)
class PatientClarificationDecision:
    action: DecisionAction
    edited_words: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class PatientConfirmedClarification:
    """Intent only; a future writer must authorize and atomically bind evidence.

    Text topics can use the existing patient correction command. A choice stays
    a choice; supplementary patient words require a separately qualified source
    write and MUST NOT be passed to that command as a choice value.
    """

    package_id: str
    source_version: str
    question_set_version_id: str
    question_key: str
    current_source_ref: str
    basis: ContributionBasis
    actor_role: Literal["patient", "caregiver"]
    action: Literal["confirm", "edit"]
    patient_words: str = field(repr=False)
    response: ResponseInput = field(repr=False)
    supplemental_words: str | None = field(repr=False)
    prompt_version: str = PROMPT_VERSION


def _ref(value: object, prefixes: set[str]) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = TypeID.from_string(value)
        return parsed.prefix in prefixes and str(parsed) == value
    except (ValueError, TypeIDException):
        return False


def _words(value: object) -> str:
    try:
        result = _normalized_answer(value)
    except VisitPrepInterviewValidation:
        raise VisitPrepClarificationInvalid() from None
    if result != value:
        raise VisitPrepClarificationInvalid()
    return result


def validate_clarification_context(context: VisitPrepClarificationContext) -> None:
    if (
        type(context) is not VisitPrepClarificationContext
        or not _ref(context.package_id, {"visit_prep"})
        or not _ref(
            context.question_set_version_id, {"visit_prep_question_set_version"}
        )
        or not _ref(
            context.current_source_ref,
            {"visit_prep_response", "visit_prep_contribution"},
        )
        or not isinstance(context.source_version, str)
        or re.fullmatch(r"[0-9a-f]{64}", context.source_version) is None
        or type(context.basis) is not ContributionBasis
        or context.basis.original_response_count == 0
        or not isinstance(context.actor_role, str)
        or context.actor_role not in {"patient", "caregiver"}
        or not isinstance(context.question_key, str)
        or re.fullmatch(r"[a-z][a-z0-9_]{0,63}", context.question_key) is None
        or type(context.position) is not int
        or not 1 <= context.position <= 5
        or not isinstance(context.kind, str)
        or context.kind not in {"story", "list", "choice"}
        or not isinstance(context.prompt, str)
        or not 1 <= len(context.prompt) <= 1_000
        or type(context.turns) is not tuple
        or len(context.turns) > MAX_CLARIFICATION_TURNS
    ):
        raise VisitPrepClarificationInvalid()
    _words(context.answer)
    _words(context.reported_words)
    _words(context.prompt)
    try:
        ContributionBasis(
            context.basis.original_response_count, context.basis.event_sequence
        )
    except VisitPrepInterviewValidation:
        raise VisitPrepClarificationInvalid() from None
    if context.kind != "choice" and context.answer != context.reported_words:
        raise VisitPrepClarificationInvalid()
    for turn in context.turns:
        if (
            type(turn) is not PatientClarificationTurn
            or not isinstance(turn.prompt_code, str)
            or turn.prompt_code not in PROMPTS
        ):
            raise VisitPrepClarificationInvalid()
        _words(turn.patient_words)
    if len({turn.prompt_code for turn in context.turns}) != len(context.turns):
        raise VisitPrepClarificationInvalid()


def select_clarification_context(
    package: VisitPrepSourcePackage,
    *,
    question_key: str,
) -> VisitPrepClarificationContext:
    """Select one answered applicable topic from an already-authorized package.

    A skipped, missing or hidden topic never becomes implicit AI interview input.
    Original/superseded answers and other topics do not enter this context.
    """
    if type(package) is not VisitPrepSourcePackage:
        raise VisitPrepClarificationInvalid()
    topics = package.contribution.topics
    if (
        len(topics) != 5
        or tuple(topic.question.position for topic in topics) != (1, 2, 3, 4, 5)
        or len({topic.question.key for topic in topics}) != 5
        or topics[-1].question.key != "story"
        or topics[-1].question.kind != "story"
        or topics[-1].applicable is not True
    ):
        raise VisitPrepClarificationInvalid()
    selected = [topic for topic in topics if topic.question.key == question_key]
    if len(selected) != 1:
        raise VisitPrepClarificationInvalid()
    topic = selected[0]
    source = topic.current_correction_id or topic.original_response_id
    if (
        not topic.applicable
        or topic.response_kind != "answered"
        or topic.answer is None
        or source is None
    ):
        raise VisitPrepClarificationInvalid()
    reported = topic.answer
    if topic.question.kind == "choice":
        choices = [
            choice.label
            for choice in topic.question.choices
            if choice.value == topic.answer
        ]
        if len(choices) != 1:
            raise VisitPrepClarificationInvalid()
        reported = choices[0]
    context = VisitPrepClarificationContext(
        package_id=str(package.preparation.id),
        source_version=package.source_version,
        question_set_version_id=str(package.preparation.question_set_version_id),
        basis=package.contribution.basis,
        actor_role=package.contribution.actor_role,
        question_key=topic.question.key,
        position=topic.question.position,
        kind=topic.question.kind,
        prompt=topic.question.prompt,
        answer=topic.answer,
        reported_words=reported,
        current_source_ref=str(source),
    )
    validate_clarification_context(context)
    return context


def validate_clarification_reply(
    context: VisitPrepClarificationContext, reply: PatientClarificationReply
) -> str | None:
    """Validate a closed quote-only interpretation; return no generated prose."""
    validate_clarification_context(context)
    try:
        if type(reply) is not PatientClarificationReply:
            raise ValueError
        reply = PatientClarificationReply.model_validate(
            reply.model_dump(warnings="error")
        )
        if len(reply.model_dump_json().encode("utf-8")) > MAX_REPLY_BYTES:
            raise ValueError
    except (ValueError, TypeError, ValidationError):
        raise VisitPrepClarificationInvalid() from None
    if reply.kind == "unavailable":
        if reply.prompt_code is not None or reply.statements:
            raise VisitPrepClarificationInvalid()
        return None
    if reply.kind == "clarify":
        if (
            reply.prompt_code not in PROMPTS
            or reply.statements
            or len(context.turns) >= MAX_CLARIFICATION_TURNS
            or any(turn.prompt_code == reply.prompt_code for turn in context.turns)
        ):
            raise VisitPrepClarificationInvalid()
        return None
    if reply.prompt_code is not None or not reply.statements:
        raise VisitPrepClarificationInvalid()
    words = {"answer": context.reported_words}
    words.update(
        {
            f"clarification_{i + 1}": turn.patient_words
            for i, turn in enumerate(context.turns)
        }
    )
    spans: dict[str, list[tuple[int, int]]] = {}
    for statement in reply.statements:
        text = words.get(statement.source)
        if (
            text is None
            or not 0 <= statement.start < statement.end <= len(text)
            or statement.quote != text[statement.start : statement.end]
        ):
            raise VisitPrepClarificationInvalid()
        _words(statement.quote)
        existing = spans.setdefault(statement.source, [])
        if any(
            statement.start < end and start < statement.end for start, end in existing
        ):
            raise VisitPrepClarificationInvalid()
        existing.append((statement.start, statement.end))
    organized = "\n".join(statement.quote for statement in reply.statements)
    if len(organized) > MAX_CONFIRMED_CHARACTERS:
        raise VisitPrepClarificationInvalid()
    return organized


def append_patient_clarification(
    context: VisitPrepClarificationContext,
    reply: PatientClarificationReply,
    *,
    patient_words: str,
) -> VisitPrepClarificationContext:
    """Add explicit patient words to a validated neutral prompt, not to storage."""
    validate_clarification_reply(context, reply)
    if reply.kind != "clarify" or reply.prompt_code is None:
        raise VisitPrepClarificationInvalid()
    words = _words(patient_words)
    updated = replace(
        context,
        turns=(*context.turns, PatientClarificationTurn(reply.prompt_code, words)),
    )
    validate_clarification_context(updated)
    return updated


def confirm_patient_clarification(
    context: VisitPrepClarificationContext,
    reply: PatientClarificationReply,
    decision: PatientClarificationDecision,
    *,
    current_package: VisitPrepSourcePackage,
) -> PatientConfirmedClarification | None:
    """Prepare patient confirmation against freshly authorized current sources.

    No write/consent/identity proof. A caller MUST persist the exact presented
    proposal, actual patient-action evidence and current authority atomically
    before claiming a saved AI-assisted answer. Context equality is not authority.
    """
    organized = validate_clarification_reply(context, reply)
    current = select_clarification_context(
        current_package, question_key=context.question_key
    )
    if replace(context, turns=()) != current:
        raise VisitPrepClarificationChanged()
    if (
        type(decision) is not PatientClarificationDecision
        or not isinstance(decision.action, str)
        or decision.action not in {"confirm", "edit", "keep_original", "dismiss"}
    ):
        raise VisitPrepClarificationInvalid()
    if decision.action == "keep_original" or decision.action == "dismiss":
        if decision.edited_words is not None:
            raise VisitPrepClarificationInvalid()
        return None
    if reply.kind != "organize" or organized is None:
        raise VisitPrepClarificationInvalid()
    if decision.action != "confirm" and decision.action != "edit":
        raise VisitPrepClarificationInvalid()
    if decision.action == "edit":
        confirmed = _words(decision.edited_words)
    else:
        if decision.edited_words is not None:
            raise VisitPrepClarificationInvalid()
        confirmed = organized
    choice = context.kind == "choice"
    return PatientConfirmedClarification(
        package_id=context.package_id,
        source_version=context.source_version,
        question_set_version_id=context.question_set_version_id,
        question_key=context.question_key,
        current_source_ref=context.current_source_ref,
        basis=context.basis,
        actor_role=context.actor_role,
        action=decision.action,
        patient_words=confirmed,
        response=ResponseInput(
            context.question_key, "answered", context.answer if choice else confirmed
        ),
        supplemental_words=confirmed
        if choice and confirmed != context.reported_words
        else None,
    )
