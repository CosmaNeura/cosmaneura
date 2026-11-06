"""Founder-five defaults and per-physician question set versioning."""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Sequence, cast
from uuid import UUID

from activemodel.session_manager import get_session
from activemodel.types import TypeIDType
from sqlmodel import select

from app.models.visit_prep_question_set import (
    VisitPrepQuestion, VisitPrepQuestionChoice, VisitPrepQuestionSet, VisitPrepQuestionSetVersion,
)

QuestionKind = Literal["story", "list", "choice"]
_SAFE_KEY = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

class VisitPrepQuestionError(Exception):
    pass

class VisitPrepQuestionNotFound(VisitPrepQuestionError):
    pass

class VisitPrepQuestionValidation(VisitPrepQuestionError, ValueError):
    pass

class VisitPrepQuestionConflict(VisitPrepQuestionError):
    pass

@dataclass(frozen=True)
class ChoiceIn:
    value: str; label: str; hint: str | None = None

@dataclass(frozen=True)
class ConditionIn:
    question_key: str; values: tuple[str, ...]

@dataclass(frozen=True)
class QuestionIn:
    key: str; prompt: str; kind: QuestionKind
    helper_text: str | None = None; choices: tuple[ChoiceIn, ...] = (); show_if: ConditionIn | None = None

@dataclass(frozen=True)
class VersionIn:
    name: str; questions: tuple[QuestionIn, ...] = ()

@dataclass(frozen=True)
class QuestionOut:
    id: TypeIDType; key: str; position: int; prompt: str; helper_text: str | None
    kind: QuestionKind; choices: tuple[ChoiceIn, ...]; show_if: ConditionIn | None

@dataclass(frozen=True)
class VersionOut:
    id: TypeIDType; question_set_id: TypeIDType; version_number: int; name: str
    published_at: datetime | None; is_default: bool; questions: tuple[QuestionOut, ...]

DEFAULT_SEED, DEFAULT_NAME = "default.v1", "Visit Prep default questions"
CANONICAL_DEFAULT = VersionIn(name=DEFAULT_NAME, questions=(
    QuestionIn("reason", "What is the main reason for this visit?", "story",
               "In your own words. The thing you most want understood before you sit down."),
    QuestionIn("medications", "What medications are you prescribed right now?", "list",
               "Include the dose if you know it. Over-the-counter and supplements count too."),
    QuestionIn("adherence", "Are you taking them as prescribed?", "choice",
               "There is no wrong answer. Honesty here saves the visit.", (
                   ChoiceIn("yes", "Yes, as prescribed", "Most days, as written"),
                   ChoiceIn("some", "Some of them", "A few get skipped"),
                   ChoiceIn("no", "Not really", "Something got in the way"),
                   ChoiceIn("none", "I don't take any"),
               )),
    QuestionIn("adherence_barrier", "If not — what got in the way?", "story",
               "Cost, side effects, a chaotic morning, forgetting, not being sure it helps.",
               show_if=ConditionIn("adherence", ("some", "no"))),
    QuestionIn("story", "Tell me your health story — whatever feels relevant.", "story",
               "What has been happening in your body, and in your life, that belongs in this visit?"),
))

def _text(value: object, *, maximum: int, message: str) -> str:
    if not isinstance(value, str):
        raise VisitPrepQuestionValidation(message)
    normalized = unicodedata.normalize("NFC", value.replace("\r\n", "\n")).strip()
    if (not normalized or len(normalized) > maximum
            or any(unicodedata.category(c).startswith("C") and c not in "\n\t" for c in normalized)):
        raise VisitPrepQuestionValidation(message)
    return normalized

def _key(value: object) -> str:
    if not isinstance(value, str) or _SAFE_KEY.fullmatch(value) is None:
        raise VisitPrepQuestionValidation("question key is invalid")
    return value

