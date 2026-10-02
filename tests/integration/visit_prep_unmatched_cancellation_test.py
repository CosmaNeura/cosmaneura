"""Real signed source ordering: a delayed booking cannot undo cancellation."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from threading import Event
from uuid import uuid4

import pytest

from app.configuration import visit_prep_acuity
from app.configuration.visit_prep_acuity import AcuityBookingSettings
from app.routes import visit_prep_acuity as routes
from app.services import visit_prep_acuity as adapter
from app.services import visit_prep_booking_lifecycle as lifecycle
from app.services.visit_prep_booking import record_current_booking
from app.services.visit_prep_booking_contract import BookingSourceUnavailable
from app.services.visit_prep_packages import _database_now

from activemodel.session_manager import get_engine
from app.models.visit_prep_booking_cancellation import VisitPrepBookingCancellation
from sqlalchemy import text
from sqlmodel import Session, select

from tests.integration.visit_prep_booking_lifecycle_ingress_test import _changed
from tests.integration.visit_prep_booking_test import _post, _ready, _rows


def _fences(settings):
    with Session(get_engine()) as database:
        return database.exec(
            select(VisitPrepBookingCancellation)
            .where(VisitPrepBookingCancellation.connection_id == settings.connection_id)
            .order_by(VisitPrepBookingCancellation.sequence)
        ).all()


@pytest.mark.parametrize("lane", ["scheduled", "changed"])
def test_delayed_active_fetch_cannot_create_after_current_cancellation(
    monkeypatch, lane
):
    scope, _, settings, response, _, client = _ready(monkeypatch)
    captured, release = Event(), Event()
    name = (
        "fetch_current_booking"
        if lane == "scheduled"
        else "fetch_current_booking_state"
    )
    fetch = getattr(routes, name)

    def delayed(**arguments):
        booking = fetch(**arguments)
        if not captured.is_set():
            captured.set()
            assert release.wait(20), "release the owned synthetic fetch"
        return booking

    monkeypatch.setattr(routes, name, delayed)
    with client, ThreadPoolExecutor(max_workers=1) as executor:
        slow = executor.submit(
            _post if lane == "scheduled" else _changed, client, settings
        )
        try:
            assert captured.wait(20), "capture the owned synthetic active source"
            response.clear()
            response.update(id=54321, calendarID=27238, canceled=True)
            cancellation = _changed(client, settings)
            assert cancellation.status_code == 200
            assert cancellation.json()["disposition"] == "not_found"
            assert _rows(scope) == ([], [])
            assert len(_fences(settings)) == 1
        finally:
            release.set()
        stale = slow.result(timeout=20)
        assert stale.status_code == 500
    assert _rows(scope) == ([], []) and len(_fences(settings)) == 1
    assert "vp1_" not in stale.text and "patient@example.com" not in stale.text


def test_repeated_cancellation_advances_fence_and_fresh_active_source_can_create(
    monkeypatch,
):
    scope, binding, settings, response, _, client = _ready(monkeypatch)
    active_response = dict(response)
    with client:
        response["canceled"] = True
        assert _changed(client, settings).status_code == 200
        first = _fences(settings)[0]
        response["canceled"] = False
        with Session(get_engine()) as database:
            observed = _database_now(database)
        active = adapter.fetch_current_booking(
            appointment_id=54321, binding=binding, settings=settings
        )
        source, booking = adapter.project_current_booking(
            booking=active, binding=binding, settings=settings
        )
        response["canceled"] = True
        assert _changed(client, settings).status_code == 200
        fences = _fences(settings)
        assert [row.sequence for row in fences] == [1, 2]
        assert fences[0].recorded_at == first.recorded_at
        assert fences[1].recorded_at > observed
        with pytest.raises(BookingSourceUnavailable):
            record_current_booking(
                booking=booking,
                source=source,
                observed_at=observed,
                require_current_source=lambda: adapter.require_current_booking_source(
                    binding, settings
                ),
            )
        assert _rows(scope) == ([], [])
        response.clear()
        response.update(active_response)
        current = _post(client, settings)
        assert current.status_code == 200 and current.json()["disposition"] == "created"
        assert _post(client, settings).json()["disposition"] == "replayed"
    assert tuple(map(len, _rows(scope))) == (1, 1)
    assert len(_fences(settings)) == 2


def test_cancel_evidence_rolls_back_if_current_source_disables_after_stage(monkeypatch):
    scope, _, settings, response, _, client = _ready(monkeypatch)
    stage = lifecycle.stage_unmatched_cancellation

    def disable_after_write(*arguments, **options):
        stage(*arguments, **options)
        monkeypatch.setattr(visit_prep_acuity, "SETTINGS", AcuityBookingSettings())

    monkeypatch.setattr(lifecycle, "stage_unmatched_cancellation", disable_after_write)
    response["canceled"] = True
    with client:
        assert _changed(client, settings).status_code == 500
    assert not _fences(settings) and _rows(scope) == ([], [])


@pytest.mark.parametrize("fault", ["sequence", "stale", "future", "owner"])
def test_database_rejects_invalid_cancellation_lineage(monkeypatch, fault):
    scope, _, settings, response, _, client = _ready(monkeypatch)
    response["canceled"] = True
    with client:
        assert _changed(client, settings).status_code == 200
    prior = _fences(settings)[0]
    with Session(get_engine()) as database:
        now = _database_now(database)
        values = dict(
            connection_id=prior.connection_id,
            source=prior.source,
            booking_digest=prior.booking_digest,
            source_snapshot_hmac=prior.source_snapshot_hmac,
            owner_practice_member_id=scope.owner.id,
            owner_doctor_id=scope.doctor.id,
            practice_id=scope.practice.id,
            sequence=2,
            observed_at=now,
            recorded_at=now,
        )
        if fault == "sequence":
            values["sequence"] = 3
        elif fault == "stale":
            values["observed_at"] = prior.recorded_at - timedelta(seconds=1)
        elif fault == "future":
            values["recorded_at"] = now + timedelta(hours=1)
        else:
            other, *_ = _ready(monkeypatch)
            values.update(
                owner_practice_member_id=other.owner.id,
                owner_doctor_id=other.doctor.id,
                practice_id=other.practice.id,
            )
        database.add(VisitPrepBookingCancellation(**values))
        with pytest.raises(Exception, match="Visit Prep cancellation"):
            database.commit()
        database.rollback()
    assert len(_fences(settings)) == 1 and _rows(scope) == ([], [])


def test_cancellation_evidence_is_immutable_and_only_for_unmatched_bookings(
    monkeypatch,
):
    scope, _, settings, response, _, client = _ready(monkeypatch)
    response["canceled"] = True
    with client:
        assert _changed(client, settings).status_code == 200
    prior = _fences(settings)[0]
    for statement in (
        "UPDATE visit_prep_booking_cancellation SET source_snapshot_hmac = :digest WHERE id = :id",
        "DELETE FROM visit_prep_booking_cancellation WHERE id = :id",
    ):
        with Session(get_engine()) as database:
            with pytest.raises(Exception, match="immutable"):
                database.execute(
                    text(statement), {"id": prior.id.uuid, "digest": "b" * 64}
                )
            database.rollback()
    response["canceled"] = False
    with client:
        assert _post(client, settings).status_code == 200
    with Session(get_engine()) as database:
        now = _database_now(database)
        database.add(
            VisitPrepBookingCancellation(
                connection_id=prior.connection_id,
                source=prior.source,
                booking_digest=prior.booking_digest,
                source_snapshot_hmac=prior.source_snapshot_hmac,
                owner_practice_member_id=scope.owner.id,
                owner_doctor_id=scope.doctor.id,
                practice_id=scope.practice.id,
                sequence=2,
                observed_at=now,
                recorded_at=now,
            )
        )
        # No cancellation stream can be appended once a preparation owns
        # the ordinary lifecycle evidence, even with otherwise valid lineage.
        with pytest.raises(Exception, match="Visit Prep cancellation"):
            database.commit()
        database.rollback()
    assert tuple(map(len, _rows(scope))) == (1, 1)


def test_connection_scopes_do_not_share_cancellation_fences(monkeypatch):
    scope, _, settings, response, _, client = _ready(monkeypatch)
    response["canceled"] = True
    with client:
        assert _changed(client, settings).status_code == 200
        changed_connection = replace(settings, connection_id=uuid4())
        monkeypatch.setattr(visit_prep_acuity, "SETTINGS", changed_connection)
        response["canceled"] = False
        assert _post(client, changed_connection).status_code == 200
    assert tuple(map(len, _rows(scope))) == (1, 1)
    assert not _fences(changed_connection) and len(_fences(settings)) == 1
