"""Physician-owned Visit Prep question-set lifecycle operations.

Routes enforce eligibility and view-as. This module owns tenant scoping,
idempotency, version lineage and database locking for question-set mutations.
Definitions remain re-exported here for the recovered callers.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Sequence, cast

from app.services.visit_prep_question_definitions import (
    _MAX_NAME_LENGTH,
    _canonical_client_seed_key,
    _creation_payload_sha256,
    _normalize_version_input,
    _normalized_text,
)
from app.services.visit_prep_question_definitions import (
    CANONICAL_DEFAULT_QUESTION_SET as CANONICAL_DEFAULT_QUESTION_SET,
)
from app.services.visit_prep_question_definitions import (
    DEFAULT_QUESTION_SET_NAME as DEFAULT_QUESTION_SET_NAME,
)
from app.services.visit_prep_question_definitions import (
    DEFAULT_QUESTION_SET_SEED_KEY as DEFAULT_QUESTION_SET_SEED_KEY,
)
from app.services.visit_prep_question_definitions import (
    MutationDisposition as MutationDisposition,
)
from app.services.visit_prep_question_definitions import (
    QuestionChoiceInput as QuestionChoiceInput,
)
from app.services.visit_prep_question_definitions import (
    QuestionChoiceResult as QuestionChoiceResult,
)
from app.services.visit_prep_question_definitions import (
    QuestionConditionInput as QuestionConditionInput,
)
from app.services.visit_prep_question_definitions import (
    QuestionConditionResult as QuestionConditionResult,
)
from app.services.visit_prep_question_definitions import (
    QuestionDraftInput as QuestionDraftInput,
)
from app.services.visit_prep_question_definitions import (
    QuestionKind as QuestionKind,
)
from app.services.visit_prep_question_definitions import (
    QuestionResult as QuestionResult,
)
from app.services.visit_prep_question_definitions import (
    QuestionSetMutationResult as QuestionSetMutationResult,
)
from app.services.visit_prep_question_definitions import (
    QuestionSetResult as QuestionSetResult,
)
from app.services.visit_prep_question_definitions import (
    QuestionSetVersionInput as QuestionSetVersionInput,
)
from app.services.visit_prep_question_definitions import (
    QuestionSetVersionResult as QuestionSetVersionResult,
)
from app.services.visit_prep_question_definitions import (
    VisitPrepQuestionSetConflict as VisitPrepQuestionSetConflict,
)
from app.services.visit_prep_question_definitions import (
    VisitPrepQuestionSetError as VisitPrepQuestionSetError,
)
from app.services.visit_prep_question_definitions import (
    VisitPrepQuestionSetNotFound as VisitPrepQuestionSetNotFound,
)
from app.services.visit_prep_question_definitions import (
    VisitPrepQuestionSetValidation as VisitPrepQuestionSetValidation,
)
from app.services.visit_prep_question_definitions import (
    validate_question_set_draft as validate_question_set_draft,
)
from app.services.visit_prep_question_definitions import (
    validate_question_set_for_publish as validate_question_set_for_publish,
)

from activemodel.session_manager import get_session
from activemodel.types import TypeIDType
from app.models.practice import (
    SEAT_HOLDING_ACCESS_STATES,
    PracticeMember,
    member_access_state,
)
from app.models.visit_prep_question_set import (
    VisitPrepQuestion,
    VisitPrepQuestionChoice,
    VisitPrepQuestionSet,
    VisitPrepQuestionSetVersion,
)
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select


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
        select(PracticeMember)
        .where(PracticeMember.id == owner_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()
    if (
        owner is None
        or member_access_state(owner, at=datetime.now(UTC))
        not in SEAT_HOLDING_ACCESS_STATES
    ):
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
        # Flush each later question before its earlier condition target.
        # The ORM otherwise batches deletes by primary key and can violate
        # the immediate self-referencing RESTRICT constraint.
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
        _definition_from_rows(
            version,
            _question_rows(session, version.id, lock=True),
            for_publish=True,
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
