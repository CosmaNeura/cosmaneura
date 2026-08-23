"""Provider-organization roster access stays distinct from BAA attestation."""

import secrets
from datetime import datetime, timedelta, timezone
from typing import cast

import pytest

from app.services.organization_access import (
    OrganizationAccessError,
    RosterApprovalInput,
    claim_roster_access,
    configure_provider_organization,
    find_access_opportunity,
    import_roster_approvals,
    member_access_in_force,
    member_authorization_basis,
    offboard_member,
    revoke_roster_access,
)

from app.models.baa import BaaAcceptance
from app.models.doctor import Doctor
from app.models.organization_access import OrganizationRosterApproval
from app.models.practice import Practice, PracticeMember
from app.models.practice_access_audit import (
    PracticeAccessAction,
    PracticeAccessAudit,
)


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
        email_domain=domain,
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
