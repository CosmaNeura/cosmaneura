"""Production heartbeat boundary tests with in-memory HTTP only."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
import sentry_sdk
from sentry_sdk.envelope import Envelope, Item, PayloadRef

from app.configuration import session_prep_production_heartbeat as heartbeat_health
from app.jobs import heartbeat

SENTINEL = "SYNTHETIC-PRIVATE-CHART-PROVIDER-CREDENTIAL-SENTINEL"
DSN = "https://synthetic@example.invalid/1"


def value() -> dict[str, Any]:
    reference = uuid4().hex
    return {
        "type": "check_in",
        "event_id": reference,
        "check_in_id": reference,
        "monitor_slug": "session-prep-production-worker",
        "status": "ok",
        "duration": 0.0,
        "environment": "production",
        "release": "session-prep-production-health",
        "monitor_config": {
            "schedule": {"type": "interval", "value": 1, "unit": "minute"},
            "timezone": "UTC",
            "checkin_margin": 2,
            "max_runtime": 1,
            "failure_issue_threshold": 1,
            "recovery_threshold": 1,
        },
    }


def envelope(payload: dict[str, Any] | None = None) -> Envelope:
    payload = payload if payload is not None else value()
    result = Envelope(
        headers={
            "event_id": payload["event_id"],
            "sent_at": datetime.now(UTC).isoformat(),
        }
    )
    result.add_checkin(payload)
    return result


def global_clients() -> tuple[Any, ...]:
    return tuple(
        scope.client
        for scope in (
            sentry_sdk.get_global_scope(),
            sentry_sdk.get_current_scope(),
            sentry_sdk.get_isolation_scope(),
        )
    )


@pytest.fixture
def local_http(monkeypatch: pytest.MonkeyPatch):
    heartbeat_health.close_production_heartbeat_client()
    calls: list[Any] = []
    closed: list[bool] = []
    pool = SimpleNamespace(
        request=lambda method, url, **kwargs: calls.append((method, url, kwargs))
        or SimpleNamespace(status=200, headers={}, close=lambda: closed.append(True))
    )

    class LocalTransport(heartbeat_health._ProductionHeartbeatTransport):
        def _make_pool(self) -> Any:
            return pool

    monkeypatch.setattr(
        heartbeat_health, "_ProductionHeartbeatTransport", LocalTransport
    )
    monkeypatch.setattr(heartbeat_health, "is_production", lambda: True)
    monkeypatch.setattr(
        heartbeat_health,
        "config",
        lambda name, **_kwargs: DSN if name == "SENTRY_DSN" else "true",
    )
    yield SimpleNamespace(calls=calls, closed=closed, pool=pool)
    heartbeat_health.close_production_heartbeat_client()


def flush() -> None:
    heartbeat_health._client.flush(timeout=1)


def wire_checkin(request: Any) -> dict[str, Any]:
    method, url, kwargs = request
    assert method == "POST" and url == "https://example.invalid/api/1/envelope/"
    assert kwargs["redirect"] is False and kwargs["retries"] is False
    assert set(kwargs["headers"]) == {
        "Content-Type",
        "User-Agent",
        "X-Sentry-Auth",
    }
    header, item, payload = map(json.loads, kwargs["body"].splitlines())
    assert set(header) == {"event_id", "sent_at"}
    assert item["type"] == "check_in"
    assert header["event_id"] == payload["event_id"] == payload["check_in_id"]
    expected = value()
    expected.update(event_id=payload["event_id"], check_in_id=payload["check_in_id"])
    assert payload == expected
    return payload


def test_ordinary_heartbeat_task_sends_fixed_production_checkin_without_global_binding(
    local_http: Any,
) -> None:
    before = global_clients()
    with sentry_sdk.isolation_scope() as scope:
        scope.set_user({"id": SENTINEL})
        scope.set_context("clinical", {"note": SENTINEL})
        scope.add_attachment(bytes=SENTINEL.encode(), filename="synthetic.txt")
        heartbeat.perform()
    flush()
    assert len(local_http.calls) == len(local_http.closed) == 1
    assert SENTINEL not in repr(wire_checkin(local_http.calls[0]))
    assert global_clients() == before
    assert heartbeat_health._client.integrations == {}
    assert heartbeat_health._client.options["send_default_pii"] is False


@pytest.mark.parametrize("production", [False, None])
def test_nonproduction_never_reads_optin_or_dsn(
    monkeypatch: pytest.MonkeyPatch, production: bool | None
) -> None:
    monkeypatch.setattr(heartbeat_health, "is_production", lambda: production)
    monkeypatch.setattr(
        heartbeat_health,
        "config",
        lambda *_a, **_k: pytest.fail("nonproduction read heartbeat config"),
    )
    heartbeat_health.capture_production_worker_heartbeat()


@pytest.mark.parametrize("optin", [None, "false", "True", "TRUE", "1", "", True])
def test_nonexact_optin_never_reads_dsn_or_constructs_client(
    monkeypatch: pytest.MonkeyPatch, optin: Any
) -> None:
    reads: list[str] = []

    def setting(name: str, **_kwargs: Any) -> Any:
        reads.append(name)
        return "true" if name == "SESSION_PREP_PROVIDER_ENABLED" else optin

    monkeypatch.setattr(heartbeat_health, "is_production", lambda: True)
    monkeypatch.setattr(heartbeat_health, "config", setting)
    monkeypatch.setattr(
        heartbeat_health,
        "_make_client",
        lambda _dsn: pytest.fail("disabled heartbeat constructed a client"),
    )
    heartbeat_health.capture_production_worker_heartbeat()
    assert reads == [
        "SESSION_PREP_PROVIDER_ENABLED",
        "SESSION_PREP_PRODUCTION_HEARTBEAT_ENABLED",
    ]


@pytest.mark.parametrize("provider_gate", [None, "false", "True", "1", "", True])
def test_heartbeat_drift_cannot_outlive_the_product_gate_or_read_dsn(
    monkeypatch: pytest.MonkeyPatch, provider_gate: Any
) -> None:
    reads: list[str] = []

    def setting(name: str, **_kwargs: Any) -> Any:
        reads.append(name)
        if name == "SESSION_PREP_PROVIDER_ENABLED":
            return provider_gate
        return "true"

    monkeypatch.setattr(heartbeat_health, "is_production", lambda: True)
    monkeypatch.setattr(heartbeat_health, "config", setting)
    monkeypatch.setattr(
        heartbeat_health,
        "_make_client",
        lambda _dsn: pytest.fail("product-dark heartbeat constructed a client"),
    )
    heartbeat_health.capture_production_worker_heartbeat()
    assert reads == ["SESSION_PREP_PROVIDER_ENABLED"]


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
        "doctor_id",
        "appointment_id",
        "source",
    ],
)
def test_private_or_unknown_payload_is_rejected_before_http(
    local_http: Any, field: str
) -> None:
    heartbeat_health.capture_production_worker_heartbeat()
    flush()
    forged_payload = value()
    forged_payload[field] = SENTINEL
    forged = envelope(forged_payload)
    transport = heartbeat_health._client.transport
    assert heartbeat_health._closed_envelope(forged) is False
    transport.capture_envelope(forged)
    transport._send_envelope(forged)
    flush()
    assert len(local_http.calls) == 1


@pytest.mark.parametrize(
    "field,replacement",
    [
        ("monitor_slug", "session-prep-staging-worker"),
        ("monitor_slug", SENTINEL),
        ("status", "error"),
        ("status", "in_progress"),
        ("environment", "staging"),
        ("release", SENTINEL),
        ("duration", 0),
        ("duration", 1.0),
        ("event_id", "x" * 32),
        ("check_in_id", "f" * 32),
    ],
)
def test_noncanonical_checkin_is_rejected(field: str, replacement: Any) -> None:
    payload = value()
    payload[field] = replacement
    assert heartbeat_health._closed_envelope(envelope(payload)) is False


@pytest.mark.parametrize(
    "field,replacement",
    [
        ("schedule", {"type": "interval", "value": True, "unit": "minute"}),
        ("schedule", {"type": "interval", "value": 1, "unit": "second"}),
        ("timezone", SENTINEL),
        ("checkin_margin", 2.0),
        ("max_runtime", True),
        ("failure_issue_threshold", True),
        ("recovery_threshold", True),
        ("unknown", SENTINEL),
    ],
)
def test_monitor_configuration_cannot_be_changed_or_extended(
    field: str, replacement: Any
) -> None:
    payload = value()
    payload["monitor_config"][field] = replacement
    assert heartbeat_health._closed_envelope(envelope(payload)) is False


@pytest.mark.parametrize(
    "kind",
    ["attachment", "transaction", "event", "session", "client_report", "log"],
)
def test_mixed_items_duplicate_keys_and_oversize_are_rejected(
    local_http: Any, kind: str
) -> None:
    heartbeat_health.capture_production_worker_heartbeat()
    flush()
    transport = heartbeat_health._client.transport

    forged = envelope()
    forged.items.append(Item(payload=PayloadRef(bytes=SENTINEL.encode()), type=kind))
    transport.capture_envelope(forged)
    transport._send_envelope(forged)

    forged = envelope()
    raw = json.dumps(value()).replace(
        '"status": "ok"', '"status": "ok", "status": "ok"'
    )
    forged.items = [Item(payload=PayloadRef(bytes=raw.encode()), type="check_in")]
    assert heartbeat_health._closed_envelope(forged) is False
    forged.items = [Item(payload=PayloadRef(bytes=b" " * 1025), type="check_in")]
    assert heartbeat_health._closed_envelope(forged) is False
    flush()
    assert len(local_http.calls) == 1


@pytest.mark.parametrize("status", [301, 401, 429, 500])
def test_http_failure_closes_once_without_retry_or_sensitive_logs(
    local_http: Any, caplog: pytest.LogCaptureFixture, status: int
) -> None:
    local_http.pool.request = lambda method, url, **kwargs: local_http.calls.append(
        (method, url, kwargs)
    ) or SimpleNamespace(
        status=status,
        headers={},
        data=SENTINEL.encode(),
        close=lambda: local_http.closed.append(True),
    )
    heartbeat_health.capture_production_worker_heartbeat()
    flush()
    assert len(local_http.calls) == len(local_http.closed) == 1
    assert SENTINEL not in caplog.text and DSN not in caplog.text


def test_network_failure_does_not_escape_or_retry(
    local_http: Any, caplog: pytest.LogCaptureFixture
) -> None:
    attempts: list[int] = []

    def fail(*_args: Any, **_kwargs: Any) -> None:
        attempts.append(1)
        raise RuntimeError(SENTINEL)

    local_http.pool.request = fail
    assert heartbeat_health.capture_production_worker_heartbeat() is None
    flush()
    assert attempts == [1]
    assert SENTINEL not in caplog.text and DSN not in caplog.text


def test_fork_recreates_client_without_touching_parent(
    local_http: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent_calls: list[str] = []
    monkeypatch.setattr(
        heartbeat_health,
        "_client",
        SimpleNamespace(
            close=lambda **_kwargs: parent_calls.append("close"),
            transport=SimpleNamespace(
                capture_envelope=lambda _envelope: parent_calls.append("capture")
            ),
        ),
    )
    monkeypatch.setattr(heartbeat_health, "_client_pid", -1)
    previous_lock = heartbeat_health._client_lock
    heartbeat_health.capture_production_worker_heartbeat()
    flush()
    assert parent_calls == []
    assert heartbeat_health._client_lock is not previous_lock
    assert len(local_http.calls) == 1


def test_infrastructure_switch_is_default_off_and_worker_only() -> None:
    root = Path(__file__).resolve().parents[2]
    variables = (root / "infra/variables.tf").read_text()
    main = (root / "infra/main.tf").read_text()
    collision_guard = (root / "infra/session_prep_production_heartbeat.tf").read_text()
    marker = 'variable "enable_session_prep_production_worker_heartbeat"'
    block = variables[variables.index(marker) :]
    next_variable = block.find('\nvariable "', len(marker))
    block = block if next_variable == -1 else block[:next_variable]
    assert "default     = false" in block
    assert main.count('name  = "SESSION_PREP_PRODUCTION_HEARTBEAT_ENABLED"') == 1
    worker = main[main.index('resource "azurerm_container_app" "worker"') :]
    assert 'name  = "SESSION_PREP_PRODUCTION_HEARTBEAT_ENABLED"' in worker
    scheduler = main[
        main.index('resource "azurerm_container_app" "scheduler"') : main.index(
            'resource "azurerm_container_app" "worker"'
        )
    ]
    api = main[: main.index('resource "azurerm_container_app" "scheduler"')]
    assert "SESSION_PREP_PRODUCTION_HEARTBEAT_ENABLED" not in scheduler
    assert "SESSION_PREP_PRODUCTION_HEARTBEAT_ENABLED" not in api
    assert "var.python_api_only_secret_vars" in collision_guard
    assert "var.python_billing_api_only_secret_vars" in collision_guard
    assert "session_prep_production_heartbeat_env_name_collisions" in collision_guard
    assert (
        "length(local.session_prep_production_heartbeat_env_name_collisions) == 0"
        in main
    )
    assert (
        "!var.enable_session_prep_production_worker_heartbeat || var.session_prep_production_rollout.enabled"
        in main
    )
