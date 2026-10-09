"""Actual provider discovery preserves locks, refusal and fresh owner rows."""

from contextlib import contextmanager, nullcontext
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services import authority
from app.services import companion_encounters as encounters
from app.services import doctor_access_row_ownership as owned

from activemodel import session_manager
from app.models.doctor import Doctor
from app.models.practice import Practice, PracticeMember, lock_practice_seats
from sqlalchemy import event
from sqlmodel import Session, select

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
from tests.authority_kernel.scribe_provider_connection_read_test import change_row

engine, native, workflow = _engine, _native, _workflow


def previous_discovery(session, subject):
    owner = session.exec(
        select(Doctor)
        .where(Doctor.clerk_id == subject)
        .execution_options(populate_existing=True)
    ).one_or_none()
    member = (
        session.exec(
            select(PracticeMember)
            .where(PracticeMember.doctor_id == owner.id)
            .execution_options(populate_existing=True)
        ).one_or_none()
        if owner is not None
        else None
    )
    return owner, member


@contextmanager
def variant(monkeypatch, candidate):
    with monkeypatch.context() as patch:
        if not candidate:
            patch.setattr(
                encounters, "_provider_owner_discovery_rows", previous_discovery
            )
        yield


@pytest.mark.parametrize("kind", ["mount", "creator_review", "capabilities"])
def test_complete_receipt_exact_locks_and_fewer_reads(
    native, engine, monkeypatch, kind, record_property
):
    runs = []
    for candidate in (False, True):
        locks = []

        def observe(_connection, _cursor, statement, *_):
            shape = " ".join(statement.lower().split())
            if " for update" in shape:
                locks.append(shape)

        with variant(monkeypatch, candidate):
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
    assert runs[0][0] == runs[1][0] and runs[0][2] == runs[1][2]
    assert runs[0][1] - runs[1][1] == (5 if kind == "mount" else 4)
    record_property("original_selects", runs[0][1])
    record_property("candidate_selects", runs[1][1])
    record_property("complete_receipt_and_lock_parity", True)


@pytest.mark.parametrize("candidate", [False, True])
@pytest.mark.parametrize(
    "change",
    [
        "identity",
        "actor",
        "tenant",
        "authority_membership",
        "link",
        "doctor",
        "member",
        "label",
    ],
)
def test_committed_current_changes_remain_denied(
    native, engine, monkeypatch, candidate, change
):
    with variant(monkeypatch, candidate):
        assert native.post().status_code == 200
        with session_for(engine) as writer:
            change_row(writer, native, change)
            writer.commit()
        assert native.post().status_code == 403
        assert engine.pool.checkedout() == 0


@pytest.mark.parametrize("candidate", [False, True])
@pytest.mark.parametrize(
    "change", ["doctor_subject", "doctor_disabled", "practice_move", "member_deleted"]
)
def test_change_committed_after_discovery_denies_before_remote_io(
    native, engine, monkeypatch, candidate, change
):
    with variant(monkeypatch, candidate):
        original = encounters._provider_owner_discovery_rows
        changed = []

        def discovery(session, subject):
            owner, member = original(session, subject)
            if not changed and owned.lookup_uses_provider_session(session):
                with session_for(engine) as writer:
                    if change.startswith("doctor_"):
                        row = writer.get(Doctor, owner.id)
                        if change == "doctor_subject":
                            row.clerk_id = "user_changed_" + uuid4().hex
                        else:
                            row.deleted_at = datetime.now(UTC)
                        writer.add(row)
                    else:
                        row = writer.exec(
                            select(PracticeMember).where(
                                PracticeMember.doctor_id == owner.id
                            )
                        ).one()
                        if change == "member_deleted":
                            lock_practice_seats(writer, row.practice_id)
                            writer.delete(row)
                        else:
                            practice = Practice(
                                legal_name="Invented moved practice",
                                email_domain="synthetic.invalid",
                                join_code=uuid4().hex[:16],
                            )
                            writer.add(practice)
                            writer.flush()
                            for identifier in sorted(
                                [row.practice_id, practice.id], key=str
                            ):
                                lock_practice_seats(writer, identifier)
                            row.practice_id = practice.id
                            writer.add(row)
                    writer.commit()
                changed.append(True)
            return owner, member

        monkeypatch.setattr(encounters, "_provider_owner_discovery_rows", discovery)

        def forbidden(**_):
            pytest.fail("Refusal must precede remote Scribe calls")

        monkeypatch.setattr(native.remote, "capture_operation", forbidden)
        assert native.post("mount").status_code == 403
        assert changed == [True] and engine.pool.checkedout() == 0


