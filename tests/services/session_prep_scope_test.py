from dataclasses import replace
from datetime import UTC, datetime

import pytest

import app.services.session_prep_scope as session_prep_scope
import app.services.staging_synthetic_appointment as staging_fixture_service
from app.configuration.session_prep import SessionPrepSyntheticSettings
from app.services.session_prep_scope import synthetic_session_prep_context_ref
from app.services.staging_synthetic_appointment import (
    StagingSyntheticAppointmentResult,
    ensure_staging_synthetic_appointment,
)

from activemodel.session_manager import get_session
from app.models.appointment import Appointment
from app.models.doctor import Doctor
from app.models.patient import Patient
from app.models.synthetic_preview_appointment_fixture import (
    SyntheticPreviewAppointmentFixture,
)

CONTEXT_SNAPSHOT_REF = "context_synthetic_session_prep_v1"


def _doctor(suffix: str) -> Doctor:
    return Doctor(
        clerk_id=f"user_test_session_prep_scope_{suffix}",
        email=f"session-prep-scope-{suffix}@example.test",
        biography=None,
        last_active_at=datetime.now(UTC),
    ).save()


def _fixture(monkeypatch: pytest.MonkeyPatch, suffix: str):
    monkeypatch.setattr(staging_fixture_service, "is_staging", lambda: True)
    doctor = _doctor(suffix)
    fixture = ensure_staging_synthetic_appointment(
        doctor_id=doctor.id,
        doctor_email=doctor.email,
        operator_ref=f"operator_session_prep_{suffix}",
    )
    return doctor, fixture


def _settings(
    doctor: Doctor,
    *,
    fixture: StagingSyntheticAppointmentResult,
    enabled: bool = True,
    clerk_subject: str | None = None,
    appointment_id: str | None = None,
    fixture_id: str | None = None,
) -> SessionPrepSyntheticSettings:
    return SessionPrepSyntheticSettings(
        enabled=enabled,
        context_snapshot_ref=CONTEXT_SNAPSHOT_REF,
        clerk_subject=clerk_subject or doctor.clerk_id,
        appointment_id=appointment_id or str(fixture.appointment.id),
        fixture_id=fixture_id or str(fixture.fixture.id),
    )


def _resolve(
    doctor: Doctor,
    fixture: StagingSyntheticAppointmentResult,
    *,
    settings: SessionPrepSyntheticSettings | None = None,
) -> str | None:
    return synthetic_session_prep_context_ref(
        clerk_subject=doctor.clerk_id,
        record_ref=str(fixture.appointment.id),
        settings=settings or _settings(doctor, fixture=fixture),
    )


def test_scope_is_default_off_and_requires_complete_staging_configuration(monkeypatch):
    doctor, fixture = _fixture(monkeypatch, "configuration")
    monkeypatch.setattr(session_prep_scope, "is_staging", lambda: True)
    complete = _settings(doctor, fixture=fixture)

    for incomplete in (
        replace(complete, enabled=False),
        replace(complete, context_snapshot_ref=""),
        replace(complete, clerk_subject=""),
        replace(complete, appointment_id=""),
        replace(complete, fixture_id=""),
    ):
        assert _resolve(doctor, fixture, settings=incomplete) is None

    monkeypatch.setattr(session_prep_scope, "is_staging", lambda: False)
    assert _resolve(doctor, fixture) is None

    monkeypatch.setattr(session_prep_scope, "is_staging", lambda: True)
    assert _resolve(doctor, fixture) == CONTEXT_SNAPSHOT_REF


