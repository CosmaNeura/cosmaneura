"""Current provider exchange lock evidence on owned synthetic PostgreSQL."""

import asyncio
import hashlib
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from typeid import TypeID

from app.configuration.scribe_capture import ScribeCaptureSettings
from app.configuration.scribe_encounters import ScribeEncounterSettings
from app.routes import embed_context as embed_route
from app.routes.embed_context import VerifiedClerkIdentity
from app.services import authority, doctor_access
from app.services import doctor_access_row_ownership as owned
from app.services import provider_record_authority as records
from app.services import scribe_native_capture as service
from app.services.scribe_capture_deadline import CaptureBudget, capture_budget

from activemodel import session_manager
from app.models.appointment import Appointment
from app.models.authority import AuthorityActorExternalIdentity
from app.models.doctor import Doctor
from app.models.pilot_access_grandfather_grant import PilotAccessGrandfatherGrant
from app.models.practice import Practice, PracticeMember, lock_practice_seats
from app.models.provider_record_authority import ProviderRecordAuthority
from app.models.scribe_capture_binding import ScribeCaptureProviderLink
from app.models.session_prep_runtime import (
    SessionPrepArtifact,
    SessionPrepBuild,
    SessionPrepReadReceipt,
)
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

engine, native, workflow = _engine, _native, _workflow


def _replace_ordinary_entitlement_with_pilot(engine, native, *, mapped):
    now = datetime.now(UTC)
    with Session(engine) as session:
        doctor = session.get(Doctor, native.args["doctor_id"])
        practice = session.get(Practice, native.args["practice_id"])
        assert doctor is not None and doctor.email is not None
        assert practice is not None
        practice.billing_exempt = False
        practice.billing_exempt_until = None
        practice.subscription_status = None
        practice.trial_ends_at = None
        email = doctor.email.lower()
        session.add_all(
            [
                practice,
                PilotAccessGrandfatherGrant(
                    clerk_id=doctor.clerk_id,
                    doctor_id_snapshot=doctor.id,
                    practice_id_snapshot=practice.id,
                    email_snapshot=email,
                    email_sha256=hashlib.sha256(email.encode()).hexdigest(),
                    source_ref="synthetic:capture-continuity",
                    source_sha256="a" * 64,
                    grant_plan_sha256="b" * 64,
                    granted_by_ref="synthetic-capture-continuity-review",
                    grant_reason="Preserve the existing invented capture policy",
                    granted_at=now - timedelta(seconds=1),
                ),
            ]
        )
        record = session.exec(
            select(ProviderRecordAuthority).where(
                ProviderRecordAuthority.appointment_id
                == TypeID.from_string(native.row["appointment_ref"])
            )
        ).one()
        if not mapped:
            for model in (
                SessionPrepReadReceipt,
                SessionPrepArtifact,
                SessionPrepBuild,
            ):
                for row in session.exec(
                    select(model).where(model.record_scope_id == record.id)
                ).all():
                    session.delete(row)
            session.flush()
            session.delete(record)
        session.commit()


def _browser_exchange(native):
    app = FastAPI()
    app.include_router(embed_route.embed_context_api_app)
    app.dependency_overrides[embed_route.authenticate_embed_platform_session] = (
        lambda: VerifiedClerkIdentity(
            SETTINGS.clerk_issuer,
            native.args["verified_clerk_subject"],
            "session_invented_scribe",
        )
    )
    app.dependency_overrides[embed_route.get_authority_kernel_settings] = (
        lambda: SETTINGS
    )
    with TestClient(app) as client:
        return client.post(
            "/embed/v1/context-tokens/exchange",
            headers={"X-Cosmaneura-Embed-Version": "1.0"},
            json={
                "requested_surface": "provider_assistant",
                "record_ref": native.row["appointment_ref"],
            },
        )


@pytest.mark.parametrize("mapped", [False, True], ids=["legacy", "mapped"])
def test_pilot_capture_continues_without_session_prep_authority(native, engine, mapped):
    _replace_ordinary_entitlement_with_pilot(engine, native, mapped=mapped)

    exchanged = _browser_exchange(native)
    assert exchanged.status_code == 201, exchanged.text
    with session_manager.global_session() as session:
        resolved = authority.resolve_mount_context(
            workload_client_id="11111111-1111-4111-8111-111111111111",
            mount_context_token=exchanged.json()["context_token"],
            client_turn_id=uuid4(),
            session=session,
            settings=SETTINGS,
        )
        session.commit()
    assert resolved.context.surface == "provider_assistant"
    assert resolved.context.session_prep_context_ref is None
    assert "session-prep:read" not in resolved.context.capabilities
    if mapped:
        from app.services import session_prep_runtime
        from app.services.session_prep import SessionPrepScopeDenied

        with Session(engine) as session:
            scope = session.exec(
                select(ProviderRecordAuthority).where(
                    ProviderRecordAuthority.appointment_id
                    == TypeID.from_string(native.row["appointment_ref"])
                )
            ).one()
            with pytest.raises(SessionPrepScopeDenied):
                session_prep_runtime.current_source_state(
                    session, scope, observed_at=datetime.now(UTC), settings=SETTINGS
                )

    captured = native.post()
    assert captured.status_code == 200, captured.text
    assert (
        captured.json()["result"]["encounter"]["appointment_ref"]
        == (native.row["appointment_ref"])
    )


