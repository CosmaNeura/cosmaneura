import hashlib
import secrets
from datetime import UTC, datetime, timedelta

import pytest

import app.services.doctor_account_closure as account_closure
import app.services.stripe_billing as stripe_billing
from app.configuration.authority import AuthorityKernelSettings
from app.services.doctor_access import resolve_doctor_access
from app.services.doctor_account_closure import (
    DoctorAccountClosureError,
    close_doctor_account,
    plan_doctor_account_closure,
)
from app.services.organization_access import (
    RosterApprovalInput,
    claim_roster_access,
    configure_provider_organization,
    import_roster_approvals,
)
from app.services.zoom import get_zoom_integration

from app.models.authority import (
    AuthorityActor,
    AuthorityActorExternalIdentity,
    AuthorityPerson,
    AuthorityPersonBinding,
    AuthorityTenant,
    AuthorityTenantMembership,
    MountContextSession,
)
from app.models.baa import BaaAcceptance, BaaVersion
from app.models.doctor import Doctor
from app.models.doctor_video_integration import DoctorVideoIntegration, VideoProvider
from app.models.organization_access import OrganizationRosterApproval
from app.models.patient import Patient
from app.models.pilot_access_grandfather_grant import PilotAccessGrandfatherGrant
from app.models.practice import Practice, PracticeMember
from app.models.practice_access_audit import PracticeAccessAction, PracticeAccessAudit


def _scope(*, is_solo: bool = True, role: str = "owner"):
    suffix = secrets.token_hex(5)
    doctor = Doctor(
        clerk_id=f"user_close_{suffix}",
        email=f"provider-{suffix}@example.com",
        biography="",
    ).save()
    practice = Practice(
        legal_name=f"Closure Practice {suffix}",
        join_code=f"C{secrets.token_hex(4).upper()[:7]}",
        email_domain="example.com",
        is_solo=is_solo,
        origin_channel="direct",
        pastoral_pack=False,
        baa_version="test-preserved",
        baa_accepted_at=datetime(2026, 8, 1, 12, 0, tzinfo=UTC),
        baa_accepted_by_doctor_id=doctor.id,
        baa_signature_name="Retained Signer",
        baa_signature_title="Owner",
    ).save()
    member = PracticeMember(
        practice_id=practice.id,
        doctor_id=doctor.id,
        role=role,
        attested_at=datetime(2026, 8, 1, 12, 0, tzinfo=UTC),
        attestation_name="Retained Signer",
    ).save()
    return doctor, practice, member


def _pilot_grant(doctor: Doctor, practice: Practice) -> PilotAccessGrandfatherGrant:
    assert doctor.email is not None
    return PilotAccessGrandfatherGrant(
        clerk_id=doctor.clerk_id,
        doctor_id_snapshot=doctor.id,
        practice_id_snapshot=practice.id,
        email_snapshot=doctor.email,
        email_sha256=hashlib.sha256(doctor.email.encode()).hexdigest(),
        source_ref="controlled://closure-test/pilot-export",
        source_sha256="a" * 64,
        grant_plan_sha256="b" * 64,
        granted_by_ref="change:pilot-import-test",
        grant_reason="Preserve approved pilot access",
        granted_at=datetime(2026, 8, 2, 12, 0, tzinfo=UTC),
    ).save()


