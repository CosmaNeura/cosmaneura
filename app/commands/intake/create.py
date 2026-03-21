"""
Create a new intake form
"""

from datetime import date, datetime, timezone
from typing import cast

from pydantic import BaseModel, EmailStr, HttpUrl, model_validator
from pydantic.json_schema import SkipJsonSchema

from app import log
from app.jobs.intake.generate_questions import queue as queue_question_generation

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

    @model_validator(mode="after")
    def check_appointment_date(self):
        """
        - Validate that the appointment date is in the future
        """
        if self.date.astimezone(timezone.utc) < datetime.now(timezone.utc):
            raise ValueError("Appointment date must be in the future")

        return self

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
    "intake information collected by an external system"

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
def perform(appointment_request: AppointmentRequest) -> AppointmentResponse:
    # This is set in the check_doctor_identifier callback during request
    # validation.
    assert appointment_request.doctor

    # let's see if we can find the appointment by the external ID
    if appointment_request.id and (
        appointment := Appointment.get(external_id=appointment_request.id)
    ):
        intake = Intake.one(appointment_id=appointment.id)

        # TODO update the appointment with the new data
        log.warning("found existing appointment, but update API not implemented")

        return AppointmentResponse(url=intake.public_url, id=intake.id)

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

    # TODO we should really be wrapping this in a transaction with the above operation as well

    # we are finally ready to create an intake
    intake = Intake(
        doctor_id=doctor_id,
        appointment_id=appointment.id,
        # copy intake schema questions into the intake object itself
        # this is slightly paranoid/unnecessary
        intake_questions=[],
        external_intake_data=cast(dict[str, str], appointment_request.intake_json or {}),
    ).save()

    queue_question_generation(intake.id)

    return AppointmentResponse(url=intake.public_url, id=intake.id)
