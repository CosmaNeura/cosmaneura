"""Google Calendar adapter. Read-only; never writes back to the calendar.

Unlike Acuity, Google's OAuth2 is a standard refresh-token flow (mirrors
app/services/zoom.py's _get_doctor_token pattern). A Google Calendar event
has no patient/client concept — only attendees. This adapter treats the
first non-organizer attendee as the patient and uses their normalized email
as external_patient_ref, the same non-fuzzy exact-match approach as Acuity
(see scheduling_acuity.py). An event with no such attendee is not an
appointment candidate and is skipped entirely, not queued.
"""

from datetime import datetime, timedelta, timezone

import httpx

from app import log
from app.configuration.google_calendar import (
    GOOGLE_CALENDAR_API_BASE,
    GOOGLE_CALENDAR_CLIENT_ID,
    GOOGLE_CALENDAR_CLIENT_SECRET,
    GOOGLE_OAUTH_TOKEN_URL,
)
from app.services.external_webhook_inbox import provider_log_ref
from app.services.scheduling_provider import NormalizedAppointmentEvent

from app.models.doctor import Doctor
from app.models.doctor_scheduling_integration import DoctorSchedulingIntegration
from app.models.patient_external_identity import SchedulingProviderSource

_HTTPX_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
_TOKEN_EXPIRY_BUFFER = timedelta(minutes=5)
_GOOGLE_PAGE_SIZE = 100


class GoogleCalendarIntegrationInvalid(Exception):
    """Raised when the doctor's Google OAuth grant is no longer valid."""


def get_google_calendar_integration(
    doctor: Doctor,
) -> DoctorSchedulingIntegration | None:
    if doctor.deleted_at is not None:
        return None
    return DoctorSchedulingIntegration.get(
        doctor_id=doctor.id, source=SchedulingProviderSource.google_calendar
    )


def _doctor_ref(doctor_id) -> str:
    return provider_log_ref(f"doctor:{doctor_id}")


def _get_access_token(
    integration: DoctorSchedulingIntegration, *, force: bool = False
) -> str:
    """Return a valid access token, refreshing if it's within 5 minutes of expiry."""
    now = datetime.now(timezone.utc)
    expires_at = integration.token_expires_at
    if expires_at is not None:
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if not force and expires_at > now + _TOKEN_EXPIRY_BUFFER:
            return integration.access_token

    if not integration.refresh_token:
        # No refresh token on file and the current token is expired (or a
        # refresh was forced) - nothing left to try.
        raise GoogleCalendarIntegrationInvalid(
            "Google Calendar access token expired with no refresh token on file"
        )

    with httpx.Client(timeout=_HTTPX_TIMEOUT) as client:
        response = client.post(
            GOOGLE_OAUTH_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": integration.refresh_token,
                "client_id": GOOGLE_CALENDAR_CLIENT_ID,
                "client_secret": GOOGLE_CALENDAR_CLIENT_SECRET,
            },
        )

    if response.status_code == 400:
        try:
            error_code = response.json().get("error")
        except ValueError:
            error_code = None
        if error_code == "invalid_grant":
            log.warning(
                "google calendar refresh returned invalid_grant; removing integration",
                doctor_ref=_doctor_ref(integration.doctor_id),
                operation="refresh_token",
                status="failed",
                provider_status=response.status_code,
                error_type="invalid_grant",
            )
            integration.delete()
            raise GoogleCalendarIntegrationInvalid(
                "Google Calendar OAuth grant was revoked"
            )

    if not response.is_success:
        log.warning(
            "google calendar token refresh failed",
            doctor_ref=_doctor_ref(integration.doctor_id),
            operation="refresh_token",
            status="failed",
            provider_status=response.status_code,
            error_type="provider_http_error",
        )
    response.raise_for_status()
    data = response.json()

    integration.access_token = data["access_token"]
    # Google only returns a new refresh_token on rare re-consent flows;
    # otherwise the original stays valid and must be preserved.
    if data.get("refresh_token"):
        integration.refresh_token = data["refresh_token"]
    integration.token_expires_at = now + timedelta(seconds=data["expires_in"])
    integration.save()

    log.info(
        "google calendar token refreshed",
        doctor_ref=_doctor_ref(integration.doctor_id),
        operation="refresh_token",
        status="succeeded",
    )
    return integration.access_token


