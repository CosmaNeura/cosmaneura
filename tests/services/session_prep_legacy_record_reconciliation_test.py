"""Ordinary legacy authority repair through real verified-exchange services.

These are synthetic PostgreSQL domain cases, not real provider credentials or
live access proof. Existing fixtures/conftest and ownership checks stay active.
"""

from datetime import UTC, datetime

import pytest

from app.services import authority
from app.services.doctor_access_row_ownership import provider_record_access_row_scope

from activemodel.session_manager import SessionManager, get_session
from app.models.authority import (
    AuthorityActor,
    AuthorityActorExternalIdentity,
    AuthorityAuditEvent,
    AuthorityPerson,
    AuthorityPersonBinding,
    AuthorityPersonOrigin,
    AuthorityTenant,
    AuthorityTenantMembership,
)
from app.models.practice import PracticeMember
from app.models.provider_record_authority import (
    ProviderPracticeAuthority,
    ProviderRecordAuthority,
)
from app.models.synthetic_preview_appointment_fixture import (
    SyntheticPreviewAppointmentFixture,
)
from sqlalchemy import event

from tests.factories import DoctorFactory, save_practice_member
from tests.routes.utils import provision_practice_in_force
from tests.services.provider_record_authority_test import (
    scope as record_scope_fixture,
)
from tests.services.session_prep_authority_test import AUTHORITY_SETTINGS, _token

scope = record_scope_fixture


def _legacy(scope, invalid=None):
    doctor, practice, patient, appointment = scope
    now = datetime.now(UTC)
    actor = AuthorityActor(
        actor_kind="human", status="revoked" if invalid == "actor_revoked" else "active"
    ).save()
    identity = AuthorityActorExternalIdentity(
        actor_id=actor.id,
        issuer=AUTHORITY_SETTINGS.clerk_issuer,
        subject=doctor.clerk_id,
        verified_at=now,
        revoked_at=now if invalid == "identity_revoked" else None,
    ).save()
    tenant = AuthorityTenant(
        status="revoked" if invalid == "tenant" else "active"
    ).save()
    mapping = ProviderPracticeAuthority(
        practice_id=practice.id, tenant_id=tenant.id
    ).save()
    if invalid != "missing_member":
        AuthorityTenantMembership(
            actor_id=actor.id,
            tenant_id=tenant.id,
            membership_kind="staff" if invalid == "staff" else "provider",
            status="active",
        ).save()
    if invalid == "extra_tenant":
        AuthorityTenantMembership(
            actor_id=actor.id,
            tenant_id=AuthorityTenant(status="active").save().id,
            membership_kind="provider",
            status="active",
        ).save()
    person = AuthorityPerson(
        lifecycle_state="patient",
        status="revoked" if invalid == "person" else "active",
    ).save()
    origin = AuthorityPersonOrigin(
        person_id=person.id,
        origin="sync" if invalid == "import" else "provider_entry",
        source_ref="unrelated" if invalid == "origin_ref" else str(patient.id),
    ).save()
    if invalid == "mixed_origin":
        AuthorityPersonOrigin(
            person_id=person.id, origin="sync", source_ref="import"
        ).save()
    binding = AuthorityPersonBinding(
        person_id=person.id,
        tenant_id=tenant.id,
        actor_id=None if invalid == "actor_null" else actor.id,
        binding_ref=str(appointment.id),
        binding_kind="identified_session" if invalid == "kind" else "provider_record",
        status="revoked" if invalid == "binding" else "active",
        verified_at=None if invalid == "unverified" else now,
        revoked_at=now if invalid == "binding" else None,
    ).save()
    if invalid == "wrong_actor":
        binding.actor_id = AuthorityActor(actor_kind="human", status="active").save().id
        binding.save()
    if invalid in {"fixture", "full_fixture"}:
        SyntheticPreviewAppointmentFixture(
            doctor_id=doctor.id,
            patient_id=patient.id,
            appointment_id=appointment.id,
            provisioned_by="synthetic-test",
        ).save()
    if invalid in {"fixture_audit", "full_fixture"}:
        AuthorityAuditEvent(
            event_type="authority.provider_fixture.provisioned",
            outcome="accepted",
            occurred_at=now,
            actor_id=actor.id,
            tenant_id=tenant.id,
            person_id=person.id,
            resource_type="appointment",
            resource_ref=str(appointment.id),
        ).save()
    return actor, identity, tenant, mapping, person, origin, binding


def _unchanged(rows):
    return tuple(type(row).one(id=row.id).model_dump(mode="json") for row in rows)


def _context(token):
    return authority.bootstrap_mount_context(
        mount_context_token=token, settings=AUTHORITY_SETTINGS
    )


