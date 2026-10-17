"""Only numeric observations; no network, credentials, or shared database."""

import asyncio
import contextvars
from types import SimpleNamespace
from typing import Any, cast

import pytest

from app.utils import native_request_timing as timing

from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError


@pytest.fixture
def observations(monkeypatch):
    rows = []
    monkeypatch.setattr(timing, "enabled", lambda: True)
    monkeypatch.setattr(timing, "_emit", rows.append)
    return rows


@pytest.mark.parametrize(
    "environment", ["production", "test", "development", "Staging", ""]
)
def test_explicit_staging_gate_rejects_other_environments(monkeypatch, environment):
    monkeypatch.setenv("PYTHON_ENV", environment)
    monkeypatch.setenv("SCRIBE_NATIVE_REQUEST_TIMING", "true")
    assert not timing.enabled()


@pytest.mark.parametrize("flag", ["", "TRUE", "1", " true", "false"])
def test_gate_accepts_only_exact_managed_value(monkeypatch, flag):
    monkeypatch.setenv("PYTHON_ENV", "staging")
    monkeypatch.setenv("SCRIBE_NATIVE_REQUEST_TIMING", flag)
    assert not timing.enabled()


def test_explicit_staging_gate(monkeypatch):
    monkeypatch.setenv("PYTHON_ENV", "staging")
    monkeypatch.setenv("SCRIBE_NATIVE_REQUEST_TIMING", "true")
    assert timing.enabled()


def test_request_description_accepts_only_closed_actions(observations):
    with timing.request_timing() as ledger:
        ledger.describe_request("prepared_admission")
        ledger.describe_request("https://synthetic-private.invalid/patient")
    assert observations[0]["request_kind"] == "prepared_admission"
    assert "private" not in repr(observations)
    ledger.describe_request("prepared_source")
    assert observations[0]["request_kind"] == "prepared_admission"


def test_disabled_keeps_results_exceptions_and_no_collection(monkeypatch):
    monkeypatch.setattr(timing, "enabled", lambda: False)
    monkeypatch.setattr(timing, "_emit", lambda _: pytest.fail("disabled emission"))
    expected = ValueError("invented-private-error")
    with pytest.raises(ValueError) as caught:
        with timing.request_timing() as ledger:
            assert ledger is None and not timing.active()
            with timing.phase("clerk_auth"):
                raise expected
    assert caught.value is expected


def test_phase_failure_nested_and_safe_numeric_schema(observations):
    expected = RuntimeError("invented-private-error")
    with timing.request_timing():
        with timing.phase("workload_agent"):
            with pytest.raises(RuntimeError) as caught:
                with timing.phase("entra_jwks"):
                    raise expected
            assert caught.value is expected
    row = observations[0]
    assert row["phase_count"]["workload_agent"] == 1
    assert row["phase_count"]["entra_jwks"] == 1
    assert row["phase_failures"]["entra_jwks"] == 1
    assert row["phase_failures"]["workload_agent"] == 0
    assert set(row) == {
        "event",
        "request_kind",
        "total_ms",
        "phase_ms",
        "phase_count",
        "phase_failures",
        "sql_cursor_ms",
        "sql_cursor_count",
        "sql_cursor_failures",
    }
    assert set(row["phase_ms"]) == set(timing.PHASES)
    assert "private" not in repr(row)
    assert not timing.active()


def test_sql_success_error_and_disabled_query_unchanged(observations, monkeypatch):
    engine = create_engine("sqlite://")
    with timing.request_timing():
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT :value"), {"value": "private-fixture"}
                ).scalar()
                == "private-fixture"
            )
            with pytest.raises(OperationalError):
                connection.execute(text("SELECT nonexistent_private_fixture"))
    assert observations[0]["sql_cursor_count"] == 2
    assert observations[0]["sql_cursor_failures"] == 1
    assert "private" not in repr(observations)
    monkeypatch.setattr(timing, "enabled", lambda: False)
    with timing.request_timing(), engine.connect() as connection:
        assert connection.execute(text("SELECT 2")).scalar() == 2
    assert len(observations) == 1
    engine.dispose()


