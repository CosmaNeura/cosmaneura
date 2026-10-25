"""Catalog critical-section reduction keeps actual PostgreSQL authority locks."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from threading import Event

import pytest

from app.services import authority, scribe_encounters
from app.services import doctor_access_row_ownership as owned
from app.services.companion_appointment_setup import _current_provider

from activemodel import session_manager
from app.models.authority import AuthorityActor, AuthorityActorExternalIdentity
from app.models.doctor import Doctor
from app.models.practice import Practice, PracticeMember, lock_practice_seats
from app.models.scribe_capture_binding import ScribeCaptureProviderLink
from sqlalchemy import event, text
from sqlalchemy.exc import OperationalError
from sqlmodel import select

from tests.authority_kernel.companion_provider_provisioning_test import session_for
from tests.authority_kernel.scribe_encounters_test import engine as _engine
from tests.authority_kernel.scribe_encounters_test import workflow as _workflow

engine, workflow = _engine, _workflow


def previous_final(session, *, expected, doctor_id, args):
    connection, current_doctor, _ = _current_provider(session, args)
    return connection, current_doctor


def test_catalog_preserves_full_visits_and_lock_order_with_fewer_reads(
    engine, workflow, monkeypatch, record_property
):
    _, _, request, invoke = workflow
    first = invoke(request())["encounter"]
    second = invoke(
        request(patient={"kind": "existing", "patient_ref": first["patient_ref"]})
    )["encounter"]
    runs = []
    for previous in (True, False):
        statements = []

        def observe(_connection, _cursor, statement, *_):
            statements.append(" ".join(statement.lower().split()))

        with monkeypatch.context() as patch:
            if previous:
                patch.setattr(
                    scribe_encounters,
                    "lookup_doctor_access_row_scope",
                    lambda _: nullcontext(),
                )
                patch.setattr(scribe_encounters, "_locked_provider", previous_final)
            event.listen(engine, "before_cursor_execute", observe)
            try:
                result = invoke(request("list"))
            finally:
                event.remove(engine, "before_cursor_execute", observe)
        assert sorted(result["encounters"], key=lambda r: r["workspace_id"]) == sorted(
            [first, second], key=lambda r: r["workspace_id"]
        )
        assert not any(s.startswith(("insert", "update", "delete")) for s in statements)
        assert owned._LOOKUP_ROWS.get() is None and owned._CAPTURE_ROWS.get() is None
        assert engine.pool.checkedout() == 0
        runs.append(statements)
    old_locks = [s for s in runs[0] if " for update" in s]
    new_locks = [s for s in runs[1] if " for update" in s]
    remaining = iter(old_locks)
    assert all(any(old == new for old in remaining) for new in new_locks)
    assert set(new_locks) == set(old_locks)
    assert len(runs[1]) < len(runs[0]) * 0.7
    record_property("previous_statements", len(runs[0]))
    record_property("current_statements", len(runs[1]))
    record_property("all_distinct_lock_statements_retained", True)


@pytest.mark.parametrize("change", ["actor", "identity", "link", "member", "label"])
def test_catalog_final_boundary_rechecks_current_rows(
    engine, workflow, monkeypatch, change
):
    _, provisioned, request, invoke = workflow
    invoke(request())
    actual = scribe_encounters._locked_provider

    def changed(session, **kwargs):
        link = session.get(ScribeCaptureProviderLink, provisioned.provider_link_ref)
        assert link is not None
        if change == "actor":
            row = session.get(AuthorityActor, link.actor_id)
            row.status = "revoked"
        elif change == "identity":
            row = session.get(AuthorityActorExternalIdentity, link.source_identity_id)
            row.revoked_at = datetime.now(UTC)
        elif change == "link":
            row = link
            row.revoked_at = datetime.now(UTC)
        elif change == "member":
            lock_practice_seats(session, link.practice_id)
            row = session.exec(
                select(PracticeMember).where(PracticeMember.doctor_id == link.doctor_id)
            ).one()
            row.seat_state = "inactive"
            row.access_ends_at = datetime.now(UTC) - timedelta(days=1)
        else:
            row = session.get(Practice, link.practice_id)
            row.legal_name = "X" * 201
        session.add(row)
        session.flush()
        return actual(session, **kwargs)

    monkeypatch.setattr(scribe_encounters, "_locked_provider", changed)
    with pytest.raises(authority.AuthorityRequestDeniedError):
        invoke(request("list"))
    assert owned._LOOKUP_ROWS.get() is None and owned._CAPTURE_ROWS.get() is None


@pytest.mark.parametrize("transition", ["commit", "rollback", "savepoint"])
def test_catalog_transaction_transition_requires_full_provider_check(
    workflow, monkeypatch, transition
):
    _, _, request, invoke = workflow
    visit = invoke(request())["encounter"]
    original_visit = scribe_encounters._visit
    original_provider = scribe_encounters._current_provider
    calls = []

    def read_visit(session, *args, **kwargs):
        result = original_visit(session, *args, **kwargs)
        if transition == "commit":
            session.commit()
        elif transition == "rollback":
            session.rollback()
        else:
            with session.begin_nested():
                pass
        return result

    def provider(*args, **kwargs):
        calls.append("full")
        return original_provider(*args, **kwargs)

    def forbidden(*_args, **_kwargs):
        pytest.fail("Final read scope survived a transaction transition")

    monkeypatch.setattr(scribe_encounters, "_visit", read_visit)
    monkeypatch.setattr(scribe_encounters, "_current_provider", provider)
    monkeypatch.setattr(scribe_encounters, "_locked_provider", forbidden)
    assert invoke(request("list"))["encounters"] == [visit]
    assert calls == ["full", "full"]


@pytest.mark.parametrize("target", ["doctor", "identity", "link"])
def test_catalog_blocks_concurrent_revocation_and_next_read_denies(
    engine, workflow, monkeypatch, target
):
    args, provisioned, request, invoke = workflow
    visit = invoke(request())["encounter"]
    reading, release = Event(), Event()
    original = scribe_encounters._visit

    def held(session, *values, **kwargs):
        value = original(session, *values, **kwargs)
        reading.set()
        assert release.wait(5)
        return value

    def revoke(session):
        link = session.get(ScribeCaptureProviderLink, provisioned.provider_link_ref)
        assert link is not None
        if target == "doctor":
            row = session.get(Doctor, args["doctor_id"])
            row.deleted_at = datetime.now(UTC)
        elif target == "identity":
            row = session.get(AuthorityActorExternalIdentity, link.source_identity_id)
            row.revoked_at = datetime.now(UTC)
        else:
            row = link
            row.revoked_at = datetime.now(UTC)
        session.add(row)
        session.flush()

    monkeypatch.setattr(scribe_encounters, "_visit", held)
    with ThreadPoolExecutor(max_workers=1) as pool:
        read = pool.submit(invoke, request("list"))
        try:
            assert reading.wait(5)
            with session_for(engine) as writer:
                writer.exec(text("SET LOCAL lock_timeout = '150ms'"))
                with pytest.raises(OperationalError) as blocked:
                    revoke(writer)
                assert blocked.value.orig.sqlstate == "55P03"
        finally:
            release.set()
        assert read.result(timeout=5)["encounters"] == [visit]
    monkeypatch.setattr(scribe_encounters, "_visit", original)
    with session_for(engine) as writer:
        revoke(writer)
        writer.commit()
    with pytest.raises(authority.AuthorityRequestDeniedError):
        invoke(request("list"))
    assert owned._LOOKUP_ROWS.get() is None and owned._CAPTURE_ROWS.get() is None
    assert session_manager._session_context.get() is None
    assert engine.pool.checkedout() == 0