def normalize_version(definition: VersionIn, *, for_publish: bool) -> VersionIn:
    if not isinstance(definition, VersionIn):
        raise VisitPrepQuestionValidation("question-set payload is invalid")
    if len(definition.questions) > 5 or (for_publish and len(definition.questions) != 5):
        raise VisitPrepQuestionValidation("question count is invalid")
    name = _text(definition.name, maximum=120, message="name is invalid")
    seen: set[str] = set(); choices_by_key: dict[str, frozenset[str]] = {}; out: list[QuestionIn] = []
    for raw in definition.questions:
        key = _key(raw.key)
        if key in seen:
            raise VisitPrepQuestionValidation("question keys must be unique")
        seen.add(key)
        if raw.kind not in {"story", "list", "choice"}:
            raise VisitPrepQuestionValidation("question kind is invalid")
        kind = cast(QuestionKind, raw.kind)
        choices: list[ChoiceIn] = []; values: set[str] = set()
        for choice in raw.choices:
            value = _key(choice.value)
            if value in values:
                raise VisitPrepQuestionValidation("question choice values must be unique")
            values.add(value)
            hint = None if choice.hint is None else _text(choice.hint, maximum=500, message="choice hint is invalid")
            choices.append(ChoiceIn(value, _text(choice.label, maximum=120, message="choice label is invalid"), hint))
        if kind == "choice" and len(choices) < 2:
            raise VisitPrepQuestionValidation("choice questions need at least two choices")
        if kind != "choice" and choices:
            raise VisitPrepQuestionValidation("non-choice questions cannot have choices")
        show_if = None
        if raw.show_if is not None:
            parent = _key(raw.show_if.question_key)
            if parent not in choices_by_key:
                raise VisitPrepQuestionValidation("condition parent is invalid")
            vals = tuple(_key(v) for v in raw.show_if.values)
            if not vals or any(v not in choices_by_key[parent] for v in vals):
                raise VisitPrepQuestionValidation("condition values are invalid")
            show_if = ConditionIn(parent, vals)
        if kind == "choice":
            choices_by_key[key] = frozenset(values)
        helper = None if raw.helper_text is None else _text(raw.helper_text, maximum=500, message="helper is invalid")
        out.append(QuestionIn(key, _text(raw.prompt, maximum=1000, message="prompt is invalid"),
                              kind, helper, tuple(choices), show_if))
    if for_publish and (out[-1].key != "story" or out[-1].kind != "story" or out[-1].show_if is not None):
        raise VisitPrepQuestionValidation("final story topic is required")
    return VersionIn(name=name, questions=tuple(out))

def creation_payload_sha256(definition: VersionIn) -> str:
    n = normalize_version(definition, for_publish=True)
    payload = {"name": n.name, "questions": [{
        "key": q.key, "prompt": q.prompt, "kind": q.kind, "helper_text": q.helper_text,
        "choices": [{"value": c.value, "label": c.label, "hint": c.hint} for c in q.choices],
        "show_if": None if q.show_if is None else {
            "question_key": q.show_if.question_key, "values": list(q.show_if.values)},
    } for q in n.questions]}
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()).hexdigest()

def _serialize_version(version: VisitPrepQuestionSetVersion) -> VersionOut:
    questions = sorted(
        VisitPrepQuestion.where(VisitPrepQuestion.question_set_version_id == version.id).all(),
        key=lambda q: q.position,
    )
    by_id = {q.id: q for q in questions}
    out: list[QuestionOut] = []
    for question in questions:
        show_if = None
        if question.show_if_question_id is not None:
            parent = by_id[question.show_if_question_id]
            show_if = ConditionIn(parent.question_key, tuple(question.show_if_values or ()))
        choices: list[ChoiceIn] = []
        for raw in question.choice_options:
            if isinstance(raw, dict):
                choices.append(ChoiceIn(raw["value"], raw["label"], raw.get("hint")))
            else:
                choices.append(ChoiceIn(raw.value, raw.label, raw.hint))
        out.append(QuestionOut(
            question.id, question.question_key, question.position, question.prompt_text,
            question.helper_text, cast(QuestionKind, question.kind), tuple(choices), show_if))
    return VersionOut(version.id, version.question_set_id, version.version_number, version.name,
                      version.published_at, version.is_default, tuple(out))

def _write_questions(session, *, version: VisitPrepQuestionSetVersion, definition: VersionIn) -> None:
    key_to_id: dict[str, TypeIDType] = {}; pending: list[tuple[VisitPrepQuestion, QuestionIn]] = []
    for index, question in enumerate(definition.questions, start=1):
        row = VisitPrepQuestion(
            question_set_version_id=version.id, question_key=question.key, position=index,
            prompt_text=question.prompt, helper_text=question.helper_text, kind=question.kind,
            choice_options=[VisitPrepQuestionChoice(value=c.value, label=c.label, hint=c.hint)
                            for c in question.choices])
        session.add(row); session.flush()
        key_to_id[question.key] = row.id; pending.append((row, question))
    for row, question in pending:
        if question.show_if is None:
            continue
        row.show_if_question_id = key_to_id[question.show_if.question_key]
        row.show_if_values = list(question.show_if.values)
        session.add(row)

