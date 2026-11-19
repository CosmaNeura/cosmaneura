"""Synthetic-only coverage for authenticated self-delete."""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

import pytest

import app.services.doctor_self_delete as self_delete_module
from app.services.doctor_access import doctor_tombstone, resolve_doctor_access
from app.services.doctor_self_delete import (
    DoctorSelfDeleteError,
    assert_self_only,
    delete_own_account,
)
from app.services.organization_access import (
    RosterApprovalInput,
    claim_roster_access,
    configure_provider_organization,
    import_roster_approvals,
)

from app.models.doctor import Doctor
from app.models.doctor_video_integration import DoctorVideoIntegration, VideoProvider
from app.models.note_template_preference import NoteTemplatePreference
from app.models.organization_access import OrganizationRosterApproval
from app.models.patient import Patient
from app.models.pilot_access_grandfather_grant import PilotAccessGrandfatherGrant
from app.models.practice_access_audit import PracticeAccessAction, PracticeAccessAudit
from app.models.superadmin_capability import (
    SuperadminCapabilityEnvironment,
    SuperadminCapabilityGrant,
    SuperadminCapabilityKind,
)

from tests.factories import add_practice_peer, build_self_delete_scope

SUPERADMIN_ISSUER = "https://self-delete-superadmin.example"


def _delete(doctor: Doctor, *, clerk_deleter=None, now=None):
    return delete_own_account(
        doctor_id=doctor.id,
        confirm="DELETE",
        authenticated_doctor_id=doctor.id,
        now=now or datetime(2026, 11, 2, 12, 0, tzinfo=UTC),
        clerk_deleter=clerk_deleter or (lambda _c: True),
    )


def test_self_delete_removes_only_that_user_and_leaves_colleague_intact():
    owner, practice, _ = build_self_delete_scope(is_solo=False)
    colleague = add_practice_peer(practice, role="workforce")
    patient = Patient(
        doctor_id=owner.id,
        name="Synthetic Retained Patient",
        date_of_birth=datetime(1980, 1, 1, tzinfo=UTC),
    ).save()
    deleted: list[str] = []
    result = _delete(owner, clerk_deleter=lambda c: deleted.append(c) or True)
    assert result.status == "deleted" and result.practice_left_ownerless is True
    assert deleted == [owner.clerk_id]
    owner.refresh()
    colleague.refresh()
    practice.refresh()
    assert owner.account_deletion_status == "deleted" and owner.email is None
    assert resolve_doctor_access(owner).denial_reason == "doctor_disabled"
    assert colleague.deleted_at is None and doctor_tombstone(colleague) is None
    assert resolve_doctor_access(colleague).allowed is True
    assert Patient.get(id=patient.id) is not None
    assert practice.owner_reassignment_needed_at is not None
    assert practice.baa_version is not None


def test_self_delete_sole_owner_leaves_practice_ownerless_and_intact():
    doctor, practice, member = build_self_delete_scope(is_solo=True)
    result = _delete(doctor, now=datetime(2026, 11, 2, 13, 0, tzinfo=UTC))
    assert result.practice_left_ownerless is True
    doctor.refresh()
    member.refresh()
    practice.refresh()
    assert doctor.account_deletion_status == "deleted"
    assert member.access_revoked_at is not None
    assert practice.owner_reassignment_needed_at == datetime(
        2026, 11, 2, 13, 0, tzinfo=UTC
    )
    receipt = PracticeAccessAudit.get(
        practice_id=practice.id,
        action=PracticeAccessAction.doctor_self_deleted.value,
    )
    assert receipt is not None and receipt.operator_ref.startswith("self:doctor:")


def test_self_delete_co_owner_and_workforce_do_not_leave_ownerless():
    owner, practice, _ = build_self_delete_scope(is_solo=False)
    co_owner = add_practice_peer(practice, role="owner")
    workforce = add_practice_peer(practice, role="workforce")
    assert _delete(owner).practice_left_ownerless is False
    practice.refresh()
    assert practice.owner_reassignment_needed_at is None
    assert _delete(workforce).practice_left_ownerless is False
    practice.refresh()
    co_owner.refresh()
    assert practice.owner_reassignment_needed_at is None
    assert co_owner.deleted_at is None


