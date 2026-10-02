"""Actual signed source lifecycle, current-state retries and atomic rollback."""

import base64
import hashlib
import hmac
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from app.configuration import visit_prep, visit_prep_acuity
from app.configuration.visit_prep_acuity import AcuityBookingSettings
from app.routes.visit_prep_acuity import LIFECYCLE_PATH
from app.services import visit_prep_access
from app.services import visit_prep_acuity as adapter
from app.services import visit_prep_booking as creator
from app.services import visit_prep_booking_lifecycle as lifecycle
from app.services.visit_prep_booking_contract import BookingSourceUnavailable
from app.services.visit_prep_interviews import (
    ResponseInput,
    confirm_interview,
    get_interview,
    submit_response,
)
from app.services.visit_prep_packages import _database_now, list_visit_preps
from app.services.visit_preps import _lock_current_lineage

from activemodel.session_manager import get_engine
from app.models.visit_prep import VisitPrep
from app.models.visit_prep_booking import VisitPrepBookingReceipt
from app.models.visit_prep_booking_event import VisitPrepBookingEvent
from app.models.visit_prep_delivery import VisitPrepEmailDelivery
from sqlalchemy import text
from sqlmodel import Session, select

from tests.integration.visit_prep_booking_test import _post, _ready


def _changed(client, settings, *, action="changed", bad_signature=False):
    body = f"action={action}&id=54321&calendarID=27238&appointmentTypeID=12".encode()
    signature = base64.b64encode(
        hmac.new(settings.api_key.encode(), body, hashlib.sha256).digest()
    ).decode()
    return client.post(
        LIFECYCLE_PATH,
        content=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Acuity-Signature": "A" * 44 if bad_signature else signature,
        },
    )


def _current(scope):
    with Session(get_engine()) as database:
        prep = database.exec(
            select(VisitPrep).where(
                VisitPrep.owner_practice_member_id == scope.owner.id
            )
        ).one()
        schedule, credential = _lock_current_lineage(database, visit_prep=prep)
        events = database.exec(
            select(VisitPrepBookingEvent)
            .where(VisitPrepBookingEvent.visit_prep_id == prep.id)
            .order_by(VisitPrepBookingEvent.sequence)
        ).all()
        assert not database.exec(
            select(VisitPrepEmailDelivery).where(
                VisitPrepEmailDelivery.visit_prep_id == prep.id
            )
        ).all()
        assert (
            len(
                database.exec(
                    select(VisitPrepBookingReceipt).where(
                        VisitPrepBookingReceipt.visit_prep_id == prep.id
                    )
                ).all()
            )
            == 1
        )
        return prep, schedule, credential, events


@pytest.mark.parametrize("action", ["changed", "rescheduled", "canceled"])
def test_notifications_refetch_current_schedule_and_commit_once(monkeypatch, action):
    scope, _, settings, response, calls, client = _ready(monkeypatch)
    with client:
        assert _post(client, settings).status_code == 200
        original = _current(scope)
        response["datetime"] = (
            datetime.fromisoformat(response["datetime"]) + timedelta(days=1)
        ).isoformat()
        changed = _changed(client, settings, action=action)
        assert changed.status_code == 200, changed.text
        assert changed.json()["disposition"] == "rescheduled"
        replay = _changed(client, settings, action=action)
        assert replay.status_code == 200 and replay.json()["disposition"] == "replayed"
    prep, schedule, credential, events = _current(scope)
    assert prep.id == original[0].id
    assert (
        prep.recipient_snapshot_ciphertext == original[0].recipient_snapshot_ciphertext
    )
    assert schedule.generation == credential.generation == 2
    assert len(events) == 1 and events[0].action == "rescheduled"
    assert "vp1_" not in changed.text + replay.text
    assert len(calls) == 3
    listed = list_visit_preps(
        owner_practice_member_id=scope.owner.id,
        owner_doctor_id=scope.doctor.id,
        practice_id=scope.practice.id,
        expected_clerk_subject=scope.doctor.clerk_id,
    )
    assert listed.preparations[0].scheduled_at == schedule.scheduled_at


