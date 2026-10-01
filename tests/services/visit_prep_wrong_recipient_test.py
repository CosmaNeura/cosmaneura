"""Wrong-recipient revocation and retained quarantine on owned PostgreSQL."""

from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import uuid4

import pytest

import app.services.visit_prep_interviews as interviews
from app.configuration.visit_prep import VisitPrepSettings
from app.services.visit_preps import revoke_visit_prep, rotate_visit_prep_credential

from activemodel.session_manager import get_session
from app.models.visit_prep import VisitPrep, VisitPrepCredentialGeneration
from app.models.visit_prep_response import VisitPrepQuestionResponse
from sqlalchemy import delete
from sqlalchemy.exc import DBAPIError
from sqlmodel import select

from tests.services.visit_prep_interviews_test import _confirm, _fixture, _respond


@pytest.mark.parametrize("phase", ["invited", "confirmed", "in_progress", "completed"])
def test_report_revokes_unused_or_quarantines_interacted_content(monkeypatch, phase):
    fixture = _fixture(monkeypatch, "misdelivery")
    if phase != "invited":
        _confirm(fixture)
    if phase == "in_progress":
        _respond(fixture, "reason", answer="Original synthetic patient wording")
    if phase == "completed":
        for key in ("reason", "medications", "adherence", "story"):
            _respond(fixture, key)
        interviews.complete_interview(access_token=fixture.access_token)
    with get_session() as session:
        root = session.exec(
            select(VisitPrep).where(VisitPrep.id == fixture.prep_id)
        ).one()
        retention = (root.confirmed_at, root.confirmed_by_role, root.retain_until)
        original_responses = session.exec(
            select(VisitPrepQuestionResponse).where(
                VisitPrepQuestionResponse.visit_prep_id == fixture.prep_id
            )
        ).all()
        original = [(row.id, row.answer_ciphertext) for row in original_responses]

    assert interviews.report_wrong_recipient(access_token=fixture.access_token) is None
    with get_session() as session:
        root = session.exec(
            select(VisitPrep).where(VisitPrep.id == fixture.prep_id)
        ).one()
        assert root.lifecycle_state == (
            "invited" if phase == "invited" else "misdirected"
        )
        assert root.revocation_reason == "wrong_recipient"
        assert root.revoked_by_ref is not None
        assert fixture.access_token not in root.revoked_by_ref
        assert (
            root.confirmed_at,
            root.confirmed_by_role,
            root.retain_until,
        ) == retention
        if phase == "invited":
            assert root.misdirected_at is None
            assert root.misdirected_incident_ref is None
        else:
            assert root.misdirected_at == root.revoked_at
            assert root.misdirected_disposition == "pending_review"
            assert root.misdirected_incident_ref is not None
        first_report = (root.revoked_at, root.misdirected_incident_ref)
        credential = session.exec(
            select(VisitPrepCredentialGeneration).where(
                VisitPrepCredentialGeneration.visit_prep_id == fixture.prep_id
            )
        ).one()
        assert credential.revoked_at == root.revoked_at
        assert credential.revoked_by_ref == root.revoked_by_ref
        assert credential.revocation_reason == "wrong_recipient"
        responses = session.exec(
            select(VisitPrepQuestionResponse).where(
                VisitPrepQuestionResponse.visit_prep_id == fixture.prep_id
            )
        ).all()
        assert [(row.id, row.answer_ciphertext) for row in responses] == original
    assert interviews.report_wrong_recipient(access_token=fixture.access_token) is None
    with get_session() as session:
        root = session.exec(
            select(VisitPrep).where(VisitPrep.id == fixture.prep_id)
        ).one()
        assert (root.revoked_at, root.misdirected_incident_ref) == first_report

    for operation in (
        lambda: interviews.get_interview(access_token=fixture.access_token),
        lambda: interviews.confirm_interview(
            access_token=fixture.access_token,
            first_name="Jos\N{LATIN SMALL LETTER E WITH ACUTE}",
            role="patient",
        ),
        lambda: interviews.submit_response(
            access_token=fixture.access_token,
            idempotency_key=str(uuid4()),
            response=interviews.ResponseInput(
                question_key="reason", response_kind="skipped"
            ),
        ),
        lambda: interviews.complete_interview(access_token=fixture.access_token),
    ):
        with pytest.raises(interviews.VisitPrepInterviewUnavailable):
            operation()

    if phase != "invited":
        with get_session() as session:
            with pytest.raises(DBAPIError) as caught:
                session.execute(
                    delete(VisitPrep).where(cast(Any, VisitPrep.id) == fixture.prep_id)
                )
                session.commit()
            assert getattr(caught.value.orig, "sqlstate", None) == "55000"
            session.rollback()


def test_old_credential_cannot_report_or_replay_after_rotation(monkeypatch):
    fixture = _fixture(monkeypatch, "old-report")
    rotated = rotate_visit_prep_credential(
        owner_practice_member_id=fixture.owner.id,
        owner_doctor_id=fixture.doctor.id,
        practice_id=fixture.practice.id,
        visit_prep_id=fixture.prep_id,
        expected_credential_generation=1,
    )
    assert rotated.access_token is not None
    with pytest.raises(interviews.VisitPrepInterviewUnavailable):
        interviews.report_wrong_recipient(access_token=fixture.access_token)
    assert (
        interviews.get_interview(access_token=rotated.access_token).status
        == "confirmation_required"
    )
    interviews.report_wrong_recipient(access_token=rotated.access_token)
    with pytest.raises(interviews.VisitPrepInterviewUnavailable):
        interviews.report_wrong_recipient(access_token=fixture.access_token)


def test_wrong_recipient_replay_still_requires_current_owner_authority(monkeypatch):
    fixture = _fixture(monkeypatch, "report-authority")
    interviews.report_wrong_recipient(access_token=fixture.access_token)
    monkeypatch.setattr(
        interviews.visit_prep_access,
        "VISIT_PREP_SETTINGS",
        VisitPrepSettings(enabled=True, public_api_enabled=True),
    )
    with pytest.raises(interviews.VisitPrepInterviewUnavailable):
        interviews.report_wrong_recipient(access_token=fixture.access_token)


@pytest.mark.parametrize(
    "reason", ["physician_revoked", "appointment_cancelled", "wrong_recipient"]
)
def test_public_report_cannot_replay_an_owner_revocation(monkeypatch, reason):
    fixture = _fixture(monkeypatch, "owner-revocation-report")
    revoke_visit_prep(
        owner_practice_member_id=fixture.owner.id,
        owner_doctor_id=fixture.doctor.id,
        practice_id=fixture.practice.id,
        visit_prep_id=fixture.prep_id,
        expected_credential_generation=1,
        reason=reason,
    )
    with pytest.raises(interviews.VisitPrepInterviewUnavailable):
        interviews.report_wrong_recipient(access_token=fixture.access_token)


def test_expired_report_cannot_change_revocation_or_quarantine(monkeypatch):
    from tests.integration.visit_prep_interviews_lock_test import (
        _fixture as timed_fixture,
    )

    scheduled_at = datetime.now(UTC) - timedelta(days=4)
    fixture = timed_fixture(
        monkeypatch,
        "expired-report",
        created_at=scheduled_at - timedelta(minutes=1),
        scheduled_at=scheduled_at,
    )
    with pytest.raises(interviews.VisitPrepInterviewUnavailable):
        interviews.report_wrong_recipient(access_token=fixture.access_token)
    with get_session() as session:
        prep = session.exec(
            select(VisitPrep).where(VisitPrep.id == fixture.visit_prep_id)
        ).one()
        assert prep.revoked_at is None
        assert prep.misdirected_at is None
