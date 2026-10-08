"""Fresh joined practice reads preserve current policy and transaction boundaries."""

from datetime import UTC, datetime, timedelta

import pytest

from app.services import provider_record_authority as records
from app.services import scribe_capture_gateway as gateway
from app.services import scribe_native_capture as native_service

from app.models.doctor import Doctor
from app.models.practice import Practice, PracticeMember
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


def previous_rows(session, doctor):
    member = session.exec(
        select(PracticeMember)
        .where(PracticeMember.doctor_id == doctor.id)
        .execution_options(populate_existing=True)
    ).one_or_none()
    return (
        (member, session.get(Practice, member.practice_id, populate_existing=True))
        if member is not None
        else None
    )


@pytest.mark.parametrize("kind", ["creator_review", "preparation_metadata"])
def test_actual_native_response_calls_and_lock_order_match_previous_reads(
    native, engine, remote_reads, monkeypatch, kind, record_property
):
    assert native.post(kind).status_code == 200
    # Fixture admission is already complete; measure only the actual request.
    monkeypatch.setattr(
        native_service, "execute_capture_operation", gateway.execute_capture_operation
    )
    results = []
    for joined in (False, True):
        locks, calls = [], remote_reads[0]
        calls.clear()

        def observe(_connection, _cursor, statement, *_args):
            shape = " ".join(statement.lower().split())
            if " for update" in shape:
                locks.append(shape)

        with monkeypatch.context() as patch:
            if not joined:
                patch.setattr(records, "_current_practice_rows", previous_rows)
            event.listen(engine, "before_cursor_execute", observe)
            try:
                with query_counts(engine) as counts:
                    response = native.post(kind)
            finally:
                event.remove(engine, "before_cursor_execute", observe)
        assert response.status_code == 200
        results.append(
            (
                response.json()["result"]["receipt"],
                locks,
                calls[:],
                counts["all_selects"],
            )
        )
    assert results[0][:3] == results[1][:3]
    assert results[0][3] - results[1][3] == (16 if kind == "creator_review" else 30)
    assert engine.pool.checkedout() == 0
    record_property("previous_selects", results[0][3])
    record_property("joined_selects", results[1][3])


@pytest.mark.parametrize(
    "name",
    ["normal", "pilot", "inactive", "revoked_member", "grant_revoked", "disabled"],
)
def test_complete_practice_access_matches_previous_committed_state(
    engine, monkeypatch, name
):
    args, _at = scenario(engine, name)
    outcomes = []
    for joined in (False, True):
        with session_for(engine) as session, monkeypatch.context() as patch:
            if not joined:
                patch.setattr(records, "_current_practice_rows", previous_rows)
            doctor = session.get(Doctor, args["doctor_id"])
            assert doctor is not None
            try:
                outcomes.append(records._current_practice(session, doctor).id)
            except records.ProviderRecordScopeDenied:
                outcomes.append(None)
    assert outcomes[0] == outcomes[1]


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
            current = records._current_practice_rows(session, doctor)
        assert current is not None and getattr(row, name) == original


def test_missing_membership_has_no_fallback(engine):
    args, _at = scenario(engine, "normal")
    with session_for(engine) as session:
        doctor = session.get(Doctor, args["doctor_id"])
        assert doctor is not None
        from typeid import TypeID

        set_committed_value(doctor, "id", TypeID("doctor"))
        with session.no_autoflush:
            assert records._current_practice_rows(session, doctor) is None


def test_commit_after_access_is_observed_before_practice_is_returned(
    engine, monkeypatch
):
    args, _at = scenario(engine, "normal")
    with session_for(engine) as session:
        doctor = session.get(Doctor, args["doctor_id"])
        assert doctor is not None
        original = records.doctor_access_in_force

        def revoke_after_access(current, **kwargs):
            allowed = original(current, **kwargs)
            assert allowed
            with Session(engine) as writer:
                member = writer.exec(
                    select(PracticeMember).where(PracticeMember.doctor_id == current.id)
                ).one()
                member.seat_state = "inactive"
                member.access_ends_at = datetime.now(UTC) - timedelta(seconds=1)
                writer.add(member)
                writer.commit()
            return allowed

        monkeypatch.setattr(records, "doctor_access_in_force", revoke_after_access)
        with pytest.raises(records.ProviderRecordScopeDenied):
            records._current_practice(session, doctor)


def test_denied_access_never_dispatches_practice_read(engine, monkeypatch):
    args, _at = scenario(engine, "normal")
    with session_for(engine) as session:
        doctor = session.get(Doctor, args["doctor_id"])
        assert doctor is not None
        monkeypatch.setattr(records, "doctor_access_in_force", lambda *_a, **_k: False)

        def unexpected(*_args):
            pytest.fail("practice read dispatched after denied access")

        monkeypatch.setattr(records, "_current_practice_rows", unexpected)
        with pytest.raises(records.ProviderRecordScopeDenied):
            records._current_practice(session, doctor)