def test_closure_preserves_clinical_and_baa_records_and_is_idempotent():
    doctor, practice, member = _scope()
    doctor.generate_api_key()
    retained_api_key = doctor.api_key
    grant = _pilot_grant(doctor, practice)
    video_integration = DoctorVideoIntegration(
        doctor_id=doctor.id,
        provider=VideoProvider.zoom,
        provider_user_id=f"zoom-{secrets.token_hex(5)}",
        access_token="encrypted-access-fixture",
        refresh_token="encrypted-refresh-fixture",
        token_expires_at=datetime(2026, 8, 25, 12, 0, tzinfo=UTC),
    ).save()
    patient = Patient(
        doctor_id=doctor.id,
        name="Retained Test Patient",
        date_of_birth=datetime(1980, 1, 1, tzinfo=UTC),
    ).save()
    version = BaaVersion(
        doc_id=f"TEST-{secrets.token_hex(4)}",
        version="1.0",
        sha256="c" * 64,
        content_path="app/assets/baa/baa_cn-baa-cust-cw_v2.2.html",
        published_at=datetime(2026, 8, 1, 11, 0, tzinfo=UTC),
        is_current=False,
    ).save()
    acceptance = BaaAcceptance(
        practice_id=practice.id,
        baa_version_id=version.id,
        covered_entity_legal_name=practice.legal_name,
        accepted_by_doctor_id=doctor.id,
        accepted_by_name="Retained Signer",
        accepted_by_title="Owner",
        accepted_by_email=doctor.email or "",
        accepted_at_utc=datetime(2026, 8, 1, 12, 0, tzinfo=UTC),
        content_sha256=version.sha256,
    ).save()
    args = {
        "doctor_id": doctor.id,
        "operator_ref": "change:account-closure-test",
        "reason": "Honor a verified provider account closure request",
    }

    first_plan = plan_doctor_account_closure(**args)
    second_plan = plan_doctor_account_closure(**args)
    assert first_plan == second_plan
    assert first_plan.pilot_grant_count == 1
    assert first_plan.video_integration_count == 1

    closed_at = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)
    result = close_doctor_account(
        **args,
        confirmed_plan_sha256=first_plan.plan_sha256,
        now=closed_at,
    )

    assert result.changed is True
    assert result.retain_until == datetime(2033, 8, 24, 12, 0, tzinfo=UTC)
    original_clerk_id = doctor.clerk_id
    doctor.refresh()
    member.refresh()
    grant.refresh()
    practice.refresh()
    assert doctor.deleted_at == closed_at
    assert doctor.clerk_id == original_clerk_id
    assert doctor.api_key == retained_api_key
    assert resolve_doctor_access(doctor).denial_reason == "doctor_disabled"
    assert member.access_revoked_at == closed_at
    assert member.access_records_retain_until == result.retain_until
    assert grant.revoked_at == closed_at
    assert grant.retain_until == result.retain_until
    assert grant.revocation_plan_sha256 == first_plan.plan_sha256
    assert Patient.get(id=patient.id) is not None
    assert BaaAcceptance.get(id=acceptance.id) is not None
    assert DoctorVideoIntegration.get(id=video_integration.id) is None
    assert get_zoom_integration(doctor) is None
    assert practice.baa_version == "test-preserved"
    assert practice.baa_signature_name == "Retained Signer"
    receipt = PracticeAccessAudit.get(
        practice_id=practice.id,
        action=PracticeAccessAction.doctor_account_closed.value,
    )
    assert receipt is not None
    assert receipt.evidence_sha256 == first_plan.plan_sha256
    assert receipt.expires_at == result.retain_until

    retry_plan = plan_doctor_account_closure(**args)
    assert retry_plan.already_applied is True
    assert retry_plan.plan_sha256 == first_plan.plan_sha256
    retry = close_doctor_account(
        **args,
        confirmed_plan_sha256=retry_plan.plan_sha256,
    )
    assert retry.changed is False
    assert retry.retain_until == result.retain_until
    assert (
        PracticeAccessAudit.where(
            PracticeAccessAudit.practice_id == practice.id,
            PracticeAccessAudit.action
            == PracticeAccessAction.doctor_account_closed.value,
        ).count()
        == 1
    )


def test_closure_refuses_owner_while_another_active_member_remains():
    owner, practice, owner_member = _scope(is_solo=False)
    other = Doctor(
        clerk_id=f"user_close_other_{secrets.token_hex(5)}",
        email=f"other-{secrets.token_hex(5)}@example.com",
        biography="",
    ).save()
    PracticeMember(
        practice_id=practice.id,
        doctor_id=other.id,
        role="workforce",
        attested_at=datetime(2026, 8, 2, 12, 0, tzinfo=UTC),
        attestation_name="Other Provider",
    ).save()

    with pytest.raises(DoctorAccountClosureError, match="transfer ownership first"):
        plan_doctor_account_closure(
            doctor_id=owner.id,
            operator_ref="change:owner-closure-test",
            reason="Attempt owner closure before transferring ownership",
        )

    owner.refresh()
    owner_member.refresh()
    assert owner.deleted_at is None
    assert owner_member.access_revoked_at is None


def test_closure_permits_non_owner_and_leaves_owner_active():
    owner, practice, _ = _scope(is_solo=False)
    workforce = Doctor(
        clerk_id=f"user_close_workforce_{secrets.token_hex(5)}",
        email=f"workforce-{secrets.token_hex(5)}@example.com",
        biography="",
    ).save()
    workforce_member = PracticeMember(
        practice_id=practice.id,
        doctor_id=workforce.id,
        role="workforce",
        attested_at=datetime(2026, 8, 2, 12, 0, tzinfo=UTC),
        attestation_name="Workforce Provider",
    ).save()
    args = {
        "doctor_id": workforce.id,
        "operator_ref": "change:workforce-closure-test",
        "reason": "Close a departing workforce provider account",
    }
    plan = plan_doctor_account_closure(**args)
    close_doctor_account(
        **args,
        confirmed_plan_sha256=plan.plan_sha256,
        now=datetime(2026, 8, 24, 13, 0, tzinfo=UTC),
    )

    owner.refresh()
    workforce.refresh()
    workforce_member.refresh()
    assert owner.deleted_at is None
    assert workforce.deleted_at is not None
    assert workforce_member.access_revoked_at is not None


