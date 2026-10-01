"""Owner-authorized publication/read of immutable patient-report briefs.

The worker additionally proves its live lease and durable usage recording.
Patient sources and authority are reacquired in the same transaction as the
artifact and audit; caller-supplied sources are never publication authority.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, cast

from app.services.visit_prep_packages import (
    VisitPrepSourcePackage,
    _audit,
    _authorize,
    _database_now,
    _load_authorized_package,
    _LockedPackage,
)
from app.services.visit_prep_synthesis_contract import (
    GENERATOR_VERSION,
    PROMPT_VERSION,
    VisitPrepPreparedBrief,
    VisitPrepSynthesisInvalid,
    VisitPrepSynthesisSelection,
    select_patient_sources,
    validate_generated_brief,
    validate_selection,
)
from app.services.visit_prep_synthesis_generator import MODEL_PROFILE
from app.services.visit_prep_synthesis_payload import (
    decrypt_synthesis_payload,
    encrypt_synthesis_payload,
)
from app.services.visit_preps import VisitPrepConflict, _commit, _visit_prep_session

from activemodel.types import TypeIDType
from app.models.visit_prep_synthesis import VisitPrepSynthesisArtifact
from sqlmodel import Session, select


class VisitPrepSynthesisStale(VisitPrepConflict):
    def __init__(self) -> None:
        super().__init__("Visit Prep synthesis sources changed")


@dataclass(frozen=True)
class VisitPrepPublicationReceipt:
    artifact_id: TypeIDType
    version: int
    source_version: str
    disposition: Literal["published", "replayed"]


@dataclass(frozen=True)
class VisitPrepSynthesisView:
    package: VisitPrepSourcePackage = field(repr=False)
    status: Literal["missing", "stale", "ready"]
    artifact_id: TypeIDType | None = None
    version: int | None = None
    recorded_at: datetime | None = None
    prepared: VisitPrepPreparedBrief | None = field(default=None, repr=False)


def _current_artifact(
    session: Session, *, prep_id: TypeIDType, source_version: str
) -> VisitPrepSynthesisArtifact | None:
    return session.exec(
        select(VisitPrepSynthesisArtifact).where(
            VisitPrepSynthesisArtifact.visit_prep_id == prep_id,
            VisitPrepSynthesisArtifact.source_version == source_version,
            VisitPrepSynthesisArtifact.generator_version == GENERATOR_VERSION,
            VisitPrepSynthesisArtifact.prompt_version == PROMPT_VERSION,
            VisitPrepSynthesisArtifact.model_profile == MODEL_PROFILE,
        )
    ).one_or_none()


def _decoded_current(
    artifact: VisitPrepSynthesisArtifact,
    locked: _LockedPackage,
    selection: VisitPrepSynthesisSelection,
) -> VisitPrepPreparedBrief:
    if (
        artifact.visit_prep_id != locked.preparation.id
        or artifact.owner_practice_member_id != locked.owner.id
        or artifact.question_set_version_id
        != locked.preparation.question_set_version_id
        or artifact.schedule_generation_id != locked.schedule.id
        or artifact.credential_generation != locked.credential.generation
        or artifact.source_version != selection.source_version
        or artifact.basis_response_count != selection.basis.original_response_count
        or artifact.basis_event_sequence != selection.basis.event_sequence
        or artifact.actor_role != selection.actor_role
        or artifact.original_interview_completed
        != selection.original_interview_completed
        or artifact.current_answers_complete != selection.current_answers_complete
        or artifact.current_answers_reviewed != selection.current_answers_reviewed
        or artifact.generator_version != GENERATOR_VERSION
        or artifact.prompt_version != PROMPT_VERSION
        or artifact.model_profile != MODEL_PROFILE
        or artifact.payload_format_version != 1
        or artifact.recorded_at is None
        or artifact.recorded_at.tzinfo is None
        or artifact.recorded_at.utcoffset() is None
    ):
        raise VisitPrepSynthesisInvalid()
    return decrypt_synthesis_payload(artifact.payload_ciphertext, selection)


def _finish_audit(
    session: Session,
    locked: _LockedPackage,
    operation: Literal["synthesis_read", "synthesis_published"],
) -> None:
    now = _database_now(session)
    _authorize(session, owner=locked.owner, practice=locked.practice, at=now)
    _audit(
        session,
        owner=locked.owner,
        prep_id=locked.preparation.id,
        at=now,
        source_version=locked.package.source_version,
        operation=operation,
    )
    _commit(session)


def publish_visit_prep_synthesis(
    *,
    owner_practice_member_id: TypeIDType,
    owner_doctor_id: TypeIDType,
    practice_id: TypeIDType,
    expected_clerk_subject: str,
    visit_prep_id: TypeIDType,
    expected_selection: VisitPrepSynthesisSelection,
    prepared: VisitPrepPreparedBrief,
) -> VisitPrepPublicationReceipt:
    """Publish against exact current sources, or replay without overwriting.

    Superseded output is never rebased onto later patient wording. Model work
    happens before this transaction; this function never generates a response.
    """
    validate_selection(expected_selection)
    with _visit_prep_session() as session:
        locked = _load_authorized_package(
            session,
            owner_practice_member_id=owner_practice_member_id,
            owner_doctor_id=owner_doctor_id,
            practice_id=practice_id,
            expected_clerk_subject=expected_clerk_subject,
            visit_prep_id=visit_prep_id,
        )
        receipt = _publish_locked(session, locked, expected_selection, prepared)
        _finish_audit(session, locked, "synthesis_published")
        return receipt


def _publish_locked(
    session: Session,
    locked: _LockedPackage,
    expected_selection: VisitPrepSynthesisSelection,
    prepared: VisitPrepPreparedBrief,
) -> VisitPrepPublicationReceipt:
    """Stage an artifact without committing; the caller owns the final fence.

    Both owner publication and the worker use the same source comparison and
    first-valid-artifact rule. The worker commits this write, ready job state
    and audit together, not through a second publication transaction.
    """
    validate_selection(expected_selection)
    current = select_patient_sources(locked.package)
    if current != expected_selection:
        raise VisitPrepSynthesisStale()
    validate_generated_brief(prepared, current)
    prep_id = locked.preparation.id
    existing = _current_artifact(
        session, prep_id=prep_id, source_version=current.source_version
    )
    if existing is not None:
        _decoded_current(existing, locked, current)
        return VisitPrepPublicationReceipt(
            existing.id, existing.version, existing.source_version, "replayed"
        )
    predecessor = session.exec(
        select(VisitPrepSynthesisArtifact)
        .where(VisitPrepSynthesisArtifact.visit_prep_id == prep_id)
        .order_by(cast(Any, VisitPrepSynthesisArtifact.version).desc())
        .limit(1)
    ).first()
    artifact = VisitPrepSynthesisArtifact(
        visit_prep_id=prep_id,
        owner_practice_member_id=locked.owner.id,
        question_set_version_id=locked.preparation.question_set_version_id,
        schedule_generation_id=locked.schedule.id,
        credential_generation=locked.credential.generation,
        version=predecessor.version + 1 if predecessor else 1,
        source_version=current.source_version,
        basis_response_count=current.basis.original_response_count,
        basis_event_sequence=current.basis.event_sequence,
        actor_role=current.actor_role,
        original_interview_completed=current.original_interview_completed,
        current_answers_complete=current.current_answers_complete,
        current_answers_reviewed=current.current_answers_reviewed,
        generator_version=GENERATOR_VERSION,
        prompt_version=PROMPT_VERSION,
        model_profile=MODEL_PROFILE,
        payload_ciphertext=encrypt_synthesis_payload(prepared, current),
        payload_format_version=1,
    )
    session.add(artifact)
    session.flush()
    return VisitPrepPublicationReceipt(
        artifact.id, artifact.version, artifact.source_version, "published"
    )


def get_visit_prep_synthesis(
    *,
    owner_practice_member_id: TypeIDType,
    owner_doctor_id: TypeIDType,
    practice_id: TypeIDType,
    expected_clerk_subject: str,
    visit_prep_id: TypeIDType,
) -> VisitPrepSynthesisView:
    """Show only current profile/source output; stale payload is not decrypted."""
    with _visit_prep_session() as session:
        locked = _load_authorized_package(
            session,
            owner_practice_member_id=owner_practice_member_id,
            owner_doctor_id=owner_doctor_id,
            practice_id=practice_id,
            expected_clerk_subject=expected_clerk_subject,
            visit_prep_id=visit_prep_id,
        )
        current = select_patient_sources(locked.package)
        artifact = _current_artifact(
            session, prep_id=visit_prep_id, source_version=current.source_version
        )
        if artifact is not None:
            result = VisitPrepSynthesisView(
                package=locked.package,
                status="ready",
                artifact_id=artifact.id,
                version=artifact.version,
                recorded_at=artifact.recorded_at,
                prepared=_decoded_current(artifact, locked, current),
            )
        else:
            prior_id = session.exec(
                select(cast(Any, VisitPrepSynthesisArtifact.id))
                .where(VisitPrepSynthesisArtifact.visit_prep_id == visit_prep_id)
                .limit(1)
            ).first()
            result = VisitPrepSynthesisView(
                package=locked.package, status="stale" if prior_id else "missing"
            )
        _finish_audit(session, locked, "synthesis_read")
        return result
