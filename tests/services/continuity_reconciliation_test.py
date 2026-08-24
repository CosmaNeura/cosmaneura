import hashlib
import json
import secrets
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from typeid import TypeID

import app.services.continuity_reconciliation as continuity
import app.services.pilot_access_audit as pilot_access_audit
from app.configuration.authority import AuthorityKernelSettings
from app.services.clerk_cohort import ClerkCohortIdentity
from app.services.continuity_reconciliation import (
    CONTINUITY_BASIS_OPERATIONAL_OMISSION,
    ContinuityIdentityInput,
    ContinuityManifest,
    ContinuityReconciliationError,
    apply_continuity_amendment,
    continuity_amendment_plan_sha256,
    plan_continuity_amendment,
    read_private_continuity_manifest,
)
from app.services.doctor_identity_rebind import (
    plan_doctor_identity_rebind,
    rebind_doctor_identity,
)
from app.services.pilot_access_audit import audit_pilot_access_state
from app.services.pilot_access_grandfather import (
    GrandfatherGrantInput,
    active_pilot_access_grant,
    grant_pilot_access_grants,
    pilot_access_grant_plan_sha256,
)

from activemodel.session_manager import get_session
from app.models.authority import (
    AuthorityActor,
    AuthorityActorExternalIdentity,
    AuthorityTenant,
    AuthorityTenantMembership,
)
from app.models.doctor import Doctor
from app.models.organization_access import (
    OrganizationRosterApproval,
    ProviderOrganization,
)
from app.models.pilot_access_continuity_amendment import (
    PilotAccessContinuityAmendment,
    PilotAccessContinuityAmendmentIdentity,
)
from app.models.pilot_access_grandfather_grant import PilotAccessGrandfatherGrant
from app.models.practice import Practice, PracticeMember
from sqlalchemy import text

ISSUER = "https://continuity-test.clerk.invalid"
SOURCE_REF = "change:continuity-omission-source"
OPERATOR_REF = "change:continuity-operator"
REASON = "Add reviewed operational-user omissions"


def _base_entries(label: str) -> list[GrandfatherGrantInput]:
    nonce = secrets.token_hex(4)
    return [
        GrandfatherGrantInput(
            clerk_id=f"user_base_{label}_{nonce}_{index:02d}",
            email=f"base-{label}-{nonce}-{index:02d}@example.com",
        )
        for index in range(47)
    ]


