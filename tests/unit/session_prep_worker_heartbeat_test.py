"""Real SDK check-ins with in-memory HTTP; no database or external requests."""

from __future__ import annotations

import ast
import inspect
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, cast
from uuid import uuid4

import pytest
import sentry_sdk
from sentry_sdk.envelope import Envelope, Item, PayloadRef

from app.configuration import session_prep_health_alerts as health
from app.jobs import heartbeat

SENTINEL = "SYNTHETIC-PRIVATE-SOURCE-AND-CREDENTIAL-SENTINEL"
DSN = "https://synthetic@example.invalid/1"


def value():
    reference = uuid4().hex
    return {
        "type": "check_in",
        "event_id": reference,
        "check_in_id": reference,
        "monitor_slug": "session-prep-staging-worker",
        "status": "ok",
        "duration": 0.0,
        "environment": "staging",
        "release": "session-prep-health",
        "monitor_config": {
            "schedule": {"type": "interval", "value": 1, "unit": "minute"},
            "timezone": "UTC",
            "checkin_margin": 2,
            "max_runtime": 1,
            "failure_issue_threshold": 1,
            "recovery_threshold": 1,
        },
    }


def envelope(payload=None):
    payload = payload if payload is not None else value()
    result = Envelope(
        headers={
            "event_id": payload["event_id"],
            "sent_at": datetime.now(UTC).isoformat(),
        }
    )
    result.add_checkin(payload)
    return result


@pytest.fixture
def local_http(monkeypatch):
    health.close_health_client()
    calls, closed = [], []
    pool = SimpleNamespace(
        request=lambda method, url, **kwargs: calls.append((method, url, kwargs))
        or SimpleNamespace(status=200, headers={}, close=lambda: closed.append(True))
    )

    class LocalTransport(health._HealthTransport):
        def _make_pool(self) -> Any:
            return pool

    monkeypatch.setattr(health, "_HealthTransport", LocalTransport)
    monkeypatch.setattr(health, "is_staging", lambda: True)
    monkeypatch.setattr(health, "sentry_enabled", lambda: False)
    monkeypatch.setattr(
        health,
        "config",
        lambda name, **_kwargs: DSN if name == "SENTRY_DSN" else "true",
    )
    yield SimpleNamespace(calls=calls, closed=closed, pool=pool)
    health.close_health_client()


def flush():
    health._client.flush(timeout=1)


def wire_checkin(request):
    method, url, kwargs = request
    assert method == "POST" and url == "https://example.invalid/api/1/envelope/"
    assert kwargs["redirect"] is False and kwargs["retries"] is False
    assert set(kwargs["headers"]) == {"Content-Type", "User-Agent", "X-Sentry-Auth"}
    header, item, payload = map(json.loads, kwargs["body"].splitlines())
    assert set(header) == {"event_id", "sent_at"}
    assert item["type"] == "check_in" and set(item) <= {
        "type",
        "content_type",
        "length",
    }
    assert header["event_id"] == payload["check_in_id"] == payload["event_id"]
    expected = value()
    expected.update(event_id=payload["event_id"], check_in_id=payload["check_in_id"])
    assert payload == expected
    assert health._canonical_heartbeat(payload)
    return payload


def test_existing_task_to_actual_SDK_HTTP_is_fixed_and_global_client_unchanged(
    local_http,
):
    before = tuple(
        scope.client
        for scope in (
            sentry_sdk.get_global_scope(),
            sentry_sdk.get_current_scope(),
            sentry_sdk.get_isolation_scope(),
        )
    )
    assert heartbeat.perform() is None
    flush()
    assert len(local_http.calls) == len(local_http.closed) == 1
    wire_checkin(local_http.calls[0])
    assert before == tuple(
        scope.client
        for scope in (
            sentry_sdk.get_global_scope(),
            sentry_sdk.get_current_scope(),
            sentry_sdk.get_isolation_scope(),
        )
    )
    assert health._client.integrations == {}
    assert health._client.options["transport_queue_size"] == 4
    assert health._client.transport._worker._queue.maxsize == 4


def test_existing_60second_schedule_and_registered_task_name_are_preserved():
    assert not inspect.signature(health.capture_worker_heartbeat).parameters
    assert "app.jobs.heartbeat.perform" in heartbeat.celery_app.tasks
    source = Path(heartbeat.__file__).parents[1] / "celery.py"
    tree = ast.parse(source.read_bytes())
    schedules = [
        node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Attribute) and target.attr == "beat_schedule"
            for target in node.targets
        )
    ]
    assert len(schedules) == 1
    assert isinstance(schedules[0], ast.Dict)
    selected = next(
        child
        for key, child in zip(schedules[0].keys, schedules[0].values)
        if isinstance(key, ast.Constant) and key.value == "heartbeat"
    )
    assert ast.literal_eval(selected) == {
        "task": "app.jobs.heartbeat.perform",
        "schedule": 60.0,
    }
    with pytest.raises(TypeError):
        cast(Callable[..., None], health.capture_worker_heartbeat)(
            monitor_slug=SENTINEL
        )


