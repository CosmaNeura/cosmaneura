from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, cast

from activemodel.session_manager import get_session
from activemodel.types import TypeIDType
from app.models.appointment import Appointment
from app.models.ehr_sandbox_record_mapping import (
    ATHENA_ADAPTER,
    ATHENA_PREVIEW_ENVIRONMENT,
    ATHENA_PREVIEW_PRACTICE,
    EHRSandboxRecordMapping,
)
from app.models.patient import Patient
from sqlalchemy import and_
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

_OPAQUE_REF = re.compile(r"^[A-Za-z0-9._~-]+$")


class SandboxRecordMappingError(ValueError):
    """Base class for safe operator-facing mapping refusals."""


class SandboxRecordMappingNotFound(SandboxRecordMappingError):
    pass


class SandboxRecordMappingConflict(SandboxRecordMappingError):
    pass


def _validated_opaque(value: str, *, field: str, maximum: int = 160) -> str:
    if not 3 <= len(value) <= maximum or not _OPAQUE_REF.fullmatch(value):
        raise SandboxRecordMappingError(f"{field} must be an opaque reference")
    return value


def provision_athena_preview_mapping(
    *,
    doctor_id: TypeIDType,
    patient_id: TypeIDType,
    record_ref: str,
    operator_ref: str,
) -> EHRSandboxRecordMapping:
    """Create one active synthetic Preview mapping, idempotently.

    Neither practice nor environment is caller-selectable. Locking the patient
    and any current binding makes two concurrent operator attempts serialize;
    partial unique indexes provide the final database-level fence.
    """

    record_ref = _validated_opaque(record_ref, field="record_ref")
    operator_ref = _validated_opaque(operator_ref, field="operator_ref")

    with get_session() as session:
        patient = session.exec(
            select(Patient)
            .where(Patient.id == patient_id, Patient.doctor_id == doctor_id)
            .with_for_update()
        ).one_or_none()
        if patient is None:
            raise SandboxRecordMappingNotFound(
                "patient was not found in the specified doctor's scope"
            )

        current = session.exec(
            select(EHRSandboxRecordMapping)
            .where(
                EHRSandboxRecordMapping.doctor_id == doctor_id,
                EHRSandboxRecordMapping.patient_id == patient_id,
                cast(Any, EHRSandboxRecordMapping.revoked_at).is_(None),
            )
            .with_for_update()
        ).one_or_none()
        if current is not None:
            if current.record_ref == record_ref:
                return current
            raise SandboxRecordMappingConflict(
                "the doctor/patient scope already has an active sandbox mapping"
            )

        record_owner = session.exec(
            select(EHRSandboxRecordMapping)
            .where(
                EHRSandboxRecordMapping.adapter == ATHENA_ADAPTER,
                EHRSandboxRecordMapping.environment == ATHENA_PREVIEW_ENVIRONMENT,
                EHRSandboxRecordMapping.practice_ref == ATHENA_PREVIEW_PRACTICE,
                EHRSandboxRecordMapping.record_ref == record_ref,
                cast(Any, EHRSandboxRecordMapping.revoked_at).is_(None),
            )
            .with_for_update()
        ).one_or_none()
        if record_owner is not None:
            raise SandboxRecordMappingConflict(
                "the sandbox record is already mapped to another scope"
            )

        mapping = EHRSandboxRecordMapping(
            doctor_id=doctor_id,
            patient_id=patient_id,
            record_ref=record_ref,
            provisioned_by=operator_ref,
        )
        session.add(mapping)
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            raise SandboxRecordMappingConflict(
                "a concurrent sandbox mapping change won the race; retry after review"
            ) from exc
        session.refresh(mapping)
        return mapping


def revoke_athena_preview_mapping(
    *,
    doctor_id: TypeIDType,
    patient_id: TypeIDType,
    operator_ref: str,
) -> EHRSandboxRecordMapping:
    """Revoke the active mapping while retaining its full history."""

    operator_ref = _validated_opaque(operator_ref, field="operator_ref")
    with get_session() as session:
        mapping = session.exec(
            select(EHRSandboxRecordMapping)
            .where(
                EHRSandboxRecordMapping.doctor_id == doctor_id,
                EHRSandboxRecordMapping.patient_id == patient_id,
                EHRSandboxRecordMapping.adapter == ATHENA_ADAPTER,
                EHRSandboxRecordMapping.environment == ATHENA_PREVIEW_ENVIRONMENT,
                EHRSandboxRecordMapping.practice_ref == ATHENA_PREVIEW_PRACTICE,
                cast(Any, EHRSandboxRecordMapping.synthetic_only).is_(True),
                cast(Any, EHRSandboxRecordMapping.revoked_at).is_(None),
            )
            .with_for_update()
        ).one_or_none()
        if mapping is None:
            raise SandboxRecordMappingNotFound("no active sandbox mapping was found")

        mapping.revoked_at = datetime.now(UTC)
        mapping.revoked_by = operator_ref
        session.add(mapping)
        session.commit()
        session.refresh(mapping)
        return mapping


def resolve_open_athena_preview_record_ref(
    *,
    doctor_id: TypeIDType,
    appointment_id: TypeIDType,
) -> str | None:
    """Resolve only an active, synthetic mapping for the doctor's open record.

    Appointment ownership, patient ownership, mapping scope, environment and
    revocation state are evaluated in one database statement. A shared row lock
    keeps a concurrent revocation from committing until this response finishes.
    """

    with get_session() as session:
        statement = (
            select(EHRSandboxRecordMapping.record_ref)
            .join(
                Appointment,
                and_(
                    cast(Any, Appointment.patient_id)
                    == EHRSandboxRecordMapping.patient_id,
                    cast(Any, Appointment.doctor_id)
                    == EHRSandboxRecordMapping.doctor_id,
                ),
            )
            .join(
                Patient,
                and_(
                    cast(Any, Patient.id) == Appointment.patient_id,
                    cast(Any, Patient.doctor_id) == Appointment.doctor_id,
                ),
            )
            .where(
                Appointment.id == appointment_id,
                Appointment.doctor_id == doctor_id,
                EHRSandboxRecordMapping.doctor_id == doctor_id,
                EHRSandboxRecordMapping.adapter == ATHENA_ADAPTER,
                EHRSandboxRecordMapping.environment == ATHENA_PREVIEW_ENVIRONMENT,
                EHRSandboxRecordMapping.practice_ref == ATHENA_PREVIEW_PRACTICE,
                cast(Any, EHRSandboxRecordMapping.synthetic_only).is_(True),
                cast(Any, EHRSandboxRecordMapping.revoked_at).is_(None),
            )
            .with_for_update(read=True, of=EHRSandboxRecordMapping)
        )
        return session.exec(statement).one_or_none()