def test_current_cancellation_accepts_tombstone_and_does_not_copy_patient_fields(
    monkeypatch,
):
    scope, _, settings, response, _, client = _ready(monkeypatch)
    with client:
        assert _post(client, settings).status_code == 200
        response.clear()
        response.update(id=54321, calendarID=27238, canceled=True)
        cancelled = _changed(client, settings, action="rescheduled")
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["disposition"] == "cancelled"
        assert _changed(client, settings).json()["disposition"] == "replayed"
    prep, schedule, credential, events = _current(scope)
    assert prep.revoked_at and credential.revoked_at
    assert (
        prep.revocation_reason
        == credential.revocation_reason
        == "appointment_cancelled"
    )
    assert schedule.generation == credential.generation == len(events) == 1


@pytest.mark.parametrize("also_rescheduled", [False, True])
def test_contact_change_revokes_original_without_reassigning_content(
    monkeypatch, also_rescheduled
):
    scope, _, settings, response, _, client = _ready(monkeypatch)
    with client:
        assert _post(client, settings).status_code == 200
        original = _current(scope)[0]
        response["email"] = "different@example.com"
        if also_rescheduled:
            response["datetime"] = (
                datetime.fromisoformat(response["datetime"]) + timedelta(days=1)
            ).isoformat()
        changed = _changed(client, settings)
        assert changed.status_code == 200, changed.text
        assert changed.json()["disposition"] == "recipient_changed"
        assert _changed(client, settings).json()["disposition"] == "replayed"
    prep, _, credential, events = _current(scope)
    assert prep.recipient_snapshot_ciphertext == original.recipient_snapshot_ciphertext
    assert prep.revoked_at and credential.revoked_at
    assert prep.patient_id is prep.appointment_id is None
    assert len(events) == 1 and events[0].action == "recipient_changed"


def test_catchall_initial_booking_creates_once_and_unknown_cancellation_is_noop(
    monkeypatch,
):
    scope, _, settings, response, _, client = _ready(monkeypatch)
    with client:
        response["canceled"] = True
        result = _changed(client, settings)
        assert result.status_code == 200 and result.json()["disposition"] == "not_found"
        response["canceled"] = False
        result = _changed(client, settings)
        assert result.status_code == 200 and result.json()["disposition"] == "created"
        assert _changed(client, settings).json()["disposition"] == "replayed"
    assert not _current(scope)[3]


@pytest.mark.parametrize("fault", ["signature", "malformed", "calendar", "disabled"])
def test_invalid_current_source_has_no_lifecycle_effect(monkeypatch, fault):
    scope, _, settings, response, calls, client = _ready(monkeypatch)
    with client:
        assert _post(client, settings).status_code == 200
        response["datetime"] = (
            datetime.fromisoformat(response["datetime"]) + timedelta(days=1)
        ).isoformat()
        if fault == "malformed":
            response["canceled"] = "false"
        elif fault == "calendar":
            response["calendarID"] = 99
        elif fault == "disabled":
            monkeypatch.setattr(visit_prep_acuity, "SETTINGS", AcuityBookingSettings())
        result = _changed(client, settings, bad_signature=fault == "signature")
        assert result.status_code != 200
    prep, schedule, credential, events = _current(scope)
    assert schedule.generation == credential.generation == 1
    assert prep.revoked_at is credential.revoked_at is None and not events
    assert len(calls) == (1 if fault in {"signature", "disabled"} else 2)
    assert "patient@example.com" not in result.text


@pytest.mark.parametrize("fault", ["gate", "receipt"])
def test_late_failure_rolls_back_schedule_credential_and_receipt(monkeypatch, fault):
    scope, _, settings, response, _, client = _ready(monkeypatch)
    stage = lifecycle.stage_visit_prep_reschedule

    def fail_after_staging(*args, **kwargs):
        result = stage(*args, **kwargs)
        if fault == "gate":
            monkeypatch.setattr(visit_prep_acuity, "SETTINGS", AcuityBookingSettings())
        else:
            monkeypatch.setattr(
                lifecycle,
                "VisitPrepBookingEvent",
                lambda **_: (_ for _ in ()).throw(
                    RuntimeError("invented storage failure")
                ),
            )
        return result

    with client:
        assert _post(client, settings).status_code == 200
        response["datetime"] = (
            datetime.fromisoformat(response["datetime"]) + timedelta(days=1)
        ).isoformat()
        monkeypatch.setattr(
            lifecycle, "stage_visit_prep_reschedule", fail_after_staging
        )
        result = _changed(client, settings)
        assert result.status_code == 500
    prep, schedule, credential, events = _current(scope)
    assert schedule.generation == credential.generation == 1
    assert prep.revoked_at is credential.revoked_at is None and not events


