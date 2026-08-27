import secrets
from datetime import datetime, timedelta, timezone

import pytest

from app.services.organization_access import (
    OrganizationAccessError,
    RosterApprovalInput,
    claim_roster_access,
    configure_provider_organization,
    import_roster_approvals,
)
from app.services.public_entitlements import (
    MAX_ACTIVE_CLINICIAN_SEATS,
    active_clinician_seat_count,
)

from activemodel.session_manager import get_session
from app.models.doctor import Doctor
from app.models.organization_access import OrganizationRosterApproval
from app.models.practice import ClinicianSeatState, Practice, PracticeMember


def _doctor(email: str) -> Doctor:
    return Doctor(
        clerk_id=f"user_org_seat_{secrets.token_hex(8)}",
        email=email,
        biography="",
    ).save()


def test_managed_claim_rejects_reactivation_as_twenty_first_seat() -> None:
    now = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)
    domain = f"managed-seat-{secrets.token_hex(4)}.example.com"
    practice = Practice(
        legal_name="Full Managed Practice",
        join_code=f"M{secrets.token_hex(4).upper()[:7]}",
        email_domain=domain,
        origin_channel="direct",
        is_solo=False,
        negotiated_baa_on_file=True,
        negotiated_baa_effective_date=now - timedelta(days=1),
        negotiated_baa_reference="executed-baa:seat-cap",
        billing_exempt=True,
    ).save()
    _, code, _ = configure_provider_organization(
        practice,
        legal_name="Full Managed Organization",
        slug=f"managed-seat-{secrets.token_hex(5)}",
        email_domain=domain,
        operator_ref="test-seat-cap",
        reason="Exercise the managed claim seat ceiling",
        now=now,
    )
    practice.refresh()
    practice.stripe_subscription_id = f"sub_managed_seat_{secrets.token_hex(8)}"
    practice.stripe_quantity_sync_target = MAX_ACTIVE_CLINICIAN_SEATS
    practice.stripe_quantity_sync_version = 6
    practice.save()
    for ordinal in range(MAX_ACTIVE_CLINICIAN_SEATS):
        doctor = _doctor(f"active-{ordinal}-{secrets.token_hex(3)}@{domain}")
        PracticeMember(
            practice_id=practice.id,
            doctor_id=doctor.id,
            role="workforce",
        ).save()

    claimant = _doctor(f"claimant@{domain}")
    inactive_member = PracticeMember(
        practice_id=practice.id,
        doctor_id=claimant.id,
        role="workforce",
        seat_state=ClinicianSeatState.inactive.value,
    ).save()
    import_roster_approvals(
        practice,
        [RosterApprovalInput(email=claimant.email or "")],
        operator_ref="test-seat-cap",
        reason="Approve the capacity-bound claimant",
        source_ref="test://seat-cap-roster-v1",
        source_sha256="a" * 64,
        now=now,
    )

    with pytest.raises(OrganizationAccessError, match="at most 20"):
        claim_roster_access(
            claimant,
            supplied_code=code,
            now=now + timedelta(seconds=1),
        )

    inactive_member.refresh()
    assert inactive_member.seat_state == ClinicianSeatState.inactive.value
    assert inactive_member.organization_roster_approval_id is None
    approval = OrganizationRosterApproval.get(
        approved_email=claimant.email,
        target_practice_id=practice.id,
    )
    assert approval is not None
    assert approval.claimed_doctor_id is None
    practice.refresh()
    assert practice.stripe_quantity_sync_required is False
    assert practice.stripe_quantity_sync_target == MAX_ACTIVE_CLINICIAN_SEATS
    assert practice.stripe_quantity_sync_version == 6


def test_managed_claim_records_authoritative_quantity_in_claim_transaction() -> None:
    now = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)
    domain = f"managed-seat-success-{secrets.token_hex(4)}.example.com"
    practice = Practice(
        legal_name="Managed Practice With One Open Seat",
        join_code=f"M{secrets.token_hex(4).upper()[:7]}",
        email_domain=domain,
        origin_channel="direct",
        is_solo=False,
        negotiated_baa_on_file=True,
        negotiated_baa_effective_date=now - timedelta(days=1),
        negotiated_baa_reference="executed-baa:seat-cap-success",
        billing_exempt=True,
    ).save()
    _, code, _ = configure_provider_organization(
        practice,
        legal_name="Managed Organization With One Open Seat",
        slug=f"managed-seat-success-{secrets.token_hex(5)}",
        email_domain=domain,
        operator_ref="test-seat-cap",
        reason="Exercise atomic managed claim quantity recording",
        now=now,
    )
    practice.refresh()
    practice.stripe_subscription_id = f"sub_managed_seat_{secrets.token_hex(8)}"
    practice.save()
    for ordinal in range(MAX_ACTIVE_CLINICIAN_SEATS - 1):
        doctor = _doctor(f"active-{ordinal}-{secrets.token_hex(3)}@{domain}")
        PracticeMember(
            practice_id=practice.id,
            doctor_id=doctor.id,
            role="workforce",
        ).save()

    inactive_doctor = _doctor(f"inactive-{secrets.token_hex(3)}@{domain}")
    PracticeMember(
        practice_id=practice.id,
        doctor_id=inactive_doctor.id,
        role="workforce",
        seat_state=ClinicianSeatState.inactive.value,
    ).save()
    revoked_at = now - timedelta(days=1)
    revoked_doctor = _doctor(f"revoked-{secrets.token_hex(3)}@{domain}")
    PracticeMember(
        practice_id=practice.id,
        doctor_id=revoked_doctor.id,
        role="workforce",
        access_revoked_at=revoked_at,
        access_revoked_by_ref="test-seat-cap",
        access_revocation_reason="Synthetic irreversible revocation",
        access_records_retain_until=revoked_at + timedelta(days=365 * 8),
    ).save()

    claimant = _doctor(f"claimant@{domain}")
    import_roster_approvals(
        practice,
        [RosterApprovalInput(email=claimant.email or "")],
        operator_ref="test-seat-cap",
        reason="Approve the final capacity-bound claimant",
        source_ref="test://seat-cap-success-roster-v1",
        source_sha256="b" * 64,
        now=now,
    )

    _, member = claim_roster_access(
        claimant,
        supplied_code=code,
        now=now + timedelta(seconds=1),
    )

    practice.refresh()
    member.refresh()
    assert member.seat_state == ClinicianSeatState.active.value
    assert practice.stripe_quantity_sync_required is True
    assert practice.stripe_quantity_sync_target == MAX_ACTIVE_CLINICIAN_SEATS
    assert practice.stripe_quantity_sync_version == 1
    with get_session() as session:
        assert (
            active_clinician_seat_count(session, practice.id)
            == MAX_ACTIVE_CLINICIAN_SEATS
        )
