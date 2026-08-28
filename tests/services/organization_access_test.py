"""Provider-organization roster access stays distinct from BAA attestation."""

import secrets
from datetime import datetime, timedelta, timezone
from typing import cast

import pytest

import app.services.organization_access as organization_access
from app.services.organization_access import (
    OrganizationAccessError,
    RosterApprovalInput,
    RosterAssignmentInput,
    claim_roster_access,
    configure_provider_organization,
    configure_provider_organization_with_roster,
    find_access_opportunity,
    import_roster_approvals,
    member_access_in_force,
    member_authorization_basis,
    offboard_member,
    provider_organization_with_roster_plan_sha256,
    revoke_roster_access,
    rotate_access_code,
    validate_provider_organization_with_roster,
)

from activemodel.session_manager import get_session
from app.models.baa import BaaAcceptance
from app.models.doctor import Doctor
from app.models.organization_access import (
    OrganizationRosterApproval,
    ProviderOrganization,
)
from app.models.practice import Practice, PracticeMember
from app.models.practice_access_audit import (
    PracticeAccessAction,
    PracticeAccessAudit,
)
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError


def _doctor(email: str) -> Doctor:
    return Doctor(
        clerk_id=f"user_org_{secrets.token_hex(8)}",
        email=email,
        biography="",
    ).save()


def _configured_practice(
    domain: str,
    *,
    now: datetime,
) -> tuple[Practice, str]:
    practice = Practice(
        legal_name=f"{domain} Pilot Practice",
        join_code=f"O{secrets.token_hex(4).upper()[:7]}",
        email_domain=domain,
        origin_channel="direct",
        is_solo=False,
        negotiated_baa_on_file=True,
        negotiated_baa_effective_date=now - timedelta(days=1),
        negotiated_baa_reference=f"executed-baa:{domain}",
        billing_exempt=True,
    ).save()
    _, code, _ = configure_provider_organization(
        practice,
        legal_name=f"{domain} Provider Organization",
        slug=f"pilot-{secrets.token_hex(5)}",
        email_domain=domain,
        operator_ref="test-operator",
        reason="Test organization setup",
        now=now,
    )
    return practice, code


def _approve(
    practice: Practice,
    email: str,
    *,
    now: datetime,
    source_sha256: str = "a" * 64,
):
    return import_roster_approvals(
        practice,
        [RosterApprovalInput(email=email)],
        operator_ref="test-roster-operator",
        reason="Approved pilot roster",
        source_ref="test://sanitized-roster-v1",
        source_sha256=source_sha256,
        now=now,
    )


def _unlinked_pilot_practice(domain: str, label: str, *, now: datetime) -> Practice:
    return Practice(
        legal_name=f"{label} Pilot Practice",
        join_code=f"M{secrets.token_hex(4).upper()[:7]}",
        email_domain=domain,
        origin_channel="direct",
        is_solo=False,
        negotiated_baa_on_file=True,
        negotiated_baa_effective_date=now - timedelta(days=1),
        negotiated_baa_reference=f"executed-baa:{domain}",
        billing_exempt=True,
    ).save()


@pytest.mark.parametrize(
    ("negotiated_baa_reference", "billing_exempt", "expected_error"),
    [
        (None, True, "negotiated BAA"),
        ("executed-baa:test", False, "billing exemption"),
    ],
)
def test_organization_configuration_requires_baa_and_billing_preconditions(
    negotiated_baa_reference,
    billing_exempt,
    expected_error,
):
    now = datetime(2026, 8, 22, 14, 0, tzinfo=timezone.utc)
    domain = f"precondition-{secrets.token_hex(4)}.example.com"
    practice = Practice(
        legal_name="Precondition Test Practice",
        join_code=f"P{secrets.token_hex(4).upper()[:7]}",
        email_domain=domain,
        origin_channel="direct",
        is_solo=False,
        negotiated_baa_on_file=True,
        negotiated_baa_effective_date=now - timedelta(days=1),
        negotiated_baa_reference=negotiated_baa_reference,
        billing_exempt=billing_exempt,
    ).save()

    with pytest.raises(OrganizationAccessError, match=expected_error):
        configure_provider_organization(
            practice,
            legal_name="Precondition Test Organization",
            slug=f"precondition-{secrets.token_hex(5)}",
            email_domain=domain,
            operator_ref="test-operator",
            reason="Verify fail-closed setup prerequisites",
            now=now,
        )

    practice.refresh()
    assert practice.organization_id is None


@pytest.mark.parametrize(
    ("checkout_session_id", "subscription_id", "subscription_status", "message"),
    [
        ("cs_existing", None, None, "Stripe checkout"),
        (None, "sub_existing", "active", "Stripe subscription"),
    ],
)
def test_organization_configuration_rejects_parallel_stripe_billing(
    checkout_session_id,
    subscription_id,
    subscription_status,
    message,
):
    now = datetime(2026, 8, 22, 14, 0, tzinfo=timezone.utc)
    domain = f"billing-conflict-{secrets.token_hex(4)}.example.com"
    practice = Practice(
        legal_name="Billing Conflict Practice",
        join_code=f"B{secrets.token_hex(4).upper()[:7]}",
        email_domain=domain,
        origin_channel="direct",
        is_solo=False,
        negotiated_baa_on_file=True,
        negotiated_baa_effective_date=now - timedelta(days=1),
        negotiated_baa_reference="executed-baa:test",
        billing_exempt=True,
        stripe_checkout_session_id=checkout_session_id,
        stripe_subscription_id=subscription_id,
        subscription_status=subscription_status,
    ).save()

    with pytest.raises(OrganizationAccessError, match=message):
        configure_provider_organization(
            practice,
            legal_name="Billing Conflict Organization",
            slug=f"billing-conflict-{secrets.token_hex(5)}",
            email_domain=domain,
            operator_ref="test-operator",
            reason="Prevent parallel pilot and Stripe billing",
            now=now,
        )

    practice.refresh()
    assert practice.organization_id is None


