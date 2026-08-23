import secrets
from datetime import datetime, timezone

import pytest

import app.services.practice_bootstrap as practice_bootstrap_service
from app.services.organization_access import (
    RosterApprovalInput,
    member_authorization_basis,
)
from app.services.practice_bootstrap import (
    ProviderPracticeBootstrapError,
    bootstrap_provider_practice,
    plan_provider_practice_bootstrap,
)

from app.models.baa import BaaAcceptance
from app.models.doctor import Doctor
from app.models.practice import Practice, PracticeMember
from app.models.practice_access_audit import (
    PracticeAccessAction,
    PracticeAccessAudit,
)


def _doctor(email: str, *, onboarded: bool = True) -> Doctor:
    return Doctor(
        clerk_id=f"user_bootstrap_{secrets.token_hex(8)}",
        email=email,
        biography="",
        onboarding_completed_at=(
            datetime(2026, 1, 1, tzinfo=timezone.utc) if onboarded else None
        ),
    ).save()


def _inputs(domain: str):
    owner = _doctor(f"owner@{domain}")
    workforce = _doctor(f"workforce@{domain}")
    entries = [
        RosterApprovalInput(email=owner.email or ""),
        RosterApprovalInput(email=workforce.email or ""),
    ]
    values = {
        "entries": entries,
        "roster_sha256": "a" * 64,
        "owner_doctor_id": owner.id,
        "owner_email_confirmation": owner.email or "",
        "verified_primary_email": owner.email,
        "legal_name": "Fiat Integrative Health, PLLC",
        "email_domain": domain,
        "bootstrap_ref": f"bootstrap:{secrets.token_hex(8)}",
        "operator_ref": "change:provider-bootstrap-test",
        "reason": "Create the approved pilot practice shell",
    }
    return owner, workforce, values


def test_bootstrap_creates_closed_owner_practice_and_is_idempotent():
    domain = f"bootstrap-{secrets.token_hex(4)}.example.com"
    owner, workforce, values = _inputs(domain)

    plan = plan_provider_practice_bootstrap(**values)
    result = bootstrap_provider_practice(
        **values,
        confirm_roster_sha256=plan.roster_sha256,
        confirm_plan_sha256=plan.plan_sha256,
        now=datetime(2026, 8, 23, 14, 0, tzinfo=timezone.utc),
    )

    assert not result.already_applied
    assert result.practice.legal_name == values["legal_name"]
    assert result.practice.email_domain == domain
    assert result.practice.negotiated_baa_required is True
    assert result.practice.is_solo is False
    assert result.practice.origin_channel == "direct"
    assert result.practice.pastoral_pack is False
    assert result.practice.organization_id is None
    assert result.practice.baa_accepted_at is None
    assert result.practice.baa_accepted_by_doctor_id is None
    assert result.practice.baa_signature_name is None
    assert result.practice.baa_signature_title is None
    assert result.practice.trial_ends_at is None
    assert result.practice.negotiated_baa_on_file is False
    assert result.practice.billing_exempt is False
    assert result.practice.stripe_customer_id is None
    assert result.practice.stripe_subscription_id is None
    assert result.owner_member.doctor_id == owner.id
    assert result.owner_member.role == "owner"
    assert result.owner_member.attested_at is None
    assert result.owner_member.organization_roster_approval_id is None
    assert result.owner_member.organization_authorized_at is None
    assert member_authorization_basis(result.owner_member) is None
    assert PracticeMember.get(doctor_id=workforce.id) is None
    assert (
        BaaAcceptance.where(BaaAcceptance.practice_id == result.practice.id).count()
        == 0
    )
    assert result.audit.action == (
        PracticeAccessAction.provider_practice_bootstrapped.value
    )
    assert result.audit.document_reference == values["bootstrap_ref"]
    assert result.audit.evidence_sha256 == plan.plan_sha256

    retry = bootstrap_provider_practice(
        **values,
        confirm_roster_sha256=plan.roster_sha256,
        confirm_plan_sha256=plan.plan_sha256,
    )
    assert retry.already_applied
    assert retry.practice.id == result.practice.id
    assert retry.owner_member.id == result.owner_member.id
    assert retry.audit.id == result.audit.id
    assert Practice.where(Practice.email_domain == domain).count() == 1
    assert (
        PracticeAccessAudit.where(
            PracticeAccessAudit.action
            == PracticeAccessAction.provider_practice_bootstrapped.value,
            PracticeAccessAudit.document_reference == values["bootstrap_ref"],
        ).count()
        == 1
    )


def test_bootstrap_confirmation_mismatch_writes_nothing():
    domain = f"confirm-{secrets.token_hex(4)}.example.com"
    _, _, values = _inputs(domain)
    plan = plan_provider_practice_bootstrap(**values)

    with pytest.raises(ProviderPracticeBootstrapError, match="confirmed plan"):
        bootstrap_provider_practice(
            **values,
            confirm_roster_sha256=plan.roster_sha256,
            confirm_plan_sha256="b" * 64,
        )

    assert Practice.where(Practice.email_domain == domain).count() == 0
    assert (
        PracticeAccessAudit.where(
            PracticeAccessAudit.document_reference == values["bootstrap_ref"]
        ).count()
        == 0
    )


