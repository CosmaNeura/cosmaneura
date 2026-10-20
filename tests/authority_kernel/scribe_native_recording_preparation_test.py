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
from tests.authority_kernel.scribe_prepared_window_context_test import (
    new_mounted_context,
    prepared,
    prepared_admission,
)
from tests.authority_kernel.scribe_window_scope_fence_test import admitted_arguments

engine, native, workflow, remote_reads = (
    fixtures.engine,
    fixtures.native,
    fixtures.workflow,
    fixtures.remote_reads,
)


def test_fresh_consent_returns_same_native_preparation_without_another_request(
    native, engine, remote_reads
):
    mounted = new_mounted_context(native)
    before = len(native.remote.consents), len(native.remote.sessions)
    remote_reads[0].clear()
    answer = prepared_admission(native, mounted, include_native_preparation=True)
    assert answer.status_code == 200, answer.text
    assert answer.headers["x-cosmaneura-admission-native-preparation-version"] == "1"
    result = answer.json()["result"]
    assert (len(native.remote.consents), len(native.remote.sessions)) == (
        before[0] + 1,
        before[1] + 1,
    )
    assert remote_reads[0] == ["preparation_metadata"]
    separate = native.post(
        "native_recording_preparation",
        workspace_id=mounted["workspace_id"],
        binding_ref=mounted["binding_ref"],
    )
    assert separate.status_code == 200, separate.text
    assert (
        result["native_recording_preparation"] == separate.json()["result"]["receipt"]
    )
    assert (
        result["native_recording_preparation"]["context"]["session_id"]
        == result["session_id"]
    )
    assert mounted["prepared_context"]["context_token"] not in answer.text
    assert engine.pool.checkedout() == 0


@pytest.mark.parametrize("fault", ["revocation", "reply_loss"])
def test_combined_admission_never_publishes_partial_metadata_or_repeats_consent(
    native, engine, remote_reads, monkeypatch, fault
):
    mounted = new_mounted_context(native)
    before = len(native.remote.consents), len(native.remote.sessions)
    calls, original = remote_reads
    calls.clear()

    async def altered(*args, **kwargs):
        value = await original(*args, **kwargs)
        if fault == "reply_loss":
            raise ScribeWorkflowRetryableError("synthetic_retryable")
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
        return value

    monkeypatch.setattr(native.remote, "capture_operation", altered)
    answer = prepared_admission(native, mounted, include_native_preparation=True)
    assert answer.status_code == (503 if fault == "reply_loss" else 403), answer.text
    assert (
        "native_recording_preparation" not in answer.text
        and "session_id" not in answer.text
    )
    assert calls == ["preparation_metadata"]
    assert (len(native.remote.consents), len(native.remote.sessions)) == (
        before[0] + 1,
        before[1] + 1,
    )
    assert engine.pool.checkedout() == 0


@pytest.mark.parametrize(
    "action", ["mount", "operation", "prepared_native_read", "preparation_context"]
)
def test_native_admission_preparation_opt_in_is_closed_to_other_actions(native, action):
    with pytest.raises(ValidationError):
        from app.services.scribe_native_capture import NativeCaptureRequest

        from tests.authority_kernel.scribe_native_capture_receiver_test import body

        NativeCaptureRequest.model_validate(
            body(action=action, include_native_preparation=True)
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
