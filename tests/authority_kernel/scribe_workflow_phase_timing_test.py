"""Actual transport/transaction observations with synthetic credentials and I/O."""

import asyncio
from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from azure.core.credentials import AccessToken
from azure.core.exceptions import AzureError
from pydantic import BaseModel

from app.services import scribe_capture_admission as admission
from app.services.scribe_capture_deadline import (
    CaptureBudget,
    CaptureDeadlineExceeded,
    capture_budget,
)
from app.services.scribe_workflow import (
    HttpScribeWorkflowClient,
    ScribeWorkflowRetryableError,
)
from app.utils import native_request_timing as timing


class Reply(BaseModel):
    ok: bool


@pytest.fixture
def rows(monkeypatch):
    result = []
    monkeypatch.setattr(timing, "enabled", lambda: True)
    monkeypatch.setattr(timing, "_emit", result.append)
    return result


class Credential:
    def __init__(self, action=None):
        self.action, self.calls = action, 0

    async def get_token(self, *scopes):
        assert scopes == ("api://synthetic-scribe/.default",)
        self.calls += 1
        if self.action:
            await self.action()
        return AccessToken("synthetic-private-token", 4_000_000_000)

    async def close(self):
        pass


async def request(credential, handler, *, method="POST"):
    async with httpx.AsyncClient(
        base_url="https://synthetic.invalid", transport=httpx.MockTransport(handler)
    ) as http:
        client = HttpScribeWorkflowClient(
            base_url="https://synthetic.invalid",
            scope="api://synthetic-scribe/.default",
            timeout_seconds=5,
            credential=credential,
            client=http,
        )
        if method == "POST":
            return await client._post(
                "/v1/fixed", uuid4(), Reply(ok=True), "synthetic-key", Reply
            )
        return await client._get_resource(uuid4(), "/v1/fixed", Reply, max_bytes=16384)


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["POST", "GET"])
async def test_token_and_http_are_separate_current_request_observations(rows, method):
    credential = Credential()

    async def handler(request):
        assert credential.calls == 1
        assert request.headers["authorization"] == "Bearer synthetic-private-token"
        return httpx.Response(200, json={"ok": True})

    with timing.request_timing():
        with timing.phase("downstream"):
            assert await request(credential, handler, method=method) == Reply(ok=True)
    row = rows[0]
    for phase in ("workflow_token", "workflow_http", "downstream"):
        assert row["phase_count"][phase] == 1
        assert row["phase_failures"][phase] == 0
    assert row["phase_ms"]["downstream"] >= row["phase_ms"]["workflow_token"]
    assert row["phase_ms"]["downstream"] >= row["phase_ms"]["workflow_http"]
    assert "synthetic-private" not in repr(rows)


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["POST", "GET"])
async def test_identity_failure_remains_fixed_and_never_dispatches_http(rows, method):
    async def reject():
        raise AzureError("synthetic-private-token-exception")

    def forbidden(_):
        pytest.fail("identity failure must not dispatch HTTP")

    with timing.request_timing(), pytest.raises(ScribeWorkflowRetryableError) as caught:
        await request(Credential(reject), forbidden, method=method)
    assert caught.value.code == "scribe_identity_unavailable"
    assert rows[0]["phase_failures"]["workflow_token"] == 1
    assert rows[0]["phase_count"]["workflow_http"] == 0
    assert "synthetic-private" not in repr(rows)


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["POST", "GET"])
async def test_http_timeout_preserves_code_and_token_success(rows, method):
    def reject(_):
        raise httpx.ReadTimeout("synthetic-private-response")

    with timing.request_timing(), pytest.raises(ScribeWorkflowRetryableError) as caught:
        await request(Credential(), reject, method=method)
    assert caught.value.code == "scribe_request_timeout"
    assert rows[0]["phase_failures"]["workflow_token"] == 0
    assert rows[0]["phase_failures"]["workflow_http"] == 1
    assert "synthetic-private" not in repr(rows)


@pytest.mark.asyncio
async def test_async_phase_includes_owned_response_close(rows):
    entered, release = asyncio.Event(), asyncio.Event()

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'{"ok":true}'

        async def aclose(self):
            entered.set()
            await release.wait()

    async def handler(_):
        return httpx.Response(
            200, headers={"content-type": "application/json"}, stream=Body()
        )

    async def worker():
        with timing.request_timing():
            return await request(Credential(), handler)

    task = asyncio.create_task(worker())
    await asyncio.wait_for(entered.wait(), 2)
    assert not task.done() and rows == []
    release.set()
    assert await task == Reply(ok=True)
    assert rows[0]["phase_count"]["workflow_http"] == 1
    assert rows[0]["phase_failures"]["workflow_http"] == 0


