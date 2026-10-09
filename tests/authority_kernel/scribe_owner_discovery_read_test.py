"""Current lock-owner discovery batching keeps original authority and locks."""

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services import scribe_capture_scope as capture

from app.models.authority import (
    AuthorityActor,
    AuthorityActorExternalIdentity,
    MountContextSession,
)
from app.models.doctor import Doctor
from app.models.practice import PracticeMember
from sqlalchemy import event
from sqlalchemy.orm.attributes import set_committed_value
from sqlmodel import select

from tests.authority_kernel.companion_provider_provisioning_test import (
    SETTINGS,
    session_for,
)
from tests.authority_kernel.scribe_native_status_enrichment_test import query_counts
from tests.authority_kernel.scribe_preparation_metadata_test import engine as _engine
from tests.authority_kernel.scribe_preparation_metadata_test import native as _native
from tests.authority_kernel.scribe_preparation_metadata_test import (
    remote_reads as _remote_reads,
)
from tests.authority_kernel.scribe_preparation_metadata_test import (
    workflow as _workflow,
)

engine, native, remote_reads, workflow = _engine, _native, _remote_reads, _workflow


def previous_discovery(session, mount, settings):
    identity = session.get(
        AuthorityActorExternalIdentity, mount.source_identity_id, populate_existing=True
    )
    if (
        identity is None
        or identity.actor_id != mount.actor_id
        or identity.issuer != settings.clerk_issuer
        or identity.revoked_at is not None
    ):
        raise capture.CaptureScopeDenied
    doctor = session.exec(
        select(Doctor)
        .where(Doctor.clerk_id == identity.subject)
        .execution_options(populate_existing=True)
    ).one_or_none()
    if doctor is None:
        raise capture.CaptureScopeDenied
    member = session.exec(
        select(PracticeMember)
        .where(PracticeMember.doctor_id == doctor.id)
        .execution_options(populate_existing=True)
    ).one_or_none()
    return identity, doctor, member


def previous_current(session, owner):
    return (
        session.get(
            AuthorityActorExternalIdentity, owner.identity_id, populate_existing=True
        ),
        session.get(MountContextSession, owner.mount_id, populate_existing=True),
    )


@pytest.mark.parametrize("kind", ["mount", "creator_review", "preparation_metadata"])
def test_complete_native_receipts_and_exact_locks_match_separate_discovery(
    native, engine, remote_reads, monkeypatch, record_property, kind
):
    assert native.post(kind).status_code == 200
    results = []
    for previous in (True, False):
        locks = []

        def observe(_connection, _cursor, statement, *_):
            shape = " ".join(statement.lower().split())
            if " for update" in shape:
                locks.append(shape)

        remote_reads[0].clear()
        with monkeypatch.context() as patch:
            if previous:
                patch.setattr(
                    capture, "_capture_owner_discovery_rows", previous_discovery
                )
                patch.setattr(
                    capture, "_capture_owner_current_identity", previous_current
                )
            event.listen(engine, "before_cursor_execute", observe)
            try:
                with query_counts(engine) as count:
                    response = native.post(kind)
            finally:
                event.remove(engine, "before_cursor_execute", observe)
        assert response.status_code == 200
        result = response.json()["result"]
        results.append(
            (
                result["encounter"] if kind == "mount" else result["receipt"],
                count["all_selects"],
                locks,
                remote_reads[0][:],
            )
        )
        assert engine.pool.checkedout() == 0
    assert results[0][0] == results[1][0]
    assert results[0][2:] == results[1][2:]
    assert results[1][1] < results[0][1]
    record_property("operation", kind)
    record_property("previous_selects", results[0][1])
    record_property("current_selects", results[1][1])
    record_property("exact_locks_and_remote_sequence_match", True)


