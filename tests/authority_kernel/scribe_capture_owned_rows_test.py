"""Capture row ownership never retains permission across a transaction or I/O."""

from contextlib import contextmanager
from datetime import UTC, datetime

import pytest

from app.services import doctor_access as access
from app.services import doctor_access_row_ownership as ownership
from app.services import scribe_capture_scope as capture

from activemodel import session_manager
from app.models.authority import AuthorityActorExternalIdentity
from app.models.doctor import Doctor
from sqlalchemy import event, select
from sqlmodel import Session

from tests.authority_kernel.companion_provider_provisioning_test import session_for
from tests.authority_kernel.doctor_access_owned_rows_test import owned_case
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


@contextmanager
def original_capture_rows(session, **args):
    # Previous exact Practice -> Doctor -> membership reads, without ownership.
    with ownership.lock_doctor_access_rows(session, **args) as rows:
        yield rows


@pytest.mark.parametrize("kind", ["creator_review", "preparation_metadata"])
def test_actual_native_receipts_and_lock_order_match_with_fewer_roundtrips(
    native, engine, remote_reads, monkeypatch, kind, record_property
):
    results = []
    for enabled in (False, True):
        locks = []

        def observe(_connection, _cursor, statement, *_args):
            shape = " ".join(statement.lower().split())
            if " for update" in shape:
                locks.append(shape)

        with monkeypatch.context() as patch:
            if not enabled:
                patch.setattr(
                    capture, "capture_doctor_access_rows", original_capture_rows
                )
            event.listen(engine, "before_cursor_execute", observe)
            try:
                with query_counts(engine) as counts:
                    response = native.post(kind)
            finally:
                event.remove(engine, "before_cursor_execute", observe)
        assert response.status_code == 200
        results.append(
            (response.json()["result"]["receipt"], counts["all_selects"], locks)
        )
    assert results[0][0] == results[1][0]
    assert results[0][2] == results[1][2]
    # Metadata now performs nine authorizations: two overlapping configured
    # read checks were consolidated after receipt validation. Ownership still
    # removes nine selects per authorization, with unchanged lock order.
    assert results[0][1] - results[1][1] >= (
        81 if kind == "preparation_metadata" else 18
    )
    assert engine.pool.checkedout() == 0
    assert session_manager._session_context.get() is None
    assert ownership._CAPTURE_ROWS.get() is None
    record_property("previous_selects", results[0][1])
    record_property("owned_selects", results[1][1])


def test_only_exact_capture_scope_binds_session_and_restores_it_on_exception(engine):
    args, _at = owned_case(engine)
    assert session_manager._session_context.get() is None
    with Session(engine) as session:
        doctor = session.get(Doctor, args["doctor_id"])
        assert doctor is not None
        proof = None
        with pytest.raises(RuntimeError, match="synthetic interrupted policy"):
            with ownership.capture_doctor_access_rows(
                session,
                practice_id=args["practice_id"],
                doctor_id=args["doctor_id"],
                subject=args["verified_clerk_subject"],
            ) as rows:
                assert rows is not None
                proof = rows[3]
                with session_manager.get_session() as current:
                    assert current is session
                assert ownership.current_capture_doctor_rows(session, doctor) is proof
                raise RuntimeError("synthetic interrupted policy")
        assert session_manager._session_context.get() is None
        assert ownership.current_capture_doctor_rows(session, doctor) is None
        assert proof is not None
        assert not proof.matches(session, doctor, practice_id=args["practice_id"])
    assert engine.pool.checkedout() == 0


@pytest.mark.parametrize("change", ["commit", "rollback", "savepoint"])
def test_changed_capture_transaction_reverts_to_original_access_queries(engine, change):
    args, at = owned_case(engine)
    with session_for(engine) as session:
        assert session.get(Doctor, args["doctor_id"]) is not None
        with ownership.capture_doctor_access_rows(
            session,
            practice_id=args["practice_id"],
            doctor_id=args["doctor_id"],
            subject=args["verified_clerk_subject"],
        ) as rows:
            assert rows is not None
            if change == "commit":
                session.commit()
            elif change == "rollback":
                session.rollback()
            else:
                session.begin_nested().rollback()
            doctor = session.get(Doctor, args["doctor_id"])
            assert doctor is not None
            assert ownership.current_capture_doctor_rows(session, doctor) is None
            with query_counts(engine) as counts:
                assert access.resolve_doctor_access(doctor, at=at).allowed
            assert counts["all_selects"] == 6
        assert session_manager._session_context.get() is session


@pytest.mark.parametrize("remote_phase", range(1, 6))
def test_capture_session_and_ownership_end_before_each_remote_read(
    native, engine, remote_reads, monkeypatch, remote_phase
):
    calls, original = remote_reads

    async def revoke(*args, **kwargs):
        assert ownership._CAPTURE_ROWS.get() is None
        assert session_manager._session_context.get() is None
        assert engine.pool.checkedout() == 0
        response = await original(*args, **kwargs)
        if len(calls) == remote_phase:
            with Session(engine) as session:
                identity = session.execute(
                    select(AuthorityActorExternalIdentity)
                    .where(
                        AuthorityActorExternalIdentity.subject
                        == native.args["verified_clerk_subject"]
                    )
                    .with_for_update(nowait=True)
                ).scalar_one()
                identity.revoked_at = datetime.now(UTC)
                session.add(identity)
                session.commit()
        return response

    monkeypatch.setattr(native.remote, "capture_operation", revoke)
    assert native.post("preparation_metadata").status_code == 403
    assert engine.pool.checkedout() == 0
    assert ownership._CAPTURE_ROWS.get() is None
    assert session_manager._session_context.get() is None
