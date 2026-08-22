from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

from app.environments import is_staging

from activemodel.session_manager import get_session
from activemodel.types import TypeIDType
from app.models.appointment import Appointment
from app.models.doctor import Doctor
from app.models.patient import Patient
from app.models.synthetic_preview_appointment_fixture import (
    SYNTHETIC_PREVIEW_FIXTURE_VERSION,
    SyntheticPreviewAppointmentFixture,
)
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

SYNTHETIC_PATIENT_NAME = "Cosmaneura Synthetic Preview Patient - NOT A REAL PERSON"
# These legacy columns are PostgreSQL timestamps without time zone. Build the
# fixed values from explicit UTC and store them as naive UTC so the DB driver
# cannot shift them according to the session time zone.
SYNTHETIC_PATIENT_DATE_OF_BIRTH = datetime(2000, 1, 1, tzinfo=UTC).replace(tzinfo=None)
SYNTHETIC_APPOINTMENT_DATE = datetime(2099, 1, 1, 15, 0, tzinfo=UTC).replace(
    tzinfo=None
)
SYNTHETIC_APPOINTMENT_NAME = "SYNTHETIC PREVIEW LAB DEMO - NO REAL PATIENT DATA"
SYNTHETIC_APPOINTMENT_NOTES = (
    "Synthetic test fixture only. Contains no real patient data."
)
SYNTHETIC_CREATED_BY_SYSTEM = "operator-synthetic-preview-v1"

_OPAQUE_OPERATOR_REF = re.compile(r"^[A-Za-z0-9._~-]+$")
_SAFE_EMAIL = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+$")


class StagingSyntheticAppointmentError(ValueError):
    """Safe, operator-facing refusal for synthetic fixture provisioning."""


class StagingSyntheticAppointmentConflict(StagingSyntheticAppointmentError):
    pass


@dataclass(frozen=True)
class StagingSyntheticAppointmentResult:
    patient: Patient
    appointment: Appointment
    fixture: SyntheticPreviewAppointmentFixture
    created: bool


def _validate_operator_ref(value: str) -> str:
    if not 3 <= len(value) <= 160 or not _OPAQUE_OPERATOR_REF.fullmatch(value):
        raise StagingSyntheticAppointmentError(
            "operator_ref must be an opaque audit reference"
        )
    return value


def _validate_exact_email(value: str) -> str:
    if (
        not 3 <= len(value) <= 254
        or value != value.strip()
        or not _SAFE_EMAIL.fullmatch(value)
    ):
        raise StagingSyntheticAppointmentError(
            "doctor_email must be one exact, safe email value"
        )
    return value


def _normalized_naive_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def _validate_existing_fixture(
    *,
    doctor: Doctor,
    fixture: SyntheticPreviewAppointmentFixture,
    patient: Patient | None,
    appointment: Appointment | None,
) -> None:
    valid = (
        patient is not None
        and appointment is not None
        and fixture.fixture_version == SYNTHETIC_PREVIEW_FIXTURE_VERSION
        and fixture.synthetic_only is True
        and fixture.doctor_id == doctor.id
        and patient.doctor_id == doctor.id
        and patient.name == SYNTHETIC_PATIENT_NAME
        and _normalized_naive_utc(patient.date_of_birth)
        == SYNTHETIC_PATIENT_DATE_OF_BIRTH
        and appointment.doctor_id == doctor.id
        and appointment.patient_id == patient.id
        and _normalized_naive_utc(appointment.appointment_date)
        == SYNTHETIC_APPOINTMENT_DATE
        and appointment.appointment_name == SYNTHETIC_APPOINTMENT_NAME
        and appointment.appointment_notes_preview == SYNTHETIC_APPOINTMENT_NOTES
        and appointment.created_by_system == SYNTHETIC_CREATED_BY_SYSTEM
        and appointment.external_id is None
    )
    if not valid:
        raise StagingSyntheticAppointmentConflict(
            "existing synthetic fixture failed its fixed-data integrity check"
        )


def ensure_staging_synthetic_appointment(
    *,
    doctor_id: TypeIDType,
    doctor_email: str,
    operator_ref: str,
) -> StagingSyntheticAppointmentResult:
    """Create or return one fixed synthetic appointment for an exact doctor.

    The service is staging-only and never accepts patient data, a vendor record
    reference, or an EHR identifier. It also does not designate or map the
    patient; those remain separate, explicitly confirmed operator operations.
    """

    if not is_staging():
        raise StagingSyntheticAppointmentError(
            "synthetic appointment provisioning is restricted to staging"
        )

    doctor_email = _validate_exact_email(doctor_email)
    operator_ref = _validate_operator_ref(operator_ref)

    with get_session() as session:
        # The doctor row is both the exact identity lookup and the concurrency
        # lock. We never search for a provider or patient by a supplied name.
        doctor = session.exec(
            select(Doctor).where(Doctor.id == doctor_id).with_for_update()
        ).one_or_none()
        if (
            doctor is None
            or doctor.deleted_at is not None
            or doctor.email != doctor_email
            or doctor.last_active_at is None
        ):
            raise StagingSyntheticAppointmentError(
                "doctor id and email did not match one signed-in staging doctor"
            )

        fixture = session.exec(
            select(SyntheticPreviewAppointmentFixture)
            .where(SyntheticPreviewAppointmentFixture.doctor_id == doctor.id)
            .with_for_update()
        ).one_or_none()
        if fixture is not None:
            patient = session.exec(
                select(Patient).where(
                    Patient.id == fixture.patient_id,
                    Patient.doctor_id == doctor.id,
                )
            ).one_or_none()
            appointment = session.exec(
                select(Appointment).where(
                    Appointment.id == fixture.appointment_id,
                    Appointment.doctor_id == doctor.id,
                    Appointment.patient_id == fixture.patient_id,
                )
            ).one_or_none()
            _validate_existing_fixture(
                doctor=doctor,
                fixture=fixture,
                patient=patient,
                appointment=appointment,
            )
            return StagingSyntheticAppointmentResult(
                patient=cast(Patient, patient),
                appointment=cast(Appointment, appointment),
                fixture=fixture,
                created=False,
            )

        try:
            patient = Patient(
                doctor_id=doctor.id,
                name=SYNTHETIC_PATIENT_NAME,
                date_of_birth=SYNTHETIC_PATIENT_DATE_OF_BIRTH,
            )
            session.add(patient)
            # TypeIDs are assigned by ActiveModel at INSERT time, so each
            # parent must be flushed before constructing its dependent row.
            session.flush()

            appointment = Appointment(
                doctor_id=doctor.id,
                patient_id=patient.id,
                appointment_date=SYNTHETIC_APPOINTMENT_DATE,
                appointment_name=SYNTHETIC_APPOINTMENT_NAME,
                appointment_notes_preview=SYNTHETIC_APPOINTMENT_NOTES,
                created_by_system=SYNTHETIC_CREATED_BY_SYSTEM,
                external_id=None,
            )
            session.add(appointment)
            session.flush()

            fixture = SyntheticPreviewAppointmentFixture(
                doctor_id=doctor.id,
                patient_id=patient.id,
                appointment_id=appointment.id,
                provisioned_by=operator_ref,
            )
            session.add(fixture)
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            raise StagingSyntheticAppointmentConflict(
                "a concurrent synthetic fixture change won the race; retry after review"
            ) from exc

        session.refresh(patient)
        session.refresh(appointment)
        session.refresh(fixture)
        return StagingSyntheticAppointmentResult(
            patient=patient,
            appointment=appointment,
            fixture=fixture,
            created=True,
        )
