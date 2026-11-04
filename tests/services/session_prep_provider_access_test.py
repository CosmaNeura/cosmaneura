import copy
import hashlib
import secrets
from datetime import UTC, datetime, timedelta

import pytest

import app.services.session_prep_provider_access as provider_access
from app.services.organization_access import (
    RosterApprovalInput,
    claim_roster_access,
    configure_provider_organization,
    import_roster_approvals,
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
    doctor: Doctor,
    *,
    practice: Practice | None = None,
    snapshot_doctor: Doctor | None = None,
    granted_at: datetime | None = None,
) -> PilotAccessGrandfatherGrant:
    now = datetime.now(UTC)
    assert doctor.email is not None
    email = doctor.email.lower()
    return PilotAccessGrandfatherGrant(
        clerk_id=doctor.clerk_id,
        doctor_id_snapshot=(
            snapshot_doctor.id if snapshot_doctor is not None else doctor.id
        ),
        practice_id_snapshot=practice.id if practice is not None else None,
        email_snapshot=email,
        email_sha256=hashlib.sha256(email.encode()).hexdigest(),
        source_ref="test:session-prep-provider-access",
        source_sha256="a" * 64,
        grant_plan_sha256="b" * 64,
        granted_by_ref="test-suite",
        grant_reason="Verify active-account Session Prep access",
        granted_at=granted_at if granted_at is not None else now - timedelta(minutes=1),
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
        reason="Verify MCD account access for Session Prep",
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


def test_active_account_with_current_practice_is_allowed() -> None:
    doctor = _doctor("allowed")
    practice = provision_practice_in_force(doctor)

    decision = resolve_session_prep_provider_access(doctor)

    assert decision.allowed is True
    assert decision.denial_reason is None
    assert decision.practice is not None
    assert decision.practice.id == practice.id


def test_active_new_account_needs_no_onboarding_or_practice() -> None:
    doctor = _doctor("new-no-practice", onboarded=False)

    decision = resolve_session_prep_provider_access(doctor)

    assert decision.allowed is True
    assert decision.denial_reason is None
    assert decision.practice is None


def test_expired_mcd_roster_does_not_disable_the_account() -> None:
    now = datetime.now(UTC)
    doctor, practice = _rostered_provider(
        "expired",
        now=now,
        expires_at=now + timedelta(minutes=5),
    )

    decision = resolve_session_prep_provider_access(
        doctor,
        at=now + timedelta(minutes=6),
    )

    assert decision.allowed is True
    assert decision.denial_reason is None
    assert decision.practice is not None
    assert decision.practice.id == practice.id


def test_null_practice_legacy_grant_needs_no_membership() -> None:
    doctor = _doctor("legacy-null-practice")
    _pilot_grant(doctor)

    decision = resolve_session_prep_provider_access(doctor)

    assert decision.allowed is True
    assert decision.denial_reason is None
    assert decision.practice is None


def test_practice_baa_and_authorized_membership_do_not_gate_active_account() -> None:
    doctor = _doctor("no-baa")
    practice = Practice(
        legal_name="Session Prep no-BAA practice",
        join_code=f"S{secrets.token_hex(4).upper()[:7]}",
        email_domain="example.com",
        billing_exempt=False,
    ).save()
    save_practice_member(
        PracticeMember(
            practice_id=practice.id,
            doctor_id=doctor.id,
            role="workforce",
            attested_at=None,
        )
    )

    decision = resolve_session_prep_provider_access(doctor)

    assert decision.allowed is True
    assert decision.denial_reason is None
    assert decision.practice is not None
    assert decision.practice.id == practice.id


def test_expired_trial_does_not_gate_active_account() -> None:
    doctor = _doctor("expired-trial")
    practice = provision_practice_in_force(doctor)
    practice.billing_exempt = False
    practice.billing_exempt_until = None
    practice.trial_ends_at = datetime.now(UTC) - timedelta(minutes=1)
    practice.subscription_status = None
    practice.save()

    decision = resolve_session_prep_provider_access(doctor)

    assert decision.allowed is True
    assert decision.denial_reason is None
    assert decision.practice is not None
    assert decision.practice.id == practice.id


def test_membership_revocation_does_not_replace_account_status() -> None:
    doctor = _doctor("revoked-membership")
    provision_practice_in_force(doctor)
    member = PracticeMember.one(doctor_id=doctor.id)
    now = datetime.now(UTC)
    member.access_revoked_at = now
    member.access_revoked_by_ref = "test-suite"
    member.access_revocation_reason = "Test practice revocation"
    member.access_records_retain_until = now + timedelta(days=2922)
    save_practice_member(member)

    decision = resolve_session_prep_provider_access(doctor)

    assert decision.allowed is True
    assert decision.denial_reason is None


@pytest.mark.parametrize(
    "legacy_state",
    ["missing", "unbound_practice", "other_practice", "future", "email", "doctor"],
)
def test_legacy_grant_metadata_is_not_an_enrollment_gate(legacy_state: str) -> None:
    doctor = _doctor(f"legacy-{legacy_state}")
    practice = provision_practice_in_force(doctor)
    practice.billing_exempt = False
    practice.billing_exempt_until = None
    practice.trial_ends_at = None
    practice.subscription_status = None
    practice.save()
    if legacy_state != "missing":
        grant_practice = None if legacy_state == "unbound_practice" else practice
        if legacy_state == "other_practice":
            grant_practice = provision_practice_in_force(_doctor("other-practice"))
        _pilot_grant(
            doctor,
            practice=grant_practice,
            snapshot_doctor=(
                _doctor("other-doctor") if legacy_state == "doctor" else None
            ),
            granted_at=(
                datetime.now(UTC) + timedelta(days=1)
                if legacy_state == "future"
                else None
            ),
        )
        if legacy_state == "email":
            doctor.email = f"changed-{secrets.token_hex(4)}@example.com"
            doctor.save()

    decision = resolve_session_prep_provider_access(doctor)

    assert decision.allowed is True
    assert decision.denial_reason is None


def test_retired_pilot_grant_does_not_disable_an_active_account() -> None:
    doctor = _doctor("revoked-pilot")
    grant = _pilot_grant(doctor)
    now = datetime.now(UTC)
    grant.revoked_at = now
    grant.revoked_by_ref = "test-suite"
    grant.revocation_reason = "Test account stop"
    grant.revocation_plan_sha256 = "c" * 64
    grant.retain_until = now + timedelta(days=2922)
    grant.save()

    decision = resolve_session_prep_provider_access(doctor)

    assert decision.allowed is True
    assert decision.denial_reason is None


def test_active_replacement_is_also_ignored_as_enrollment_metadata() -> None:
    doctor = _doctor("replacement-pilot")
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


def test_fresh_deleted_state_overrides_stale_active_input() -> None:
    doctor = _doctor("fresh-delete", onboarded=False)
    stale = copy.deepcopy(doctor)
    doctor.deleted_at = datetime.now(UTC)
    doctor.save()
    assert stale.deleted_at is None

    decision = resolve_session_prep_provider_access(stale)

    assert decision.allowed is False
    assert decision.denial_reason == "provider_unavailable"


def test_nonpersisted_provider_is_unavailable() -> None:
    doctor = Doctor(
        clerk_id="user_session_prep_missing_provider",
        email="session-prep-missing-provider@example.com",
        biography="",
    )

    decision = resolve_session_prep_provider_access(doctor)

    assert decision.allowed is False
    assert decision.denial_reason == "provider_unavailable"


@pytest.mark.parametrize(
    "denial",
    ["scribe_cancelled_read_only", "scribe_export_period_expired"],
)
def test_fresh_cancellation_state_refreezes_previously_active_account(
    denial: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    doctor = _doctor(f"cancel-{denial}", onboarded=False)
    assert resolve_session_prep_provider_access(doctor).allowed is True
    monkeypatch.setattr(
        provider_access,
        "doctor_cancellation_denial",
        lambda *_args, **_kwargs: denial,
    )

    decision = resolve_session_prep_provider_access(doctor)

    assert decision.allowed is False
    assert decision.denial_reason == denial
