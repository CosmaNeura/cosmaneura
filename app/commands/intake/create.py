"""
Create a new intake form
"""

from datetime import date, datetime

from pydantic import AnyHttpUrl, BaseModel, EmailStr, HttpUrl, model_validator

from app.models.doctor import Doctor


class PatientRequest(BaseModel):
    name: str
    "name of the patient"

    dob: date
    "date of birth of the patient"


class AppointmentRequest(BaseModel):
    """
    A valid doctor association must be provided or the API will return an error.
    """

    id: str
    "ID of the appointment in the external system"

    date: datetime
    "date and time of the appointment"

    doctor_email: EmailStr | None = None
    "email of the doctor who the appointment is scheduled with"

    doctor_id: str | None = None
    "if an email is not a good identifier, specify a unique ID that represents the doctor"

    @model_validator(mode="after")
    def check_doctor_identifier(self):
        """Validate that at least one doctor identifier is provided"""
        if not (self.doctor_email or self.doctor_id):
            raise ValueError("Either doctor_email or doctor_id must be provided")
        return self

    intake_json: dict | None
    "if available, JSON that is the result of an intake form"

    intake_text: str | None
    "if JSON is not available, unstructured text that is the result of an intake form"

    patient: PatientRequest
    "DOB is required to reduce the risk of duplicates"

    appointment_type: str
    "type of appointment, e.g. 'follow up', 'new patient', etc."

    metadata: dict | None
    "additional metadata about the appointment"


class AppointmentResponse(BaseModel):
    url: HttpUrl
    "URL of the intake form for this appointment"


def perform(
    appointment_request: AppointmentRequest, doctor: Doctor
) -> AppointmentResponse:
    return AppointmentResponse(url=HttpUrl("https://example.com"))
