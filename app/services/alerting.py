"""PHI-scrubbed Sentry alerts for otherwise silent dispatch failures.

Structlog does not emit stdlib records here, and Sentry strips event text and
context. Exception type identifies the issue; structured logs hold only
pseudonymous correlation references and bounded metadata.
"""

from typing import Any

import sentry_sdk

from app import log
from app.services.external_webhook_inbox import provider_log_ref


class DispatchAlert(Exception):
    """A visit may go unrecorded and a human needs to know."""


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


class MissingAppointmentBinding(DispatchAlert):
    "A tracked Zoom meeting has no appointment bound, so capture is unauthorized."


class RecordingContinuationBlocked(DispatchAlert):
    "Continuation was refused without an exact encounter binding."


class WatchdogArmingFailed(DispatchAlert):
    "Monitoring could not be armed. The visit proceeds; nothing is watching it."


class StuckHoldingForDoctor(DispatchAlert):
    "Patients are in the room but no participant was ever classified as the host."


class NoRecordingWithBothPresent(DispatchAlert):
    "Doctor and patient are both in the room and nothing is recording."


class AlertDeliveryProbe(DispatchAlert):
    "Deliberate test alert fired by `cli verify-alert-delivery`."


class WebhookRouteUnreachable(DispatchAlert):
    "The synthetic monitor could not reach, or was rejected by, our own endpoint."


class RecallApiUnreachable(DispatchAlert):
    "The synthetic monitor could not authenticate against the Recall API."


def alert(alert_type: type[DispatchAlert], **context: Any) -> str | None:
    """Log safe context and capture a type-identifiable Sentry event."""
    log.error(
        "zoom dispatch alert",
        event_code=alert_type.__name__,
        **context,
    )
    return sentry_sdk.capture_exception(alert_type())


def ref(value: Any) -> str:
    "Non-reversible correlation token for an identifier in alert context."
    return provider_log_ref(None if value is None else str(value))