def test_scope_rejects_a_second_otherwise_valid_synthetic_fixture(monkeypatch):
    pinned_doctor, pinned_fixture = _fixture(monkeypatch, "pinned")
    other_doctor, other_fixture = _fixture(monkeypatch, "second-valid")
    monkeypatch.setattr(session_prep_scope, "is_staging", lambda: True)
    pinned_settings = _settings(pinned_doctor, fixture=pinned_fixture)

    assert _resolve(pinned_doctor, pinned_fixture, settings=pinned_settings)
    assert _resolve(other_doctor, other_fixture, settings=pinned_settings) is None

    wrong_appointment_pin = replace(
        pinned_settings,
        appointment_id=str(other_fixture.appointment.id),
    )
    wrong_fixture_pin = replace(
        pinned_settings,
        fixture_id=str(other_fixture.fixture.id),
    )
    assert (
        _resolve(pinned_doctor, pinned_fixture, settings=wrong_appointment_pin) is None
    )
    assert _resolve(pinned_doctor, pinned_fixture, settings=wrong_fixture_pin) is None


def test_scope_rejects_wrong_record_and_current_clerk_owner(monkeypatch):
    doctor, fixture = _fixture(monkeypatch, "ownership")
    other_doctor = _doctor("other-owner")
    ordinary_patient = Patient(
        doctor_id=doctor.id,
        name="Synthetic ordinary nonfixture record",
        date_of_birth=datetime(2001, 1, 1),
    ).save()
    ordinary_appointment = Appointment(
        doctor_id=doctor.id,
        patient_id=ordinary_patient.id,
        appointment_date=datetime(2098, 1, 1),
        external_id=None,
    ).save()
    monkeypatch.setattr(session_prep_scope, "is_staging", lambda: True)

    pinned_settings = _settings(doctor, fixture=fixture)
    assert (
        synthetic_session_prep_context_ref(
            clerk_subject=doctor.clerk_id,
            record_ref=str(ordinary_appointment.id),
            settings=pinned_settings,
        )
        is None
    )
    assert (
        synthetic_session_prep_context_ref(
            clerk_subject=other_doctor.clerk_id,
            record_ref=str(fixture.appointment.id),
            settings=pinned_settings,
        )
        is None
    )
    assert (
        synthetic_session_prep_context_ref(
            clerk_subject=doctor.clerk_id,
            record_ref=str(fixture.patient.id),
            settings=pinned_settings,
        )
        is None
    )
    assert (
        synthetic_session_prep_context_ref(
            clerk_subject=doctor.clerk_id,
            record_ref="appointment_not-a-typeid",
            settings=pinned_settings,
        )
        is None
    )


def test_scope_fails_closed_after_fixed_data_tampering(monkeypatch):
    doctor, fixture = _fixture(monkeypatch, "tampered")
    monkeypatch.setattr(session_prep_scope, "is_staging", lambda: True)
    assert _resolve(doctor, fixture) == CONTEXT_SNAPSHOT_REF

    fixture.appointment.appointment_notes_preview = "tampered synthetic marker"
    fixture.appointment.save()

    assert _resolve(doctor, fixture) is None


def test_scope_fails_closed_after_fixture_deletion(monkeypatch):
    doctor, fixture = _fixture(monkeypatch, "deleted")
    monkeypatch.setattr(session_prep_scope, "is_staging", lambda: True)
    assert _resolve(doctor, fixture) == CONTEXT_SNAPSHOT_REF

    with get_session() as session:
        provenance = session.get(SyntheticPreviewAppointmentFixture, fixture.fixture.id)
        assert provenance is not None
        session.delete(provenance)
        session.commit()

    assert _resolve(doctor, fixture) is None


def test_scope_fails_closed_for_deleted_or_never_active_owner(monkeypatch):
    doctor, fixture = _fixture(monkeypatch, "inactive-owner")
    monkeypatch.setattr(session_prep_scope, "is_staging", lambda: True)
    assert _resolve(doctor, fixture) == CONTEXT_SNAPSHOT_REF

    doctor.last_active_at = None
    doctor.save()
    assert _resolve(doctor, fixture) is None

    doctor.last_active_at = datetime.now(UTC)
    doctor.deleted_at = datetime.now(UTC)
    doctor.save()
    assert _resolve(doctor, fixture) is None