def ensure_default_question_set(*, owner_practice_member_id: TypeIDType) -> VersionOut:
    """Idempotently seed and publish the founder five for one owner."""
    definition = normalize_version(CANONICAL_DEFAULT, for_publish=True)
    fingerprint = creation_payload_sha256(definition)
    with get_session() as session:
        existing = session.exec(select(VisitPrepQuestionSet).where(
            VisitPrepQuestionSet.owner_practice_member_id == owner_practice_member_id,
            VisitPrepQuestionSet.seed_key == DEFAULT_SEED)).one_or_none()
        if existing is not None:
            if existing.creation_payload_sha256 != fingerprint:
                raise VisitPrepQuestionConflict("default seed fingerprint mismatch")
            version = session.exec(select(VisitPrepQuestionSetVersion).where(
                VisitPrepQuestionSetVersion.question_set_id == existing.id,
                VisitPrepQuestionSetVersion.is_default.is_(True),
            ).order_by(VisitPrepQuestionSetVersion.version_number.desc())).first()
            if version is None:
                raise VisitPrepQuestionNotFound("default version missing")
            return _serialize_version(version)
        question_set = VisitPrepQuestionSet(
            owner_practice_member_id=owner_practice_member_id, seed_key=DEFAULT_SEED,
            creation_payload_sha256=fingerprint)
        session.add(question_set); session.flush()
        # Insert questions while unpublished (default check requires published_at).
        version = VisitPrepQuestionSetVersion(
            question_set_id=question_set.id, owner_practice_member_id=owner_practice_member_id,
            version_number=1, name=definition.name, published_at=None, is_default=False)
        session.add(version); session.flush()
        _write_questions(session, version=version, definition=definition)
        version.published_at = datetime.now(UTC)
        version.is_default = True
        session.add(version); session.flush()
        session.commit(); session.refresh(version)
        return _serialize_version(version)

def publish_question_set_version(
    *, owner_practice_member_id: TypeIDType, question_set_id: TypeIDType,
    definition: VersionIn, make_default: bool = True,
) -> VersionOut:
    """Publish a new version (edit/reorder/add/remove within five topics)."""
    normalized = normalize_version(definition, for_publish=True)
    with get_session() as session:
        question_set = session.exec(select(VisitPrepQuestionSet).where(
            VisitPrepQuestionSet.id == question_set_id,
            VisitPrepQuestionSet.owner_practice_member_id == owner_practice_member_id)).one_or_none()
        if question_set is None:
            raise VisitPrepQuestionNotFound("question set not found")
        latest = session.exec(select(VisitPrepQuestionSetVersion)
            .where(VisitPrepQuestionSetVersion.question_set_id == question_set.id)
            .order_by(VisitPrepQuestionSetVersion.version_number.desc())).first()
        next_number = 1 if latest is None else latest.version_number + 1
        if make_default:
            for row in session.exec(select(VisitPrepQuestionSetVersion).where(
                VisitPrepQuestionSetVersion.owner_practice_member_id == owner_practice_member_id,
                VisitPrepQuestionSetVersion.is_default.is_(True))):
                row.is_default = False; session.add(row)
        version = VisitPrepQuestionSetVersion(
            question_set_id=question_set.id, owner_practice_member_id=owner_practice_member_id,
            version_number=next_number, name=normalized.name,
            supersedes_version_id=None if latest is None else latest.id,
            published_at=None, is_default=False)
        session.add(version); session.flush()
        _write_questions(session, version=version, definition=normalized)
        version.published_at = datetime.now(UTC)
        version.is_default = make_default
        session.add(version); session.flush()
        session.commit(); session.refresh(version)
        return _serialize_version(version)

def list_question_sets(*, owner_practice_member_id: TypeIDType) -> list[VersionOut]:
    ensure_default_question_set(owner_practice_member_id=owner_practice_member_id)
    versions = VisitPrepQuestionSetVersion.where(
        VisitPrepQuestionSetVersion.owner_practice_member_id == owner_practice_member_id,
    ).all()
    published = [v for v in versions if v.published_at is not None]
    published.sort(key=lambda v: (not v.is_default, -v.version_number))
    return [_serialize_version(v) for v in published]

def get_published_version(*, owner_practice_member_id: TypeIDType, question_set_version_id: TypeIDType) -> VersionOut:
    version = VisitPrepQuestionSetVersion.get(
        id=question_set_version_id, owner_practice_member_id=owner_practice_member_id)
    if version is None or version.published_at is None:
        raise VisitPrepQuestionNotFound("question version not found")
    return _serialize_version(version)

def version_from_questions(questions: Sequence[QuestionOut]) -> VersionIn:
    return VersionIn(name=DEFAULT_NAME, questions=tuple(
        QuestionIn(q.key, q.prompt, q.kind, q.helper_text, q.choices, q.show_if) for q in questions))

def canonical_idempotency_key(value: object) -> str:
    if not isinstance(value, str):
        raise VisitPrepQuestionValidation("idempotency key is invalid")
    try:
        canonical = str(UUID(value))
    except (AttributeError, TypeError, ValueError) as exc:
        raise VisitPrepQuestionValidation("idempotency key is invalid") from exc
    if canonical != value:
        raise VisitPrepQuestionValidation("idempotency key is invalid")
    return canonical
