import hashlib
import secrets
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast

import pytest

import app.services.doctor_identity_rebind as identity_rebind
from app.configuration.authority import AuthorityKernelSettings
from app.services.clerk_cohort import ClerkCohortIdentity
from app.services.doctor_identity_rebind import (
    DoctorIdentityRebindError,
    plan_doctor_identity_rebind,
    rebind_doctor_identity,
    resolve_live_rebind_identity,
)

from app.models.authority import (
    AuthorityActor,
    AuthorityActorExternalIdentity,
    AuthorityAuditEvent,
    AuthorityTenant,
    AuthorityTenantMembership,
)
from app.models.doctor import Doctor
from app.models.patient import Patient
from app.models.pilot_access_grandfather_grant import PilotAccessGrandfatherGrant
from app.models.practice import Practice, PracticeMember
from app.models.practice_access_audit import PracticeAccessAction, PracticeAccessAudit
from app.models.superadmin_capability import (
    SuperadminCapabilityEnvironment,
    SuperadminCapabilityGrant,
    SuperadminCapabilityKind,
)
from app.models.view_as import ViewAsAuditEvent, ViewAsSession

from tests.factories import save_practice_member

TEST_CLERK_ISSUER = "https://identity-rebind-test.clerk.example"


@pytest.fixture(autouse=True)
def _configured_identity_rebind_provenance(monkeypatch):
    monkeypatch.setattr(identity_rebind, "python_environment", lambda: "test")
    monkeypatch.setattr(
        identity_rebind,
        "AUTHORITY_KERNEL_SETTINGS",
        AuthorityKernelSettings(
            enabled=True,
            token_ttl_seconds=900,
            token_refresh_after_seconds=300,
            clerk_issuer=TEST_CLERK_ISSUER,
        ),
    )


def test_live_rebind_requires_same_verified_email_and_absent_predecessor(monkeypatch):
    stale = "user_stale_live_test"
    replacement = "user_replacement_live_test"
    email = "identity-live@example.com"

    def get(*, user_id: str):
        if user_id == stale:
            return None
        return SimpleNamespace(
            id=replacement,
            banned=False,
            locked=False,
            primary_email_address_id="idn_primary",
            email_addresses=[
                SimpleNamespace(
                    id="idn_primary",
                    email_address=email.upper(),
                    verification=SimpleNamespace(status="verified"),
                )
            ],
        )

    monkeypatch.setattr(
        identity_rebind,
        "clerk",
        SimpleNamespace(users=SimpleNamespace(get=get)),
    )

    assert resolve_live_rebind_identity(
        stale_clerk_id=stale,
        replacement_clerk_id=replacement,
        confirmed_email=email,
    ) == ClerkCohortIdentity(clerk_id=replacement, email=email)


def _scope(*, provisional: bool = True, provisional_email: str | None = None):
    suffix = secrets.token_hex(5)
    email = f"identity-{suffix}@example.com"
    stale_subject = f"user_stale_{suffix}"
    replacement_subject = f"user_replacement_{suffix}"
    doctor = Doctor(
        clerk_id=stale_subject,
        email=email,
        biography="",
    ).save()
    practice = Practice(
        legal_name="Identity Recovery Practice",
        join_code=f"I{secrets.token_hex(4).upper()[:7]}",
        email_domain="example.com",
        is_solo=True,
        origin_channel="direct",
        pastoral_pack=False,
    ).save()
    save_practice_member(
        PracticeMember(
            practice_id=practice.id,
            doctor_id=doctor.id,
            role="owner",
        )
    )
    replacement_shell = None
    if provisional:
        replacement_shell = Doctor(
            clerk_id=replacement_subject,
            email=cast(str, provisional_email),
            biography=None,
        ).save()
    replacement = ClerkCohortIdentity(
        clerk_id=replacement_subject,
        email=email,
    )
    args = {
        "doctor_id": doctor.id,
        "stale_clerk_id": stale_subject,
        "replacement": replacement,
        "operator_ref": "change:identity-recovery-test",
        "reason": "Recover the provider after verified sign-in replacement",
    }
    return doctor, practice, replacement_shell, args


