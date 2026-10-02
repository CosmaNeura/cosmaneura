"""Real Core first-party flow evidence; synthetic owned PostgreSQL only."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from threading import Barrier, Event
from typing import TypedDict
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

import app.services.visit_prep_interviews as interviews
import app.services.visit_prep_open_evidence as evidence
import app.services.visit_preps as commands

from activemodel.session_manager import SessionManager, global_session
from activemodel.types import TypeIDType
from app.models.authority import AuthorityAuditEvent
from app.models.practice import Practice
from app.models.visit_prep import VisitPrep
from sqlalchemy import func, text
from sqlmodel import Session, select

from tests.integration.visit_prep_contribution_lock_test import _observe_waiting_lock
from tests.integration.visit_prep_interviews_lock_test import (
    _fixture as _deadline_fixture,
)
from tests.services.visit_prep_interviews_test import _fixture


def _rows(fixture, event_type):
    with Session(SessionManager.get_instance().get_engine()) as session:
        return session.exec(
            select(AuthorityAuditEvent).where(
                AuthorityAuditEvent.resource_ref == str(fixture.prep_id),
                AuthorityAuditEvent.event_type == event_type,
            )
        ).all()


def _read(fixture):
    # Synthetic evidence test only; no runtime scope/authorization is claimed.
    with Session(SessionManager.get_instance().get_engine()) as session:
        prep = session.exec(
            select(VisitPrep).where(VisitPrep.id == fixture.prep_id).with_for_update()
        ).one()
        return evidence.read_invitation_open_evidence(
            session, prep=prep, at=session.exec(select(func.clock_timestamp())).one()
        )


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
def test_creation_and_successful_flow_open_are_committed_without_identity_claim(
    monkeypatch, role
):
    fixture = _fixture(monkeypatch, "open-committed", recipient_role=role)
    before = _read(fixture)
    assert before.state == "unopened"
    assert before.opened_at is None
    assert len(_rows(fixture, evidence._COVERAGE)) == 1
    assert _rows(fixture, evidence._OPEN) == []
    state = interviews.get_interview(access_token=fixture.access_token)
    assert state.status == "confirmation_required"
    assert state.physician_display_name is None
    after = _read(fixture)
    assert after.state == "opened"
    assert after.opened_at is not None
    assert repr(after) == "InvitationOpenEvidence(<redacted>)"
    for row in [*_rows(fixture, evidence._COVERAGE), *_rows(fixture, evidence._OPEN)]:
        assert row.event_source == "platform_authoritative"
        assert row.outcome == "observed"
        assert (
            row.actor_id is row.person_id is row.tenant_id is row.interaction_id is None
        )
        assert (
            row.request_digest is row.content_sha256 is row.workload_client_id is None
        )
        assert fixture.access_token not in str(row.platform_refs)
        assert "Jos" not in str(row.platform_refs)
        assert row.client_event_id is not None
    with Session(SessionManager.get_instance().get_engine()) as session:
        prep = session.get(VisitPrep, fixture.prep_id)
        assert prep is not None
        assert prep.lifecycle_state == "invited"
        assert prep.confirmed_at is prep.started_at is prep.retain_until is None
    interviews.get_interview(access_token=fixture.access_token)
    assert _read(fixture) == after
    assert len(_rows(fixture, evidence._OPEN)) == 1


def test_actual_creation_replay_never_adds_coverage(monkeypatch):
    fixture = _fixture(monkeypatch, "open-create-replay")
    with Session(SessionManager.get_instance().get_engine()) as session:
        prep = session.get(VisitPrep, fixture.prep_id)
        assert prep is not None
        key, version = prep.creation_idempotency_key, prep.question_set_version_id
    replay = commands.create_visit_prep(
        owner_practice_member_id=fixture.owner.id,
        owner_doctor_id=fixture.doctor.id,
        practice_id=fixture.practice.id,
        idempotency_key=key,
        recipient=commands.RecipientInput(
            first_name="José", role="patient", email="patient@example.com"
        ),
        physician_display_name="Dr. Synthetic",
        scheduled_at=fixture.scheduled_at,
        timezone_name="UTC",
        question_set_version_id=version,
    )
    assert replay.disposition == "replayed"
    assert replay.access_token is None
    assert len(_rows(fixture, evidence._COVERAGE)) == 1
    assert _read(fixture).state == "unopened"


def test_historical_preparation_stays_unknown_without_backfill(monkeypatch):
    original = commands.stage_creation_coverage
    monkeypatch.setattr(commands, "stage_creation_coverage", lambda *_a, **_k: None)
    fixture = _fixture(monkeypatch, "open-historical")
    monkeypatch.setattr(commands, "stage_creation_coverage", original)
    assert _read(fixture).state == "unknown"
    assert _rows(fixture, evidence._COVERAGE) == []
    interviews.get_interview(access_token=fixture.access_token)
    assert _read(fixture).state == "opened"
    assert _rows(fixture, evidence._COVERAGE) == []


def test_confirmation_is_interaction_not_an_invented_flow_event(monkeypatch):
    fixture = _fixture(monkeypatch, "open-direct-confirm")
    interviews.confirm_interview(
        access_token=fixture.access_token, first_name="José", role="patient"
    )
    assert _rows(fixture, evidence._OPEN) == []
    assert _read(fixture).state == "unknown"
    interviews.get_interview(access_token=fixture.access_token)
    assert _read(fixture).state == "opened"


def test_first_flow_review_can_occur_after_direct_completion(monkeypatch):
    fixture = _fixture(monkeypatch, "open-after-completion")
    state = interviews.confirm_interview(
        access_token=fixture.access_token, first_name="José", role="patient"
    ).interview
    while state.current_question is not None:
        state = interviews.submit_response(
            access_token=fixture.access_token,
            idempotency_key=str(uuid4()),
            response=interviews.ResponseInput(
                question_key=state.current_question.key, response_kind="skipped"
            ),
        ).interview
    interviews.complete_interview(access_token=fixture.access_token)
    assert _read(fixture).state == "unknown"
    interviews.get_interview(access_token=fixture.access_token)
    observed = _read(fixture)
    assert observed.state == "opened" and observed.opened_at is not None
    with Session(SessionManager.get_instance().get_engine()) as session:
        prep = session.get(VisitPrep, fixture.prep_id)
        assert prep is not None and prep.completed_at is not None
        assert observed.opened_at >= prep.completed_at
        assert prep.lifecycle_state == "completed"


@pytest.mark.parametrize("kind", ["open", "coverage"])
@pytest.mark.parametrize(
    "change",
    ["outcome", "route", "workload", "identity", "refs", "future", "digest", "lineage"],
)
def test_closed_validator_rejects_malformed_single_evidence(monkeypatch, kind, change):
    fixture = _fixture(monkeypatch, "open-invalid-single")
    if kind == "open":
        interviews.get_interview(access_token=fixture.access_token)
    source = _rows(fixture, evidence._OPEN if kind == "open" else evidence._COVERAGE)[0]
    clone = AuthorityAuditEvent(**source.model_dump())
    if change == "outcome":
        clone.outcome = "allowed"
    elif change == "route":
        clone.route = "synthetic_untrusted_route"
    elif change == "workload":
        clone.workload_client_id = "synthetic_workload"
    elif change == "identity":
        clone.person_id = fixture.prep_id
    elif change == "refs":
        clone.platform_refs = []
    elif change == "future":
        clone.recorded_at = datetime.now(UTC) + timedelta(days=1)
    elif change == "digest":
        clone.request_digest = "a" * 64
    else:
        clone.platform_refs = [
            *clone.platform_refs[:-1],
            {"kind": "visit_prep_credential", "ref": "invalid_synthetic_ref"},
        ]
    with Session(SessionManager.get_instance().get_engine()) as session:
        prep = session.get(VisitPrep, fixture.prep_id)
        assert prep is not None
        with pytest.raises(evidence.InvitationOpenEvidenceInvalid):
            evidence._validate(
                session,
                prep,
                clone,
                at=session.exec(select(func.clock_timestamp())).one(),
            )
    # Invalid clone is diagnostic input only; immutable authoritative history
    # is not updated, and this is not a claim of compromised-storage repair.


def test_rotation_and_reschedule_preserve_original_open(monkeypatch):
    fixture = _fixture(monkeypatch, "open-rotation")
    interviews.get_interview(access_token=fixture.access_token)
    original = _read(fixture)
    rotated = commands.rotate_visit_prep_credential(**_command(fixture))
    assert rotated.access_token is not None
    with pytest.raises(interviews.VisitPrepInterviewUnavailable):
        interviews.get_interview(access_token=fixture.access_token)
    interviews.get_interview(access_token=rotated.access_token)
    assert _read(fixture) == original
    rescheduled = commands.reschedule_visit_prep(
        **_command(fixture, 2),
        expected_schedule_generation=1,
        scheduled_at=(fixture.scheduled_at + timedelta(days=1)).astimezone(
            ZoneInfo("America/New_York")
        ),
        timezone_name="America/New_York",
    )
    assert rescheduled.access_token is not None
    interviews.get_interview(access_token=rescheduled.access_token)
    assert _read(fixture) == original
    assert len(_rows(fixture, evidence._OPEN)) == 1
    assert len(_rows(fixture, evidence._COVERAGE)) == 1


def test_concurrent_resume_and_lost_acknowledgment_retain_one_open(monkeypatch):
    fixture = _fixture(monkeypatch, "open-concurrent")
    start = Barrier(3)

    def resume():
        start.wait(timeout=5)
        return interviews.get_interview(access_token=fixture.access_token)

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(resume) for _ in range(3)]
        assert all(
            f.result(timeout=10).status == "confirmation_required" for f in futures
        )
    assert len(_rows(fixture, evidence._OPEN)) == 1
    first = _read(fixture)
    original_commit = interviews._commit

    def commit_then_lose_ack(session):
        original_commit(session)
        raise RuntimeError("synthetic lost acknowledgment")

    monkeypatch.setattr(interviews, "_commit", commit_then_lose_ack)
    with pytest.raises(RuntimeError, match="synthetic lost acknowledgment"):
        interviews.get_interview(access_token=fixture.access_token)
    monkeypatch.setattr(interviews, "_commit", original_commit)
    interviews.get_interview(access_token=fixture.access_token)
    assert _read(fixture) == first
    assert len(_rows(fixture, evidence._OPEN)) == 1


def test_new_open_is_not_committed_when_request_rolls_back(monkeypatch):
    fixture = _fixture(monkeypatch, "open-rollback")
    original_session = interviews.get_session

    @contextmanager
    def request_session():
        with Session(SessionManager.get_instance().get_engine()) as session:
            try:
                yield session
            finally:
                session.rollback()

    monkeypatch.setattr(interviews, "get_session", request_session)
    monkeypatch.setattr(interviews, "_commit", lambda session: session.flush())
    interviews.get_interview(access_token=fixture.access_token)
    monkeypatch.setattr(interviews, "get_session", original_session)
    assert _rows(fixture, evidence._OPEN) == []
    assert _read(fixture).state == "unopened"


@pytest.mark.parametrize(
    "gate", ["disabled", "baa", "revoked", "wrong_recipient", "rotated", "malformed"]
)
def test_denied_flow_never_records_an_open(monkeypatch, gate):
    fixture = _fixture(monkeypatch, "open-denied")
    token = fixture.access_token
    if gate == "disabled":
        monkeypatch.setattr(interviews, "public_interview_api_enabled", lambda: False)
    elif gate == "baa":
        fixture.practice.negotiated_baa_on_file = False
        fixture.practice.save()
    elif gate == "revoked":
        commands.revoke_visit_prep(**_command(fixture), reason="appointment_cancelled")
    elif gate == "wrong_recipient":
        interviews.report_wrong_recipient(access_token=token)
    elif gate == "rotated":
        commands.rotate_visit_prep_credential(**_command(fixture))
    else:
        token = "invalid synthetic token"
    with pytest.raises(interviews.VisitPrepInterviewUnavailable):
        interviews.get_interview(access_token=token)
    assert _rows(fixture, evidence._OPEN) == []


@pytest.mark.parametrize("kind", ["open", "coverage"])
def test_duplicate_evidence_is_not_silently_selected(monkeypatch, kind):
    fixture = _fixture(monkeypatch, "open-bad-evidence")
    if kind == "open":
        interviews.get_interview(access_token=fixture.access_token)
    event_type = evidence._OPEN if kind == "open" else evidence._COVERAGE
    source = _rows(fixture, event_type)[0]
    values = {
        key: value
        for key, value in source.model_dump().items()
        if key not in {"id", "created_at", "updated_at"}
    }
    # Append-only history is not altered. Prove the bounded reader rejects
    # duplicate evidence rather than selecting a convenient row.
    with Session(SessionManager.get_instance().get_engine()) as session:
        session.add(AuthorityAuditEvent(**values))
        session.commit()
    with pytest.raises(evidence.InvitationOpenEvidenceInvalid) as caught:
        _read(fixture)
    assert str(caught.value) == "Visit Prep open evidence is unavailable."
    assert fixture.access_token not in str(caught.value)


def test_expiry_crossed_after_real_audit_flush_rolls_back_observation(monkeypatch):
    expiry = datetime.now(UTC) + timedelta(seconds=3)
    scheduled = expiry - timedelta(hours=72)
    fixture = _deadline_fixture(
        monkeypatch,
        "open-final-expiry",
        created_at=scheduled - timedelta(minutes=1),
        scheduled_at=scheduled,
    )
    original = interviews.stage_first_flow_open

    def flush_then_wait(session, **kwargs):
        original(session, **kwargs)
        session.execute(
            text(
                "SELECT pg_sleep(GREATEST(0, EXTRACT(EPOCH FROM (:expiry - clock_timestamp()))) + 0.1)"
            ),
            {"expiry": expiry},
        )

    monkeypatch.setattr(interviews, "stage_first_flow_open", flush_then_wait)
    with pytest.raises(interviews.VisitPrepInterviewUnavailable):
        interviews.get_interview(access_token=fixture.access_token)
    with Session(SessionManager.get_instance().get_engine()) as session:
        assert (
            session.exec(
                select(func.count())
                .select_from(AuthorityAuditEvent)
                .where(
                    AuthorityAuditEvent.resource_ref == str(fixture.visit_prep_id),
                    AuthorityAuditEvent.event_type == evidence._OPEN,
                )
            ).one()
            == 0
        )


def test_first_open_survives_lost_acknowledgment_without_duplicate(monkeypatch):
    fixture = _fixture(monkeypatch, "open-first-lost-ack")
    original_commit = interviews._commit

    def lose_ack(session):
        original_commit(session)
        raise RuntimeError("synthetic lost acknowledgment")

    monkeypatch.setattr(interviews, "_commit", lose_ack)
    with pytest.raises(RuntimeError, match="synthetic lost acknowledgment"):
        interviews.get_interview(access_token=fixture.access_token)
    first = _read(fixture)
    assert first.state == "opened"
    monkeypatch.setattr(interviews, "_commit", original_commit)
    interviews.get_interview(access_token=fixture.access_token)
    assert _read(fixture) == first
    assert len(_rows(fixture, evidence._OPEN)) == 1


@pytest.mark.parametrize("change", ["baa", "rotation", "wrong_recipient"])
def test_waiting_open_rechecks_actual_committed_security_change(monkeypatch, change):
    fixture = _fixture(monkeypatch, "open-security-wait")
    started = Event()

    def open_flow():
        with global_session() as session:
            session.execute(text("SET LOCAL application_name = 'vp_first_flow_wait'"))
            started.set()
            return interviews.get_interview(access_token=fixture.access_token)

    with global_session() as parent:
        parent.exec(
            select(Practice).where(Practice.id == fixture.practice.id).with_for_update()
        ).one()
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(open_flow)
            try:
                assert started.wait(timeout=5)
                _observe_waiting_lock(parent, "vp_first_flow_wait")
                if change == "baa":
                    fixture.practice.negotiated_baa_on_file = False
                    fixture.practice.save()
                elif change == "rotation":
                    commands.rotate_visit_prep_credential(**_command(fixture))
                else:
                    interviews.report_wrong_recipient(access_token=fixture.access_token)
            finally:
                parent.commit()
            with pytest.raises(interviews.VisitPrepInterviewUnavailable):
                future.result(timeout=10)
    assert _rows(fixture, evidence._OPEN) == []
