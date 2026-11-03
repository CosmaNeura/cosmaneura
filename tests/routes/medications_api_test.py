from datetime import datetime, timezone
from typing import cast

import pytest
from fastapi import status
from fastapi.testclient import TestClient

import app.jobs.extract_medications as extract_medications_job
from app.server import api_app

from app.models.ai_visit_transcript import AIVisitTranscript, TranscriptEntry
from app.models.doctor import Doctor
from app.models.drug_reference import DrugReference
from app.models.patient import Patient
from app.models.patient_medication import (
    MedicationSource,
    MedicationStatus,
    PatientMedication,
)

from tests.factories import AppointmentFactory, DoctorFactory, PatientFactory
from tests.routes.utils import get_clerk_dev_user


@pytest.mark.parametrize("previous_status", ["active", "stopped"])
def test_disabled_new_holds_preserves_existing_row_before_any_update(
    authenticated_client: TestClient, monkeypatch, previous_status: str
):
    monkeypatch.setenv("HELD_MEDICATION_WRITES_ENABLED", "false")
    doctor = _authed_doctor()
    patient = _patient_for(doctor)
    row = PatientMedication(
        doctor_id=doctor.id,
        patient_id=patient.id,
        name="Synthetic existing medication",
        status=MedicationStatus(previous_status),
        dosage="Recorded dose",
        confirmed=True,
    ).save()
    before = row.model_dump()
    response = authenticated_client.patch(
        api_app.url_path_for("medication_update", medication_id=str(row.id)),
        json={"status": "held", "name": "Must not be assigned", "confirmed": False},
        headers=READER_HEADERS,
    )
    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json()["detail"]["code"] == "HELD_MEDICATION_WRITES_DISABLED"
    assert PatientMedication.one(id=row.id).model_dump() == before


