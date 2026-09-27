"""Current real-record authorization, exercised only with synthetic fixtures."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.configuration.session_prep as configuration
import app.services.authority as authority
from app.configuration.session_prep import (
    SessionPrepProviderSettings,
    SessionPrepSyntheticSettings,
)
from app.routes.agent import agent_api_app, get_agent_authority_settings
from app.routes.dependencies.entra_service import (
    EntraServicePrincipal,
    authenticate_agent_api_request,
)

from activemodel.session_manager import get_session
from app.models.authority import (
    AuthorityActor,
    AuthorityActorExternalIdentity,
    AuthorityAuditEvent,
    AuthorityPersonBinding,
    AuthorityTenant,
    AuthorityTenantMembership,
    MountContextToken,
)
from app.models.practice import PracticeMember
from app.models.provider_record_authority import (
    ProviderPracticeAuthority,
    ProviderRecordAuthority,
)
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.factories import (
    AppointmentFactory,
    DoctorFactory,
    PatientFactory,
    save_practice_member,
)
from tests.routes.utils import provision_practice_in_force
from tests.services.session_prep_authority_test import (
    AUTHORITY_SETTINGS,
    _token,
)


@pytest.fixture
def scope(monkeypatch):
    doctor = DoctorFactory.build().save()
    practice = provision_practice_in_force(doctor)
    patient = PatientFactory.build(doctor_id=doctor.id).save()
    appointment = AppointmentFactory.build(
        doctor_id=doctor.id, patient_id=patient.id
    ).save()
    monkeypatch.setattr(
        configuration,
        "SESSION_PREP_PROVIDER_SETTINGS",
        SessionPrepProviderSettings(True, frozenset({doctor.clerk_id})),
    )
    return doctor, practice, patient, appointment


def _context(token):
    return authority.bootstrap_mount_context(
        mount_context_token=token, settings=AUTHORITY_SETTINGS
    )


def _record(appointment):
    return ProviderRecordAuthority.get(appointment_id=appointment.id)


def test_synthetic_and_real_cohort_overlap_is_a_configuration_error(scope, monkeypatch):
    doctor, _practice, _patient, appointment = scope
    monkeypatch.setattr(
        configuration,
        "SESSION_PREP_SYNTHETIC_SETTINGS",
        SessionPrepSyntheticSettings(
            True,
            "context_synthetic_test",
            doctor.clerk_id,
            str(appointment.id),
            "synthetic_preview_fixture_test",
        ),
    )
    with pytest.raises(ValueError, match="cohorts must be distinct"):
        configuration.validate_session_prep_cohorts()
    monkeypatch.setattr(
        configuration,
        "SESSION_PREP_PROVIDER_SETTINGS",
        SessionPrepProviderSettings(True, frozenset({"user_other_synthetic"})),
    )
    configuration.validate_session_prep_cohorts()


def test_verified_exchange_binds_actual_record_and_reuses_practice_and_person(scope):
    doctor, _practice, patient, appointment = scope
    token = _token(doctor, str(appointment.id))
    context = _context(token)
    assert "session-prep:read" in context.capabilities
    assert context.session_prep_context_ref.startswith("context_prep_")
    assert set(context.public_body()) == {
        "surface",
        "auth_tier",
        "capabilities",
        "tenant_id",
        "actor_id",
    }
    assert context.session_prep_context_ref not in repr(context)
    assert (
        _context(_token(doctor, str(appointment.id))).session_prep_context_ref
        == context.session_prep_context_ref
    )
    assert ProviderRecordAuthority.count() == 1
    second = AppointmentFactory.build(doctor_id=doctor.id, patient_id=patient.id).save()
    second_context = _context(_token(doctor, str(second.id)))
    assert second_context.person_id == context.person_id
    assert second_context.session_prep_context_ref != context.session_prep_context_ref
    assert ProviderPracticeAuthority.count() == 1
    assert (
        AuthorityAuditEvent.where(
            AuthorityAuditEvent.event_type == "authority.provider_record.bound"
        ).count()
        == 2
    )


def test_practice_move_replaces_prior_membership_without_ambiguous_actor(scope):
    doctor, _practice, patient, appointment = scope
    old_token = _token(doctor, str(appointment.id))
    old_context = _context(old_token)
    other_doctor = DoctorFactory.build().save()
    next_practice = provision_practice_in_force(other_doctor)
    member = PracticeMember.get(doctor_id=doctor.id)
    member.practice_id = next_practice.id
    save_practice_member(member)
    next_appointment = AppointmentFactory.build(
        doctor_id=doctor.id, patient_id=patient.id
    ).save()
    new_context = _context(_token(doctor, str(next_appointment.id)))
    assert new_context.actor_id == old_context.actor_id
    assert new_context.tenant_id != old_context.tenant_id
    memberships = AuthorityTenantMembership.where(
        AuthorityTenantMembership.actor_id == new_context.actor_id
    ).all()
    assert [
        (str(row.tenant_id), row.status)
        for row in memberships
        if row.status == "active"
    ] == [(new_context.tenant_id, "active")]
    assert (
        AuthorityTenantMembership.get(
            actor_id=old_context.actor_id, tenant_id=old_context.tenant_id
        ).status
        == "revoked"
    )
    with pytest.raises(authority.AuthorityRequestDeniedError):
        _context(old_token)
    assert (
        AuthorityAuditEvent.where(
            AuthorityAuditEvent.event_type == "authority.provider_membership.replaced"
        ).count()
        == 1
    )


def test_existing_staff_membership_is_not_silently_reassigned(scope):
    doctor, _practice, _patient, appointment = scope
    context = _context(_token(doctor, str(appointment.id)))
    other_tenant = AuthorityTenant(status="active").save()
    staff = AuthorityTenantMembership(
        tenant_id=other_tenant.id,
        actor_id=context.actor_id,
        membership_kind="staff",
        status="active",
    ).save()
    with pytest.raises(authority.AuthorityRequestDeniedError):
        _token(doctor, str(appointment.id))
    assert AuthorityTenantMembership.get(id=staff.id).status == "active"
    assert (
        AuthorityAuditEvent.where(
            AuthorityAuditEvent.event_type == "authority.provider_membership.replaced"
        ).count()
        == 0
    )


def test_unmapped_fixture_membership_is_not_retired_by_real_record_exchange(scope):
    doctor, _practice, _patient, appointment = scope
    _token(doctor, str(appointment.id))
    binding = AuthorityPersonBinding.one(id=_record(appointment).binding_id)
    fixture_tenant = AuthorityTenant(status="active").save()
    fixture_membership = AuthorityTenantMembership(
        actor_id=binding.actor_id,
        tenant_id=fixture_tenant.id,
        membership_kind="provider",
        status="active",
    ).save()
    with pytest.raises(authority.AuthorityRequestDeniedError):
        _token(doctor, str(appointment.id))
    assert AuthorityTenantMembership.get(id=fixture_membership.id).status == "active"
    assert AuthorityTenantMembership.where(
        AuthorityTenantMembership.actor_id == binding.actor_id,
        AuthorityTenantMembership.status == "active",
    ).count() == 2
    assert AuthorityAuditEvent.where(
        AuthorityAuditEvent.event_type == "authority.provider_membership.replaced"
    ).count() == 0


@pytest.mark.parametrize("same_practice", [False, True])
def test_other_selected_provider_cannot_exchange_or_reuse_record(
    scope, monkeypatch, same_practice
):
    doctor, practice, _patient, appointment = scope
    _token(doctor, str(appointment.id))
    outsider = DoctorFactory.build().save()
    provision_practice_in_force(outsider)
    if same_practice:
        member = PracticeMember.get(doctor_id=outsider.id)
        member.practice_id = practice.id
        save_practice_member(member)
    monkeypatch.setattr(
        configuration,
        "SESSION_PREP_PROVIDER_SETTINGS",
        SessionPrepProviderSettings(
            True, frozenset({doctor.clerk_id, outsider.clerk_id})
        ),
    )
    with pytest.raises(authority.AuthorityRequestDeniedError):
        _token(outsider, str(appointment.id))
    assert ProviderRecordAuthority.count() == 1


@pytest.mark.parametrize(
    "change",
    [
        "cohort",
        "doctor",
        "member",
        "seat",
        "practice",
        "patient",
        "appointment_doctor",
        "appointment_patient",
        "binding",
        "actor",
        "identity",
        "tenant",
    ],
)
def test_existing_mount_and_new_exchange_recheck_current_scope(
    scope, monkeypatch, change
):
    doctor, _practice, patient, appointment = scope
    token = _token(doctor, str(appointment.id))
    assert _context(token).session_prep_context_ref
    record = _record(appointment)
    binding = AuthorityPersonBinding.one(id=record.binding_id)
    member = PracticeMember.get(doctor_id=doctor.id)
    if change == "cohort":
        monkeypatch.setattr(
            configuration,
            "SESSION_PREP_PROVIDER_SETTINGS",
            SessionPrepProviderSettings(),
        )
    elif change == "doctor":
        doctor.deleted_at = datetime.now(UTC)
        doctor.save()
    elif change == "member":
        member.access_revoked_at = datetime.now(UTC)
        member.access_revoked_by_ref = str(doctor.id)
        member.access_revocation_reason = "Synthetic access revocation"
        member.access_records_retain_until = member.access_revoked_at + timedelta(
            days=2922
        )
        save_practice_member(member)
    elif change == "seat":
        member.seat_state = "inactive"
        save_practice_member(member)
    elif change == "practice":
        other = DoctorFactory.build().save()
        member.practice_id = provision_practice_in_force(other).id
        save_practice_member(member)
    elif change == "patient":
        patient.doctor_id = DoctorFactory.build().save().id
        patient.save()
    elif change == "appointment_doctor":
        appointment.doctor_id = DoctorFactory.build().save().id
        appointment.save()
    elif change == "appointment_patient":
        appointment.patient_id = PatientFactory.build(doctor_id=doctor.id).save().id
        appointment.save()
    elif change == "binding":
        binding.revoked_at = datetime.now(UTC)
        binding.save()
    elif change == "actor":
        actor = AuthorityActor.one(id=binding.actor_id)
        actor.status = "revoked"
        actor.save()
    elif change == "identity":
        identity = AuthorityActorExternalIdentity.get(actor_id=binding.actor_id)
        identity.revoked_at = datetime.now(UTC)
        identity.save()
    elif change == "tenant":
        tenant = AuthorityTenant.one(id=binding.tenant_id)
        tenant.status = "revoked"
        tenant.save()
    with pytest.raises(authority.AuthorityRequestDeniedError):
        _context(token)
    with pytest.raises(authority.AuthorityRequestDeniedError):
        _token(doctor, str(appointment.id))


def test_wrong_issuer_and_expired_token_are_denied(scope):
    doctor, _practice, _patient, appointment = scope
    with pytest.raises(authority.AuthorityRequestDeniedError):
        authority.exchange_context_token(
            clerk_issuer="https://wrong.clerk.invalid",
            clerk_subject=doctor.clerk_id,
            requested_surface="provider_assistant",
            record_ref=str(appointment.id),
            settings=AUTHORITY_SETTINGS,
        )
    assert ProviderRecordAuthority.count() == 0
    token = _token(doctor, str(appointment.id))
    stored = MountContextToken.get(session_id=_context(token).session_id)
    stored.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    stored.save()
    with pytest.raises(authority.ContextTokenExpiredError):
        _context(token)


@pytest.mark.parametrize(
    "operation",
    [
        "UPDATE provider_record_authority SET context_snapshot_ref = 'context_prep_changed'",
        "DELETE FROM provider_record_authority",
    ],
)
def test_record_scope_cannot_be_retargeted_or_removed_to_enable_fallback(
    scope, operation
):
    doctor, _practice, _patient, appointment = scope
    _token(doctor, str(appointment.id))
    with get_session() as session:
        with pytest.raises(DBAPIError):
            session.exec(text(operation))
            session.flush()
        session.rollback()


def test_service_resolution_returns_handle_only_in_authenticated_transport(scope):
    doctor, _practice, _patient, appointment = scope
    token = _token(doctor, str(appointment.id))
    app = FastAPI()
    app.include_router(agent_api_app)
    app.dependency_overrides[authenticate_agent_api_request] = (
        lambda: EntraServicePrincipal(client_id="11111111-1111-4111-8111-111111111111")
    )
    app.dependency_overrides[get_agent_authority_settings] = lambda: AUTHORITY_SETTINGS
    response = TestClient(app).post(
        "/service/v1/agent/mount-context/resolve",
        headers={
            "X-Cosmaneura-Mount-Context": token,
            "X-Cosmaneura-Client-Turn-ID": str(uuid4()),
        },
    )
    assert response.status_code == 200
    assert (
        response.headers["X-Cosmaneura-Session-Prep-Context"]
        == _context(token).session_prep_context_ref
    )
    assert "context_snapshot_ref" not in response.text
    assert str(appointment.id) not in response.text
    assert "no-store" in response.headers["cache-control"]