def _get(integration: DoctorSchedulingIntegration, path: str, params: dict) -> dict:
    token = _get_access_token(integration)
    with httpx.Client(timeout=_HTTPX_TIMEOUT) as client:
        response = client.get(
            f"{GOOGLE_CALENDAR_API_BASE}{path}",
            params=params,
            headers={"Authorization": f"Bearer {token}"},
        )

    if response.status_code == httpx.codes.UNAUTHORIZED:
        log.info(
            "google calendar token rejected, refreshing",
            doctor_ref=_doctor_ref(integration.doctor_id),
            operation="fetch_events",
            status="retrying",
            provider_status=response.status_code,
            error_type="unauthorized",
        )
        token = _get_access_token(integration, force=True)
        with httpx.Client(timeout=_HTTPX_TIMEOUT) as client:
            response = client.get(
                f"{GOOGLE_CALENDAR_API_BASE}{path}",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )

    if not response.is_success:
        log.warning(
            "google calendar provider request failed",
            doctor_ref=_doctor_ref(integration.doctor_id),
            operation="fetch_events",
            status="failed",
            provider_status=response.status_code,
            error_type="provider_http_error",
        )
    response.raise_for_status()
    return response.json()


def _normalize(
    raw: dict, doctor_external_calendar_ref: str
) -> NormalizedAppointmentEvent | None:
    start = raw.get("start", {}).get("dateTime")
    end = raw.get("end", {}).get("dateTime")
    if not start or not end:
        # All-day events (date-only, no dateTime) are never patient
        # appointments in this adapter's scope.
        return None

    organizer_email = (raw.get("organizer", {}).get("email") or "").strip().lower()
    attendee = next(
        (
            a
            for a in raw.get("attendees", [])
            if (a.get("email") or "").strip().lower() != organizer_email
            and not a.get("resource", False)
        ),
        None,
    )
    if attendee is None:
        # No attendee besides the doctor: a personal block, not an
        # appointment candidate. Skip entirely rather than queue noise.
        return None

    email = (attendee.get("email") or "").strip().lower() or None
    name = attendee.get("displayName") or None

    return NormalizedAppointmentEvent(
        source=SchedulingProviderSource.google_calendar,
        external_appointment_ref=raw["id"],
        external_patient_ref=email,
        doctor_external_calendar_ref=doctor_external_calendar_ref,
        start_at=datetime.fromisoformat(start),
        end_at=datetime.fromisoformat(end),
        patient_name=name,
        patient_email=email,
        appointment_label=raw.get("summary"),
        canceled=raw.get("status") == "cancelled",
    )


class GoogleCalendarSchedulingProvider:
    name = "google_calendar"

    def __init__(self, doctor: Doctor):
        integration = get_google_calendar_integration(doctor)
        if integration is None:
            raise ValueError("doctor has no google_calendar integration")
        self._doctor = doctor
        self._integration = integration

    def fetch_upcoming(
        self, doctor: Doctor, since: datetime
    ) -> list[NormalizedAppointmentEvent]:
        events, _ = self._fetch_page(
            time_min=since, time_max=None, page_token=None, order="startTime"
        )
        log.info(
            "google calendar events fetched",
            event_code="google_calendar_fetch_upcoming_completed",
            doctor_ref=_doctor_ref(doctor.id),
            count=len(events),
        )
        return events

    def fetch_historical(
        self, doctor: Doctor, before: datetime, cursor: str | None
    ) -> tuple[list[NormalizedAppointmentEvent], str | None]:
        events, next_page_token = self._fetch_page(
            time_min=None, time_max=before, page_token=cursor, order=None
        )
        log.info(
            "google calendar historical events fetched",
            event_code="google_calendar_fetch_historical_completed",
            doctor_ref=_doctor_ref(doctor.id),
            count=len(events),
        )
        return events, next_page_token

    def _fetch_page(
        self,
        *,
        time_min: datetime | None,
        time_max: datetime | None,
        page_token: str | None,
        order: str | None,
    ) -> tuple[list[NormalizedAppointmentEvent], str | None]:
        params: dict = {
            "singleEvents": "true",
            "maxResults": _GOOGLE_PAGE_SIZE,
        }
        if time_min is not None:
            params["timeMin"] = time_min.astimezone(timezone.utc).isoformat()
        if time_max is not None:
            params["timeMax"] = time_max.astimezone(timezone.utc).isoformat()
        if page_token:
            params["pageToken"] = page_token
        if order:
            params["orderBy"] = order

        data = self._get(
            f"/calendars/{self._integration.provider_calendar_ref}/events", params
        )
        events = [
            e
            for raw in data.get("items", [])
            if (e := _normalize(raw, self._integration.provider_calendar_ref))
            is not None
        ]
        return events, data.get("nextPageToken")

    def _get(self, path: str, params: dict) -> dict:
        return _get(self._integration, path, params)