def _pair_digest(entries: list[GrandfatherGrantInput]) -> str:
    canonical = "".join(
        f"{entry.clerk_id}\t{entry.email}\n"
        for entry in sorted(entries, key=lambda entry: entry.clerk_id)
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _seed_base(entries: list[GrandfatherGrantInput] | None = None):
    entries = entries or _base_entries("seed")
    source_sha256 = _pair_digest(entries)
    plan_sha256 = pilot_access_grant_plan_sha256(
        entries,
        source_ref="clerk-export:base",
        source_sha256=source_sha256,
        operator_ref="change:base-import",
        reason="Preserve reviewed base cohort",
    )
    grant_pilot_access_grants(
        entries,
        source_ref="clerk-export:base",
        source_sha256=source_sha256,
        operator_ref="change:base-import",
        reason="Preserve reviewed base cohort",
        confirm_plan_sha256=plan_sha256,
        now=datetime(2026, 8, 23, 12, 0, tzinfo=UTC),
    )
    return entries


def _manifest(
    *entries: ContinuityIdentityInput,
    digest: str = "a" * 64,
) -> ContinuityManifest:
    return ContinuityManifest(
        environment="production",
        identities=tuple(entries),
        manifest_sha256=digest,
    )


def _identity(
    *,
    subject: str,
    email: str,
    doctor: Doctor | None = None,
    practice: Practice | None = None,
) -> ContinuityIdentityInput:
    return ContinuityIdentityInput(
        issuer=ISSUER,
        subject=subject,
        verified_primary_email=email,
        doctor_id=doctor.id if doctor else None,
        safe_prelogin=doctor is None,
        continuity_basis=CONTINUITY_BASIS_OPERATIONAL_OMISSION,
        practice_id=practice.id if practice else None,
    )


def _clerk_user(subject: str, email: str):
    return SimpleNamespace(
        id=subject,
        banned=False,
        locked=False,
        primary_email_address_id="idn_primary",
        email_addresses=[
            SimpleNamespace(
                id="idn_primary",
                email_address=email,
                verification=SimpleNamespace(status="verified"),
            )
        ],
    )


@pytest.fixture(autouse=True)
def configured_environment(monkeypatch):
    monkeypatch.setattr(continuity, "_current_environment", lambda: "production")
    monkeypatch.setattr(
        continuity,
        "AUTHORITY_KERNEL_SETTINGS",
        AuthorityKernelSettings(
            enabled=True,
            token_ttl_seconds=900,
            token_refresh_after_seconds=300,
            clerk_issuer=ISSUER,
        ),
    )


def _set_live_users(monkeypatch, identities: list[ContinuityIdentityInput]):
    users = {
        identity.subject: _clerk_user(identity.subject, identity.verified_primary_email)
        for identity in identities
    }
    monkeypatch.setattr(
        continuity,
        "clerk",
        SimpleNamespace(users=SimpleNamespace(get=lambda *, user_id: users[user_id])),
    )


def test_private_manifest_requires_exact_0600_and_explicit_prelogin(tmp_path):
    subject = "user_private_manifest_test"
    email = "private-manifest@example.com"
    path = tmp_path / "continuity.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "environment": "production",
                "identities": [
                    {
                        "issuer": ISSUER,
                        "subject": subject,
                        "verified_primary_email": email,
                        "doctor_id": None,
                        "safe_prelogin": True,
                        "continuity_basis": CONTINUITY_BASIS_OPERATIONAL_OMISSION,
                        "practice_id": None,
                    }
                ],
            }
        )
    )
    path.chmod(0o640)

    with pytest.raises(ContinuityReconciliationError, match="exactly 0600"):
        read_private_continuity_manifest(str(path))

    path.chmod(0o600)
    parsed = read_private_continuity_manifest(str(path))
    assert parsed.environment == "production"
    assert len(parsed.identities) == 1
    assert parsed.identities[0].safe_prelogin is True
    assert parsed.manifest_sha256 == hashlib.sha256(path.read_bytes()).hexdigest()


def test_private_manifest_rejects_duplicate_json_keys(tmp_path):
    path = tmp_path / "duplicate-key.json"
    path.write_text(
        """{
          "schema_version": 1,
          "environment": "production",
          "environment": "staging",
          "identities": []
        }"""
    )
    path.chmod(0o600)

    with pytest.raises(ContinuityReconciliationError, match="duplicate object keys"):
        read_private_continuity_manifest(str(path))


def test_amendment_adds_existing_doctor_without_fabricating_account_state(monkeypatch):
    _seed_base()
    subject = f"user_omission_{secrets.token_hex(6)}"
    email = f"omission-{secrets.token_hex(5)}@example.com"
    doctor = Doctor(clerk_id=subject, email=email, biography="").save()
    manifest = _manifest(_identity(subject=subject, email=email, doctor=doctor))
    _set_live_users(monkeypatch, list(manifest.identities))

    plan = plan_continuity_amendment(
        manifest,
        source_ref=SOURCE_REF,
        operator_ref=OPERATOR_REF,
        reason=REASON,
    )
    assert plan.account_count == 1
    assert plan.existing_doctor_count == 1
    assert plan.safe_prelogin_count == 0
    assert plan.already_applied is False

    result = apply_continuity_amendment(
        manifest,
        source_ref=SOURCE_REF,
        operator_ref=OPERATOR_REF,
        reason=REASON,
        confirm_plan_sha256=plan.plan_sha256,
        now=datetime(2026, 8, 24, 12, 0, tzinfo=UTC),
    )

    assert result.created_count == 1
    assert PilotAccessContinuityAmendment.count() == 1
    assert PilotAccessContinuityAmendmentIdentity.count() == 1
    assert PilotAccessGrandfatherGrant.count() == 48
    doctor.refresh()
    assert doctor.onboarding_completed_at is None
    assert PracticeMember.get(doctor_id=doctor.id) is None
    assert active_pilot_access_grant(doctor) is not None
    report = audit_pilot_access_state(
        expected_environment="production", expected_clerk_issuer=ISSUER
    )
    assert report.valid is True
    assert report.payload["base_count"] == 47
    assert report.payload["amendment_count"] == 1
    assert report.payload["amendment_account_count"] == 1
    assert report.payload["lineage_count"] == 48

    retry = apply_continuity_amendment(
        manifest,
        source_ref=SOURCE_REF,
        operator_ref=OPERATOR_REF,
        reason=REASON,
        confirm_plan_sha256=plan.plan_sha256,
    )
    assert retry.created_count == 0
    assert retry.unchanged_count == 1
    assert PilotAccessGrandfatherGrant.count() == 48


