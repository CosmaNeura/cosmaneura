"""Fixed, PHI-free production worker heartbeat with an independent client.

The client is dark unless both the production environment and the exact
production heartbeat opt-in are present.  It never binds or mutates Sentry's
global client and accepts only one closed check-in shape.
"""

from __future__ import annotations

import atexit
import json
import os
import re
from copy import deepcopy
from datetime import UTC, datetime
from threading import Lock
from typing import Any
from uuid import uuid4

import sentry_sdk
import urllib3
from celery.signals import worker_process_shutdown
from decouple import config
from sentry_sdk.envelope import Envelope
from sentry_sdk.transport import HttpTransport
from sentry_sdk.utils import Dsn

from app.environments import is_production

_IDENTITY = "session-prep-production-health"
_HEARTBEAT_SLUG = "session-prep-production-worker"
_HEARTBEAT_FIELDS = frozenset(
    {
        "type",
        "event_id",
        "check_in_id",
        "monitor_slug",
        "status",
        "duration",
        "environment",
        "release",
        "monitor_config",
    }
)
_HEARTBEAT_CONFIG = {
    "schedule": {"type": "interval", "value": 1, "unit": "minute"},
    "timezone": "UTC",
    "checkin_margin": 2,
    "max_runtime": 1,
    "failure_issue_threshold": 1,
    "recovery_threshold": 1,
}
_client: Any = None
_client_pid = os.getpid()
_client_lock = Lock()


def production_heartbeat_enabled() -> bool:
    """Require the production product gate plus an independent exact opt-in."""

    return (
        is_production()
        and config("SESSION_PREP_PROVIDER_ENABLED", default="false", cast=str) == "true"
        and config(
            "SESSION_PREP_PRODUCTION_HEARTBEAT_ENABLED",
            default="false",
            cast=str,
        )
        == "true"
    )


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_production_heartbeat_key")
        result[key] = value
    return result


def _canonical_heartbeat(value: Any) -> bool:
    """Reject every field other than fixed transport status/configuration."""

    if type(value) is not dict or set(value) != _HEARTBEAT_FIELDS:
        return False
    config_value = value["monitor_config"]
    if type(config_value) is not dict or config_value != _HEARTBEAT_CONFIG:
        return False
    schedule = config_value.get("schedule")
    if (
        type(schedule) is not dict
        or type(schedule.get("value")) is not int
        or any(
            type(config_value.get(field)) is not int
            for field in (
                "checkin_margin",
                "max_runtime",
                "failure_issue_threshold",
                "recovery_threshold",
            )
        )
    ):
        return False
    return (
        type(value["event_id"]) is str
        and re.fullmatch(r"[0-9a-f]{32}", value["event_id"]) is not None
        and type(value["check_in_id"]) is str
        and value["check_in_id"] == value["event_id"]
        and value["type"] == "check_in"
        and value["monitor_slug"] == _HEARTBEAT_SLUG
        and value["status"] == "ok"
        and type(value["duration"]) is float
        and value["duration"] == 0.0
        and value["environment"] == "production"
        and value["release"] == _IDENTITY
    )


def _closed_envelope(envelope: Any) -> bool:
    """Accept exactly one small check-in and no event, trace, log, or report."""

    try:
        if set(envelope.headers) != {"event_id", "sent_at"} or len(envelope.items) != 1:
            return False
        sent_at = envelope.headers["sent_at"]
        if type(sent_at) is not str or datetime.fromisoformat(
            sent_at
        ).utcoffset() != UTC.utcoffset(None):
            return False
        item = envelope.items[0]
        if (
            set(item.headers) - {"type", "content_type"}
            or item.headers.get("type") != "check_in"
            or item.headers.get("content_type") not in (None, "application/json")
        ):
            return False
        raw = item.payload.get_bytes()
        if type(raw) is not bytes or not 0 < len(raw) <= 1024:
            return False
        value = json.loads(raw, object_pairs_hook=_unique)
        return (
            _canonical_heartbeat(value)
            and envelope.headers["event_id"] == value["event_id"]
        )
    except Exception:
        return False


