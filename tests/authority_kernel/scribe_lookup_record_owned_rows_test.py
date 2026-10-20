"""Actual lookup receipts, locks, revocation and lock-evidence scope cleanup."""

from contextlib import nullcontext
from datetime import UTC, datetime, timedelta

import pytest

from app.services import authority, provider_record_authority, scribe_encounters
from app.services import doctor_access_row_ownership as owned

from activemodel import session_manager
from app.models.authority import AuthorityActorExternalIdentity
from app.models.doctor import Doctor
from app.models.practice import PracticeMember, lock_practice_seats
from app.models.scribe_capture_binding import ScribeCaptureProviderLink
from sqlalchemy import event
from sqlmodel import select

from tests.authority_kernel.companion_provider_provisioning_test import session_for
from tests.authority_kernel.scribe_native_status_enrichment_test import (
    engine as _engine,
)
from tests.authority_kernel.scribe_native_status_enrichment_test import (
    native as _native,
)
from tests.authority_kernel.scribe_native_status_enrichment_test import query_counts
from tests.authority_kernel.scribe_native_status_enrichment_test import (
    workflow as _workflow,
)

engine, native, workflow = _engine, _native, _workflow


@pytest.mark.parametrize("kind", ["mount", "creator_review", "capabilities"])
def test_real_native_retains_receipts_and_exact_locks_with_fewer_reads(
    native, engine, monkeypatch, kind, record_property
):
    runs = []
    for previous in (True, False):
        locks = []

        def observe(_connection, _cursor, statement, *_):
            shape = " ".join(statement.lower().split())
            if " for update" in shape:
                locks.append(shape)

        with monkeypatch.context() as patch:
            if previous:
                patch.setattr(
                    scribe_encounters,
                    "lookup_doctor_access_row_scope",
                    lambda _: nullcontext(),
                )
            event.listen(engine, "before_cursor_execute", observe)
            try:
                with query_counts(engine) as counts:
                    response = native.post(kind)
            finally:
                event.remove(engine, "before_cursor_execute", observe)
        assert response.status_code == 200
        result = response.json()["result"]
        runs.append(
            (
                result["encounter"] if kind == "mount" else result["receipt"],
                counts["all_selects"],
                locks,
            )
        )
        assert owned._LOOKUP_ROWS.get() is None and owned._CAPTURE_ROWS.get() is None
        assert (
            engine.pool.checkedout() == 0
            and session_manager._session_context.get() is None
        )
    assert runs[0][0] == runs[1][0]
    assert runs[0][2] == runs[1][2]
    assert runs[1][1] < runs[0][1]
    record_property("operation", kind)
    record_property("previous_selects", runs[0][1])
    record_property("current_selects", runs[1][1])
    record_property("exact_lock_sequence_preserved", True)


def test_record_adapter_discovers_current_locks_only_inside_lookup(
    native, engine, monkeypatch
):
    original = provider_record_authority.doctor_access_in_force
    checks = []

    def access(doctor, **args):
        session = session_manager._session_context.get()
        proof = (
            owned.current_capture_doctor_rows(session, doctor)
            if session is not None
            else None
        )
        scope = owned._LOOKUP_ROWS.get()
        checks.append((scope is not None, proof is not None))
        if scope is not None:
            assert scope[0] is session
        return original(doctor, **args)

    monkeypatch.setattr(provider_record_authority, "doctor_access_in_force", access)
    assert native.post("mount").status_code == 200
    assert any(scope and proof for scope, proof in checks)
    assert owned._LOOKUP_ROWS.get() is None and owned._CAPTURE_ROWS.get() is None
    assert engine.pool.checkedout() == 0


@pytest.mark.parametrize(
    "transition", ["commit", "rollback", "savepoint", "expiration"]
)
def test_retained_proof_expires_before_another_access_decision(
    native, engine, transition
):
    with session_for(engine) as session:
        # Start the exact transaction before acquiring the original provider locks.
        doctor = session.get(Doctor, native.args["doctor_id"])
        with owned.lookup_doctor_access_row_scope(session):
            with owned.provider_doctor_access_rows(
                session,
                practice_id=native.args["practice_id"],
                doctor_id=native.args["doctor_id"],
                subject=native.args["verified_clerk_subject"],
            ) as locked:
                assert locked is not None
                doctor, proof = locked[1], locked[3]
            assert owned.current_capture_doctor_rows(session, doctor) is proof
            if transition == "commit":
                session.commit()
            elif transition == "rollback":
                session.rollback()
            elif transition == "savepoint":
                with session.begin_nested():
                    pass
            else:
                session.expire_all()
            assert owned.current_capture_doctor_rows(session, doctor) is None
        assert owned._CAPTURE_ROWS.get() is None and owned._LOOKUP_ROWS.get() is None