def _stale_mapped_member(actor):
    other = DoctorFactory.build(onboarding_completed_at=datetime.now(UTC)).save()
    prior_practice = provision_practice_in_force(other)
    tenant = AuthorityTenant(status="active").save()
    mapping = ProviderPracticeAuthority(
        practice_id=prior_practice.id, tenant_id=tenant.id
    ).save()
    member = AuthorityTenantMembership(
        actor_id=actor.id,
        tenant_id=tenant.id,
        membership_kind="provider",
        status="active",
    ).save()
    return mapping, member


def test_ordinary_legacy_exchange_retires_stale_mapped_membership_and_reuses_scope(
    scope,
):
    doctor, _practice, _patient, appointment = scope
    rows = _legacy(scope)
    before = _unchanged(rows)
    stale_mapping, stale_member = _stale_mapped_member(rows[0])
    mapping_before = _unchanged((stale_mapping,))
    current_member = AuthorityTenantMembership.one(
        actor_id=rows[0].id, tenant_id=rows[2].id
    )
    current_before = _unchanged((current_member,))

    context = _context(_token(doctor, str(appointment.id)))

    assert "session-prep:read" in context.capabilities
    assert context.session_prep_context_ref is not None
    assert AuthorityTenantMembership.one(id=stale_member.id).status == "revoked"
    assert _unchanged((current_member,)) == current_before
    assert _unchanged(rows) == before
    assert _unchanged((stale_mapping,)) == mapping_before
    active_members = AuthorityTenantMembership.where(
        AuthorityTenantMembership.actor_id == rows[0].id,
        AuthorityTenantMembership.status == "active",
    ).all()
    assert [row.id for row in active_members] == [current_member.id]
    assert (
        ProviderRecordAuthority.one(appointment_id=appointment.id).binding_id
        == rows[-1].id
    )
    assert (
        _context(_token(doctor, str(appointment.id))).session_prep_context_ref
        == context.session_prep_context_ref
    )
    assert ProviderRecordAuthority.count() == 1
    for event_type in (
        "authority.provider_membership.replaced",
        "authority.provider_record.bound",
    ):
        assert (
            AuthorityAuditEvent.where(
                AuthorityAuditEvent.event_type == event_type
            ).count()
            == 1
        )


