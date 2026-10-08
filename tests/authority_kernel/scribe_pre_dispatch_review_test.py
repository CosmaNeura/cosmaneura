"""One fresh post-validation check replaces overlapping creator-review checks."""

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta

import pytest

from app.services import scribe_capture_gateway as gateway
from app.services import scribe_native_capture as native_service
from app.services.authority import ContextTokenExpiredError
from app.services.scribe_capture_deadline import (
    CaptureBudget,
    CaptureDeadlineExceeded,
    capture_budget,
)

from app.models.authority import AuthorityActorExternalIdentity, MountContextToken
from sqlalchemy import event, select
from sqlmodel import Session, col

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
from tests.authority_kernel.scribe_window_scope_fence_test import admitted_arguments

engine, native, remote_reads, workflow = _engine, _native, _remote_reads, _workflow


@pytest.mark.parametrize(
    "kind", ["encryption_key", "capabilities", "preparation_metadata"]
)
def test_actual_native_receipts_and_remaining_lock_order_match_previous_checks(
    native, engine, remote_reads, monkeypatch, kind, record_property
):
    assert native.post(kind).status_code == 200
    monkeypatch.setattr(
        native_service, "execute_capture_operation", gateway.execute_capture_operation
    )
    current = gateway._execute_creator_review
    results = []

    async def previous_checks(*args, **kwargs):
        result = await current(*args, **kwargs)
        if args[5] == "configured-capture-current":
            # The overlapping caller check from 222153c6, after the helper's
            # fresh decision and with no intervening await or remote work.
            assert (
                gateway.authorize_admitted_capture(args[0], args[1], args[2]) == args[3]
            )
        return result

    for previous in (True, False):
        calls = remote_reads[0]
        calls.clear()
        locks = []

        def observe(_connection, _cursor, statement, *_args):
            shape = " ".join(statement.lower().split())
            if " for update" in shape:
                locks.append(shape)

        with monkeypatch.context() as patch:
            if previous:
                patch.setattr(gateway, "_execute_creator_review", previous_checks)
            event.listen(engine, "before_cursor_execute", observe)
            try:
                with query_counts(engine) as counts:
                    response = native.post(kind)
            finally:
                event.remove(engine, "before_cursor_execute", observe)
        assert response.status_code == 200
        results.append(
            (
                response.json()["result"]["receipt"],
                counts["all_selects"],
                locks,
                calls[:],
            )
        )
    assert results[0][0] == results[1][0]
    assert results[0][3] == results[1][3]
    # All remaining lock acquisition follows the original order. The removed
    # whole authorization transactions are deliberately absent.
    remaining = iter(results[0][2])
    assert all(any(old == lock for old in remaining) for lock in results[1][2])
    # Each removed authorization now uses the joined practice read, saving
    # two fewer SELECTs than when membership and practice were read separately.
    assert results[0][1] - results[1][1] == (
        0 if kind == "preparation_metadata" else 67
    )
    assert engine.pool.checkedout() == 0
    record_property("operation", kind)
    record_property("previous_selects", results[0][1])
    record_property("current_selects", results[1][1])


@pytest.mark.parametrize(
    "component", ["creator_review", "encryption_key", "capabilities"]
)
def test_committed_revocation_during_creator_validation_denies_later_read(
    native, engine, remote_reads, monkeypatch, component
):
    assert native.post("preparation_metadata").status_code == 200
    monkeypatch.setattr(
        native_service, "execute_capture_operation", gateway.execute_capture_operation
    )
    calls = remote_reads[0]
    calls.clear()
    original = gateway.validate_receipt
    validations = []

    def revoke(operation, *args, **kwargs):
        checked = original(operation, *args, **kwargs)
        if operation.kind == component:
            validations.append(True)
            if len(validations) == 1:
                assert engine.pool.checkedout() == 0
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
        return checked

    monkeypatch.setattr(gateway, "validate_receipt", revoke)
    response = native.post("preparation_metadata")
    assert response.status_code in (403, 409)
    assert len(calls) == 1
    assert "receipt" not in response.json() and engine.pool.checkedout() == 0


@pytest.mark.parametrize(
    "component", ["creator_review", "encryption_key", "capabilities"]
)
def test_cancellation_during_creator_validation_prevents_later_dispatch(
    native, engine, remote_reads, monkeypatch, component
):
    arguments = admitted_arguments(native, monkeypatch)
    arguments["operation"] = gateway.protocol.ReadOperation(kind="preparation_metadata")
    calls = remote_reads[0]
    calls.clear()
    original = gateway.validate_receipt
    validations = []
    budget = CaptureBudget(30)

    def cancel(operation, *args, **kwargs):
        checked = original(operation, *args, **kwargs)
        if operation.kind == component:
            validations.append(True)
            if len(validations) == 1:
                budget.cancel()
        return checked

    monkeypatch.setattr(gateway, "validate_receipt", cancel)

    async def invoke():
        with capture_budget(budget):
            return await gateway.execute_capture_operation(**arguments)

    with pytest.raises((CaptureDeadlineExceeded, gateway.CaptureOperationUnconfirmed)):
        asyncio.run(invoke())
    assert len(calls) == 1
    assert engine.pool.checkedout() == 0


@pytest.mark.parametrize(
    "component", ["creator_review", "encryption_key", "capabilities"]
)
def test_token_expiry_during_creator_validation_prevents_later_dispatch(
    native, engine, remote_reads, monkeypatch, component
):
    arguments = admitted_arguments(native, monkeypatch)
    arguments["operation"] = gateway.protocol.ReadOperation(kind="preparation_metadata")
    calls = remote_reads[0]
    calls.clear()
    original = gateway.validate_receipt
    validations = []
    token_hash = hashlib.sha256(
        arguments["caller"].mount_context_token.encode()
    ).hexdigest()

    def expire(operation, *args, **kwargs):
        checked = original(operation, *args, **kwargs)
        if operation.kind == component:
            validations.append(True)
            if len(validations) == 1:
                assert engine.pool.checkedout() == 0
                with Session(engine) as session:
                    token = session.execute(
                        select(MountContextToken)
                        .where(col(MountContextToken.token_hash) == token_hash)
                        .with_for_update(nowait=True)
                    ).scalar_one()
                    token.expires_at = datetime.now(UTC) - timedelta(seconds=1)
                    session.add(token)
                    session.commit()
        return checked

    monkeypatch.setattr(gateway, "validate_receipt", expire)

    async def invoke():
        with capture_budget(CaptureBudget(30)):
            return await gateway.execute_capture_operation(**arguments)

    with pytest.raises(ContextTokenExpiredError):
        asyncio.run(invoke())
    assert len(calls) == 1
    assert engine.pool.checkedout() == 0