def test_bootstrap_requires_exact_verified_rostered_owner():
    domain = f"owner-{secrets.token_hex(4)}.example.com"
    _, _, values = _inputs(domain)

    with pytest.raises(ProviderPracticeBootstrapError, match="Clerk primary"):
        plan_provider_practice_bootstrap(
            **(values | {"verified_primary_email": f"other@{domain}"})
        )
    with pytest.raises(ProviderPracticeBootstrapError, match="exact roster"):
        plan_provider_practice_bootstrap(
            **(
                values
                | {
                    "owner_email_confirmation": f"missing@{domain}",
                    "verified_primary_email": f"missing@{domain}",
                }
            )
        )
    assert Practice.where(Practice.email_domain == domain).count() == 0


def test_bootstrap_rejects_roster_accounts_from_a_subdomain():
    domain = f"exact-domain-{secrets.token_hex(4)}.example.com"
    _, _, values = _inputs(domain)
    values["entries"] = [
        values["entries"][0],
        RosterApprovalInput(email=f"provider@sub.{domain}"),
    ]

    with pytest.raises(ProviderPracticeBootstrapError, match="practice domain"):
        plan_provider_practice_bootstrap(**values)

    assert Practice.where(Practice.email_domain == domain).count() == 0


def test_bootstrap_ref_cannot_be_reused_for_a_changed_plan():
    domain = f"ref-{secrets.token_hex(4)}.example.com"
    _, _, values = _inputs(domain)
    plan = plan_provider_practice_bootstrap(**values)
    bootstrap_provider_practice(
        **values,
        confirm_roster_sha256=plan.roster_sha256,
        confirm_plan_sha256=plan.plan_sha256,
    )

    with pytest.raises(ProviderPracticeBootstrapError, match="different plan"):
        plan_provider_practice_bootstrap(
            **(values | {"reason": "A changed bootstrap reason"})
        )


def test_stale_concurrent_plan_fails_cleanly_after_reference_lock(monkeypatch):
    domain = f"stale-{secrets.token_hex(4)}.example.com"
    _, _, values = _inputs(domain)
    original_plan = plan_provider_practice_bootstrap(**values)
    changed_values = values | {"reason": "A concurrently changed bootstrap reason"}
    stale_changed_plan = plan_provider_practice_bootstrap(**changed_values)

    bootstrap_provider_practice(
        **values,
        confirm_roster_sha256=original_plan.roster_sha256,
        confirm_plan_sha256=original_plan.plan_sha256,
    )

    calls: list[str] = []
    original_lock = practice_bootstrap_service._lock_bootstrap_reference
    original_lookup = practice_bootstrap_service._existing_bootstrap_audit

    def tracked_lock(session, bootstrap_ref):
        calls.append("lock")
        return original_lock(session, bootstrap_ref)

    def tracked_lookup(session, bootstrap_ref, *, lock=False):
        if lock:
            assert calls and calls[-1] == "lock"
            calls.append("locked_lookup")
        return original_lookup(session, bootstrap_ref, lock=lock)

    monkeypatch.setattr(
        practice_bootstrap_service,
        "plan_provider_practice_bootstrap",
        lambda **_kwargs: stale_changed_plan,
    )
    monkeypatch.setattr(
        practice_bootstrap_service,
        "_lock_bootstrap_reference",
        tracked_lock,
    )
    monkeypatch.setattr(
        practice_bootstrap_service,
        "_existing_bootstrap_audit",
        tracked_lookup,
    )

    with pytest.raises(ProviderPracticeBootstrapError, match="different plan"):
        bootstrap_provider_practice(
            **changed_values,
            confirm_roster_sha256=stale_changed_plan.roster_sha256,
            confirm_plan_sha256=stale_changed_plan.plan_sha256,
        )
    assert calls == ["lock", "locked_lookup"]


@pytest.mark.parametrize(
    ("field", "drifted_value"),
    [
        ("legal_name", "Unexpected Practice Name"),
        ("email_domain", "unexpected.example.com"),
        ("is_solo", True),
        ("origin_channel", "catholic"),
        ("pastoral_pack", True),
        ("negotiated_baa_required", False),
    ],
)
def test_bootstrap_retry_fails_closed_when_fixed_practice_state_drifted(
    field,
    drifted_value,
):
    domain = f"drift-{secrets.token_hex(4)}.example.com"
    _, _, values = _inputs(domain)
    plan = plan_provider_practice_bootstrap(**values)
    result = bootstrap_provider_practice(
        **values,
        confirm_roster_sha256=plan.roster_sha256,
        confirm_plan_sha256=plan.plan_sha256,
    )

    setattr(result.practice, field, drifted_value)
    result.practice.save()

    with pytest.raises(
        ProviderPracticeBootstrapError,
        match="prior bootstrap evidence conflicts",
    ):
        plan_provider_practice_bootstrap(**values)


def test_bootstrap_refuses_any_existing_domain_practice():
    domain = f"existing-{secrets.token_hex(4)}.example.com"
    _, _, values = _inputs(domain)
    Practice(
        legal_name="Existing Practice",
        join_code=f"E{secrets.token_hex(4).upper()[:7]}",
        email_domain=domain,
        origin_channel="direct",
    ).save()

    with pytest.raises(ProviderPracticeBootstrapError, match="already exists"):
        plan_provider_practice_bootstrap(**values)