@pytest.mark.parametrize("candidate", [False, True])
@pytest.mark.parametrize("component", ["doctor", "member"])
def test_absent_discovery_components_keep_original_refusal(
    native, engine, monkeypatch, candidate, component
):
    with variant(monkeypatch, candidate):
        with session_for(engine) as session:
            owner = session.get(Doctor, native.args["doctor_id"])
            with owned.lookup_doctor_access_row_scope(session):
                if component == "member":
                    member = session.exec(
                        select(PracticeMember).where(
                            PracticeMember.doctor_id == owner.id
                        )
                    ).one()
                    lock_practice_seats(session, member.practice_id)
                    session.delete(member)
                    session.flush()
                    assert (
                        encounters._provider_owner_discovery_rows(
                            session, owner.clerk_id
                        )[1]
                        is None
                    )
                else:
                    assert encounters._provider_owner_discovery_rows(
                        session, "user_absent_" + uuid4().hex
                    ) == (None, None)
                with pytest.raises(authority.AuthorityRequestDeniedError):
                    encounters._provider(
                        session,
                        issuer=SETTINGS.clerk_issuer,
                        subject=owner.clerk_id
                        if component == "member"
                        else "user_absent_" + uuid4().hex,
                        session_id="session_invented_scribe",
                        tenant_ref=native.args["tenant_id"],
                        settings=SETTINGS,
                    )


@pytest.mark.parametrize("component", ["doctor", "member"])
def test_join_refreshes_loaded_owner_inputs(native, engine, component):
    from sqlalchemy.orm.attributes import set_committed_value

    with session_for(engine) as session:
        owner = session.get(Doctor, native.args["doctor_id"])
        member = session.exec(
            select(PracticeMember).where(PracticeMember.doctor_id == owner.id)
        ).one()
        with owned.lookup_doctor_access_row_scope(session):
            if component == "doctor":
                set_committed_value(owner, "clerk_id", "user_stale")
            else:
                set_committed_value(member, "practice_id", None)
            current, current_member = encounters._provider_owner_discovery_rows(
                session, native.args["verified_clerk_subject"]
            )
            assert current is owner and current_member is member
            assert (
                current.clerk_id == native.args["verified_clerk_subject"]
                and current_member.practice_id == native.args["practice_id"]
            )


@pytest.mark.parametrize("lookup", [False, True])
@pytest.mark.parametrize("present", [False, True])
def test_nontransactional_adapter_preserves_separate_reads_and_absent_owner(
    lookup, present
):
    calls = []
    owner = SimpleNamespace(id="invented-doctor")

    class Adapter:
        def exec(self, statement):
            calls.append(statement)
            return SimpleNamespace(
                one_or_none=lambda: owner if len(calls) == 1 and present else None
            )

    adapter = Adapter()
    scope = owned.lookup_doctor_access_row_scope(adapter) if lookup else nullcontext()
    with scope:
        row, member = encounters._provider_owner_discovery_rows(
            adapter, "user_invented"
        )
    assert row is (owner if present else None) and member is None
    assert len(calls) == (2 if present else 1)
    assert (
        len(calls[0].column_descriptions) == 1
        and calls[0].column_descriptions[0]["entity"] is Doctor
    )
    if present:
        assert calls[1].column_descriptions[0]["entity"] is PracticeMember


@pytest.mark.parametrize("mismatch", ["scope", "ambient"])
def test_another_session_cannot_select_joined_lookup(
    native, engine, monkeypatch, mismatch
):
    from activemodel.session_manager import _session_context

    with session_for(engine) as current, Session(engine) as other:
        with owned.lookup_doctor_access_row_scope(current):
            selected = other if mismatch == "scope" else current
            token = _session_context.set(other) if mismatch == "ambient" else None
            try:
                assert not owned.lookup_uses_provider_session(selected)
                with query_counts(engine) as counts:
                    owner, member = encounters._provider_owner_discovery_rows(
                        selected, native.args["verified_clerk_subject"]
                    )
                assert (
                    owner is not None
                    and member is not None
                    and counts["all_selects"] == 2
                )
            finally:
                if token is not None:
                    _session_context.reset(token)