def test_organization_configuration_rejects_public_onboarding_practice() -> None:
    now = datetime(2026, 8, 27, 14, 0, tzinfo=timezone.utc)
    domain = f"public-conversion-{secrets.token_hex(4)}.example.com"
    practice = _unlinked_pilot_practice(domain, "Public", now=now)
    practice.public_onboarding_required = True
    practice.save()

    with pytest.raises(OrganizationAccessError, match="audited conversion"):
        configure_provider_organization(
            practice,
            legal_name="Public Conversion Organization",
            slug=f"public-conversion-{secrets.token_hex(5)}",
            email_domain=domain,
            operator_ref="test-operator",
            reason="Reject an implicit public-to-managed conversion",
            now=now,
        )

    practice.refresh()
    assert practice.organization_id is None


def test_single_practice_configuration_refuses_a_sibling_domain_practice():
    now = datetime(2026, 8, 22, 14, 30, tzinfo=timezone.utc)
    domain = f"multi-required-{secrets.token_hex(4)}.example.com"
    first = _unlinked_pilot_practice(domain, "First", now=now)
    second = _unlinked_pilot_practice(domain, "Second", now=now)

    with pytest.raises(OrganizationAccessError, match="exactly one practice"):
        configure_provider_organization(
            first,
            legal_name="Multi Required Organization",
            slug=f"multi-required-{secrets.token_hex(5)}",
            email_domain=domain,
            operator_ref="test-operator",
            reason="Require atomic multi-practice setup",
            now=now,
        )

    first.refresh()
    second.refresh()
    assert first.organization_id is None
    assert second.organization_id is None


def test_exact_roster_and_code_claim_without_personal_baa_attestation():
    now = datetime(2026, 8, 22, 14, 0, tzinfo=timezone.utc)
    domain = f"pilot-{secrets.token_hex(4)}.example.com"
    doctor = _doctor(f"clinician@{domain}")
    practice, code = _configured_practice(domain, now=now)
    assert (
        PracticeAccessAudit.where(
            PracticeAccessAudit.practice_id == practice.id,
            PracticeAccessAudit.action
            == PracticeAccessAction.provider_organization_configured.value,
        ).count()
        == 1
    )

    first_import = _approve(practice, doctor.email or "", now=now)
    retry_import = _approve(practice, doctor.email or "", now=now)

    assert first_import.created == 1
    assert first_import.unchanged == 0
    assert retry_import.created == 0
    assert retry_import.unchanged == 1
    imported_audits = PracticeAccessAudit.where(
        PracticeAccessAudit.practice_id == practice.id,
        PracticeAccessAudit.action
        == PracticeAccessAction.organization_roster_imported.value,
    )
    assert imported_audits.count() == 1
    first_audit = cast(PracticeAccessAudit, imported_audits.one())
    assert first_audit.evidence_sha256 == "a" * 64

    distinct_source_import = _approve(
        practice,
        doctor.email or "",
        now=now + timedelta(seconds=1),
        source_sha256="c" * 64,
    )
    assert distinct_source_import.created == 0
    assert distinct_source_import.unchanged == 1
    assert imported_audits.count() == 2
    assert {
        audit.evidence_sha256
        for audit in cast(list[PracticeAccessAudit], list(imported_audits.all()))
    } == {
        "a" * 64,
        "c" * 64,
    }

    opportunity = find_access_opportunity(doctor, at=now)
    assert opportunity is not None
    assert opportunity.practice.id == practice.id

    claimed_practice, member = claim_roster_access(
        doctor,
        supplied_code=code,
        now=now + timedelta(seconds=2),
    )
    retry_practice, retry_member = claim_roster_access(
        doctor,
        supplied_code=code,
        now=now + timedelta(seconds=3),
    )

    assert claimed_practice.id == retry_practice.id == practice.id
    assert member.id == retry_member.id
    assert member.attested_at is None
    assert member.attestation_name is None
    assert member.organization_authorized_at == now + timedelta(seconds=2)
    assert member_authorization_basis(member, at=now + timedelta(seconds=3)) == (
        "organization_roster"
    )
    assert member_access_in_force(member, at=now + timedelta(seconds=3))
    assert BaaAcceptance.where(BaaAcceptance.practice_id == practice.id).count() == 0
    assert (
        PracticeAccessAudit.where(
            PracticeAccessAudit.practice_id == practice.id,
            PracticeAccessAudit.action
            == PracticeAccessAction.organization_access_granted.value,
        ).count()
        == 1
    )


def test_roster_import_rejects_account_attached_to_another_practice():
    now = datetime(2026, 8, 22, 14, 0, tzinfo=timezone.utc)
    domain = f"conflict-{secrets.token_hex(4)}.example.com"
    doctor = _doctor(f"clinician@{domain}")
    unrelated_practice = Practice(
        legal_name="Existing Unrelated Practice",
        join_code=f"X{secrets.token_hex(4).upper()[:7]}",
        email_domain=f"unrelated-{secrets.token_hex(4)}.example.com",
        origin_channel="direct",
        is_solo=True,
    ).save()
    PracticeMember(
        practice_id=unrelated_practice.id,
        doctor_id=doctor.id,
        role="owner",
    ).save()
    target_practice, _ = _configured_practice(domain, now=now)

    with pytest.raises(
        OrganizationAccessError,
        match="belongs to another practice",
    ):
        _approve(target_practice, doctor.email or "", now=now)

    assert (
        OrganizationRosterApproval.get(
            organization_id=target_practice.organization_id,
            approved_email=doctor.email,
        )
        is None
    )


