import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import pytest
from typeid import TypeID

from app.configuration.visit_prep import VisitPrepSettings, load_visit_prep_settings
from app.services.visit_prep_access import resolve_visit_prep_access

from app.models.doctor import Doctor
from app.models.pilot_access_grandfather_grant import PilotAccessGrandfatherGrant
from app.models.practice import Practice, PracticeMember

from tests.factories import save_practice_member


def _doctor(label: str) -> Doctor:
    nonce = secrets.token_hex(4)
    return Doctor(
        clerk_id=f"user_visit_prep_{label}_{nonce}",
        email=f"visit-prep-{label}-{nonce}@example.com",
        biography="",
    ).save()


def _practice(
    doctor: Doctor,
    *,
    baa: bool = True,
    entitled: bool = True,
    attested: bool = True,
) -> tuple[Practice, PracticeMember]:
    now = datetime.now(timezone.utc)
    practice = Practice(
        legal_name="Visit Prep Test Practice",
        join_code=f"V{secrets.token_hex(4).upper()[:7]}",
        email_domain="example.com",
        negotiated_baa_on_file=baa,
        negotiated_baa_effective_date=now - timedelta(minutes=1) if baa else None,
        negotiated_baa_reference="test:visit-prep-baa" if baa else None,
        billing_exempt=entitled,
    ).save()
    member = save_practice_member(
        PracticeMember(
            practice_id=practice.id,
            doctor_id=doctor.id,
            role="owner",
            attested_at=now if attested else None,
            attestation_name="Visit Prep Test Physician" if attested else None,
        )
    )
    return practice, member


def _settings(
    *,
    doctor: Doctor | None = None,
    practice: Practice | None = None,
    email: bool = False,
    sms: bool = False,
) -> VisitPrepSettings:
    return VisitPrepSettings(
        enabled=True,
        enabled_doctor_ids=(frozenset({str(doctor.id)}) if doctor else frozenset()),
        enabled_practice_ids=(
            frozenset({str(practice.id)}) if practice else frozenset()
        ),
        email_send_enabled=email,
        sms_send_enabled=sms,
    )


def _pilot_grant(
    doctor: Doctor,
    *,
    revoked: bool = False,
) -> PilotAccessGrandfatherGrant:
    now = datetime.now(timezone.utc)
    assert doctor.email
    email = doctor.email.lower()
    return PilotAccessGrandfatherGrant(
        clerk_id=doctor.clerk_id,
        doctor_id_snapshot=doctor.id,
        email_snapshot=email,
        email_sha256=hashlib.sha256(email.encode()).hexdigest(),
        source_ref="test:visit-prep",
        source_sha256="a" * 64,
        grant_plan_sha256="b" * 64,
        granted_by_ref="test-suite",
        grant_reason="Prove pilot access alone cannot unlock Visit Prep",
        granted_at=now - timedelta(minutes=1),
        revoked_at=now if revoked else None,
        revoked_by_ref="test-suite" if revoked else None,
        revocation_reason="Test offboarding" if revoked else None,
        revocation_plan_sha256="c" * 64 if revoked else None,
        retain_until=now + timedelta(days=365 * 8) if revoked else None,
    ).save()


def test_configuration_defaults_every_capability_off():
    settings = load_visit_prep_settings({})

    assert settings == VisitPrepSettings()
    assert settings.configuration_valid is True
    assert settings.public_api_enabled is False


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("FF_VISIT_PREP_202608", "sometimes"),
        ("VISIT_PREP_PUBLIC_API_ENABLED", "enabled"),
        ("VISIT_PREP_EMAIL_SEND_ENABLED", "enabled"),
        ("VISIT_PREP_SMS_SEND_ENABLED", object()),
        ("VISIT_PREP_ENABLED_DOCTOR_IDS", str(TypeID("practice"))),
        ("VISIT_PREP_ENABLED_PRACTICE_IDS", "practice_not-a-typeid"),
        ("VISIT_PREP_ENABLED_PRACTICE_IDS", f"{TypeID('practice')},"),
    ],
)
def test_malformed_configuration_disables_every_capability(name, value):
    values = {
        "FF_VISIT_PREP_202608": "true",
        "VISIT_PREP_PUBLIC_API_ENABLED": "true",
        "VISIT_PREP_ENABLED_DOCTOR_IDS": str(TypeID("doctor")),
        "VISIT_PREP_ENABLED_PRACTICE_IDS": str(TypeID("practice")),
        "VISIT_PREP_EMAIL_SEND_ENABLED": "true",
        "VISIT_PREP_SMS_SEND_ENABLED": "true",
        name: value,
    }

    settings = load_visit_prep_settings(values)

    assert settings.configuration_valid is False
    assert settings.enabled is False
    assert settings.public_api_enabled is False
    assert settings.enabled_doctor_ids == frozenset()
    assert settings.enabled_practice_ids == frozenset()
    assert settings.email_send_enabled is False
    assert settings.sms_send_enabled is False


def test_exact_doctor_cohort_requires_live_practice_baa_and_entitlement():
    doctor = _doctor("doctor-cohort")
    practice, member = _practice(doctor)

    decision = resolve_visit_prep_access(
        doctor,
        settings=_settings(doctor=doctor, email=True),
    )

    assert decision.eligible is True
    assert decision.can_manage is True
    assert decision.can_send_email is True
    assert decision.can_send_sms is False
    assert decision.doctor_id == doctor.id
    assert decision.practice_id == practice.id
    assert decision.practice_member_id == member.id
    assert decision.denial_reason is None


def test_exact_practice_cohort_enables_an_authorized_member():
    doctor = _doctor("practice-cohort")
    practice, _ = _practice(doctor)

    decision = resolve_visit_prep_access(
        doctor,
        settings=_settings(practice=practice),
    )

    assert decision.eligible is True


