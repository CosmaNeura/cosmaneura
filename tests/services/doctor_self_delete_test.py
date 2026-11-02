"""Synthetic-only coverage for authenticated self-delete."""

import secrets
from datetime import UTC, datetime

import pytest

from app.services.doctor_access import doctor_tombstone, resolve_doctor_access
from app.services.doctor_self_delete import (
    DoctorSelfDeleteError,
    assert_self_only,
    delete_own_account,
)

from app.models.baa import BaaAcceptance
from app.models.doctor import Doctor
from app.models.patient import Patient
from app.models.practice import Practice, PracticeMember
from app.models.practice_access_audit import PracticeAccessAction, PracticeAccessAudit

from tests.factories import save_practice_member
from tests.routes.utils import ensure_current_baa_version


def _scope(*, is_solo: bool = True, role: str = "owner"):
    suffix = secrets.token_hex(5)
    doctor = Doctor(
        clerk_id=f"user_selfdel_{suffix}",
        email=f"selfdel-{suffix}@example.com",
        biography="Synthetic personal profile text",
    ).save()
    version = ensure_current_baa_version()
    practice = Practice(
        legal_name=f"Self Delete Practice {suffix}",
        join_code=f"S{secrets.token_hex(4).upper()[:7]}",
        email_domain="example.com",
        is_solo=is_solo,
        origin_channel="direct",
        pastoral_pack=False,
        billing_exempt=True,
        baa_version=f"{version.doc_id} v{version.version}",
        baa_accepted_at=datetime(2026, 8, 1, 12, 0, tzinfo=UTC),
        baa_accepted_by_doctor_id=doctor.id,
        baa_signature_name="Synthetic Signer",
        baa_signature_title="Owner",
    ).save()
    BaaAcceptance(
        practice_id=practice.id,
        baa_version_id=version.id,
        covered_entity_legal_name=practice.legal_name,
        accepted_by_doctor_id=doctor.id,
        accepted_by_name="Synthetic Signer",
        accepted_by_title="Owner",
        accepted_by_email=doctor.email or "synthetic@example.com",
        accepted_at_utc=datetime(2026, 8, 1, 12, 0, tzinfo=UTC),
        content_sha256=version.sha256,
    ).save()
    member = save_practice_member(
        PracticeMember(
            practice_id=practice.id,
            doctor_id=doctor.id,
            role=role,
            attested_at=datetime(2026, 8, 1, 12, 0, tzinfo=UTC),
            attestation_name="Synthetic Signer",
        )
    )
    return doctor, practice, member


def test_self_delete_removes_only_that_user_and_leaves_colleague_intact():
    owner, practice, _ = _scope(is_solo=False)
    colleague = Doctor(
        clerk_id=f"user_selfdel_peer_{secrets.token_hex(5)}",
        email=f"peer-{secrets.token_hex(5)}@example.com",
        biography="Colleague biography",
    ).save()
    peer_member = save_practice_member(
        PracticeMember(
            practice_id=practice.id,
            doctor_id=colleague.id,
            role="workforce",
            attested_at=datetime(2026, 8, 2, 12, 0, tzinfo=UTC),
            attestation_name="Colleague",
        )
    )
    patient = Patient(
        doctor_id=owner.id,
        name="Synthetic Retained Patient",
        date_of_birth=datetime(1980, 1, 1, tzinfo=UTC),
    ).save()

    deleted_clerk: list[str] = []

    result = delete_own_account(
        doctor_id=owner.id,
        confirm="DELETE",
        now=datetime(2026, 11, 2, 12, 0, tzinfo=UTC),
        clerk_deleter=lambda clerk_id: deleted_clerk.append(clerk_id) or True,
    )

    assert result.status == "deleted"
    assert result.changed is True
    assert result.practice_left_ownerless is True
    assert deleted_clerk == [owner.clerk_id]

    owner.refresh()
    colleague.refresh()
    peer_member.refresh()
    practice.refresh()
    assert owner.account_deletion_status == "deleted"
    assert owner.deleted_at is not None
    assert owner.email is None
    assert owner.biography == ""
    assert resolve_doctor_access(owner).denial_reason == "doctor_disabled"
    assert colleague.deleted_at is None
    assert colleague.email is not None
    assert colleague.account_deletion_status is None
    assert peer_member.access_revoked_at is None
    assert doctor_tombstone(colleague) is None
    assert resolve_doctor_access(colleague).allowed is True
    assert Patient.get(id=patient.id) is not None
    assert practice.owner_reassignment_needed_at is not None
    assert Practice.get(id=practice.id) is not None
    assert practice.baa_version is not None