def test_closure_revokes_linked_organization_roster_approval():
    now = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
    suffix = secrets.token_hex(5)
    domain = f"closure-{suffix}.example.com"
    doctor = Doctor(
        clerk_id=f"user_close_roster_{suffix}",
        email=f"provider@{domain}",
        biography="",
    ).save()
    practice = Practice(
        legal_name=f"Roster Closure Practice {suffix}",
        join_code=f"R{secrets.token_hex(4).upper()[:7]}",
        email_domain=domain,
        is_solo=False,
        origin_channel="direct",
        pastoral_pack=False,
        negotiated_baa_on_file=True,
        negotiated_baa_effective_date=now - timedelta(days=1),
        negotiated_baa_reference=f"executed-baa:{suffix}",
        billing_exempt=True,
    ).save()
    _, code, _ = configure_provider_organization(
        practice,
        legal_name=f"Roster Closure Organization {suffix}",
        slug=f"closure-{suffix}",
        email_domain=domain,
        operator_ref="change:roster-setup-test",
        reason="Configure a synthetic closure test organization",
        now=now,
    )
    import_roster_approvals(
        practice,
        [RosterApprovalInput(email=doctor.email or "")],
        operator_ref="change:roster-import-test",
        reason="Approve the synthetic closure test provider",
        source_ref="test://account-closure-roster",
        source_sha256="d" * 64,
        now=now + timedelta(minutes=1),
    )
    _, member = claim_roster_access(
        doctor,
        supplied_code=code,
        now=now + timedelta(minutes=2),
    )
    assert member.organization_roster_approval_id is not None
    approval_id = member.organization_roster_approval_id
    args = {
        "doctor_id": doctor.id,
        "operator_ref": "change:roster-closure-test",
        "reason": "Close an organization-rostered provider account",
    }
    plan = plan_doctor_account_closure(**args)
    assert plan.organization_approval_count == 1
    result = close_doctor_account(
        **args,
        confirmed_plan_sha256=plan.plan_sha256,
        now=now + timedelta(days=1),
    )

    approval = OrganizationRosterApproval.get(id=approval_id)
    assert approval is not None
    assert approval.revoked_at == now + timedelta(days=1)
    assert approval.retain_until == result.retain_until


def test_closure_rejects_wrong_digest_without_mutation():
    doctor, _, member = _scope()

    with pytest.raises(DoctorAccountClosureError, match="confirmed plan digest"):
        close_doctor_account(
            doctor_id=doctor.id,
            operator_ref="change:digest-test",
            reason="Exercise closure confirmation protection",
            confirmed_plan_sha256="0" * 64,
        )

    doctor.refresh()
    member.refresh()
    assert doctor.deleted_at is None
    assert member.access_revoked_at is None


def test_closure_retry_refuses_missing_grant_evidence():
    doctor, practice, member = _scope()
    closed_at = datetime(2026, 8, 24, 13, 15, tzinfo=UTC)
    retain_until = datetime(2033, 8, 24, 13, 15, tzinfo=UTC)
    operator_ref = "change:missing-evidence-test"
    reason = "Exercise fail-closed closure receipt validation"
    doctor.deleted_at = closed_at
    doctor.save()
    member.access_revoked_at = closed_at
    member.access_revoked_by_ref = operator_ref
    member.access_revocation_reason = reason
    member.access_records_retain_until = retain_until
    member.save()
    PracticeAccessAudit(
        practice_id=practice.id,
        action=PracticeAccessAction.doctor_account_closed.value,
        operator_ref=operator_ref,
        reason=reason,
        effective_at=closed_at,
        expires_at=retain_until,
        document_reference=(
            f"doctor-account-closure:{doctor.id}:p=1:r=0:a=0:m=0:v=0"
        ),
        evidence_sha256="e" * 64,
    ).save()

    with pytest.raises(
        DoctorAccountClosureError, match="grant closure evidence is incomplete"
    ):
        plan_doctor_account_closure(
            doctor_id=doctor.id,
            operator_ref=operator_ref,
            reason=reason,
        )


def test_closure_retry_repairs_a_failed_stripe_reconciliation_queue(monkeypatch):
    doctor, practice, _ = _scope()
    practice.stripe_subscription_id = f"sub_closure_{secrets.token_hex(5)}"
    practice.save()
    args = {
        "doctor_id": doctor.id,
        "operator_ref": "change:stripe-closure-test",
        "reason": "Close the provider and queue billing reconciliation",
    }
    plan = plan_doctor_account_closure(**args)
    assert plan.stripe_follow_up_required is True

    def fail_queue(_: Practice) -> None:
        raise RuntimeError("synthetic queue failure")

    monkeypatch.setattr(stripe_billing, "mark_quantity_sync_required", fail_queue)
    with pytest.raises(DoctorAccountClosureError, match="closure is applied"):
        close_doctor_account(
            **args,
            confirmed_plan_sha256=plan.plan_sha256,
            now=datetime(2026, 8, 24, 13, 30, tzinfo=UTC),
        )
    doctor.refresh()
    assert doctor.deleted_at is not None

    queued: list[str] = []
    monkeypatch.setattr(
        stripe_billing,
        "mark_quantity_sync_required",
        lambda value: queued.append(str(value.id)),
    )
    retry_plan = plan_doctor_account_closure(**args)
    retry = close_doctor_account(
        **args,
        confirmed_plan_sha256=retry_plan.plan_sha256,
    )
    assert retry.changed is False
    assert queued == [str(practice.id)]


