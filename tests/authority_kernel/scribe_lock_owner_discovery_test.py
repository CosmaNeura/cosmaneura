"""Lock discovery is not permission; the full protected decisions still run."""

import pytest

from app.services import authority
from app.services import scribe_capture_scope as capture
from app.services import scribe_native_capture as native_service
from app.services.scribe_capture_gateway import execute_capture_operation

from app.models.authority import AuthorityActor, AuthorityPerson, AuthorityTenant
from sqlalchemy import event, select
from sqlmodel import Session

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


def previous_discovery(current):
    def discover(session, mount, settings, ownership):
        # The removed pre-lock decision from bfdfc562. The original fresh
        # identity/doctor/member reads and all lock-taking code remain current.
        context = authority._context_for_session(
            session, mount, clerk_issuer=settings.clerk_issuer
        )
        if (
            context.surface != "provider_assistant"
            or context.auth_tier != "provider"
            or context.person_id is None
            or mount.tenant_id is None
            or mount.actor_id is None
        ):
            raise authority.AuthorityRequestDeniedError
        return current(session, mount, settings, ownership)

    return discover


@pytest.mark.parametrize("kind", ["creator_review", "preparation_metadata"])
def test_actual_native_receipts_and_lock_order_match_without_prelock_policy(
    native, engine, remote_reads, monkeypatch, kind, record_property
):
    # Admit the synthetic visit once before counting. No repeated fixture
    # admission is attributed to the actual production route being compared.
    assert native.post(kind).status_code == 200
    monkeypatch.setattr(
        native_service, "execute_capture_operation", execute_capture_operation
    )
    original = capture._lock_capture_owner
    results = []
    for previous in (True, False):
        locks = []

        def observe(_connection, _cursor, statement, *_args):
            shape = " ".join(statement.lower().split())
            if " for update" in shape:
                locks.append(shape)

        with monkeypatch.context() as patch:
            if previous:
                patch.setattr(
                    capture, "_lock_capture_owner", previous_discovery(original)
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
    assert results[0][1] > results[1][1]
    assert engine.pool.checkedout() == 0
    record_property("operation", kind)
    record_property("previous_selects", results[0][1])
    record_property("discovery_selects", results[1][1])
    record_property("receipts_and_original_locks_identical", True)


@pytest.mark.parametrize("model", [AuthorityActor, AuthorityTenant, AuthorityPerson])
def test_committed_authority_revocation_during_owner_discovery_prevents_io(
    native, engine, remote_reads, monkeypatch, model
):
    assert native.post("preparation_metadata").status_code == 200
    monkeypatch.setattr(
        native_service, "execute_capture_operation", execute_capture_operation
    )
    calls = remote_reads[0]
    calls.clear()
    original = capture._lock_capture_owner

    def revoke(session, mount, settings, ownership):
        owner = original(session, mount, settings, ownership)
        if model is AuthorityPerson:
            from app.models.authority import AuthorityPersonBinding

            binding = session.get(AuthorityPersonBinding, mount.person_binding_id)
            assert binding is not None
            identifier = binding.person_id
        else:
            identifier = mount.actor_id if model is AuthorityActor else mount.tenant_id
        # These authority inputs are locked only after discovery. Commit from
        # an independent transaction while the original owner locks are held.
        with Session(engine) as revoker:
            row = revoker.execute(
                select(model).where(model.id == identifier).with_for_update(nowait=True)
            ).scalar_one()
            row.status = "revoked"
            revoker.add(row)
            revoker.commit()
        return owner

    monkeypatch.setattr(capture, "_lock_capture_owner", revoke)
    assert native.post("preparation_metadata").status_code == 403
    assert calls == []
    assert engine.pool.checkedout() == 0
