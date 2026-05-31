from fastapi.testclient import TestClient

from app.server import api_app

from app.models.doctor import Doctor
from app.models.doctor_note_schema import DoctorNoteSchema

from tests.routes.utils import get_clerk_dev_user


def _authed_doctor() -> Doctor:
    _, _, clerk_user = get_clerk_dev_user()
    return Doctor.find_or_create_by(clerk_id=clerk_user.id)


def test_settings_includes_default_customization_when_no_row(
    authenticated_client: TestClient,
):
    doctor = _authed_doctor()
    schema = DoctorNoteSchema(
        doctor_id=doctor.id, note_type="SP2 SOAP", fields={"CC": "x"}
    ).save()

    resp = authenticated_client.get(api_app.url_path_for("settings_data"))
    assert resp.status_code == 200
    body = resp.json()

    entry = next(
        c
        for c in body["note_customizations"]
        if c["doctor_note_schema_id"] == str(schema.id)
    )
    assert entry["subjective_source"] == "patient_only"
    assert entry["require_plan_reason"] is True
    assert entry["custom_instructions"] is None