def test_wrong_code_unrostered_email_and_wrong_domain_are_denied():
    now = datetime(2026, 8, 22, 15, 0, tzinfo=timezone.utc)
    domain = f"pilot-{secrets.token_hex(4)}.example.com"
    approved = _doctor(f"approved@{domain}")
    unrostered = _doctor(f"not-approved@{domain}")
    practice, _ = _configured_practice(domain, now=now)
    _approve(practice, approved.email or "", now=now)

    with pytest.raises(
        OrganizationAccessError,
        match="organization access is not available",
    ):
        claim_roster_access(approved, supplied_code="WRONGCODE123", now=now)

    assert PracticeMember.get(doctor_id=approved.id) is None
    assert find_access_opportunity(unrostered, at=now) is None
    with pytest.raises(
        OrganizationAccessError,
        match="organization access is not available",
    ):
        claim_roster_access(unrostered, supplied_code="WRONGCODE123", now=now)

    with pytest.raises(OrganizationAccessError, match="does not match"):
        _approve(practice, "outsider@wrong.example.com", now=now)
    with pytest.raises(OrganizationAccessError, match="does not match"):
        _approve(practice, f"outsider@sub.{domain}", now=now)
    assert PracticeMember.get(doctor_id=unrostered.id) is None


@pytest.mark.parametrize("invalid_negotiated_baa", ["future", "missing-reference"])
def test_old_clickwrap_cannot_rescue_invalid_negotiated_baa_for_org_access(
    invalid_negotiated_baa,
):
    now = datetime(2026, 8, 22, 15, 30, tzinfo=timezone.utc)
    domain = f"pilot-{secrets.token_hex(4)}.example.com"
    approved = _doctor(f"approved@{domain}")
    later_candidate = _doctor(f"later@{domain}")
    practice, code = _configured_practice(domain, now=now)
    _approve(practice, approved.email or "", now=now)

    # Simulate a clickwrap that predates the organization cutover. It keeps
    # the generic practice BAA predicate true, but must never substitute for
    # the organization's own negotiated instrument.
    practice.baa_accepted_at = now - timedelta(days=90)
    practice.baa_version = "CN-BAA-CUST-CW v2.2"
    if invalid_negotiated_baa == "future":
        practice.negotiated_baa_effective_date = now + timedelta(days=1)
    else:
        practice.negotiated_baa_reference = None
    practice.save()

    assert practice.baa_in_force(at=now)
    assert not practice.negotiated_baa_in_force(at=now)
    with pytest.raises(OrganizationAccessError, match="negotiated BAA"):
        _approve(practice, later_candidate.email or "", now=now)
    assert find_access_opportunity(approved, at=now) is None
    with pytest.raises(
        OrganizationAccessError,
        match="organization access is not available",
    ):
        claim_roster_access(approved, supplied_code=code, now=now)
    assert PracticeMember.get(doctor_id=approved.id) is None


def test_existing_same_practice_member_claim_preserves_role_and_attestation():
    now = datetime(2026, 8, 22, 16, 0, tzinfo=timezone.utc)
    historical_attestation = now - timedelta(days=120)
    domain = f"pilot-{secrets.token_hex(4)}.example.com"
    rostered_owner = _doctor(f"owner@{domain}")
    unrostered_member = _doctor(f"legacy@{domain}")
    practice, code = _configured_practice(domain, now=now)
    owner_member = PracticeMember(
        practice_id=practice.id,
        doctor_id=rostered_owner.id,
        role="owner",
        attested_at=historical_attestation,
        attestation_name="Historical Owner",
    ).save()
    legacy_member = PracticeMember(
        practice_id=practice.id,
        doctor_id=unrostered_member.id,
        role="workforce",
        attested_at=historical_attestation,
        attestation_name="Historical Workforce Member",
    ).save()
    _approve(practice, rostered_owner.email or "", now=now)

    # Linking a practice to an organization immediately makes a legacy
    # attestation insufficient unless an exact roster authorization is added.
    assert member_authorization_basis(legacy_member, at=now) is None
    assert not member_access_in_force(legacy_member, at=now)
    assert find_access_opportunity(unrostered_member, at=now) is None
    with pytest.raises(OrganizationAccessError, match="not available"):
        claim_roster_access(unrostered_member, supplied_code=code, now=now)

    _, claimed_member = claim_roster_access(
        rostered_owner,
        supplied_code=code,
        now=now + timedelta(seconds=1),
    )
    assert claimed_member.id == owner_member.id
    assert claimed_member.role == "owner"
    assert claimed_member.attested_at == historical_attestation
    assert claimed_member.attestation_name == "Historical Owner"
    assert (
        member_authorization_basis(
            claimed_member,
            at=now + timedelta(seconds=1),
        )
        == "organization_roster"
    )


def test_claimed_roster_access_fails_closed_after_doctor_email_changes():
    now = datetime(2026, 8, 22, 16, 30, tzinfo=timezone.utc)
    domain = f"pilot-{secrets.token_hex(4)}.example.com"
    doctor = _doctor(f"original@{domain}")
    practice, code = _configured_practice(domain, now=now)
    _approve(practice, doctor.email or "", now=now)
    _, member = claim_roster_access(
        doctor,
        supplied_code=code,
        now=now + timedelta(seconds=1),
    )
    assert member_access_in_force(member, at=now + timedelta(seconds=1))

    doctor.email = f"changed@{domain}"
    doctor.save()

    assert member_authorization_basis(member, at=now + timedelta(seconds=2)) is None
    assert not member_access_in_force(member, at=now + timedelta(seconds=2))