def _superadmin_grant(
    doctor: Doctor,
    *,
    subject: str | None = None,
) -> SuperadminCapabilityGrant:
    assert doctor.email is not None
    bound_subject = subject or doctor.clerk_id
    return SuperadminCapabilityGrant(
        environment=SuperadminCapabilityEnvironment.staging,
        capability_kind=SuperadminCapabilityKind.view_as,
        clerk_issuer=TEST_CLERK_ISSUER,
        clerk_subject=bound_subject,
        doctor_id=doctor.id,
        issuer_sha256=hashlib.sha256(TEST_CLERK_ISSUER.encode()).hexdigest(),
        subject_sha256=hashlib.sha256(bound_subject.encode()).hexdigest(),
        verified_primary_email_sha256=hashlib.sha256(
            doctor.email.strip().lower().encode()
        ).hexdigest(),
        source_ref="controlled:identity-rebind-test",
        source_sha256="a" * 64,
        grant_plan_sha256="b" * 64,
        granted_by_ref="change:identity-rebind-test",
        grant_reason_code="reviewed_test_grant",
        granted_at=datetime(2026, 8, 23, 12, 0, tzinfo=UTC),
    ).save()


def _revoke_superadmin_grant(grant: SuperadminCapabilityGrant) -> None:
    revoked_at = datetime(2026, 8, 23, 13, 0, tzinfo=UTC)
    grant.revoked_at = revoked_at
    grant.revoked_by_ref = "change:identity-rebind-test"
    grant.revocation_reason_code = "reviewed_test_revocation"
    grant.revocation_plan_sha256 = "c" * 64
    grant.retain_until = datetime(2034, 8, 23, 13, 0, tzinfo=UTC)
    grant.save()


def test_rebind_preserves_provider_records_and_tombstones_empty_shell():
    doctor, practice, provisional, args = _scope()
    assert provisional is not None

    plan = plan_doctor_identity_rebind(**args)
    result = rebind_doctor_identity(
        **args,
        confirmed_plan_sha256=plan.plan_sha256,
        now=datetime(2026, 8, 24, 12, 0, tzinfo=UTC),
    )

    assert result.changed is True
    doctor.refresh()
    provisional.refresh()
    assert doctor.clerk_id == args["replacement"].clerk_id
    assert doctor.email == args["replacement"].email
    assert provisional.deleted_at is not None
    assert provisional.clerk_id.startswith("user_rebound_")
    assert Doctor.get(id=provisional.id) is not None
    audit = PracticeAccessAudit.get(
        practice_id=practice.id,
        action=PracticeAccessAction.doctor_identity_rebound.value,
    )
    assert audit is not None
    assert audit.evidence_sha256 == plan.plan_sha256

    retry_plan = plan_doctor_identity_rebind(**args)
    retry = rebind_doctor_identity(
        **args,
        confirmed_plan_sha256=retry_plan.plan_sha256,
    )
    assert retry.changed is False
    assert retry.plan_sha256 == plan.plan_sha256


def test_rebind_recovers_a_stateless_case_variant_email_shell():
    doctor, _, provisional, args = _scope(provisional_email="IDENTITY-PLACEHOLDER")
    assert provisional is not None
    provisional.email = args["replacement"].email.upper()
    provisional.save()

    plan = plan_doctor_identity_rebind(**args)
    result = rebind_doctor_identity(
        **args,
        confirmed_plan_sha256=plan.plan_sha256,
        now=datetime(2026, 8, 24, 12, 0, tzinfo=UTC),
    )

    doctor.refresh()
    provisional.refresh()
    assert result.changed is True
    assert doctor.clerk_id == args["replacement"].clerk_id
    assert provisional.deleted_at is not None
    assert provisional.email is None


def test_rebind_refuses_replacement_shell_with_records():
    _, _, provisional, args = _scope()
    assert provisional is not None
    Patient(
        doctor_id=provisional.id,
        name="Synthetic Test Patient",
        date_of_birth=datetime(2000, 1, 1, tzinfo=UTC),
    ).save()

    with pytest.raises(
        DoctorIdentityRebindError,
        match="replacement sign-in already owns application state",
    ):
        plan_doctor_identity_rebind(**args)