@pytest.mark.asyncio
async def test_task_and_thread_contexts_are_isolated(observations):
    barrier = asyncio.Event()

    async def worker(name):
        with timing.request_timing():
            await barrier.wait()
            await asyncio.to_thread(lambda: execute(name))

    def execute(name):
        with timing.phase(name):
            pass

    tasks = [
        asyncio.create_task(worker(name)) for name in ("workload_agent", "clerk_auth")
    ]
    await asyncio.sleep(0)
    barrier.set()
    await asyncio.gather(*tasks)
    assert len(observations) == 2
    assert sorted(
        (row["phase_count"]["workload_agent"], row["phase_count"]["clerk_auth"])
        for row in observations
    ) == [(0, 1), (1, 0)]
    assert not timing.active()


@pytest.mark.asyncio
async def test_cancellation_freezes_ledger_and_late_child_is_ignored(observations):
    started = asyncio.Event()
    never = asyncio.Event()
    saved = []

    async def worker():
        with timing.request_timing():
            saved.append(contextvars.copy_context())
            with timing.phase("downstream"):
                started.set()
                await never.wait()

    task = asyncio.create_task(worker())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    before = repr(observations)
    saved[0].run(lambda: complete_late())
    assert repr(observations) == before
    assert observations[0]["phase_failures"]["downstream"] == 1
    assert not timing.active()


def complete_late():
    with timing.phase("entra_jwks"):
        pass
    context = SimpleNamespace()
    timing._before_cursor(None, None, "private", {"private": "value"}, context, False)
    timing._after_cursor(None, None, "private", {"private": "value"}, context, False)


def test_logging_and_clock_failures_never_mask_source_error(monkeypatch):
    monkeypatch.setattr(timing, "enabled", lambda: True)
    monkeypatch.setattr(
        timing, "_emit", lambda _: (_ for _ in ()).throw(OSError("offline"))
    )
    expected = ValueError("source")
    with pytest.raises(ValueError) as caught:
        with timing.request_timing():
            with timing.phase("clerk_auth"):
                raise expected
    assert caught.value is expected and not timing.active()
    monkeypatch.setattr(
        timing, "perf_counter", lambda: (_ for _ in ()).throw(ValueError("clock"))
    )
    with timing.request_timing() as ledger:
        assert ledger is None


@pytest.mark.parametrize("elapsed", [float("nan"), float("inf"), -1, 1_000_000])
@pytest.mark.parametrize(
    "phase", ["clerk_auth", "workflow_token", "workflow_http", "capture_transaction"]
)
def test_finite_bounded_durations_and_counts(monkeypatch, elapsed, phase):
    ledger = timing.Ledger()
    monkeypatch.setattr(timing, "perf_counter", lambda: elapsed)
    ledger.counts[phase] = timing.MAX_COUNT
    ledger.record(phase, 0, False)
    row = cast(dict[str, Any], ledger.close())
    assert 0 <= row["total_ms"] <= timing.MAX_MS
    assert 0 <= row["phase_ms"][phase] <= timing.MAX_MS
    assert row["phase_count"][phase] == timing.MAX_COUNT


