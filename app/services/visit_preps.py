"""Owner-scoped Visit Prep preparation commands; no delivery or public API.

Routes enforce the release cohort and view-as. These transactions recheck the
current Core identity, practice, seat, BAA and authorization basis under locks.
Inputs and cryptographic formatting live in visit_prep_command_contract.
"""

from __future__ import annotations

import hmac
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any, cast

from app.services.doctor_access import doctor_tombstone
from app.services.organization_access import member_authorization_basis
from app.services.visit_prep_command_contract import (
    _CREATION_SOURCE,
    _actor_ref,
    _aware_utc,
    _canonical_uuid,
    _creation_payload_hmac,
    _new_access_token,
    _normalize_recipient,
    _normalized_schedule,
    _normalized_text,
    _recipient_plaintext,
    _schedule_deadlines,
)
from app.services.visit_prep_command_contract import (
    MutationDisposition as MutationDisposition,
)
from app.services.visit_prep_command_contract import RecipientInput as RecipientInput
from app.services.visit_prep_command_contract import RecipientRole as RecipientRole
from app.services.visit_prep_command_contract import (
    RevocationReason as RevocationReason,
)
from app.services.visit_prep_command_contract import (
    VisitPrepConflict as VisitPrepConflict,
)
from app.services.visit_prep_command_contract import VisitPrepError as VisitPrepError
from app.services.visit_prep_command_contract import (
    VisitPrepMutationResult as VisitPrepMutationResult,
)
from app.services.visit_prep_command_contract import (
    VisitPrepNotFound as VisitPrepNotFound,
)
from app.services.visit_prep_command_contract import VisitPrepResult as VisitPrepResult
from app.services.visit_prep_command_contract import (
    VisitPrepUnavailable as VisitPrepUnavailable,
)
from app.services.visit_prep_command_contract import (
    VisitPrepValidation as VisitPrepValidation,
)
from app.services.visit_prep_command_contract import (
    access_token_hash as access_token_hash,
)
from app.services.visit_prep_open_evidence import (
    InvitationOpenEvidenceInvalid,
    stage_creation_coverage,
)
from app.services.visit_prep_question_definitions import VisitPrepQuestionSetValidation
from app.services.visit_prep_question_sets import _definition_from_rows
from app.utils.encryption import EncryptionError, encrypt_value

from activemodel.session_manager import get_session
from activemodel.types import TypeIDType
from app.models.appointment import Appointment
from app.models.doctor import Doctor
from app.models.organization_access import (
    OrganizationRosterApproval,
    ProviderOrganization,
)
from app.models.patient import Patient
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
from app.models.visit_prep_question_set import (
    VisitPrepQuestion,
    VisitPrepQuestionSetVersion,
)
from sqlalchemy import DateTime, func, literal
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlmodel import Session, select

_LOCK_NOT_AVAILABLE_SQLSTATE = "55P03"


@contextmanager
def _visit_prep_session() -> Iterator[Session]:
    """Roll back fail-fast identity-lock contention before surfacing a 503."""

    with get_session() as session:
        try:
            yield session
        except OperationalError as exc:
            if getattr(exc.orig, "sqlstate", None) == _LOCK_NOT_AVAILABLE_SQLSTATE:
                session.rollback()
            raise