def test_mismatched_session_cannot_publish_lock_evidence(native, engine):
    with session_for(engine) as current:
        with owned.lookup_doctor_access_row_scope(current):
            with session_for(engine) as other:
                other.get(Doctor, native.args["doctor_id"])
                with owned.provider_doctor_access_rows(
                    other,
                    practice_id=native.args["practice_id"],
                    doctor_id=native.args["doctor_id"],
                    subject=native.args["verified_clerk_subject"],
                ) as locked:
                    assert locked is not None
                    assert owned.current_capture_doctor_rows(other, locked[1]) is None
                assert owned._CAPTURE_ROWS.get() is None
            owned_rows = owned._LOOKUP_ROWS.get()
            assert owned_rows is not None
            assert owned_rows[0] is current
        assert owned._LOOKUP_ROWS.get() is None


def test_nested_scope_restores_outer_evidence_and_cleans_up(native, engine):
    with session_for(engine) as session:
        session.get(Doctor, native.args["doctor_id"])
        arguments = dict(
            practice_id=native.args["practice_id"],
            doctor_id=native.args["doctor_id"],
            subject=native.args["verified_clerk_subject"],
        )
        with owned.lookup_doctor_access_row_scope(session):
            with owned.provider_doctor_access_rows(session, **arguments) as outer:
                assert outer is not None
            doctor, proof = outer[1], outer[3]
            inner = None
            with pytest.raises(RuntimeError, match="invented nested failure"):
                with owned.lookup_doctor_access_row_scope(session):
                    with owned.provider_doctor_access_rows(
                        session, **arguments
                    ) as inner:
                        assert inner is not None
                    assert (
                        owned.current_capture_doctor_rows(session, doctor) is inner[3]
                    )
                    raise RuntimeError("invented nested failure")
            assert owned.current_capture_doctor_rows(session, doctor) is proof
            assert inner is not None
            assert not inner[3].matches_subject(session, arguments["subject"])
        assert owned.current_capture_doctor_rows(session, doctor) is None
        assert owned._CAPTURE_ROWS.get() is None and owned._LOOKUP_ROWS.get() is None


def test_exception_does_not_leave_closed_session_or_permission_evidence(
    native, engine, monkeypatch
):
    original = scribe_encounters._visit

    def failed(session, *args, **kwargs):
        doctor = session.get(Doctor, native.args["doctor_id"])
        assert owned.current_capture_doctor_rows(session, doctor) is not None
        raise RuntimeError("invented record read failure")

    monkeypatch.setattr(scribe_encounters, "_visit", failed)
    with pytest.raises(RuntimeError, match="invented record read failure"):
        native.invoke(native.request("lookup", workspace_id=native.row["workspace_id"]))
    assert owned._CAPTURE_ROWS.get() is None and owned._LOOKUP_ROWS.get() is None
    assert (
        session_manager._session_context.get() is None and engine.pool.checkedout() == 0
    )
    monkeypatch.setattr(scribe_encounters, "_visit", original)
    assert (
        native.invoke(
            native.request("lookup", workspace_id=native.row["workspace_id"])
        )["encounter"]
        == native.row
    )


@pytest.mark.parametrize("change", ["identity", "link", "member", "doctor"])
def test_next_lookup_denies_committed_revocation(native, engine, change):
    assert (
        native.invoke(
            native.request("lookup", workspace_id=native.row["workspace_id"])
        )["encounter"]
        == native.row
    )
    with session_for(engine) as writer:
        link = writer.get(
            ScribeCaptureProviderLink, native.provisioned.provider_link_ref
        )
        assert link is not None
        if change == "identity":
            row = writer.get(AuthorityActorExternalIdentity, link.source_identity_id)
            assert row is not None
            row.revoked_at = datetime.now(UTC)
        elif change == "link":
            row = link
            row.revoked_at = datetime.now(UTC)
        elif change == "member":
            lock_practice_seats(writer, link.practice_id)
            row = writer.exec(
                select(PracticeMember).where(PracticeMember.doctor_id == link.doctor_id)
            ).one()
            row.seat_state = "inactive"
            row.access_ends_at = datetime.now(UTC) - timedelta(days=1)
        else:
            row = writer.get(Doctor, link.doctor_id)
            assert row is not None
            row.deleted_at = datetime.now(UTC)
        writer.add(row)
        writer.commit()
    with pytest.raises(authority.AuthorityRequestDeniedError):
        native.invoke(native.request("lookup", workspace_id=native.row["workspace_id"]))
    assert owned._CAPTURE_ROWS.get() is None and owned._LOOKUP_ROWS.get() is None
    assert (
        session_manager._session_context.get() is None and engine.pool.checkedout() == 0
    )