def test_offboarding_revokes_access_and_retains_authorization_records_for_seven_years():
    approved_at = datetime(2028, 2, 1, 9, 0, tzinfo=timezone.utc)
    revoked_at = datetime(2028, 2, 29, 16, 30, tzinfo=timezone.utc)
    expected_retain_until = datetime(2035, 2, 28, 16, 30, tzinfo=timezone.utc)
    domain = f"pilot-{secrets.token_hex(4)}.example.com"
    doctor = _doctor(f"departing@{domain}")
    practice, code = _configured_practice(domain, now=approved_at)
    _approve(practice, doctor.email or "", now=approved_at)
    _, member = claim_roster_access(
        doctor,
        supplied_code=code,
        now=approved_at + timedelta(minutes=1),
    )
    approval_id = member.organization_roster_approval_id
    assert approval_id is not None

    revoked_member, retain_until, audit = offboard_member(
        practice,
        doctor,
        operator_ref="ticket-OFFBOARD-7",
        reason="Provider left the pilot organization",
        now=revoked_at,
    )

    assert retain_until == expected_retain_until
    assert revoked_member.access_revoked_at == revoked_at
    assert revoked_member.access_records_retain_until == expected_retain_until
    assert not member_access_in_force(revoked_member, at=revoked_at)
    assert member_authorization_basis(revoked_member, at=revoked_at) is None

    persisted_member = PracticeMember.get(id=member.id)
    persisted_approval = OrganizationRosterApproval.get(id=approval_id)
    persisted_doctor = Doctor.get(id=doctor.id)
    assert persisted_member is not None
    assert persisted_approval is not None
    assert persisted_approval.revoked_at == revoked_at
    assert persisted_approval.retain_until == expected_retain_until
    assert persisted_doctor is not None
    assert persisted_doctor.deleted_at == revoked_at
    assert audit.action == PracticeAccessAction.organization_access_revoked.value
    member_id = member.id
    audit_id = audit.id

    retry_member, retry_retain_until, retry_audit = offboard_member(
        practice,
        doctor,
        operator_ref="ticket-OFFBOARD-7",
        reason="Provider left the pilot organization",
        now=revoked_at + timedelta(days=1),
    )
    assert retry_member.id == member_id
    assert retry_retain_until == expected_retain_until
    assert retry_audit.id == audit_id
    assert (
        PracticeAccessAudit.where(
            PracticeAccessAudit.practice_id == practice.id,
            PracticeAccessAudit.action
            == PracticeAccessAction.organization_access_revoked.value,
        ).count()
        == 1
    )


def test_offboarding_rolls_back_when_seat_reconciliation_cannot_be_recorded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2028, 2, 1, 9, 0, tzinfo=timezone.utc)
    domain = f"atomic-offboard-{secrets.token_hex(4)}.example.com"
    doctor = _doctor(f"departing@{domain}")
    practice, code = _configured_practice(domain, now=now)
    _approve(practice, doctor.email or "", now=now)
    _, member = claim_roster_access(
        doctor,
        supplied_code=code,
        now=now + timedelta(minutes=1),
    )
    approval_id = member.organization_roster_approval_id
    assert approval_id is not None
    practice.stripe_subscription_id = f"sub_atomic_{secrets.token_hex(5)}"
    practice.save()

    def fail_record(*_args, **_kwargs) -> int:
        raise RuntimeError("synthetic seat-ledger failure")

    monkeypatch.setattr(
        organization_access,
        "record_active_clinician_seat_quantity",
        fail_record,
    )

    with pytest.raises(RuntimeError, match="synthetic seat-ledger failure"):
        revoke_roster_access(
            practice,
            doctor.email or "",
            operator_ref="ticket-ATOMIC-OFFBOARD",
            reason="Exercise atomic seat evidence",
            now=now + timedelta(days=1),
        )

    member.refresh()
    approval = OrganizationRosterApproval.get(id=approval_id)
    assert approval is not None
    assert member.access_revoked_at is None
    assert approval.revoked_at is None
    assert (
        PracticeAccessAudit.where(
            PracticeAccessAudit.practice_id == practice.id,
            PracticeAccessAudit.action
            == PracticeAccessAction.organization_access_revoked.value,
        ).count()
        == 0
    )


def test_offboarding_retry_repairs_legacy_post_commit_seat_queue_gap() -> None:
    approved_at = datetime(2025, 2, 1, 9, 0, tzinfo=timezone.utc)
    revoked_at = approved_at + timedelta(days=1)
    domain = f"retry-seat-repair-{secrets.token_hex(4)}.example.com"
    doctor = _doctor(f"departing@{domain}")
    practice, code = _configured_practice(domain, now=approved_at)
    _approve(practice, doctor.email or "", now=approved_at)
    _, member = claim_roster_access(
        doctor,
        supplied_code=code,
        now=approved_at + timedelta(minutes=1),
    )
    practice.stripe_subscription_id = f"sub_retry_{secrets.token_hex(5)}"
    practice.save()

    first = revoke_roster_access(
        practice,
        doctor.email or "",
        operator_ref="ticket-RETRY-SEAT-REPAIR",
        reason="Exercise legacy post-commit repair",
        now=revoked_at,
    )
    practice.refresh()
    first_version = practice.stripe_quantity_sync_version
    assert practice.stripe_quantity_sync_required is True
    assert practice.stripe_quantity_sync_target is None

    # Emulate an older release committing the revocation before its
    # best-effort Stripe queue marker was durably written.
    practice.stripe_quantity_sync_required = False
    practice.stripe_quantity_sync_target = None
    practice.stripe_quantity_sync_error = None
    practice.stripe_quantity_synced_at = revoked_at + timedelta(minutes=1)
    practice.save()

    assert first.audit.created_at is not None
    assert practice.stripe_quantity_synced_at < first.audit.created_at

    repaired = revoke_roster_access(
        practice,
        doctor.email or "",
        operator_ref="ticket-RETRY-SEAT-REPAIR",
        reason="Exercise legacy post-commit repair",
        now=revoked_at + timedelta(hours=1),
    )
    practice.refresh()
    assert repaired.audit.id == first.audit.id
    assert repaired.member is not None
    assert repaired.member.id == member.id
    assert practice.stripe_quantity_sync_required is True
    assert practice.stripe_quantity_sync_target is None
    assert practice.stripe_quantity_sync_version == first_version + 1
    assert practice.stripe_quantity_sync_error == (
        "active clinician seat count is zero; subscription cancellation required"
    )

    revoke_roster_access(
        practice,
        doctor.email or "",
        operator_ref="ticket-RETRY-SEAT-REPAIR",
        reason="Exercise legacy post-commit repair",
        now=revoked_at + timedelta(hours=2),
    )
    practice.refresh()
    assert practice.stripe_quantity_sync_version == first_version + 1