def test_rebind_refuses_active_superadmin_capability_without_side_effects():
    doctor, _, provisional, args = _scope()
    plan = plan_doctor_identity_rebind(**args)
    grant = _superadmin_grant(doctor)
    authority_audits_before = AuthorityAuditEvent.count()
    practice_audits_before = PracticeAccessAudit.count()
    view_as_audits_before = ViewAsAuditEvent.count()

    with pytest.raises(DoctorIdentityRebindError, match="capabilities must be revoked"):
        rebind_doctor_identity(
            **args,
            confirmed_plan_sha256=plan.plan_sha256,
        )

    doctor.refresh()
    grant.refresh()
    assert doctor.clerk_id == args["stale_clerk_id"]
    assert provisional is not None
    provisional.refresh()
    assert provisional.deleted_at is None
    assert grant.revoked_at is None
    assert AuthorityAuditEvent.count() == authority_audits_before
    assert PracticeAccessAudit.count() == practice_audits_before
    assert ViewAsAuditEvent.count() == view_as_audits_before


def test_rebind_refuses_active_view_as_session_after_capability_revocation():
    doctor, _, provisional, args = _scope()
    plan = plan_doctor_identity_rebind(**args)
    grant = _superadmin_grant(doctor)
    _revoke_superadmin_grant(grant)
    target = Doctor(
        clerk_id=f"user_rebind_view_target_{secrets.token_hex(5)}",
        email=f"rebind-view-target-{secrets.token_hex(5)}@example.com",
        biography="",
    ).save()
    started_at = datetime(2026, 8, 23, 14, 0, tzinfo=UTC)
    active_session = ViewAsSession(
        environment=SuperadminCapabilityEnvironment.staging.value,
        capability_grant_id=grant.id,
        actor_doctor_id=doctor.id,
        target_doctor_id=target.id,
        actor_issuer_sha256=hashlib.sha256(TEST_CLERK_ISSUER.encode()).hexdigest(),
        actor_subject_sha256=hashlib.sha256(doctor.clerk_id.encode()).hexdigest(),
        clerk_session_sha256="d" * 64,
        browser_nonce_sha256="e" * 64,
        started_at=started_at,
        absolute_expires_at=datetime(2026, 8, 23, 14, 15, tzinfo=UTC),
    ).save()
    authority_audits_before = AuthorityAuditEvent.count()
    practice_audits_before = PracticeAccessAudit.count()
    view_as_audits_before = ViewAsAuditEvent.count()

    with pytest.raises(DoctorIdentityRebindError, match="sessions must be ended"):
        rebind_doctor_identity(
            **args,
            confirmed_plan_sha256=plan.plan_sha256,
        )

    doctor.refresh()
    active_session.refresh()
    assert doctor.clerk_id == args["stale_clerk_id"]
    assert provisional is not None
    provisional.refresh()
    assert provisional.deleted_at is None
    assert active_session.ended_at is None
    assert AuthorityAuditEvent.count() == authority_audits_before
    assert PracticeAccessAudit.count() == practice_audits_before
    assert ViewAsAuditEvent.count() == view_as_audits_before


def test_rebind_moves_active_pilot_grant_without_erasing_history():
    doctor, practice, _, args = _scope()
    email = args["replacement"].email
    old_grant = PilotAccessGrandfatherGrant(
        clerk_id=args["stale_clerk_id"],
        doctor_id_snapshot=doctor.id,
        practice_id_snapshot=practice.id,
        email_snapshot=email,
        email_sha256=hashlib.sha256(email.encode()).hexdigest(),
        source_ref="controlled://pilot-export/test",
        source_sha256="a" * 64,
        grant_plan_sha256="b" * 64,
        granted_by_ref="change:pilot-import-test",
        grant_reason="Preserve approved pilot access",
        granted_at=datetime(2026, 8, 23, 12, 0, tzinfo=UTC),
    ).save()

    plan = plan_doctor_identity_rebind(**args)
    assert plan.pilot_grant_rebound is True
    rebind_doctor_identity(
        **args,
        confirmed_plan_sha256=plan.plan_sha256,
        now=datetime(2026, 8, 24, 12, 0, tzinfo=UTC),
    )

    old_grant.refresh()
    assert old_grant.revoked_at is not None
    assert old_grant.retain_until is not None
    replacement_grant = PilotAccessGrandfatherGrant.get(
        clerk_id=args["replacement"].clerk_id,
        revoked_at=None,
    )
    assert replacement_grant is not None
    assert replacement_grant.doctor_id_snapshot == doctor.id
    assert replacement_grant.email_snapshot == email


