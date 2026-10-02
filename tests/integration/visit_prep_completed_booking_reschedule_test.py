"""Completed patient sources survive actual signed booking rescheduling."""

from dataclasses import replace
from datetime import datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.configuration import visit_prep
from app.routes import companion_visit_prep
from app.routes.visit_prep_synthesis import _synthesis_response
from app.services import visit_prep_access, visit_prep_booking
from app.services import visit_prep_booking_lifecycle as lifecycle
from app.services import visit_prep_interviews as interviews
from app.services.visit_prep_command_contract import VisitPrepConflict
from app.services.visit_prep_interview_contract import ResponseInput
from app.services.visit_prep_synthesis_worker import run_one_build
from app.services.visit_preps import reschedule_visit_prep, stage_visit_prep_reschedule

from activemodel.session_manager import get_engine
from app.models.authority import AuthorityAuditEvent
from app.models.visit_prep_synthesis_build import VisitPrepSynthesisBuild
from sqlalchemy import text
from sqlmodel import Session, select

from tests.authority_kernel.visit_prep_companion_auth_test import (
    signed as signed_identity,
)
from tests.integration.visit_prep_booking_lifecycle_ingress_test import (
    _changed,
    _current,
)
from tests.integration.visit_prep_booking_test import _post, _ready
from tests.integration.visit_prep_companion_http_test import _client
from tests.services.visit_prep_contribution_commands_test import _correct
from tests.services.visit_prep_model_test import (
    _change,
    _change_rejected,
    _credential,
    _insert_rejected,
    _schedule,
)
from tests.services.visit_prep_packages_test import _read
from tests.services.visit_prep_synthesis_publication_test import (
    _publish,
    _selected,
    _view,
)
from tests.services.visit_prep_synthesis_test import _brief, _fake

signed = signed_identity


def _completed(monkeypatch):
    scope, binding, settings, response, _, client = _ready(monkeypatch)
    admission = replace(visit_prep.VISIT_PREP_SETTINGS, public_api_enabled=True)
    monkeypatch.setattr(visit_prep, "VISIT_PREP_SETTINGS", admission)
    monkeypatch.setattr(visit_prep_access, "VISIT_PREP_SETTINGS", admission)
    create = visit_prep_booking.create_locked_visit_prep
    tokens = []

    def capture(*args, **kwargs):
        result = create(*args, **kwargs)
        tokens.append(result.access_token)
        return result

    monkeypatch.setattr(visit_prep_booking, "create_locked_visit_prep", capture)
    assert _post(client, settings).status_code == 200
    token = tokens[0]
    interviews.confirm_interview(access_token=token, first_name="José", role="patient")
    for _ in range(5):
        state = interviews.get_interview(access_token=token)
        question = state.current_question
        if question is None:
            break
        interviews.submit_response(
            access_token=token,
            idempotency_key=str(uuid4()),
            response=ResponseInput(
                question.key, "answered", "My invented patient story."
            )
            if question.kind == "story"
            else ResponseInput(question.key, "skipped"),
        )
    assert interviews.complete_interview(access_token=token).disposition == "completed"
    fixture = SimpleNamespace(
        owner=scope.owner,
        doctor=scope.doctor,
        practice=scope.practice,
        prep_id=_current(scope)[0].id,
        access_token=token,
    )
    return fixture, scope, binding, settings, response, client


def _clinical_snapshot(fixture):
    with Session(get_engine()) as database:
        return {
            table: database.execute(
                text(
                    f"SELECT to_jsonb(r)::text FROM {table} r WHERE visit_prep_id = :id ORDER BY id"
                ),
                {"id": fixture.prep_id.uuid},
            )
            .scalars()
            .all()
            for table in (
                "visit_prep_question_response",
                "visit_prep_contribution_event",
            )
        }


