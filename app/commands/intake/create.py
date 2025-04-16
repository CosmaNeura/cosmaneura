"""
Create a new intake form
"""

from datetime import date, datetime, timezone

from pydantic import BaseModel, EmailStr, HttpUrl, model_validator
from pydantic.json_schema import SkipJsonSchema

import app.jobs.intake.schema_picker

from activemodel.types import TypeIDType
from app.models.appointment import Appointment
from app.models.doctor import Doctor
from app.models.intake.intake import Intake
from app.models.patient import Patient


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

    doctor: SkipJsonSchema[Doctor | None] = None

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

        if self.date.astimezone(timezone.utc) > datetime.now(timezone.utc):
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

        # error out if the doctor doesn't have any intake form uploaded

        # remove all time information from the date
        # self.date = datetime(self.date.year, self.date.month, self.date.day, 0, 0, 0, 0)

        return self

    intake_json: dict | None
    "if available, JSON that is the result of an intake form"

    # TODO initial release only needs JSON, could easily support string input in the future
    # intake_text: str | None
    # "if JSON is not available, unstructured text that is the result of an intake form"

    patient: PatientRequest
    "DOB is required to reduce the risk of duplicates"

    appointment_type: str
    "type of appointment, e.g. 'follow up', 'new patient', etc."

    # TODO not required for the initial release, but probably something we should offer in the future
    # metadata: dict | None
    # "additional metadata about the appointment"


class AppointmentResponse(BaseModel):
    id: TypeIDType
    "ID of the appointment in cosmaneura"

    url: HttpUrl
    "URL of the intake form for this appointment"


# TODO wrap the whole thing in a DB transaction
def perform(
    appointment_request: AppointmentRequest, api_doctor: Doctor
) -> AppointmentResponse:
    # the "doctor" is this case is an API user, not an actual doctor
    # this means we shouldn't associate the appointment with that doctor

    assert appointment_request.doctor

    # let's see if we can find the appointment by the external ID
    if appointment_request.id and (
        appointment := Appointment.get(external_id=appointment_request.id)
    ):
        # TODO update the appointment with the new data
        return AppointmentResponse(
            url=HttpUrl("https://example.com"), id=str(appointment.id)
        )

    doctor_id = appointment_request.doctor.id

    # TODO obviously, this will fail in many edge cases, but let's start simple
    # Try to find existing patient by name and date of birth
    patient = Patient.find_or_create_by(
        name=appointment_request.patient.name,
        date_of_birth=appointment_request.patient.dob,
        doctor_id=doctor_id,
    )

    # now, let's find or create an appointment
    appointment = Appointment(
        doctor_id=doctor_id,
        patient_id=patient.id,
        appointment_date=appointment_request.date,
        external_id=appointment_request.id,
        created_by_system="api",
    ).save()

    # we are finally ready to create an intake
    intake = Intake(
        doctor_id=doctor_id,
        appointment_id=appointment.id,
        # copy intake schema questions into the intake object itself
        # this is slightly paranoid/unnecessary
        intake_questions=None,
        external_intake_data=appointment_request.intake_json,
    ).save()

    app.jobs.intake.schema_picker.perform.delay(intake.id)

    # TODO something in activemodel is broken, without casting to string this breaks...
    return AppointmentResponse(url=intake.public_url, id=str(intake.id))
