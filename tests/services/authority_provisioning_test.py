from datetime import UTC, datetime

import pytest

import app.services.authority_provisioning as authority_provisioning
import app.services.staging_synthetic_appointment as staging_fixture_service
from app.configuration.authority import AuthorityKernelSettings
from app.services.authority_provisioning import (
    AuthorityProvisioningError,
    provision_staging_provider_authority,
)
from app.services.staging_synthetic_appointment import (
    ensure_staging_synthetic_appointment,
)

from app.models.authority import (
    AuthorityActor,
    AuthorityActorExternalIdentity,
    AuthorityAuditEvent,
    AuthorityPerson,
    AuthorityPersonBinding,
    AuthorityTenant,
    AuthorityTenantMembership,
)
from app.models.doctor import Doctor

TEST_AUTHORITY_SETTINGS = AuthorityKernelSettings(
    enabled=False,
    token_ttl_seconds=900,
    token_refresh_after_seconds=300,
    clerk_issuer="https://test.clerk.invalid",
)


def _doctor(suffix: str) -> Doctor:
    return Doctor(
        clerk_id=f"user_test_authority_{suffix}",
        email=f"authority-{suffix}@example.test",
        biography=None,
        last_active_at=datetime.now(UTC),
    ).save()


def _fixture(monkeypatch: pytest.MonkeyPatch, doctor: Doctor):
    monkeypatch.setattr(staging_fixture_service, "is_staging", lambda: True)
    monkeypatch.setattr(authority_provisioning, "is_staging", lambda: True)
    return ensure_staging_synthetic_appointment(
        doctor_id=doctor.id,
        doctor_email=doctor.email,
        operator_ref="operator_authority_fixture",
    )


def test_provider_authority_provisioning_is_explicit_and_idempotent(monkeypatch):
    doctor = _doctor("owner")
    fixture = _fixture(monkeypatch, doctor)

    first = provision_staging_provider_authority(
        doctor_id=doctor.id,
        doctor_email=doctor.email,
        appointment_id=fixture.appointment.id,
        operator_ref="operator_authority_v7",
        settings=TEST_AUTHORITY_SETTINGS,
    )
    second = provision_staging_provider_authority(
        doctor_id=doctor.id,
        doctor_email=doctor.email,
        appointment_id=fixture.appointment.id,
        operator_ref="operator_authority_v7_retry",
        settings=TEST_AUTHORITY_SETTINGS,
    )

    assert first.created is True
    assert second.created is False
    assert second.actor_id == first.actor_id
    assert second.tenant_id == first.tenant_id
    assert second.person_id == first.person_id
    assert second.binding_id == first.binding_id
    assert second.binding_ref == str(fixture.appointment.id)
    assert second.binding_kind == "provider_record"
    assert second.binding_actor_id is None
    assert AuthorityActor.count() == 1
    assert AuthorityActorExternalIdentity.count() == 1
    assert AuthorityActorExternalIdentity.first().issuer == "https://test.clerk.invalid"
    assert AuthorityTenant.count() == 1
    assert AuthorityTenantMembership.count() == 1
    assert AuthorityPerson.count() == 1
    assert AuthorityPersonBinding.count() == 1
    assert (
        AuthorityAuditEvent.where(
            AuthorityAuditEvent.event_type
            == "authority.provider_fixture.provisioned"
        ).count()
        == 1
    )


def test_additional_pilot_provider_can_join_explicit_existing_tenant(monkeypatch):
    first_doctor = _doctor("first")
    first_fixture = _fixture(monkeypatch, first_doctor)
    first = provision_staging_provider_authority(
        doctor_id=first_doctor.id,
        doctor_email=first_doctor.email,
        appointment_id=first_fixture.appointment.id,
        operator_ref="operator_authority_first",
        settings=TEST_AUTHORITY_SETTINGS,
    )
    second_doctor = _doctor("second")
    second_fixture = _fixture(monkeypatch, second_doctor)

    second = provision_staging_provider_authority(
        doctor_id=second_doctor.id,
        doctor_email=second_doctor.email,
        appointment_id=second_fixture.appointment.id,
        operator_ref="operator_authority_second",
        tenant_id=first.tenant_id,
        settings=TEST_AUTHORITY_SETTINGS,
    )

    assert second.tenant_id == first.tenant_id
    assert second.actor_id != first.actor_id
    assert AuthorityTenant.count() == 1
    assert AuthorityTenantMembership.count() == 2


def test_same_subject_from_another_issuer_never_cross_binds(monkeypatch):
    doctor = _doctor("cross-issuer")
    fixture = _fixture(monkeypatch, doctor)
    other_actor = AuthorityActor(actor_kind="human", status="active").save()
    AuthorityActorExternalIdentity(
        actor_id=other_actor.id,
        issuer="https://other-clerk.test.invalid",
        subject=doctor.clerk_id,
        verified_at=datetime.now(UTC),
    ).save()

    result = provision_staging_provider_authority(
        doctor_id=doctor.id,
        doctor_email=doctor.email,
        appointment_id=fixture.appointment.id,
        operator_ref="operator_authority_cross_issuer",
        settings=TEST_AUTHORITY_SETTINGS,
    )

    assert result.actor_id != other_actor.id
    exact_identity = AuthorityActorExternalIdentity.where(
        AuthorityActorExternalIdentity.issuer
        == TEST_AUTHORITY_SETTINGS.clerk_issuer,
        AuthorityActorExternalIdentity.subject == doctor.clerk_id,
    ).one()
    assert exact_identity.actor_id == result.actor_id
    assert AuthorityActorExternalIdentity.count() == 2


def test_provider_authority_refuses_without_staging_and_synthetic_fixture(
    monkeypatch,
):
    doctor = _doctor("refused")
    monkeypatch.setattr(authority_provisioning, "is_staging", lambda: False)

    with pytest.raises(AuthorityProvisioningError, match="restricted to staging"):
        provision_staging_provider_authority(
            doctor_id=doctor.id,
            doctor_email=doctor.email,
            appointment_id="appointment_01j00000000000000000000000",  # type: ignore[arg-type]
            operator_ref="operator_authority_refused",
            settings=TEST_AUTHORITY_SETTINGS,
        )