def test_closure_revokes_governed_authority_without_deleting_history(monkeypatch):
    doctor, _, _ = _scope()
    issuer = "https://test-clerk.example"
    monkeypatch.setattr(
        account_closure,
        "AUTHORITY_KERNEL_SETTINGS",
        AuthorityKernelSettings(
            enabled=True,
            token_ttl_seconds=900,
            token_refresh_after_seconds=300,
            clerk_issuer=issuer,
        ),
    )
    tenant = AuthorityTenant(status="active").save()
    actor = AuthorityActor(actor_kind="human", status="active").save()
    membership = AuthorityTenantMembership(
        tenant_id=tenant.id,
        actor_id=actor.id,
        membership_kind="provider",
        status="active",
    ).save()
    identity = AuthorityActorExternalIdentity(
        actor_id=actor.id,
        issuer=issuer,
        subject=doctor.clerk_id,
        verified_at=datetime(2026, 8, 20, 12, 0, tzinfo=UTC),
    ).save()
    person = AuthorityPerson(lifecycle_state="patient", status="active").save()
    binding = AuthorityPersonBinding(
        person_id=person.id,
        tenant_id=tenant.id,
        actor_id=actor.id,
        binding_ref=f"provider-record-{secrets.token_hex(5)}",
        binding_kind="provider_record",
        status="active",
        verified_at=datetime(2026, 8, 20, 12, 0, tzinfo=UTC),
    ).save()
    mount = MountContextSession(
        actor_id=actor.id,
        tenant_id=tenant.id,
        person_binding_id=binding.id,
        source_identity_id=identity.id,
        session_kind="authenticated",
        surface_ceiling="provider_assistant",
        auth_tier_ceiling="provider",
        status="active",
        absolute_expires_at=datetime(2026, 8, 25, 12, 0, tzinfo=UTC),
    ).save()
    args = {
        "doctor_id": doctor.id,
        "operator_ref": "change:authority-closure-test",
        "reason": "Close all governed access for the provider",
    }
    plan = plan_doctor_account_closure(**args)
    assert plan.authority_identity_count == 1
    assert plan.active_mount_session_count == 1

    close_doctor_account(
        **args,
        confirmed_plan_sha256=plan.plan_sha256,
        now=datetime(2026, 8, 24, 14, 0, tzinfo=UTC),
    )

    actor.refresh()
    identity.refresh()
    membership.refresh()
    mount.refresh()
    assert AuthorityActorExternalIdentity.get(id=identity.id) is not None
    assert identity.revoked_at is not None
    assert actor.status == "revoked"
    assert membership.status == "revoked"
    assert mount.status == "revoked"
    assert mount.revoked_at is not None

    retry_plan = plan_doctor_account_closure(**args)
    assert retry_plan.already_applied is True
    assert retry_plan.active_mount_session_count == 1

    membership.status = "suspended"
    membership.save()
    with pytest.raises(
        DoctorAccountClosureError, match="authority membership closure is incomplete"
    ):
        plan_doctor_account_closure(**args)
    membership.status = "revoked"
    membership.save()

    mount.revoked_at = datetime(2026, 8, 24, 14, 1, tzinfo=UTC)
    mount.save()
    with pytest.raises(
        DoctorAccountClosureError,
        match="authority session closure evidence is incomplete",
    ):
        plan_doctor_account_closure(**args)


def test_closure_cli_is_dry_run_first_and_does_not_print_identity_values():
    from typer.testing import CliRunner

    from app.cli import app

    doctor, _, _ = _scope()
    assert doctor.email is not None
    result = CliRunner().invoke(
        app,
        [
            "close-provider-account",
            "--doctor-id",
            str(doctor.id),
            "--operator-ref",
            "change:closure-cli-test",
            "--reason",
            "Verify the dry-run closure command",
            "--confirm-email",
            doctor.email,
        ],
    )

    assert result.exit_code == 0, result.output
    assert "DRY RUN: provider account closure" in result.output
    assert "plan_sha256=" in result.output
    assert doctor.email not in result.output
    assert doctor.clerk_id not in result.output
    doctor.refresh()
    assert doctor.deleted_at is None