def test_safe_prelogin_amendment_does_not_create_doctor_or_tenant_state(monkeypatch):
    _seed_base()
    subject = f"user_prelogin_{secrets.token_hex(6)}"
    email = f"prelogin-{secrets.token_hex(5)}@example.com"
    manifest = _manifest(_identity(subject=subject, email=email))
    _set_live_users(monkeypatch, list(manifest.identities))
    plan = plan_continuity_amendment(
        manifest,
        source_ref=SOURCE_REF,
        operator_ref=OPERATOR_REF,
        reason=REASON,
    )

    apply_continuity_amendment(
        manifest,
        source_ref=SOURCE_REF,
        operator_ref=OPERATOR_REF,
        reason=REASON,
        confirm_plan_sha256=plan.plan_sha256,
    )

    assert Doctor.get(clerk_id=subject) is None
    grant = PilotAccessGrandfatherGrant.get(clerk_id=subject, revoked_at=None)
    assert grant is not None
    assert grant.doctor_id_snapshot is None
    assert grant.practice_id_snapshot is None
    doctor = Doctor(clerk_id=subject, email=email, biography="").save()
    assert active_pilot_access_grant(doctor) is not None


def test_amendment_refuses_a_live_primary_email_mismatch(monkeypatch):
    _seed_base()
    subject = f"user_live_mismatch_{secrets.token_hex(6)}"
    email = f"manifest-{secrets.token_hex(5)}@example.com"
    manifest = _manifest(_identity(subject=subject, email=email))
    monkeypatch.setattr(
        continuity,
        "clerk",
        SimpleNamespace(
            users=SimpleNamespace(
                get=lambda *, user_id: _clerk_user(
                    user_id,
                    f"different-{secrets.token_hex(5)}@example.com",
                )
            )
        ),
    )

    with pytest.raises(ContinuityReconciliationError, match="do not match"):
        plan_continuity_amendment(
            manifest,
            source_ref=SOURCE_REF,
            operator_ref=OPERATOR_REF,
            reason=REASON,
        )

    assert PilotAccessContinuityAmendment.count() == 0


def test_amendment_refuses_implicit_subject_rebind(monkeypatch):
    _seed_base()
    email = f"rebind-refusal-{secrets.token_hex(5)}@example.com"
    doctor = Doctor(
        clerk_id=f"user_old_{secrets.token_hex(6)}", email=email, biography=""
    ).save()
    replacement_subject = f"user_new_{secrets.token_hex(6)}"
    manifest = _manifest(
        _identity(subject=replacement_subject, email=email, doctor=doctor)
    )
    _set_live_users(monkeypatch, list(manifest.identities))

    with pytest.raises(
        ContinuityReconciliationError, match="Doctor identities do not match"
    ):
        plan_continuity_amendment(
            manifest,
            source_ref=SOURCE_REF,
            operator_ref=OPERATOR_REF,
            reason=REASON,
        )
    assert doctor.clerk_id != replacement_subject
    assert PilotAccessContinuityAmendment.count() == 0