def test_self_delete_revokes_api_key_grants_roster_oauth_prefs(monkeypatch):
    del monkeypatch
    now = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
    suffix = secrets.token_hex(5)
    domain = f"selfdel-roster-{suffix}.example.com"
    doctor = Doctor(
        clerk_id=f"user_selfdel_revoke_{suffix}",
        email=f"provider@{domain}",
        biography="Synthetic personal profile text",
    ).save()
    doctor.generate_api_key()
    from app.models.practice import Practice

    practice = Practice(
        legal_name=f"Self Delete Roster {suffix}",
        join_code=f"S{secrets.token_hex(4).upper()[:7]}",
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
        legal_name=f"Self Delete Organization {suffix}",
        slug=f"selfdel-{suffix}",
        email_domain=domain,
        operator_ref="change:self-delete-roster-setup",
        reason="Configure a synthetic self-delete test organization",
        now=now,
    )
    import_roster_approvals(
        practice,
        [RosterApprovalInput(email=doctor.email or "")],
        operator_ref="change:self-delete-roster-import",
        reason="Approve the synthetic self-delete test provider",
        source_ref="test://self-delete-roster",
        source_sha256="d" * 64,
        now=now + timedelta(minutes=1),
    )
    _, member = claim_roster_access(
        doctor, supplied_code=code, now=now + timedelta(minutes=2)
    )
    approval_id = member.organization_roster_approval_id
    grant = PilotAccessGrandfatherGrant(
        clerk_id=doctor.clerk_id,
        doctor_id_snapshot=doctor.id,
        practice_id_snapshot=practice.id,
        email_snapshot=doctor.email,
        email_sha256=hashlib.sha256(doctor.email.encode()).hexdigest(),
        source_ref="controlled://self-delete-test/pilot-export",
        source_sha256="a" * 64,
        grant_plan_sha256="b" * 64,
        granted_by_ref="change:self-delete-test",
        grant_reason="Synthetic pilot grant for self-delete",
        granted_at=datetime(2026, 8, 2, 12, 0, tzinfo=UTC),
    ).save()
    preference = NoteTemplatePreference(doctor_id=doctor.id, revision=1).save()
    video = DoctorVideoIntegration(
        doctor_id=doctor.id,
        provider=VideoProvider.zoom,
        provider_user_id=f"zoom-{secrets.token_hex(5)}",
        access_token="encrypted-access-fixture",
        refresh_token="encrypted-refresh-fixture",
        token_expires_at=datetime(2026, 8, 25, 12, 0, tzinfo=UTC),
    ).save()
    _delete(doctor, now=datetime(2026, 11, 2, 15, 0, tzinfo=UTC))
    doctor.refresh()
    grant.refresh()
    approval = OrganizationRosterApproval.get(id=approval_id)
    assert doctor.api_key is None
    assert NoteTemplatePreference.get(id=preference.id) is None
    assert DoctorVideoIntegration.get(id=video.id) is None
    assert grant.revoked_at is not None
    assert approval is not None and approval.revoked_at is not None


def test_sole_owner_nonterminal_stripe_flags_follow_up_without_cancel(monkeypatch):
    doctor, practice, _ = build_self_delete_scope(is_solo=True)
    practice.stripe_subscription_id = f"sub_selfdel_{secrets.token_hex(4)}"
    practice.subscription_status = "active"
    practice.stripe_quantity_sync_required = False
    practice.stripe_quantity_sync_target = 1
    practice.stripe_quantity_sync_version = 3
    practice.save()
    cancel_calls: list[object] = []
    monkeypatch.setattr(
        "app.services.stripe_billing.cancel_subscription",
        lambda *a, **k: cancel_calls.append((a, k))
        or (_ for _ in ()).throw(AssertionError("no cancel")),
        raising=False,
    )
    result = _delete(doctor)
    assert result.status == "deleted" and result.stripe_follow_up_required is True
    assert cancel_calls == []
    practice.refresh()
    assert practice.subscription_status == "active"
    assert practice.stripe_quantity_sync_required is True
    assert practice.stripe_quantity_sync_version == 4
    receipt = PracticeAccessAudit.get(
        practice_id=practice.id,
        action=PracticeAccessAction.doctor_self_deleted.value,
    )
    assert receipt is not None
    assert "stripe_follow_up=1" in (receipt.document_reference or "")


def test_assert_self_only_confirm_idempotent_and_pending_resume():
    left, _, _ = build_self_delete_scope()
    right, _, _ = build_self_delete_scope()
    with pytest.raises(DoctorSelfDeleteError, match="cannot delete another"):
        assert_self_only(authenticated_doctor_id=left.id, requested_doctor_id=right.id)
    with pytest.raises(DoctorSelfDeleteError, match="confirm") as raised:
        delete_own_account(
            doctor_id=left.id,
            confirm="delete",
            authenticated_doctor_id=left.id,
            clerk_deleter=lambda _c: True,
        )
    assert raised.value.status_code == 422
    first = _delete(left)
    second = _delete(
        left,
        clerk_deleter=lambda _c: (_ for _ in ()).throw(AssertionError("no clerk")),
    )
    assert first.status == "deleted"
    assert second.status == "already_deleted" and second.changed is False

    pending_doctor, practice, _ = build_self_delete_scope()
    calls: list[str] = []

    def fail_once(clerk_id: str) -> bool:
        calls.append(clerk_id)
        if len(calls) == 1:
            raise DoctorSelfDeleteError(
                "clerk identity could not be deleted", status_code=503
            )
        return True

    with pytest.raises(DoctorSelfDeleteError) as pending:
        _delete(pending_doctor, clerk_deleter=fail_once)
    assert pending.value.status_code == 503
    pending_doctor.refresh()
    assert pending_doctor.account_deletion_status == "deletion_pending"
    assert _delete(pending_doctor, clerk_deleter=fail_once).status == "deleted"
    assert len(calls) == 2
    practice.refresh()
    assert practice.owner_reassignment_needed_at is not None