def test_revoked_roster_history_is_a_global_authorization_tombstone():
    approved_at = datetime(2028, 3, 1, 9, 0, tzinfo=timezone.utc)
    revoked_at = approved_at + timedelta(days=1)
    domain = f"tombstone-{secrets.token_hex(4)}.example.com"
    doctor = _doctor(f"returning@{domain}")
    practice, code = _configured_practice(domain, now=approved_at)
    _approve(practice, doctor.email or "", now=approved_at)
    revoke_roster_access(
        practice,
        doctor.email or "",
        operator_ref="ticket-TOMBSTONE",
        reason="Exercise historical revocation precedence",
        now=revoked_at,
    )

    organization = ProviderOrganization.get(id=practice.organization_id)
    assert organization is not None
    conflicting_active = OrganizationRosterApproval(
        organization_id=organization.id,
        target_practice_id=practice.id,
        approved_email=doctor.email or "",
        approved_at=revoked_at + timedelta(seconds=1),
        approved_by_ref="test-corrupt-state",
        source_ref="test://unauthorized-reauthorization",
        source_sha256="e" * 64,
        claimed_doctor_id=doctor.id,
        claimed_at=revoked_at + timedelta(seconds=2),
        claimed_code_version=organization.access_code_version,
    ).save()
    member = PracticeMember(
        practice_id=practice.id,
        doctor_id=doctor.id,
        role="workforce",
        organization_roster_approval_id=conflicting_active.id,
        organization_authorized_at=revoked_at + timedelta(seconds=2),
    ).save()

    assert find_access_opportunity(doctor, at=revoked_at + timedelta(seconds=3)) is None
    assert (
        member_authorization_basis(
            member,
            at=revoked_at + timedelta(seconds=3),
        )
        is None
    )
    with pytest.raises(OrganizationAccessError, match="not available"):
        claim_roster_access(
            doctor,
            supplied_code=code,
            now=revoked_at + timedelta(seconds=3),
        )


def test_database_guards_revoked_organization_access_evidence_for_seven_years():
    approved_at = datetime(2028, 2, 1, 9, 0, tzinfo=timezone.utc)
    revoked_at = datetime(2028, 2, 2, 9, 0, tzinfo=timezone.utc)
    domain = f"pilot-{secrets.token_hex(4)}.example.com"
    doctor = _doctor(f"retained@{domain}")
    practice, code = _configured_practice(domain, now=approved_at)
    _approve(practice, doctor.email or "", now=approved_at)
    _, member = claim_roster_access(
        doctor,
        supplied_code=code,
        now=approved_at + timedelta(minutes=1),
    )
    approval_id = member.organization_roster_approval_id
    assert approval_id is not None

    active_delete_statements = (
        (
            "DELETE FROM organization_roster_approval WHERE id = :row_id",
            approval_id.uuid,
            "organization roster approval evidence is still retained",
        ),
        (
            "DELETE FROM practice_member WHERE id = :row_id",
            member.id.uuid,
            "organization membership evidence is still retained",
        ),
    )
    for statement, row_id, expected_error in active_delete_statements:
        with get_session() as session:
            savepoint = session.begin_nested()
            with pytest.raises(DBAPIError, match=expected_error):
                session.execute(text(statement), params={"row_id": row_id})
            savepoint.rollback()

    offboard_member(
        practice,
        doctor,
        operator_ref="ticket-RETAIN-GUARD",
        reason="Exercise database retention guard",
        now=revoked_at,
    )

    blocked_statements = (
        (
            "UPDATE organization_roster_approval "
            "SET source_ref = 'changed' WHERE id = :row_id",
            approval_id.uuid,
            "revoked organization roster approval evidence is immutable",
        ),
        (
            "DELETE FROM organization_roster_approval WHERE id = :row_id",
            approval_id.uuid,
            "organization roster approval evidence is still retained",
        ),
        (
            "UPDATE practice_member SET role = 'owner' WHERE id = :row_id",
            member.id.uuid,
            "revoked organization membership evidence is immutable",
        ),
        (
            "DELETE FROM practice_member WHERE id = :row_id",
            member.id.uuid,
            "organization membership evidence is still retained",
        ),
    )
    for statement, row_id, expected_error in blocked_statements:
        with get_session() as session:
            savepoint = session.begin_nested()
            with pytest.raises(DBAPIError, match=expected_error):
                session.execute(text(statement), params={"row_id": row_id})
            savepoint.rollback()

    assert OrganizationRosterApproval.get(id=approval_id) is not None
    assert PracticeMember.get(id=member.id) is not None


def test_unclaimed_roster_approval_can_be_revoked_retained_and_retried():
    approved_at = datetime(2028, 2, 1, 9, 0, tzinfo=timezone.utc)
    revoked_at = datetime(2028, 2, 29, 16, 30, tzinfo=timezone.utc)
    expected_retain_until = datetime(2035, 2, 28, 16, 30, tzinfo=timezone.utc)
    domain = f"pilot-{secrets.token_hex(4)}.example.com"
    doctor = _doctor(f"never-claimed@{domain}")
    practice, _ = _configured_practice(domain, now=approved_at)
    imported = _approve(practice, doctor.email or "", now=approved_at)
    approval_id = imported.approval_ids[0]
    approval = OrganizationRosterApproval.get(id=approval_id)
    assert approval is not None
    assert approval.claimed_doctor_id is None
    assert PracticeMember.get(doctor_id=doctor.id) is None

    result = revoke_roster_access(
        practice,
        doctor.email or "",
        operator_ref="ticket-UNCLAIMED-OFFBOARD",
        reason="Removed before first login",
        now=revoked_at,
    )

    assert result.member is None
    assert result.doctor is None
    assert result.retain_until == expected_retain_until
    assert result.approval.id == approval.id
    assert result.approval.revoked_at == revoked_at
    assert result.approval.retain_until == expected_retain_until
    assert result.audit.action == PracticeAccessAction.organization_access_revoked.value
    assert result.audit.document_reference == str(approval.id)
    assert OrganizationRosterApproval.get(id=approval.id) is not None
    assert find_access_opportunity(doctor, at=revoked_at) is None
    audit_id = result.audit.id

    retry = revoke_roster_access(
        practice,
        doctor.email or "",
        operator_ref="ticket-UNCLAIMED-OFFBOARD",
        reason="Removed before first login",
        now=revoked_at + timedelta(days=1),
    )
    assert retry.member is None
    assert retry.doctor is None
    assert retry.retain_until == expected_retain_until
    assert retry.audit.id == audit_id
    assert (
        PracticeAccessAudit.where(
            PracticeAccessAudit.practice_id == practice.id,
            PracticeAccessAudit.action
            == PracticeAccessAction.organization_access_revoked.value,
            PracticeAccessAudit.document_reference == str(approval.id),
        ).count()
        == 1
    )

    with pytest.raises(OrganizationAccessError, match="explicit reauthorization"):
        _approve(
            practice,
            doctor.email or "",
            now=revoked_at + timedelta(days=2),
        )
    persisted_approvals = cast(
        list[OrganizationRosterApproval],
        list(
            OrganizationRosterApproval.where(
                OrganizationRosterApproval.organization_id == practice.organization_id,
                OrganizationRosterApproval.target_practice_id == practice.id,
                OrganizationRosterApproval.approved_email == doctor.email,
            ).all()
        ),
    )
    assert len(persisted_approvals) == 1
    assert persisted_approvals[0].id == approval.id
    assert persisted_approvals[0].revoked_at == revoked_at