def test_ambient_scope_processors_attachments_and_hostile_SDK_environment_are_unused(
    local_http, monkeypatch, caplog
):
    for name in ("SENTRY_RELEASE", "SENTRY_ENVIRONMENT", "SENTRY_SPOTLIGHT"):
        monkeypatch.setenv(name, SENTINEL)
    monkeypatch.setenv("SENTRY_DEBUG", "true")
    processors = []
    with sentry_sdk.isolation_scope() as scope:
        scope.set_user({"id": SENTINEL})
        scope.set_context("clinical", {"source": SENTINEL})
        scope.add_attachment(bytes=SENTINEL.encode(), filename="synthetic.txt")
        scope.add_event_processor(
            lambda payload, _hint: processors.append(1)
            or {**payload, "message": SENTINEL}
        )
        heartbeat.perform()
    flush()
    assert processors == [] and len(local_http.calls) == 1
    assert SENTINEL not in repr(wire_checkin(local_http.calls[0]))
    assert SENTINEL not in caplog.text and DSN not in caplog.text


@pytest.mark.parametrize("optin", [None, "false", "True", "TRUE", "1", "", True, False])
def test_nonexact_optin_does_not_read_DSN_or_construct_client(monkeypatch, optin):
    reads = []
    monkeypatch.setattr(health, "is_staging", lambda: True)
    monkeypatch.setattr(
        health, "config", lambda name, **_kwargs: reads.append(name) or optin
    )
    monkeypatch.setattr(
        health,
        "_make_client",
        lambda _dsn: pytest.fail("disabled heartbeat constructed a client"),
    )
    health.capture_worker_heartbeat()
    assert reads == ["SESSION_PREP_HEALTH_ALERTS_ENABLED"]


def test_production_or_enabled_global_client_cannot_construct_private_heartbeat(
    monkeypatch,
):
    monkeypatch.setattr(health, "is_staging", lambda: False)
    monkeypatch.setattr(
        health, "config", lambda *_a, **_k: pytest.fail("nonstaging read config")
    )
    health.capture_worker_heartbeat()
    monkeypatch.setattr(health, "health_alerts_enabled", lambda: True)
    monkeypatch.setattr(health, "sentry_enabled", lambda: True)
    health.capture_worker_heartbeat()


@pytest.mark.parametrize(
    "field",
    [
        "request",
        "contexts",
        "trace",
        "extra",
        "message",
        "user",
        "breadcrumbs",
        "task_id",
        "source",
    ],
)
def test_private_or_unknown_payload_field_is_rejected_before_queue_and_HTTP(
    local_http, field
):
    health.capture_worker_heartbeat()
    flush()
    transport = health._client.transport
    payload = value()
    payload[field] = SENTINEL
    forged = envelope(payload)
    assert health._closed_envelope(forged) is False
    transport.capture_envelope(forged)
    transport._send_envelope(forged)
    flush()
    assert len(local_http.calls) == 1


@pytest.mark.parametrize(
    "field,setting",
    [
        ("monitor_slug", SENTINEL),
        ("type", "event"),
        ("status", "error"),
        ("status", "in_progress"),
        ("duration", False),
        ("duration", 0),
        ("duration", 1.0),
        ("environment", "production"),
        ("release", SENTINEL),
        ("event_id", "x" * 32),
        ("check_in_id", "f" * 32),
    ],
)
def test_noncanonical_checkin_values_are_rejected(field, setting):
    payload = value()
    payload[field] = setting
    assert health._closed_envelope(envelope(payload)) is False


@pytest.mark.parametrize(
    "field,setting",
    [
        ("schedule", {"type": "interval", "value": True, "unit": "minute"}),
        ("schedule", {"type": "interval", "value": 1.0, "unit": "minute"}),
        ("schedule", {"type": "interval", "value": 1, "unit": "second"}),
        ("timezone", SENTINEL),
        ("checkin_margin", True),
        ("checkin_margin", 2.0),
        ("max_runtime", True),
        ("failure_issue_threshold", True),
        ("recovery_threshold", True),
        ("unknown", SENTINEL),
    ],
)
def test_monitor_config_cannot_be_changed_or_extended(field, setting):
    payload = value()
    payload["monitor_config"][field] = setting
    assert health._closed_envelope(envelope(payload)) is False


@pytest.mark.parametrize(
    "kind",
    [
        "attachment",
        "transaction",
        "event",
        "session",
        "client_report",
        "profile",
        "log",
    ],
)
def test_mixed_items_are_rejected_before_queue_and_send(local_http, kind):
    health.capture_worker_heartbeat()
    flush()
    forged = envelope()
    forged.items.append(Item(payload=PayloadRef(bytes=SENTINEL.encode()), type=kind))
    transport = health._client.transport
    transport.capture_envelope(forged)
    transport._send_envelope(forged)
    flush()
    assert len(local_http.calls) == 1


