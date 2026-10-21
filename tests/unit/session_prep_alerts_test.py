"""Actual local SDK capture and the unchanged PHI scrubber; no network or DB."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest
import sentry_sdk
from sentry_sdk.transport import Transport

import app.jobs.session_prep as job
from app.configuration import sentry
from app.services import session_prep_alerts as alerts

SENTINEL = "synthetic-private-source-and-credential-sentinel"


@pytest.fixture
def local_events(monkeypatch):
    events = []
    monkeypatch.setattr(alerts, "sentry_enabled", lambda: True)
    with sentry_sdk.init(
        dsn="https://public@example.invalid/1",
        transport=lambda event: events.append(event),
        default_integrations=False,
        auto_enabling_integrations=False,
        traces_sample_rate=0,
        before_send=sentry.scrub_event,
        include_local_variables=False,
        send_default_pii=False,
    ):
        yield events


def _types(events):
    return [event["exception"]["values"][0]["type"] for event in events]


def _health(**changes):
    return {
        "status": "degraded",
        "oldest_pending_seconds": 0,
        "actionable_failed": 0,
        **changes,
    }


@pytest.mark.parametrize(
    "health,expected",
    [
        (_health(oldest_pending_seconds=121), ["SessionPrepQueueStalled"]),
        (_health(actionable_failed=1), ["SessionPrepRecentBuildFailures"]),
        (
            _health(oldest_pending_seconds=121, actionable_failed=1),
            ["SessionPrepQueueStalled", "SessionPrepRecentBuildFailures"],
        ),
        (_health(oldest_pending_seconds=120), []),
        (_health(), []),
        (_health(oldest_pending_seconds=-1, actionable_failed=-1), []),
        (_health(oldest_pending_seconds=True, actionable_failed=True), []),
        (_health(oldest_pending_seconds="121", actionable_failed="1"), []),
        (_health(oldest_pending_seconds=121.0, actionable_failed=1.0), []),
        (_health(oldest_pending_seconds=None, actionable_failed=None), []),
    ],
)
def test_only_exact_existing_health_thresholds_emit_fixed_types(
    local_events, health, expected
):
    alerts.report_degraded_health(health)
    assert _types(local_events) == expected


def test_actual_capture_excludes_inherited_context_and_raw_exception(local_events):
    def hostile_context(event: Any, _hint: Any) -> Any:
        return {
            **event,
            "event_id": "f" * 32,
            "message": SENTINEL,
            "request": {"data": SENTINEL, "method": SENTINEL},
            "extra": {"source": SENTINEL},
            "fingerprint": [SENTINEL],
            "breadcrumbs": {"values": [{"category": SENTINEL, "message": SENTINEL}]},
            "exception": {"values": [{"type": SENTINEL, "value": SENTINEL}]},
            "transaction": SENTINEL,
        }

    with sentry_sdk.isolation_scope() as scope:
        scope.set_user({"id": SENTINEL})
        scope.set_tag("source", SENTINEL)
        scope.set_context("clinical", {"source": SENTINEL})
        scope.add_event_processor(hostile_context)
        try:
            raise RuntimeError(SENTINEL)
        except RuntimeError:
            alerts.report_maintenance_unavailable()
    assert _types(local_events) == ["SessionPrepMaintenanceUnavailable"]
    event = local_events[0]
    assert event["exception"]["values"] == [
        {
            "type": "SessionPrepMaintenanceUnavailable",
            "module": "app.services.session_prep_alerts",
        }
    ]
    assert SENTINEL not in repr(event)
    assert event["event_id"] != "f" * 32
    assert not {"request", "breadcrumbs", "transaction", "message"} & event.keys()
    assert not {"user", "extra", "tags", "contexts", "fingerprint"} & event.keys()


def test_actual_sdk_envelope_cannot_include_inherited_attachments(monkeypatch):
    envelopes = []

    class LocalTransport(Transport):
        def capture_envelope(self, envelope):
            envelopes.append(envelope)

    monkeypatch.setattr(alerts, "sentry_enabled", lambda: True)
    with (
        sentry_sdk.init(
            dsn="https://public@example.invalid/1",
            transport=LocalTransport(),
            default_integrations=False,
            auto_enabling_integrations=False,
            before_send=sentry.scrub_event,
        ),
        sentry_sdk.isolation_scope() as scope,
    ):
        scope.add_attachment(bytes=SENTINEL.encode(), filename=f"{SENTINEL}.txt")
        alerts.report_degraded_health(_health(oldest_pending_seconds=121))
    assert len(envelopes) == 1
    envelope = envelopes[0]
    assert [item.headers["type"] for item in envelope.items] == ["event"]
    assert SENTINEL.encode() not in envelope.items[0].payload.get_bytes()


def test_fresh_exception_has_no_caught_error_chain(monkeypatch):
    captured = []
    monkeypatch.setattr(alerts, "sentry_enabled", lambda: True)
    monkeypatch.setattr(
        sentry_sdk, "capture_exception", lambda error: captured.append(error)
    )
    try:
        raise RuntimeError(SENTINEL)
    except RuntimeError:
        alerts.report_maintenance_unavailable()
    assert len(captured) == 1
    error = captured[0]
    assert type(error) is alerts.SessionPrepMaintenanceUnavailable
    assert error.args == ()
    assert error.__cause__ is None
    assert error.__context__ is None
    assert error.__traceback__ is None


def test_unapproved_types_and_subclasses_are_never_constructed(local_events):
    class HostileAlert(alerts.SessionPrepMaintenanceUnavailable):
        def __init__(self):
            pytest.fail("An unapproved exception type was instantiated")

    alerts._capture(RuntimeError)
    alerts._capture(HostileAlert)
    assert local_events == []


def test_existing_sentry_disable_gate_blocks_capture(local_events, monkeypatch):
    monkeypatch.setattr(alerts, "sentry_enabled", lambda: False)
    alerts.report_maintenance_unavailable()
    alerts.report_degraded_health(
        _health(oldest_pending_seconds=121, actionable_failed=1)
    )
    assert local_events == []


def _maintenance(monkeypatch, health, *, enabled=True):
    cleanup = []
    logs = []
    monkeypatch.setattr(
        job.configuration,
        "SESSION_PREP_PROVIDER_SETTINGS",
        replace(job.configuration.SESSION_PREP_PROVIDER_SETTINGS, enabled=enabled),
    )
    monkeypatch.setattr(job, "expire_read_receipts", lambda: cleanup.append(1) or 1)
    monkeypatch.setattr(job, "runtime_health", lambda: health)
    monkeypatch.setattr(
        job.log, "warning", lambda *args, **kwargs: logs.append((args, kwargs))
    )
    monkeypatch.setattr(
        job.log, "info", lambda *args, **kwargs: logs.append((args, kwargs))
    )
    return cleanup, logs


@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize("status", ["ok", "degraded"])
def test_maintenance_health_alerts_require_enabled_degraded_state(
    local_events, monkeypatch, enabled, status
):
    cleanup, _ = _maintenance(
        monkeypatch,
        _health(status=status, oldest_pending_seconds=121, actionable_failed=1),
        enabled=enabled,
    )
    assert job.maintain() is None
    assert cleanup == [1]
    assert _types(local_events) == (
        ["SessionPrepQueueStalled", "SessionPrepRecentBuildFailures"]
        if enabled and status == "degraded"
        else []
    )


@pytest.mark.parametrize("failure", ["cleanup", "health"])
@pytest.mark.parametrize("enabled", [True, False])
def test_maintenance_outage_preserves_safe_log_and_fixed_alert(
    local_events, monkeypatch, failure, enabled
):
    _, logs = _maintenance(monkeypatch, _health(), enabled=enabled)

    def fail():
        raise RuntimeError(SENTINEL)

    monkeypatch.setattr(
        job, "expire_read_receipts" if failure == "cleanup" else "runtime_health", fail
    )
    assert job.maintain() is None
    assert _types(local_events) == ["SessionPrepMaintenanceUnavailable"]
    assert logs == [
        (
            ("Session Prep maintenance failed",),
            {"event_code": "session_prep_maintenance_failed"},
        )
    ]
    assert SENTINEL not in repr(local_events) + repr(logs)


@pytest.mark.parametrize("failure", ["config", "identifier", "scope", "capture"])
def test_monitoring_failure_cannot_interrupt_successful_maintenance(
    local_events, monkeypatch, failure
):
    cleanup, logs = _maintenance(
        monkeypatch, _health(oldest_pending_seconds=121, actionable_failed=1)
    )

    def fail(*_args, **_kwargs):
        raise RuntimeError(SENTINEL)

    target, name = {
        "config": (alerts, "sentry_enabled"),
        "identifier": (alerts, "uuid4"),
        "scope": (sentry_sdk, "new_scope"),
        "capture": (sentry_sdk, "capture_exception"),
    }[failure]
    monkeypatch.setattr(target, name, fail)
    assert job.maintain() is None
    assert cleanup == [1]
    assert local_events == []
    assert SENTINEL not in repr(logs)
