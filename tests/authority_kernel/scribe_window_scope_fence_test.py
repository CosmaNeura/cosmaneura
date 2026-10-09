"""Real native/Core/SQL and immutable expected-scope checks on owned synthetic PG."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from app.services import scribe_capture_gateway as gateway
from app.services import scribe_native_capture as native_service
from app.services.authority import AuthorityKernelError
from app.services.scribe_capture_deadline import CaptureBudget, capture_budget
from app.services.scribe_creator_review_contract import CreatorReviewView

from activemodel import session_manager
from app.models.authority import AuthorityActorExternalIdentity
from sqlalchemy import event
from sqlmodel import Session, select

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
from tests.authority_kernel.scribe_recording_window_test import (
    operation,
    prepared,
    receipts,
    selected_scope,
)

engine, native, workflow = _engine, _native, _workflow


def install_remote(native, monkeypatch, after=None):
    saved = next(iter(native.remote.sessions.values()))
    selected = replace(
        selected_scope(), session_id=saved.id, tenant_id=native.remote.tenant
    )
    values = receipts(selected)
    combined = prepared(selected).model_copy(
        update={
            "binding": values["bind_key"],
            "lease": values["issue_lease"],
            "reservations": values["reserve_chunks"],
        }
    )
    calls = []

    async def capture_operation(tenant, session, component, **kwargs):
        assert tenant == selected.tenant_id and session == selected.session_id
        calls.append((component.kind, kwargs.get("idempotency_key")))
        if component.kind == "prepare_recording_window":
            result = combined
        elif component.kind == "creator_review":
            result = CreatorReviewView(
                schema_version="1.0", session_id=session, status="created"
            )
        else:
            result = values[component.kind]
        if after is not None:
            after(component.kind)
        return result

    monkeypatch.setattr(native.remote, "capture_operation", capture_operation)
    return calls


def admitted_arguments(native, monkeypatch):
    captured = {}
    original_native = native_service.execute_capture_operation

    async def capture_caller(**kwargs):
        captured.update(kwargs)
        return await original_native(**kwargs)

    # Obtain the actual admitted binding/caller via the ordinary read-only
    # native route. Compound writes remain excluded from that route's allowlist.
    with monkeypatch.context() as patch:
        patch.setattr(native_service, "execute_capture_operation", capture_caller)
        assert native.post("creator_review").status_code == 200
    return captured | {
        "operation": operation(),
        "client_request_key": "synthetic-window-replay",
    }


async def invoke(arguments):
    with capture_budget(CaptureBudget(30)):
        return await gateway.execute_capture_operation(**arguments)


def test_real_gateway_window_removes_requests_with_current_lock_order(
    native, engine, monkeypatch, record_property
):
    calls = install_remote(native, monkeypatch)
    arguments = admitted_arguments(native, monkeypatch)
    from app.services import scribe_recording_window as window

    from tests.authority_kernel.scribe_recording_window_legacy_fixture import (
        execute_recording_window as legacy,
    )

    current = window.execute_recording_window

    async def previous_gateway(**kwargs):
        # Reproduce the former outer gateway decision for the legacy helper.
        kwargs["scope"] = gateway.authorize_admitted_capture(
            kwargs["caller"], kwargs["binding_ref"], kwargs["workspace_id"]
        )
        return await legacy(**kwargs)

    results = []
    for previous in (True, False):
        transactions = []

        def begin(connection):
            connection.info["scope_fence_locks"] = []

        def commit(connection):
            locks = connection.info.pop("scope_fence_locks", [])
            if locks:
                transactions.append(tuple(locks))

        def observe(connection, _cursor, statement, *_args):
            shape = " ".join(statement.lower().split())
            if " for update" in shape:
                connection.info["scope_fence_locks"].append(shape)

        calls.clear()
        event.listen(engine, "begin", begin)
        event.listen(engine, "commit", commit)
        event.listen(engine, "before_cursor_execute", observe)
        try:
            with monkeypatch.context() as patch, query_counts(engine) as count:
                patch.setattr(
                    window,
                    "execute_recording_window",
                    previous_gateway if previous else current,
                )
                response = asyncio.run(invoke(arguments))
        finally:
            event.remove(engine, "before_cursor_execute", observe)
            event.remove(engine, "commit", commit)
            event.remove(engine, "begin", begin)
        results.append(
            (response.model_dump(mode="json"), count, transactions, calls[:])
        )
    assert results[0][0] == results[1][0]
    assert [row[0] for row in results[0][3]] == [
        "creator_review",
        "bind_key",
        "issue_lease",
        "reserve_chunks",
    ]
    assert [row[0] for row in results[1][3]] == ["prepare_recording_window"]
    assert results[1][1]["all_selects"] < results[0][1]["all_selects"]
    before, after = results[0][2], results[1][2]
    assert len(after) == 2 and len(before) == 11
    assert all(transaction == before[0] for transaction in before + after)
    record_property("previous_selects", results[0][1]["all_selects"])
    record_property("current_selects", results[1][1]["all_selects"])
    record_property("previous_authorization_transactions", len(before))
    record_property("current_authorization_transactions", len(after))
    record_property("previous_scribe_requests", len(results[0][3]))
    record_property("current_scribe_requests", len(results[1][3]))


@pytest.mark.parametrize(
    "phase,kind",
    [("remote", "prepare_recording_window")]
    + [
        ("validation", kind)
        for kind in (
            "bind_key",
            "issue_lease",
            "reserve_chunks",
            "prepare_recording_window",
        )
    ],
)
def test_committed_revocation_prevents_next_component_or_final_receipt(
    native, engine, monkeypatch, phase, kind
):
    calls = install_remote(native, monkeypatch)
    arguments = admitted_arguments(native, monkeypatch)

    def revoke(observed):
        if observed != kind:
            return
        # A real independent writer commits while the gateway holds no locks.
        assert session_manager._session_context.get() is None
        assert engine.pool.checkedout() == 0
        with Session(engine) as session:
            identity = session.exec(
                select(AuthorityActorExternalIdentity).where(
                    AuthorityActorExternalIdentity.subject
                    == native.args["verified_clerk_subject"]
                )
            ).one()
            identity.revoked_at = datetime.now(UTC)
            session.add(identity)
            session.commit()

    if phase == "remote":
        calls = install_remote(native, monkeypatch, after=revoke)
    else:
        original = gateway.validate_receipt

        def validate(component, *args, **kwargs):
            result = original(component, *args, **kwargs)
            revoke(component.kind)
            return result

        monkeypatch.setattr(gateway, "validate_receipt", validate)
    calls.clear()
    with pytest.raises(AuthorityKernelError):
        asyncio.run(invoke(arguments))
    assert [call[0] for call in calls] == ["prepare_recording_window"]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["bind_key", "issue_lease", "reserve_chunks"])
@pytest.mark.parametrize(
    "changed", ["session_id", "tenant_id", "creator_actor_id", "operation_ref"]
)
async def test_expected_scope_is_compared_with_current_authorization_before_remote_io(
    monkeypatch, kind, changed
):
    from uuid import uuid4

    from app.services.scribe_workflow import DisabledScribeWorkflowClient

    from tests.authority_kernel.scribe_capture_gateway_test import pair

    selected = selected_scope()
    changed_value = (
        uuid4()
        if changed.endswith("_id") and changed != "creator_actor_id"
        else "changed"
    )
    monkeypatch.setattr(
        gateway,
        "authorize_admitted_capture",
        lambda *_: replace(selected, **{changed: changed_value}),
    )
    component, _ = pair(kind, selected)
    with pytest.raises(gateway.CaptureReceiptInvalid):
        await gateway.execute_capture_operation(
            caller=None,
            binding_ref="synthetic",
            workspace_id=uuid4(),
            operation=component,
            client_request_key="synthetic-window-component",
            client=DisabledScribeWorkflowClient(),
            _expected_scope=selected,
        )