def test_jwks_observer_preserves_constructor_fetch_result_error_and_cache(
    monkeypatch, observations
):
    from jwt import PyJWKClient

    from app.routes.dependencies import entra_service as entra

    expected = {"keys": []}
    calls = []

    def fetch(client):
        calls.append(client.uri)
        return expected

    monkeypatch.setattr(PyJWKClient, "fetch_data", fetch)
    policy = cast(
        entra.EntraAuthPolicy,
        SimpleNamespace(
            issuer="https://identity.test.invalid",
            jwks_url="https://identity.test.invalid/jwks",
        ),
    )
    plain = entra.EntraTokenDecoder(policy).jwks_client
    assert type(plain) is PyJWKClient
    with timing.request_timing():
        wrapped = entra.EntraTokenDecoder(policy).jwks_client
        assert isinstance(wrapped, entra._ObservedJWKClient)
        assert wrapped.timeout == plain.timeout
        assert wrapped.jwk_set_cache is not None and plain.jwk_set_cache is not None
        assert wrapped.jwk_set_cache.lifespan == plain.jwk_set_cache.lifespan
        assert (
            cast(Any, wrapped.get_signing_key).cache_info().maxsize
            == cast(Any, plain.get_signing_key).cache_info().maxsize
        )
        assert wrapped.fetch_data() is expected
        error = ValueError("private-fetch-error")
        monkeypatch.setattr(
            PyJWKClient, "fetch_data", lambda _: (_ for _ in ()).throw(error)
        )
        with pytest.raises(ValueError) as caught:
            wrapped.fetch_data()
        assert caught.value is error
    assert calls == [policy.jwks_url]
    assert observations[0]["phase_count"]["entra_jwks"] == 2
    assert observations[0]["phase_failures"]["entra_jwks"] == 1
    assert policy.jwks_url not in repr(observations)


def test_real_jwks_cache_same_key_unknown_key_and_new_client_are_unchanged(
    monkeypatch, observations
):
    import io
    import json
    import urllib.request

    from cryptography.hazmat.primitives.asymmetric import rsa
    from jwt import PyJWKClient, PyJWKClientError
    from jwt.algorithms import RSAAlgorithm

    from app.routes.dependencies import entra_service as entra

    public_key = rsa.generate_private_key(
        public_exponent=65537, key_size=2048
    ).public_key()
    jwk = json.loads(RSAAlgorithm.to_jwk(public_key)) | {
        "kid": "synthetic-key",
        "use": "sig",
        "alg": "RS256",
    }
    requests = []

    class Opener:
        def open(self, request, timeout):
            requests.append((request.full_url, timeout))
            return io.BytesIO(json.dumps({"keys": [jwk]}).encode())

    monkeypatch.setattr(urllib.request, "build_opener", lambda *_: Opener())
    baseline = PyJWKClient("https://identity.test.invalid/jwks", cache_keys=True)
    original = baseline.get_signing_key("synthetic-key")
    assert baseline.get_signing_key("synthetic-key") is original
    with pytest.raises(PyJWKClientError):
        baseline.get_signing_key("unknown-key")
    baseline_count = len(requests)
    with timing.request_timing():
        observed = entra._ObservedJWKClient(
            "https://identity.test.invalid/jwks", cache_keys=True
        )
        selected = observed.get_signing_key("synthetic-key")
        assert observed.get_signing_key("synthetic-key") is selected
        with pytest.raises(PyJWKClientError):
            observed.get_signing_key("unknown-key")
        assert len(requests) - baseline_count == baseline_count
        # Existing per-call decoder construction remains fresh, not cached here.
        another = entra._ObservedJWKClient(
            "https://identity.test.invalid/jwks", cache_keys=True
        )
        another.get_signing_key("synthetic-key")
    assert observations[0]["phase_count"]["entra_jwks"] == baseline_count + 1
    assert all(timeout == 30 for _, timeout in requests)


@pytest.mark.parametrize("denied", [None, "agent", "capture"])
def test_mounted_native_route_observes_thread_dependencies_and_preserves_http(
    monkeypatch, observations, denied
):
    from fastapi import HTTPException

    from app.routes import scribe_native_capture as route

    from tests.authority_kernel import scribe_native_capture_receiver_test as fixture

    client, calls = fixture.client(monkeypatch)
    if denied:

        def reject(_):
            raise HTTPException(403, "private-error")

        monkeypatch.setattr(
            route,
            "authenticate_"
            + ("agent_api_request" if denied == "agent" else "scribe_capture_request"),
            reject,
        )
    response = client.post(fixture.PATH, json=fixture.body(), headers=fixture.headers())
    assert response.status_code == (403 if denied else 200)
    assert len(observations) == 1
    assert observations[0]["phase_count"]["workload_agent"] == 1
    assert observations[0]["phase_count"]["workload_capture"] == int(denied != "agent")
    if denied:
        assert observations[0]["phase_failures"]["workload_" + denied] == 1
    assert not timing.active()
    assert "private" not in repr(observations)
    import json

    header = response.headers["x-cosmaneura-native-request-timing"]
    snapshot = json.loads(header)
    assert len(header.encode("ascii")) <= timing.MAX_HEADER_BYTES
    assert snapshot["phase_count"] == observations[0]["phase_count"]
    assert snapshot["phase_failures"] == observations[0]["phase_failures"]
    assert "private" not in header


