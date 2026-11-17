from datetime import UTC, datetime, timedelta

import pytest

import app.services.staging_zoom_join_test_appointments as zoom_join_service
from app.services.staging_zoom_join_test_appointments import (
    APPOINTMENT_NOTES,
    CREATED_BY_SYSTEM,
    LAB_A_DOCTOR_EMAIL,
    StagingZoomJoinTestError,
    default_appointment_start,
    ensure_staging_zoom_join_test_appointments,
)

from app.models.appointment import Appointment
from app.models.doctor import Doctor
from app.models.patient import Patient


def _lab_a_doctor(**overrides) -> Doctor:
    values = {
        "clerk_id": "user_test_zoom_join_lab_a",
        "email": LAB_A_DOCTOR_EMAIL,
        "biography": None,
        "last_active_at": datetime.now(UTC),
    }
    values.update(overrides)
    return Doctor(**values).save()


def _enable_staging(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(zoom_join_service, "is_staging", lambda: True)


def test_default_start_is_next_whole_hour_plus_one_naive_utc():
    now = datetime(2026, 11, 17, 15, 23, 45, tzinfo=UTC)
    assert default_appointment_start(now=now) == datetime(2026, 11, 17, 17, 0, 0)


def test_ensure_refuses_outside_staging(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(zoom_join_service, "is_staging", lambda: False)
    _lab_a_doctor()

    with pytest.raises(StagingZoomJoinTestError, match="restricted to staging"):
        ensure_staging_zoom_join_test_appointments()


def test_ensure_refuses_missing_doctor(monkeypatch: pytest.MonkeyPatch):
    _enable_staging(monkeypatch)

    with pytest.raises(StagingZoomJoinTestError, match="missing, deleted"):
        ensure_staging_zoom_join_test_appointments()


def test_ensure_refuses_doctor_who_has_not_signed_in(monkeypatch: pytest.MonkeyPatch):
    _enable_staging(monkeypatch)
    _lab_a_doctor(last_active_at=None)

    with pytest.raises(StagingZoomJoinTestError, match="never signed in"):
        ensure_staging_zoom_join_test_appointments()


def test_ensure_refuses_deleted_doctor(monkeypatch: pytest.MonkeyPatch):
    _enable_staging(monkeypatch)
    doctor = _lab_a_doctor()
    doctor.deleted_at = datetime.now(UTC)
    doctor.save()

    with pytest.raises(StagingZoomJoinTestError, match="missing, deleted"):
        ensure_staging_zoom_join_test_appointments()


def test_ensure_creates_four_appointments_and_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
):
    _enable_staging(monkeypatch)
    doctor = _lab_a_doctor()
    start = datetime(2026, 11, 18, 16, 0, 0)

    first = ensure_staging_zoom_join_test_appointments(start_time=start)
    second = ensure_staging_zoom_join_test_appointments(start_time=start)

    assert len(first.appointments) == 4
    assert all(item.created for item in first.appointments)
    assert all(not item.created for item in second.appointments)
    assert [item.appointment_id for item in first.appointments] == [
        item.appointment_id for item in second.appointments
    ]
    assert [item.external_id for item in first.appointments] == [
        "zoom-synthetic-join-2026-11-17:1",
        "zoom-synthetic-join-2026-11-17:2",
        "zoom-synthetic-join-2026-11-17:3",
        "zoom-synthetic-join-2026-11-17:4",
    ]

    assert Patient.where(Patient.doctor_id == doctor.id).count() == 2
    assert Appointment.where(Appointment.doctor_id == doctor.id).count() == 4

    alpha = Patient.get(
        doctor_id=doctor.id,
        name="Synthetic Patient Alpha - NOT A REAL PERSON",
    )
    bravo = Patient.get(
        doctor_id=doctor.id,
        name="Synthetic Patient Bravo - NOT A REAL PERSON",
    )
    assert alpha is not None
    assert bravo is not None
    assert alpha.date_of_birth == datetime(1990, 1, 15, tzinfo=UTC).replace(tzinfo=None)
    assert bravo.date_of_birth == datetime(1991, 2, 20, tzinfo=UTC).replace(tzinfo=None)

    expected_patients = {
        1: alpha,
        2: alpha,
        3: bravo,
        4: bravo,
    }
    for index in range(1, 5):
        external_id = f"zoom-synthetic-join-2026-11-17:{index}"
        row = Appointment.get(doctor_id=doctor.id, external_id=external_id)
        assert row is not None
        assert row.patient_id == expected_patients[index].id
        assert row.appointment_date == start + timedelta(hours=index - 1)
        assert row.appointment_name == (
            f"SYNTHETIC ZOOM JOIN TEST {index} - NO REAL PATIENT DATA"
        )
        assert row.appointment_notes_preview == APPOINTMENT_NOTES
        assert row.created_by_system == CREATED_BY_SYSTEM