def test_rebind_never_reauthorizes_a_previously_revoked_replacement_subject():
    doctor, _, _, args = _scope()
    replacement = args["replacement"]
    revoked_at = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)
    PilotAccessGrandfatherGrant(
        clerk_id=replacement.clerk_id,
        doctor_id_snapshot=None,
        practice_id_snapshot=None,
        email_snapshot=replacement.email,
        email_sha256=hashlib.sha256(replacement.email.encode()).hexdigest(),
        source_ref="controlled://pilot-export/revoked-test",
        source_sha256="a" * 64,
        grant_plan_sha256="b" * 64,
        granted_by_ref="change:pilot-import-test",
        grant_reason="Preserve approved pilot access",
        granted_at=datetime(2026, 8, 22, 12, 0, tzinfo=UTC),
        revoked_at=revoked_at,
        revoked_by_ref="change:pilot-offboarding-test",
        revocation_reason="Explicit prior offboarding",
        revocation_plan_sha256="c" * 64,
        retain_until=revoked_at.replace(year=revoked_at.year + 7),
    ).save()
    PilotAccessGrandfatherGrant(
        clerk_id=args["stale_clerk_id"],
        doctor_id_snapshot=doctor.id,
        practice_id_snapshot=None,
        email_snapshot=replacement.email,
        email_sha256=hashlib.sha256(replacement.email.encode()).hexdigest(),
        source_ref="controlled://pilot-export/active-test",
        source_sha256="d" * 64,
        grant_plan_sha256="e" * 64,
        granted_by_ref="change:pilot-import-test",
        grant_reason="Preserve approved pilot access",
        granted_at=datetime(2026, 8, 22, 12, 0, tzinfo=UTC),
    ).save()

    with pytest.raises(
        DoctorIdentityRebindError,
        match="pilot authorization has conflicting state",
    ):
        plan_doctor_identity_rebind(**args)


def test_rebind_refuses_mixed_clerk_issuer_without_mutation(monkeypatch):
    doctor, _, _, args = _scope()
    issuer = "https://test-clerk.example"
    monkeypatch.setattr(
        identity_rebind,
        "AUTHORITY_KERNEL_SETTINGS",
        AuthorityKernelSettings(
            enabled=True,
            token_ttl_seconds=900,
            token_refresh_after_seconds=300,
            clerk_issuer=issuer,
        ),
    )
    tenant = AuthorityTenant(status="active").save()
    actor = AuthorityActor(actor_kind="human", status="active").save()
    membership = AuthorityTenantMembership(
        tenant_id=tenant.id,
        actor_id=actor.id,
        membership_kind="provider",
        status="active",
    ).save()
    configured_identity = AuthorityActorExternalIdentity(
        actor_id=actor.id,
        issuer=issuer,
        subject=args["stale_clerk_id"],
        verified_at=datetime(2026, 8, 22, 12, 0, tzinfo=UTC),
    ).save()
    unrelated_actor = AuthorityActor(actor_kind="human", status="active").save()
    unrelated_identity = AuthorityActorExternalIdentity(
        actor_id=unrelated_actor.id,
        issuer="https://other-idp.example",
        subject=args["stale_clerk_id"],
        verified_at=datetime(2026, 8, 22, 12, 0, tzinfo=UTC),
    ).save()

    audit_count = AuthorityAuditEvent.count()
    practice_audit_count = PracticeAccessAudit.count()
    with pytest.raises(DoctorIdentityRebindError, match="unexpected Clerk issuer"):
        plan_doctor_identity_rebind(**args)

    doctor.refresh()
    configured_identity.refresh()
    unrelated_identity.refresh()
    assert doctor.clerk_id == args["stale_clerk_id"]
    assert configured_identity.revoked_at is None
    assert unrelated_identity.revoked_at is None
    assert AuthorityAuditEvent.count() == audit_count
    assert PracticeAccessAudit.count() == practice_audit_count
    assert membership.tenant_id == tenant.id


