"""Acuity Scheduling adapter. Fetches appointments only; never writes back.

Acuity's OAuth2 (https://developers.acuityscheduling.com/docs/oauth2) issues
an access token with no documented refresh token and no expiry, unlike
Zoom/Google — DoctorSchedulingIntegration.refresh_token/token_expires_at are
nullable for exactly this reason. A 401 is treated as a revoked grant.

Acuity's /appointments response has no stable client ID field (confirmed
against the API reference — only firstName/lastName/email/phone). Its own
UI treats email as how it recognizes a returning client, so this adapter
uses the normalized email as this doctor's Acuity-scoped
external_patient_ref when present. This is still an exact-match key, never
a fuzzy name/DOB heuristic: an appointment with no email gets no
external_patient_ref and always routes to the confirmation queue (see
scheduling_provider.resolve_event).
"""

from datetime import datetime, timedelta

import httpx

from app import log
from app.configuration.acuity import (
    ACUITY_API_BASE,
    ACUITY_CLIENT_ID,
    ACUITY_CLIENT_SECRET,
    ACUITY_OAUTH_DISCONNECT_URL,
)
from app.services.external_webhook_inbox import provider_log_ref
from app.services.scheduling_provider import NormalizedAppointmentEvent

from app.models.doctor import Doctor
from app.models.doctor_scheduling_integration import DoctorSchedulingIntegration
from app.models.patient_external_identity import SchedulingProviderSource

_HTTPX_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
_ACUITY_PAGE_SIZE = 100


class AcuityIntegrationInvalid(Exception):
    """Raised when the doctor's Acuity OAuth grant is no longer valid (401)."""


def get_acuity_integration(doctor: Doctor) -> DoctorSchedulingIntegration | None:
    if doctor.deleted_at is not None:
        return None
    return DoctorSchedulingIntegration.get(
        doctor_id=doctor.id, source=SchedulingProviderSource.acuity
    )


def _doctor_ref(doctor_id) -> str:
    return provider_log_ref(f"doctor:{doctor_id}")


def revoke_token(doctor: Doctor) -> None:
    """Revoke the doctor's Acuity OAuth grant and delete the integration record.

    Mirrors app/services/zoom.py::revoke_token. Errors from Acuity are
    logged but never raised — the token may already be invalid, and a
    doctor-initiated disconnect must still succeed locally either way.
    """
    integration = get_acuity_integration(doctor)
    if integration is None:
        return

    with httpx.Client(timeout=_HTTPX_TIMEOUT) as client:
        response = client.post(
            ACUITY_OAUTH_DISCONNECT_URL,
            data={
                "access_token": integration.access_token,
                "client_id": ACUITY_CLIENT_ID,
                "client_secret": ACUITY_CLIENT_SECRET,
            },
        )

    if not response.is_success:
        log.warning(
            "acuity token revoke returned error",
            doctor_ref=_doctor_ref(doctor.id),
            operation="revoke_token",
            status="failed",
            provider_status=response.status_code,
            error_type="provider_http_error",
        )
    else:
        log.info(
            "acuity token revoked",
            doctor_ref=_doctor_ref(doctor.id),
            operation="revoke_token",
            status="succeeded",
            provider_status=response.status_code,
        )

    integration.delete()


def _get(
    integration: DoctorSchedulingIntegration, path: str, params: dict
) -> list[dict]:
    with httpx.Client(timeout=_HTTPX_TIMEOUT) as client:
        response = client.get(
            f"{ACUITY_API_BASE}{path}",
            params=params,
            headers={"Authorization": f"Bearer {integration.access_token}"},
        )

    if response.status_code == httpx.codes.UNAUTHORIZED:
        log.warning(
            "acuity token rejected; removing integration",
            doctor_ref=_doctor_ref(integration.doctor_id),
            operation="fetch_appointments",
            status="failed",
            provider_status=response.status_code,
            error_type="unauthorized",
        )
        integration.delete()
        raise AcuityIntegrationInvalid("Acuity OAuth grant was revoked")

    if not response.is_success:
        log.warning(
            "acuity provider request failed",
            doctor_ref=_doctor_ref(integration.doctor_id),
            operation="fetch_appointments",
            status="failed",
            provider_status=response.status_code,
            error_type="provider_http_error",
        )
    response.raise_for_status()
    return response.json()


def _normalize(
    raw: dict, doctor_external_calendar_ref: str
) -> NormalizedAppointmentEvent:
    email = (raw.get("email") or "").strip().lower() or None
    first = raw.get("firstName") or ""
    last = raw.get("lastName") or ""
    name = f"{first} {last}".strip() or None
    scheduled_by = raw.get("scheduledBy")
    start_at = datetime.fromisoformat(raw["datetime"])
    duration_minutes = int(raw.get("duration") or 0)
    end_at = start_at + timedelta(minutes=duration_minutes)
    return NormalizedAppointmentEvent(
        source=SchedulingProviderSource.acuity,
        external_appointment_ref=str(raw["id"]),
        external_patient_ref=email,
        doctor_external_calendar_ref=doctor_external_calendar_ref,
        start_at=start_at,
        end_at=end_at,
        patient_name=name,
        patient_email=email,
        patient_phone=raw.get("phone") or None,
        appointment_label=raw.get("type"),
        created_by="staff_scheduled" if scheduled_by else "patient_self_scheduled",
        canceled=bool(raw.get("canceled", False)),
    )


class AcuitySchedulingProvider:
    name = "acuity"

    def __init__(self, doctor: Doctor):
        integration = get_acuity_integration(doctor)
        if integration is None:
            raise ValueError("doctor has no acuity integration")
        self._doctor = doctor
        self._integration = integration

    def fetch_upcoming(
        self, doctor: Doctor, since: datetime
    ) -> list[NormalizedAppointmentEvent]:
        raw = _get(
            self._integration,
            "/appointments",
            params={
                "calendarID": self._integration.provider_calendar_ref,
                "minDate": since.date().isoformat(),
                "max": _ACUITY_PAGE_SIZE,
                "direction": "ASC",
            },
        )
        events = [_normalize(a, self._integration.provider_calendar_ref) for a in raw]
        log.info(
            "acuity appointments fetched",
            event_code="acuity_fetch_upcoming_completed",
            doctor_ref=_doctor_ref(doctor.id),
            count=len(events),
        )
        return events

    def fetch_historical(
        self, doctor: Doctor, before: datetime, cursor: str | None
    ) -> tuple[list[NormalizedAppointmentEvent], str | None]:
        # Acuity paginates by date range, not an opaque cursor. The cursor
        # here is the ISO maxDate of the oldest appointment fetched in the
        # previous page; None means "start from the most recent side".
        max_date = cursor or before.date().isoformat()
        raw = _get(
            self._integration,
            "/appointments",
            params={
                "calendarID": self._integration.provider_calendar_ref,
                "maxDate": max_date,
                "max": _ACUITY_PAGE_SIZE,
                "direction": "DESC",
            },
        )
        events = [_normalize(a, self._integration.provider_calendar_ref) for a in raw]
        log.info(
            "acuity historical appointments fetched",
            event_code="acuity_fetch_historical_completed",
            doctor_ref=_doctor_ref(doctor.id),
            count=len(events),
        )
        if len(raw) < _ACUITY_PAGE_SIZE:
            return events, None
        oldest = min(events, key=lambda e: e.start_at)
        next_cursor = oldest.start_at.date().isoformat()
        if next_cursor == max_date:
            # No forward progress possible (all results share one date); stop
            # rather than loop forever.
            return events, None
        return events, next_cursor