def test_apply_rechecks_every_identity_and_keeps_a_conflicting_batch_atomic(
    monkeypatch,
):
    _seed_base()
    accepted_subject = f"user_atomic_ok_{secrets.token_hex(6)}"
    accepted_email = f"atomic-ok-{secrets.token_hex(5)}@example.com"
    accepted_doctor = Doctor(
        clerk_id=accepted_subject,
        email=accepted_email,
        biography="",
    ).save()
    retained_subject = f"user_atomic_old_{secrets.token_hex(6)}"
    conflicting_subject = f"user_atomic_new_{secrets.token_hex(6)}"
    conflicting_email = f"atomic-conflict-{secrets.token_hex(5)}@example.com"
    conflicting_doctor = Doctor(
        clerk_id=retained_subject,
        email=conflicting_email,
        biography="",
    ).save()
    manifest = _manifest(
        _identity(
            subject=accepted_subject,
            email=accepted_email,
            doctor=accepted_doctor,
        ),
        _identity(
            subject=conflicting_subject,
            email=conflicting_email,
            doctor=conflicting_doctor,
        ),
    )
    _set_live_users(monkeypatch, list(manifest.identities))
    plan_sha256 = continuity_amendment_plan_sha256(
        manifest,
        source_ref=SOURCE_REF,
        operator_ref=OPERATOR_REF,
        reason=REASON,
    )

    with pytest.raises(
        ContinuityReconciliationError,
        match="Doctor identities do not match",
    ):
        apply_continuity_amendment(
            manifest,
            source_ref=SOURCE_REF,
            operator_ref=OPERATOR_REF,
            reason=REASON,
            confirm_plan_sha256=plan_sha256,
        )

    assert PilotAccessContinuityAmendment.count() == 0
    assert PilotAccessContinuityAmendmentIdentity.count() == 0
    assert PilotAccessGrandfatherGrant.count() == 47


def test_amendment_refuses_revoked_or_conflicting_practice_state(monkeypatch):
    _seed_base()
    subject = f"user_revoked_member_{secrets.token_hex(6)}"
    email = f"revoked-member-{secrets.token_hex(5)}@example.com"
    doctor = Doctor(clerk_id=subject, email=email, biography="").save()
    practice = Practice(
        legal_name="Continuity Conflict Practice",
        join_code=f"C{secrets.token_hex(4).upper()[:7]}",
        email_domain="example.com",
    ).save()
    PracticeMember(
        practice_id=practice.id,
        doctor_id=doctor.id,
        role="workforce",
        access_revoked_at=datetime(2026, 8, 24, 10, 0, tzinfo=UTC),
        access_revoked_by_ref="change:offboard",
        access_revocation_reason="Prior offboarding",
        access_records_retain_until=datetime(2033, 8, 24, 10, 0, tzinfo=UTC),
    ).save()
    manifest = _manifest(
        _identity(subject=subject, email=email, doctor=doctor, practice=practice)
    )
    _set_live_users(monkeypatch, list(manifest.identities))

    with pytest.raises(ContinuityReconciliationError, match="prior access revocation"):
        plan_continuity_amendment(
            manifest,
            source_ref=SOURCE_REF,
            operator_ref=OPERATOR_REF,
            reason=REASON,
        )
    assert PilotAccessContinuityAmendment.count() == 0


@pytest.mark.parametrize(
    ("organization_status", "expires_at", "message"),
    [
        (
            "active",
            datetime(2026, 8, 23, 10, 0, tzinfo=UTC),
            "roster identities are inactive",
        ),
        ("suspended", None, "roster scopes are inactive"),
    ],
)
def test_amendment_refuses_expired_or_suspended_organization_authority(
    monkeypatch,
    organization_status,
    expires_at,
    message,
):
    _seed_base()
    subject = f"user_inactive_org_{secrets.token_hex(6)}"
    email = f"inactive-org-{secrets.token_hex(5)}@example.com"
    now = datetime(2026, 8, 24, 10, 0, tzinfo=UTC)
    organization = ProviderOrganization(
        legal_name="Inactive Organization",
        slug=f"inactive-{secrets.token_hex(4)}",
        email_domain="example.com",
        status=organization_status,
        access_code_salt="a" * 32,
        access_code_digest=secrets.token_hex(32),
        access_code_version=1,
        access_code_created_at=now,
    ).save()
    practice = Practice(
        legal_name="Inactive Organization Practice",
        join_code=f"O{secrets.token_hex(4).upper()[:7]}",
        email_domain=organization.email_domain,
        organization_id=organization.id,
    ).save()
    OrganizationRosterApproval(
        organization_id=organization.id,
        target_practice_id=practice.id,
        approved_email=email,
        approved_at=datetime(2026, 8, 22, 10, 0, tzinfo=UTC),
        approved_by_ref="change:roster-review",
        source_ref="controlled://roster/test",
        source_sha256="d" * 64,
        expires_at=expires_at,
    ).save()
    manifest = _manifest(_identity(subject=subject, email=email, practice=practice))
    _set_live_users(monkeypatch, list(manifest.identities))

    with pytest.raises(ContinuityReconciliationError, match=message):
        plan_continuity_amendment(
            manifest,
            source_ref=SOURCE_REF,
            operator_ref=OPERATOR_REF,
            reason=REASON,
        )


