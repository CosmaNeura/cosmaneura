"""Dedicated actual SDK envelopes with in-memory HTTP; no DB/vendor traffic."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
import sentry_sdk
from sentry_sdk.envelope import Envelope, Item, PayloadRef

from app.configuration import session_prep_health_alerts as health
from app.services import session_prep_alerts as alerts

SENTINEL = "SYNTHETIC-PRIVATE-CHART-AND-CREDENTIAL-SENTINEL"
DSN = "https://synthetic@example.invalid/1"


def event(category="SessionPrepMaintenanceUnavailable") -> Any:
    return {
        "event_id": uuid4().hex, "platform": "python", "level": "error",
        "exception": {"values": [{"type": category, "module": "app.services.session_prep_alerts"}]},
    }


def envelope(value: Any = None):
    value = value or event()
    result = Envelope(headers={"event_id": value["event_id"], "sent_at": "2026-10-21T10:00:00Z"})
    result.add_event(value)
    return result


def global_clients():
    return tuple(scope.client for scope in (
        sentry_sdk.get_global_scope(), sentry_sdk.get_current_scope(), sentry_sdk.get_isolation_scope()
    ))


@pytest.fixture
def local_http(monkeypatch):
    health.close_health_client()
    requests = []
    closed = []
    pool = SimpleNamespace(
        request=lambda method, url, **kwargs: requests.append((method, url, kwargs))
        or SimpleNamespace(status=200, headers={}, close=lambda: closed.append(True)),
    )

    class LocalHealthTransport(health._HealthTransport):
        def _make_pool(self) -> Any:
            return pool

    monkeypatch.setattr(health, "_HealthTransport", LocalHealthTransport)
    monkeypatch.setattr(health, "is_staging", lambda: True)
    monkeypatch.setattr(health, "config", lambda name, **_kwargs: DSN if name == "SENTRY_DSN" else "true")
    monkeypatch.setattr(alerts, "sentry_enabled", lambda: False)
    yield SimpleNamespace(requests=requests, closed=closed, pool=pool)
    health.close_health_client()


def flush():
    health._client.flush(timeout=1)


def wire_event(request):
    method, url, kwargs = request
    assert method == "POST" and url == "https://example.invalid/api/1/envelope/"
    assert kwargs["redirect"] is False and kwargs["retries"] is False
    assert set(kwargs["headers"]) == {"Content-Type", "User-Agent", "X-Sentry-Auth"}
    lines = kwargs["body"].splitlines()
    assert len(lines) == 3
    headers, item, value = map(json.loads, lines)
    assert set(headers) == {"event_id", "sent_at"}
    assert set(item) <= {"type", "content_type", "length"} and item["type"] == "event"
    assert headers["event_id"] == value["event_id"]
    assert health._canonical_event(value)
    return value


def test_real_SDK_sends_only_three_types_without_binding_global_client(local_http):
    before = global_clients()
    alerts.report_degraded_health({"oldest_pending_seconds": 121, "actionable_failed": 1})
    alerts.report_maintenance_unavailable()
    flush()
    assert global_clients() == before
    assert len(local_http.requests) == len(local_http.closed) == 3
    values = [wire_event(request) for request in local_http.requests]
    assert {value["exception"]["values"][0]["type"] for value in values} == health._CATEGORIES
    assert len({value["event_id"] for value in values}) == 3
    client = health._client
    assert client.integrations == {}
    assert client.metrics_aggregator is client.log_batcher is client.monitor is None
    for setting in ("default_integrations", "auto_enabling_integrations", "enable_tracing",
                    "auto_session_tracking", "send_client_reports", "enable_backpressure_handling", "spotlight"):
        assert client.options[setting] is False
    assert client.options["traces_sample_rate"] is None
    assert client.options["profiles_sample_rate"] == client.options["profile_session_sample_rate"] == 0


def test_hostile_ambient_scope_and_attachments_cannot_reach_health_client(local_http):
    processors = []
    with sentry_sdk.isolation_scope() as scope:
        scope.set_user({"id": SENTINEL})
        scope.set_tag("clinical", SENTINEL)
        scope.set_context("clinical", {"note": SENTINEL})
        scope.add_attachment(bytes=SENTINEL.encode(), filename="synthetic.txt")
        scope.add_event_processor(lambda value, _hint: processors.append(1) or {**value, "message": SENTINEL})
        alerts.report_maintenance_unavailable()
    flush()
    assert processors == [] and len(local_http.requests) == 1
    assert SENTINEL not in repr(wire_event(local_http.requests[0]))


@pytest.mark.parametrize("value", [None, "false", "TRUE", "True", "1", "", True, False])
def test_missing_or_nonexact_optin_never_reads_DSN_or_constructs_client(monkeypatch, value):
    names = []
    monkeypatch.setattr(health, "is_staging", lambda: True)
    monkeypatch.setattr(health, "config", lambda name, **_kwargs: names.append(name) or value)
    monkeypatch.setattr(health, "_make_client", lambda _dsn: pytest.fail("disabled opt-in constructed a client"))
    health.capture_health_category("SessionPrepMaintenanceUnavailable")
    assert names == ["SESSION_PREP_HEALTH_ALERTS_ENABLED"]


def test_new_setting_does_not_enable_health_transport_in_production(monkeypatch):
    monkeypatch.setattr(health, "is_staging", lambda: False)
    monkeypatch.setattr(health, "config", lambda *_a, **_k: pytest.fail("non-staging read a health setting or DSN"))
    health.capture_health_category("SessionPrepMaintenanceUnavailable")
    assert health.health_alerts_enabled() is False


@pytest.mark.parametrize("category", ["RuntimeError", "SessionPrepMaintenanceUnavailableExtra", "", None, True, []])
def test_unknown_categories_do_not_read_config_or_client(monkeypatch, category):
    monkeypatch.setattr(health, "health_alerts_enabled", lambda: pytest.fail("unknown category read config"))
    health.capture_health_category(category)


@pytest.mark.parametrize("field", ["request", "breadcrumbs", "transaction", "user", "extra", "contexts", "message", "unknown"])
def test_before_send_rejects_unknown_content_instead_of_scrubbing_it(field):
    value = event()
    value[field] = SENTINEL
    assert health._before_send(value, {}) is None


@pytest.mark.parametrize("hint", [{"attachments": [SENTINEL]}, {"exc_info": SENTINEL}, {"unknown": SENTINEL}, None])
def test_before_send_rejects_nonempty_or_malformed_hints(hint):
    assert health._before_send(event(), hint) is None


@pytest.mark.parametrize("field", ["release", "server_name", "environment", "timestamp", "sdk"])
def test_before_send_rejects_forged_late_metadata(field):
    value = event()
    value[field] = SENTINEL
    assert health._before_send(value, {}) is None


@pytest.mark.parametrize("kind", ["transaction", "check_in", "profile", "log", "client_report"])
def test_actual_SDK_drops_nonerror_items_and_extra_content(local_http, kind):
    health.capture_health_category("SessionPrepMaintenanceUnavailable")
    flush()
    client = health._client
    value = event()
    value["type"] = kind
    client.capture_event(value, hint={}, scope=None)
    forged = event()
    forged["request"] = {"data": SENTINEL}
    client.capture_event(forged, hint={}, scope=None)
    client.capture_event(event(), hint={"attachments": [SENTINEL]}, scope=None)
    flush()
    assert len(local_http.requests) == 1


@pytest.mark.parametrize("field,value", [
    ("event_id", True), ("event_id", "f" * 31), ("level", "warning"), ("platform", "other"),
    ("exception", {"values": [{"type": "RuntimeError", "module": "app.services.session_prep_alerts"}]}),
    ("exception", {"values": [{"type": "SessionPrepMaintenanceUnavailable", "module": "app.jobs.scribe"}]}),
    ("exception", {"values": [{"type": "SessionPrepMaintenanceUnavailable", "module": "app.services.session_prep_alerts", "value": SENTINEL}]}),
])
def test_final_payload_gate_rejects_noncanonical_values(field, value):
    payload = event()
    payload[field] = value
    assert health._closed_envelope(envelope(payload)) is False


@pytest.mark.parametrize("field,value", [("trace", {"clinical": SENTINEL}), ("dsn", DSN), ("sdk", SENTINEL),
                                         ("sent_at", "2026-10-21T10:00:00"), ("sent_at", "2026-10-21T10:00:00+01:00"),
                                         ("event_id", "f" * 32)])
def test_final_header_gate_rejects_extra_or_forged_metadata(field, value):
    value_envelope = envelope()
    value_envelope.headers[field] = value
    assert health._closed_envelope(value_envelope) is False


@pytest.mark.parametrize("kind", ["attachment", "transaction", "client_report", "session", "profile", "statsd", "log"])
def test_final_transport_gate_drops_unknown_items_before_queue_or_HTTP(local_http, kind):
    health.capture_health_category("SessionPrepMaintenanceUnavailable")
    flush()
    transport = health._client.transport
    bad = envelope()
    bad.items.append(Item(payload=PayloadRef(bytes=SENTINEL.encode()), type=kind))
    transport.capture_envelope(bad)
    transport._send_envelope(bad)
    flush()
    assert len(local_http.requests) == 1


def test_final_transport_gate_rejects_duplicate_JSON_extra_item_headers_and_oversize():
    bad = envelope()
    bad.items[0].headers["filename"] = SENTINEL
    assert health._closed_envelope(bad) is False
    bad = envelope()
    raw = json.dumps(event()).replace('"platform": "python"', '"platform": "python", "platform": "python"')
    bad.items = [Item(payload=PayloadRef(bytes=raw.encode()), type="event")]
    assert health._closed_envelope(bad) is False
    bad.items = [Item(payload=PayloadRef(bytes=b" " * 1025), type="event")]
    assert health._closed_envelope(bad) is False


def test_network_failure_has_one_attempt_no_raw_error_log(local_http, caplog):
    calls = []

    def fail(*args, **kwargs):
        calls.append(1)
        raise RuntimeError(SENTINEL)

    local_http.pool.request = fail
    alerts.report_maintenance_unavailable()
    flush()
    assert calls == [1]
    assert SENTINEL not in caplog.text and DSN not in caplog.text


def test_private_transport_uses_existing_TLS_policy_with_explicit_timeouts(local_http):
    health.capture_health_category("SessionPrepMaintenanceUnavailable")
    flush()
    options = health._client.transport._get_pool_options()
    assert options["cert_reqs"] == "CERT_REQUIRED" and options["retries"] is False
    assert options["timeout"].total == 5
    assert options["timeout"].connect_timeout == 2 and options["timeout"].read_timeout == 3


@pytest.mark.parametrize("dsn", ["", SENTINEL, "http://synthetic@example.invalid/1",
                                  "https://example.invalid/1", "https://synthetic@example.invalid/not-a-project",
                                  "https://synthetic@/1"])
def test_invalid_and_HTTP_DSNs_never_reach_SDK_constructor_or_logs(monkeypatch, caplog, dsn):
    monkeypatch.setattr(sentry_sdk, "Client", lambda **_kwargs: pytest.fail("invalid DSN reached SDK constructor"))
    with pytest.raises(ValueError, match="^Session Prep health DSN unavailable$"):
        health._make_client(dsn)
    assert SENTINEL not in caplog.text
    if dsn:
        assert dsn not in caplog.text


def test_actual_SDK_constructor_failure_with_hostile_debug_env_is_not_logged(monkeypatch, caplog):
    health.close_health_client()
    monkeypatch.setenv("SENTRY_DEBUG", "true")
    monkeypatch.setenv("SENTRY_RELEASE", SENTINEL)
    monkeypatch.setenv("SENTRY_ENVIRONMENT", SENTINEL)
    monkeypatch.setenv("SENTRY_SPOTLIGHT", SENTINEL)
    monkeypatch.setattr(health, "health_alerts_enabled", lambda: True)
    monkeypatch.setattr(health, "config", lambda *_args, **_kwargs: DSN)

    class FailingConstructor(health._HealthTransport):
        def __init__(self, _options):
            raise RuntimeError(SENTINEL)

    monkeypatch.setattr(health, "_HealthTransport", FailingConstructor)
    caplog.set_level("DEBUG", logger="sentry_sdk.errors")
    health.capture_health_category("SessionPrepMaintenanceUnavailable")
    assert health._client is None
    assert SENTINEL not in caplog.text and DSN not in caplog.text


def test_actual_valid_constructor_ignores_hostile_SDK_metadata_and_debug_environment(local_http, monkeypatch, caplog):
    for name in ("SENTRY_RELEASE", "SENTRY_ENVIRONMENT", "SENTRY_SPOTLIGHT"):
        monkeypatch.setenv(name, SENTINEL)
    monkeypatch.setenv("SENTRY_DEBUG", "true")
    caplog.set_level("DEBUG", logger="sentry_sdk.errors")
    alerts.report_degraded_health({"oldest_pending_seconds": 121, "actionable_failed": 1})
    alerts.report_maintenance_unavailable()
    flush()
    assert len(local_http.requests) == 3
    assert all(SENTINEL not in repr(wire_event(request)) for request in local_http.requests)
    assert SENTINEL not in caplog.text and DSN not in caplog.text


@pytest.mark.parametrize("status", [301, 401, 429, 500])
def test_HTTP_error_body_is_closed_without_logging_or_retry(local_http, caplog, status):
    closed = []
    local_http.pool.request = lambda *args, **kwargs: local_http.requests.append((args, kwargs)) or SimpleNamespace(
        status=status, headers={}, data=SENTINEL.encode(), close=lambda: closed.append(True)
    )
    alerts.report_maintenance_unavailable()
    flush()
    assert len(local_http.requests) == 1 and closed == [True]
    assert SENTINEL not in caplog.text and DSN not in caplog.text


def test_production_keeps_existing_global_alert_path_with_new_optin_present(monkeypatch):
    global_events = []
    monkeypatch.setattr(health, "is_staging", lambda: False)
    monkeypatch.setattr(health, "config", lambda *_args, **_kwargs: "true")
    monkeypatch.setattr(alerts, "sentry_enabled", lambda: True)
    monkeypatch.setattr(health, "_make_client", lambda *_args, **_kwargs: pytest.fail("production constructed staging health client"))
    with sentry_sdk.init(
        dsn=DSN, transport=lambda value: global_events.append(value), default_integrations=False,
        auto_enabling_integrations=False, before_send=health.scrub_event,
    ):
        alerts.report_maintenance_unavailable()
    assert len(global_events) == 1
    assert global_events[0]["exception"]["values"][0]["type"] == "SessionPrepMaintenanceUnavailable"


def test_enabled_global_path_has_priority_without_health_SDK_or_setting_read(monkeypatch):
    global_events = []
    monkeypatch.setattr(alerts, "sentry_enabled", lambda: True)
    monkeypatch.setattr(alerts, "health_alerts_enabled", lambda: pytest.fail("global-positive path read new setting"))
    with sentry_sdk.init(
        dsn=DSN, transport=lambda value: global_events.append(value), default_integrations=False,
        auto_enabling_integrations=False, before_send=health.scrub_event,
    ):
        alerts.report_maintenance_unavailable()
    assert len(global_events) == 1


def test_private_helper_does_not_construct_client_if_global_SDK_is_enabled(monkeypatch):
    monkeypatch.setattr(health, "health_alerts_enabled", lambda: True)
    monkeypatch.setattr(health, "sentry_enabled", lambda: True)
    monkeypatch.setattr(health, "config", lambda *_args, **_kwargs: pytest.fail("enabled global read health DSN"))
    monkeypatch.setattr(health, "_make_client", lambda *_args: pytest.fail("enabled global created health SDK"))
    health.capture_health_category("SessionPrepMaintenanceUnavailable")


@pytest.mark.parametrize("phase", ["config", "construct", "capture", "identifier"])
def test_private_capture_failure_safe_without_logging(monkeypatch, caplog, phase):
    health.close_health_client()

    def fail(*_args, **_kwargs):
        raise RuntimeError(SENTINEL)

    monkeypatch.setattr(health, "health_alerts_enabled", lambda: True)
    monkeypatch.setattr(health, "config", fail if phase == "config" else lambda *_a, **_k: DSN)
    monkeypatch.setattr(health, "_make_client", fail if phase == "construct" else lambda _dsn: SimpleNamespace(capture_event=fail, close=lambda **_k: None))
    if phase == "identifier":
        monkeypatch.setattr(health, "uuid4", fail)
    health.capture_health_category("SessionPrepMaintenanceUnavailable")
    health.close_health_client()
    assert SENTINEL not in caplog.text and DSN not in caplog.text


def test_fork_reset_does_not_flush_or_reuse_parent_client(monkeypatch):
    calls = []
    old = SimpleNamespace(close=lambda **_k: calls.append("old-close"))
    monkeypatch.setattr(health, "_client", old)
    monkeypatch.setattr(health, "_client_pid", -1)
    previous_lock = health._client_lock
    health.close_health_client()
    assert calls == [] and health._client is None and health._client_lock is not previous_lock


def test_shutdown_closes_same_process_client_once_with_bounded_wait(monkeypatch):
    calls = []
    monkeypatch.setattr(health, "_client", SimpleNamespace(close=lambda **kwargs: calls.append(kwargs)))
    health.close_health_client()
    health.close_health_client()
    assert calls == [{"timeout": 1}]