@pytest.mark.parametrize("enabled", [False, True])
def test_response_observation_absent_when_disabled_or_broken(monkeypatch, enabled):
    from tests.authority_kernel import scribe_native_capture_receiver_test as fixture

    monkeypatch.setattr(timing, "enabled", lambda: enabled)
    monkeypatch.setattr(timing, "_emit", lambda _: None)
    if enabled:

        def fail(_):
            raise ValueError("synthetic-private-observer-error")

        monkeypatch.setattr(timing.Ledger, "snapshot", fail)
    client, _ = fixture.client(monkeypatch)
    response = client.post(fixture.PATH, json=fixture.body(), headers=fixture.headers())
    assert response.status_code == 200
    assert response.json()["version"] == "1"
    assert "x-cosmaneura-native-request-timing" not in response.headers


@pytest.mark.parametrize(
    "bad", ["extra", "private_kind", "unknown_phase", "nonfinite", "boolean_count"]
)
def test_response_snapshot_rejects_nonclosed_metadata(monkeypatch, bad):
    monkeypatch.setattr(timing, "enabled", lambda: True)
    ledger = timing.Ledger()
    row = ledger.snapshot()
    if bad == "extra":
        row["private_context"] = "synthetic-private"
    elif bad == "private_kind":
        row["request_kind"] = "synthetic-private"
    elif bad == "unknown_phase":
        row["phase_ms"]["private_context"] = 1
    elif bad == "nonfinite":
        row["phase_ms"]["workflow_token"] = float("nan")
    else:
        row["phase_count"]["workflow_token"] = True
    monkeypatch.setattr(ledger, "snapshot", lambda: row)
    assert timing.response_header(ledger) is None


def test_response_snapshot_is_bounded_and_does_not_close_ledger(monkeypatch):
    monkeypatch.setattr(timing, "enabled", lambda: True)
    ledger = timing.Ledger()
    assert timing.response_header(ledger) is not None and not ledger.closed
    monkeypatch.setattr(timing, "MAX_HEADER_BYTES", 1)
    assert timing.response_header(ledger) is None and not ledger.closed
    ledger.record("workflow_http", timing.perf_counter(), False)
    assert ledger.close()["phase_count"]["workflow_http"] == 1


@pytest.mark.asyncio
async def test_native_service_counts_fresh_policy_calls_without_changing_order(
    monkeypatch, observations
):
    from tests.authority_kernel import scribe_native_capture_service_test as fixture

    _, calls, _, invoke = fixture.fixture(monkeypatch)
    with timing.request_timing():
        result = await invoke()
    assert result["version"] == "1"
    assert [c if isinstance(c, str) else c[0] for c in calls] == [
        "lookup",
        "exchange",
        "lookup",
        "resolve",
        "lookup",
        "binding",
        "lookup",
        "operation",
        "lookup",
    ]
    counts = observations[0]["phase_count"]
    assert counts["policy_lookup"] == 5
    assert (
        counts["policy_exchange"]
        == counts["policy_mount"]
        == counts["policy_binding"]
        == counts["operation"]
        == 1
    )
    assert counts["downstream"] == 0  # Fake gateway does not make a wire call.