class _ProductionHeartbeatTransport(HttpTransport):
    """Bounded transport that revalidates the closed envelope before HTTP."""

    def _get_pool_options(self):
        options = super()._get_pool_options()
        options["timeout"] = urllib3.Timeout(connect=2, read=3, total=5)
        options["retries"] = False
        return options

    def capture_envelope(self, envelope):
        if _closed_envelope(envelope):
            super().capture_envelope(envelope)

    def _send_envelope(self, envelope):
        if not _closed_envelope(envelope) or self._check_disabled("monitor"):
            return
        try:
            if self.parsed_dsn is None or self.parsed_dsn.scheme != "https":
                return
            encoding, body = self._serialize_envelope(envelope)
            headers = {
                "Content-Type": "application/x-sentry-envelope",
                "User-Agent": str(self._auth.client),
                "X-Sentry-Auth": str(self._auth.to_header()),
            }
            if encoding:
                headers["Content-Encoding"] = encoding
            response = self._pool.request(
                "POST",
                self._auth.get_api_url(),
                body=body.getvalue(),
                headers=headers,
                redirect=False,
                retries=False,
            )
            try:
                self._update_rate_limits(response)
            finally:
                response.close()
        except Exception:
            # Never log a response, URL, DSN, or transport exception here.
            return


def _make_client(dsn: str):
    try:
        if type(dsn) is not str or Dsn(dsn).scheme != "https":
            raise ValueError
    except Exception:
        raise ValueError("Production Session Prep heartbeat DSN unavailable") from None
    return sentry_sdk.Client(
        dsn=dsn,
        transport=_ProductionHeartbeatTransport,
        release=_IDENTITY,
        environment="production",
        server_name=_IDENTITY,
        project_root="/",
        integrations=[],
        default_integrations=False,
        auto_enabling_integrations=False,
        enable_tracing=False,
        traces_sample_rate=None,
        profiles_sample_rate=0,
        profile_session_sample_rate=0,
        auto_session_tracking=False,
        send_client_reports=False,
        enable_backpressure_handling=False,
        attach_stacktrace=False,
        include_local_variables=False,
        send_default_pii=False,
        spotlight=False,
        debug=False,
        transport_queue_size=4,
        shutdown_timeout=1,
        before_send=lambda _event, _hint: None,
        before_send_transaction=lambda _event, _hint: None,
        _experiments={
            "enable_metrics": False,
            "enable_logs": False,
            "continuous_profiling_auto_start": False,
            "transport_compression_level": 0,
        },
    )


def _after_fork() -> None:
    global _client, _client_pid, _client_lock
    _client = None
    _client_pid = os.getpid()
    _client_lock = Lock()


def _heartbeat_client() -> Any:
    global _client
    if os.getpid() != _client_pid:
        _after_fork()
    with _client_lock:
        if _client is None:
            _client = _make_client(config("SENTRY_DSN", default="", cast=str))
        return _client


def capture_production_worker_heartbeat() -> None:
    """Send one fixed success check-in from the ordinary worker task path."""

    try:
        if not production_heartbeat_enabled():
            return
        reference = uuid4().hex
        value = {
            "type": "check_in",
            "event_id": reference,
            "check_in_id": reference,
            "monitor_slug": _HEARTBEAT_SLUG,
            "status": "ok",
            "duration": 0.0,
            "environment": "production",
            "release": _IDENTITY,
            "monitor_config": deepcopy(_HEARTBEAT_CONFIG),
        }
        envelope = Envelope(
            headers={"event_id": reference, "sent_at": datetime.now(UTC).isoformat()}
        )
        envelope.add_checkin(value)
        if not _closed_envelope(envelope):
            return
        client = _heartbeat_client()
        if client.transport is not None:
            client.transport.capture_envelope(envelope)
    except Exception:
        # Monitoring must never fail or retry an otherwise successful task.
        return


def close_production_heartbeat_client(*_args: Any, **_kwargs: Any) -> None:
    global _client
    if os.getpid() != _client_pid:
        _after_fork()
        return
    with _client_lock:
        client, _client = _client, None
    if client is not None:
        try:
            client.close(timeout=1)
        except Exception:
            return


os.register_at_fork(after_in_child=_after_fork)
worker_process_shutdown.connect(close_production_heartbeat_client, weak=False)
atexit.register(close_production_heartbeat_client)
