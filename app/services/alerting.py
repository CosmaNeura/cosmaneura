"""Alerts for dispatch failures that are otherwise invisible.

Why this module exists: the application logger is structlog configured with a
``PrintLoggerFactory``/``BytesLoggerFactory`` (see
``structlog_config._logger_factory``), so an ``app.log`` call never creates a
stdlib ``LogRecord``. Sentry's ``LoggingIntegration`` hooks
``logging.Logger.callHandlers``, which means **no ``log.error(...)`` in this
codebase has ever produced a Sentry event**. Only unhandled exceptions reach
Sentry, and every abort in the Zoom -> Recall path is a ``return`` with a log
line rather than a raise -- so a note-taker that fails to join a real visit
produces exactly zero alerts and is discovered when the doctor complains.

**How this fits the PHI boundary.** ``configuration.sentry.scrub_event`` drops
``message``, ``logentry``, ``tags``, ``contexts``, ``extra`` and ``fingerprint``
from every outbound event, and blanks exception ``value`` strings. That is
deliberate: Sentry is not a channel that carries application text. The one
thing it still carries is the **exception type**, so each failure mode is its
own class here. Sentry gets a distinguishable, empty-by-construction event; the
detail lives in the structured log, keyed by the same ``event_code``.

Identifiers go through ``provider_log_ref`` for the same reason they do in the
webhook adapters -- non-reversible, still greppable, correlates across lines.
"""

from typing import Any

import sentry_sdk

from app import log
from app.services.external_webhook_inbox import provider_log_ref


class DispatchAlert(Exception):
    """A visit will go unrecorded and a human needs to know.

    Never raised as control flow -- instances are handed to
    ``sentry_sdk.capture_exception`` so the class name becomes the Sentry
    issue. Raising one would change celery retry behaviour on paths that
    deliberately return.
    """


class LostSessionState(DispatchAlert):
    "meeting.started never registered this session, so dispatch has no doctor."


class DoctorNotFound(DispatchAlert):
    "Session state points at a doctor row that no longer exists."


class ZoomOAuthRevoked(DispatchAlert):
    "The doctor disconnected Zoom; every visit of theirs is unrecorded until they reconnect."


class BotNeverReady(DispatchAlert):
    "Recall accepted the bot but it never reached a recordable state."


class RecallApiError(DispatchAlert):
    "The Recall API refused or was unreachable during dispatch."


class StuckHoldingForDoctor(DispatchAlert):
    "Patients are in the room but no participant was ever classified as the host."


class NoRecordingWithBothPresent(DispatchAlert):
    "Doctor and patient are both in the room and nothing is recording."


class WebhookRouteUnreachable(DispatchAlert):
    "The synthetic monitor could not reach, or was rejected by, our own endpoint."


class RecallApiUnreachable(DispatchAlert):
    "The synthetic monitor could not authenticate against the Recall API."


def alert(alert_type: type[DispatchAlert], **context: Any) -> None:
    """Write the structured log line and raise a Sentry event.

    ``event_code`` is the class name, matching the convention the webhook
    adapters already use, so the log and the Sentry issue join on one token.
    Context values are counts and enum-like strings; pass identifiers through
    ``ref()`` rather than raw.
    """
    log.error(
        "zoom dispatch alert",
        event_code=alert_type.__name__,
        **context,
    )
    sentry_sdk.capture_exception(alert_type())


def ref(value: Any) -> str:
    "Non-reversible correlation token for an identifier in alert context."
    return provider_log_ref(None if value is None else str(value))