def test_self_delete_sole_owner_leaves_practice_ownerless_and_intact():
    doctor, practice, member = _scope(is_solo=True)

    result = delete_own_account(
        doctor_id=doctor.id,
        confirm="DELETE",
        now=datetime(2026, 11, 2, 13, 0, tzinfo=UTC),
        clerk_deleter=lambda _clerk_id: True,
    )

    assert result.practice_left_ownerless is True
    doctor.refresh()
    member.refresh()
    practice.refresh()
    assert doctor.account_deletion_status == "deleted"
    assert member.access_revoked_at is not None
    assert practice.owner_reassignment_needed_at == datetime(
        2026, 11, 2, 13, 0, tzinfo=UTC
    )
    assert Practice.get(id=practice.id) is not None
    assert practice.baa_version is not None
    receipt = PracticeAccessAudit.get(
        practice_id=practice.id,
        action=PracticeAccessAction.doctor_self_deleted.value,
    )
    assert receipt is not None
    assert receipt.operator_ref.startswith("self:doctor:")
    assert "example.com" not in (receipt.reason or "")
    assert doctor.email is None


def test_assert_self_only_refuses_another_user_id():
    left, _, _ = _scope()
    right, _, _ = _scope()
    with pytest.raises(DoctorSelfDeleteError, match="cannot delete another"):
        assert_self_only(
            authenticated_doctor_id=left.id,
            requested_doctor_id=right.id,
        )


def test_self_delete_requires_exact_confirm_phrase():
    doctor, _, _ = _scope()
    with pytest.raises(DoctorSelfDeleteError, match="confirm"):
        delete_own_account(
            doctor_id=doctor.id,
            confirm="delete",
            clerk_deleter=lambda _clerk_id: True,
        )
    doctor.refresh()
    assert doctor.deleted_at is None
    assert doctor.account_deletion_status is None


def test_self_delete_is_idempotent_after_completion():
    doctor, _, _ = _scope()
    first = delete_own_account(
        doctor_id=doctor.id,
        confirm="DELETE",
        clerk_deleter=lambda _clerk_id: True,
    )
    second = delete_own_account(
        doctor_id=doctor.id,
        confirm="DELETE",
        clerk_deleter=lambda _clerk_id: (_ for _ in ()).throw(
            AssertionError("Clerk must not be called again")
        ),
    )
    assert first.status == "deleted"
    assert second.status == "already_deleted"
    assert second.changed is False


def test_self_delete_resumes_deletion_pending_after_clerk_failure():
    doctor, practice, _ = _scope()
    calls: list[str] = []

    def fail_once(clerk_id: str) -> bool:
        calls.append(clerk_id)
        if len(calls) == 1:
            raise DoctorSelfDeleteError("clerk identity could not be deleted")
        return True

    with pytest.raises(DoctorSelfDeleteError, match="clerk identity"):
        delete_own_account(
            doctor_id=doctor.id,
            confirm="DELETE",
            clerk_deleter=fail_once,
        )

    doctor.refresh()
    assert doctor.account_deletion_status == "deletion_pending"
    assert doctor.deleted_at is not None

    resumed = delete_own_account(
        doctor_id=doctor.id,
        confirm="DELETE",
        clerk_deleter=fail_once,
    )
    assert resumed.status == "deleted"
    assert resumed.changed is True
    assert len(calls) == 2
    doctor.refresh()
    practice.refresh()
    assert doctor.account_deletion_status == "deleted"
    assert practice.owner_reassignment_needed_at is not None