def test_amendment_refuses_revoked_or_ambiguous_authority_scope(monkeypatch):
    _seed_base()
    subject = f"user_inactive_authority_{secrets.token_hex(6)}"
    email = f"inactive-authority-{secrets.token_hex(5)}@example.com"
    actor = AuthorityActor(actor_kind="human", status="active").save()
    AuthorityActorExternalIdentity(
        actor_id=actor.id,
        issuer=ISSUER,
        subject=subject,
        verified_at=datetime(2026, 8, 22, 10, 0, tzinfo=UTC),
    ).save()
    tenant = AuthorityTenant(status="active").save()
    AuthorityTenantMembership(
        tenant_id=tenant.id,
        actor_id=actor.id,
        membership_kind="provider",
        status="revoked",
    ).save()
    manifest = _manifest(_identity(subject=subject, email=email))
    _set_live_users(monkeypatch, list(manifest.identities))

    with pytest.raises(ContinuityReconciliationError, match="authority scopes"):
        plan_continuity_amendment(
            manifest,
            source_ref=SOURCE_REF,
            operator_ref=OPERATOR_REF,
            reason=REASON,
        )


def test_audit_accepts_audited_rebind_without_redefining_base_cohort():
    entries = _base_entries("audited-rebind")
    original = entries[0]
    doctor = Doctor(
        clerk_id=original.clerk_id, email=original.email, biography=""
    ).save()
    practice = Practice(
        legal_name="Audited Rebind Practice",
        join_code=f"B{secrets.token_hex(4).upper()[:7]}",
        email_domain="example.com",
    ).save()
    PracticeMember(practice_id=practice.id, doctor_id=doctor.id, role="owner").save()
    _seed_base(entries)
    replacement = ClerkCohortIdentity(
        clerk_id=f"user_replacement_{secrets.token_hex(6)}",
        email=original.email,
    )
    args = {
        "doctor_id": doctor.id,
        "stale_clerk_id": original.clerk_id,
        "replacement": replacement,
        "operator_ref": "change:audited-rebind",
        "reason": "Recover exact retained provider identity",
    }
    plan = plan_doctor_identity_rebind(**args)
    rebind_doctor_identity(
        **args,
        confirmed_plan_sha256=plan.plan_sha256,
        now=datetime(2026, 8, 24, 13, 0, tzinfo=UTC),
    )

    report = audit_pilot_access_state()

    assert report.valid is True
    assert report.payload["base_count"] == 47
    assert report.payload["rebind_count"] == 1
    assert report.payload["lineage_count"] == 47
    assert report.payload["total_count"] == 48
    assert report.payload["state"] == "active"

    # Simulate corrupted historical receipt data to prove the aggregate audit
    # checks the full transition, not merely a matching digest. Production's
    # append-only trigger prevents this mutation; the test disables it only in
    # the same transaction as the controlled corruption.
    with get_session() as session:
        session.execute(
            text(
                "ALTER TABLE practice_access_audit "
                "DISABLE TRIGGER practice_access_audit_reject_mutation"
            )
        )
        session.execute(
            text(
                "UPDATE practice_access_audit "
                "SET operator_ref = 'change:tampered-receipt' "
                "WHERE evidence_sha256 = :plan_sha256"
            ),
            params={"plan_sha256": plan.plan_sha256},
        )
        session.execute(
            text(
                "ALTER TABLE practice_access_audit "
                "ENABLE TRIGGER practice_access_audit_reject_mutation"
            )
        )
        session.commit()
    corrupted = audit_pilot_access_state()
    assert corrupted.valid is False