def test_disabled_new_holds_keeps_old_create_values_and_public_control(
    authenticated_client: TestClient, monkeypatch
):
    monkeypatch.setenv("HELD_MEDICATION_WRITES_ENABLED", "false")
    doctor = _authed_doctor()
    patient = _patient_for(doctor)
    endpoint = api_app.url_path_for("medication_create", patient_id=str(patient.id))
    response = authenticated_client.post(
        endpoint,
        json={"name": "Synthetic hold", "status": "held"},
        headers=READER_HEADERS,
    )
    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json()["detail"]["code"] == "HELD_MEDICATION_WRITES_DISABLED"
    assert (
        PatientMedication.where(PatientMedication.patient_id == patient.id).count() == 0
    )
    for value in ("active", "stopped"):
        response = authenticated_client.post(
            endpoint, json={"name": "Synthetic " + value, "status": value}
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["status"] == value
    panel = api_app.url_path_for(
        "patient_reference_resources", patient_id=str(patient.id)
    )
    response = authenticated_client.get(panel)
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["held_medication_writes_enabled"] is False
    assert {
        item["medication"]["status"] for item in response.json()["medications"]
    } == {"active", "stopped"}
    monkeypatch.setenv("HELD_MEDICATION_WRITES_ENABLED", "true")
    assert (
        authenticated_client.get(panel).json()["held_medication_writes_enabled"] is True
    )


def test_disabled_new_holds_keeps_existing_hold_confirmation_edit_and_resume(
    authenticated_client: TestClient, monkeypatch
):
    monkeypatch.setenv("HELD_MEDICATION_WRITES_ENABLED", "false")
    doctor = _authed_doctor()
    patient = _patient_for(doctor)
    source = AppointmentFactory.build(doctor_id=doctor.id, patient_id=patient.id).save()
    row = PatientMedication(
        doctor_id=doctor.id,
        patient_id=patient.id,
        appointment_id=source.id,
        name="Synthetic held",
        status=MedicationStatus.held,
        source=MedicationSource.transcript,
    ).save()
    endpoint = api_app.url_path_for("medication_update", medication_id=str(row.id))
    for body in ({"status": "held", "dosage": "Recorded dose"}, {"confirmed": True}):
        response = authenticated_client.patch(
            endpoint, json=body, headers=READER_HEADERS
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["status"] == "held"
        assert response.json()["appointment_id"] == str(source.id)
    response = authenticated_client.get(
        api_app.url_path_for("medication_list", patient_id=str(patient.id)),
        headers=READER_HEADERS,
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.json()[0]["status"] == "held"
    confirmation = PatientMedication.one(id=row.id).confirmed_at
    response = authenticated_client.patch(
        endpoint, json={"status": "active"}, headers=READER_HEADERS
    )
    assert response.status_code == status.HTTP_200_OK
    current = PatientMedication.one(id=row.id)
    assert current.status == MedicationStatus.active and current.confirmed
    assert current.confirmed_at == confirmation and current.appointment_id == source.id
    assert (
        current.source == MedicationSource.transcript
        and current.dosage == "Recorded dose"
    )


def test_disabled_new_holds_still_checks_owner_and_reader_first(
    authenticated_client: TestClient, monkeypatch
):
    monkeypatch.setenv("HELD_MEDICATION_WRITES_ENABLED", "false")
    doctor = _authed_doctor()
    patient = _patient_for(doctor)
    response = authenticated_client.post(
        api_app.url_path_for("medication_create", patient_id=str(patient.id)),
        json={"name": "Synthetic hold", "status": "held"},
    )
    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json()["detail"]["code"] == "MEDICATION_READER_UPGRADE_REQUIRED"
    foreign = DoctorFactory.build().save()
    foreign_patient = _patient_for(foreign)
    row = PatientMedication(
        doctor_id=foreign.id, patient_id=foreign_patient.id, name="Synthetic foreign"
    ).save()
    before = row.model_dump()
    for headers in ({}, READER_HEADERS):
        response = authenticated_client.post(
            api_app.url_path_for(
                "medication_create", patient_id=str(foreign_patient.id)
            ),
            json={"name": "Synthetic hold", "status": "held"},
            headers=headers,
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND
        response = authenticated_client.patch(
            api_app.url_path_for("medication_update", medication_id=str(row.id)),
            json={"status": "held"},
            headers=headers,
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND
    assert PatientMedication.one(id=row.id).model_dump() == before


def _authed_doctor() -> Doctor:
    _, _, clerk_user = get_clerk_dev_user()
    return Doctor.find_or_create_by(clerk_id=clerk_user.id)


def _patient_for(doctor: Doctor) -> Patient:
    return PatientFactory.build(doctor_id=doctor.id).save()


def test_create_list_update_delete(authenticated_client: TestClient):
    doctor = _authed_doctor()
    patient = _patient_for(doctor)

    resp = authenticated_client.post(
        api_app.url_path_for("medication_create", patient_id=str(patient.id)),
        json={"name": "Metformin", "kind": "prescription", "dosage": "500mg"},
    )
    assert resp.status_code == status.HTTP_200_OK
    body = resp.json()
    assert body["name"] == "Metformin"
    assert body["confirmed"] is True  # manual rows are confirmed
    assert body["source"] == "manual"
    med_id = body["id"]

    resp = authenticated_client.get(
        api_app.url_path_for("medication_list", patient_id=str(patient.id))
    )
    assert resp.status_code == status.HTTP_200_OK
    assert [m["id"] for m in resp.json()] == [med_id]

    resp = authenticated_client.patch(
        api_app.url_path_for("medication_update", medication_id=med_id),
        json={"status": "stopped", "dosage": "discontinued"},
    )
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["status"] == "stopped"

    resp = authenticated_client.delete(
        api_app.url_path_for("medication_delete", medication_id=med_id)
    )
    assert resp.status_code == status.HTTP_204_NO_CONTENT

    resp = authenticated_client.get(
        api_app.url_path_for("medication_list", patient_id=str(patient.id))
    )
    assert resp.json() == []


def test_confirming_sets_confirmed_flag(authenticated_client: TestClient):
    doctor = _authed_doctor()
    patient = _patient_for(doctor)
    row = PatientMedication(
        doctor_id=doctor.id,
        patient_id=patient.id,
        name="metformin",
        normalized_name="metformin",
        source=MedicationSource.transcript,
        confirmed=False,
    ).save()

    resp = authenticated_client.patch(
        api_app.url_path_for("medication_update", medication_id=str(row.id)),
        json={"confirmed": True},
    )
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["confirmed"] is True

    row = PatientMedication.one(id=row.id)
    assert row.confirmed is True
    assert row.confirmed_at is not None


def test_other_doctors_patient_is_404(authenticated_client: TestClient):
    _authed_doctor()
    other_doctor = DoctorFactory.build().save()
    other_patient = _patient_for(other_doctor)

    resp = authenticated_client.get(
        api_app.url_path_for("medication_list", patient_id=str(other_patient.id))
    )
    assert resp.status_code == status.HTTP_404_NOT_FOUND


def test_extract_endpoint_queues_job(
    authenticated_client: TestClient, sync_celery, monkeypatch
):
    # The offline extraction stand-in fires only when PYTHON_ENV=test AND the
    # Azure OpenAI credentials are absent. CI has them, so this test was calling
    # the live gpt-4.1 deployment and failing whenever it hit a 429 — the point
    # here is that the endpoint queues the job, not that the model responds.
    monkeypatch.setattr(
        extract_medications_job, "should_use_test_openai_fallback", lambda: True
    )
    doctor = _authed_doctor()
    patient = _patient_for(doctor)
    appointment = AppointmentFactory.build(
        doctor_id=doctor.id, patient_id=patient.id, appointment_date=datetime.now()
    ).save()
    AIVisitTranscript(
        doctor_id=doctor.id,
        appointment_id=appointment.id,
        patient_id=patient.id,
        duration=60,
        transcript_data=[
            TranscriptEntry(
                speaker="Patient",
                content="I take lisinopril and ibuprofen.",
                startTime=0,
                endTime=1,
            )
        ],
    ).save()

    resp = authenticated_client.post(
        api_app.url_path_for("medication_extract", patient_id=str(patient.id)),
        json={"appointment_id": str(appointment.id)},
    )
    assert resp.status_code == status.HTTP_200_OK

    rows = cast(
        list[PatientMedication],
        list(PatientMedication.where(PatientMedication.patient_id == patient.id).all()),
    )
    names = {r.normalized_name for r in rows}
    assert "lisinopril" in names
    assert "ibuprofen" in names


def test_owner_hold_resume_preserves_source_and_other_confirmed_states(
    authenticated_client: TestClient,
):
    doctor = _authed_doctor()
    patient = _patient_for(doctor)
    source = AppointmentFactory.build(
        doctor_id=doctor.id,
        patient_id=patient.id,
        appointment_date=datetime(2026, 10, 31, 12, 0),
    ).save()
    confirmation = datetime(2026, 10, 31, 13, 0, tzinfo=timezone.utc)
    rows = [
        PatientMedication(
            doctor_id=doctor.id,
            patient_id=patient.id,
            appointment_id=source.id,
            name="Synthetic medication",
            status=value,
            source=MedicationSource.transcript,
            confirmed=True,
            confirmed_at=confirmation,
        ).save()
        for value in (
            MedicationStatus.active,
            MedicationStatus.stopped,
            MedicationStatus.held,
        )
    ]
    endpoint = api_app.url_path_for("medication_update", medication_id=str(rows[0].id))
    for target in ("held", "active"):
        response = authenticated_client.patch(
            endpoint, json={"status": target}, headers=READER_HEADERS
        )
        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert body["status"] == target and body["confirmed"] is True
        assert body["appointment_id"] == str(source.id)
        assert (
            datetime.fromisoformat(body["source_appointment_date"])
            == source.appointment_date
        )
        assert datetime.fromisoformat(body["confirmed_at"]) == confirmation
        assert body["list_updated_at"] is not None
        row = PatientMedication.one(id=rows[0].id)
        assert row.status.value == target and row.confirmed
        assert row.confirmed_at == confirmation
    assert PatientMedication.one(id=rows[1].id).status == MedicationStatus.stopped
    assert PatientMedication.one(id=rows[2].id).status == MedicationStatus.held
    response = authenticated_client.get(
        api_app.url_path_for("medication_list", patient_id=str(patient.id)),
        headers=READER_HEADERS,
    )
    assert response.status_code == status.HTTP_200_OK
    assert {m["id"] for m in response.json()} == {str(row.id) for row in rows}
    assert {m["status"] for m in response.json()} == {"active", "stopped", "held"}


def test_owner_confirms_held_suggestion_and_manual_held_entry(
    authenticated_client: TestClient,
):
    doctor = _authed_doctor()
    patient = _patient_for(doctor)
    row = PatientMedication(
        doctor_id=doctor.id,
        patient_id=patient.id,
        name="Synthetic metformin",
        source=MedicationSource.transcript,
        status=MedicationStatus.stopped,
    ).save()
    endpoint = api_app.url_path_for("medication_update", medication_id=str(row.id))
    response = authenticated_client.patch(
        endpoint, json={"status": "held"}, headers=READER_HEADERS
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["status"] == "held" and not response.json()["confirmed"]
    response = authenticated_client.patch(
        endpoint, json={"confirmed": True}, headers=READER_HEADERS
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["status"] == "held" and response.json()["confirmed"]
    assert response.json()["confirmed_at"] is not None
    response = authenticated_client.post(
        api_app.url_path_for("medication_create", patient_id=str(patient.id)),
        json={"name": "Synthetic held entry", "status": "held"},
        headers=READER_HEADERS,
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["status"] == "held" and response.json()["confirmed"]
    assert response.json()["source"] == "manual"


def test_foreign_hold_resume_and_create_are_denied(authenticated_client: TestClient):
    _authed_doctor()
    other = DoctorFactory.build().save()
    patient = _patient_for(other)
    row = PatientMedication(
        doctor_id=other.id,
        patient_id=patient.id,
        name="Synthetic",
        status=MedicationStatus.held,
        confirmed=True,
    ).save()
    endpoint = api_app.url_path_for("medication_update", medication_id=str(row.id))
    for target in ("active", "held"):
        response = authenticated_client.patch(
            endpoint, json={"status": target, "confirmed": True}, headers=READER_HEADERS
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND
    response = authenticated_client.post(
        api_app.url_path_for("medication_create", patient_id=str(patient.id)),
        json={"name": "Synthetic", "status": "held"},
        headers=READER_HEADERS,
    )
    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert PatientMedication.one(id=row.id).status == MedicationStatus.held
    assert (
        PatientMedication.where(PatientMedication.patient_id == patient.id).count() == 1
    )


READER_HEADERS = {"X-Cosmaneura-Medication-Reader": "held-v1"}


@pytest.mark.parametrize(
    "capability",
    [None, "unknown", "held-v1 "],
    ids=["missing", "unknown", "near-match"],
)
def test_legacy_reader_refuses_entire_held_representation(
    authenticated_client: TestClient, capability: str | None
):
    doctor = _authed_doctor()
    patient = _patient_for(doctor)
    rows = [
        PatientMedication(
            doctor_id=doctor.id,
            patient_id=patient.id,
            name="Synthetic " + value.value,
            status=value,
            confirmed=True,
        ).save()
        for value in MedicationStatus
    ]
    headers = (
        {} if capability is None else {"X-Cosmaneura-Medication-Reader": capability}
    )
    for route in (
        "medication_list",
        "patient_reference_resources",
        "medication_drug_reference",
        "medication_nutrient_depletions",
    ):
        response = authenticated_client.get(
            api_app.url_path_for(route, patient_id=str(patient.id)), headers=headers
        )
        assert response.status_code == status.HTTP_409_CONFLICT
        assert response.json() == {
            "detail": {
                "code": "MEDICATION_READER_UPGRADE_REQUIRED",
                "message": "This medication list requires an updated reader.",
            }
        }
        assert all(str(row.id) not in response.text for row in rows)
    response = authenticated_client.get(
        api_app.url_path_for("medication_list", patient_id=str(patient.id)),
        headers=READER_HEADERS,
    )
    assert response.status_code == status.HTTP_200_OK
    assert {row["id"]: row["status"] for row in response.json()} == {
        str(row.id): row.status.value for row in rows
    }
    response = authenticated_client.get(
        api_app.url_path_for("patient_reference_resources", patient_id=str(patient.id)),
        headers=READER_HEADERS,
    )
    assert response.status_code == status.HTTP_200_OK
    assert {
        item["medication"]["status"] for item in response.json()["medications"]
    } == {"active", "stopped", "held"}


def test_legacy_nonheld_reader_is_compatible_and_header_is_not_ownership(
    authenticated_client: TestClient,
):
    doctor = _authed_doctor()
    patient = _patient_for(doctor)
    rows = [
        PatientMedication(
            doctor_id=doctor.id,
            patient_id=patient.id,
            name="metformin",
            normalized_name="metformin",
            status=value,
            confirmed=True,
        ).save()
        for value in (MedicationStatus.active, MedicationStatus.stopped)
    ]
    other_patient = _patient_for(doctor)
    PatientMedication(
        doctor_id=doctor.id,
        patient_id=other_patient.id,
        name="Synthetic held elsewhere",
        status=MedicationStatus.held,
    ).save()
    deleted = PatientMedication(
        doctor_id=doctor.id,
        patient_id=patient.id,
        name="Synthetic deleted hold",
        status=MedicationStatus.held,
    )
    deleted.deleted_at = datetime.now()
    deleted.save()
    response = authenticated_client.get(
        api_app.url_path_for("medication_list", patient_id=str(patient.id))
    )
    assert response.status_code == status.HTTP_200_OK
    assert {item["id"] for item in response.json()} == {str(row.id) for row in rows}
    assert str(deleted.id) not in response.text
    DrugReference.find_or_create_by(normalized_name="metformin")
    for route in (
        "patient_reference_resources",
        "medication_drug_reference",
        "medication_nutrient_depletions",
    ):
        reference = authenticated_client.get(
            api_app.url_path_for(route, patient_id=str(patient.id))
        )
        assert reference.status_code == status.HTTP_200_OK
        entries = reference.json()["medications"]
        ids = {
            item["medication"]["id"]
            if route == "patient_reference_resources"
            else item["medication_id"]
            for item in entries
        }
        assert ids == {str(row.id) for row in rows}
    foreign = DoctorFactory.build().save()
    foreign_patient = _patient_for(foreign)
    foreign_row = PatientMedication(
        doctor_id=foreign.id,
        patient_id=foreign_patient.id,
        name="Synthetic foreign hold",
        status=MedicationStatus.held,
    ).save()
    for headers in ({}, READER_HEADERS):
        for route in ("medication_list", "patient_reference_resources"):
            response = authenticated_client.get(
                api_app.url_path_for(route, patient_id=str(foreign_patient.id)),
                headers=headers,
            )
            assert response.status_code == status.HTTP_404_NOT_FOUND
        endpoint = api_app.url_path_for(
            "medication_update", medication_id=str(foreign_row.id)
        )
        response = authenticated_client.patch(
            endpoint, json={"confirmed": True}, headers=headers
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND
        response = authenticated_client.delete(
            api_app.url_path_for(
                "medication_delete", medication_id=str(foreign_row.id)
            ),
            headers=headers,
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND
    assert PatientMedication.one(id=foreign_row.id).status == MedicationStatus.held


@pytest.mark.parametrize(
    "body",
    [
        {"status": "active"},
        {"confirmed": True},
        {"confirmed": False},
        {"name": "Changed"},
        {"dosage": "Changed"},
        None,
    ],
    ids=["status", "confirm", "unconfirm", "name", "dosage", "delete"],
)
def test_legacy_held_mutation_refuses_without_touching_row(
    authenticated_client: TestClient, body: dict | None
):
    doctor = _authed_doctor()
    patient = _patient_for(doctor)
    source = AppointmentFactory.build(doctor_id=doctor.id, patient_id=patient.id).save()
    row = PatientMedication(
        doctor_id=doctor.id,
        patient_id=patient.id,
        appointment_id=source.id,
        name="Synthetic held",
        status=MedicationStatus.held,
        source=MedicationSource.transcript,
        dosage="Recorded dose",
    ).save()
    before = row.model_dump()
    response = authenticated_client.request(
        "DELETE" if body is None else "PATCH",
        api_app.url_path_for(
            "medication_delete" if body is None else "medication_update",
            medication_id=str(row.id),
        ),
        json=body,
    )
    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json()["detail"]["code"] == "MEDICATION_READER_UPGRADE_REQUIRED"
    assert PatientMedication.one(id=row.id).model_dump() == before


def test_guarded_mutation_locks_owned_row_in_request_session(
    authenticated_client: TestClient,
):
    from activemodel.session_manager import get_engine
    from sqlalchemy import event

    statements = []

    def observe(_connection, _cursor, statement, _parameters, _context, _many):
        if "patient_medication" in statement and "FOR UPDATE" in statement:
            statements.append(statement)

    doctor = _authed_doctor()
    patient = _patient_for(doctor)
    row = PatientMedication(
        doctor_id=doctor.id, patient_id=patient.id, name="Synthetic"
    ).save()
    engine = get_engine()
    event.listen(engine, "before_cursor_execute", observe)
    try:
        response = authenticated_client.patch(
            api_app.url_path_for("medication_update", medication_id=str(row.id)),
            json={"dosage": "Changed"},
        )
        assert response.status_code == status.HTTP_200_OK
    finally:
        event.remove(engine, "before_cursor_execute", observe)
    assert len(statements) == 1
    assert "doctor_id" in statements[0] and "deleted_at IS NULL" in statements[0]
    assert PatientMedication.one(id=row.id).dosage == "Changed"


def test_locked_representation_check_refreshes_cached_active_target(
    authenticated_client: TestClient,
    monkeypatch,
):
    import app.routes.internal as internal_routes

    from activemodel.session_manager import get_session
    from sqlalchemy import update
    from sqlmodel import col

    doctor = _authed_doctor()
    patient = _patient_for(doctor)
    row = PatientMedication(
        doctor_id=doctor.id, patient_id=patient.id, name="Synthetic"
    ).save()
    original = internal_routes._owned_medication_or_404

    def changed_database_state(doctor, medication_id):
        cached = PatientMedication.one(id=medication_id, doctor_id=doctor.id)
        assert cached.status == MedicationStatus.active
        with get_session() as session:
            session.execute(
                update(PatientMedication)
                .where(col(PatientMedication.id) == medication_id)
                .values(status=MedicationStatus.held)
                .execution_options(synchronize_session=False)
            )
        assert cached.status == MedicationStatus.active
        return original(doctor, medication_id)

    monkeypatch.setattr(
        internal_routes, "_owned_medication_or_404", changed_database_state
    )
    response = authenticated_client.patch(
        api_app.url_path_for("medication_update", medication_id=str(row.id)),
        json={"status": "stopped", "name": "Must not overwrite hold"},
    )
    assert response.status_code == status.HTTP_409_CONFLICT
    # The refused request rolls its transaction back, including the synthetic
    # direct-SQL fixture change; the attempted user mutation is never persisted.
    assert PatientMedication.one(id=row.id).name == "Synthetic"


@pytest.mark.parametrize(
    "body",
    [
        {"status": "active"},
        {"confirmed": True},
        {"confirmed": False},
        {"name": "Old name"},
        {"dosage": "Old dose"},
        None,
    ],
    ids=["resume", "confirm", "dismiss", "name", "dosage", "delete"],
)
def test_old_active_snapshot_cannot_overwrite_new_owner_hold(
    authenticated_client: TestClient,
    body: dict | None,
):
    doctor = _authed_doctor()
    patient = _patient_for(doctor)
    source = AppointmentFactory.build(doctor_id=doctor.id, patient_id=patient.id).save()
    row = PatientMedication(
        doctor_id=doctor.id,
        patient_id=patient.id,
        appointment_id=source.id,
        name="Synthetic recorded",
        status=MedicationStatus.active,
        source=MedicationSource.transcript,
        dosage="Recorded dose",
        confirmed=False,
    ).save()
    old_read = authenticated_client.get(
        api_app.url_path_for("medication_list", patient_id=str(patient.id))
    )
    assert old_read.status_code == status.HTTP_200_OK
    assert old_read.json()[0]["status"] == "active"
    held = authenticated_client.patch(
        api_app.url_path_for("medication_update", medication_id=str(row.id)),
        json={"status": "held"},
        headers=READER_HEADERS,
    )
    assert held.status_code == status.HTTP_200_OK
    assert held.json()["status"] == "held" and not held.json()["confirmed"]
    before = PatientMedication.one(id=row.id).model_dump()
    response = authenticated_client.request(
        "DELETE" if body is None else "PATCH",
        api_app.url_path_for(
            "medication_delete" if body is None else "medication_update",
            medication_id=str(row.id),
        ),
        json=body,
    )
    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json()["detail"]["code"] == "MEDICATION_READER_UPGRADE_REQUIRED"
    assert PatientMedication.one(id=row.id).model_dump() == before
    old_read = authenticated_client.get(
        api_app.url_path_for("medication_list", patient_id=str(patient.id))
    )
    assert old_read.status_code == status.HTTP_409_CONFLICT
    fresh_read = authenticated_client.get(
        api_app.url_path_for("medication_list", patient_id=str(patient.id)),
        headers=READER_HEADERS,
    )
    assert fresh_read.status_code == status.HTTP_200_OK
    assert fresh_read.json()[0]["status"] == "held"


@pytest.mark.parametrize("capability", [None, "held-v2"], ids=["missing", "unknown"])
def test_held_producers_require_representation_capability_after_ownership(
    authenticated_client: TestClient,
    capability: str | None,
):
    doctor = _authed_doctor()
    patient = _patient_for(doctor)
    row = PatientMedication(
        doctor_id=doctor.id, patient_id=patient.id, name="Synthetic"
    ).save()
    before = row.model_dump()
    headers = (
        {} if capability is None else {"X-Cosmaneura-Medication-Reader": capability}
    )
    for method, route, body in (
        (
            "POST",
            api_app.url_path_for("medication_create", patient_id=str(patient.id)),
            {"name": "Synthetic hold", "status": "held"},
        ),
        (
            "PATCH",
            api_app.url_path_for("medication_update", medication_id=str(row.id)),
            {"status": "held"},
        ),
    ):
        response = authenticated_client.request(
            method, route, json=body, headers=headers
        )
        assert response.status_code == status.HTTP_409_CONFLICT
        assert response.json()["detail"]["code"] == "MEDICATION_READER_UPGRADE_REQUIRED"
    assert PatientMedication.one(id=row.id).model_dump() == before
    assert (
        PatientMedication.where(PatientMedication.patient_id == patient.id).count() == 1
    )
