"""Ownership transfer clears owner_reassignment_needed_at (synthetic data)."""

import secrets
from datetime import datetime, timezone

from app.services.practice_owner_transfer import transfer_practice_owner

from app.models.doctor import Doctor
from app.models.practice import Practice, PracticeMember, PracticeRole

from tests.factories import save_practice_member

NOW = datetime(2026, 11, 19, 18, tzinfo=timezone.utc)


def _seed():
    suffix = secrets.token_hex(4)
    old = Doctor(
        clerk_id=f"user_reassignOld{suffix}",
        email=f"old-{suffix}@synthetic.invalid",
        biography="",
        onboarding_completed_at=NOW,
    ).save()
    new = Doctor(
        clerk_id=f"user_reassignNew{suffix}",
        email=f"new-{suffix}@synthetic.invalid",
        biography="",
        onboarding_completed_at=NOW,
    ).save()
    practice = Practice(
        legal_name="Reassignment Transfer Practice",
        join_code=f"R{suffix.upper()[:7]}",
        email_domain="synthetic.invalid",
        owner_reassignment_needed_at=NOW,
    ).save()
    save_practice_member(
        PracticeMember(
            practice_id=practice.id,
            doctor_id=old.id,
            role=PracticeRole.owner.value,
            attested_at=NOW,
            attestation_name="Old Owner",
        )
    )
    save_practice_member(
        PracticeMember(
            practice_id=practice.id,
            doctor_id=new.id,
            role=PracticeRole.workforce.value,
            attested_at=NOW,
            attestation_name="New Owner",
        )
    )
    emails = {old.clerk_id: old.email, new.clerk_id: new.email}
    return practice, old, new, emails


def test_owner_reassignment_needed_at_clears_on_ownership_transfer():
    practice, old, new, emails = _seed()
    assert practice.owner_reassignment_needed_at == NOW
    assert old.clerk_id is not None and new.clerk_id is not None
    assert new.email is not None

    transfer_ref = f"synthetic-reassign-{secrets.token_hex(4)}"
    plan = transfer_practice_owner(
        practice_id=practice.id,
        expected_owner_subject=old.clerk_id,
        new_owner_subject=new.clerk_id,
        new_owner_email_confirmation=new.email,
        transfer_ref=transfer_ref,
        operator_ref="synthetic-operator",
        reason="owner_correction",
        verify_primary_email=emails.get,
        now=NOW,
    )
    applied = transfer_practice_owner(
        practice_id=practice.id,
        expected_owner_subject=old.clerk_id,
        new_owner_subject=new.clerk_id,
        new_owner_email_confirmation=new.email,
        transfer_ref=transfer_ref,
        operator_ref="synthetic-operator",
        reason="owner_correction",
        verify_primary_email=emails.get,
        now=NOW,
        apply=True,
        confirm_plan_sha256=plan.plan_sha256,
    )

    practice.refresh()
    assert applied.already_applied is False
    assert practice.owner_reassignment_needed_at is None