def test_audit_accepts_audited_rebind_of_an_amendment(monkeypatch):
    _seed_base()
    original_subject = f"user_amendment_rebind_{secrets.token_hex(6)}"
    replacement_subject = f"user_amendment_replacement_{secrets.token_hex(6)}"
    email = f"amendment-rebind-{secrets.token_hex(5)}@example.com"
    doctor = Doctor(clerk_id=original_subject, email=email, biography="").save()
    practice = Practice(
        legal_name="Amendment Rebind Practice",
        join_code=f"R{secrets.token_hex(4).upper()[:7]}",
        email_domain="example.com",
    ).save()
    PracticeMember(practice_id=practice.id, doctor_id=doctor.id, role="owner").save()
    manifest = _manifest(
        _identity(
            subject=original_subject,
            email=email,
            doctor=doctor,
            practice=practice,
        )
    )
    _set_live_users(monkeypatch, list(manifest.identities))
    amendment_plan = plan_continuity_amendment(
        manifest,
        source_ref=SOURCE_REF,
        operator_ref=OPERATOR_REF,
        reason=REASON,
    )
    apply_continuity_amendment(
        manifest,
        source_ref=SOURCE_REF,
        operator_ref=OPERATOR_REF,
        reason=REASON,
        confirm_plan_sha256=amendment_plan.plan_sha256,
        now=datetime(2026, 8, 24, 12, 0, tzinfo=UTC),
    )

    replacement = ClerkCohortIdentity(
        clerk_id=replacement_subject,
        email=email,
    )
    rebind_args = {
        "doctor_id": doctor.id,
        "stale_clerk_id": original_subject,
        "replacement": replacement,
        "operator_ref": "change:amendment-rebind",
        "reason": "Recover amended provider identity",
    }
    rebind_plan = plan_doctor_identity_rebind(**rebind_args)
    rebind_doctor_identity(
        **rebind_args,
        confirmed_plan_sha256=rebind_plan.plan_sha256,
        now=datetime(2026, 8, 24, 13, 0, tzinfo=UTC),
    )

    report = audit_pilot_access_state(
        expected_environment="production",
        expected_clerk_issuer=ISSUER,
    )

    assert report.valid is True
    assert report.payload["base_count"] == 47
    assert report.payload["amendment_count"] == 1
    assert report.payload["amendment_account_count"] == 1
    assert report.payload["rebind_count"] == 1
    assert report.payload["lineage_count"] == 48
    assert report.payload["total_count"] == 49


def test_audit_fails_closed_when_amendment_issuer_configuration_is_missing(
    monkeypatch,
):
    _seed_base()
    subject = f"user_missing_issuer_{secrets.token_hex(6)}"
    email = f"missing-issuer-{secrets.token_hex(5)}@example.com"
    manifest = _manifest(_identity(subject=subject, email=email))
    _set_live_users(monkeypatch, list(manifest.identities))
    plan = plan_continuity_amendment(
        manifest,
        source_ref=SOURCE_REF,
        operator_ref=OPERATOR_REF,
        reason=REASON,
    )
    apply_continuity_amendment(
        manifest,
        source_ref=SOURCE_REF,
        operator_ref=OPERATOR_REF,
        reason=REASON,
        confirm_plan_sha256=plan.plan_sha256,
    )
    monkeypatch.setattr(pilot_access_audit, "python_environment", lambda: "production")
    monkeypatch.setattr(
        pilot_access_audit,
        "AUTHORITY_KERNEL_SETTINGS",
        AuthorityKernelSettings(
            enabled=True,
            token_ttl_seconds=900,
            token_refresh_after_seconds=300,
            clerk_issuer="",
        ),
    )

    report = audit_pilot_access_state()

    assert report.valid is False
    assert report.payload["integrity_error_count"] == 1


def test_manifest_rejects_doctor_and_prelogin_both_set(tmp_path):
    path = tmp_path / "ambiguous.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "environment": "production",
                "identities": [
                    {
                        "issuer": ISSUER,
                        "subject": "user_ambiguous_manifest",
                        "verified_primary_email": "ambiguous@example.com",
                        "doctor_id": str(TypeID("doctor")),
                        "safe_prelogin": True,
                        "continuity_basis": CONTINUITY_BASIS_OPERATIONAL_OMISSION,
                        "practice_id": None,
                    }
                ],
            }
        )
    )
    path.chmod(0o600)

    with pytest.raises(ContinuityReconciliationError, match="either Doctor ID"):
        read_private_continuity_manifest(str(path))
