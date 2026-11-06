from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, Sequence, cast
from uuid import UUID

from activemodel.session_manager import get_session
from activemodel.types import TypeIDType
from app.models.visit_prep_question_set import (
    VisitPrepQuestion,
    VisitPrepQuestionChoice,
    VisitPrepQuestionSet,
    VisitPrepQuestionSetVersion,
)
from sqlmodel import Session, select

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


VE = VisitPrepQuestionValidation
DEFAULT_SEED = "default.v1"
DEFAULT_NAME = "Visit Prep default questions"


@dataclass(frozen=True)
class ChoiceIn:
    value: str
    label: str
    hint: str | None = None


C = ChoiceIn


@dataclass(frozen=True)
class ConditionIn:
    question_key: str
    values: tuple[str, ...]


@dataclass(frozen=True)
class QuestionIn:
    key: str
    prompt: str
    kind: QuestionKind
    helper_text: str | None = None
    choices: tuple[ChoiceIn, ...] = ()
    show_if: ConditionIn | None = None


@dataclass(frozen=True)
class VersionIn:
    name: str
    questions: tuple[QuestionIn, ...] = ()


@dataclass(frozen=True)
class QuestionOut:
    id: TypeIDType
    key: str
    position: int
    prompt: str
    helper_text: str | None
    kind: QuestionKind
    choices: tuple[ChoiceIn, ...]
    show_if: ConditionIn | None


@dataclass(frozen=True)
class VersionOut:
    id: TypeIDType
    question_set_id: TypeIDType
    version_number: int
    name: str
    published_at: datetime | None
    is_default: bool
    questions: tuple[QuestionOut, ...]


# fmt: off
CANONICAL_DEFAULT = VersionIn(DEFAULT_NAME, (QuestionIn("reason", "What is the main reason for this visit?", "story", "In your own words — what you most want understood."), QuestionIn("medications", "What medications are you prescribed right now?", "list", "Include dose if known. OTC and supplements count."), QuestionIn("adherence", "Are you taking them as prescribed?", "choice", "No wrong answer. Honesty saves the visit.", (C("yes", "Yes, as prescribed", "Most days, as written"), C("some", "Some of them", "A few get skipped"), C("no", "Not really", "Something got in the way"), C("none", "I don't take any"))), QuestionIn("adherence_barrier", "If not — what got in the way?", "story", "Cost, side effects, chaos, forgetting, unsure it helps.", (), ConditionIn("adherence", ("some", "no"))), QuestionIn("story", "Tell me your health story — whatever feels relevant.", "story", "What in your body and life belongs in this visit?")))

def _text(v: object, *, maximum: int, message: str) -> str:
    if not isinstance(v, str):
        raise VE(message)
    t = unicodedata.normalize("NFC", v.replace("\r\n", "\n")).strip()
    bad = any(unicodedata.category(c).startswith("C") and c not in "\n\t" for c in t)
    if not t or len(t) > maximum or bad:
        raise VE(message)
    return t

def _key(v: object) -> str:
    if not isinstance(v, str) or _SAFE_KEY.fullmatch(v) is None:
        raise VE("question key is invalid")
    return v

def normalize_version(d: VersionIn, *, for_publish: bool) -> VersionIn:
    if not isinstance(d, VersionIn):
        raise VE("question-set payload is invalid")
    nq = len(d.questions)
    if nq > 5 or (for_publish and nq != 5):
        raise VE("question count is invalid")
    name = _text(d.name, maximum=120, message="name is invalid")
    seen, cbk, out = set(), {}, []
    for raw in d.questions:
        key = _key(raw.key)
        if key in seen:
            raise VE("question keys must be unique")
        seen.add(key)
        if raw.kind not in {"story", "list", "choice"}:
            raise VE("question kind is invalid")
        kind = cast(QuestionKind, raw.kind)
        ch, vals = [], set()
        for c in raw.choices:
            cv = _key(c.value)
            if cv in vals:
                raise VE("question choice values must be unique")
            vals.add(cv)
            h = None if c.hint is None else _text(c.hint, maximum=500, message="choice hint is invalid")
            ch.append(C(cv, _text(c.label, maximum=120, message="choice label is invalid"), h))
        if kind == "choice" and len(ch) < 2:
            raise VE("choice questions need at least two choices")
        if kind != "choice" and ch:
            raise VE("non-choice questions cannot have choices")
        show_if = None
        if raw.show_if is not None:
            parent = _key(raw.show_if.question_key)
            if parent not in cbk:
                raise VE("condition parent is invalid")
            vals2 = tuple(_key(x) for x in raw.show_if.values)
            if not vals2:
                raise VE("condition values are invalid")
            for x in vals2:
                if x not in cbk[parent]:
                    raise VE("condition values are invalid")
            show_if = ConditionIn(parent, vals2)
        if kind == "choice":
            cbk[key] = frozenset(vals)
        helper = None if raw.helper_text is None else _text(raw.helper_text, maximum=500, message="helper is invalid")
        out.append(QuestionIn(key, _text(raw.prompt, maximum=1000, message="prompt is invalid"), kind, helper, tuple(ch), show_if))
    last = out[-1]
    if for_publish:
        if last.key != "story" or last.kind != "story" or last.show_if is not None:
            raise VE("final story topic is required")
    return VersionIn(name, tuple(out))

