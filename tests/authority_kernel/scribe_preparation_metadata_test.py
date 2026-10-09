"""Actual native route/authority/SQL with synthetic remote Scribe receipts."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.services import scribe_capture_gateway as gateway
from app.services import scribe_capture_protocol as protocol
from app.services.scribe_capture_deadline import (
    CaptureBudget,
    CaptureDeadlineExceeded,
    capture_budget,
)
from app.services.scribe_workflow import ScribeWorkflowRetryableError

from app.models.authority import AuthorityActorExternalIdentity
from sqlalchemy import event, select
from sqlmodel import Session

from tests.authority_kernel.scribe_capture_gateway_test import pair
from tests.authority_kernel.scribe_gateway_receiver_test import scope
from tests.authority_kernel.scribe_native_status_enrichment_test import (
    engine as _engine,
)
from tests.authority_kernel.scribe_native_status_enrichment_test import (
    native as _native,
)
from tests.authority_kernel.scribe_native_status_enrichment_test import (
    workflow as _workflow,
)
from tests.authority_kernel.scribe_window_scope_fence_test import admitted_arguments

engine, native, workflow = _engine, _native, _workflow


@pytest.fixture
def remote_reads(native, monkeypatch):
    original = native.remote.capture_operation
    calls = []
    key = pair("encryption_key", scope())[1]

    async def read(tenant_id, session_id, operation, **kwargs):
        calls.append(operation.kind)
        if operation.kind == "preparation_metadata":
            review = await original(
                tenant_id,
                session_id,
                protocol.ReadOperation(kind="creator_review"),
                **kwargs,
            )
            capabilities = await original(
                tenant_id,
                session_id,
                protocol.ReadOperation(kind="capabilities"),
                **kwargs,
            )
            return protocol.PreparationMetadata(
                review=review, encryption_key=key, capabilities=capabilities
            )
        if operation.kind == "encryption_key":
            assert tenant_id == native.remote.tenant
            return key
        return await original(tenant_id, session_id, operation, **kwargs)

    monkeypatch.setattr(native.remote, "capture_operation", read)
    return calls, read


def test_combined_actual_native_reads_match_direct_receipts_with_less_sql(
    native, engine, remote_reads, record_property
):
    counts = []

    def statement(_connection, _cursor, _sql, _parameters, _context, _many):
        counts.append(True)

    event.listen(engine, "before_cursor_execute", statement)
    try:
        direct = {}
        for kind in ("creator_review", "encryption_key", "capabilities"):
            result = native.post(kind)
            assert result.status_code == 200
            direct[kind] = result.json()["result"]["receipt"]
        direct_count = len(counts)
        counts.clear()
        result = native.post("preparation_metadata")
        combined_count = len(counts)
    finally:
        event.remove(engine, "before_cursor_execute", statement)
    assert result.status_code == 200
    envelope = result.json()["result"]
    assert envelope["operation"] == "preparation_metadata"
    receipt = envelope["receipt"]
    assert receipt == {
        "schema_version": "1.0",
        "review": direct["creator_review"],
        "encryption_key": direct["encryption_key"],
        "capabilities": direct["capabilities"],
    }
    assert direct_count - combined_count > 500
    assert remote_reads[0] == [
        "creator_review",
        "creator_review",
        "encryption_key",
        "creator_review",
        "capabilities",
    ] + ["preparation_metadata"]
    assert engine.pool.checkedout() == 0
    record_property("three_direct_statements", direct_count)
    record_property("combined_statements", combined_count)


@pytest.mark.parametrize("phase", [1])
def test_committed_revocation_at_every_remote_read_prevents_combined_publication(
    native, engine, remote_reads, monkeypatch, phase
):
    calls, original = remote_reads

    async def revoke(*args, **kwargs):
        result = await original(*args, **kwargs)
        assert engine.pool.checkedout() == 0
        if len(calls) == phase:
            with Session(engine) as session:
                identity = session.execute(
                    select(AuthorityActorExternalIdentity)
                    .where(
                        AuthorityActorExternalIdentity.subject
                        == native.args["verified_clerk_subject"]
                    )
                    .with_for_update(nowait=True)
                ).scalar_one()
                identity.revoked_at = datetime.now(UTC)
                session.add(identity)
                session.commit()
        return result

    monkeypatch.setattr(native.remote, "capture_operation", revoke)
    result = native.post("preparation_metadata")
    assert result.status_code in (403, 409)
    assert "encryption_key" not in result.json() and "receipt" not in result.json()
    assert len(calls) == phase and engine.pool.checkedout() == 0


@pytest.mark.parametrize("phase", [1])
def test_remote_failure_returns_no_partial_metadata_or_later_reads(
    native, engine, remote_reads, monkeypatch, phase
):
    calls, original = remote_reads

    async def failed(*args, **kwargs):
        if len(calls) + 1 == phase:
            calls.append(args[2].kind)
            raise ScribeWorkflowRetryableError()
        return await original(*args, **kwargs)

    monkeypatch.setattr(native.remote, "capture_operation", failed)
    result = native.post("preparation_metadata")
    assert result.status_code == 503
    assert "receipt" not in result.json() and len(calls) == phase
    assert engine.pool.checkedout() == 0


@pytest.mark.parametrize(
    "extra", ["session_id", "tenant_id", "profile_receipt_id", "operations"]
)
def test_combined_closed_request_does_not_accept_selected_scope_or_components(extra):
    with pytest.raises(ValidationError):
        protocol.OPERATION_ADAPTER.validate_python(
            {"kind": "preparation_metadata", extra: "invented"}
        )


@pytest.mark.parametrize("phase", [1])
def test_cancelled_component_never_returns_partial_metadata_or_dispatches_later_reads(
    native, engine, remote_reads, monkeypatch, phase
):
    calls, original = remote_reads
    arguments = admitted_arguments(native, monkeypatch)
    arguments["operation"] = protocol.ReadOperation(kind="preparation_metadata")
    calls.clear()
    budget = CaptureBudget(30)

    async def cancel(*args, **kwargs):
        result = await original(*args, **kwargs)
        if len(calls) == phase:
            budget.cancel()
        return result

    monkeypatch.setattr(native.remote, "capture_operation", cancel)

    async def invoke():
        with capture_budget(budget):
            return await gateway.execute_capture_operation(**arguments)

    with pytest.raises((CaptureDeadlineExceeded, gateway.CaptureOperationUnconfirmed)):
        asyncio.run(invoke())
    assert len(calls) == phase and engine.pool.checkedout() == 0


def test_changed_post_read_scope_is_rejected_before_metadata_publication(
    native, engine, remote_reads, monkeypatch
):
    arguments = admitted_arguments(native, monkeypatch)
    arguments["operation"] = protocol.ReadOperation(kind="preparation_metadata")
    calls = remote_reads[0]
    calls.clear()
    original = gateway.authorize_admitted_capture
    checks = []

    def authorize(*args):
        current = original(*args)
        checks.append(True)
        return current if len(checks) == 1 else replace(current, session_id=uuid4())

    monkeypatch.setattr(gateway, "authorize_admitted_capture", authorize)
    with pytest.raises(gateway.CaptureReceiptInvalid):
        asyncio.run(gateway.execute_capture_operation(**arguments))
    assert (
        len(checks) == 2
        and calls == ["preparation_metadata"]
        and engine.pool.checkedout() == 0
    )