def test_multi_practice_configuration_is_atomic_and_routes_explicit_assignments():
    now = datetime(2028, 3, 1, 10, 0, tzinfo=timezone.utc)
    domain = f"multi-{secrets.token_hex(4)}.example.com"
    first = _unlinked_pilot_practice(domain, "First", now=now)
    second = _unlinked_pilot_practice(domain, "Second", now=now)
    first_owner = _doctor(f"first.owner@{domain}")
    second_owner = _doctor(f"second.owner@{domain}")
    new_workforce = _doctor(f"new.workforce@{domain}")
    historical_attestation = now - timedelta(days=90)
    first_owner_member = PracticeMember(
        practice_id=first.id,
        doctor_id=first_owner.id,
        role="owner",
        attested_at=historical_attestation,
        attestation_name="First Historical Owner",
    ).save()
    second_owner_member = PracticeMember(
        practice_id=second.id,
        doctor_id=second_owner.id,
        role="owner",
        attested_at=historical_attestation,
        attestation_name="Second Historical Owner",
    ).save()
    assignments = [
        RosterAssignmentInput(first_owner.email or "", first.id),
        RosterAssignmentInput(second_owner.email or "", second.id),
        RosterAssignmentInput(new_workforce.email or "", second.id),
    ]
    slug = f"multi-{secrets.token_hex(5)}"

    validate_provider_organization_with_roster(
        [second, first],
        assignments,
        legal_name="Multi Practice Provider",
        slug=slug,
        email_domain=domain,
        operator_ref="ticket-MULTI-PLAN",
        reason="Validate the complete multi-practice cutover",
        source_ref="test://multi-practice-roster-v2",
        source_sha256="d" * 64,
        now=now,
    )
    first.refresh()
    second.refresh()
    assert first.organization_id is None
    assert second.organization_id is None
    assert ProviderOrganization.get(slug=slug) is None

    plan_sha256 = provider_organization_with_roster_plan_sha256(
        assignments,
        organization_slug=slug,
        email_domain=domain,
        legal_name="Multi Practice Provider",
        operator_ref="ticket-MULTI-APPLY",
        reason="Apply the complete multi-practice cutover",
        source_ref="test://multi-practice-roster-v2",
        roster_sha256="d" * 64,
    )
    for rejected_plan, message in (
        ("not-a-digest", "64-character hex"),
        ("0" * 64, "does not match"),
    ):
        with pytest.raises(OrganizationAccessError, match=message):
            configure_provider_organization_with_roster(
                [second, first],
                assignments,
                legal_name="Multi Practice Provider",
                slug=slug,
                email_domain=domain,
                operator_ref="ticket-MULTI-APPLY",
                reason="Apply the complete multi-practice cutover",
                source_ref="test://multi-practice-roster-v2",
                source_sha256="d" * 64,
                plan_sha256=rejected_plan,
                now=now,
            )
        assert ProviderOrganization.get(slug=slug) is None

    organization, code, roster, configuration_audits = (
        configure_provider_organization_with_roster(
            [second, first],
            assignments,
            legal_name="Multi Practice Provider",
            slug=slug,
            email_domain=domain,
            operator_ref="ticket-MULTI-APPLY",
            reason="Apply the complete multi-practice cutover",
            source_ref="test://multi-practice-roster-v2",
            source_sha256="d" * 64,
            plan_sha256=plan_sha256,
            now=now,
        )
    )

    first.refresh()
    second.refresh()
    assert first.organization_id == second.organization_id == organization.id
    assert roster.created == 3
    assert roster.unchanged == 0
    assert len(roster.approval_ids) == 3
    assert {audit.practice_id for audit in configuration_audits} == {
        first.id,
        second.id,
    }
    assert all(
        audit.action == PracticeAccessAction.provider_organization_configured.value
        for audit in configuration_audits
    )
    assert all(audit.evidence_sha256 == plan_sha256 for audit in configuration_audits)
    approvals = cast(
        list[OrganizationRosterApproval],
        list(
            OrganizationRosterApproval.where(
                OrganizationRosterApproval.organization_id == organization.id
            ).all()
        ),
    )
    targets = {
        approval.approved_email: approval.target_practice_id for approval in approvals
    }
    assert targets == {
        first_owner.email: first.id,
        second_owner.email: second.id,
        new_workforce.email: second.id,
    }
    for practice in (first, second):
        assert (
            PracticeAccessAudit.where(
                PracticeAccessAudit.practice_id == practice.id,
                PracticeAccessAudit.action
                == PracticeAccessAction.organization_roster_imported.value,
                PracticeAccessAudit.evidence_sha256 == "d" * 64,
            ).count()
            == 1
        )

    claimed_first, first_member = claim_roster_access(
        first_owner,
        supplied_code=code,
        now=now + timedelta(seconds=1),
    )
    claimed_second, second_member = claim_roster_access(
        second_owner,
        supplied_code=code,
        now=now + timedelta(seconds=1),
    )
    assert claimed_first.id == first.id
    assert claimed_second.id == second.id
    assert first_member.id == first_owner_member.id
    assert second_member.id == second_owner_member.id
    assert first_member.role == second_member.role == "owner"
    assert first_member.attested_at == historical_attestation
    assert second_member.attested_at == historical_attestation

    rotated_code, rotation_audits = rotate_access_code(
        organization,
        operator_ref="ticket-MULTI-ROTATE",
        reason="Rotate the shared enrollment secret",
        now=now + timedelta(seconds=2),
    )
    assert rotated_code != code
    assert {audit.practice_id for audit in rotation_audits} == {
        first.id,
        second.id,
    }
    with pytest.raises(OrganizationAccessError, match="not available"):
        claim_roster_access(
            new_workforce,
            supplied_code=code,
            now=now + timedelta(seconds=3),
        )
    claimed_workforce_practice, workforce_member = claim_roster_access(
        new_workforce,
        supplied_code=rotated_code,
        now=now + timedelta(seconds=3),
    )
    assert claimed_workforce_practice.id == second.id
    assert workforce_member.role == "workforce"