def _lock_owner_scope(
    session: Session,
    *,
    owner_practice_member_id: TypeIDType,
    owner_doctor_id: TypeIDType,
    practice_id: TypeIDType,
    now: datetime,
) -> tuple[PracticeMember, Practice]:
    # Match organization access and public interview transactions so an
    # offboarding operation cannot form a PracticeMember -> Practice cycle.
    practice = session.exec(
        select(Practice)
        .where(Practice.id == practice_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()
    if practice is None:
        raise VisitPrepNotFound("Visit Prep owner is unavailable")

    if practice.organization_id is not None:
        organization = session.exec(
            select(ProviderOrganization)
            .where(ProviderOrganization.id == practice.organization_id)
            .with_for_update(nowait=True)
            .execution_options(populate_existing=True)
        ).one_or_none()
        if organization is None or organization.status != "active":
            raise VisitPrepNotFound("Visit Prep owner is unavailable")

    doctor = session.exec(
        select(Doctor)
        .where(Doctor.id == owner_doctor_id)
        .with_for_update(nowait=True)
        .execution_options(populate_existing=True)
    ).one_or_none()
    if doctor is None or doctor_tombstone(doctor) is not None:
        raise VisitPrepNotFound("Visit Prep owner is unavailable")

    owner = session.exec(
        select(PracticeMember)
        .where(
            PracticeMember.id == owner_practice_member_id,
            PracticeMember.doctor_id == doctor.id,
            PracticeMember.practice_id == practice.id,
        )
        .with_for_update(nowait=True)
        .execution_options(populate_existing=True)
    ).one_or_none()
    if owner is None:
        raise VisitPrepNotFound("Visit Prep owner is unavailable")
    if owner.organization_roster_approval_id is not None:
        session.exec(
            select(OrganizationRosterApproval)
            .where(
                OrganizationRosterApproval.id == owner.organization_roster_approval_id
            )
            .with_for_update(nowait=True)
            .execution_options(populate_existing=True)
        ).one_or_none()
    if (
        member_access_state(owner, at=now) not in SEAT_HOLDING_ACCESS_STATES
        or not practice.baa_in_force(at=now)
        or not practice.entitled(at=now)
        or member_authorization_basis(owner, at=now, practice=practice)
        not in {"organization_roster", "workforce_attestation"}
    ):
        raise VisitPrepNotFound("Visit Prep owner is unavailable")
    return owner, practice


def _published_question_version(
    session: Session,
    *,
    owner_practice_member_id: TypeIDType,
    question_set_version_id: TypeIDType,
) -> VisitPrepQuestionSetVersion:
    version = session.exec(
        select(VisitPrepQuestionSetVersion)
        .where(
            VisitPrepQuestionSetVersion.id == question_set_version_id,
            VisitPrepQuestionSetVersion.owner_practice_member_id
            == owner_practice_member_id,
            cast(Any, VisitPrepQuestionSetVersion.published_at).is_not(None),
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()
    if version is None:
        raise VisitPrepNotFound("question-set version was not found")
    questions = session.exec(
        select(VisitPrepQuestion)
        .where(VisitPrepQuestion.question_set_version_id == version.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).all()
    try:
        _definition_from_rows(version, questions, for_publish=True)
    except VisitPrepQuestionSetValidation:
        raise VisitPrepValidation("published question-set version is invalid") from None
    return version


def _owned_patient(
    session: Session,
    *,
    owner_doctor_id: TypeIDType,
    patient_id: TypeIDType,
) -> Patient:
    patient = session.exec(
        select(Patient)
        .where(Patient.id == patient_id, Patient.doctor_id == owner_doctor_id)
        .with_for_update()
    ).one_or_none()
    if patient is None:
        raise VisitPrepNotFound("patient was not found")
    return patient


def _owned_appointment(
    session: Session,
    *,
    owner_doctor_id: TypeIDType,
    appointment_id: TypeIDType,
) -> Appointment:
    appointment = session.exec(
        select(Appointment)
        .where(
            Appointment.id == appointment_id,
            Appointment.doctor_id == owner_doctor_id,
        )
        .with_for_update()
    ).one_or_none()
    if appointment is None:
        raise VisitPrepNotFound("appointment was not found")
    return appointment


def _appointment_matches_schedule(
    session: Session,
    *,
    appointment_id: TypeIDType,
    scheduled_at: datetime,
) -> bool:
    """Compare an instant with the legacy timezone-less appointment column.

    PostgreSQL converts aware appointment inputs into the connection timezone
    before storing them in this legacy column.  Perform the inverse-compatible
    comparison in PostgreSQL so behavior does not depend on the API host's
    timezone and remains correct if a deployment uses a non-UTC DB timezone.
    """

    legacy_wall_clock = func.timezone(
        func.current_setting("TimeZone"),
        literal(scheduled_at, type_=DateTime(timezone=True)),
    )
    return (
        session.exec(
            select(func.count())
            .select_from(Appointment)
            .where(
                Appointment.id == appointment_id,
                Appointment.appointment_date == legacy_wall_clock,
            )
        ).one()
        == 1
    )


def _creation_links(
    session: Session,
    *,
    owner_doctor_id: TypeIDType,
    patient_id: TypeIDType | None,
    appointment_id: TypeIDType | None,
    scheduled_at: datetime,
) -> tuple[TypeIDType | None, TypeIDType | None]:
    requested_patient_id = patient_id
    if appointment_id is not None:
        appointment = _owned_appointment(
            session,
            owner_doctor_id=owner_doctor_id,
            appointment_id=appointment_id,
        )
        if not _appointment_matches_schedule(
            session,
            appointment_id=appointment.id,
            scheduled_at=scheduled_at,
        ):
            raise VisitPrepConflict("appointment schedule does not match")
        if (
            requested_patient_id is not None
            and requested_patient_id != appointment.patient_id
        ):
            raise VisitPrepConflict("appointment patient does not match")
        requested_patient_id = appointment.patient_id
    if requested_patient_id is not None:
        _owned_patient(
            session,
            owner_doctor_id=owner_doctor_id,
            patient_id=requested_patient_id,
        )
    return requested_patient_id, appointment_id


def _lock_visit_prep(
    session: Session,
    *,
    owner_practice_member_id: TypeIDType,
    visit_prep_id: TypeIDType,
) -> VisitPrep:
    visit_prep = session.exec(
        select(VisitPrep)
        .where(
            VisitPrep.id == visit_prep_id,
            VisitPrep.owner_practice_member_id == owner_practice_member_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()
    if visit_prep is None:
        raise VisitPrepNotFound("Visit Prep was not found")
    return visit_prep


def _lock_current_lineage(
    session: Session,
    *,
    visit_prep: VisitPrep,
) -> tuple[VisitPrepScheduleGeneration, VisitPrepCredentialGeneration]:
    schedule = session.exec(
        select(VisitPrepScheduleGeneration)
        .where(
            VisitPrepScheduleGeneration.visit_prep_id == visit_prep.id,
            VisitPrepScheduleGeneration.owner_practice_member_id
            == visit_prep.owner_practice_member_id,
        )
        .order_by(cast(Any, VisitPrepScheduleGeneration.generation).desc())
        .limit(1)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()
    credential = session.exec(
        select(VisitPrepCredentialGeneration)
        .where(
            VisitPrepCredentialGeneration.visit_prep_id == visit_prep.id,
            VisitPrepCredentialGeneration.owner_practice_member_id
            == visit_prep.owner_practice_member_id,
        )
        .order_by(cast(Any, VisitPrepCredentialGeneration.generation).desc())
        .limit(1)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()
    if schedule is None or credential is None:
        raise VisitPrepConflict("Visit Prep lineage is incomplete")
    if credential.schedule_generation_id != schedule.id:
        raise VisitPrepConflict("Visit Prep lineage is inconsistent")
    return schedule, credential


def _expected_credential(
    credential: VisitPrepCredentialGeneration,
    *,
    expected_generation: int,
) -> None:
    if not isinstance(expected_generation, int) or isinstance(
        expected_generation, bool
    ):
        raise VisitPrepValidation("credential generation is invalid")
    if expected_generation <= 0:
        raise VisitPrepValidation("credential generation is invalid")
    if credential.generation != expected_generation:
        raise VisitPrepConflict("credential generation is stale")


def _active_credential(
    credential: VisitPrepCredentialGeneration,
    *,
    expected_generation: int,
) -> None:
    _expected_credential(
        credential,
        expected_generation=expected_generation,
    )
    if credential.revoked_at is not None:
        raise VisitPrepConflict("Visit Prep has no active credential")


def _expected_schedule(
    schedule: VisitPrepScheduleGeneration,
    *,
    expected_generation: int,
) -> None:
    if not isinstance(expected_generation, int) or isinstance(
        expected_generation, bool
    ):
        raise VisitPrepValidation("schedule generation is invalid")
    if expected_generation <= 0:
        raise VisitPrepValidation("schedule generation is invalid")
    if schedule.generation != expected_generation:
        raise VisitPrepConflict("schedule generation is stale")


def _snapshot(
    visit_prep: VisitPrep,
    schedule: VisitPrepScheduleGeneration,
    credential: VisitPrepCredentialGeneration,
    *,
    at: datetime,
) -> VisitPrepResult:
    return VisitPrepResult(
        id=visit_prep.id,
        question_set_version_id=visit_prep.question_set_version_id,
        patient_id=visit_prep.patient_id,
        appointment_id=visit_prep.appointment_id,
        lifecycle_state=visit_prep.lifecycle_state,
        has_email_contact=visit_prep.has_email_contact,
        has_sms_contact=visit_prep.has_sms_contact,
        physician_display_name=visit_prep.physician_display_name,
        practice_display_name=visit_prep.practice_display_name,
        scheduled_at=schedule.scheduled_at,
        timezone_name=schedule.timezone_name,
        schedule_generation=schedule.generation,
        credential_generation=credential.generation,
        credential_expires_at=credential.expires_at,
        credential_active=(
            visit_prep.revoked_at is None
            and visit_prep.lifecycle_state not in {"completed", "misdirected"}
            and credential.revoked_at is None
            and credential.expires_at > at
        ),
        revoked_at=visit_prep.revoked_at,
        created_at=visit_prep.created_at,
        updated_at=visit_prep.updated_at,
    )


def _commit(session: Session) -> None:
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise VisitPrepConflict("Visit Prep state changed concurrently") from exc


def _refresh_lineage(
    session: Session,
    *,
    visit_prep: VisitPrep,
    schedule: VisitPrepScheduleGeneration,
    credential: VisitPrepCredentialGeneration,
    at: datetime,
) -> VisitPrepResult:
    session.flush()
    session.refresh(visit_prep)
    session.refresh(schedule)
    session.refresh(credential)
    return _snapshot(visit_prep, schedule, credential, at=at)


def _issue_credential(
    session: Session,
    *,
    visit_prep: VisitPrep,
    schedule: VisitPrepScheduleGeneration,
    generation: int,
    issued_at: datetime,
) -> tuple[VisitPrepCredentialGeneration, str]:
    _unused_purge_at, _unused_hard_purge_at, expires_at = _schedule_deadlines(
        schedule.scheduled_at
    )
    if expires_at <= issued_at:
        raise VisitPrepConflict("Visit Prep schedule has already expired")
    token = _new_access_token()
    credential = VisitPrepCredentialGeneration(
        visit_prep_id=visit_prep.id,
        owner_practice_member_id=visit_prep.owner_practice_member_id,
        schedule_generation_id=schedule.id,
        generation=generation,
        token_format_version=1,
        token_hash=access_token_hash(token),
        issued_at=issued_at,
        expires_at=expires_at,
    )
    credential.created_at = issued_at
    credential.updated_at = issued_at
    session.add(credential)
    return credential, token


def create_visit_prep(
    *,
    owner_practice_member_id: TypeIDType,
    owner_doctor_id: TypeIDType,
    practice_id: TypeIDType,
    idempotency_key: str,
    recipient: RecipientInput,
    physician_display_name: str,
    scheduled_at: datetime,
    timezone_name: str,
    question_set_version_id: TypeIDType,
    patient_id: TypeIDType | None = None,
    appointment_id: TypeIDType | None = None,
    now: datetime | None = None,
) -> VisitPrepMutationResult:
    """Create one aggregate atomically, returning its bearer exactly once."""

    with _visit_prep_session() as session:
        result = create_locked_visit_prep(
            session,
            owner_practice_member_id=owner_practice_member_id,
            owner_doctor_id=owner_doctor_id,
            practice_id=practice_id,
            idempotency_key=idempotency_key,
            recipient=recipient,
            physician_display_name=physician_display_name,
            scheduled_at=scheduled_at,
            timezone_name=timezone_name,
            question_set_version_id=question_set_version_id,
            patient_id=patient_id,
            appointment_id=appointment_id,
            now=now,
        )
        _commit(session)
        return result


def create_locked_visit_prep(
    session: Session,
    *,
    owner_practice_member_id: TypeIDType,
    owner_doctor_id: TypeIDType,
    practice_id: TypeIDType,
    idempotency_key: str,
    recipient: RecipientInput,
    physician_display_name: str,
    scheduled_at: datetime,
    timezone_name: str,
    question_set_version_id: TypeIDType,
    patient_id: TypeIDType | None = None,
    appointment_id: TypeIDType | None = None,
    now: datetime | None = None,
    creation_source: str = _CREATION_SOURCE,
) -> VisitPrepMutationResult:
    """Stage creation in the caller's transaction, with all existing fences.

    Booking adapters must commit their source receipt in the SAME transaction.
    This internal helper takes no external action and never commits or rolls
    back the caller. Routes must not expose its creation_source parameter.
    """
    if creation_source not in {_CREATION_SOURCE, "appointment_booking"}:
        raise VisitPrepValidation("creation source is invalid")
    effective_at = _aware_utc(now, message="creation time is invalid")
    canonical_key = _canonical_uuid(idempotency_key)
    normalized_recipient = _normalize_recipient(recipient)
    normalized_physician = _normalized_text(
        physician_display_name,
        maximum=160,
        message="physician display name is invalid",
    )
    normalized_scheduled_at, normalized_timezone = _normalized_schedule(
        scheduled_at,
        timezone_name,
        now=effective_at,
        require_future=False,
    )

    _owner, practice = _lock_owner_scope(
        session,
        owner_practice_member_id=owner_practice_member_id,
        owner_doctor_id=owner_doctor_id,
        practice_id=practice_id,
        now=effective_at,
    )
    version = _published_question_version(
        session,
        owner_practice_member_id=owner_practice_member_id,
        question_set_version_id=question_set_version_id,
    )
    existing = session.exec(
        select(VisitPrep)
        .where(
            VisitPrep.owner_practice_member_id == owner_practice_member_id,
            VisitPrep.creation_idempotency_key == canonical_key,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()
    if existing is not None:
        # Canonical records and the practice name can change after the
        # original command.  A retry compares the request's semantics to
        # the immutable snapshots instead of re-deriving mutable values.
        linked_patient_id = patient_id
        if (
            appointment_id is not None
            and patient_id is None
            and existing.appointment_id == appointment_id
        ):
            linked_patient_id = existing.patient_id
        payload_hmac = _creation_payload_hmac(
            owner_practice_member_id=owner_practice_member_id,
            question_set_version_id=version.id,
            recipient=normalized_recipient,
            physician_display_name=normalized_physician,
            practice_display_name=existing.practice_display_name,
            scheduled_at=normalized_scheduled_at,
            timezone_name=normalized_timezone,
            patient_id=linked_patient_id,
            appointment_id=appointment_id,
            creation_source=creation_source,
        )
        if not hmac.compare_digest(
            existing.creation_payload_hmac_sha256,
            payload_hmac,
        ):
            raise VisitPrepConflict("idempotency key belongs to a different payload")
        current_schedule, current_credential = _lock_current_lineage(
            session,
            visit_prep=existing,
        )
        snapshot = _snapshot(
            existing,
            current_schedule,
            current_credential,
            at=effective_at,
        )
        return VisitPrepMutationResult(
            visit_prep=snapshot,
            disposition="replayed",
            access_token=None,
        )

    if normalized_scheduled_at <= effective_at:
        raise VisitPrepValidation("scheduled time must be in the future")

    normalized_practice = _normalized_text(
        practice.legal_name,
        maximum=200,
        message="practice display name is invalid",
    )
    linked_patient_id, linked_appointment_id = _creation_links(
        session,
        owner_doctor_id=owner_doctor_id,
        patient_id=patient_id,
        appointment_id=appointment_id,
        scheduled_at=normalized_scheduled_at,
    )
    payload_hmac = _creation_payload_hmac(
        owner_practice_member_id=owner_practice_member_id,
        question_set_version_id=version.id,
        recipient=normalized_recipient,
        physician_display_name=normalized_physician,
        practice_display_name=normalized_practice,
        scheduled_at=normalized_scheduled_at,
        timezone_name=normalized_timezone,
        patient_id=linked_patient_id,
        appointment_id=linked_appointment_id,
        creation_source=creation_source,
    )
    actor_ref = _actor_ref(owner_doctor_id)
    try:
        recipient_ciphertext = encrypt_value(_recipient_plaintext(normalized_recipient))
    except EncryptionError as exc:
        raise VisitPrepUnavailable("recipient encryption unavailable") from exc

    visit_prep = VisitPrep(
        practice_id=practice_id,
        owner_practice_member_id=owner_practice_member_id,
        owner_doctor_id=owner_doctor_id,
        question_set_version_id=version.id,
        patient_id=linked_patient_id,
        appointment_id=linked_appointment_id,
        creation_source=creation_source,
        creation_idempotency_key=canonical_key,
        creation_payload_hmac_sha256=payload_hmac,
        recipient_snapshot_ciphertext=recipient_ciphertext,
        recipient_snapshot_format_version=1,
        has_email_contact=normalized_recipient.email is not None,
        has_sms_contact=normalized_recipient.phone is not None,
        physician_display_name=normalized_physician,
        practice_display_name=normalized_practice,
        patient_linked_at=effective_at if linked_patient_id is not None else None,
        patient_linked_by_ref=(actor_ref if linked_patient_id is not None else None),
        appointment_linked_at=(
            effective_at if linked_appointment_id is not None else None
        ),
        appointment_linked_by_ref=(
            actor_ref if linked_appointment_id is not None else None
        ),
    )
    visit_prep.created_at = effective_at
    visit_prep.updated_at = effective_at
    session.add(visit_prep)
    session.flush()
    unused_purge_at, unused_hard_purge_at, _expires_at = _schedule_deadlines(
        normalized_scheduled_at
    )
    schedule = VisitPrepScheduleGeneration(
        visit_prep_id=visit_prep.id,
        owner_practice_member_id=owner_practice_member_id,
        generation=1,
        scheduled_at=normalized_scheduled_at,
        timezone_name=normalized_timezone,
        unused_purge_at=unused_purge_at,
        unused_hard_purge_at=unused_hard_purge_at,
    )
    schedule.created_at = effective_at
    schedule.updated_at = effective_at
    session.add(schedule)
    session.flush()
    credential, access_token = _issue_credential(
        session,
        visit_prep=visit_prep,
        schedule=schedule,
        generation=1,
        issued_at=effective_at,
    )
    snapshot = _refresh_lineage(
        session,
        visit_prep=visit_prep,
        schedule=schedule,
        credential=credential,
        at=effective_at,
    )
    try:
        stage_creation_coverage(session, prep=visit_prep, credential=credential)
    except InvitationOpenEvidenceInvalid:
        raise VisitPrepUnavailable("Visit Prep is unavailable") from None
    return VisitPrepMutationResult(
        visit_prep=snapshot,
        disposition="created",
        access_token=access_token,
    )


def rotate_visit_prep_credential(
    *,
    owner_practice_member_id: TypeIDType,
    owner_doctor_id: TypeIDType,
    practice_id: TypeIDType,
    visit_prep_id: TypeIDType,
    expected_credential_generation: int,
    now: datetime | None = None,
) -> VisitPrepMutationResult:
    """Revoke the current bearer and return one fresh generation after commit."""

    effective_at = _aware_utc(now, message="rotation time is invalid")
    with _visit_prep_session() as session:
        _lock_owner_scope(
            session,
            owner_practice_member_id=owner_practice_member_id,
            owner_doctor_id=owner_doctor_id,
            practice_id=practice_id,
            now=effective_at,
        )
        visit_prep = _lock_visit_prep(
            session,
            owner_practice_member_id=owner_practice_member_id,
            visit_prep_id=visit_prep_id,
        )
        if visit_prep.revoked_at is not None or visit_prep.lifecycle_state in {
            "completed",
            "misdirected",
        }:
            raise VisitPrepConflict("inactive Visit Prep cannot rotate credentials")
        schedule, credential = _lock_current_lineage(
            session,
            visit_prep=visit_prep,
        )
        _active_credential(
            credential,
            expected_generation=expected_credential_generation,
        )
        if credential.issued_at > effective_at:
            raise VisitPrepValidation("rotation time is invalid")
        if credential.expires_at <= effective_at:
            raise VisitPrepConflict("Visit Prep schedule has already expired")

        actor_ref = _actor_ref(owner_doctor_id)
        credential.revoked_at = effective_at
        credential.revoked_by_ref = actor_ref
        credential.revocation_reason = "rotated"
        session.add(credential)
        session.flush()
        successor, access_token = _issue_credential(
            session,
            visit_prep=visit_prep,
            schedule=schedule,
            generation=credential.generation + 1,
            issued_at=effective_at,
        )
        snapshot = _refresh_lineage(
            session,
            visit_prep=visit_prep,
            schedule=schedule,
            credential=successor,
            at=effective_at,
        )
        _commit(session)
        return VisitPrepMutationResult(
            visit_prep=snapshot,
            disposition="rotated",
            access_token=access_token,
        )


def revoke_visit_prep(
    *,
    owner_practice_member_id: TypeIDType,
    owner_doctor_id: TypeIDType,
    practice_id: TypeIDType,
    visit_prep_id: TypeIDType,
    expected_credential_generation: int,
    reason: RevocationReason,
    now: datetime | None = None,
) -> VisitPrepMutationResult:
    """Irreversibly revoke the aggregate and its current credential."""

    with _visit_prep_session() as session:
        result = stage_visit_prep_revocation(
            session,
            owner_practice_member_id=owner_practice_member_id,
            owner_doctor_id=owner_doctor_id,
            practice_id=practice_id,
            visit_prep_id=visit_prep_id,
            expected_credential_generation=expected_credential_generation,
            reason=reason,
            now=now,
        )
        _commit(session)
        return result


def stage_visit_prep_revocation(
    session: Session,
    *,
    owner_practice_member_id: TypeIDType,
    owner_doctor_id: TypeIDType,
    practice_id: TypeIDType,
    visit_prep_id: TypeIDType,
    expected_credential_generation: int,
    reason: RevocationReason,
    now: datetime | None = None,
) -> VisitPrepMutationResult:
    """Stage revocation in the caller transaction; never commit or release a receipt."""

    effective_at = _aware_utc(now, message="revocation time is invalid")
    if reason not in {
        "physician_revoked",
        "wrong_recipient",
        "appointment_cancelled",
    }:
        raise VisitPrepValidation("revocation reason is invalid")
    _lock_owner_scope(
        session,
        owner_practice_member_id=owner_practice_member_id,
        owner_doctor_id=owner_doctor_id,
        practice_id=practice_id,
        now=effective_at,
    )
    visit_prep = _lock_visit_prep(
        session,
        owner_practice_member_id=owner_practice_member_id,
        visit_prep_id=visit_prep_id,
    )
    schedule, credential = _lock_current_lineage(
        session,
        visit_prep=visit_prep,
    )
    if visit_prep.revoked_at is not None:
        _expected_credential(
            credential,
            expected_generation=expected_credential_generation,
        )
        if (
            visit_prep.revocation_reason != reason
            or credential.revocation_reason != reason
        ):
            raise VisitPrepConflict("Visit Prep was revoked for another reason")
        snapshot = _snapshot(
            visit_prep,
            schedule,
            credential,
            at=effective_at,
        )
        return VisitPrepMutationResult(
            visit_prep=snapshot,
            disposition="replayed",
        )
    if reason == "wrong_recipient" and visit_prep.lifecycle_state != "invited":
        raise VisitPrepConflict("interacted recipient requires quarantine")
    _active_credential(
        credential,
        expected_generation=expected_credential_generation,
    )
    if (
        visit_prep.created_at is not None and visit_prep.created_at > effective_at
    ) or credential.issued_at > effective_at:
        raise VisitPrepValidation("revocation time is invalid")

    actor_ref = _actor_ref(owner_doctor_id)
    credential.revoked_at = effective_at
    credential.revoked_by_ref = actor_ref
    credential.revocation_reason = reason
    visit_prep.revoked_at = effective_at
    visit_prep.revoked_by_ref = actor_ref
    visit_prep.revocation_reason = reason
    session.add(credential)
    session.add(visit_prep)
    snapshot = _refresh_lineage(
        session,
        visit_prep=visit_prep,
        schedule=schedule,
        credential=credential,
        at=effective_at,
    )
    return VisitPrepMutationResult(
        visit_prep=snapshot,
        disposition="revoked",
    )


def reschedule_visit_prep(
    *,
    owner_practice_member_id: TypeIDType,
    owner_doctor_id: TypeIDType,
    practice_id: TypeIDType,
    visit_prep_id: TypeIDType,
    expected_schedule_generation: int,
    expected_credential_generation: int,
    scheduled_at: datetime,
    timezone_name: str,
    now: datetime | None = None,
) -> VisitPrepMutationResult:
    """Append a schedule generation and atomically rotate its bearer."""

    with _visit_prep_session() as session:
        result = stage_visit_prep_reschedule(
            session,
            owner_practice_member_id=owner_practice_member_id,
            owner_doctor_id=owner_doctor_id,
            practice_id=practice_id,
            visit_prep_id=visit_prep_id,
            expected_schedule_generation=expected_schedule_generation,
            expected_credential_generation=expected_credential_generation,
            scheduled_at=scheduled_at,
            timezone_name=timezone_name,
            now=now,
        )
        _commit(session)
        return result


def stage_visit_prep_reschedule(
    session: Session,
    *,
    owner_practice_member_id: TypeIDType,
    owner_doctor_id: TypeIDType,
    practice_id: TypeIDType,
    visit_prep_id: TypeIDType,
    expected_schedule_generation: int,
    expected_credential_generation: int,
    scheduled_at: datetime,
    timezone_name: str,
    now: datetime | None = None,
    allow_expired: bool = False,
) -> VisitPrepMutationResult:
    """Stage rescheduling in the caller transaction; bearer is unusable until commit."""

    effective_at = _aware_utc(now, message="reschedule time is invalid")
    if type(allow_expired) is not bool:
        raise VisitPrepValidation("reschedule recovery is invalid")
    normalized_scheduled_at, normalized_timezone = _normalized_schedule(
        scheduled_at,
        timezone_name,
        now=effective_at,
    )
    _lock_owner_scope(
        session,
        owner_practice_member_id=owner_practice_member_id,
        owner_doctor_id=owner_doctor_id,
        practice_id=practice_id,
        now=effective_at,
    )
    visit_prep = _lock_visit_prep(
        session,
        owner_practice_member_id=owner_practice_member_id,
        visit_prep_id=visit_prep_id,
    )
    if visit_prep.revoked_at is not None or visit_prep.lifecycle_state == "misdirected":
        raise VisitPrepConflict("inactive Visit Prep cannot be rescheduled")
    if visit_prep.confirmed_at is not None and (
        visit_prep.retain_until is None
        or visit_prep.retain_until <= effective_at
        or normalized_scheduled_at >= visit_prep.retain_until
    ):
        raise VisitPrepConflict("reschedule exceeds retained Visit Prep window")
    schedule, credential = _lock_current_lineage(
        session,
        visit_prep=visit_prep,
    )
    _expected_schedule(
        schedule,
        expected_generation=expected_schedule_generation,
    )
    _active_credential(
        credential,
        expected_generation=expected_credential_generation,
    )
    if credential.issued_at > effective_at:
        raise VisitPrepValidation("reschedule time is invalid")
    if credential.expires_at <= effective_at and not allow_expired:
        raise VisitPrepConflict("Visit Prep credential has already expired")
    if (
        visit_prep.confirmed_at is None
        and schedule.unused_hard_purge_at <= effective_at
    ):
        raise VisitPrepConflict("unused Visit Prep retention window has ended")

    if visit_prep.appointment_id is not None:
        appointment = _owned_appointment(
            session,
            owner_doctor_id=owner_doctor_id,
            appointment_id=visit_prep.appointment_id,
        )
        if not _appointment_matches_schedule(
            session,
            appointment_id=appointment.id,
            scheduled_at=normalized_scheduled_at,
        ):
            raise VisitPrepConflict("appointment schedule does not match")

    if (
        schedule.scheduled_at == normalized_scheduled_at
        and schedule.timezone_name == normalized_timezone
    ):
        snapshot = _snapshot(
            visit_prep,
            schedule,
            credential,
            at=effective_at,
        )
        return VisitPrepMutationResult(
            visit_prep=snapshot,
            disposition="replayed",
        )

    actor_ref = _actor_ref(owner_doctor_id)
    credential.revoked_at = effective_at
    credential.revoked_by_ref = actor_ref
    credential.revocation_reason = "rescheduled"
    session.add(credential)
    session.flush()
    unused_purge_at, unused_hard_purge_at, _expires_at = _schedule_deadlines(
        normalized_scheduled_at
    )
    successor_schedule = VisitPrepScheduleGeneration(
        visit_prep_id=visit_prep.id,
        owner_practice_member_id=owner_practice_member_id,
        generation=schedule.generation + 1,
        scheduled_at=normalized_scheduled_at,
        timezone_name=normalized_timezone,
        unused_purge_at=unused_purge_at,
        unused_hard_purge_at=unused_hard_purge_at,
    )
    successor_schedule.created_at = effective_at
    successor_schedule.updated_at = effective_at
    session.add(successor_schedule)
    session.flush()
    successor_credential, access_token = _issue_credential(
        session,
        visit_prep=visit_prep,
        schedule=successor_schedule,
        generation=credential.generation + 1,
        issued_at=effective_at,
    )
    snapshot = _refresh_lineage(
        session,
        visit_prep=visit_prep,
        schedule=successor_schedule,
        credential=successor_credential,
        at=effective_at,
    )
    return VisitPrepMutationResult(
        visit_prep=snapshot,
        disposition="rescheduled",
        access_token=access_token,
    )


def reconcile_visit_prep(
    *,
    owner_practice_member_id: TypeIDType,
    owner_doctor_id: TypeIDType,
    practice_id: TypeIDType,
    visit_prep_id: TypeIDType,
    patient_id: TypeIDType | None = None,
    appointment_id: TypeIDType | None = None,
    now: datetime | None = None,
) -> VisitPrepMutationResult:
    """Attach previously absent canonical records without mutating those records."""

    effective_at = _aware_utc(now, message="reconciliation time is invalid")
    if patient_id is None and appointment_id is None:
        raise VisitPrepValidation("a canonical record is required")
    with _visit_prep_session() as session:
        _lock_owner_scope(
            session,
            owner_practice_member_id=owner_practice_member_id,
            owner_doctor_id=owner_doctor_id,
            practice_id=practice_id,
            now=effective_at,
        )
        visit_prep = _lock_visit_prep(
            session,
            owner_practice_member_id=owner_practice_member_id,
            visit_prep_id=visit_prep_id,
        )
        if (
            visit_prep.revoked_at is not None
            or visit_prep.lifecycle_state == "misdirected"
        ):
            raise VisitPrepConflict("inactive Visit Prep cannot be reconciled")
        schedule, credential = _lock_current_lineage(
            session,
            visit_prep=visit_prep,
        )

        linked_patient_id = patient_id
        if appointment_id is not None:
            appointment = _owned_appointment(
                session,
                owner_doctor_id=owner_doctor_id,
                appointment_id=appointment_id,
            )
            if not _appointment_matches_schedule(
                session,
                appointment_id=appointment.id,
                scheduled_at=schedule.scheduled_at,
            ):
                raise VisitPrepConflict("appointment schedule does not match")
            if (
                linked_patient_id is not None
                and linked_patient_id != appointment.patient_id
            ):
                raise VisitPrepConflict("appointment patient does not match")
            linked_patient_id = appointment.patient_id
        if linked_patient_id is not None:
            _owned_patient(
                session,
                owner_doctor_id=owner_doctor_id,
                patient_id=linked_patient_id,
            )

        if (
            visit_prep.patient_id is not None
            and visit_prep.patient_id != linked_patient_id
        ):
            raise VisitPrepConflict("Visit Prep patient is already linked")
        if (
            visit_prep.appointment_id is not None
            and appointment_id is not None
            and visit_prep.appointment_id != appointment_id
        ):
            raise VisitPrepConflict("Visit Prep appointment is already linked")

        patient_changed = (
            visit_prep.patient_id is None and linked_patient_id is not None
        )
        appointment_changed = (
            visit_prep.appointment_id is None and appointment_id is not None
        )
        if not patient_changed and not appointment_changed:
            snapshot = _snapshot(
                visit_prep,
                schedule,
                credential,
                at=effective_at,
            )
            _commit(session)
            return VisitPrepMutationResult(
                visit_prep=snapshot,
                disposition="replayed",
            )
        if visit_prep.created_at is not None and visit_prep.created_at > effective_at:
            raise VisitPrepValidation("reconciliation time is invalid")

        actor_ref = _actor_ref(owner_doctor_id)
        if patient_changed:
            visit_prep.patient_id = linked_patient_id
            visit_prep.patient_linked_at = effective_at
            visit_prep.patient_linked_by_ref = actor_ref
        if appointment_changed:
            visit_prep.appointment_id = appointment_id
            visit_prep.appointment_linked_at = effective_at
            visit_prep.appointment_linked_by_ref = actor_ref
        session.add(visit_prep)
        snapshot = _refresh_lineage(
            session,
            visit_prep=visit_prep,
            schedule=schedule,
            credential=credential,
            at=effective_at,
        )
        _commit(session)
        return VisitPrepMutationResult(
            visit_prep=snapshot,
            disposition="reconciled",
        )
