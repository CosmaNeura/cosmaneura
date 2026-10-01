"""Authenticated owner reads of the current patient-source package.

Uses the same locked contribution projection as patient review. This is not a
model-generated brief or a clinical verification. Invitation expiry and the
public API gate do not govern an authenticated owner's retained record read.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, cast

from app.services import visit_prep_access
from app.services.organization_access import member_authorization_basis
from app.services.visit_prep_contribution_contract import PatientContribution
from app.services.visit_prep_contributions import _load_contribution
from app.services.visit_prep_interview_contract import VisitPrepInterviewUnavailable
from app.services.visit_preps import (
    VisitPrepConflict,
    VisitPrepNotFound,
    VisitPrepResult,
    VisitPrepValidation,
    _appointment_matches_schedule,
    _commit,
    _lock_current_lineage,
    _lock_owner_scope,
    _lock_visit_prep,
    _owned_appointment,
    _owned_patient,
    _snapshot,
    _visit_prep_session,
)

from activemodel.types import TypeIDType
from app.models.authority import AuthorityAuditEvent
from app.models.doctor import Doctor
from app.models.practice import (
    SEAT_HOLDING_ACCESS_STATES,
    Practice,
    PracticeMember,
    member_access_state,
)
from app.models.visit_prep import (
    VisitPrep,
    VisitPrepCredentialGeneration,
    VisitPrepScheduleGeneration,
)
from sqlalchemy import func, or_
from sqlmodel import Session, select


@dataclass(frozen=True)
class VisitPrepSourcePackage:
    preparation: VisitPrepResult
    source_version: str
    contribution: PatientContribution = field(repr=False)


@dataclass(frozen=True)
class VisitPrepList:
    preparations: tuple[VisitPrepResult, ...]
    next_before_id: TypeIDType | None


@dataclass(frozen=True)
class _LockedPackage:
    """Internal transaction-bound snapshot; never return the ORM authority rows."""

    package: VisitPrepSourcePackage = field(repr=False)
    preparation: VisitPrep = field(repr=False)
    owner: PracticeMember = field(repr=False)
    practice: Practice = field(repr=False)
    schedule: VisitPrepScheduleGeneration = field(repr=False)
    credential: VisitPrepCredentialGeneration = field(repr=False)


def _database_now(session: Session) -> datetime:
    now = cast(datetime, session.exec(select(func.clock_timestamp())).one())
    if now.tzinfo is None or now.utcoffset() is None:
        raise VisitPrepNotFound("Visit Prep is unavailable")
    return now.astimezone(UTC)


def _authorize(
    session: Session, *, owner: PracticeMember, practice: Practice, at: datetime
) -> None:
    doctor = session.exec(select(Doctor).where(Doctor.id == owner.doctor_id)).one()
    decision = visit_prep_access.resolve_visit_prep_access(
        doctor, settings=visit_prep_access.VISIT_PREP_SETTINGS, at=at
    )
    if (
        not decision.eligible
        or decision.view_as_active
        or decision.practice_member_id != owner.id
        or decision.practice_id != practice.id
        or decision.doctor_id != owner.doctor_id
        or owner.access_revoked_at is not None
        or member_access_state(owner, at=at) not in SEAT_HOLDING_ACCESS_STATES
        or member_authorization_basis(owner, at=at, practice=practice)
        not in {"organization_roster", "workforce_attestation"}
        or not practice.baa_in_force(at=at)
        or not practice.entitled(at=at)
    ):
        raise VisitPrepNotFound("Visit Prep is unavailable")


def _owner(
    session: Session,
    *,
    owner_practice_member_id: TypeIDType,
    owner_doctor_id: TypeIDType,
    practice_id: TypeIDType,
    expected_clerk_subject: str,
) -> tuple[PracticeMember, Practice]:
    owner, practice = _lock_owner_scope(
        session,
        owner_practice_member_id=owner_practice_member_id,
        owner_doctor_id=owner_doctor_id,
        practice_id=practice_id,
        now=_database_now(session),
    )
    doctor = session.exec(select(Doctor).where(Doctor.id == owner_doctor_id)).one()
    if not expected_clerk_subject or doctor.clerk_id != expected_clerk_subject:
        raise VisitPrepNotFound("Visit Prep is unavailable")
    _authorize(session, owner=owner, practice=practice, at=_database_now(session))
    return owner, practice


def _audit(
    session: Session,
    *,
    owner: PracticeMember,
    prep_id: TypeIDType | None,
    at: datetime,
    source_version: str | None = None,
    operation: Literal["read", "synthesis_read", "synthesis_published"] = "read",
) -> None:
    # Existing Core audit store; no new identity binding is fabricated. Locked
    # canonical owner references attribute the provider-authorized operation.
    session.add(
        AuthorityAuditEvent(
            event_type=(
                f"visit_prep.{operation}"
                if operation != "read"
                else "visit_prep.package_read"
                if prep_id
                else "visit_prep.list_read"
            ),
            outcome="allowed",
            occurred_at=at,
            resource_type="visit_prep" if prep_id else "practice_member",
            resource_ref=str(prep_id or owner.id),
            route=(
                "visit_prep_synthesis"
                if operation != "read"
                else "visit_prep_package"
                if prep_id
                else "visit_prep_list"
            ),
            intent=(
                "provider_previsit_synthesis"
                if operation == "synthesis_published"
                else "provider_previsit_review"
            ),
            request_digest=source_version,
            platform_refs=[
                {"kind": "doctor", "ref": str(owner.doctor_id)},
                {"kind": "practice", "ref": str(owner.practice_id)},
                {"kind": "practice_member", "ref": str(owner.id)},
            ],
        )
    )


def list_visit_preps(
    *,
    owner_practice_member_id: TypeIDType,
    owner_doctor_id: TypeIDType,
    practice_id: TypeIDType,
    expected_clerk_subject: str,
    limit: int = 20,
    before_id: TypeIDType | None = None,
) -> VisitPrepList:
    if type(limit) is not int or not 1 <= limit <= 50:
        raise VisitPrepValidation("Visit Prep list limit is invalid")
    with _visit_prep_session() as session:
        owner, practice = _owner(
            session,
            owner_practice_member_id=owner_practice_member_id,
            owner_doctor_id=owner_doctor_id,
            practice_id=practice_id,
            expected_clerk_subject=expected_clerk_subject,
        )
        query = select(VisitPrep).where(
            VisitPrep.owner_practice_member_id == owner.id,
            VisitPrep.owner_doctor_id == owner.doctor_id,
            VisitPrep.practice_id == practice.id,
        )
        if before_id is not None:
            cursor = _lock_visit_prep(
                session, owner_practice_member_id=owner.id, visit_prep_id=before_id
            )
            if cursor.created_at is None:
                raise VisitPrepNotFound("Visit Prep is unavailable")
            query = query.where(
                or_(
                    cast(Any, VisitPrep.created_at) < cursor.created_at,
                    (cast(Any, VisitPrep.created_at) == cursor.created_at)
                    & (cast(Any, VisitPrep.id) < cursor.id),
                )
            )
        rows = session.exec(
            query.order_by(
                cast(Any, VisitPrep.created_at).desc(), cast(Any, VisitPrep.id).desc()
            )
            .limit(limit + 1)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()
        lineages = [
            (row, *_lock_current_lineage(session, visit_prep=row))
            for row in rows[:limit]
        ]
        now = _database_now(session)
        _authorize(session, owner=owner, practice=practice, at=now)
        result = VisitPrepList(
            preparations=tuple(
                _snapshot(row, schedule, credential, at=now)
                for row, schedule, credential in lineages
            ),
            next_before_id=rows[limit - 1].id if len(rows) > limit else None,
        )
        _audit(session, owner=owner, prep_id=None, at=now)
        _commit(session)
        return result


def get_visit_prep_package(
    *,
    owner_practice_member_id: TypeIDType,
    owner_doctor_id: TypeIDType,
    practice_id: TypeIDType,
    expected_clerk_subject: str,
    visit_prep_id: TypeIDType,
) -> VisitPrepSourcePackage:
    with _visit_prep_session() as session:
        locked = _load_authorized_package(
            session,
            owner_practice_member_id=owner_practice_member_id,
            owner_doctor_id=owner_doctor_id,
            practice_id=practice_id,
            expected_clerk_subject=expected_clerk_subject,
            visit_prep_id=visit_prep_id,
        )
        now = _database_now(session)
        _authorize(session, owner=locked.owner, practice=locked.practice, at=now)
        _audit(
            session,
            owner=locked.owner,
            prep_id=locked.preparation.id,
            at=now,
            source_version=locked.package.source_version,
        )
        _commit(session)
        return locked.package


def _load_authorized_package(
    session: Session,
    *,
    owner_practice_member_id: TypeIDType,
    owner_doctor_id: TypeIDType,
    practice_id: TypeIDType,
    expected_clerk_subject: str,
    visit_prep_id: TypeIDType,
) -> _LockedPackage:
    """Share the exact owner/source boundary without committing between steps.

    Callers hold this transaction through publication/disclosure and audit. No
    model request belongs inside these locks. This function grants no bot or
    patient authority and writes no audit or artifact by itself.
    """
    owner, practice = _owner(
        session,
        owner_practice_member_id=owner_practice_member_id,
        owner_doctor_id=owner_doctor_id,
        practice_id=practice_id,
        expected_clerk_subject=expected_clerk_subject,
    )
    prep = _lock_visit_prep(
        session, owner_practice_member_id=owner.id, visit_prep_id=visit_prep_id
    )
    if (
        prep.practice_id != practice.id
        or prep.owner_doctor_id != owner.doctor_id
        or prep.revoked_at is not None
        or prep.misdirected_at is not None
        or prep.lifecycle_state not in {"confirmed", "in_progress", "completed"}
    ):
        raise VisitPrepNotFound("Visit Prep is unavailable")
    schedule, credential = _lock_current_lineage(session, visit_prep=prep)
    if credential.revoked_at is not None:
        raise VisitPrepNotFound("Visit Prep is unavailable")
    if prep.appointment_id is not None:
        appointment = _owned_appointment(
            session,
            owner_doctor_id=owner.doctor_id,
            appointment_id=prep.appointment_id,
        )
        if (
            appointment.patient_id != prep.patient_id
            or not _appointment_matches_schedule(
                session,
                appointment_id=appointment.id,
                scheduled_at=schedule.scheduled_at,
            )
        ):
            raise VisitPrepConflict("Visit Prep linked context changed")
    if prep.patient_id is not None:
        _owned_patient(
            session, owner_doctor_id=owner.doctor_id, patient_id=prep.patient_id
        )
    _authorize(session, owner=owner, practice=practice, at=_database_now(session))
    try:
        contribution = _load_contribution(session, visit_prep=prep)
    except VisitPrepInterviewUnavailable:
        raise VisitPrepNotFound("Visit Prep is unavailable") from None
    now = _database_now(session)
    _authorize(session, owner=owner, practice=practice, at=now)
    # Preserve the original source-version recipe: immutable source identities
    # and lifecycle metadata only, never a hash of patient wording.
    version = hashlib.sha256(
        json.dumps(
            {
                "contract": "visit_prep.patient_sources.v1",
                "prep": str(prep.id),
                "questions": str(prep.question_set_version_id),
                "schedule": str(schedule.id),
                "credential_generation": credential.generation,
                "patient": str(prep.patient_id) if prep.patient_id else None,
                "appointment": str(prep.appointment_id)
                if prep.appointment_id
                else None,
                "completed": contribution.original_interview_completed,
                "responses": contribution.basis.original_response_count,
                "events": contribution.basis.event_sequence,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return _LockedPackage(
        package=VisitPrepSourcePackage(
            preparation=_snapshot(prep, schedule, credential, at=now),
            source_version=version,
            contribution=contribution,
        ),
        preparation=prep,
        owner=owner,
        practice=practice,
        schedule=schedule,
        credential=credential,
    )
