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

    # TODO can we hide this from the generated API?
    doctor: Doctor | None

    doctor_email: EmailStr | None = None
    "email of the doctor who the appointment is scheduled with"

    doctor_id: str | None = None
    "if an email is not a good identifier, specify a unique ID that represents the doctor"

    @model_validator(mode="after")
    def check_doctor_identifier(self):
        """
        - Validate that at least one doctor identifier is provided
        - Validate that the appointment date is not in the future
        - Validate that we can locate the doctor by uuid provided
        """

        if not (self.doctor_email or self.doctor_id):
            raise ValueError("Either doctor_email or doctor_id must be provided")

        if self.date > datetime.now():
            raise ValueError("Appointment date must be in the past")

        if self.doctor_email:
            # TODO we should scope the doctor by domain associated with this doctor API
            self.doctor = Doctor.get(email=self.doctor_email)

        if self.doctor_id:
            raise NotImplementedError("doctor_id not implemented yet")

        if not self.doctor:
            raise ValueError(
                "Doctor not found by email, make sure they have signed in to Cosmaneura"
            )

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
    appointment_request: AppointmentRequest, api_doctor: Doctor
) -> AppointmentResponse:
    # the "doctor" is this case is an API user, not an actual doctor
    # this means we shouldn't necessarily associate the
    return AppointmentResponse(url=HttpUrl("https://example.com"))