@pytest.mark.asyncio
async def test_cancelled_http_keeps_cancellation_and_separate_context(rows):
    entered, never = asyncio.Event(), asyncio.Event()

    async def handler(_):
        entered.set()
        await never.wait()

    async def worker():
        with timing.request_timing():
            await request(Credential(), handler)

    task = asyncio.create_task(worker())
    await asyncio.wait_for(entered.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert rows[0]["phase_count"]["workflow_token"] == 1
    assert rows[0]["phase_failures"]["workflow_http"] == 1
    assert not timing.active()


@pytest.mark.asyncio
async def test_deadline_after_token_cannot_start_http(rows):
    budget = CaptureBudget(5)

    async def cancel():
        budget.cancel()

    def forbidden(_):
        pytest.fail("cancelled deadline must not dispatch HTTP")

    with (
        capture_budget(budget),
        timing.request_timing(),
        pytest.raises(CaptureDeadlineExceeded),
    ):
        await request(Credential(cancel), forbidden)
    assert rows[0]["phase_count"]["workflow_token"] == 1
    assert rows[0]["phase_count"]["workflow_http"] == 0


@pytest.mark.asyncio
async def test_disabled_observer_preserves_transport_and_emits_nothing(
    rows, monkeypatch
):
    monkeypatch.setattr(timing, "enabled", lambda: False)
    with timing.request_timing():
        assert await request(
            Credential(), lambda _: httpx.Response(200, json={"ok": True})
        ) == Reply(ok=True)
    assert rows == []


@pytest.mark.asyncio
async def test_concurrent_workflow_requests_keep_distinct_numeric_ledgers(rows):
    entered, release = asyncio.Event(), asyncio.Event()

    async def credential_action():
        entered.set()
        await release.wait()

    async def worker(method):
        with timing.request_timing():
            return await request(
                Credential(credential_action),
                lambda _: httpx.Response(200, json={"ok": True}),
                method=method,
            )

    tasks = [asyncio.create_task(worker(method)) for method in ("POST", "GET")]
    await asyncio.wait_for(entered.wait(), 2)
    assert rows == []
    release.set()
    assert await asyncio.gather(*tasks) == [Reply(ok=True), Reply(ok=True)]
    assert len(rows) == 2
    assert all(
        row["phase_count"]["workflow_token"] == row["phase_count"]["workflow_http"] == 1
        for row in rows
    )
    assert not timing.active()


def test_transaction_includes_acquire_commit_and_context_exit(rows, monkeypatch):
    clock = [1.0]
    monkeypatch.setattr(timing, "perf_counter", lambda: clock[0])
    session = SimpleNamespace(commit=lambda: clock.__setitem__(0, clock[0] + 0.007))

    @contextmanager
    def managed():
        clock[0] += 0.003
        yield session
        clock[0] += 0.005

    monkeypatch.setattr(admission, "get_session", managed)
    with timing.request_timing():
        with admission._database() as actual:
            assert actual is session
            actual.commit()
    assert rows[0]["phase_count"]["capture_transaction"] == 1
    assert rows[0]["phase_ms"]["capture_transaction"] == pytest.approx(15)
    assert (
        rows[0]["sql_cursor_count"] == 0
    )  # Inclusive transaction is not a SQL-only timer.


@pytest.mark.parametrize("failure", ["acquisition", "commit"])
def test_transaction_failure_preserves_error_and_rollback(rows, monkeypatch, failure):
    expected, rolled_back = RuntimeError("synthetic-private-db-error"), []

    def commit():
        raise expected

    session = SimpleNamespace(commit=commit, rollback=lambda: rolled_back.append(True))

    @contextmanager
    def managed():
        if failure == "acquisition":
            raise expected
        yield session

    monkeypatch.setattr(admission, "get_session", managed)
    with timing.request_timing(), pytest.raises(RuntimeError) as caught:
        with admission._database() as actual:
            actual.commit()
    assert caught.value is expected
    assert rolled_back == ([] if failure == "acquisition" else [True])
    assert rows[0]["phase_failures"]["capture_transaction"] == 1
    assert "synthetic-private" not in repr(rows)