def test_multi_practice_configuration_rejects_public_onboarding_practice() -> None:
    now = datetime(2028, 3, 1, 10, 30, tzinfo=timezone.utc)
    domain = f"multi-public-{secrets.token_hex(4)}.example.com"
    first = _unlinked_pilot_practice(domain, "First", now=now)
    second = _unlinked_pilot_practice(domain, "Second", now=now)
    second.public_onboarding_required = True
    second.save()
    first_owner = _doctor(f"first.owner@{domain}")
    second_owner = _doctor(f"second.owner@{domain}")
    PracticeMember(
        practice_id=first.id,
        doctor_id=first_owner.id,
        role="owner",
    ).save()
    PracticeMember(
        practice_id=second.id,
        doctor_id=second_owner.id,
        role="owner",
    ).save()
    assignments = [
        RosterAssignmentInput(first_owner.email or "", first.id),
        RosterAssignmentInput(second_owner.email or "", second.id),
    ]
    slug = f"multi-public-{secrets.token_hex(5)}"
    plan_sha256 = provider_organization_with_roster_plan_sha256(
        assignments,
        organization_slug=slug,
        email_domain=domain,
        legal_name="Public Conversion Organization",
        operator_ref="ticket-MULTI-PUBLIC",
        reason="Reject an implicit public-to-managed conversion",
        source_ref="test://multi-public-roster-v2",
        roster_sha256="f" * 64,
    )

    with pytest.raises(OrganizationAccessError, match="audited conversion"):
        configure_provider_organization_with_roster(
            [first, second],
            assignments,
            legal_name="Public Conversion Organization",
            slug=slug,
            email_domain=domain,
            operator_ref="ticket-MULTI-PUBLIC",
            reason="Reject an implicit public-to-managed conversion",
            source_ref="test://multi-public-roster-v2",
            source_sha256="f" * 64,
            plan_sha256=plan_sha256,
            now=now,
        )

    first.refresh()
    second.refresh()
    assert first.organization_id is None
    assert second.organization_id is None
    assert ProviderOrganization.get(slug=slug) is None
    assert (
        PracticeAccessAudit.where(
            PracticeAccessAudit.practice_id.in_([first.id, second.id]),
            PracticeAccessAudit.action
            == PracticeAccessAction.provider_organization_configured.value,
        ).count()
        == 0
    )


@pytest.mark.parametrize(
    "failure",
    ["omitted-member", "cross-target-member", "omitted-unattached-account"],
)
def test_multi_practice_configuration_rolls_back_incomplete_or_moved_members(failure):
    now = datetime(2028, 3, 1, 11, 0, tzinfo=timezone.utc)
    domain = f"multi-invalid-{secrets.token_hex(4)}.example.com"
    first = _unlinked_pilot_practice(domain, "First", now=now)
    second = _unlinked_pilot_practice(domain, "Second", now=now)
    first_owner = _doctor(f"first.owner@{domain}")
    second_owner = _doctor(f"second.owner@{domain}")
    PracticeMember(
        practice_id=first.id,
        doctor_id=first_owner.id,
        role="owner",
    ).save()
    PracticeMember(
        practice_id=second.id,
        doctor_id=second_owner.id,
        role="owner",
    ).save()
    assignments = [
        RosterAssignmentInput(first_owner.email or "", first.id),
        RosterAssignmentInput(second_owner.email or "", second.id),
    ]
    if failure == "omitted-member":
        assignments.pop()
    elif failure == "cross-target-member":
        assignments[0] = RosterAssignmentInput(first_owner.email or "", second.id)
    else:
        _doctor(f"omitted.unattached@{domain}")
    slug = f"multi-invalid-{secrets.token_hex(5)}"
    plan_sha256 = provider_organization_with_roster_plan_sha256(
        assignments,
        organization_slug=slug,
        email_domain=domain,
        legal_name="Invalid Multi Practice Provider",
        operator_ref="ticket-MULTI-INVALID",
        reason="Prove invalid placement is atomic",
        source_ref="test://invalid-multi-practice-roster-v2",
        roster_sha256="e" * 64,
    )

    with pytest.raises(
        OrganizationAccessError,
        match=(
            "current target practice|different target practice|owner|"
            "active organization-domain account"
        ),
    ):
        configure_provider_organization_with_roster(
            [first, second],
            assignments,
            legal_name="Invalid Multi Practice Provider",
            slug=slug,
            email_domain=domain,
            operator_ref="ticket-MULTI-INVALID",
            reason="Prove invalid placement is atomic",
            source_ref="test://invalid-multi-practice-roster-v2",
            source_sha256="e" * 64,
            plan_sha256=plan_sha256,
            now=now,
        )

    first.refresh()
    second.refresh()
    assert first.organization_id is None
    assert second.organization_id is None
    assert ProviderOrganization.get(slug=slug) is None
    assert (
        OrganizationRosterApproval.where(
            OrganizationRosterApproval.source_sha256 == "e" * 64
        ).count()
        == 0
    )