def test_master_flag_off_overrides_cohort_and_channel_flags():
    doctor = _doctor("master-off")
    practice, _ = _practice(doctor)
    settings = VisitPrepSettings(
        enabled=False,
        enabled_doctor_ids=frozenset({str(doctor.id)}),
        enabled_practice_ids=frozenset({str(practice.id)}),
        email_send_enabled=True,
        sms_send_enabled=True,
    )

    decision = resolve_visit_prep_access(doctor, settings=settings)

    assert decision.eligible is False
    assert decision.can_manage is False
    assert decision.can_send_email is False
    assert decision.can_send_sms is False
    assert decision.denial_reason == "feature_disabled"


def test_invalid_configuration_is_not_partially_usable():
    doctor = _doctor("invalid-config")
    _practice(doctor)
    settings = VisitPrepSettings(
        enabled=True,
        enabled_doctor_ids=frozenset({str(doctor.id)}),
        email_send_enabled=True,
        sms_send_enabled=True,
        configuration_valid=False,
    )

    decision = resolve_visit_prep_access(doctor, settings=settings)

    assert decision.eligible is False
    assert decision.denial_reason == "configuration_invalid"
    assert decision.can_manage is False
    assert decision.can_send_email is False
    assert decision.can_send_sms is False


def test_mcd_origin_channel_is_not_release_authority():
    doctor = _doctor("mcd-channel")
    practice, _ = _practice(doctor)
    practice.origin_channel = "mcd"
    practice.save()

    decision = resolve_visit_prep_access(
        doctor,
        settings=VisitPrepSettings(enabled=True),
    )

    assert decision.eligible is False
    assert decision.denial_reason == "cohort_not_enabled"


def test_a_different_exact_typeid_does_not_match_the_cohort():
    doctor = _doctor("wrong-cohort")
    practice, _ = _practice(doctor)
    settings = VisitPrepSettings(
        enabled=True,
        enabled_doctor_ids=frozenset({str(TypeID("doctor"))}),
        enabled_practice_ids=frozenset({str(TypeID("practice"))}),
    )

    decision = resolve_visit_prep_access(doctor, settings=settings)

    assert decision.eligible is False
    assert decision.denial_reason == "cohort_not_enabled"
    assert decision.practice_id == practice.id


def test_pilot_grant_alone_is_not_visit_prep_authority():
    doctor = _doctor("pilot-only")
    _pilot_grant(doctor)

    decision = resolve_visit_prep_access(
        doctor,
        settings=_settings(doctor=doctor, email=True, sms=True),
    )

    assert decision.eligible is False
    assert decision.denial_reason == "practice_membership_required"
    assert decision.can_send_email is False
    assert decision.can_send_sms is False


def test_deleted_doctor_cannot_use_visit_prep():
    doctor = _doctor("deleted-doctor")
    _practice(doctor)
    doctor.deleted_at = datetime.now(timezone.utc)

    decision = resolve_visit_prep_access(
        doctor,
        settings=_settings(doctor=doctor, email=True, sms=True),
    )

    assert decision.eligible is False
    assert decision.denial_reason == "doctor_disabled"
    assert decision.can_manage is False
    assert decision.can_send_email is False
    assert decision.can_send_sms is False


def test_revoked_pilot_grant_tombstone_blocks_valid_practice():
    doctor = _doctor("revoked-pilot")
    _practice(doctor)
    _pilot_grant(doctor, revoked=True)

    decision = resolve_visit_prep_access(
        doctor,
        settings=_settings(doctor=doctor, email=True, sms=True),
    )

    assert decision.eligible is False
    assert decision.denial_reason == "pilot_grant_revoked"
    assert decision.can_manage is False
    assert decision.can_send_email is False
    assert decision.can_send_sms is False


def test_revoked_practice_membership_cannot_use_visit_prep():
    doctor = _doctor("revoked-member")
    _, member = _practice(doctor)
    now = datetime.now(timezone.utc)
    member.access_revoked_at = now
    member.access_revoked_by_ref = "test-suite"
    member.access_revocation_reason = "Test offboarding"
    member.access_records_retain_until = now + timedelta(days=365 * 8)
    save_practice_member(member)

    decision = resolve_visit_prep_access(
        doctor,
        settings=_settings(doctor=doctor, email=True, sms=True),
    )

    assert decision.eligible is False
    assert decision.denial_reason == "practice_membership_revoked"
    assert decision.can_manage is False
    assert decision.can_send_email is False
    assert decision.can_send_sms is False


@pytest.mark.parametrize(
    ("baa", "entitled", "attested", "reason"),
    [
        (False, True, True, "baa_not_in_force"),
        (True, False, True, "entitlement_not_in_force"),
        (True, True, False, "practice_access_not_authorized"),
    ],
)
def test_practice_safety_requirements_fail_closed(baa, entitled, attested, reason):
    doctor = _doctor(reason)
    _practice(doctor, baa=baa, entitled=entitled, attested=attested)

    decision = resolve_visit_prep_access(
        doctor,
        settings=_settings(doctor=doctor),
    )

    assert decision.eligible is False
    assert decision.denial_reason == reason


def test_view_as_can_read_eligibility_but_cannot_manage_or_send():
    doctor = _doctor("view-as")
    _practice(doctor)

    decision = resolve_visit_prep_access(
        doctor,
        view_as_active=True,
        settings=_settings(doctor=doctor, email=True, sms=True),
    )

    assert decision.eligible is True
    assert decision.can_manage is False
    assert decision.can_send_email is False
    assert decision.can_send_sms is False
    assert decision.denial_reason is None
    assert decision.view_as_active is True
