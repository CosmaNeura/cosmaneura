"""Current provider exchange lock evidence on owned synthetic PostgreSQL."""

import asyncio
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.configuration.scribe_capture import ScribeCaptureSettings
from app.configuration.scribe_encounters import ScribeEncounterSettings
from app.services import authority
from app.services import doctor_access_row_ownership as owned
from app.services import provider_record_authority as records
from app.services import scribe_native_capture as service
from app.services.scribe_capture_deadline import CaptureBudget, capture_budget

from activemodel import session_manager
from app.models.authority import AuthorityActorExternalIdentity
from app.models.doctor import Doctor
from app.models.practice import PracticeMember, lock_practice_seats
from app.models.scribe_capture_binding import ScribeCaptureProviderLink
from sqlalchemy import event
from sqlmodel import select

from tests.authority_kernel.companion_provider_provisioning_test import (
    SETTINGS,
    session_for,
)
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
def test_real_native_exchange_preserves_receipt_and_exact_locks(
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
                    service, "provider_record_access_row_scope", lambda _: nullcontext()
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
        assert owned._RECORD_ROWS.get() is None and owned._CAPTURE_ROWS.get() is None
        assert (
            engine.pool.checkedout() == 0
            and session_manager._session_context.get() is None
        )
    assert runs[0][0] == runs[1][0] and runs[0][2] == runs[1][2]
    assert runs[1][1] < runs[0][1]
    record_property("operation", kind)
    record_property("previous_selects", runs[0][1])
    record_property("current_selects", runs[1][1])
    record_property("exact_lock_sequence_preserved", True)


@pytest.mark.parametrize(
    "transition", ["commit", "rollback", "savepoint", "expiration"]
)
def test_exchange_lock_evidence_expires_on_transition(native, engine, transition):
    with session_for(engine) as session:
        doctor = session.get(Doctor, native.args["doctor_id"])
        with owned.provider_record_access_row_scope(session):
            with owned.provider_record_owner_rows(
                session,
                practice_id=native.args["practice_id"],
                subject=native.args["verified_clerk_subject"],
            ) as locked:
                assert locked is doctor
                proof = owned.current_capture_doctor_rows(session, doctor)
                assert proof is not None
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
        assert owned._RECORD_ROWS.get() is None and owned._CAPTURE_ROWS.get() is None


def test_mismatched_exchange_session_cannot_publish(native, engine):
    with session_for(engine) as current:
        with owned.provider_record_access_row_scope(current):
            with session_for(engine) as other:
                with owned.provider_record_owner_rows(
                    other,
                    practice_id=native.args["practice_id"],
                    subject=native.args["verified_clerk_subject"],
                ) as doctor:
                    assert doctor is not None
                    assert owned.current_capture_doctor_rows(other, doctor) is None
                assert owned._CAPTURE_ROWS.get() is None
            assert owned._RECORD_ROWS.get()[0] is current
        assert owned._RECORD_ROWS.get() is None


def test_nested_exchange_failure_restores_outer_evidence(native, engine):
    with session_for(engine) as session:
        args = dict(
            practice_id=native.args["practice_id"],
            subject=native.args["verified_clerk_subject"],
        )
        with owned.provider_record_access_row_scope(session):
            with owned.provider_record_owner_rows(session, **args) as doctor:
                outer = owned.current_capture_doctor_rows(session, doctor)
                assert outer is not None
            with pytest.raises(RuntimeError, match="invented nested exchange"):
                with owned.provider_record_access_row_scope(session):
                    with owned.provider_record_owner_rows(session, **args) as inner:
                        assert (
                            owned.current_capture_doctor_rows(session, inner)
                            is not outer
                        )
                    raise RuntimeError("invented nested exchange")
            assert owned.current_capture_doctor_rows(session, doctor) is outer
        assert owned._RECORD_ROWS.get() is None and owned._CAPTURE_ROWS.get() is None
        assert not outer.matches_subject(session, args["subject"])


def test_original_unscoped_owner_does_not_publish_evidence(native, engine):
    with session_for(engine) as session:
        with query_counts(engine) as counts:
            doctor, practice = records.lock_provider_record_owner(
                session=session, clerk_subject=native.args["verified_clerk_subject"]
            )
        assert (
            doctor.id == native.args["doctor_id"]
            and practice.id == native.args["practice_id"]
        )
        assert counts["all_selects"] > 0
        assert owned._CAPTURE_ROWS.get() is None and owned._RECORD_ROWS.get() is None


@pytest.mark.parametrize("change", ["identity", "link", "member", "doctor"])
def test_next_native_exchange_denies_committed_revocation(native, engine, change):
    assert native.post("mount").status_code == 200
    with session_for(engine) as writer:
        link = writer.get(
            ScribeCaptureProviderLink, native.provisioned.provider_link_ref
        )
        assert link is not None
        if change == "identity":
            row = writer.get(AuthorityActorExternalIdentity, link.source_identity_id)
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
            row.deleted_at = datetime.now(UTC)
        writer.add(row)
        writer.commit()
    assert native.post("mount").status_code in (403, 409)
    assert owned._RECORD_ROWS.get() is None and owned._CAPTURE_ROWS.get() is None
    assert (
        engine.pool.checkedout() == 0 and session_manager._session_context.get() is None
    )


def invoke_mount(native):
    with session_manager.global_session() as session:
        link = session.get(
            ScribeCaptureProviderLink, native.provisioned.provider_link_ref
        )
        actor_ref, tenant_ref = str(link.actor_id), str(link.tenant_id)

    async def call():
        with capture_budget(CaptureBudget(30)):
            return await service.execute_native_capture(
                issuer=SETTINGS.clerk_issuer,
                subject=native.args["verified_clerk_subject"],
                clerk_session_id="session_invented_scribe",
                workload_client_id="11111111-1111-4111-8111-111111111111",
                data=service.NativeCaptureRequest(
                    action="mount",
                    workspace_id=native.row["workspace_id"],
                    request_nonce=uuid4(),
                    expected_actor_ref=actor_ref,
                    expected_tenant_ref=tenant_ref,
                ),
                key="invented-exchange-mount",
                authority_settings=SETTINGS,
                capture_settings=ScribeCaptureSettings(enabled=True),
                encounter_settings=ScribeEncounterSettings(enabled=True),
                client=native.remote,
            )

    return asyncio.run(call())


def test_changed_membership_after_lock_is_rechecked(native, engine, monkeypatch):
    original = records._current_practice
    changed = []

    def current(session, doctor):
        proof = owned.current_capture_doctor_rows(session, doctor)
        if proof is not None and owned._RECORD_ROWS.get() is not None and not changed:
            lock_practice_seats(session, native.args["practice_id"])
            member = session.exec(
                select(PracticeMember).where(PracticeMember.doctor_id == doctor.id)
            ).one()
            member.seat_state = "inactive"
            member.access_ends_at = datetime.now(UTC) - timedelta(days=1)
            session.add(member)
            session.flush()
            changed.append(True)
        return original(session, doctor)

    monkeypatch.setattr(records, "_current_practice", current)
    with pytest.raises(records.ProviderRecordScopeDenied):
        invoke_mount(native)
    assert changed == [True]
    assert owned._RECORD_ROWS.get() is None and owned._CAPTURE_ROWS.get() is None
    assert (
        engine.pool.checkedout() == 0 and session_manager._session_context.get() is None
    )


def test_exchange_exception_cleans_scope_before_next_request(
    native, engine, monkeypatch
):
    original = authority.exchange_context_token

    def failed(**_):
        assert (
            owned._RECORD_ROWS.get() is not None
            and owned._CAPTURE_ROWS.get() is not None
        )
        raise RuntimeError("invented exchange failure")

    monkeypatch.setattr(authority, "exchange_context_token", failed)
    with pytest.raises(RuntimeError, match="invented exchange failure"):
        invoke_mount(native)
    assert owned._RECORD_ROWS.get() is None and owned._CAPTURE_ROWS.get() is None
    assert (
        engine.pool.checkedout() == 0 and session_manager._session_context.get() is None
    )
    monkeypatch.setattr(authority, "exchange_context_token", original)
    assert invoke_mount(native)["action"] == "mount"


@pytest.mark.parametrize("writer_kind", ["billing", "membership", "pilot_revoke"])
def test_real_writer_remains_blocked_by_exchange_locks_then_revokes(
    native, engine, monkeypatch, writer_kind, record_property
):
    # Reuse the real mutation/rollback/commit comparison, but trigger its writer
    # only while this new exchange scope owns the original provider row locks.
    from app.services import companion_encounters

    from tests.authority_kernel.doctor_access_owned_rows_native_test import (
        test_actual_writer_waits_then_commits_and_next_native_open_denies,
    )

    original_lookup_access = companion_encounters.doctor_access_in_force
    original_record_access = records.doctor_access_in_force

    def lookup_access(doctor, **kwargs):
        if owned._RECORD_ROWS.get() is None:
            kwargs = kwargs | {"_ownership": None}
        return original_lookup_access(doctor, **kwargs)

    def record_access(doctor, **kwargs):
        proof = owned._CAPTURE_ROWS.get()
        if proof is not None and owned._RECORD_ROWS.get() is not None:
            kwargs = kwargs | {"_ownership": proof, "_practice_id": proof._practice_id}
        return original_record_access(doctor, **kwargs)

    monkeypatch.setattr(companion_encounters, "doctor_access_in_force", lookup_access)
    monkeypatch.setattr(records, "doctor_access_in_force", record_access)
    test_actual_writer_waits_then_commits_and_next_native_open_denies(
        native,
        engine,
        monkeypatch,
        writer_kind,
        record_property,
        _expected_access_queries=2,
    )
    assert owned._RECORD_ROWS.get() is None and owned._CAPTURE_ROWS.get() is None
