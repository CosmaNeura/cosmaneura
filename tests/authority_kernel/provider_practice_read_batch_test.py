"""Provider-record practice reads preserve strict Session Prep access policy."""

from datetime import UTC, datetime, timedelta

import pytest

from app.services import provider_record_authority as records
from app.services import scribe_capture_gateway as gateway
from app.services import scribe_native_capture as native_service
from app.services.session_prep_provider_access import (
    SessionPrepProviderAccessDecision,
)

from app.models.doctor import Doctor
from app.models.practice import Practice, PracticeMember, lock_practice_seats
from sqlalchemy import event
from sqlalchemy.orm.attributes import set_committed_value
from sqlmodel import Session, select

from tests.authority_kernel.companion_provider_provisioning_test import session_for
from tests.authority_kernel.doctor_access_query_equivalence_test import scenario
from tests.authority_kernel.scribe_native_status_enrichment_test import query_counts
from tests.authority_kernel.scribe_preparation_metadata_test import (
    engine as _engine,
)
from tests.authority_kernel.scribe_preparation_metadata_test import (
    native as _native,
)
from tests.authority_kernel.scribe_preparation_metadata_test import (
    remote_reads as _remote_reads,
)
from tests.authority_kernel.scribe_preparation_metadata_test import (
    workflow as _workflow,
)

engine, native, remote_reads, workflow = _engine, _native, _remote_reads, _workflow


@pytest.fixture(autouse=True)
def enable_session_prep_for_synthetic_providers(monkeypatch):
    monkeypatch.setattr(
        records.configuration,
        "SESSION_PREP_PROVIDER_SETTINGS",
        records.configuration.SessionPrepProviderSettings(enabled=True),
    )


@pytest.mark.parametrize("kind", ["creator_review", "preparation_metadata"])
def test_actual_native_response_retains_receipt_locks_and_remote_calls(
    native, engine, remote_reads, monkeypatch, kind, record_property
):
    assert native.post(kind).status_code == 200
    # Fixture admission is already complete; measure only the actual request.
    monkeypatch.setattr(
        native_service, "execute_capture_operation", gateway.execute_capture_operation
    )
    locks, calls = [], remote_reads[0]
    calls.clear()

    def observe(_connection, _cursor, statement, *_args):
        shape = " ".join(statement.lower().split())
        if " for update" in shape:
            locks.append(shape)

    event.listen(engine, "before_cursor_execute", observe)
    try:
        with query_counts(engine) as counts:
            response = native.post(kind)
    finally:
        event.remove(engine, "before_cursor_execute", observe)
    assert response.status_code == 200
    assert response.json()["result"]["receipt"]
    assert locks and calls
    assert engine.pool.checkedout() == 0
    record_property("selects", counts["all_selects"])


@pytest.mark.parametrize(
    "name",
    ["normal", "pilot", "inactive", "revoked_member", "grant_revoked", "disabled"],
)
def test_provider_record_practice_uses_strict_session_prep_access(engine, name):
    args, _at = scenario(engine, name)
    with session_for(engine) as session:
        doctor = session.get(Doctor, args["doctor_id"])
        assert doctor is not None
        decision = records.resolve_session_prep_provider_access(doctor, session=session)
        if decision.allowed:
            assert decision.practice is not None
            assert records._current_practice(session, doctor).id == decision.practice.id
        else:
            with pytest.raises(records.ProviderRecordScopeDenied):
                records._current_practice(session, doctor)
    assert decision.allowed is (name == "normal")


@pytest.mark.parametrize("field", ["member", "practice"])
def test_join_refreshes_stale_orm_rows(engine, field):
    args, _at = scenario(engine, "normal")
    with session_for(engine) as session:
        doctor = session.get(Doctor, args["doctor_id"])
        assert doctor is not None
        member = session.exec(
            select(PracticeMember).where(PracticeMember.doctor_id == doctor.id)
        ).one()
        practice = session.get(Practice, args["practice_id"])
        assert practice is not None
        row, name = (member, "role") if field == "member" else (practice, "baa_version")
        original = getattr(row, name)
        set_committed_value(row, name, "stale-synthetic-value")
        with session.no_autoflush:
            decision = records.resolve_session_prep_provider_access(
                doctor, session=session
            )
        assert decision.allowed and getattr(row, name) == original


def test_missing_membership_has_no_fallback(engine):
    args, _at = scenario(engine, "normal")
    with session_for(engine) as session:
        doctor = session.get(Doctor, args["doctor_id"])
        assert doctor is not None
        from typeid import TypeID

        set_committed_value(doctor, "id", TypeID("doctor"))
        with session.no_autoflush:
            decision = records.resolve_session_prep_provider_access(
                doctor, session=session
            )
        assert not decision.allowed and decision.practice is None


def test_committed_revocation_is_observed_before_practice_is_returned(engine):
    args, _at = scenario(engine, "normal")
    with session_for(engine) as session:
        doctor = session.get(Doctor, args["doctor_id"])
        assert doctor is not None
        with Session(engine) as writer:
            assert lock_practice_seats(writer, args["practice_id"]) is not None
            member = writer.exec(
                select(PracticeMember).where(PracticeMember.doctor_id == doctor.id)
            ).one()
            member.seat_state = "inactive"
            member.access_ends_at = datetime.now(UTC) - timedelta(seconds=1)
            writer.add(member)
            writer.commit()
        with pytest.raises(records.ProviderRecordScopeDenied):
            records._current_practice(session, doctor)


def test_denied_access_never_returns_a_practice(engine, monkeypatch):
    args, _at = scenario(engine, "normal")
    with session_for(engine) as session:
        doctor = session.get(Doctor, args["doctor_id"])
        assert doctor is not None
        monkeypatch.setattr(
            records,
            "resolve_session_prep_provider_access",
            lambda *_a, **_k: SessionPrepProviderAccessDecision(
                allowed=False,
                denial_reason="practice_membership_inactive",
            ),
        )
        with pytest.raises(records.ProviderRecordScopeDenied):
            records._current_practice(session, doctor)
