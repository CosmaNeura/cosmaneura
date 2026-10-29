import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from app.services.organization_access import (
    RosterApprovalInput,
    claim_roster_access,
    configure_provider_organization,
    import_roster_approvals,
    revoke_roster_access,
)
from app.services.session_prep_provider_access import (
    resolve_session_prep_provider_access,
)

from app.models.doctor import Doctor
from app.models.organization_access import OrganizationRosterApproval
from app.models.pilot_access_grandfather_grant import PilotAccessGrandfatherGrant
from app.models.practice import Practice, PracticeMember

from tests.factories import save_practice_member
from tests.routes.utils import provision_practice_in_force


def _doctor(label: str, *, onboarded: bool = True) -> Doctor:
    nonce = secrets.token_hex(4)
    return Doctor(
        clerk_id=f"user_session_prep_access_{label}_{nonce}",
        email=f"session-prep-access-{label}-{nonce}@example.com",
        biography="",
        onboarding_completed_at=datetime.now(UTC) if onboarded else None,
    ).save()


def _pilot_grant(
    doctor: Doctor, *, practice: Practice | None = None
) -> PilotAccessGrandfatherGrant:
    now = datetime.now(UTC)
    assert doctor.email is not None
    email = doctor.email.lower()
    return PilotAccessGrandfatherGrant(
        clerk_id=doctor.clerk_id,
        doctor_id_snapshot=doctor.id,
        practice_id_snapshot=practice.id if practice is not None else None,
        email_snapshot=email,
        email_sha256=hashlib.sha256(email.encode()).hexdigest(),
        source_ref="test:session-prep-provider-access",
        source_sha256="a" * 64,
        grant_plan_sha256="b" * 64,
        granted_by_ref="test-suite",
        grant_reason="Prove pilot access cannot replace Session Prep prerequisites",
        granted_at=now - timedelta(minutes=1),
    ).save()


def _rostered_provider(
    label: str,
    *,
    now: datetime,
    expires_at: datetime | None = None,
) -> tuple[Doctor, Practice]:
    domain = f"session-prep-{label}-{secrets.token_hex(4)}.example.com"
    doctor = Doctor(
        clerk_id=f"user_session_prep_roster_{label}_{secrets.token_hex(4)}",
        email=f"provider@{domain}",
        biography="",
        onboarding_completed_at=now - timedelta(minutes=1),
    ).save()
    practice = Practice(
        legal_name=f"Session Prep {label} MCD Practice",
        join_code=f"R{secrets.token_hex(4).upper()[:7]}",
        email_domain=domain,
        origin_channel="mcd",
        is_solo=False,
        negotiated_baa_on_file=True,
        negotiated_baa_effective_date=now - timedelta(days=1),
        negotiated_baa_reference=f"executed-baa:{domain}",
        billing_exempt=True,
    ).save()
    organization, access_code, _ = configure_provider_organization(
        practice,
        legal_name=f"Session Prep {label} MCD Organization",
        slug=f"session-prep-{label}-{secrets.token_hex(5)}",
        email_domain=domain,
        operator_ref="test-session-prep-roster",
        reason="Verify MCD roster eligibility for Session Prep",
        now=now,
    )
    if expires_at is None:
        import_roster_approvals(
            practice,
            [RosterApprovalInput(email=doctor.email or "")],
            operator_ref="test-session-prep-roster",
            reason="Authorize a synthetic MCD provider",
            source_ref=f"test://session-prep-roster/{label}",
            source_sha256=hashlib.sha256(label.encode()).hexdigest(),
            now=now,
        )
    else:
        OrganizationRosterApproval(
            organization_id=organization.id,
            target_practice_id=practice.id,
            approved_email=doctor.email or "",
            approved_at=now,
            approved_by_ref="test-session-prep-roster",
            source_ref=f"test://session-prep-roster/{label}",
            source_sha256=hashlib.sha256(label.encode()).hexdigest(),
            expires_at=expires_at,
        ).save()
    claimed_practice, _ = claim_roster_access(
        doctor,
        supplied_code=access_code,
        now=now + timedelta(seconds=1),
    )
    assert claimed_practice.id == practice.id
    return doctor, practice


def test_current_onboarded_provider_with_full_access_is_allowed() -> None:
    doctor = _doctor("allowed")
    practice = provision_practice_in_force(doctor)

    decision = resolve_session_prep_provider_access(doctor)

    assert decision.allowed is True
    assert decision.denial_reason is None
    assert decision.practice is not None
    assert decision.practice.id == practice.id


def test_current_mcd_roster_provider_is_allowed() -> None:
    now = datetime.now(UTC)
    doctor, practice = _rostered_provider("allowed", now=now)

    decision = resolve_session_prep_provider_access(
        doctor,
        at=now + timedelta(seconds=2),
    )

    assert decision.allowed is True
    assert decision.denial_reason is None
    assert decision.practice is not None
    assert decision.practice.id == practice.id