def creation_payload_sha256(definition: VersionIn) -> str:
    n = normalize_version(definition, for_publish=True)
    rows = [{"key": q.key, "prompt": q.prompt, "kind": q.kind, "helper_text": q.helper_text, "choices": [{"value": c.value, "label": c.label, "hint": c.hint} for c in q.choices], "show_if": None if q.show_if is None else {"question_key": q.show_if.question_key, "values": list(q.show_if.values)}} for q in n.questions]
    return hashlib.sha256(json.dumps({"name": n.name, "questions": rows}, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()).hexdigest()

def _craw(r: Any) -> ChoiceIn:
    if isinstance(r, dict):
        return C(r["value"], r["label"], r.get("hint"))
    return C(r.value, r.label, r.hint)

def _serialize_version(v: VisitPrepQuestionSetVersion) -> VersionOut:
    qs = sorted(cast(list[VisitPrepQuestion], VisitPrepQuestion.where(VisitPrepQuestion.question_set_version_id == v.id).all()), key=lambda q: q.position)
    by = {q.id: q for q in qs}
    o = []
    for q in qs:
        sf = None
        if q.show_if_question_id is not None:
            p = by[q.show_if_question_id]
            sf = ConditionIn(p.question_key, tuple(q.show_if_values or ()))
        ch = tuple(_craw(x) for x in q.choice_options)
        o.append(QuestionOut(q.id, q.question_key, q.position, q.prompt_text, q.helper_text, cast(QuestionKind, q.kind), ch, sf))
    return VersionOut(v.id, v.question_set_id, v.version_number, v.name, v.published_at, v.is_default, tuple(o))

def _write_questions(session: Session, *, version: VisitPrepQuestionSetVersion, definition: VersionIn) -> None:
    k2i, pend = {}, []
    for i, q in enumerate(definition.questions, start=1):
        r = VisitPrepQuestion(question_set_version_id=version.id, question_key=q.key, position=i, prompt_text=q.prompt, helper_text=q.helper_text, kind=q.kind, choice_options=[VisitPrepQuestionChoice(value=c.value, label=c.label, hint=c.hint) for c in q.choices])
        session.add(r)
        session.flush()
        k2i[q.key] = r.id
        pend.append((r, q))
    for r, q in pend:
        if q.show_if is None:
            continue
        r.show_if_question_id = k2i[q.show_if.question_key]
        r.show_if_values = list(q.show_if.values)
        session.add(r)

def _commit_published(session: Session, version: VisitPrepQuestionSetVersion, *, is_default: bool) -> VersionOut:
    version.published_at = datetime.now(UTC)
    version.is_default = is_default
    session.add(version)
    session.flush()
    session.commit()
    session.refresh(version)
    return _serialize_version(version)

def ensure_default_question_set(*, owner_practice_member_id: TypeIDType) -> VersionOut:
    definition = normalize_version(CANONICAL_DEFAULT, for_publish=True)
    fp = creation_payload_sha256(definition)
    with get_session() as session:
        ex = session.exec(select(VisitPrepQuestionSet).where(VisitPrepQuestionSet.owner_practice_member_id == owner_practice_member_id, VisitPrepQuestionSet.seed_key == DEFAULT_SEED)).one_or_none()
        if ex is not None:
            if ex.creation_payload_sha256 != fp:
                raise VisitPrepQuestionConflict("default seed fingerprint mismatch")
            ver = session.exec(select(VisitPrepQuestionSetVersion).where(VisitPrepQuestionSetVersion.question_set_id == ex.id, cast(Any, VisitPrepQuestionSetVersion.is_default).is_(True)).order_by(cast(Any, VisitPrepQuestionSetVersion.version_number).desc())).first()
            if ver is None:
                raise VisitPrepQuestionNotFound("default version missing")
            return _serialize_version(ver)
        qset = VisitPrepQuestionSet(owner_practice_member_id=owner_practice_member_id, seed_key=DEFAULT_SEED, creation_payload_sha256=fp)
        session.add(qset)
        session.flush()
        ver = VisitPrepQuestionSetVersion(question_set_id=qset.id, owner_practice_member_id=owner_practice_member_id, version_number=1, name=definition.name, published_at=None, is_default=False)
        session.add(ver)
        session.flush()
        _write_questions(session, version=ver, definition=definition)
        return _commit_published(session, ver, is_default=True)

def publish_question_set_version(*, owner_practice_member_id: TypeIDType, question_set_id: TypeIDType, definition: VersionIn, make_default: bool = True) -> VersionOut:
    norm = normalize_version(definition, for_publish=True)
    with get_session() as session:
        qset = session.exec(select(VisitPrepQuestionSet).where(VisitPrepQuestionSet.id == question_set_id, VisitPrepQuestionSet.owner_practice_member_id == owner_practice_member_id)).one_or_none()
        if qset is None:
            raise VisitPrepQuestionNotFound("question set not found")
        latest = session.exec(select(VisitPrepQuestionSetVersion).where(VisitPrepQuestionSetVersion.question_set_id == qset.id).order_by(cast(Any, VisitPrepQuestionSetVersion.version_number).desc())).first()
        nxt = 1 if latest is None else latest.version_number + 1
        if make_default:
            idef = cast(Any, VisitPrepQuestionSetVersion.is_default)
            for row in session.exec(select(VisitPrepQuestionSetVersion).where(VisitPrepQuestionSetVersion.owner_practice_member_id == owner_practice_member_id, idef.is_(True))):
                row.is_default = False
                session.add(row)
        ver = VisitPrepQuestionSetVersion(question_set_id=qset.id, owner_practice_member_id=owner_practice_member_id, version_number=nxt, name=norm.name, supersedes_version_id=None if latest is None else latest.id, published_at=None, is_default=False)
        session.add(ver)
        session.flush()
        _write_questions(session, version=ver, definition=norm)
        return _commit_published(session, ver, is_default=make_default)

def list_question_sets(*, owner_practice_member_id: TypeIDType) -> list[VersionOut]:
    ensure_default_question_set(owner_practice_member_id=owner_practice_member_id)
    vs = cast(list[VisitPrepQuestionSetVersion], VisitPrepQuestionSetVersion.where(VisitPrepQuestionSetVersion.owner_practice_member_id == owner_practice_member_id).all())
    pub = [x for x in vs if x.published_at is not None]
    pub.sort(key=lambda x: (not x.is_default, -x.version_number))
    return [_serialize_version(x) for x in pub]

def get_published_version(*, owner_practice_member_id: TypeIDType, question_set_version_id: TypeIDType) -> VersionOut:
    ver = VisitPrepQuestionSetVersion.get(id=question_set_version_id, owner_practice_member_id=owner_practice_member_id)
    if ver is None or ver.published_at is None:
        raise VisitPrepQuestionNotFound("question version not found")
    return _serialize_version(ver)

def version_from_questions(questions: Sequence[QuestionOut]) -> VersionIn:
    return VersionIn(DEFAULT_NAME, tuple(QuestionIn(q.key, q.prompt, q.kind, q.helper_text, q.choices, q.show_if) for q in questions))

def canonical_idempotency_key(value: object) -> str:
    if not isinstance(value, str):
        raise VE("idempotency key is invalid")
    try:
        c = str(UUID(value))
    except (AttributeError, TypeError, ValueError) as exc:
        raise VE("idempotency key is invalid") from exc
    if c != value:
        raise VE("idempotency key is invalid")
    return c
# fmt: on
