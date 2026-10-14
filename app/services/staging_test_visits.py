"""Create an idempotent pair of empty, fixed-data staging recording visits."""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID

from app.environments import is_staging
from app.services.staging_synthetic_appointment import (
    StagingSyntheticAppointmentConflict,
    StagingSyntheticAppointmentError,
    ensure_staging_synthetic_appointment,
    validate_exact_synthetic_fixture,
)

from activemodel.session_manager import get_session
from activemodel.types import TypeIDType
from app.models.appointment import Appointment
from app.models.doctor import Doctor
from app.models.patient import Patient
from app.models.synthetic_preview_appointment_fixture import (
    SyntheticPreviewAppointmentFixture,
)
from sqlmodel import select

TEST_VISIT_SYSTEM = "staging-recording-test-v1"
TEST_VISIT_NOTICE = "Invented acceptance test only. Contains no real patient data."


@dataclass(frozen=True)
class CreatedTestVisit:
    id: str
    appointment_name: str


@dataclass(frozen=True)
class StagingTestVisitsResult:
    appointments: tuple[CreatedTestVisit, CreatedTestVisit]
    created: bool


def _snapshot_pair(
    rows: list[Appointment],
) -> tuple[CreatedTestVisit, CreatedTestVisit]:
    return (
        CreatedTestVisit(str(rows[0].id), str(rows[0].appointment_name)),
        CreatedTestVisit(str(rows[1].id), str(rows[1].appointment_name)),
    )


def ensure_staging_test_visits(
    *,
    doctor_id: TypeIDType,
    doctor_email: str,
    request_id: UUID,
    visit_date: date,
) -> StagingTestVisitsResult:
    if not is_staging():
        raise StagingSyntheticAppointmentError("test visits are restricted to staging")
    today = datetime.now(UTC).date()
    if (
        not isinstance(request_id, UUID)
        or type(visit_date) is not date
        or not today - timedelta(days=7) <= visit_date <= today
    ):
        raise StagingSyntheticAppointmentError("invalid test visit request")

    # Reuse the governed fixed-data patient. Its independent provisioning is
    # idempotent; it does not create Scribe admission, audio or vendor authority.
    ensure_staging_synthetic_appointment(
        doctor_id=doctor_id,
        doctor_email=doctor_email,
        operator_ref=f"staging-web.{request_id}",
    )
    marker = f"{TEST_VISIT_SYSTEM}.{request_id}"
    expected = {
        f"Invented CosmaBot acceptance {label} {visit_date:%Y%m%d}": datetime.combine(
            visit_date, time(12, minute)
        )
        for label, minute in (("one", 0), ("two", 1))
    }
    with get_session() as session:
        try:
            # Serialize retries before inspecting or writing either visit.
            doctor = session.exec(
                select(Doctor)
                .where(Doctor.id == doctor_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            ).one_or_none()
            if (
                doctor is None
                or doctor.email != doctor_email
                or doctor.deleted_at is not None
                or doctor.last_active_at is None
            ):
                raise StagingSyntheticAppointmentError("staging account unavailable")
            fixture = session.exec(
                select(SyntheticPreviewAppointmentFixture)
                .where(SyntheticPreviewAppointmentFixture.doctor_id == doctor_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            ).one_or_none()
            if fixture is None:
                raise StagingSyntheticAppointmentError("staging fixture unavailable")
            patient = session.exec(
                select(Patient)
                .where(Patient.id == fixture.patient_id)
                .with_for_update(read=True)
                .execution_options(populate_existing=True)
            ).one_or_none()
            original = session.get(
                Appointment, fixture.appointment_id, populate_existing=True
            )
            validate_exact_synthetic_fixture(
                doctor=doctor,
                fixture=fixture,
                patient=patient,
                appointment=original,
            )
            rows = list(
                session.exec(
                    select(Appointment)
                    .where(
                        Appointment.doctor_id == doctor_id,
                        Appointment.created_by_system == marker,
                    )
                    .with_for_update()
                    .execution_options(populate_existing=True)
                ).all()
            )
            if rows:
                if (
                    len(rows) != 2
                    or {row.appointment_name for row in rows} != set(expected)
                    or any(
                        row.patient_id != fixture.patient_id
                        or row.appointment_name is None
                        or row.appointment_date != expected[row.appointment_name]
                        or row.appointment_notes_preview != TEST_VISIT_NOTICE
                        or row.external_id is not None
                        for row in rows
                    )
                ):
                    raise StagingSyntheticAppointmentConflict(
                        "test visit receipt changed"
                    )
                return StagingTestVisitsResult(
                    _snapshot_pair(sorted(rows, key=lambda row: row.appointment_date)),
                    False,
                )
            rows = [
                Appointment(
                    doctor_id=doctor_id,
                    patient_id=fixture.patient_id,
                    appointment_date=instant,
                    appointment_name=name,
                    appointment_notes_preview=TEST_VISIT_NOTICE,
                    created_by_system=marker,
                    external_id=None,
                )
                for name, instant in expected.items()
            ]
            session.add_all(rows)
            session.commit()
            for row in rows:
                session.refresh(row)
            return StagingTestVisitsResult(_snapshot_pair(rows), True)
        finally:
            session.rollback()
