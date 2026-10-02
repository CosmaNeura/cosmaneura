"""Invented patient → durable worker → dashboard/Companion workflow.

Owned PostgreSQL, actual signed Core Companion requests; only the external AI
transport/de-identification result is synthetic. No messaging or deployment.
"""

from dataclasses import replace
from uuid import uuid4

import pytest

import app.services.visit_prep_patient_handoff as handoff
from app.configuration.visit_prep import load_visit_prep_settings
from app.routes import companion_visit_prep
from app.routes.visit_prep_synthesis import _synthesis_response
from app.services import visit_prep_access
from app.services import visit_prep_interviews as interviews
from app.services.visit_prep_interview_contract import VisitPrepInterviewUnavailable
from app.services.visit_prep_synthesis_builds import (
    VisitPrepBuildDiscarded,
    claim_visit_prep_synthesis,
    fail_visit_prep_synthesis_claim,
    load_claimed_synthesis_sources,
)
from app.services.visit_prep_synthesis_worker import run_one_build

from activemodel.session_manager import get_session
from app.models.authority import AuthorityAuditEvent
from app.models.visit_prep import VisitPrep
from app.models.visit_prep_synthesis_build import VisitPrepSynthesisBuild
from sqlmodel import select

from tests.authority_kernel.visit_prep_companion_auth_test import (
    signed as signed_identity,
)
from tests.integration.visit_prep_companion_http_test import _client
from tests.services.visit_prep_contribution_commands_test import _correct, _review
from tests.services.visit_prep_contribution_projection_test import _prepared
from tests.services.visit_prep_interviews_test import _fixture, _respond
from tests.services.visit_prep_synthesis_builds_test import _build
from tests.services.visit_prep_synthesis_publication_test import _rows, _selected, _view
from tests.services.visit_prep_synthesis_test import _brief, _fake
from tests.services.visit_prep_synthesis_usage_test import _events

signed = signed_identity


def _enable(monkeypatch):
    monkeypatch.setattr(
        visit_prep_access,
        "VISIT_PREP_SETTINGS",
        replace(visit_prep_access.VISIT_PREP_SETTINGS, auto_synthesis_enabled=True),
    )


def _queue_rows(fixture):
    with get_session() as session:
        return session.exec(
            select(VisitPrepSynthesisBuild).where(
                VisitPrepSynthesisBuild.visit_prep_id == fixture.prep_id
            )
        ).all()


def _finish_job(fixture):
    claim = claim_visit_prep_synthesis()
    assert claim is not None and claim.visit_prep_id == fixture.prep_id
    assert fail_visit_prep_synthesis_claim(claim, code="scope_unavailable")


def test_submission_and_confirmed_correction_deliver_same_package_to_both_surfaces(
    monkeypatch, signed
):
    case, client, headers = _client(monkeypatch, signed)
    fixture = case.fixture
    assert not _queue_rows(fixture)  # dark is the default
    _enable(monkeypatch)
    # A retry of explicit completion also recovers an interrupted handoff.
    interviews.complete_interview(access_token=fixture.access_token)
    first = _build(fixture)
    assert first.state == "pending" and first.epoch == 1 and first.attempts == 0
    assert not _events(fixture) and not _rows(fixture)  # no model in patient HTTP
    initial = _selected(fixture)
    _fake(monkeypatch, _brief(initial).model_dump_json())
    assert run_one_build() == "ready"

    def same_current_package(version):
        dashboard = _synthesis_response(_view(fixture)).model_dump(mode="json")
        response = client.post(
            companion_visit_prep.PATH,
            headers=headers,
            json=case.request.model_dump(mode="json"),
        )
        assert response.status_code == 200, response.text
        assert response.json()["package"] == dashboard
        assert dashboard["status"] == "ready" and dashboard["version"] == version
        assert dashboard["sources"]["preparation"]["patient_id"] is None

    same_current_package(1)
    corrected = _correct(fixture, answer="My invented corrected interval story.")
    assert _view(fixture).status == "stale"
    assert _build(fixture).epoch == 1  # unconfirmed edit does not start AI
    interviews.complete_interview(access_token=fixture.access_token)
    assert _build(fixture).epoch == 1  # completion replay is not review consent
    key = str(uuid4())
    _review(fixture, basis=corrected.basis, key=key)
    second = _build(fixture)
    assert second.id == first.id and second.epoch == 2 and second.state == "pending"
    _review(fixture, basis=corrected.basis, key=key)
    assert _build(fixture).epoch == 2  # exact replay does not add a job
    current = _selected(fixture)
    _fake(monkeypatch, _brief(current).model_dump_json())
    assert run_one_build() == "ready"
    same_current_package(2)
    assert len(_queue_rows(fixture)) == 1 and len(_events(fixture)) == 4
    assert len(_rows(fixture)) == 2 and run_one_build() == "idle"
    with get_session() as session:
        audits = session.exec(
            select(AuthorityAuditEvent).where(
                AuthorityAuditEvent.resource_ref == str(fixture.prep_id),
                AuthorityAuditEvent.event_type == "visit_prep.patient_handoff_queued",
            )
        ).all()
    assert audits and all(a.intent == "patient_submission_handoff" for a in audits)
    assert fixture.access_token not in repr(audits)