def test_rebind_moves_one_exact_configured_authority_identity(monkeypatch):
    _, _, _, args = _scope()
    issuer = "https://test-clerk.example"
    monkeypatch.setattr(
        identity_rebind,
        "AUTHORITY_KERNEL_SETTINGS",
        AuthorityKernelSettings(
            enabled=True,
            token_ttl_seconds=900,
            token_refresh_after_seconds=300,
            clerk_issuer=issuer,
        ),
    )
    tenant = AuthorityTenant(status="active").save()
    actor = AuthorityActor(actor_kind="human", status="active").save()
    AuthorityTenantMembership(
        tenant_id=tenant.id,
        actor_id=actor.id,
        membership_kind="provider",
        status="active",
    ).save()
    configured_identity = AuthorityActorExternalIdentity(
        actor_id=actor.id,
        issuer=issuer,
        subject=args["stale_clerk_id"],
        verified_at=datetime(2026, 8, 22, 12, 0, tzinfo=UTC),
    ).save()

    plan = plan_doctor_identity_rebind(**args)
    assert plan.authority_identity_count == 1
    rebind_doctor_identity(
        **args,
        confirmed_plan_sha256=plan.plan_sha256,
        now=datetime(2026, 8, 24, 12, 0, tzinfo=UTC),
    )

    configured_identity.refresh()
    assert configured_identity.revoked_at is not None
    replacement_identity = AuthorityActorExternalIdentity.get(
        issuer=issuer,
        subject=args["replacement"].clerk_id,
    )
    assert replacement_identity is not None
    assert replacement_identity.actor_id == actor.id


def test_rebind_refuses_wrong_issuer_only_without_mutation(monkeypatch):
    doctor, _, _, args = _scope()
    issuer = "https://test-clerk.example"
    monkeypatch.setattr(
        identity_rebind,
        "AUTHORITY_KERNEL_SETTINGS",
        AuthorityKernelSettings(
            enabled=True,
            token_ttl_seconds=900,
            token_refresh_after_seconds=300,
            clerk_issuer=issuer,
        ),
    )
    wrong_actor = AuthorityActor(actor_kind="human", status="active").save()
    wrong_identity = AuthorityActorExternalIdentity(
        actor_id=wrong_actor.id,
        issuer="https://unexpected-issuer.example",
        subject=args["stale_clerk_id"],
        verified_at=datetime(2026, 8, 22, 12, 0, tzinfo=UTC),
    ).save()

    with pytest.raises(DoctorIdentityRebindError, match="unexpected Clerk issuer"):
        plan_doctor_identity_rebind(**args)

    doctor.refresh()
    wrong_identity.refresh()
    assert doctor.clerk_id == args["stale_clerk_id"]
    assert wrong_identity.revoked_at is None
    assert AuthorityAuditEvent.count() == 0
    assert PracticeAccessAudit.count() == 0


def test_rebind_rejects_wrong_confirmation_digest_without_mutation():
    doctor, _, provisional, args = _scope()

    with pytest.raises(
        DoctorIdentityRebindError,
        match="confirmed plan digest",
    ):
        rebind_doctor_identity(
            **args,
            confirmed_plan_sha256="0" * 64,
        )

    doctor.refresh()
    assert doctor.clerk_id == args["stale_clerk_id"]
    assert provisional is not None
    provisional.refresh()
    assert provisional.deleted_at is None


def test_rebind_requires_environment_bound_clerk_issuer(monkeypatch):
    _, _, _, args = _scope()
    monkeypatch.setattr(
        identity_rebind,
        "AUTHORITY_KERNEL_SETTINGS",
        AuthorityKernelSettings(
            enabled=True,
            token_ttl_seconds=900,
            token_refresh_after_seconds=300,
            clerk_issuer="",
        ),
    )

    with pytest.raises(
        DoctorIdentityRebindError,
        match="configured Clerk issuer is unavailable",
    ):
        plan_doctor_identity_rebind(**args)


def test_rebind_plan_is_bound_to_environment_and_issuer(monkeypatch):
    _, _, _, args = _scope()
    test_plan = plan_doctor_identity_rebind(**args)

    staging_issuer = "https://identity-rebind-staging.clerk.example"
    monkeypatch.setattr(identity_rebind, "python_environment", lambda: "staging")
    monkeypatch.setattr(
        identity_rebind,
        "AUTHORITY_KERNEL_SETTINGS",
        AuthorityKernelSettings(
            enabled=True,
            token_ttl_seconds=900,
            token_refresh_after_seconds=300,
            clerk_issuer=staging_issuer,
        ),
    )
    staging_plan = plan_doctor_identity_rebind(**args)

    assert test_plan.environment == "test"
    assert (
        test_plan.clerk_issuer_sha256
        == hashlib.sha256(TEST_CLERK_ISSUER.encode()).hexdigest()
    )
    assert staging_plan.environment == "staging"
    assert (
        staging_plan.clerk_issuer_sha256
        == hashlib.sha256(staging_issuer.encode()).hexdigest()
    )
    assert staging_plan.plan_sha256 != test_plan.plan_sha256