def test_duplicate_JSON_headers_oversize_and_late_queue_mutation_are_rejected(
    local_http, monkeypatch
):
    health.capture_worker_heartbeat()
    flush()
    transport = health._client.transport
    forged = envelope()
    forged.headers["trace"] = {"source": SENTINEL}
    assert health._closed_envelope(forged) is False
    forged = envelope()
    forged.items[0].headers["filename"] = SENTINEL
    assert health._closed_envelope(forged) is False
    forged = envelope()
    raw = json.dumps(value()).replace(
        '"status": "ok"', '"status": "ok", "status": "ok"'
    )
    forged.items = [Item(payload=PayloadRef(bytes=raw.encode()), type="check_in")]
    assert health._closed_envelope(forged) is False
    forged.items = [Item(payload=PayloadRef(bytes=b" " * 1025), type="check_in")]
    assert health._closed_envelope(forged) is False
    queued = []
    monkeypatch.setattr(
        transport._worker, "submit", lambda action: queued.append(action) or True
    )
    forged = envelope()
    transport.capture_envelope(forged)
    assert len(queued) == 1
    forged.headers["trace"] = {"source": SENTINEL}
    queued[0]()
    assert len(local_http.calls) == 1


def test_monitor_rate_limit_is_separate_from_existing_error_events(local_http):
    health.capture_worker_heartbeat()
    flush()
    transport = health._client.transport
    transport._disabled_until["monitor"] = datetime.now(UTC) + timedelta(seconds=30)
    health.capture_worker_heartbeat()
    health.capture_health_category("SessionPrepMaintenanceUnavailable")
    flush()
    assert len(local_http.calls) == 2
    assert json.loads(local_http.calls[1][2]["body"].splitlines()[1])["type"] == "event"


@pytest.mark.parametrize("status", [301, 401, 429, 500])
def test_failed_HTTP_response_is_closed_once_without_retry_or_raw_logs(
    local_http, caplog, status
):
    local_http.pool.request = lambda method, url, **kwargs: local_http.calls.append(
        (method, url, kwargs)
    ) or SimpleNamespace(
        status=status,
        headers={},
        data=SENTINEL.encode(),
        close=lambda: local_http.closed.append(True),
    )
    heartbeat.perform()
    flush()
    assert len(local_http.calls) == len(local_http.closed) == 1
    assert SENTINEL not in caplog.text and DSN not in caplog.text


def test_network_failure_does_not_fail_or_retry_heartbeat_task(local_http, caplog):
    attempts = []

    def fail(*_args, **_kwargs):
        attempts.append(1)
        raise RuntimeError(SENTINEL)

    local_http.pool.request = fail
    assert heartbeat.perform() is None
    flush()
    assert attempts == [1] and SENTINEL not in caplog.text


@pytest.mark.parametrize("phase", ["DSN", "construct", "queue", "identifier"])
def test_local_capture_failure_cannot_fail_or_retry_the_task(
    monkeypatch, caplog, phase
):
    health.close_health_client()

    def fail(*_args, **_kwargs):
        raise RuntimeError(SENTINEL)

    monkeypatch.setattr(health, "health_alerts_enabled", lambda: True)
    monkeypatch.setattr(health, "sentry_enabled", lambda: False)
    monkeypatch.setattr(
        health, "config", fail if phase == "DSN" else lambda *_a, **_k: DSN
    )
    if phase == "construct":
        monkeypatch.setattr(health, "_make_client", fail)
    elif phase == "queue":
        monkeypatch.setattr(
            health,
            "_health_client",
            lambda: SimpleNamespace(transport=SimpleNamespace(capture_envelope=fail)),
        )
    elif phase == "identifier":
        monkeypatch.setattr(health, "uuid4", fail)
    if phase not in ("construct", "queue"):
        monkeypatch.setattr(
            health,
            "_make_client",
            lambda _dsn: pytest.fail("failed capture reached SDK construction"),
        )
    assert heartbeat.perform() is None
    health.close_health_client()
    assert SENTINEL not in caplog.text and DSN not in caplog.text


def test_fork_recreates_client_without_using_or_flushing_parent(
    local_http, monkeypatch
):
    parent_calls = []
    monkeypatch.setattr(
        health,
        "_client",
        SimpleNamespace(
            close=lambda **_k: parent_calls.append("close"),
            transport=SimpleNamespace(
                capture_envelope=lambda _e: parent_calls.append("capture")
            ),
        ),
    )
    monkeypatch.setattr(health, "_client_pid", -1)
    previous_lock = health._client_lock
    health.capture_worker_heartbeat()
    flush()
    assert parent_calls == [] and health._client_lock is not previous_lock
    assert len(local_http.calls) == 1


def test_same_process_shutdown_drains_queue_and_closes_once(local_http, monkeypatch):
    health.capture_worker_heartbeat()
    client = health._client
    closes = []
    actual_close = client.close
    monkeypatch.setattr(
        client,
        "close",
        lambda **kwargs: closes.append(kwargs) or actual_close(**kwargs),
    )
    health.close_health_client()
    health.close_health_client()
    assert closes == [{"timeout": 1}] and health._client is None
    assert len(local_http.calls) == len(local_http.closed) == 1