@pytest.mark.parametrize("role", ["patient", "caregiver"])
def test_first_completion_queues_once_and_replay_preserves_live_lease(
    monkeypatch, role
):
    fixture = _fixture(monkeypatch, "handoff-complete", recipient_role=role)
    _enable(monkeypatch)
    interviews.confirm_interview(
        access_token=fixture.access_token, first_name="José", role=role
    )
    for question, answer in (
        ("reason", "My invented concern."),
        ("medications", None),
        ("adherence", "yes"),
        ("story", "My invented story."),
    ):
        _respond(fixture, question, answer=answer)
    assert not _queue_rows(fixture)
    interviews.complete_interview(access_token=fixture.access_token)
    assert _build(fixture).source_version == _selected(fixture).source_version
    claim = claim_visit_prep_synthesis()
    assert claim is not None and claim.visit_prep_id == fixture.prep_id
    interviews.complete_interview(access_token=fixture.access_token)
    row = _build(fixture)
    assert (row.epoch, row.attempts, row.lease_until) == (
        claim.epoch,
        claim.attempt,
        claim.lease_until,
    )
    assert fail_visit_prep_synthesis_claim(claim, code="scope_unavailable")


def test_queued_automatic_job_cannot_adopt_an_unconfirmed_patient_revision(monkeypatch):
    fixture = _prepared(monkeypatch, "handoff-unconfirmed")
    _enable(monkeypatch)
    interviews.complete_interview(access_token=fixture.access_token)
    claim = claim_visit_prep_synthesis()
    assert claim is not None and claim.visit_prep_id == fixture.prep_id
    corrected = _correct(fixture)
    with pytest.raises(VisitPrepBuildDiscarded):
        load_claimed_synthesis_sources(claim)
    assert not _events(fixture) and not _rows(fixture)
    assert _build(fixture).state == "failed"
    _review(fixture, basis=corrected.basis)
    assert _build(fixture).state == "pending" and _build(fixture).epoch == 2
    _finish_job(fixture)


def test_disabling_automatic_generation_stops_an_already_queued_handoff(monkeypatch):
    fixture = _prepared(monkeypatch, "handoff-stop")
    _enable(monkeypatch)
    interviews.complete_interview(access_token=fixture.access_token)
    monkeypatch.setattr(
        visit_prep_access,
        "VISIT_PREP_SETTINGS",
        replace(visit_prep_access.VISIT_PREP_SETTINGS, auto_synthesis_enabled=False),
    )
    assert run_one_build() == "discarded"
    assert _build(fixture).state == "failed"
    assert not _events(fixture) and not _rows(fixture)


def test_late_gate_failure_rolls_back_patient_completion_and_queued_work(monkeypatch):
    fixture = _fixture(monkeypatch, "handoff-rollback")
    _enable(monkeypatch)
    interviews.confirm_interview(
        access_token=fixture.access_token, first_name="José", role="patient"
    )
    for question, answer in (
        ("reason", "Invented concern."),
        ("medications", None),
        ("adherence", "yes"),
        ("story", "Invented story."),
    ):
        _respond(fixture, question, answer=answer)
    original = handoff.stage_synthesis_request

    def disable_after_staging(*args, **kwargs):
        result = original(*args, **kwargs)
        monkeypatch.setattr(
            visit_prep_access,
            "VISIT_PREP_SETTINGS",
            replace(
                visit_prep_access.VISIT_PREP_SETTINGS, auto_synthesis_enabled=False
            ),
        )
        return result

    monkeypatch.setattr(handoff, "stage_synthesis_request", disable_after_staging)
    with pytest.raises(VisitPrepInterviewUnavailable):
        interviews.complete_interview(access_token=fixture.access_token)
    assert not _queue_rows(fixture)
    with get_session() as session:
        prep = session.get(VisitPrep, fixture.prep_id)
        assert prep.lifecycle_state == "in_progress" and prep.completed_at is None


def test_automatic_handoff_configuration_is_explicit_and_fail_closed():
    assert not load_visit_prep_settings({}).auto_synthesis_enabled
    assert load_visit_prep_settings(
        {"VISIT_PREP_AUTO_SYNTHESIS_ENABLED": "true"}
    ).auto_synthesis_enabled
    invalid = load_visit_prep_settings(
        {"FF_VISIT_PREP_202608": "true", "VISIT_PREP_AUTO_SYNTHESIS_ENABLED": "typo"}
    )
    assert not invalid.configuration_valid and not invalid.enabled
    assert not invalid.auto_synthesis_enabled