def test_invalid_legacy_exchange_rolls_back_stale_membership_cleanup_in_savepoint(
    scope,
):
    doctor, _practice, _patient, appointment = scope
    rows = _legacy(scope, "import")
    _mapping, stale_member = _stale_mapped_member(rows[0])
    before = _unchanged((*rows, stale_member))
    statements = []
    engine = SessionManager.get_instance().get_engine()

    def capture(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        # Catch inside the managed transaction: these assertions must observe
        # the optional exchange savepoint rollback, not an outer error rollback.
        with get_session() as session:
            with pytest.raises(authority.AuthorityRequestDeniedError):
                authority.exchange_context_token(
                    clerk_issuer=AUTHORITY_SETTINGS.clerk_issuer,
                    clerk_subject=doctor.clerk_id,
                    requested_surface="provider_assistant",
                    record_ref=str(appointment.id),
                    session=session,
                    settings=AUTHORITY_SETTINGS,
                )
            assert session.is_active
            assert (
                session.get(
                    AuthorityTenantMembership, stale_member.id, populate_existing=True
                ).status
                == "active"
            )
            assert _unchanged((*rows, stale_member)) == before
            assert ProviderRecordAuthority.count() == 0
            for event_type in (
                "authority.provider_membership.replaced",
                "authority.provider_record.bound",
            ):
                assert (
                    AuthorityAuditEvent.where(
                        AuthorityAuditEvent.event_type == event_type
                    ).count()
                    == 0
                )
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    savepoint = next(i for i, s in enumerate(statements) if s.startswith("SAVEPOINT "))
    retired = next(
        i
        for i, s in enumerate(statements)
        if s.startswith("UPDATE authority_tenant_membership")
    )
    rolled_back = next(
        i for i, s in enumerate(statements) if s.startswith("ROLLBACK TO SAVEPOINT ")
    )
    assert savepoint < retired < rolled_back
    assert _unchanged((*rows, stale_member)) == before


def test_owned_ordinary_legacy_exchange_adds_only_scope_and_reuses_it(scope):
    doctor, practice, patient, appointment = scope
    rows = _legacy(scope)
    before = _unchanged(rows)
    counts = (AuthorityTenantMembership.count(), AuthorityPersonBinding.count())
    assert ProviderRecordAuthority.count() == 0
    # Exercise the actual optimized owner-row scope too: its validator None
    # shortcut cannot substitute for the new explicit ordinary discriminator.
    with get_session() as session, provider_record_access_row_scope(session):
        result = authority.exchange_context_token(
            clerk_issuer=AUTHORITY_SETTINGS.clerk_issuer,
            clerk_subject=doctor.clerk_id,
            requested_surface="provider_assistant",
            record_ref=str(appointment.id),
            session=session,
            settings=AUTHORITY_SETTINGS,
        )
    context = _context(result.context_token)
    assert "session-prep:read" in context.capabilities
    assert context.session_prep_context_ref is not None
    stored = ProviderRecordAuthority.one(appointment_id=appointment.id)
    assert stored.binding_id == rows[-1].id
    assert stored.practice_authority_id == rows[3].id
    assert stored.patient_id == patient.id
    assert stored.doctor_id == doctor.id
    assert _unchanged(rows) == before
    assert (AuthorityTenantMembership.count(), AuthorityPersonBinding.count()) == counts
    assert ProviderPracticeAuthority.one(practice_id=practice.id).id == rows[3].id
    assert (
        _context(_token(doctor, str(appointment.id))).session_prep_context_ref
        == context.session_prep_context_ref
    )
    assert ProviderRecordAuthority.count() == 1
    assert (
        AuthorityAuditEvent.where(
            AuthorityAuditEvent.event_type == "authority.provider_record.bound"
        ).count()
        == 1
    )


def test_reconciliation_executes_exact_binding_lock_before_scope_insert(scope):
    doctor, _practice, _patient, appointment = scope
    _legacy(scope)
    statements = []
    engine = SessionManager.get_instance().get_engine()

    def capture(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        _token(doctor, str(appointment.id))
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    locked = next(
        i
        for i, s in enumerate(statements)
        if "FOR UPDATE OF authority_person_binding" in s
    )
    inserted = next(
        i
        for i, s in enumerate(statements)
        if s.startswith("INSERT INTO provider_record_authority")
    )
    assert locked < inserted
    assert "authority_person_binding.id =" in statements[locked]
    fenced = statements[locked:inserted]
    assert any("FOR UPDATE OF appointment" in s for s in fenced)
    assert any("FOR UPDATE OF patient" in s for s in fenced)
    assert ProviderRecordAuthority.count() == 1


@pytest.mark.parametrize(
    "invalid",
    [
        "actor_null",
        "wrong_actor",
        "actor_revoked",
        "identity_revoked",
        "binding",
        "unverified",
        "kind",
        "person",
        "origin_ref",
        "import",
        "mixed_origin",
        "tenant",
        "missing_member",
        "staff",
        "extra_tenant",
        "fixture",
        "fixture_audit",
        "full_fixture",
    ],
)
def test_nonordinary_or_partial_legacy_bindings_never_gain_session_prep(scope, invalid):
    doctor, _practice, _patient, appointment = scope
    rows = _legacy(scope, invalid)
    before = _unchanged(rows)
    try:
        context = _context(_token(doctor, str(appointment.id)))
    except authority.AuthorityRequestDeniedError:
        pass
    else:
        assert context.session_prep_context_ref is None
        assert "session-prep:read" not in context.capabilities
    assert ProviderRecordAuthority.count() == 0
    assert _unchanged(rows) == before
    assert (
        AuthorityAuditEvent.where(
            AuthorityAuditEvent.event_type == "authority.provider_record.bound"
        ).count()
        == 0
    )


@pytest.mark.parametrize("same_practice", [True, False])
def test_other_provider_cannot_reconcile_an_owned_legacy_visit(scope, same_practice):
    _doctor, practice, _patient, appointment = scope
    rows = _legacy(scope)
    before = _unchanged(rows)
    other = DoctorFactory.build(onboarding_completed_at=datetime.now(UTC)).save()
    if same_practice:
        save_practice_member(
            PracticeMember(
                practice_id=practice.id,
                doctor_id=other.id,
                role="workforce",
                attested_at=datetime.now(UTC),
                attestation_name="Synthetic provider",
            )
        )
    else:
        provision_practice_in_force(other)
    with pytest.raises(authority.AuthorityRequestDeniedError):
        _token(other, str(appointment.id))
    assert ProviderRecordAuthority.count() == 0
    assert _unchanged(rows) == before


def test_current_practice_change_cannot_retarget_legacy_binding(scope):
    doctor, _practice, _patient, appointment = scope
    rows = _legacy(scope)
    before = _unchanged(rows)
    other = DoctorFactory.build(onboarding_completed_at=datetime.now(UTC)).save()
    current_practice = provision_practice_in_force(other)
    member = PracticeMember.one(doctor_id=doctor.id)
    member.practice_id = current_practice.id
    member.role = "workforce"
    member.attested_at = datetime.now(UTC)
    member.attestation_name = "Synthetic provider"
    save_practice_member(member)

    with pytest.raises(authority.AuthorityRequestDeniedError):
        _token(doctor, str(appointment.id))

    assert ProviderRecordAuthority.count() == 0
    assert _unchanged(rows) == before
