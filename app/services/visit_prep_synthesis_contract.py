"""Bounded patient-source selection and a cited pre-visit synthesis contract.

Not an authorization service or a clinical record. Only a caller that has
authorized the current package may construct a selection. Retained original or
hidden answers never become current model inputs. Core, not the model, supplies
package identity, completion/review state and the exact source version.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from typeid import TypeID
from typeid.errors import TypeIDException

from app.services.visit_prep_contribution_contract import ContributionBasis
from app.services.visit_prep_packages import VisitPrepSourcePackage
from app.services.visit_prep_question_definitions import QuestionKind

GENERATOR_VERSION = "patient_sources_v1"
PROMPT_VERSION = "patient_report_only_v1"
MAX_SOURCE_CHARACTERS = 20_000
MAX_OUTPUT_CHARACTERS = 16_000
MAX_BRIEF_WORDS = 220
_KEY = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_VERSION = re.compile(r"^[0-9a-f]{64}$")
_NUMBERS = re.compile(r"(?<![\w.])[+-]?\d+(?:[.,]\d+)*%?")
_UNSAFE_OUTPUT = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\b(?:diagnosis\s+is|diagnos(?:e|ed)\s+(?:as|with)|(?:new|confirmed|definitive)\s+diagnosis|this\s+confirms?\s+(?:a\s+|the\s+)?diagnosis)\b",
        r"(?:^|[.!?]\s*)(?:please\s+)?(?:start|stop|increase|decrease)\b(?!\s+(?:with|by|the\s+(?:visit|session|discussion|review)))",
        r"\b(?:start|stop|increase|decrease)\b[^.!?\n]{0,80}\b(?:taking|using|dose|dosage|medication|medicine|drug|prescription|mg|mcg|tablet|capsule)s?\b",
        r"\b(?:avoid|delay|defer|postpone|skip|do\s+not\s+seek|don't\s+seek)\b[^.!?\n]{0,80}\b(?:urgent[- ]care|emergency\s+(?:care|department)|ER|911)\b",
        r"\b(?:EHR|chart|clinically)\s+(?:verified|confirmed)\b",
        r"\b(?:https?://|www\.|vp1_[A-Za-z0-9_-]{43}\b)",
        r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}",
        r"\+\d[\d ()-]{7,}\d",
    )
)


class VisitPrepSynthesisInvalid(ValueError):
    def __init__(self) -> None:
        super().__init__("Visit Prep synthesis is invalid")


def _canonical_ref(value: object, prefixes: set[str]) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = TypeID.from_string(value)
        return parsed.prefix in prefixes and str(parsed) == value
    except (TypeIDException, ValueError):
        return False


def _text(value: str) -> str:
    if not value.strip() or any(
        character != "\n" and unicodedata.category(character).startswith("C")
        for character in value
    ):
        raise ValueError("Text is invalid")
    return value


class _ClosedModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, frozen=True, hide_input_in_errors=True
    )


class VisitPrepBriefEvidence(_ClosedModel):
    source_ref: str = Field(min_length=1, max_length=80)
    quote: str = Field(min_length=1, max_length=300, repr=False)

    _quote_text = field_validator("quote")(_text)


class VisitPrepBriefItem(_ClosedModel):
    text: str = Field(min_length=1, max_length=700, repr=False)
    evidence: list[VisitPrepBriefEvidence] = Field(
        min_length=1, max_length=5, repr=False
    )

    _item_text = field_validator("text")(_text)


class VisitPrepPreparedBrief(_ClosedModel):
    """Model interpretation, always displayed alongside the patient's sources.

    Empty sections are meaningful. Missing/skipped topics and completion/review
    flags come from Core; the model cannot invent or change those declarations.
    """

    headline: VisitPrepBriefItem | None = Field(repr=False)
    summary: list[VisitPrepBriefItem] = Field(max_length=5, repr=False)
    visit_priorities: list[VisitPrepBriefItem] = Field(max_length=3, repr=False)
    medication_and_adherence: list[VisitPrepBriefItem] = Field(max_length=3, repr=False)
    barriers: list[VisitPrepBriefItem] = Field(max_length=2, repr=False)
    uncertainties: list[VisitPrepBriefItem] = Field(max_length=3, repr=False)

    def items(self) -> tuple[VisitPrepBriefItem, ...]:
        return (
            *((self.headline,) if self.headline is not None else ()),
            *self.summary,
            *self.visit_priorities,
            *self.medication_and_adherence,
            *self.barriers,
            *self.uncertainties,
        )


@dataclass(frozen=True)
class VisitPrepSynthesisSource:
    question_key: str
    position: int
    prompt: str = field(repr=False)
    kind: QuestionKind
    source_ref: str
    recorded_at: datetime
    answer: str = field(repr=False)
    choice_label: str | None = field(default=None, repr=False)

    @property
    def reported_text(self) -> str:
        return self.choice_label if self.choice_label is not None else self.answer


@dataclass(frozen=True)
class VisitPrepSynthesisSelection:
    package_id: str
    source_version: str
    question_set_version_id: str
    basis: ContributionBasis
    actor_role: Literal["patient", "caregiver"]
    original_interview_completed: bool
    current_answers_complete: bool
    current_answers_reviewed: bool
    applicable_question_keys: tuple[str, ...]
    missing_question_keys: tuple[str, ...]
    skipped_question_keys: tuple[str, ...]
    sources: tuple[VisitPrepSynthesisSource, ...] = field(repr=False)


def select_patient_sources(
    package: VisitPrepSourcePackage,
) -> VisitPrepSynthesisSelection:
    contribution = package.contribution
    sources: list[VisitPrepSynthesisSource] = []
    applicable = [topic for topic in contribution.topics if topic.applicable]
    for topic in applicable:
        if topic.response_kind != "answered":
            continue
        ref = topic.current_correction_id or topic.original_response_id
        if ref is None or topic.recorded_at is None or topic.answer is None:
            raise VisitPrepSynthesisInvalid()
        label = None
        if topic.question.kind == "choice":
            choices = [
                choice.label
                for choice in topic.question.choices
                if choice.value == topic.answer
            ]
            if len(choices) != 1:
                raise VisitPrepSynthesisInvalid()
            label = choices[0]
        sources.append(
            VisitPrepSynthesisSource(
                question_key=topic.question.key,
                position=topic.question.position,
                prompt=topic.question.prompt,
                kind=topic.question.kind,
                source_ref=str(ref),
                recorded_at=topic.recorded_at,
                answer=topic.answer,
                choice_label=label,
            )
        )
    selection = VisitPrepSynthesisSelection(
        package_id=str(package.preparation.id),
        source_version=package.source_version,
        question_set_version_id=str(package.preparation.question_set_version_id),
        basis=contribution.basis,
        actor_role=contribution.actor_role,
        original_interview_completed=contribution.original_interview_completed,
        current_answers_complete=contribution.current_answers_complete,
        current_answers_reviewed=contribution.current_answers_reviewed,
        applicable_question_keys=tuple(topic.question.key for topic in applicable),
        missing_question_keys=contribution.missing_question_keys,
        skipped_question_keys=tuple(
            topic.question.key
            for topic in applicable
            if topic.response_kind == "skipped"
        ),
        sources=tuple(sources),
    )
    validate_selection(selection)
    return selection


def validate_selection(selection: VisitPrepSynthesisSelection) -> None:
    keys = selection.applicable_question_keys
    answered = tuple(source.question_key for source in selection.sources)
    statuses = (
        *answered,
        *selection.missing_question_keys,
        *selection.skipped_question_keys,
    )
    if (
        not _canonical_ref(selection.package_id, {"visit_prep"})
        or not _canonical_ref(
            selection.question_set_version_id, {"visit_prep_question_set_version"}
        )
        or _VERSION.fullmatch(selection.source_version) is None
        or not isinstance(selection.basis, ContributionBasis)
        or selection.actor_role not in {"patient", "caregiver"}
        or not 1 <= len(keys) <= 5
        or "story" not in keys
        or len(set(keys)) != len(keys)
        or any(_KEY.fullmatch(key) is None for key in keys)
        or len(set(statuses)) != len(statuses)
        or set(statuses) != set(keys)
        or len({source.source_ref for source in selection.sources})
        != len(selection.sources)
        or len({source.position for source in selection.sources})
        != len(selection.sources)
        or tuple(sorted(selection.sources, key=lambda source: source.position))
        != selection.sources
        or selection.current_answers_complete != (not selection.missing_question_keys)
        or (
            selection.current_answers_reviewed
            and not selection.current_answers_complete
        )
        or any(
            type(flag) is not bool
            for flag in (
                selection.original_interview_completed,
                selection.current_answers_complete,
                selection.current_answers_reviewed,
            )
        )
    ):
        raise VisitPrepSynthesisInvalid()
    for source in selection.sources:
        if (
            not _canonical_ref(
                source.source_ref, {"visit_prep_response", "visit_prep_contribution"}
            )
            or type(source.position) is not int
            or not 1 <= source.position <= 5
            or source.kind not in {"story", "list", "choice"}
            or source.recorded_at.tzinfo is None
            or source.recorded_at.utcoffset() is None
            or not 1 <= len(source.answer.strip()) <= 2000
            or not 1 <= len(source.prompt.strip()) <= 1000
            or (source.kind == "choice") != (source.choice_label is not None)
            or (
                source.choice_label is not None
                and not 1 <= len(source.choice_label.strip()) <= 120
            )
        ):
            raise VisitPrepSynthesisInvalid()


def validate_generated_brief(
    prepared: VisitPrepPreparedBrief, selection: VisitPrepSynthesisSelection
) -> None:
    """Mechanical grounding and bounded egress, not semantic clinical validation."""
    validate_selection(selection)
    # Also defend the injected-generator/loaded-artifact boundary: model_copy
    # and model_construct intentionally bypass Pydantic validation.
    try:
        prepared = VisitPrepPreparedBrief.model_validate(prepared.model_dump())
    except (AttributeError, TypeError, ValidationError):
        raise VisitPrepSynthesisInvalid() from None
    items = prepared.items()
    if (
        (
            bool(selection.sources)
            and (prepared.headline is None or not prepared.summary)
        )
        or (not selection.sources and bool(items))
        or len(prepared.model_dump_json()) > MAX_OUTPUT_CHARACTERS
        or sum(len(item.text.split()) for item in items) > MAX_BRIEF_WORDS
    ):
        raise VisitPrepSynthesisInvalid()
    sources = {source.source_ref: source for source in selection.sources}
    for item in items:
        refs = [evidence.source_ref for evidence in item.evidence]
        if len(refs) != len(set(refs)) or not set(refs) <= sources.keys():
            raise VisitPrepSynthesisInvalid()
        for evidence in item.evidence:
            reported = sources[evidence.source_ref].reported_text
            if evidence.quote not in reported or not set(
                _NUMBERS.findall(evidence.quote)
            ) <= set(_NUMBERS.findall(reported)):
                raise VisitPrepSynthesisInvalid()
        numbers = set(
            _NUMBERS.findall(" ".join(evidence.quote for evidence in item.evidence))
        )
        if not set(_NUMBERS.findall(item.text)) <= numbers or any(
            pattern.search(item.text) for pattern in _UNSAFE_OUTPUT
        ):
            raise VisitPrepSynthesisInvalid()
        if any(
            pattern.search(evidence.quote)
            for evidence in item.evidence
            for pattern in _UNSAFE_OUTPUT[5:]
        ):
            raise VisitPrepSynthesisInvalid()
