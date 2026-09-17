"""The probe must never report a delivery it did not make.

This is the one command whose entire purpose is proving the last hop works, so
it is also the one command where a reassuring message over a no-op would be
worst: it would certify the alerting as working while nothing was ever sent.
"""

import pytest
import typer

from app.cli import verify_alert_delivery
from app.services import alerting


class _InactiveClient:
    def is_active(self):
        return False


class _ActiveClient:
    def is_active(self):
        return True


class _FakeSentry:
    def __init__(self, client):
        self._client = client
        self.flushed: list[float | None] = []

    def get_client(self):
        return self._client

    def flush(self, timeout: float | None = None):
        self.flushed.append(timeout)


def test_refuses_when_sentry_is_not_initialised(monkeypatch, captured_alerts):
    """Sentry only initialises in production. Everywhere else this has to say
    nothing was sent, not print success over a no-op."""
    import sentry_sdk

    monkeypatch.setattr(sentry_sdk, "get_client", lambda: _InactiveClient())

    with pytest.raises(typer.Exit) as exit_info:
        verify_alert_delivery()

    assert exit_info.value.exit_code == 1
    assert captured_alerts == [], "nothing may be sent when Sentry is inactive"


def test_fires_and_flushes_when_sentry_is_live(monkeypatch, captured_alerts):
    """The SDK's atexit flush allows two seconds. A short-lived CLI that exits
    before its own event is transmitted would report delivery while proving
    none, which is exactly the failure this command exists to detect."""
    import app.cli as cli_module

    fake = _FakeSentry(_ActiveClient())
    monkeypatch.setattr(cli_module, "sentry_sdk", fake, raising=False)
    monkeypatch.setitem(__import__("sys").modules, "sentry_sdk", fake)

    verify_alert_delivery(flush_seconds=9.0)

    assert [c["type"] for c in captured_alerts] == [alerting.AlertDeliveryProbe]
    assert fake.flushed == [9.0], "the probe must flush before the process exits"