def test_late_provider_response_cannot_overwrite_newer_committed_schedule(monkeypatch):
    scope, binding, settings, response, _, client = _ready(monkeypatch)
    with client:
        assert _post(client, settings).status_code == 200
        with Session(get_engine()) as database:
            observed = _database_now(database)
        booking = adapter.fetch_current_booking_state(
            appointment_id=54321, binding=binding, settings=settings
        )
        source, stale = adapter.project_current_booking_state(
            booking=booking, binding=binding, settings=settings, observed_at=observed
        )
        response["datetime"] = (
            datetime.fromisoformat(response["datetime"]) + timedelta(days=1)
        ).isoformat()
        assert _changed(client, settings).status_code == 200
    with pytest.raises(BookingSourceUnavailable):
        lifecycle.record_current_booking_state(
            state=stale,
            source=source,
            require_current_source=lambda: adapter.require_current_booking_source(
                binding, settings
            ),
        )
    assert _current(scope)[1].generation == 2 and len(_current(scope)[3]) == 1


def test_genuine_return_to_earlier_schedule_is_not_a_stale_hash_replay(monkeypatch):
    scope, _, settings, response, _, client = _ready(monkeypatch)
    original = response["datetime"]
    with client:
        assert _post(client, settings).status_code == 200
        response["datetime"] = (
            datetime.fromisoformat(original) + timedelta(days=1)
        ).isoformat()
        assert _changed(client, settings).status_code == 200
        response["datetime"] = original
        assert _changed(client, settings).json()["disposition"] == "rescheduled"
    _, schedule, credential, events = _current(scope)
    assert schedule.generation == credential.generation == 3
    assert [event.sequence for event in events] == [1, 2]


def test_reschedule_retains_confirmed_patient_words_and_initial_confirmation(
    monkeypatch,
):
    scope, _, settings, response, _, client = _ready(monkeypatch)
    create = creator.create_locked_visit_prep
    tokens = []

    def capture(*args, **kwargs):
        result = create(*args, **kwargs)
        tokens.append(result.access_token)
        return result

    monkeypatch.setattr(creator, "create_locked_visit_prep", capture)
    admission = replace(visit_prep.VISIT_PREP_SETTINGS, public_api_enabled=True)
    monkeypatch.setattr(visit_prep, "VISIT_PREP_SETTINGS", admission)
    monkeypatch.setattr(visit_prep_access, "VISIT_PREP_SETTINGS", admission)
    with client:
        assert _post(client, settings).status_code == 200
        confirmed = confirm_interview(
            access_token=tokens[0], first_name="José", role="patient"
        )
        question = get_interview(access_token=tokens[0]).current_question
        assert question is not None
        submit_response(
            access_token=tokens[0],
            idempotency_key="dc533747-cd68-472c-af78-d986c1c2662a",
            response=ResponseInput(
                question.key, "answered", "My invented reason for this visit."
            ),
        )
        with Session(get_engine()) as database:
            before = (
                database.execute(
                    text(
                        "SELECT to_jsonb(r)::text FROM visit_prep_question_response r WHERE visit_prep_id = :id"
                    ),
                    {"id": _current(scope)[0].id.uuid},
                )
                .scalars()
                .all()
            )
        response["datetime"] = (
            datetime.fromisoformat(response["datetime"]) + timedelta(days=1)
        ).isoformat()
        assert _changed(client, settings).status_code == 200
        with Session(get_engine()) as database:
            after = (
                database.execute(
                    text(
                        "SELECT to_jsonb(r)::text FROM visit_prep_question_response r WHERE visit_prep_id = :id"
                    ),
                    {"id": _current(scope)[0].id.uuid},
                )
                .scalars()
                .all()
            )
    assert before == after and before
    assert _current(scope)[0].confirmed_at == confirmed.continuation.confirmed_at


def test_concurrent_duplicate_source_change_commits_once_and_retry_refetches(
    monkeypatch,
):
    scope, _, settings, response, _, client = _ready(monkeypatch)
    with client:
        assert _post(client, settings).status_code == 200
        response["datetime"] = (
            datetime.fromisoformat(response["datetime"]) + timedelta(days=1)
        ).isoformat()
        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(lambda _: _changed(client, settings), range(2)))
        assert any(result.status_code == 200 for result in results)
        assert all(result.status_code in {200, 500} for result in results)
        assert _changed(client, settings).json()["disposition"] == "replayed"
    _, schedule, credential, events = _current(scope)
    assert schedule.generation == credential.generation == 2 and len(events) == 1