@pytest.mark.parametrize("mapped", [False, True], ids=["legacy", "mapped"])
@pytest.mark.parametrize("fault", ["owner", "identity", "view_as"])
def test_pilot_fallback_rechecks_current_denials(native, engine, mapped, fault):
    _replace_ordinary_entitlement_with_pilot(engine, native, mapped=mapped)
    with Session(engine) as session:
        if fault == "owner":
            appointment = session.get(
                Appointment, TypeID.from_string(native.row["appointment_ref"])
            )
            foreign = Doctor(clerk_id=f"user_synthetic_{uuid4().hex}", biography=None)
            assert appointment is not None
            session.add(foreign)
            session.flush()
            appointment.doctor_id = foreign.id
            session.add(appointment)
            session.commit()
        elif fault == "identity":
            identity = session.exec(
                select(AuthorityActorExternalIdentity).where(
                    AuthorityActorExternalIdentity.subject
                    == native.args["verified_clerk_subject"]
                )
            ).one()
            identity.revoked_at = datetime.now(UTC)
            session.add(identity)
            session.commit()
        else:
            from tests.authority_kernel.scribe_creator_review_authority_test import (
                install_view_as,
            )

            doctor = session.get(Doctor, native.args["doctor_id"])
            identity = session.exec(
                select(AuthorityActorExternalIdentity).where(
                    AuthorityActorExternalIdentity.subject
                    == native.args["verified_clerk_subject"]
                )
            ).one()
            assert doctor is not None
            install_view_as(
                SimpleNamespace(
                    session=session,
                    case=SimpleNamespace(doctor=doctor, identity=identity),
                )
            )
            from app.models.view_as import ViewAsSession

            view = session.exec(
                select(ViewAsSession)
                .where(ViewAsSession.actor_doctor_id == doctor.id)
                .order_by(ViewAsSession.started_at.desc())  # type: ignore[attr-defined]
            ).first()
            assert view is not None
            view.clerk_session_sha256 = hashlib.sha256(
                b"session_invented_scribe"
            ).hexdigest()
            session.add(view)
            session.commit()
    assert _browser_exchange(native).status_code == 403
    assert native.post().status_code == 403


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
        assert doctor is not None
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
            owned_rows = owned._RECORD_ROWS.get()
            assert owned_rows is not None
            assert owned_rows[0] is current
        assert owned._RECORD_ROWS.get() is None


def test_nested_exchange_failure_restores_outer_evidence(native, engine):
    with session_for(engine) as session:
        args = dict(
            practice_id=native.args["practice_id"],
            subject=native.args["verified_clerk_subject"],
        )
        with owned.provider_record_access_row_scope(session):
            with owned.provider_record_owner_rows(session, **args) as doctor:
                assert doctor is not None
                outer = owned.current_capture_doctor_rows(session, doctor)
                assert outer is not None
            with pytest.raises(RuntimeError, match="invented nested exchange"):
                with owned.provider_record_access_row_scope(session):
                    with owned.provider_record_owner_rows(session, **args) as inner:
                        assert inner is not None
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
        assert link is not None
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
    original_record_access = records.resolve_session_prep_provider_access

    def lookup_access(doctor, **kwargs):
        if owned._RECORD_ROWS.get() is None:
            kwargs = kwargs | {"_ownership": None}
        return original_lookup_access(doctor, **kwargs)

    def record_access(doctor, **kwargs):
        session = session_manager._session_context.get()
        proof = (
            owned.current_capture_doctor_rows(session, doctor)
            if session is not None
            else None
        )
        if proof is not None and owned._RECORD_ROWS.get() is not None:
            # Exercise the legacy product-access decision with the retained
            # lock evidence so the shared writer-race helper can still prove
            # the lock. The provider-record result itself always comes from
            # the stricter Session Prep decision below.
            doctor_access.doctor_access_in_force(
                doctor,
                _ownership=proof,
                _practice_id=proof._practice_id,
            )
        return original_record_access(doctor, **kwargs)

    monkeypatch.setattr(companion_encounters, "doctor_access_in_force", lookup_access)
    monkeypatch.setattr(
        records,
        "resolve_session_prep_provider_access",
        record_access,
    )
    test_actual_writer_waits_then_commits_and_next_native_open_denies(
        native,
        engine,
        monkeypatch,
        writer_kind,
        record_property,
        _expected_access_queries=2,
    )
    assert owned._RECORD_ROWS.get() is None and owned._CAPTURE_ROWS.get() is None