def test_completed_signed_reschedule_preserves_original_sources_and_refreshes_package(
    monkeypatch,
    signed,
):
    fixture, scope, _, settings, response, client = _completed(monkeypatch)
    _publish(fixture)
    case, companion, headers = _client(monkeypatch, signed, fixture=fixture)
    before = _clinical_snapshot(fixture)
    original = _current(scope)[0]
    admission = replace(visit_prep.VISIT_PREP_SETTINGS, auto_synthesis_enabled=True)
    monkeypatch.setattr(visit_prep, "VISIT_PREP_SETTINGS", admission)
    monkeypatch.setattr(visit_prep_access, "VISIT_PREP_SETTINGS", admission)
    response["datetime"] = (
        datetime.fromisoformat(response["datetime"]) + timedelta(days=2)
    ).isoformat()
    result = _changed(client, settings)
    assert result.status_code == 200, result.text
    prep, schedule, credential, events = _current(scope)
    assert (
        prep.lifecycle_state == "completed"
        and prep.completed_at == original.completed_at
    )
    assert (
        prep.confirmed_at == original.confirmed_at
        and prep.retain_until == original.retain_until
    )
    assert _clinical_snapshot(fixture) == before
    assert schedule.generation == credential.generation == 2 and len(events) == 1
    assert _read(fixture).preparation.scheduled_at == schedule.scheduled_at
    assert _view(fixture).status == "stale" and _view(fixture).build_status == "pending"
    with Session(get_engine()) as database:
        build = database.exec(
            select(VisitPrepSynthesisBuild).where(
                VisitPrepSynthesisBuild.visit_prep_id == prep.id
            )
        ).one()
        assert build.source_version == _selected(fixture).source_version
    _fake(monkeypatch, _brief(_selected(fixture)).model_dump_json())
    assert run_one_build() == "ready"
    assert _view(fixture).status == "ready" and _view(fixture).version == 2
    opened = companion.post(
        companion_visit_prep.PATH,
        headers=headers,
        json=case.request.model_dump(mode="json"),
    )
    assert opened.status_code == 200, opened.text
    assert opened.json()["package"] == _synthesis_response(_view(fixture)).model_dump(
        mode="json"
    )
    assert _changed(client, settings).json()["disposition"] == "replayed"
    assert run_one_build() == "idle" and _clinical_snapshot(fixture) == before


def test_completed_reschedule_does_not_queue_unreviewed_patient_edits(monkeypatch):
    fixture, scope, _, settings, response, client = _completed(monkeypatch)
    _correct(fixture, answer="My unreviewed invented update.")
    before = _clinical_snapshot(fixture)
    admission = replace(visit_prep.VISIT_PREP_SETTINGS, auto_synthesis_enabled=True)
    monkeypatch.setattr(visit_prep, "VISIT_PREP_SETTINGS", admission)
    monkeypatch.setattr(visit_prep_access, "VISIT_PREP_SETTINGS", admission)
    response["datetime"] = (
        datetime.fromisoformat(response["datetime"]) + timedelta(days=1)
    ).isoformat()
    result = _changed(client, settings)
    assert result.status_code == 200, result.text
    assert _current(scope)[0].lifecycle_state == "completed"
    with Session(get_engine()) as database:
        assert not database.exec(
            select(VisitPrepSynthesisBuild).where(
                VisitPrepSynthesisBuild.visit_prep_id == fixture.prep_id
            )
        ).all()
    assert _clinical_snapshot(fixture) == before


@pytest.mark.parametrize("failure", ["refresh_storage", "late_auto_gate"])
def test_completed_refresh_failure_rolls_back_schedule_credentials_event_and_queue(
    monkeypatch,
    failure,
):
    fixture, scope, _, settings, response, client = _completed(monkeypatch)
    before = _clinical_snapshot(fixture)
    admission = replace(visit_prep.VISIT_PREP_SETTINGS, auto_synthesis_enabled=True)
    monkeypatch.setattr(visit_prep, "VISIT_PREP_SETTINGS", admission)
    monkeypatch.setattr(visit_prep_access, "VISIT_PREP_SETTINGS", admission)

    stage_refresh = lifecycle.stage_booking_synthesis_refresh

    def fail(*args, **kwargs):
        if failure == "refresh_storage":
            raise RuntimeError("invented refresh storage failure")
        assert stage_refresh(*args, **kwargs)
        monkeypatch.setattr(
            visit_prep,
            "VISIT_PREP_SETTINGS",
            replace(admission, auto_synthesis_enabled=False),
        )
        return True

    monkeypatch.setattr(lifecycle, "stage_booking_synthesis_refresh", fail)
    response["datetime"] = (
        datetime.fromisoformat(response["datetime"]) + timedelta(days=1)
    ).isoformat()
    assert _changed(client, settings).status_code == 500
    _, schedule, credential, events = _current(scope)
    assert schedule.generation == credential.generation == 1 and not events
    assert _clinical_snapshot(fixture) == before
    with Session(get_engine()) as database:
        assert not database.exec(
            select(VisitPrepSynthesisBuild).where(
                VisitPrepSynthesisBuild.visit_prep_id == fixture.prep_id
            )
        ).all()
        assert not database.exec(
            select(AuthorityAuditEvent).where(
                AuthorityAuditEvent.resource_ref == str(fixture.prep_id),
                AuthorityAuditEvent.event_type == "visit_prep.booking_refresh_queued",
            )
        ).all()


