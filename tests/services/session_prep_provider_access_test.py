import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from app.services.session_prep_provider_access import (
    resolve_session_prep_provider_access,
)

from app.models.doctor import Doctor
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


def test_current_onboarded_provider_with_full_access_is_allowed() -> None:
    doctor = _doctor("allowed")
    practice = provision_practice_in_force(doctor)

    decision = resolve_session_prep_provider_access(doctor)

    assert decision.allowed is True
    assert decision.denial_reason is None
    assert decision.practice is not None
    assert decision.practice.id == practice.id


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
