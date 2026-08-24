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
    AuthorityTenant,
    AuthorityTenantMembership,
)
from app.models.doctor import Doctor
from app.models.patient import Patient
from app.models.pilot_access_grandfather_grant import PilotAccessGrandfatherGrant
from app.models.practice import Practice, PracticeMember
from app.models.practice_access_audit import PracticeAccessAction, PracticeAccessAudit


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


def _scope(*, provisional: bool = True):
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
    PracticeMember(
        practice_id=practice.id,
        doctor_id=doctor.id,
        role="owner",
    ).save()
    replacement_shell = None
    if provisional:
        replacement_shell = Doctor(
            clerk_id=replacement_subject,
            email=cast(str, None),
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


def test_rebind_only_moves_exact_clerk_issuer_with_valid_provider_tenant(monkeypatch):
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

    plan = plan_doctor_identity_rebind(**args)

    assert plan.authority_identity_count == 1
    rebind_doctor_identity(
        **args,
        confirmed_plan_sha256=plan.plan_sha256,
        now=datetime(2026, 8, 24, 12, 0, tzinfo=UTC),
    )
    configured_identity.refresh()
    unrelated_identity.refresh()
    assert configured_identity.revoked_at is not None
    assert unrelated_identity.revoked_at is None
    replacement_identity = AuthorityActorExternalIdentity.get(
        issuer=issuer,
        subject=args["replacement"].clerk_id,
    )
    assert replacement_identity is not None
    assert replacement_identity.actor_id == actor.id
    assert membership.tenant_id == tenant.id


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
