"""Default-off signed current-booking ingress; never contact the patient."""

import asyncio
from contextvars import Context

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.configuration import visit_prep, visit_prep_acuity
from app.services.visit_prep_access import resolve_visit_prep_access
from app.services.visit_prep_acuity import (
    fetch_current_booking,
    fetch_current_booking_state,
    project_current_booking,
    project_current_booking_state,
    require_current_booking_source,
    verify_booking_notification,
    verify_scheduled_notification,
)
from app.services.visit_prep_booking import record_current_booking
from app.services.visit_prep_booking_lifecycle import record_current_booking_state
from app.services.visit_prep_packages import _database_now
from app.services.visit_preps import _visit_prep_session

from app.models.doctor import Doctor

PATH = "/external/v1/visit-prep/acuity/scheduled"
LIFECYCLE_PATH = "/external/v1/visit-prep/acuity/changed"
router = APIRouter()
HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}


@router.post(PATH, name="visit_prep_acuity_scheduled", include_in_schema=False)
async def scheduled_booking(request: Request):
    return await _handle_booking(request, lifecycle=False)


@router.post(LIFECYCLE_PATH, name="visit_prep_acuity_changed", include_in_schema=False)
async def changed_booking(request: Request):
    return await _handle_booking(request, lifecycle=True)


async def _handle_booking(request: Request, *, lifecycle: bool):
    settings = visit_prep_acuity.SETTINGS
    if not settings.enabled or not visit_prep.VISIT_PREP_SETTINGS.enabled:
        return JSONResponse(
            {"detail": "Visit Prep booking is unavailable."},
            status_code=503,
            headers=HEADERS,
        )
    try:
        if (
            request.query_params
            or request.headers.getlist("content-type")
            != ["application/x-www-form-urlencoded"]
            or len(request.headers.getlist("x-acuity-signature")) != 1
            or request.headers.getlist("content-encoding")
            or len(request.headers.getlist("content-length")) > 1
        ):
            raise ValueError
        length = request.headers.get("content-length")
        if length is not None and (
            not length.isascii() or not length.isdigit() or not 1 <= int(length) <= 1024
        ):
            raise ValueError
        raw = bytearray()
        async with asyncio.timeout(5):
            async for chunk in request.stream():
                if len(raw) + len(chunk) > 1024:
                    raise ValueError
                raw.extend(chunk)
        if length is not None and len(raw) != int(length):
            raise ValueError
        verifier = (
            verify_booking_notification if lifecycle else verify_scheduled_notification
        )
        appointment_id, calendar_id = verifier(
            bytes(raw), request.headers["x-acuity-signature"], settings
        )
        bindings = [b for b in settings.calendars if b.calendar_id == calendar_id]
        if len(bindings) != 1:
            raise ValueError
        binding = bindings[0]
    except Exception:
        return JSONResponse(
            {"detail": "Visit Prep booking is unavailable."},
            status_code=400,
            headers=HEADERS,
        )

    def apply():
        doctor = Doctor.get(id=binding.owner_doctor_id)
        if (
            doctor is None
            or not resolve_visit_prep_access(
                doctor, settings=visit_prep.VISIT_PREP_SETTINGS
            ).can_manage
        ):
            raise ValueError
        # Capture database time before either source fetch. A prior unmatched
        # cancellation must also fence a delayed scheduled-only response.
        with _visit_prep_session() as session:
            observed_at = _database_now(session)
        if lifecycle:
            current = fetch_current_booking_state(
                appointment_id=appointment_id, binding=binding, settings=settings
            )
            source, state = project_current_booking_state(
                booking=current,
                binding=binding,
                settings=settings,
                observed_at=observed_at,
            )
            return record_current_booking_state(
                state=state,
                source=source,
                require_current_source=lambda: require_current_booking_source(
                    binding, settings
                ),
            )
        booking = fetch_current_booking(
            appointment_id=appointment_id, binding=binding, settings=settings
        )
        source, projected = project_current_booking(
            booking=booking, binding=binding, settings=settings
        )
        return record_current_booking(
            booking=projected,
            source=source,
            observed_at=observed_at,
            require_current_source=lambda: require_current_booking_source(
                binding, settings
            ),
        )

    try:
        # A source receipt owns its transaction, not the request's cached ORM
        # session. Inherited context could otherwise commit unrelated work.
        receipt = await asyncio.to_thread(Context().run, apply)
        return JSONResponse(
            {
                "contract": "visit_prep.booking_receipt.v1",
                "preparation_id": receipt.preparation_id,
                "disposition": receipt.disposition,
                "delivery": "not_requested",
            },
            headers=HEADERS,
        )
    except Exception:
        # Acuity retries failed webhooks only on 500. No private SDK/SQL detail.
        return JSONResponse(
            {"detail": "Visit Prep booking is unavailable."},
            status_code=500,
            headers=HEADERS,
        )