def test_capability_verification_failure_is_conflict_409(monkeypatch):
    doctor, _, _ = build_self_delete_scope()

    def boom(*_a, **_k):
        from app.services.superadmin_capability import SuperadminCapabilityError

        raise SuperadminCapabilityError("synthetic capability verification failure")

    monkeypatch.setattr(
        self_delete_module, "get_active_runtime_superadmin_capability_kinds", boom
    )
    with pytest.raises(DoctorSelfDeleteError, match="capability") as raised:
        _delete(doctor)
    assert raised.value.status_code == 409


def test_active_capabilities_are_conflict_409(monkeypatch):
    doctor, _, _ = build_self_delete_scope()
    monkeypatch.setattr(
        "app.services.superadmin_capability.configured_superadmin_capability_environment",
        lambda: SuperadminCapabilityEnvironment.staging,
    )
    monkeypatch.setattr(
        "app.services.superadmin_capability.configured_superadmin_clerk_issuer",
        lambda: SUPERADMIN_ISSUER,
    )
    SuperadminCapabilityGrant(
        environment=SuperadminCapabilityEnvironment.staging,
        capability_kind=SuperadminCapabilityKind.platform_admin,
        clerk_issuer=SUPERADMIN_ISSUER,
        clerk_subject=doctor.clerk_id,
        doctor_id=doctor.id,
        issuer_sha256=hashlib.sha256(SUPERADMIN_ISSUER.encode()).hexdigest(),
        subject_sha256=hashlib.sha256(doctor.clerk_id.encode()).hexdigest(),
        verified_primary_email_sha256=hashlib.sha256(
            doctor.email.strip().lower().encode()
        ).hexdigest(),
        source_ref="controlled:self-delete-test",
        source_sha256="c" * 64,
        grant_plan_sha256="d" * 64,
        granted_by_ref="ticket:self-delete-test",
        grant_reason_code="reviewed_test_grant",
        granted_at=datetime.now(UTC) - timedelta(minutes=1),
    ).save()
    with pytest.raises(DoctorSelfDeleteError, match="capabilities") as active:
        _delete(doctor)
    assert active.value.status_code == 409


def test_sqlalchemy_error_after_rollback_is_retryable_503(monkeypatch):
    from sqlalchemy.exc import SQLAlchemyError

    doctor, _, _ = build_self_delete_scope()
    monkeypatch.setattr(
        self_delete_module,
        "_delete_personal_settings",
        lambda *_a, **_k: (_ for _ in ()).throw(SQLAlchemyError("boom")),
    )
    with pytest.raises(DoctorSelfDeleteError) as raised:
        _delete(doctor)
    assert raised.value.status_code == 503
    doctor.refresh()
    assert doctor.account_deletion_status is None


@pytest.mark.parametrize("pending", ["session", "request"])
def test_sole_owner_open_checkout_deletes_and_flags_operator(pending):
    doctor, practice, _ = build_self_delete_scope(is_solo=True)
    now = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
    if pending == "session":
        practice.stripe_checkout_session_id = f"cs_test_{secrets.token_hex(4)}"
        practice.stripe_checkout_session_expires_at = now + timedelta(hours=2)
    else:
        practice.stripe_checkout_request_json = "{}"
        practice.stripe_checkout_request_created_at = now - timedelta(hours=1)
    practice.save()
    result = _delete(doctor, now=now)
    assert result.status == "deleted" and result.stripe_follow_up_required
    flag = PracticeAccessAudit.get(
        practice_id=practice.id,
        action=PracticeAccessAction.stripe_checkout_follow_up_required.value,
    )
    assert flag is not None and flag.document_reference.startswith("stripe-checkout:")


def test_self_delete_locks_practice_before_doctor():
    from sqlalchemy import event
    from sqlalchemy.engine import Engine

    doctor, _, _ = build_self_delete_scope(is_solo=True)
    sql: list[str] = []
    record = lambda _c, _cur, statement, *_a: sql.append(statement)  # noqa: E731
    event.listen(Engine, "before_cursor_execute", record)
    try:
        _delete(doctor)
    finally:
        event.remove(Engine, "before_cursor_execute", record)
    locks = [s.split(" FROM ")[1].split()[0] for s in sql if "FOR UPDATE" in s]
    assert locks[:2] == ["practice", "doctor"]
