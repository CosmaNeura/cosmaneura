import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import pytest

from app.services.doctor_access import resolve_doctor_access

from app.models.baa import BAA_CURRENT_REFERENCE
from app.models.doctor import Doctor
from app.models.pilot_access_grandfather_grant import PilotAccessGrandfatherGrant
from app.models.practice import Practice, PracticeMember


def _doctor(label: str) -> Doctor:
    nonce = secrets.token_hex(4)
    return Doctor(
        clerk_id=f"user_access_{label}_{nonce}",
        email=f"access-{label}-{nonce}@example.com",
        biography="",
    ).save()


def _grant(
    doctor: Doctor,
    *,
    practice: Practice | None = None,
    revoked: bool = False,
    email_sha256: str | None = None,
) -> PilotAccessGrandfatherGrant:
    now = datetime.now(timezone.utc)
    email = doctor.email.lower()
    return PilotAccessGrandfatherGrant(
        clerk_id=doctor.clerk_id,
        doctor_id_snapshot=doctor.id,
        practice_id_snapshot=practice.id if practice else None,
        email_snapshot=email,
        email_sha256=email_sha256 or hashlib.sha256(email.encode()).hexdigest(),
        source_ref="test:doctor-access",
        source_sha256="a" * 64,
        grant_plan_sha256="b" * 64,
        granted_by_ref="test-suite",
        grant_reason="Test exact-account access",
        granted_at=now - timedelta(minutes=1),
        revoked_at=now if revoked else None,
        revoked_by_ref="test-suite" if revoked else None,
        revocation_reason="Test offboarding" if revoked else None,
        revocation_plan_sha256="c" * 64 if revoked else None,
        retain_until=now + timedelta(days=365 * 8) if revoked else None,
    ).save()


def _practice(
    doctor: Doctor, *, attested: bool = True
) -> tuple[Practice, PracticeMember]:
    practice = Practice(
        legal_name="Access Test Practice",
        join_code=f"A{secrets.token_hex(4).upper()[:7]}",
        email_domain="example.com",
    ).save()
    member = PracticeMember(
        practice_id=practice.id,
        doctor_id=doctor.id,
        role="workforce",
        attested_at=datetime.now(timezone.utc) if attested else None,
        attestation_name="Test Clinician" if attested else None,
    ).save()
    return practice, member


def test_active_pilot_grant_allows_access_without_membership_or_mutation():
    doctor = _doctor("grant")
    doctor.onboarding_completed_at = datetime.now(timezone.utc)
    doctor.save()
    _grant(doctor)

    decision = resolve_doctor_access(doctor)

    assert decision.allowed is True
    assert decision.authorization_basis == "pilot_grant"
    assert decision.denial_reason is None
    assert decision.baa_in_force is False
    assert decision.entitlement_in_force is False
    doctor.refresh()
    assert PracticeMember.get(doctor_id=doctor.id) is None


def test_active_pilot_grant_allows_access_without_baa_or_attestation():
    doctor = _doctor("existing-member")
    doctor.onboarding_completed_at = datetime.now(timezone.utc)
    doctor.save()
    practice, member = _practice(doctor, attested=False)
    _grant(doctor, practice=practice)

    decision = resolve_doctor_access(doctor)

    assert decision.allowed is True
    assert decision.authorization_basis == "pilot_grant"
    assert decision.denial_reason is None
    assert decision.baa_in_force is False
    assert decision.entitlement_in_force is True
    member.refresh()
    assert member.practice_id == practice.id
    assert member.role == "workforce"
    assert member.attested_at is None


def test_non_grant_user_without_membership_remains_denied():
    doctor = _doctor("non-grant")
    doctor.onboarding_completed_at = datetime.now(timezone.utc)
    doctor.save()

    decision = resolve_doctor_access(doctor)

    assert decision.allowed is False
    assert decision.authorization_basis is None
    assert decision.denial_reason == "practice_membership_required"
    assert decision.baa_in_force is False
    assert decision.entitlement_in_force is False


def test_active_pilot_grant_does_not_authorize_an_unonboarded_account():
    doctor = _doctor("unonboarded-grant")
    _grant(doctor)

    decision = resolve_doctor_access(doctor)

    assert decision.allowed is False
    assert decision.authorization_basis is None
    assert decision.denial_reason == "practice_membership_required"


def test_unonboarded_practice_bound_grant_remains_entitlement_only():
    doctor = _doctor("unonboarded-bound-grant")
    practice, _ = _practice(doctor)
    _grant(doctor, practice=practice)

    no_baa = resolve_doctor_access(doctor)
    assert no_baa.allowed is False
    assert no_baa.denial_reason == "baa_not_in_force"
    assert no_baa.entitlement_in_force is True

    practice.negotiated_baa_on_file = True
    practice.negotiated_baa_effective_date = datetime.now(timezone.utc) - timedelta(
        minutes=1
    )
    practice.negotiated_baa_reference = "Test BAA"
    practice.save()

    entitled = resolve_doctor_access(doctor)
    assert entitled.allowed is True
    assert entitled.authorization_basis == "pilot_grant"
    assert entitled.baa_in_force is True
    assert entitled.entitlement_in_force is True


