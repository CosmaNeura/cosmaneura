"""Current Core owner transfer retains clinician-specific Visit Prep lineage."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.services import visit_prep_interviews as interviews
from app.services.practice_owner_transfer import transfer_practice_owner
from app.services.visit_preps import VisitPrepNotFound, rotate_visit_prep_credential

from activemodel.session_manager import get_session
from app.models.doctor import Doctor
from app.models.practice import PracticeMember, lock_practice_seats
from app.models.visit_prep import (
    VisitPrep,
    VisitPrepCredentialGeneration,
    VisitPrepScheduleGeneration,
)
from app.models.visit_prep_response import VisitPrepQuestionResponse
from sqlmodel import select

from tests.factories import save_practice_member
from tests.services.visit_prep_interviews_test import _confirm, _fixture


def test_practice_owner_transfer_never_reassigns_preparation_or_patient_authority(
    monkeypatch,
):
    fixture = _fixture(monkeypatch, "current-owner-transfer")
    _confirm(fixture)
    with get_session() as session:
        old_doctor = session.get(Doctor, fixture.doctor.id)
        assert old_doctor is not None
        old_doctor.clerk_id = f"user_O{uuid4().hex}"
        session.add(old_doctor)
        session.commit()
        session.refresh(old_doctor)
        old_subject, old_email = old_doctor.clerk_id, old_doctor.email
    now = datetime.now(UTC)
    new_doctor = Doctor(
        clerk_id=f"user_N{uuid4().hex}",
        email=f"synthetic-new-owner-{uuid4().hex}@example.test",
        biography="Invented new practice owner.",
    ).save()
    new_member = save_practice_member(
        PracticeMember(
            practice_id=fixture.practice.id,
            doctor_id=new_doctor.id,
            role="workforce",
            attested_at=now - timedelta(days=1),
            attestation_name="Invented attestation",
        )
    )

    def lineage():
        with get_session() as session:
            prep = session.get(VisitPrep, fixture.prep_id)
            assert prep is not None
            children = [
                session.exec(
                    select(model).where(model.visit_prep_id == fixture.prep_id)
                ).all()
                for model in (
                    VisitPrepScheduleGeneration,
                    VisitPrepCredentialGeneration,
                )
            ]
            return prep.model_dump(), [
                [row.model_dump() for row in group] for group in children
            ]

    before = lineage()
    emails = {
        old_subject: old_email,
        new_doctor.clerk_id: new_doctor.email,
    }
    reference = f"synthetic_visit_prep_{uuid4().hex}"

    def transfer(*, apply=False, plan_sha256=None):
        return transfer_practice_owner(
            practice_id=fixture.practice.id,
            expected_owner_subject=old_subject,
            new_owner_subject=new_doctor.clerk_id,
            new_owner_email_confirmation=new_doctor.email,
            transfer_ref=reference,
            operator_ref="synthetic_visit_prep_owner_transfer",
            reason="synthetic_owner_correction",
            verify_primary_email=emails.get,
            apply=apply,
            confirm_plan_sha256=plan_sha256,
        )

    plan = transfer()
    applied = transfer(apply=True, plan_sha256=plan.plan_sha256)
    assert applied.audit_id is not None and not applied.already_applied
    assert lineage() == before
    with get_session() as session:
        former = session.get(PracticeMember, fixture.owner.id)
        current = session.get(PracticeMember, new_member.id)
        assert former is not None and former.role == "workforce"
        assert current is not None and current.role == "owner"
    # Core's transfer deliberately retains the former owner's active workforce
    # access. Administrative ownership does not transfer this clinician's prep.
    assert (
        interviews.get_interview(access_token=fixture.access_token).status
        == "in_progress"
    )
    with pytest.raises(VisitPrepNotFound):
        rotate_visit_prep_credential(
            owner_practice_member_id=new_member.id,
            owner_doctor_id=new_doctor.id,
            practice_id=fixture.practice.id,
            visit_prep_id=fixture.prep_id,
            expected_credential_generation=1,
        )
    assert lineage() == before

    # Separate explicit offboarding still closes the patient's authority. The
    # retained preparation/credential is not silently deleted or reassigned.
    with get_session() as session:
        assert lock_practice_seats(session, fixture.practice.id) is not None
        member = session.get(PracticeMember, fixture.owner.id)
        assert member is not None
        member.access_revoked_at = datetime.now(UTC)
        member.access_revoked_by_ref = "synthetic_visit_prep_offboarding"
        member.access_revocation_reason = "synthetic_offboarding"
        member.access_records_retain_until = member.access_revoked_at + timedelta(
            days=365 * 8
        )
        session.add(member)
        session.commit()
    with pytest.raises(interviews.VisitPrepInterviewUnavailable):
        interviews.get_interview(access_token=fixture.access_token)
    assert lineage() == before
    with get_session() as session:
        assert (
            session.exec(
                select(VisitPrepQuestionResponse).where(
                    VisitPrepQuestionResponse.visit_prep_id == fixture.prep_id
                )
            ).all()
            == []
        )