def test_expired_credential_recovery_needs_internal_booking_lane_and_retention(
    monkeypatch,
):
    fixture, scope, _, _, _, _ = _completed(monkeypatch)
    _, schedule, credential, _ = _current(scope)
    effective = credential.expires_at + timedelta(hours=1)
    args = dict(
        owner_practice_member_id=scope.owner.id,
        owner_doctor_id=scope.doctor.id,
        practice_id=scope.practice.id,
        visit_prep_id=fixture.prep_id,
        expected_schedule_generation=1,
        expected_credential_generation=1,
        scheduled_at=effective + timedelta(days=1),
        timezone_name="UTC",
        now=effective,
    )
    with pytest.raises(VisitPrepConflict, match="expired"):
        reschedule_visit_prep(**args)
    with Session(get_engine()) as database:
        staged = stage_visit_prep_reschedule(database, **args, allow_expired=True)
        assert staged.visit_prep.schedule_generation == 2
        database.rollback()
    with Session(get_engine()) as database:
        with pytest.raises(VisitPrepConflict, match="retained"):
            stage_visit_prep_reschedule(
                database,
                **(args | {"scheduled_at": _current(scope)[0].retain_until}),
                allow_expired=True,
            )
        database.rollback()


@pytest.mark.parametrize("fault", ["past", "retention", "revoked", "rotation"])
def test_completed_database_guards_still_deny_invalid_lineage(monkeypatch, fault):
    _, scope, _, _, _, _ = _completed(monkeypatch)
    prep, schedule, credential, _ = _current(scope)
    when = schedule.scheduled_at + timedelta(days=1)
    if fault == "past":
        when = credential.issued_at - timedelta(days=1)
    elif fault == "retention":
        when = prep.retain_until
    elif fault == "revoked":
        _change(
            prep,
            revoked_at=credential.issued_at + timedelta(seconds=1),
            revoked_by_ref="actor:synthetic",
            revocation_reason="appointment_cancelled",
        )
    if fault != "rotation":
        _insert_rejected(
            _schedule(prep, when, 2), "inactive Visit Prep cannot be rescheduled"
        )
        return
    _change(
        credential,
        revoked_at=credential.issued_at + timedelta(seconds=1),
        revoked_by_ref="actor:synthetic",
        revocation_reason="rotated",
    )
    replacement_schedule = _schedule(prep, when, 2).save()
    _insert_rejected(
        _credential(prep, replacement_schedule, 2),
        "inactive Visit Prep cannot receive a credential",
    )


def test_completed_reschedule_preserves_schedule_credential_and_retention_immutability(
    monkeypatch,
):
    _, scope, _, settings, response, client = _completed(monkeypatch)
    response["datetime"] = (
        datetime.fromisoformat(response["datetime"]) + timedelta(days=1)
    ).isoformat()
    assert _changed(client, settings).status_code == 200
    prep, schedule, credential, _ = _current(scope)
    _change_rejected(schedule, "schedule generations are immutable", generation=3)
    _change_rejected(credential, "credential lineage is immutable", token_hash="e" * 64)
    _change_rejected(
        prep,
        "retention snapshot is immutable",
        retain_until=prep.retain_until + timedelta(days=1),
    )
