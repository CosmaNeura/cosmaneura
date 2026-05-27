from datetime import datetime, timedelta

from fastapi import status
from fastapi.testclient import TestClient

from app.server import api_app

from activemodel.session_manager import get_engine
from app.models.ai_visit_transcript import AIVisitTranscript
from app.models.appointment import Appointment
from app.models.doctor import Doctor
from app.models.doctor_note_schema import DoctorNoteSchema
from app.models.patient import Patient
from sqlalchemy import event

from tests.factories import (
    DoctorNoteExampleFactory,
    DoctorNoteSchemaFactory,
    SimpleCompleteIntakeFactory,
)
from tests.routes.utils import get_clerk_dev_user, get_clerk_seed_user


def test_notes_list(authenticated_client: TestClient):
    response = authenticated_client.get(api_app.url_path_for("note_list"))

    assert response.status_code == status.HTTP_200_OK

    _result = response.json


def test_notes_list_no_n_plus_one(authenticated_client: TestClient):
    """Regression test: /notes must not issue one SELECT per appointment
    against `intake` or `patient`.

    Appointment exposes `intake_status` and `patient_name` as
    @computed_field properties; Pydantic serializes them for every row
    in the response. Without `selectinload(Appointment.intake)` and
    `selectinload(Appointment.patient)` on the query, each row triggers
    its own SELECT — turning a doctor with 100 appointments into ~200
    extra queries. This test asserts the query count for those two
    tables stays bounded at 1 each regardless of N.
    """
    _, _, clerk_user = get_clerk_dev_user()
    doctor = Doctor.find_or_create_by(clerk_id=clerk_user.id)

    # Seed several appointments with both intake and patient.
    # SimpleCompleteIntakeFactory creates appointment + patient + intake
    # tied to the same doctor (see its post_build).
    for _ in range(5):
        SimpleCompleteIntakeFactory.build(doctor_id=doctor.id).save()

    captured: list[str] = []

    def _record(conn, cursor, statement, parameters, context, executemany):
        captured.append(statement)

    engine = get_engine()
    event.listen(engine, "before_cursor_execute", _record)
    try:
        response = authenticated_client.get(api_app.url_path_for("note_list"))
    finally:
        event.remove(engine, "before_cursor_execute", _record)

    assert response.status_code == status.HTTP_200_OK

    intake_queries = [q for q in captured if "FROM intake" in q]
    patient_queries = [q for q in captured if "FROM patient" in q]

    assert len(intake_queries) <= 1, (
        f"N+1 against `intake` table: expected <=1 SELECT, got {len(intake_queries)}. "
        f"Statements: {intake_queries}"
    )
    assert len(patient_queries) <= 1, (
        f"N+1 against `patient` table: expected <=1 SELECT, got {len(patient_queries)}. "
        f"Statements: {patient_queries}"
    )


def test_note_detail(authenticated_client: TestClient):
    # more than one, because of the db seed
    assert Appointment.count() > 1

    _, _, clerk_user = get_clerk_dev_user()
    doctor = Doctor.find_or_create_by(clerk_id=clerk_user.id)

    if not doctor.email:
        assert clerk_user.email_addresses
        doctor.email = clerk_user.email_addresses[0].email_address
        doctor.save()

    patient = Patient(
        doctor_id=doctor.id,
        name="Test Patient",
        date_of_birth=datetime.now(),
    ).save()

    appointment = Appointment(
        doctor_id=doctor.id,
        patient_id=patient.id,
        appointment_date=datetime.now(),
        external_id=None,
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


def test_note_detail_deleted_transcript(authenticated_client: TestClient):
    # Use data from seeds.
    _, _, seed_user = get_clerk_seed_user()
    authenticated_client.override_user(seed_user)  # type: ignore[attr-defined]

    transcript = AIVisitTranscript.sample()
    assert not transcript.deleted_at
    appointment = Appointment.one(transcript.appointment_id)

    response = authenticated_client.get(
        api_app.url_path_for("note_detail", appointment_id=appointment.id)
    )

    assert response.status_code == status.HTTP_200_OK

    result = response.json()
    transcript_json = result["transcript"]
    assert transcript_json
    assert transcript_json["id"] == str(transcript.id)
    assert transcript_json["deleted_at"] is None
    assert len(transcript_json["transcript_data"]) > 0

    for entry in transcript_json["transcript_data"]:
        assert entry["speaker"]
        assert entry["content"]

    # Simulate deletion of the transcript.
    transcript.deleted_at = datetime.now()
    transcript.save()

    response = authenticated_client.get(
        api_app.url_path_for("note_detail", appointment_id=appointment.id)
    )

    assert response.status_code == status.HTTP_200_OK

    result = response.json()
    transcript_json = result["transcript"]
    assert transcript_json["id"] == str(transcript.id)
    assert transcript_json["deleted_at"] is not None
    assert len(transcript_json["transcript_data"]) == 0


def test_note_detail_old_transcript(authenticated_client: TestClient):
    "Old transcripts should be treated as deleted even if not marked as such."

    # Use data from seeds.
    _, _, seed_user = get_clerk_seed_user()
    authenticated_client.override_user(seed_user)  # type: ignore[attr-defined]

    transcript = AIVisitTranscript.sample()
    assert not transcript.deleted_at
    appointment = Appointment.one(transcript.appointment_id)

    response = authenticated_client.get(
        api_app.url_path_for("note_detail", appointment_id=appointment.id)
    )

    assert response.status_code == status.HTTP_200_OK

    result = response.json()
    transcript_json = result["transcript"]
    assert transcript_json
    assert transcript_json["id"] == str(transcript.id)
    assert transcript_json["deleted_at"] is None
    assert len(transcript_json["transcript_data"]) > 0

    # Simulate old transcript.
    transcript.created_at = datetime.now() - timedelta(days=15)
    transcript.save()

    response = authenticated_client.get(
        api_app.url_path_for("note_detail", appointment_id=appointment.id)
    )

    assert response.status_code == status.HTTP_200_OK

    result = response.json()
    transcript_json = result["transcript"]
    assert transcript_json["id"] == str(transcript.id)
    assert transcript_json["deleted_at"] is not None
    assert len(transcript_json["transcript_data"]) == 0


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
