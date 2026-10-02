"""Pure timing compatibility with real, committed Core lifecycle facts.

The test-only snapshot projection is NOT a runtime authorization loader or
new first-party open instrumentation. No scheduler or transport is connected.
"""

from datetime import UTC, datetime, timedelta
from typing import Literal, TypedDict, cast
from uuid import uuid4

import pytest

from app.services.visit_prep_delivery_timing import TimingSnapshot, plan_delivery_timing
from app.services.visit_prep_interviews import (
    ResponseInput,
    complete_interview,
    confirm_interview,
    get_interview,
    submit_response,
)
from app.services.visit_prep_open_evidence import read_invitation_open_evidence
from app.services.visit_preps import (
    reschedule_visit_prep,
    revoke_visit_prep,
    rotate_visit_prep_credential,
)

from activemodel.session_manager import SessionManager
from activemodel.types import TypeIDType
from app.models.visit_prep import (
    VisitPrep,
    VisitPrepCredentialGeneration,
    VisitPrepScheduleGeneration,
)
from sqlalchemy import func
from sqlmodel import Session, select

from tests.services.visit_prep_interviews_test import _fixture


def _snapshot(fixture):
    # Independent connection: do not infer current lifecycle from a previously
    # returned command receipt or a cached ORM object. Never decrypt contacts.
    with Session(SessionManager.get_instance().get_engine()) as session:
        root = session.exec(
            select(VisitPrep).where(VisitPrep.id == fixture.prep_id).with_for_update()
        ).one()
        assert root is not None
        schedules = session.exec(
            select(VisitPrepScheduleGeneration).where(
                VisitPrepScheduleGeneration.visit_prep_id == root.id
            )
        ).all()
        credentials = session.exec(
            select(VisitPrepCredentialGeneration).where(
                VisitPrepCredentialGeneration.visit_prep_id == root.id
            )
        ).all()
        schedule = max(schedules, key=lambda row: row.generation)
        credential = max(credentials, key=lambda row: row.generation)
        assert credential.schedule_generation_id == schedule.id
        open_evidence = read_invitation_open_evidence(
            session, prep=root, at=session.exec(select(func.clock_timestamp())).one()
        )
        return TimingSnapshot(
            booked_at=credential.issued_at,
            scheduled_at=schedule.scheduled_at,
            credential_expires_at=credential.expires_at,
            timezone_name=schedule.timezone_name,
            schedule_generation=schedule.generation,
            credential_generation=credential.generation,
            lifecycle_state=cast(
                Literal[
                    "invited", "confirmed", "in_progress", "completed", "misdirected"
                ],
                root.lifecycle_state,
            ),
            first_party_open_state=open_evidence.state,
            opened_at=open_evidence.opened_at,
            completed_at=root.completed_at,
            revoked=root.revoked_at is not None or credential.revoked_at is not None,
        )


def _plan(fixture):
    return {
        d.step: d
        for d in plan_delivery_timing(
            _snapshot(fixture),
            now=datetime.now(UTC),
            quiet_hours=(21, 8),
            minimum_channel_spacing=timedelta(hours=4),
        )
    }


class _Command(TypedDict):
    owner_practice_member_id: TypeIDType
    owner_doctor_id: TypeIDType
    practice_id: TypeIDType
    visit_prep_id: TypeIDType
    expected_credential_generation: int


def _command(fixture, generation=1) -> _Command:
    return {
        "owner_practice_member_id": fixture.owner.id,
        "owner_doctor_id": fixture.doctor.id,
        "practice_id": fixture.practice.id,
        "visit_prep_id": fixture.prep_id,
        "expected_credential_generation": generation,
    }


@pytest.mark.parametrize("role", ["patient", "caregiver"])
def test_real_create_read_confirm_complete_observes_first_party_flow(monkeypatch, role):
    fixture = _fixture(monkeypatch, "timing-lifecycle", recipient_role=role)
    before = _snapshot(fixture)
    assert before.lifecycle_state == "invited"
    assert before.first_party_open_state == "unopened"
    get_interview(access_token=fixture.access_token)
    opened = _snapshot(fixture)
    assert opened.first_party_open_state == "opened"
    assert opened.lifecycle_state == "invited"
    assert opened.opened_at is not None
    assert _plan(fixture)["unopened_48h_sms"].reason == "already_opened"
    confirm_interview(access_token=fixture.access_token, first_name="José", role=role)
    assert _snapshot(fixture).lifecycle_state == "confirmed"
    assert _plan(fixture)["unopened_48h_sms"].reason == "already_opened"
    # Skip all five visible identities via the actual deterministic flow.
    for _ in range(5):
        state = get_interview(access_token=fixture.access_token)
        if state.current_question is None:
            break
        submit_response(
            access_token=fixture.access_token,
            idempotency_key=str(uuid4()),
            response=ResponseInput(
                question_key=state.current_question.key,
                response_kind="skipped",
            ),
        )
    state = get_interview(access_token=fixture.access_token)
    assert state.current_question is None
    complete_interview(access_token=fixture.access_token)
    assert _snapshot(fixture).lifecycle_state == "completed"
    assert {d.reason for d in _plan(fixture).values()} == {"completed"}


def test_real_rotation_reschedule_and_revocation_change_current_timing(monkeypatch):
    fixture = _fixture(monkeypatch, "timing-lineage")
    original = _snapshot(fixture)
    rotated = rotate_visit_prep_credential(**_command(fixture))
    after_rotation = _snapshot(fixture)
    assert (
        rotated.visit_prep.credential_generation
        == after_rotation.credential_generation
        == 2
    )
    assert after_rotation.schedule_generation == original.schedule_generation == 1
    assert after_rotation.scheduled_at == original.scheduled_at
    new_at = original.scheduled_at + timedelta(days=5)
    reschedule_visit_prep(
        **_command(fixture, 2),
        expected_schedule_generation=1,
        scheduled_at=new_at,
        timezone_name="UTC",
    )
    after_reschedule = _snapshot(fixture)
    assert after_reschedule.schedule_generation == 2
    assert after_reschedule.credential_generation == 3
    assert after_reschedule.scheduled_at == new_at
    assert _plan(fixture)["unopened_48h_sms"].cadence_due_at == new_at - timedelta(
        hours=48
    )
    revoke_visit_prep(**_command(fixture, 3), reason="appointment_cancelled")
    assert {d.reason for d in _plan(fixture).values()} == {"inactive"}