@pytest.mark.parametrize("change", ["revoke", "subject", "issuer", "actor"])
def test_identity_change_committed_after_discovery_denies_before_io(
    native, engine, remote_reads, monkeypatch, change
):
    remote_reads[0].clear()
    original = capture._capture_owner_discovery_rows

    def changed(session, mount, settings):
        result = original(session, mount, settings)
        identity = result[0]
        with session_for(engine) as writer:
            row = writer.get(AuthorityActorExternalIdentity, identity.id)
            if change == "revoke":
                row.revoked_at = datetime.now(UTC)
            elif change == "subject":
                row.subject = "user_changed_owner_" + uuid4().hex
            elif change == "issuer":
                row.issuer = "https://changed.invalid"
            else:
                # Existing fictional actor with a valid foreign key, not a
                # fabricated integrity failure standing in for a denial.
                actor = AuthorityActor()
                writer.add(actor)
                writer.flush()
                row.actor_id = actor.id
            writer.add(row)
            writer.commit()
        return result

    monkeypatch.setattr(capture, "_capture_owner_discovery_rows", changed)
    assert native.post("preparation_metadata").status_code == 403
    assert remote_reads[0] == [] and engine.pool.checkedout() == 0


@pytest.mark.parametrize("field", ["actor_id", "source_identity_id"])
def test_current_mount_change_after_provider_locks_denies(
    native, engine, remote_reads, monkeypatch, field
):
    from typeid import TypeID

    remote_reads[0].clear()
    original = capture._capture_owner_current_identity

    def changed(session, owner):
        identity, mount = original(session, owner)
        set_committed_value(
            mount,
            field,
            TypeID("authority_actor" if field == "actor_id" else "actor_identity"),
        )
        return identity, mount

    monkeypatch.setattr(capture, "_capture_owner_current_identity", changed)
    assert native.post("preparation_metadata").status_code == 403
    assert remote_reads[0] == [] and engine.pool.checkedout() == 0


@pytest.mark.parametrize("phase", ["discovery", "current"])
def test_join_refreshes_loaded_identity_and_mount_rows(
    native, engine, remote_reads, monkeypatch, phase
):
    original = (
        capture._capture_owner_discovery_rows
        if phase == "discovery"
        else capture._capture_owner_current_identity
    )
    observed_errors = []

    def stale(session, selected, *args):
        identity_id = (
            selected.source_identity_id
            if phase == "discovery"
            else selected.identity_id
        )
        identity = session.get(AuthorityActorExternalIdentity, identity_id)
        set_committed_value(identity, "issuer", "https://stale.invalid")
        if phase == "current":
            mount = session.get(MountContextSession, selected.mount_id)
            set_committed_value(mount, "status", "stale")
        try:
            rows = original(session, selected, *args)
            assert identity.issuer == SETTINGS.clerk_issuer
            if phase == "current":
                assert mount.status == "active"
            return rows
        except Exception as error:
            observed_errors.append(error)
            raise

    monkeypatch.setattr(
        capture,
        "_capture_owner_discovery_rows"
        if phase == "discovery"
        else "_capture_owner_current_identity",
        stale,
    )
    response = native.post("preparation_metadata")
    if observed_errors:
        raise observed_errors[0]
    assert response.status_code == 200


@pytest.mark.parametrize("component", ["identity", "doctor", "member", "mount"])
def test_absent_owner_components_cannot_reach_remote_io(
    native, engine, remote_reads, monkeypatch, component
):
    remote_reads[0].clear()
    phase = "current" if component == "mount" else "discovery"
    name = (
        "_capture_owner_current_identity"
        if phase == "current"
        else "_capture_owner_discovery_rows"
    )
    original = getattr(capture, name)

    def absent(*args):
        rows = list(original(*args))
        rows[{"identity": 0, "doctor": 1, "member": 2, "mount": 1}[component]] = None
        return tuple(rows)

    monkeypatch.setattr(capture, name, absent)
    assert native.post("preparation_metadata").status_code == 403
    assert remote_reads[0] == [] and engine.pool.checkedout() == 0


def test_nontransactional_adapter_preserves_original_discovery_refusal_order():
    calls = []

    class Adapter:
        def get(self, model, identifier, **kwargs):
            calls.append(model)
            return SimpleNamespace(
                actor_id="other", issuer=SETTINGS.clerk_issuer, revoked_at=None
            )

        def exec(self, *_):
            pytest.fail("Invalid identity must deny before Doctor/member discovery")

    with pytest.raises(capture.CaptureScopeDenied):
        capture._capture_owner_discovery_rows(
            Adapter(),
            SimpleNamespace(source_identity_id="identity", actor_id="actor"),
            SETTINGS,
        )
    assert calls == [AuthorityActorExternalIdentity]
