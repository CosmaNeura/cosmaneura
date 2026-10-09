"""Fresh joined provider connection reads inside an owned synthetic lookup."""

from datetime import UTC, datetime, timedelta

import pytest

from app.services import authority
from app.services import companion_encounters as encounters
from app.services import doctor_access_row_ownership as owned

from app.models.authority import (
    AuthorityActor,
    AuthorityActorExternalIdentity,
    AuthorityTenant,
    AuthorityTenantMembership,
)
from app.models.doctor import Doctor
from app.models.practice import Practice, PracticeMember, lock_practice_seats
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
from tests.authority_kernel.scribe_native_status_enrichment_test import (
    query_counts,
)
from tests.authority_kernel.scribe_native_status_enrichment_test import (
    workflow as _workflow,
)

engine, native, workflow = _engine, _native, _workflow


@pytest.mark.parametrize("kind", ["mount", "creator_review", "capabilities"])
def test_current_receipts_and_individual_locks_match_separate_reads(
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
                patch.setattr(encounters, "lookup_owns_provider_rows", lambda *_: False)
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
        assert engine.pool.checkedout() == 0
    assert runs[0][0] == runs[1][0] and runs[0][2] == runs[1][2]
    assert runs[0][1] - runs[1][1] == (25 if kind == "mount" else 20)
    record_property("operation", kind)
    record_property("previous_selects", runs[0][1])
    record_property("current_selects", runs[1][1])
    record_property("exact_lock_sequence_preserved", True)


def change_row(session, native, change):
    link = session.get(ScribeCaptureProviderLink, native.provisioned.provider_link_ref)
    assert link is not None
    if change == "identity":
        row = session.get(AuthorityActorExternalIdentity, link.source_identity_id)
        row.revoked_at = datetime.now(UTC)
    elif change == "actor":
        row = session.get(AuthorityActor, link.actor_id)
        row.status = "revoked"
    elif change == "tenant":
        row = session.get(AuthorityTenant, link.tenant_id)
        row.status = "revoked"
    elif change == "authority_membership":
        row = session.exec(
            select(AuthorityTenantMembership).where(
                AuthorityTenantMembership.actor_id == link.actor_id,
                AuthorityTenantMembership.tenant_id == link.tenant_id,
            )
        ).one()
        row.status = "revoked"
    elif change == "link":
        row = link
        row.revoked_at = datetime.now(UTC)
    elif change == "doctor":
        row = session.get(Doctor, link.doctor_id)
        row.deleted_at = datetime.now(UTC)
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
    assert row is not None
    session.add(row)
    session.flush()


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
def test_next_request_denies_committed_current_connection_changes(
    native, engine, change
):
    assert native.post().status_code == 200
    with session_for(engine) as writer:
        change_row(writer, native, change)
        writer.commit()
    assert native.post().status_code == 403
    assert engine.pool.checkedout() == 0


@pytest.mark.parametrize(
    "change", ["actor", "tenant", "authority_membership", "link", "member", "label"]
)
def test_post_view_as_same_transaction_changes_match_original_denial(
    native, engine, monkeypatch, change
):
    guard = encounters.actor_bound_unended_view_as_exists
    for previous in (True, False):
        changed = []

        def mutate(session, **kwargs):
            result = guard(session, **kwargs)
            if not changed and owned._LOOKUP_ROWS.get() is not None:
                change_row(session, native, change)
                changed.append(True)
            return result

        with monkeypatch.context() as patch:
            if previous:
                patch.setattr(encounters, "lookup_owns_provider_rows", lambda *_: False)
            patch.setattr(encounters, "actor_bound_unended_view_as_exists", mutate)
            with pytest.raises(authority.AuthorityRequestDeniedError):
                native.invoke(
                    native.request("lookup", workspace_id=native.row["workspace_id"])
                )
        assert changed == [True] and engine.pool.checkedout() == 0


def test_catalog_never_uses_lookup_connection_join(native, monkeypatch):
    def forbidden(*_):
        pytest.fail("Catalog cannot use lookup-only joined reads")

    monkeypatch.setattr(encounters, "_provider_connection_rows", forbidden)
    result = native.invoke(native.request("list"))
    assert native.row in result["encounters"]


@pytest.mark.parametrize("transition", ["commit", "rollback", "savepoint"])
def test_transaction_transition_before_connection_read_uses_original_path(
    native, engine, monkeypatch, transition
):
    guard = encounters.actor_bound_unended_view_as_exists
    joined = encounters._provider_connection_rows
    changed = []
    calls = []

    def mutate(session, **kwargs):
        result = guard(session, **kwargs)
        if not changed and owned._LOOKUP_ROWS.get() is not None:
            if transition == "commit":
                session.commit()
            elif transition == "rollback":
                session.rollback()
            else:
                with session.begin_nested():
                    pass
            changed.append(True)
        return result

    def current(session, *args):
        assert owned._CAPTURE_ROWS.get() is not None
        calls.append(True)
        return joined(session, *args)

    monkeypatch.setattr(encounters, "actor_bound_unended_view_as_exists", mutate)
    monkeypatch.setattr(encounters, "_provider_connection_rows", current)
    result = native.invoke(
        native.request("lookup", workspace_id=native.row["workspace_id"])
    )
    assert result["encounter"] == native.row and changed == [True]
    # The first provider read had invalid evidence after its transaction changed.
    assert calls == [] and engine.pool.checkedout() == 0
