from datetime import datetime

from fastapi import status
from fastapi.testclient import TestClient

from app.server import api_app

from app.models.doctor import Doctor
from app.models.doctor_note_schema import DoctorNoteSchema
from app.models.note_type_customization import NoteTypeCustomization, SubjectiveSource

from tests.factories import DoctorFactory
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


def test_settings_reflects_saved_customization(authenticated_client: TestClient):
    doctor = _authed_doctor()
    schema = DoctorNoteSchema(
        doctor_id=doctor.id, note_type="SP2 SAVED", fields={"CC": "x"}
    ).save()
    NoteTypeCustomization(
        doctor_note_schema_id=schema.id,
        subjective_source=SubjectiveSource.patient_and_doctor,
        require_plan_reason=False,
        custom_instructions="Use metric units.",
    ).save()

    resp = authenticated_client.get(api_app.url_path_for("settings_data"))
    assert resp.status_code == 200
    entry = next(
        c
        for c in resp.json()["note_customizations"]
        if c["doctor_note_schema_id"] == str(schema.id)
    )
    assert entry["subjective_source"] == "patient_and_doctor"
    assert entry["require_plan_reason"] is False
    assert entry["custom_instructions"] == "Use metric units."


_FULL_BODY = {
    "subjective_source": "patient_and_doctor",
    "pronoun_style": "neutral",
    "plan_ordering": "as_discussed",
    "flag_new_medications": False,
    "require_plan_reason": False,
    "ros_distinct_from_subjective": False,
    "custom_instructions": "Use metric units.",
}


def test_put_creates_then_updates_in_place(authenticated_client: TestClient):
    doctor = _authed_doctor()
    schema = DoctorNoteSchema(
        doctor_id=doctor.id, note_type="SP2 PUT", fields={"CC": "x"}
    ).save()
    url = api_app.url_path_for("update_note_customization", schema_id=str(schema.id))

    resp = authenticated_client.put(url, json=_FULL_BODY)
    assert resp.status_code == 200
    assert resp.json()["require_plan_reason"] is False
    assert resp.json()["doctor_note_schema_id"] == str(schema.id)

    resp2 = authenticated_client.put(
        url, json={**_FULL_BODY, "require_plan_reason": True}
    )
    assert resp2.status_code == 200
    assert resp2.json()["require_plan_reason"] is True
    assert (
        NoteTypeCustomization.where(
            NoteTypeCustomization.doctor_note_schema_id == schema.id
        ).count()
        == 1
    )


def test_put_other_doctors_schema_returns_404(authenticated_client: TestClient):
    other = DoctorFactory.build().save()
    schema = DoctorNoteSchema(
        doctor_id=other.id, note_type="SP2 OTHER", fields={"CC": "x"}
    ).save()
    url = api_app.url_path_for("update_note_customization", schema_id=str(schema.id))
    resp = authenticated_client.put(url, json=_FULL_BODY)
    assert resp.status_code == status.HTTP_404_NOT_FOUND


def test_put_soft_deleted_schema_returns_404(authenticated_client: TestClient):
    doctor = _authed_doctor()
    schema = DoctorNoteSchema(
        doctor_id=doctor.id, note_type="SP2 SOFT-DEL", fields={"CC": "x"}
    ).save()
    schema.deleted_at = datetime.now()
    schema.save()
    assert schema.deleted_at is not None

    url = api_app.url_path_for("update_note_customization", schema_id=str(schema.id))
    resp = authenticated_client.put(url, json=_FULL_BODY)
    assert resp.status_code == status.HTTP_404_NOT_FOUND


def test_put_rejects_overlong_custom_instructions(authenticated_client: TestClient):
    doctor = _authed_doctor()
    schema = DoctorNoteSchema(
        doctor_id=doctor.id, note_type="SP2 LONG", fields={"CC": "x"}
    ).save()
    url = api_app.url_path_for("update_note_customization", schema_id=str(schema.id))
    resp = authenticated_client.put(
        url, json={**_FULL_BODY, "custom_instructions": "x" * 4001}
    )
    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
