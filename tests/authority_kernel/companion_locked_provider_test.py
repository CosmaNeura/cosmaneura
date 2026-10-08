"""Original-versus-joined final checks on owned synthetic PostgreSQL only."""

from datetime import UTC, datetime, timedelta

import pytest

from app.services import authority
from app.services import companion_appointment_setup as setup
from app.services.companion_encounters import _provider

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
from tests.authority_kernel.scribe_native_status_enrichment_test import (
    query_counts,
)
from tests.authority_kernel.scribe_native_status_enrichment_test import (
    workflow as _workflow,
)

engine, native, workflow = _engine, _native, _workflow


def original_recheck(session, *, expected, doctor_id, args):
    # Exact final call replaced in companion_appointment_setup at 0d285a98.
    return _provider(session, **args)


def arguments(native):
    return dict(
        issuer=SETTINGS.clerk_issuer,
        subject=native.args["verified_clerk_subject"],
        session_id="session_invented_scribe",
        tenant_ref=native.args["tenant_id"],
        settings=SETTINGS,
    )


@pytest.mark.parametrize("kind", ["mount", "creator_review", "capabilities"])
def test_native_keeps_identical_receipts_and_lock_order_with_fewer_reads(
    native, engine, monkeypatch, kind, record_property
):
    results = []
    for joined in (False, True):
        locks = []

        def observed(_connection, _cursor, statement, *_args):
            shape = " ".join(statement.lower().split())
            if " for update" in shape:
                # Only fixed SQL shape; never parameters or returned rows.
                locks.append(shape)

        with monkeypatch.context() as patch:
            if not joined:
                patch.setattr(setup, "_locked_provider", original_recheck)
            event.listen(engine, "before_cursor_execute", observed)
            try:
                with query_counts(engine) as counts:
                    response = native.post(kind)
            finally:
                event.remove(engine, "before_cursor_execute", observed)
        assert response.status_code == 200
        result = response.json()["result"]
        published = result["encounter"] if kind == "mount" else result["receipt"]
        results.append((published, counts["all_selects"], locks))
    assert results[0][0] == results[1][0]
    assert results[0][2] == results[1][2]
    # Each final recheck replaces eight fresh nonlocking reads with one.
    assert results[0][1] - results[1][1] == (70 if kind == "mount" else 56)
    record_property("operation", kind)
    record_property("original_selects", results[0][1])
    record_property("joined_selects", results[1][1])


@pytest.mark.parametrize(
    "change",
    [
        "actor_status",
        "actor_kind",
        "identity",
        "identity_subject",
        "tenant",
        "provider_membership",
        "provider_membership_kind",
        "link",
        "doctor",
        "member",
        "label",
    ],
)
def test_committed_changed_rows_produce_same_denial(
    native, engine, monkeypatch, change
):
    with session_for(engine) as session:
        practice = lock_practice_seats(session, native.args["practice_id"])
        link = session.get(
            ScribeCaptureProviderLink, native.provisioned.provider_link_ref
        )
        assert practice is not None and link is not None
        if change.startswith("actor"):
            row = session.get(AuthorityActor, link.actor_id)
            assert row is not None
            if change == "actor_kind":
                row.actor_kind = "workload"
            else:
                row.status = "revoked"
        elif change.startswith("identity"):
            row = session.get(AuthorityActorExternalIdentity, link.source_identity_id)
            assert row is not None
            if change == "identity_subject":
                row.subject = native.args["verified_clerk_subject"] + "_rebound"
            else:
                row.revoked_at = datetime.now(UTC)
        elif change == "tenant":
            row = session.get(AuthorityTenant, link.tenant_id)
            assert row is not None
            row.status = "revoked"
        elif change.startswith("provider_membership"):
            row = session.exec(
                select(AuthorityTenantMembership).where(
                    AuthorityTenantMembership.actor_id == link.actor_id,
                    AuthorityTenantMembership.tenant_id == link.tenant_id,
                )
            ).one()
            if change.endswith("kind"):
                row.membership_kind = "staff"
            else:
                row.status = "revoked"
        elif change == "link":
            row = link
            row.revoked_at = datetime.now(UTC)
        elif change == "doctor":
            row = session.get(Doctor, link.doctor_id)
            assert row is not None
            row.deleted_at = datetime.now(UTC)
        elif change == "member":
            row = session.exec(
                select(PracticeMember).where(PracticeMember.doctor_id == link.doctor_id)
            ).one()
            row.seat_state = "inactive"
            row.access_ends_at = datetime.now(UTC) - timedelta(days=1)
        else:
            row = practice
            row.legal_name = "A" * 201
        session.add(row)
        session.commit()
    for joined in (False, True):
        with monkeypatch.context() as patch:
            if not joined:
                patch.setattr(setup, "_locked_provider", original_recheck)
            response = native.post("creator_review")
        assert response.status_code == 403
        assert "receipt" not in response.json() and "encounter" not in response.json()


@pytest.mark.parametrize("joined", [False, True])
def test_final_recheck_refreshes_stale_orm_and_current_label(
    native, engine, monkeypatch, joined
):
    with session_for(engine) as session:
        practice = session.get(Practice, native.args["practice_id"])
        assert practice is not None
        original = practice.legal_name
        practice.legal_name = "Synthetic unsaved label"
        with session.no_autoflush, monkeypatch.context() as patch:
            if not joined:
                patch.setattr(setup, "_locked_provider", original_recheck)
            connection, _, _ = setup._current_provider(session, arguments(native))
        assert connection.practice_label == original


@pytest.mark.parametrize("change", ["actor", "identity", "tenant", "member", "label"])
def test_same_transaction_change_at_final_boundary_is_rechecked(
    native, engine, monkeypatch, change
):
    actual_recheck = setup._locked_provider
    for joined in (False, True):
        with session_for(engine) as session, monkeypatch.context() as patch:

            def changed_recheck(current_session, **kwargs):
                assert current_session is session
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
                elif change == "tenant":
                    row = session.get(AuthorityTenant, link.tenant_id)
                    row.status = "revoked"
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
                    row.legal_name = "A" * 201
                session.add(row)
                session.flush()
                function = actual_recheck if joined else original_recheck
                return function(session, **kwargs)

            patch.setattr(setup, "_locked_provider", changed_recheck)
            with pytest.raises(authority.AuthorityRequestDeniedError):
                setup._current_provider(session, arguments(native))
