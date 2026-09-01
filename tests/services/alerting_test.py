"""The contract that makes every other alert in this codebase work.

Two facts drive the design here, and both are easy to undo by accident:

1. The application logger is structlog with a PrintLoggerFactory, so it never
   creates a stdlib LogRecord and Sentry's LoggingIntegration never sees it.
   No `log.error` here has ever produced an alert.
2. `configuration.sentry.scrub_event` strips message, logentry, tags, contexts,
   extra and fingerprint from every outbound event, and blanks exception value
   strings. That is deliberate -- Sentry is not a channel that carries
   application text.

Together they mean the only thing that can distinguish one alert from another
in Sentry is the exception *type*. These tests pin both facts, so if either
changes the reasoning behind this module is revisited on purpose.
"""

import logging

import pytest

from app import log
from app.configuration.sentry import scrub_event
from app.services import alerting


class _FakeSentry:
    def __init__(self):
        self.captured: list[Exception] = []

    def capture_exception(self, exc):
        self.captured.append(exc)


@pytest.fixture
def fake_sentry(monkeypatch):
    fake = _FakeSentry()
    monkeypatch.setattr(alerting, "sentry_sdk", fake)
    return fake


def test_alert_captures_a_sentry_event(fake_sentry):
    "The whole point. A log line alone reaches no on-call."
    alerting.alert(alerting.ZoomOAuthRevoked, meeting_ref="hmac:abc")

    assert len(fake_sentry.captured) == 1
    assert isinstance(fake_sentry.captured[0], alerting.ZoomOAuthRevoked)


def test_each_failure_mode_is_its_own_exception_type(fake_sentry):
    """Type is the only thing that survives scrubbing, so it is the only way
    two failure modes can land as two Sentry issues rather than one."""
    alerting.alert(alerting.ZoomOAuthRevoked)
    alerting.alert(alerting.BotNeverReady)

    assert [type(e) for e in fake_sentry.captured] == [
        alerting.ZoomOAuthRevoked,
        alerting.BotNeverReady,
    ]


def test_the_sentry_event_survives_the_phi_scrubber():
    """The regression that motivated the whole design.

    An earlier version used capture_message with tags, contexts and a
    fingerprint. scrub_event removes every one of those, so the event arrived
    carrying nothing at all and all alerts collapsed into one issue. An
    exception event keeps its type through the same scrubber.
    """
    event = {
        "level": "error",
        "exception": {
            "values": [
                {
                    "type": "ZoomOAuthRevoked",
                    "value": "should not survive",
                    "stacktrace": {"frames": [{"vars": {"secret": "x"}}]},
                }
            ]
        },
    }

    scrubbed = scrub_event(event)
    value = scrubbed["exception"]["values"][0]

    assert value["type"] == "ZoomOAuthRevoked", "the alert would be unidentifiable"
    assert "value" not in value, "the scrubber must still strip free text"
    assert "vars" not in value["stacktrace"]["frames"][0]


def test_capture_message_would_not_survive_the_scrubber():
    "Pins why this module does not use capture_message."
    event = {
        "level": "error",
        "message": "zoom bot dispatch failed",
        "logentry": {"message": "zoom bot dispatch failed"},
        "tags": {"alert_reason": "zoom_oauth_revoked"},
        "contexts": {"alert": {"meeting_id": "999"}},
        "fingerprint": ["a", "b"],
    }

    assert sorted(scrub_event(event)) == ["level"]


def test_alert_writes_the_event_code_to_the_log(fake_sentry, capsys):
    """Sentry carries the type; the structured log carries the detail. They
    join on event_code, so it has to be the same token."""
    alerting.alert(alerting.BotNeverReady, meeting_ref="hmac:abc", patient_count=2)

    out = capsys.readouterr().out
    assert "BotNeverReady" in out
    assert "hmac:abc" in out


def test_alert_types_are_all_dispatch_alerts():
    """A stray Exception subclass would still capture, but would sit outside
    the DispatchAlert family that makes these greppable as one class."""
    exported = [
        value
        for name, value in vars(alerting).items()
        if isinstance(value, type)
        and issubclass(value, Exception)
        and value is not alerting.DispatchAlert
        and not name.startswith("_")
    ]

    assert exported, "no alert types exported"
    assert all(issubclass(t, alerting.DispatchAlert) for t in exported)


def test_ref_does_not_leak_the_identifier():
    "Identifiers reach logs as non-reversible tokens, like the adapters do."
    token = alerting.ref("meeting-99911112222")

    assert "99911112222" not in token
    assert token.startswith("hmac:")
    assert alerting.ref("meeting-99911112222") == token, "must be stable to correlate"
    assert alerting.ref(None) == "absent"


def test_app_logger_never_reaches_stdlib_logging():
    """The reason services.alerting exists at all.

    If this starts failing, structlog was reconfigured onto a stdlib logger
    factory and `log.error` would reach Sentry on its own. Good news, but it
    changes the rationale here and should be a deliberate decision.
    """
    records: list[logging.LogRecord] = []

    class _Collector(logging.Handler):
        def emit(self, record):
            records.append(record)

    collector = _Collector()
    root = logging.getLogger()
    root.addHandler(collector)
    try:
        log.error("this must not become a stdlib log record", event_code="probe")
    finally:
        root.removeHandler(collector)

    assert records == []
