"""Native preparation through actual Core/SQL; remote data is invented."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from time import perf_counter
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

from tests.authority_kernel import scribe_preparation_metadata_test as fixtures
from tests.authority_kernel.scribe_prepared_window_context_test import prepared
from tests.authority_kernel.scribe_window_scope_fence_test import admitted_arguments

engine, native, workflow, remote_reads = (
    fixtures.engine,
    fixtures.native,
    fixtures.workflow,
    fixtures.remote_reads,
)


def preparation_read(native, action):
    if action == "operation":
        return native.post("native_recording_preparation")
    context = prepared(native)
    return native.post(
        "native_recording_preparation", action=action, prepared_context=context
    )


def test_prepared_native_read_matches_fresh_read_and_omits_new_exchange(
    native, engine, remote_reads, record_property
):
    context = prepared(native)
    counts = []

    def count(_connection, _cursor, sql, *_args):
        counts.append(sql.startswith("SELECT set_config('statement_timeout'"))

    event.listen(engine, "before_cursor_execute", count)
    try:
        started = perf_counter()
        ordinary = native.post("native_recording_preparation")
        ordinary_ms = (perf_counter() - started) * 1000
        ordinary_count = len(counts)
        ordinary_deadlines = sum(counts)
        counts.clear()
        started = perf_counter()
        reused = native.post(
            "native_recording_preparation",
            action="prepared_native_read",
            prepared_context=context,
        )
        reused_ms = (perf_counter() - started) * 1000
        reused_count = len(counts)
        reused_deadlines = sum(counts)
    finally:
        event.remove(engine, "before_cursor_execute", count)
    assert ordinary.status_code == reused.status_code == 200
    assert ordinary.headers["x-cosmaneura-prepared-native-read-version"] == "1"
    assert ordinary.json()["result"]["receipt"] == reused.json()["result"]["receipt"]
    assert context["context_token"] not in reused.text
    assert ordinary_count > reused_count and engine.pool.checkedout() == 0
    record_property("ordinary_sql_statements", ordinary_count)
    record_property("prepared_read_sql_statements", reused_count)
    record_property("removed_sql_statements", ordinary_count - reused_count)
    record_property("ordinary_actual_sql", ordinary_count - ordinary_deadlines)
    record_property("prepared_actual_sql", reused_count - reused_deadlines)
    record_property("ordinary_deadline_settings", ordinary_deadlines)
    record_property("prepared_deadline_settings", reused_deadlines)
    record_property("local_ordinary_milliseconds", round(ordinary_ms, 3))
    record_property("local_prepared_milliseconds", round(reused_ms, 3))


def test_native_preparation_matches_context_and_key_with_one_remote_read(
    native, engine, remote_reads, record_property
):
    statements = []

    def count(_connection, _cursor, sql, *_args):
        # Shapes/counts only. No parameters, row contents or credentials.
        statements.append(" ".join(sql.lower().split()))

    event.listen(engine, "before_cursor_execute", count)
    try:
        context = native.post("native_recording_context")
        key = native.post("encryption_key")
        assert context.status_code == key.status_code == 200
        direct_count = len(statements)
        direct_locks = [sql for sql in statements if " for update" in sql]
        statements.clear()
        remote_reads[0].clear()
        combined = native.post("native_recording_preparation")
        combined_count = len(statements)
        combined_locks = [sql for sql in statements if " for update" in sql]
    finally:
        event.remove(engine, "before_cursor_execute", count)
    assert combined.status_code == 200
    value = combined.json()["result"]
    assert value["operation"] == "native_recording_preparation"
    assert value["receipt"] == {
        "schema_version": "1.0",
        "context": context.json()["result"]["receipt"],
        "encryption_key": key.json()["result"]["receipt"],
    }
    assert remote_reads[0] == ["preparation_metadata"]
    assert direct_count > combined_count
    assert set(combined_locks) == set(direct_locks)
    assert engine.pool.checkedout() == 0
    record_property("two_direct_statements", direct_count)
    record_property("combined_statements", combined_count)
    record_property("removed_statements", direct_count - combined_count)


@pytest.mark.parametrize("fault", ["revoked", "failure", "wrong_session", "malformed"])
@pytest.mark.parametrize("action", ["operation", "prepared_native_read"])
def test_uncertain_or_revoked_preparation_publishes_neither_context_nor_key(
    native, engine, remote_reads, monkeypatch, fault, action
):
    calls, original = remote_reads

    async def altered(*args, **kwargs):
        result = await original(*args, **kwargs)
        assert engine.pool.checkedout() == 0
        if fault == "revoked":
            with Session(engine) as session:
                identity = session.execute(
                    select(AuthorityActorExternalIdentity).where(
                        AuthorityActorExternalIdentity.subject
                        == native.args["verified_clerk_subject"]
                    )
                ).scalar_one()
                identity.revoked_at = datetime.now(UTC)
                session.add(identity)
                session.commit()
        elif fault == "failure":
            raise ScribeWorkflowRetryableError("invented_failure")
        elif fault == "wrong_session":
            result = result.model_copy(
                update={
                    "review": result.review.model_copy(update={"session_id": uuid4()})
                }
            )
        else:
            result = result.model_copy(
                update={"encryption_key": {"private_key": "invented"}}
            )
        return result

    monkeypatch.setattr(native.remote, "capture_operation", altered)
    response = preparation_read(native, action)
    assert response.status_code in (403, 409, 503)
    assert "receipt" not in response.json() and len(calls) == 1
    assert engine.pool.checkedout() == 0


@pytest.mark.parametrize("status", ["created", "capturing", "uploaded"])
@pytest.mark.parametrize("action", ["operation", "prepared_native_read"])
def test_native_preparation_requires_current_recording_state(
    native, engine, remote_reads, monkeypatch, status, action
):
    _, original = remote_reads

    async def altered(*args, **kwargs):
        result = await original(*args, **kwargs)
        return result.model_copy(
            update={"review": result.review.model_copy(update={"status": status})}
        )

    monkeypatch.setattr(native.remote, "capture_operation", altered)
    response = preparation_read(native, action)
    assert response.status_code == (409 if status == "uploaded" else 200)
    if status != "uploaded":
        assert response.json()["result"]["receipt"]["context"]["status"] == status
    else:
        assert "receipt" not in response.json()
    assert engine.pool.checkedout() == 0


@pytest.mark.parametrize("fault", ["cancel", "changed_scope"])
def test_changed_or_cancelled_preparation_is_not_published(
    native, engine, remote_reads, monkeypatch, fault
):
    arguments = admitted_arguments(native, monkeypatch)
    arguments["operation"] = protocol.ReadOperation(kind="native_recording_preparation")
    calls, original = remote_reads
    calls.clear()
    budget = CaptureBudget(30)
    authorize = gateway.authorize_admitted_capture
    checks = []

    def current(*args):
        scope = authorize(*args)
        checks.append(True)
        if fault == "changed_scope" and len(checks) > 1:
            return replace(scope, session_id=uuid4())
        return scope

    async def read(*args, **kwargs):
        result = await original(*args, **kwargs)
        if fault == "cancel":
            budget.cancel()
        return result

    monkeypatch.setattr(gateway, "authorize_admitted_capture", current)
    monkeypatch.setattr(native.remote, "capture_operation", read)

    async def invoke():
        with capture_budget(budget):
            return await gateway.execute_capture_operation(**arguments)

    with pytest.raises(
        (
            CaptureDeadlineExceeded,
            gateway.CaptureReceiptInvalid,
            gateway.CaptureOperationUnconfirmed,
        )
    ):
        asyncio.run(invoke())
    assert calls == ["preparation_metadata"] and engine.pool.checkedout() == 0


@pytest.mark.parametrize(
    "extra", ["tenant_id", "session_id", "profile_receipt_id", "operations"]
)
def test_native_preparation_does_not_accept_selected_scope(extra):
    with pytest.raises(ValidationError):
        protocol.OPERATION_ADAPTER.validate_python(
            {"kind": "native_recording_preparation", extra: "invented"}
        )
