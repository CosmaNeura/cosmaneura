"""Fixed, content-free preparation alerts using the existing Sentry boundary."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import uuid4

import sentry_sdk

from app.configuration.sentry import sentry_enabled
from app.services.session_prep_operations import QUEUE_LAG_THRESHOLD_SECONDS


class SessionPrepQueueStalled(Exception):
    """Queued preparation work exceeded the existing age threshold."""


class SessionPrepRecentBuildFailures(Exception):
    """The bounded health window contains an actionable preparation failure."""


class SessionPrepMaintenanceUnavailable(Exception):
    """Preparation receipt maintenance or queue health could not be checked."""


_ALERT_TYPES = frozenset(
    {
        SessionPrepQueueStalled,
        SessionPrepRecentBuildFailures,
        SessionPrepMaintenanceUnavailable,
    }
)


def _capture(alert_type: type[Exception]) -> None:
    """Never capture a caught error, arbitrary context, stack or health payload."""
    if alert_type not in _ALERT_TYPES:
        return

    def content_free_event(_event: Any, _hint: Any) -> Any:
        # SDK scopes may inherit clinical request breadcrumbs even in a worker.
        # Rebuild this alert's event before the unchanged global PHI scrubber.
        # Attachments live in the SDK hint rather than in the event itself.
        # Removing event fields alone must not allow an inherited file out.
        if type(_hint) is not dict:
            return None
        _hint.clear()
        return {
            # The SDK requires an event identifier after scope processing.
            # Generate fresh transport metadata; never copy an inherited ID.
            "event_id": event_id,
            "platform": "python",
            "level": "error",
            "exception": {
                "values": [{"type": alert_type.__name__, "module": __name__}]
            },
        }

    try:
        if not sentry_enabled():
            return
        event_id = uuid4().hex
        with sentry_sdk.new_scope() as scope:
            scope.clear()
            scope.add_event_processor(content_free_event)
            sentry_sdk.capture_exception(alert_type())
    except Exception:
        # Monitoring must not break receipt cleanup, job execution or recovery.
        # SDK failures can contain credentials; do not log or retry them here.
        return


def report_degraded_health(health: Mapping[str, object]) -> None:
    """Emit at most two fixed categories; no counts or source values cross out."""
    lag = health.get("oldest_pending_seconds")
    failures = health.get("actionable_failed")
    if type(lag) is int and lag > QUEUE_LAG_THRESHOLD_SECONDS:
        _capture(SessionPrepQueueStalled)
    if type(failures) is int and failures > 0:
        _capture(SessionPrepRecentBuildFailures)


def report_maintenance_unavailable() -> None:
    _capture(SessionPrepMaintenanceUnavailable)