@pytest.mark.asyncio
async def test_downstream_counts_actual_client_calls_and_errors(observations):
    from app.services.scribe_capture_deadline import (
        CaptureBudget,
        DeadlineScribeClient,
        capture_budget,
    )

    expected = object()
    failure = ValueError("private-error")

    class Client:
        async def get_capture_status(self, *args, **kwargs):
            return expected

        async def capture_operation(self, *args, **kwargs):
            raise failure

    client = DeadlineScribeClient(Client())
    with timing.request_timing(), capture_budget(CaptureBudget(10)):
        assert await client.get_capture_status("private-id") is expected
        with pytest.raises(ValueError) as caught:
            await client.capture_operation("private-id")
        assert caught.value is failure
    assert observations[0]["phase_count"]["downstream"] == 2
    assert observations[0]["phase_failures"]["downstream"] == 1
    assert "private" not in repr(observations)


@pytest.mark.parametrize("outer_boundary", [False, True])
def test_full_native_boundary_emits_once_with_private_context_isolation(
    monkeypatch, observations, outer_boundary
):
    from app.routes import scribe_capture_deadline as boundary
    from app.routes import scribe_native_capture as route
    from app.routes.dependencies.entra_service import EntraServicePrincipal

    from tests.authority_kernel import scribe_native_capture_receiver_test as fixture

    client, _ = fixture.client(monkeypatch)
    identity_context = contextvars.ContextVar[str | None](
        "synthetic_parent_identity", default=None
    )
    token = identity_context.set("private-parent-identity")
    seen = []

    def authenticate(_):
        seen.append(identity_context.get())
        return EntraServicePrincipal(fixture.WORKLOAD)

    monkeypatch.setattr(route, "authenticate_agent_api_request", authenticate)
    if outer_boundary:
        cast(Any, client.app).add_middleware(
            boundary.CaptureBoundary, paths=boundary.CAPTURE_PATHS
        )
    try:
        assert (
            client.post(
                fixture.PATH, json=fixture.body(), headers=fixture.headers()
            ).status_code
            == 200
        )
    finally:
        identity_context.reset(token)
    assert seen == [None]  # Observer transfer must never copy the parent context.
    assert len(observations) == 1
    assert observations[0]["phase_count"]["workload_agent"] == 1
    assert not timing.active()


@pytest.mark.parametrize("outer_boundary", [False, True])
def test_timeout_freezes_once_and_closed_worker_updates_cannot_emit(
    monkeypatch, observations, outer_boundary
):
    from app.routes import scribe_capture_deadline as boundary
    from app.routes import scribe_native_capture as route

    from tests.authority_kernel import scribe_native_capture_receiver_test as fixture

    client, _ = fixture.client(monkeypatch)
    monkeypatch.setattr(boundary, "CAPTURE_REQUEST_SECONDS", 0.08)
    saved = []

    async def stall(**_):
        saved.append(timing.current_ledger())
        with timing.phase("operation"):
            await asyncio.sleep(20)

    monkeypatch.setattr(route, "execute_native_capture", stall)
    if outer_boundary:
        cast(Any, client.app).add_middleware(
            boundary.CaptureBoundary, paths=boundary.CAPTURE_PATHS
        )
    assert (
        client.post(
            fixture.PATH, json=fixture.body(), headers=fixture.headers()
        ).status_code
        == 504
    )
    assert len(observations) == 1
    assert saved[0] is not None and saved[0].closed
    snapshot = repr(observations)
    saved[0].record("downstream", 0, False)
    saved[0].record_sql(0, True)
    assert repr(observations) == snapshot and not timing.active()


def test_emission_contains_only_closed_json_without_logging_context(monkeypatch):
    import io
    import json

    import structlog

    buffer = io.StringIO()
    monkeypatch.setattr(timing, "enabled", lambda: True)
    monkeypatch.setattr(timing.sys, "stdout", buffer)
    with structlog.contextvars.bound_contextvars(
        doctor_id="private-sentinel", token="private-token"
    ):
        with timing.request_timing(), timing.phase("clerk_auth"):
            pass
    output = buffer.getvalue()
    row = json.loads(output)
    assert output.count("\n") == 1
    assert row["event"] == "native_core_request_timing"
    assert "private" not in output and "token" not in row and "doctor_id" not in row
    assert set(row["phase_ms"]) == set(timing.PHASES)
