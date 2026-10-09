"""Previous/current fresh lookup checks on owned synthetic PostgreSQL."""

from datetime import UTC, datetime, timedelta

import pytest

from app.services import authority, scribe_encounters
from app.services.companion_appointment_setup import _current_provider

from app.models.authority import AuthorityActor, AuthorityActorExternalIdentity
from app.models.practice import Practice, PracticeMember, lock_practice_seats
from app.models.scribe_capture_binding import ScribeCaptureProviderLink
from sqlalchemy import event
from sqlmodel import select

from tests.authority_kernel.scribe_native_status_enrichment_test import (
    engine as _engine,
)
from tests.authority_kernel.scribe_native_status_enrichment_test import (
    native as _native,
)
from tests.authority_kernel.scribe_native_status_enrichment_test import (
    query_counts,
)
from tests.authority_kernel.scribe_native_status_enrichment_test import (
    workflow as _workflow,
)

engine, native, workflow = _engine, _native, _workflow


def previous_final(session, *, expected, doctor_id, args):
    connection, current_doctor, _ = _current_provider(session, args)
    return connection, current_doctor


@pytest.mark.parametrize("kind", ["mount", "creator_review", "capabilities"])
def test_real_native_preserves_receipts_with_fewer_lookup_reads(
    native, engine, monkeypatch, kind, record_property
):
    results = []
    for previous in (True, False):
        locks = []

        def observe(_connection, _cursor, statement, *_):
            shape = " ".join(statement.lower().split())
            if " for update" in shape:
                locks.append(shape)

        with monkeypatch.context() as patch:
            if previous:
                patch.setattr(scribe_encounters, "_locked_provider", previous_final)
            event.listen(engine, "before_cursor_execute", observe)
            try:
                with query_counts(engine) as counts:
                    response = native.post(kind)
            finally:
                event.remove(engine, "before_cursor_execute", observe)
        assert response.status_code == 200
        result = response.json()["result"]
        results.append(
            (
                result["encounter"] if kind == "mount" else result["receipt"],
                counts["all_selects"],
                locks,
            )
        )
    assert results[0][0] == results[1][0]
    assert results[1][1] < results[0][1]
    # Retained individual locks keep their original relative order. Only the
    # duplicate full provider routine's locks are omitted at the final lookup.
    retained = iter(results[0][2])
    assert all(any(old == current for old in retained) for current in results[1][2])
    record_property("operation", kind)
    record_property("previous_selects", results[0][1])
    record_property("current_selects", results[1][1])
    record_property("retained_lock_order", True)


@pytest.mark.parametrize("change", ["actor", "identity", "link", "member", "label"])
def test_final_boundary_rechecks_same_transaction_changes(
    native, engine, monkeypatch, change
):
    actual = scribe_encounters._locked_provider
    for previous in (True, False):

        def changed(session, **kwargs):
            link = session.get(
                ScribeCaptureProviderLink, native.provisioned.provider_link_ref
            )
            assert link is not None
            if change == "actor":
                row = session.get(AuthorityActor, link.actor_id)
                row.status = "revoked"
            elif change == "identity":
                row = session.get(
                    AuthorityActorExternalIdentity, link.source_identity_id
                )
                row.revoked_at = datetime.now(UTC)
            elif change == "link":
                row = link
                row.revoked_at = datetime.now(UTC)
            elif change == "member":
                lock_practice_seats(session, link.practice_id)
                row = session.exec(
                    select(PracticeMember).where(
                        PracticeMember.doctor_id == link.doctor_id
                    )
                ).one()
                row.seat_state = "inactive"
                row.access_ends_at = datetime.now(UTC) - timedelta(days=1)
            else:
                row = session.get(Practice, link.practice_id)
                row.legal_name = "X" * 201
            assert row is not None
            session.add(row)
            session.flush()
            return (previous_final if previous else actual)(session, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(scribe_encounters, "_locked_provider", changed)
            with pytest.raises(authority.AuthorityRequestDeniedError):
                native.invoke(
                    native.request("lookup", workspace_id=native.row["workspace_id"])
                )


@pytest.mark.parametrize("transition", ["commit", "rollback", "savepoint"])
def test_transaction_transition_requires_original_full_final_check(
    native, monkeypatch, transition
):
    original_visit = scribe_encounters._visit
    original_provider = scribe_encounters._current_provider
    calls = []

    def visit(session, *args, **kwargs):
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
        pytest.fail("The lock-local final check survived a transaction transition")

    monkeypatch.setattr(scribe_encounters, "_visit", visit)
    monkeypatch.setattr(scribe_encounters, "_current_provider", provider)
    monkeypatch.setattr(scribe_encounters, "_locked_provider", forbidden)
    result = native.invoke(
        native.request("lookup", workspace_id=native.row["workspace_id"])
    )
    assert result["encounter"] == native.row
    assert calls == ["full", "full"]


def test_catalog_retains_full_provider_checks(native, monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("Only an exact lookup can use the lock-local final check")

    monkeypatch.setattr(scribe_encounters, "_locked_provider", forbidden)
    result = native.invoke(native.request("list"))
    assert native.row in result["encounters"]


def test_lookup_refreshes_unsaved_stale_orm(native, monkeypatch):
    original = scribe_encounters._current_provider

    def stale(session, args):
        practice = session.get(Practice, native.args["practice_id"])
        assert practice is not None
        practice.legal_name = "Unsaved synthetic label"
        with session.no_autoflush:
            return original(session, args)

    monkeypatch.setattr(scribe_encounters, "_current_provider", stale)
    result = native.invoke(
        native.request("lookup", workspace_id=native.row["workspace_id"])
    )
    assert result["encounter"] == native.row