def test_integrity_mismatched_pilot_grant_does_not_bypass_membership():
    doctor = _doctor("grant-integrity")
    doctor.onboarding_completed_at = datetime.now(timezone.utc)
    doctor.save()
    _grant(doctor, email_sha256="f" * 64)

    decision = resolve_doctor_access(doctor)

    assert decision.allowed is False
    assert decision.authorization_basis is None
    assert decision.denial_reason == "practice_membership_required"


def test_disabled_doctor_outranks_active_pilot_grant():
    doctor = _doctor("disabled-grant")
    doctor.onboarding_completed_at = datetime.now(timezone.utc)
    doctor.save()
    _grant(doctor)
    doctor.deleted_at = datetime.now(timezone.utc)
    doctor.save()

    decision = resolve_doctor_access(doctor)

    assert decision.allowed is False
    assert decision.denial_reason == "doctor_disabled"
    assert decision.authorization_basis is None


def test_revoked_membership_outranks_active_pilot_grant():
    doctor = _doctor("revoked-member")
    doctor.onboarding_completed_at = datetime.now(timezone.utc)
    doctor.save()
    practice, member = _practice(doctor)
    now = datetime.now(timezone.utc)
    member.access_revoked_at = now
    member.access_revoked_by_ref = "test-suite"
    member.access_revocation_reason = "Test offboarding"
    member.access_records_retain_until = now + timedelta(days=365 * 8)
    member.save()
    _grant(doctor, practice=practice)

    decision = resolve_doctor_access(doctor)

    assert decision.allowed is False
    assert decision.denial_reason == "practice_membership_revoked"
    assert decision.authorization_basis is None


def test_inactive_membership_outranks_active_pilot_grant():
    doctor = _doctor("inactive-member")
    doctor.onboarding_completed_at = datetime.now(timezone.utc)
    doctor.save()
    practice, member = _practice(doctor)
    member.seat_state = "inactive"
    member.save()
    _grant(doctor, practice=practice)

    decision = resolve_doctor_access(doctor)

    assert decision.allowed is False
    assert decision.denial_reason == "practice_membership_inactive"
    assert decision.authorization_basis is None


def test_revoked_grant_tombstone_outranks_otherwise_valid_practice():
    doctor = _doctor("revoked-grant")
    doctor.onboarding_completed_at = datetime.now(timezone.utc)
    doctor.save()
    practice, _ = _practice(doctor)
    practice.negotiated_baa_on_file = True
    practice.negotiated_baa_effective_date = datetime.now(timezone.utc) - timedelta(
        minutes=1
    )
    practice.negotiated_baa_reference = "Test BAA"
    practice.billing_exempt = True
    practice.save()
    _grant(doctor, practice=practice, revoked=True)

    decision = resolve_doctor_access(doctor)

    assert decision.allowed is False
    assert decision.denial_reason == "pilot_grant_revoked"


def test_normal_practice_path_keeps_baa_and_entitlement_rules():
    doctor = _doctor("practice")
    practice, _ = _practice(doctor)

    no_baa = resolve_doctor_access(doctor)
    assert no_baa.allowed is False
    assert no_baa.denial_reason == "baa_not_in_force"

    practice.negotiated_baa_on_file = True
    practice.negotiated_baa_effective_date = datetime.now(timezone.utc) - timedelta(
        minutes=1
    )
    practice.negotiated_baa_reference = "Test BAA"
    practice.save()

    no_billing = resolve_doctor_access(doctor)
    assert no_billing.allowed is False
    assert no_billing.denial_reason == "entitlement_not_in_force"
    assert resolve_doctor_access(doctor, require_entitlement=False).allowed is True

    practice.billing_exempt = True
    practice.save()
    entitled = resolve_doctor_access(doctor)
    assert entitled.allowed is True
    assert entitled.authorization_basis == "workforce_attestation"
    assert entitled.baa_in_force is True
    assert entitled.entitlement_in_force is True


def test_stale_clickwrap_denies_access_until_current_version_is_accepted():
    doctor = _doctor("stale-baa")
    practice, _ = _practice(doctor)
    practice.baa_accepted_at = datetime.now(timezone.utc)
    practice.baa_version = "CN-BAA-CUST-CW v2.2"
    practice.billing_exempt = True
    practice.save()

    stale = resolve_doctor_access(doctor)
    assert stale.allowed is False
    assert stale.denial_reason == "baa_not_in_force"

    practice.baa_version = BAA_CURRENT_REFERENCE
    practice.save()
    current = resolve_doctor_access(doctor)
    assert current.allowed is True
    assert current.baa_in_force is True


@pytest.mark.parametrize("acquisition_source", ["managed", "public_self_service"])
def test_acquisition_provenance_is_authorization_neutral(
    acquisition_source: str,
) -> None:
    doctor = _doctor(f"provenance-{acquisition_source}")
    practice, _ = _practice(doctor)
    practice.acquisition_source = acquisition_source
    practice.negotiated_baa_on_file = True
    practice.negotiated_baa_effective_date = datetime.now(timezone.utc) - timedelta(
        minutes=1
    )
    practice.negotiated_baa_reference = "Test BAA"
    practice.billing_exempt = True
    practice.save()

    decision = resolve_doctor_access(doctor)

    assert decision.allowed is True
    assert decision.authorization_basis == "workforce_attestation"
    assert not hasattr(PracticeMember, "credential_state")
