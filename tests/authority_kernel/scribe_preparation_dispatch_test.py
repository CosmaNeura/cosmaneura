"""Actual SQL, keys and revocation when one preparation dispatch owns its fence."""

import asyncio
from datetime import UTC, datetime

import pytest

from app.services import scribe_capture_gateway as gateway
from app.services import scribe_capture_protocol as protocol
from app.services import scribe_preparation_metadata as metadata
from app.services import scribe_recording_window as window
from app.services.authority import AuthorityKernelError
from app.services.scribe_capture_deadline import CaptureBudget, capture_budget

from app.models.authority import AuthorityActorExternalIdentity
from sqlalchemy import event
from sqlmodel import Session, select

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
from tests.authority_kernel.scribe_window_scope_fence_test import (
    admitted_arguments,
    install_remote,
)

engine, native, remote_reads, workflow = _engine, _native, _remote_reads, _workflow


def request(native, monkeypatch, remote_reads, kind):
    calls = remote_reads[0]
    arguments = admitted_arguments(native, monkeypatch)
    if kind == "preparation_metadata":
        arguments["operation"] = protocol.ReadOperation(kind=kind)
    else:
        calls = install_remote(native, monkeypatch)
    calls.clear()
    return arguments, calls


async def invoke(arguments, *, previous):
    with capture_budget(CaptureBudget(30)):
        if not previous:
            return await gateway.execute_capture_operation(**arguments)
        # Previous gateway issued this extra complete decision before the
        # helper's own pre-dispatch decision, with no intervening remote I/O.
        scope = gateway.authorize_admitted_capture(
            arguments["caller"], arguments["binding_ref"], arguments["workspace_id"]
        )
        if arguments["operation"].kind == "preparation_metadata":
            return await metadata.execute_preparation_metadata(
                **{
                    k: v
                    for k, v in arguments.items()
                    if k not in {"operation", "upload_origins"}
                },
                scope=scope,
            )
        return await window.execute_recording_window(**arguments, scope=scope)


@pytest.mark.parametrize("kind", ["preparation_metadata", "prepare_recording_window"])
def test_same_receipt_and_keys_with_one_fewer_actual_authorization_transaction(
    native, engine, remote_reads, monkeypatch, record_property, kind
):
    arguments, calls = request(native, monkeypatch, remote_reads, kind)
    results = []
    for previous in (True, False):
        statements, transactions = [], []

        def begin(connection):
            connection.info["preparation_dispatch_locks"] = []

        def statement(connection, _cursor, sql, *_):
            statements.append(True)
            shape = " ".join(sql.lower().split())
            if " for update" in shape:
                connection.info["preparation_dispatch_locks"].append(shape)

        def commit(connection):
            locks = connection.info.pop("preparation_dispatch_locks", [])
            if locks:
                transactions.append(tuple(locks))

        calls.clear()
        event.listen(engine, "begin", begin)
        event.listen(engine, "before_cursor_execute", statement)
        event.listen(engine, "commit", commit)
        try:
            result = asyncio.run(invoke(arguments, previous=previous))
        finally:
            event.remove(engine, "begin", begin)
            event.remove(engine, "before_cursor_execute", statement)
            event.remove(engine, "commit", commit)
        results.append(
            (result.model_dump(mode="json"), len(statements), transactions, calls[:])
        )
    before, after = results
    assert before[0] == after[0] and before[3] == after[3]
    assert before[1] > after[1]
    assert len(before[2]) == 3 and len(after[2]) == 2
    assert all(locks == before[2][0] for locks in before[2] + after[2])
    assert engine.pool.checkedout() == 0
    record_property("operation", kind)
    record_property("previous_statements", before[1])
    record_property("current_statements", after[1])
    record_property("authorization_transactions", "3 -> 2")
    record_property("same_receipt_keys_and_lock_order", True)


@pytest.mark.parametrize("kind", ["preparation_metadata", "prepare_recording_window"])
def test_revocation_committed_at_cancellation_yield_denies_before_remote_dispatch(
    native, engine, remote_reads, monkeypatch, kind
):
    arguments, calls = request(native, monkeypatch, remote_reads, kind)
    original_sleep = asyncio.sleep
    observed = []

    async def revoke(delay):
        assert delay == 0 and engine.pool.checkedout() == 0
        with Session(engine) as session:
            identity = session.exec(
                select(AuthorityActorExternalIdentity)
                .where(
                    AuthorityActorExternalIdentity.subject
                    == native.args["verified_clerk_subject"]
                )
                .with_for_update(nowait=True)
            ).one()
            identity.revoked_at = datetime.now(UTC)
            session.add(identity)
            session.commit()
        observed.append("revoked_before_dispatch")
        await original_sleep(delay)

    monkeypatch.setattr(asyncio, "sleep", revoke)
    with pytest.raises(AuthorityKernelError):
        asyncio.run(invoke(arguments, previous=False))
    assert observed == ["revoked_before_dispatch"] and calls == []
    assert engine.pool.checkedout() == 0
