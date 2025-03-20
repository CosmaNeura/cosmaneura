from datetime import datetime

from fastapi import status
from fastapi.testclient import TestClient

from app.server import api_app

from app.models.appointment import Appointment
from app.models.doctor import Doctor
from app.models.doctor_note_schema import DoctorNoteSchema
from app.models.patient import Patient

from tests.factories import DoctorNoteExampleFactory, DoctorNoteSchemaFactory
from tests.routes.utils import get_clerk_dev_user


def test_notes_list(authenticated_client: TestClient):
    response = authenticated_client.get(api_app.url_path_for("note_list"))

    assert response.status_code == status.HTTP_200_OK

    _result = response.json


def test_note_detail(authenticated_client: TestClient):
    # more than one, because of the db seed
    assert Appointment.count() > 1

    _, _, clerk_user = get_clerk_dev_user()
    doctor = Doctor.find_or_create_by(clerk_id=clerk_user.id)

    patient = Patient(
        doctor_id=doctor.id,
        name="Test Patient",
        date_of_birth=datetime.now(),
    ).save()

    appointment = Appointment(
        doctor_id=doctor.id,
        patient_id=patient.id,
        appointment_date=datetime.now(),
    ).save()

    response = authenticated_client.get(
        api_app.url_path_for("note_detail", appointment_id=appointment.id)
    )

    assert response.status_code == status.HTTP_200_OK

    # TODO casting to str here doesn't feel right, should we fix this?
    result = response.json()
    assert result["patient"]["id"] == str(patient.id)
    assert result["appointment"]["id"] == str(appointment.id)
    assert result["appointment"]["patient_id"] == str(patient.id)
    assert result["appointment"]["doctor_id"] == str(doctor.id)


def test_note_count():
    "this test shouldn't exist, but we are learning sqlalchemy + factory boy and are being a bit paranoid"

    # paranoid check that the db is empty, we are still figuring out the right pattern for seeds, etc
    assert DoctorNoteSchema.count() == 2

    note_schema = DoctorNoteSchemaFactory.build().save()

    note_schema2 = DoctorNoteSchemaFactory.build(doctor_id=note_schema.doctor_id).save()
    DoctorNoteExampleFactory.build(
        doctor_note_schema_id=note_schema2.id, doctor_id=note_schema2.doctor_id
    ).save()

    assert DoctorNoteSchema.count() == 4
    assert note_schema.doctor_id == note_schema2.doctor_id

    assert note_schema.example_count == 1
    assert note_schema2.example_count == 2