def test_rebind_preserves_no_practice_continuity_account_and_is_idempotent():
    suffix = secrets.token_hex(5)
    email = f"identity-no-practice-{suffix}@example.com"
    stale_subject = f"user_stale_no_practice_{suffix}"
    replacement_subject = f"user_replacement_no_practice_{suffix}"
    doctor = Doctor(clerk_id=stale_subject, email=email, biography="").save()
    old_grant = PilotAccessGrandfatherGrant(
        clerk_id=stale_subject,
        doctor_id_snapshot=doctor.id,
        practice_id_snapshot=None,
        email_snapshot=email,
        email_sha256=hashlib.sha256(email.encode()).hexdigest(),
        source_ref="controlled://pilot-export/no-practice-test",
        source_sha256="a" * 64,
        grant_plan_sha256="b" * 64,
        granted_by_ref="change:pilot-import-test",
        grant_reason="Preserve approved pilot access",
        granted_at=datetime(2026, 8, 23, 12, 0, tzinfo=UTC),
    ).save()
    args = {
        "doctor_id": doctor.id,
        "stale_clerk_id": stale_subject,
        "replacement": ClerkCohortIdentity(
            clerk_id=replacement_subject,
            email=email,
        ),
        "operator_ref": "change:no-practice-identity-recovery",
        "reason": "Recover exact continuity account without practice membership",
    }

    plan = plan_doctor_identity_rebind(**args)
    assert plan.practice_id is None
    assert plan.pilot_grant_rebound is True
    applied_at = datetime(2026, 8, 24, 14, 0, tzinfo=UTC)
    result = rebind_doctor_identity(
        **args,
        confirmed_plan_sha256=plan.plan_sha256,
        now=applied_at,
    )

    assert result.changed is True
    doctor.refresh()
    old_grant.refresh()
    assert doctor.clerk_id == replacement_subject
    assert old_grant.revoked_at == applied_at
    successor = PilotAccessGrandfatherGrant.get(
        clerk_id=replacement_subject,
        revoked_at=None,
    )
    assert successor is not None
    assert successor.doctor_id_snapshot == doctor.id
    assert successor.practice_id_snapshot is None
    assert (
        PracticeAccessAudit.get(
            action=PracticeAccessAction.doctor_identity_rebound.value,
            evidence_sha256=plan.plan_sha256,
        )
        is None
    )
    receipt = AuthorityAuditEvent.get(
        event_type=identity_rebind.IDENTITY_REBIND_EVENT_TYPE,
        request_digest=plan.plan_sha256,
    )
    assert receipt is not None
    assert receipt.intent == "test"
    assert receipt.content_sha256 == plan.clerk_issuer_sha256

    retry_plan = plan_doctor_identity_rebind(**args)
    retry = rebind_doctor_identity(
        **args,
        confirmed_plan_sha256=retry_plan.plan_sha256,
    )
    assert retry.changed is False
    assert retry.plan_sha256 == plan.plan_sha256


def test_rebind_refuses_no_practice_account_without_continuity_grant():
    suffix = secrets.token_hex(5)
    email = f"identity-unprovisioned-{suffix}@example.com"
    doctor = Doctor(
        clerk_id=f"user_stale_unprovisioned_{suffix}",
        email=email,
        biography="",
    ).save()

    with pytest.raises(
        DoctorIdentityRebindError,
        match="neither active practice access nor continuity authorization",
    ):
        plan_doctor_identity_rebind(
            doctor_id=doctor.id,
            stale_clerk_id=doctor.clerk_id,
            replacement=ClerkCohortIdentity(
                clerk_id=f"user_replacement_unprovisioned_{suffix}",
                email=email,
            ),
            operator_ref="change:unprovisioned-identity-recovery",
            reason="Refuse an unprovisioned identity recovery attempt",
        )
