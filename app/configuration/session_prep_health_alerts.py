"""Explicit staging-only, unbound SDK client for three preparation alerts.

This independent opt-in never initializes or binds the global Sentry client.
The existing global switch and production telemetry keep their behavior.
"""

from __future__ import annotations

import atexit
import json
import os
import re
from datetime import UTC, datetime
from threading import Lock
from typing import Any
from uuid import uuid4

import sentry_sdk
import urllib3
from celery.signals import worker_process_shutdown
from decouple import config
from sentry_sdk.consts import VERSION
from sentry_sdk.transport import HttpTransport
from sentry_sdk.utils import Dsn

from app.configuration.sentry import scrub_event, sentry_enabled
from app.environments import is_staging

_CATEGORIES = frozenset(
    {"SessionPrepQueueStalled", "SessionPrepRecentBuildFailures", "SessionPrepMaintenanceUnavailable"}
)
_MODULE = "app.services.session_prep_alerts"
_FIELDS = frozenset({"event_id", "platform", "level", "exception"})
_SDK_FIELDS = frozenset({"timestamp", "release", "environment", "server_name", "sdk"})
_IDENTITY = "session-prep-health"
_client: Any = None
_client_pid = os.getpid()
_client_lock = Lock()


def health_alerts_enabled() -> bool:
    """A missing, malformed or non-staging setting grants no new transport."""
    return is_staging() and config(
        "SESSION_PREP_HEALTH_ALERTS_ENABLED", default="false", cast=str
    ) == "true"


def _canonical_event(event: Any) -> bool:
    if type(event) is not dict or set(event) != _FIELDS:
        return False
    if (
        type(event["event_id"]) is not str
        or re.fullmatch(r"[0-9a-f]{32}", event["event_id"]) is None
        or event["platform"] != "python"
        or event["level"] != "error"
    ):
        return False
    exception = event["exception"]
    if type(exception) is not dict or set(exception) != {"values"}:
        return False
    values = exception["values"]
    return (
        type(values) is list
        and len(values) == 1
        and type(values[0]) is dict
        and set(values[0]) == {"type", "module"}
        and type(values[0]["type"]) is str
        and values[0]["type"] in _CATEGORIES
        and values[0]["module"] == _MODULE
    )


def _before_send(event: Any, hint: Any) -> Any:
    # Only ready-made events from capture_health_category are submitted. The
    # SDK adds known metadata before this hook; unknown content is rejected.
    if type(event) is not dict or type(hint) is not dict or hint:
        return None
    if set(event) - (_FIELDS | _SDK_FIELDS):
        return None
    for field, expected in (("release", _IDENTITY), ("environment", "staging"), ("server_name", _IDENTITY)):
        if field in event and event[field] != expected:
            return None
    if "timestamp" in event:
        try:
            if type(event["timestamp"]) is not str or datetime.fromisoformat(event["timestamp"]).tzinfo is None:
                return None
        except ValueError:
            return None
    if "sdk" in event and event["sdk"] != {
        "name": "sentry.python", "version": VERSION,
        "packages": [{"name": "pypi:sentry-sdk", "version": VERSION}],
        "integrations": [],
    }:
        return None
    projected = {key: event[key] for key in _FIELDS if key in event}
    if not _canonical_event(projected):
        return None
    result = scrub_event(projected, {})
    return result if _canonical_event(result) else None


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_health_event_key")
        result[key] = value
    return result


def _closed_envelope(envelope: Any) -> bool:
    """Reject unknown headers, attachments, reports, traces and extra payload."""
    try:
        if set(envelope.headers) != {"event_id", "sent_at"} or len(envelope.items) != 1:
            return False
        sent_at = envelope.headers["sent_at"]
        if type(sent_at) is not str or datetime.fromisoformat(sent_at).utcoffset() != UTC.utcoffset(None):
            return False
        item = envelope.items[0]
        if (
            set(item.headers) - {"type", "content_type"}
            or item.headers.get("type") != "event"
            or item.headers.get("content_type") not in (None, "application/json")
        ):
            return False
        raw = item.payload.get_bytes()
        if type(raw) is not bytes or not 0 < len(raw) <= 1024:
            return False
        event = json.loads(raw, object_pairs_hook=_unique)
        return _canonical_event(event) and envelope.headers["event_id"] == event["event_id"]
    except Exception:
        return False


class _HealthTransport(HttpTransport):
    """Small bounded SDK queue; only a checked envelope reaches HTTP."""

    def _get_pool_options(self):
        options = super()._get_pool_options()
        options["timeout"] = urllib3.Timeout(connect=2, read=3, total=5)
        options["retries"] = False
        return options

    def capture_envelope(self, envelope):
        if _closed_envelope(envelope):
            super().capture_envelope(envelope)

    def _send_envelope(self, envelope):
        # Check again after queuing. Do not append SDK client-report/session
        # items after this gate, and never invoke its raw-response log path.
        if not _closed_envelope(envelope) or self._check_disabled("error"):
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
                "POST", self._auth.get_api_url(), body=body.getvalue(), headers=headers,
                redirect=False, retries=False,
            )
            try:
                self._update_rate_limits(response)
            finally:
                response.close()
        except Exception:
            # No response body, URL, DSN, exception or retry is logged here.
            return


def _make_client(dsn: str):
    # Validate privately before SDK transport construction. Invalid/HTTP DSNs
    # never reach its constructor or logging; expose only a fixed failure.
    try:
        if type(dsn) is not str or Dsn(dsn).scheme != "https":
            raise ValueError
    except Exception:
        raise ValueError("Session Prep health DSN unavailable") from None
    return sentry_sdk.Client(
        dsn=dsn, transport=_HealthTransport,
        release=_IDENTITY, environment="staging", server_name=_IDENTITY, project_root="/",
        integrations=[], default_integrations=False, auto_enabling_integrations=False,
        enable_tracing=False, traces_sample_rate=None, profiles_sample_rate=0,
        profile_session_sample_rate=0, auto_session_tracking=False, send_client_reports=False,
        enable_backpressure_handling=False, attach_stacktrace=False, include_local_variables=False,
        send_default_pii=False, spotlight=False, debug=False, transport_queue_size=4, shutdown_timeout=1,
        before_send=_before_send, before_send_transaction=lambda _event, _hint: None,
        _experiments={
            "enable_metrics": False, "enable_logs": False, "continuous_profiling_auto_start": False,
            "transport_compression_level": 0,
        },
    )


def _after_fork() -> None:
    global _client, _client_pid, _client_lock
    # Never flush an inherited client's parent-process queue or acquire its
    # inherited lock. A child creates its own client only on an allowed alert.
    _client = None
    _client_pid = os.getpid()
    _client_lock = Lock()


def capture_health_category(category: str) -> None:
    """No arbitrary event, exception, count, job identifier or context input."""
    global _client
    try:
        if (
            type(category) is not str or category not in _CATEGORIES
            or not health_alerts_enabled() or sentry_enabled()
        ):
            return
        if os.getpid() != _client_pid:
            _after_fork()
        with _client_lock:
            if _client is None:
                _client = _make_client(config("SENTRY_DSN", default="", cast=str))
            client = _client
        client.capture_event(
            {"event_id": uuid4().hex, "platform": "python", "level": "error",
             "exception": {"values": [{"type": category, "module": _MODULE}]}},
            hint={}, scope=None,
        )
    except Exception:
        return


def close_health_client(*_args: Any, **_kwargs: Any) -> None:
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
worker_process_shutdown.connect(close_health_client, weak=False)
atexit.register(close_health_client)