def test_expired_mcd_roster_provider_is_denied() -> None:
    now = datetime.now(UTC)
    doctor, _ = _rostered_provider(
        "expired",
        now=now,
        expires_at=now + timedelta(minutes=5),
    )

    decision = resolve_session_prep_provider_access(
        doctor,
        at=now + timedelta(minutes=6),
    )

    assert decision.allowed is False
    assert decision.denial_reason == "practice_access_not_authorized"


def test_revoked_mcd_roster_provider_is_denied() -> None:
    now = datetime.now(UTC)
    doctor, practice = _rostered_provider("revoked", now=now)
    revoke_roster_access(
        practice,
        doctor.email or "",
        operator_ref="test-session-prep-roster",
        reason="Verify revoked MCD roster access stays closed",
        now=now + timedelta(minutes=1),
    )

    decision = resolve_session_prep_provider_access(
        doctor,
        at=now + timedelta(minutes=2),
    )

    assert decision.allowed is False
    assert decision.denial_reason == "provider_unavailable"


def test_completed_onboarding_is_required_even_with_current_practice_access() -> None:
    doctor = _doctor("incomplete", onboarded=False)
    provision_practice_in_force(doctor)

    decision = resolve_session_prep_provider_access(doctor)

    assert decision.allowed is False
    assert decision.denial_reason == "onboarding_incomplete"


def test_pilot_grant_cannot_replace_practice_membership() -> None:
    doctor = _doctor("pilot-no-membership")
    _pilot_grant(doctor)

    decision = resolve_session_prep_provider_access(doctor)

    assert decision.allowed is False
    assert decision.denial_reason == "practice_membership_required"


def test_pilot_grant_cannot_replace_baa_or_authorized_membership() -> None:
    doctor = _doctor("pilot-no-baa")
    practice = Practice(
        legal_name="Session Prep no-BAA practice",
        join_code=f"S{secrets.token_hex(4).upper()[:7]}",
        email_domain="example.com",
        billing_exempt=True,
    ).save()
    save_practice_member(
        PracticeMember(
            practice_id=practice.id,
            doctor_id=doctor.id,
            role="workforce",
            attested_at=datetime.now(UTC),
            attestation_name="Test Clinician",
        )
    )
    _pilot_grant(doctor, practice=practice)

    decision = resolve_session_prep_provider_access(doctor)

    assert decision.allowed is False
    assert decision.denial_reason == "baa_not_in_force"


def test_revoked_membership_is_denied() -> None:
    doctor = _doctor("revoked")
    provision_practice_in_force(doctor)
    member = PracticeMember.one(doctor_id=doctor.id)
    now = datetime.now(UTC)
    member.access_revoked_at = now
    member.access_revoked_by_ref = "test-suite"
    member.access_revocation_reason = "Test revocation"
    member.access_records_retain_until = now + timedelta(days=2922)
    save_practice_member(member)

    decision = resolve_session_prep_provider_access(doctor)

    assert decision.allowed is False
    assert decision.denial_reason == "practice_membership_inactive"


def test_revoked_pilot_tombstone_remains_a_hard_denial() -> None:
    doctor = _doctor("revoked-pilot")
    provision_practice_in_force(doctor)
    grant = _pilot_grant(doctor)
    now = datetime.now(UTC)
    grant.revoked_at = now
    grant.revoked_by_ref = "test-suite"
    grant.revocation_reason = "Test revocation"
    grant.revocation_plan_sha256 = "c" * 64
    grant.retain_until = now + timedelta(days=2922)
    grant.save()

    decision = resolve_session_prep_provider_access(doctor)

    assert decision.allowed is False
    assert decision.denial_reason == "pilot_grant_revoked"


def test_active_replacement_clears_historical_pilot_tombstone() -> None:
    doctor = _doctor("replacement-pilot")
    provision_practice_in_force(doctor)
    revoked = _pilot_grant(doctor)
    now = datetime.now(UTC)
    revoked.revoked_at = now
    revoked.revoked_by_ref = "test-suite"
    revoked.revocation_reason = "Test replacement"
    revoked.revocation_plan_sha256 = "c" * 64
    revoked.retain_until = now + timedelta(days=2922)
    revoked.save()
    _pilot_grant(doctor)

    decision = resolve_session_prep_provider_access(doctor)

    assert decision.allowed is True
    assert decision.denial_reason is None


def test_pilot_grant_cannot_replace_current_practice_entitlement() -> None:
    doctor = _doctor("pilot-unentitled")
    practice = provision_practice_in_force(doctor)
    practice.billing_exempt = False
    practice.billing_exempt_until = None
    practice.trial_ends_at = None
    practice.subscription_status = None
    practice.save()
    _pilot_grant(doctor, practice=practice)

    decision = resolve_session_prep_provider_access(doctor)

    assert decision.allowed is False
    assert decision.denial_reason == "entitlement_not_in_force"
