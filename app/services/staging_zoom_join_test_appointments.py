"""Staging-only synthetic Zoom join-test patients and appointments.

Creates two unmistakably fake patients and four transcript-free appointments
for the fixed staging lab A doctor. Idempotent on appointment external_id.
Print-facing callers must emit TypeIDs and created/exists only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.environments import is_staging

from app.models.appointment import Appointment
from app.models.doctor import Doctor
from app.models.patient import Patient

LAB_A_DOCTOR_EMAIL = "scribe-lab-a+clerk_test@cosmaneura.com"
DEFAULT_RUN_DATE = "2026-11-17"
EXTERNAL_ID_PREFIX = "zoom-synthetic-join"
CREATED_BY_SYSTEM = "operator-zoom-synthetic-join-20261117"
APPOINTMENT_NOTES = (
    "Synthetic Zoom join-test visit only. Contains no real patient data."
)

SYNTHETIC_PATIENTS: tuple[tuple[str, datetime], ...] = (
    (
        "Synthetic Patient Alpha - NOT A REAL PERSON",
        datetime(1990, 1, 15, tzinfo=UTC).replace(tzinfo=None),
    ),
    (
        "Synthetic Patient Bravo - NOT A REAL PERSON",
        datetime(1991, 2, 20, tzinfo=UTC).replace(tzinfo=None),
    ),
)


class StagingZoomJoinTestError(ValueError):
    """Safe, operator-facing refusal for Zoom join-test provisioning."""


class StagingZoomJoinTestConflict(StagingZoomJoinTestError):
    pass


@dataclass(frozen=True)
class ZoomJoinAppointmentResult:
    appointment_id: str
    patient_id: str
    external_id: str
    created: bool


@dataclass(frozen=True)
class StagingZoomJoinTestResult:
    appointments: tuple[ZoomJoinAppointmentResult, ...]


def _normalized_naive_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def default_appointment_start(*, now: datetime | None = None) -> datetime:
    """Next whole hour UTC, plus one hour, stored as naive UTC."""

    current = now if now is not None else datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    else:
        current = current.astimezone(UTC)
    floor = current.replace(minute=0, second=0, microsecond=0)
    next_whole_hour = floor + timedelta(hours=1)
    return (next_whole_hour + timedelta(hours=1)).replace(tzinfo=None)


def _validate_run_date(run_date: str) -> str:
    try:
        datetime.strptime(run_date, "%Y-%m-%d")
    except ValueError as exc:
        raise StagingZoomJoinTestError(
            "run_date must be an exact YYYY-MM-DD calendar day"
        ) from exc
    if run_date != run_date.strip():
        raise StagingZoomJoinTestError(
            "run_date must be an exact YYYY-MM-DD calendar day"
        )
    return run_date


def _resolve_start_time(start_time: datetime | None) -> datetime:
    if start_time is None:
        return default_appointment_start()
    return _normalized_naive_utc(start_time)


def ensure_staging_zoom_join_test_appointments(
    *,
    run_date: str = DEFAULT_RUN_DATE,
    start_time: datetime | None = None,
) -> StagingZoomJoinTestResult:
    """Create or return four fixed synthetic Zoom join-test appointments."""

    if not is_staging():
        raise StagingZoomJoinTestError(
            "zoom join-test appointment provisioning is restricted to staging"
        )

    run_date = _validate_run_date(run_date)
    first_start = _resolve_start_time(start_time)
    external_prefix = f"{EXTERNAL_ID_PREFIX}-{run_date}"

    doctor = Doctor.get(email=LAB_A_DOCTOR_EMAIL)
    if doctor is None or doctor.deleted_at is not None or doctor.last_active_at is None:
        raise StagingZoomJoinTestError(
            "staging lab A doctor was missing, deleted, or has never signed in"
        )

    patients: list[Patient] = []
    for name, date_of_birth in SYNTHETIC_PATIENTS:
        patients.append(
            Patient.find_or_create_by(
                doctor_id=doctor.id,
                name=name,
                date_of_birth=date_of_birth,
            )
        )

    # Appointments 1-2 belong to Alpha; 3-4 belong to Bravo.
    patient_for_slot = (
        patients[0],
        patients[0],
        patients[1],
        patients[1],
    )
    results: list[ZoomJoinAppointmentResult] = []
    for index in range(1, 5):
        external_id = f"{external_prefix}:{index}"
        appointment_name = f"SYNTHETIC ZOOM JOIN TEST {index} - NO REAL PATIENT DATA"
        appointment_date = first_start + timedelta(hours=index - 1)
        expected_patient = patient_for_slot[index - 1]

        existing = Appointment.get(doctor_id=doctor.id, external_id=external_id)
        if existing is not None:
            valid = (
                existing.patient_id == expected_patient.id
                and existing.created_by_system == CREATED_BY_SYSTEM
                and existing.appointment_name == appointment_name
                and existing.appointment_notes_preview == APPOINTMENT_NOTES
            )
            if not valid:
                raise StagingZoomJoinTestConflict(
                    "existing zoom join-test appointment failed its "
                    "fixed-data integrity check"
                )
            results.append(
                ZoomJoinAppointmentResult(
                    appointment_id=str(existing.id),
                    patient_id=str(expected_patient.id),
                    external_id=external_id,
                    created=False,
                )
            )
            continue

        appointment = Appointment(
            doctor_id=doctor.id,
            patient_id=expected_patient.id,
            appointment_date=appointment_date,
            appointment_name=appointment_name,
            appointment_notes_preview=APPOINTMENT_NOTES,
            created_by_system=CREATED_BY_SYSTEM,
            external_id=external_id,
        ).save()
        results.append(
            ZoomJoinAppointmentResult(
                appointment_id=str(appointment.id),
                patient_id=str(expected_patient.id),
                external_id=external_id,
                created=True,
            )
        )

    return StagingZoomJoinTestResult(appointments=tuple(results))
