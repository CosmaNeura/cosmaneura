from datetime import UTC, datetime

import pytest

import app.services.authority as authority_service
import app.services.authority_provisioning as authority_provisioning
import app.services.session_prep_scope as session_prep_scope
import app.services.staging_synthetic_appointment as staging_fixture_service
from app.configuration.authority import AuthorityKernelSettings
from app.configuration.session_prep import SessionPrepSyntheticSettings
from app.services.authority_provisioning import provision_staging_provider_authority
from app.services.staging_synthetic_appointment import (
    StagingSyntheticAppointmentResult,
    ensure_staging_synthetic_appointment,
)

from app.models.appointment import Appointment
from app.models.authority import AuthorityPerson, AuthorityPersonBinding
from app.models.doctor import Doctor
from app.models.patient import Patient

ISSUER = "https://session-prep-test.clerk.invalid"
AUTHORITY_SETTINGS = AuthorityKernelSettings(
    enabled=True,
    token_ttl_seconds=900,
    token_refresh_after_seconds=300,
    clerk_issuer=ISSUER,
)


def _provision(monkeypatch: pytest.MonkeyPatch, suffix: str):
    monkeypatch.setattr(staging_fixture_service, "is_staging", lambda: True)
    monkeypatch.setattr(authority_provisioning, "is_staging", lambda: True)
    monkeypatch.setattr(session_prep_scope, "is_staging", lambda: True)
    doctor = Doctor(
        clerk_id=f"user_test_session_prep_authority_{suffix}",
        email=f"session-prep-authority-{suffix}@example.test",
        biography=None,
        last_active_at=datetime.now(UTC),
    ).save()
    fixture = ensure_staging_synthetic_appointment(
        doctor_id=doctor.id,
        doctor_email=doctor.email,
        operator_ref=f"operator_authority_{suffix}",
    )
    authority = provision_staging_provider_authority(
        doctor_id=doctor.id,
        doctor_email=doctor.email,
        appointment_id=fixture.appointment.id,
        operator_ref=f"operator_authority_binding_{suffix}",
        settings=AUTHORITY_SETTINGS,
    )
    return doctor, fixture, authority


def _configure_session_prep(
    monkeypatch: pytest.MonkeyPatch,
    *,
    doctor: Doctor,
    fixture: StagingSyntheticAppointmentResult,
    enabled: bool = True,
) -> None:
    monkeypatch.setattr(
        session_prep_scope,
        "SESSION_PREP_SYNTHETIC_SETTINGS",
        SessionPrepSyntheticSettings(
            enabled=enabled,
            context_snapshot_ref="context_synthetic_session_prep_v1",
            clerk_subject=doctor.clerk_id,
            appointment_id=str(fixture.appointment.id),
            fixture_id=str(fixture.fixture.id),
        ),
    )


def _token(doctor: Doctor, record_ref: str) -> str:
    return authority_service.exchange_context_token(
        clerk_issuer=ISSUER,
        clerk_subject=doctor.clerk_id,
        requested_surface="provider_assistant",
        record_ref=record_ref,
        settings=AUTHORITY_SETTINGS,
    ).context_token


def _capabilities(token: str) -> tuple[str, ...]:
    return authority_service.bootstrap_mount_context(
        mount_context_token=token,
        settings=AUTHORITY_SETTINGS,
    ).capabilities


def test_dynamic_capability_is_exactly_bound_and_static_provider_set_is_unchanged(
    monkeypatch,
):
    doctor, fixture, _authority = _provision(monkeypatch, "eligible")
    _configure_session_prep(monkeypatch, doctor=doctor, fixture=fixture)
    token = _token(doctor, str(fixture.appointment.id))

    assert "session-prep:read" not in authority_service.PROVIDER_CAPABILITIES
    assert "session-prep:read" in _capabilities(token)


def test_second_valid_synthetic_provider_gets_neither_ref_nor_capability(monkeypatch):
    first_doctor, first_fixture, _first_authority = _provision(
        monkeypatch,
        "pinned-provider",
    )
    second_doctor, second_fixture, _second_authority = _provision(
        monkeypatch,
        "other-valid-provider",
    )
    _configure_session_prep(
        monkeypatch,
        doctor=first_doctor,
        fixture=first_fixture,
    )

    assert (
        session_prep_scope.synthetic_session_prep_context_ref(
            clerk_subject=second_doctor.clerk_id,
            record_ref=str(second_fixture.appointment.id),
        )
        is None
    )
    second_token = _token(second_doctor, str(second_fixture.appointment.id))
    assert "session-prep:read" not in _capabilities(second_token)


def test_existing_mount_loses_dynamic_capability_when_feature_is_revoked(monkeypatch):
    doctor, fixture, _authority = _provision(monkeypatch, "revoked")
    _configure_session_prep(monkeypatch, doctor=doctor, fixture=fixture)
    token = _token(doctor, str(fixture.appointment.id))
    assert "session-prep:read" in _capabilities(token)

    _configure_session_prep(
        monkeypatch,
        doctor=doctor,
        fixture=fixture,
        enabled=False,
    )

    assert "session-prep:read" not in _capabilities(token)


def test_dynamic_capability_is_never_derived_outside_staging(monkeypatch):
    doctor, fixture, _authority = _provision(monkeypatch, "wrong-environment")
    _configure_session_prep(monkeypatch, doctor=doctor, fixture=fixture)
    token = _token(doctor, str(fixture.appointment.id))
    assert "session-prep:read" in _capabilities(token)

    monkeypatch.setattr(session_prep_scope, "is_staging", lambda: False)

    assert "session-prep:read" not in _capabilities(token)


def test_unprovisioned_record_switch_never_carries_synthetic_capability(monkeypatch):
    doctor, fixture, authority = _provision(monkeypatch, "record-switch")
    _configure_session_prep(monkeypatch, doctor=doctor, fixture=fixture)
    synthetic_token = _token(doctor, str(fixture.appointment.id))
    assert "session-prep:read" in _capabilities(synthetic_token)

    ordinary_patient = Patient(
        doctor_id=doctor.id,
        name="Synthetic ordinary capability-switch record",
        date_of_birth=datetime(2001, 1, 1),
    ).save()
    ordinary_appointment = Appointment(
        doctor_id=doctor.id,
        patient_id=ordinary_patient.id,
        appointment_date=datetime(2098, 1, 1),
        external_id=None,
    ).save()
    ordinary_person = AuthorityPerson(
        lifecycle_state="patient",
        status="active",
    ).save()
    AuthorityPersonBinding(
        person_id=ordinary_person.id,
        tenant_id=authority.tenant_id,
        binding_ref=str(ordinary_appointment.id),
        binding_kind="provider_record",
        status="active",
        verified_at=datetime.now(UTC),
    ).save()

    with pytest.raises(authority_service.AuthorityRequestDeniedError):
        _token(doctor, str(ordinary_appointment.id))


def test_existing_mount_loses_capability_after_fixture_tampering(monkeypatch):
    doctor, fixture, _authority = _provision(monkeypatch, "tampered")
    _configure_session_prep(monkeypatch, doctor=doctor, fixture=fixture)
    token = _token(doctor, str(fixture.appointment.id))
    assert "session-prep:read" in _capabilities(token)

    fixture.appointment.appointment_name = "tampered appointment"
    fixture.appointment.save()

    assert "session-prep:read" not in _capabilities(token)