def test_revocation_retry_is_scoped_to_the_requested_sibling_practice():
    now = datetime(2028, 3, 1, 12, 0, tzinfo=timezone.utc)
    domain = f"multi-revoke-{secrets.token_hex(4)}.example.com"
    first = _unlinked_pilot_practice(domain, "First", now=now)
    second = _unlinked_pilot_practice(domain, "Second", now=now)
    first_owner = _doctor(f"first.owner@{domain}")
    second_owner = _doctor(f"second.owner@{domain}")
    PracticeMember(
        practice_id=first.id,
        doctor_id=first_owner.id,
        role="owner",
    ).save()
    PracticeMember(
        practice_id=second.id,
        doctor_id=second_owner.id,
        role="owner",
    ).save()
    transfer_email = f"transfer@{domain}"
    assignments = [
        RosterAssignmentInput(first_owner.email or "", first.id),
        RosterAssignmentInput(second_owner.email or "", second.id),
        RosterAssignmentInput(transfer_email, first.id),
    ]
    slug = f"multi-revoke-{secrets.token_hex(5)}"
    plan_sha256 = provider_organization_with_roster_plan_sha256(
        assignments,
        organization_slug=slug,
        email_domain=domain,
        legal_name="Multi Revoke Provider",
        operator_ref="ticket-MULTI-REVOKE",
        reason="Prepare a sibling-practice revocation test",
        source_ref="test://multi-revoke-roster-v2",
        roster_sha256="f" * 64,
    )
    organization, _, _, _ = configure_provider_organization_with_roster(
        [first, second],
        assignments,
        legal_name="Multi Revoke Provider",
        slug=slug,
        email_domain=domain,
        operator_ref="ticket-MULTI-REVOKE",
        reason="Prepare a sibling-practice revocation test",
        source_ref="test://multi-revoke-roster-v2",
        source_sha256="f" * 64,
        plan_sha256=plan_sha256,
        now=now,
    )
    first_revocation = revoke_roster_access(
        first,
        transfer_email,
        operator_ref="ticket-TRANSFER-FIRST",
        reason="Remove unclaimed first-practice assignment",
        now=now + timedelta(seconds=1),
    )
    second_import = import_roster_approvals(
        second,
        [RosterApprovalInput(email=transfer_email)],
        operator_ref="ticket-TRANSFER-SECOND",
        reason="Explicitly reassign after revocation",
        source_ref="test://multi-revoke-second-v1",
        source_sha256="1" * 64,
        now=now + timedelta(seconds=2),
    )
    assert second_import.created == 1

    retry = revoke_roster_access(
        first,
        transfer_email,
        operator_ref="ticket-TRANSFER-FIRST",
        reason="Remove unclaimed first-practice assignment",
        now=now + timedelta(seconds=3),
    )
    assert retry.approval.id == first_revocation.approval.id
    active_second = OrganizationRosterApproval.get(id=second_import.approval_ids[0])
    assert active_second is not None
    assert active_second.organization_id == organization.id
    assert active_second.target_practice_id == second.id
    assert active_second.revoked_at is None

    from typer.testing import CliRunner

    from app.cli import app

    dry_run = CliRunner().invoke(
        app,
        [
            "revoke-organization-roster-access",
            "--practice-id",
            str(first.id),
            "--approved-email",
            transfer_email,
            "--operator-ref",
            "ticket-TRANSFER-DRY-RUN",
            "--reason",
            "Verify dry-run stays in the selected practice",
        ],
    )
    assert dry_run.exit_code == 0, dry_run.output
    assert "already revoked" in dry_run.output
    assert str(first_revocation.approval.id) in dry_run.output
    assert str(active_second.id) not in dry_run.output


def test_offboarding_last_owner_requires_an_authorized_successor():
    now = datetime(2028, 3, 1, 13, 0, tzinfo=timezone.utc)
    domain = f"last-owner-{secrets.token_hex(4)}.example.com"
    owner = _doctor(f"owner@{domain}")
    practice, code = _configured_practice(domain, now=now)
    PracticeMember(
        practice_id=practice.id,
        doctor_id=owner.id,
        role="owner",
    ).save()
    _approve(practice, owner.email or "", now=now)
    _, owner_member = claim_roster_access(
        owner,
        supplied_code=code,
        now=now + timedelta(seconds=1),
    )

    with pytest.raises(OrganizationAccessError, match="last active practice owner"):
        offboard_member(
            practice,
            owner,
            operator_ref="ticket-LAST-OWNER",
            reason="Attempt owner offboarding without a successor",
            now=now + timedelta(seconds=2),
        )
    owner_member.refresh()
    owner.refresh()
    assert owner_member.access_revoked_at is None
    assert owner.deleted_at is None

    successor = _doctor(f"successor@{domain}")
    PracticeMember(
        practice_id=practice.id,
        doctor_id=successor.id,
        role="owner",
    ).save()
    _approve(
        practice,
        successor.email or "",
        now=now + timedelta(seconds=3),
        source_sha256="2" * 64,
    )
    claim_roster_access(
        successor,
        supplied_code=code,
        now=now + timedelta(seconds=4),
    )
    original_successor_email = successor.email
    successor.email = f"changed@{domain}"
    successor.save()

    with pytest.raises(OrganizationAccessError, match="last active practice owner"):
        offboard_member(
            practice,
            owner,
            operator_ref="ticket-LAST-OWNER",
            reason="Reject a successor whose current email no longer matches",
            now=now + timedelta(seconds=5),
        )
    owner_member.refresh()
    assert owner_member.access_revoked_at is None

    successor.email = original_successor_email
    successor.save()
    revoked_member, _, _ = offboard_member(
        practice,
        owner,
        operator_ref="ticket-LAST-OWNER",
        reason="Offboard after an authorized successor exists",
        now=now + timedelta(seconds=6),
    )
    assert revoked_member.access_revoked_at == now + timedelta(seconds=6)
