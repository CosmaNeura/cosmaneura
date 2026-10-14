"""Signed-in staging users can create fixed invented visits without audio."""

from datetime import date
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from app.environments import is_staging
from app.services.staging_synthetic_appointment import (
    StagingSyntheticAppointmentConflict,
    StagingSyntheticAppointmentError,
)
from app.services.staging_test_visits import (
    StagingTestVisitRequestExpired,
    ensure_staging_test_visits,
)

staging_test_visits_api = APIRouter()


class StagingTestVisitsRequest(BaseModel, extra="forbid"):
    request_id: UUID
    visit_date: date


class StagingTestVisitReceipt(BaseModel):
    appointment_id: str
    appointment_name: str


class StagingTestVisitsResponse(BaseModel):
    request_id: UUID
    visit_date: date
    created: bool
    visits: tuple[StagingTestVisitReceipt, StagingTestVisitReceipt]


@staging_test_visits_api.post("/staging/test-visits", include_in_schema=False)
def create_staging_test_visits(
    request: Request,
    data: StagingTestVisitsRequest,
    response: Response,
) -> StagingTestVisitsResponse:
    response.headers["Cache-Control"] = "no-store"
    if not is_staging():
        raise HTTPException(404, "Not Found")
    if request.state.view_as is not None:
        raise HTTPException(
            403, "Test visits are unavailable during read-only view-as."
        )
    doctor = request.state.doctor
    try:
        result = ensure_staging_test_visits(
            doctor_id=doctor.id,
            doctor_email=doctor.email,
            request_id=data.request_id,
            visit_date=data.visit_date,
        )
    except StagingTestVisitRequestExpired:
        raise HTTPException(
            410,
            {
                "code": "test_visit_request_expired_without_pair",
                "request_id": str(data.request_id),
                "visit_date": data.visit_date.isoformat(),
            },
            headers={"Cache-Control": "no-store"},
        ) from None
    except StagingSyntheticAppointmentConflict:
        raise HTTPException(
            409, "The retained test visits could not be confirmed."
        ) from None
    except StagingSyntheticAppointmentError:
        raise HTTPException(
            409, "The staging synthetic fixture is unavailable."
        ) from None
    first, second = result.appointments
    return StagingTestVisitsResponse(
        request_id=data.request_id,
        visit_date=data.visit_date,
        created=result.created,
        visits=(
            StagingTestVisitReceipt(
                appointment_id=first.id,
                appointment_name=first.appointment_name,
            ),
            StagingTestVisitReceipt(
                appointment_id=second.id,
                appointment_name=second.appointment_name,
            ),
        ),
    )
