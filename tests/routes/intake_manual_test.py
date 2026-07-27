from datetime import datetime, timedelta, timezone

from fastapi import status
from fastapi.testclient import TestClient

import app.jobs.intake.send_link
from app.server import api_app

from app.models.appointment import Appointment
from app.models.doctor import Doctor
from app.models.intake.intake import Intake
from app.models.patient import Patient

from tests.factories import (
    AppointmentFactory,
    DoctorFactory,
    SimpleIncompleteIntakeFactory,
)
from tests.routes.utils import assert_status, get_clerk_dev_user


def _authed_doctor() -> Doctor:
    _, _, clerk_user = get_clerk_dev_user()
    return Doctor.find_or_create_by(clerk_id=clerk_user.id)


def _capture_mail(monkeypatch) -> list[dict]:
    "capture intake link emails instead of hitting SMTP"
    sent: list[dict] = []

    def fake_mail(to, subject, template_path, context=None, **kwargs):
        sent.append(
            {
                "to": to,
                "subject": subject,
                "template_path": template_path,
                "context": context or {},
            }
        )

    monkeypatch.setattr(app.jobs.intake.send_link, "mail", fake_mail)
    return sent


def _future_appointment_iso(days: int = 7) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


def test_create_manual_intake_and_send(
    authenticated_client: TestClient, sync_celery, monkeypatch
):
    doctor = _authed_doctor()
    sent = _capture_mail(monkeypatch)

    response = authenticated_client.post(
        api_app.url_path_for("create_manual_intake"),
        json={
            "patient_name": "  Ada Lovelace ",
            "date_of_birth": "1990-12-10",
            "email": "ada@example.com",
            "appointment_date": _future_appointment_iso(),
        },
    )

    assert_status(response)
    body = response.json()
    assert body["email_sent_to"] == "ada@example.com"

    intake = Intake.one(id=body["intake_id"])
    assert str(intake.doctor_id) == str(doctor.id)
    assert body["intake_url"] == str(intake.public_url)

    appointment = Appointment.one(id=body["appointment_id"])
    assert appointment.created_by_system == "gui"
    assert appointment.external_id is None

    patient = Patient.one(id=appointment.patient_id)
    assert patient.name == "Ada Lovelace"
    assert patient.email == "ada@example.com"

    # question generation ran (sync celery) even without external intake data
    intake.refresh()
    assert not intake.is_processing()
    assert len(intake.intake_questions) >= 1
    assert intake.cleaned_external_intake_data is not None

    # the link email was rendered and sent to the patient
    assert len(sent) == 1
    assert sent[0]["to"] == "ada@example.com"
    assert sent[0]["context"]["intake_url"] == str(intake.public_url)


def test_create_manual_intake_without_send(
    authenticated_client: TestClient, sync_celery, monkeypatch
):
    _authed_doctor()
    sent = _capture_mail(monkeypatch)

    response = authenticated_client.post(
        api_app.url_path_for("create_manual_intake"),
        json={
            "patient_name": "Grace Hopper",
            "date_of_birth": "1985-01-02",
            "email": "grace@example.com",
            "appointment_date": _future_appointment_iso(),
            "send_email": False,
        },
    )

    assert_status(response)
    assert response.json()["email_sent_to"] is None
    assert sent == []


def test_create_manual_intake_rejects_past_appointment(
    authenticated_client: TestClient,
):
    _authed_doctor()

    response = authenticated_client.post(
        api_app.url_path_for("create_manual_intake"),
        json={
            "patient_name": "Old Appointment",
            "date_of_birth": "1985-01-02",
            "email": "old@example.com",
            "appointment_date": (
                datetime.now(timezone.utc) - timedelta(days=1)
            ).isoformat(),
        },
    )

    assert_status(response, status.HTTP_422_UNPROCESSABLE_ENTITY)


def test_create_manual_intake_reuses_existing_patient(
    authenticated_client: TestClient, sync_celery, monkeypatch
):
    doctor = _authed_doctor()
    _capture_mail(monkeypatch)

    payload = {
        "patient_name": "Repeat Visitor",
        "date_of_birth": "1970-05-05",
        "email": "first@example.com",
        "appointment_date": _future_appointment_iso(),
    }

    assert_status(
        authenticated_client.post(
            api_app.url_path_for("create_manual_intake"), json=payload
        )
    )

    payload["email"] = "updated@example.com"
    payload["appointment_date"] = _future_appointment_iso(days=14)
    assert_status(
        authenticated_client.post(
            api_app.url_path_for("create_manual_intake"), json=payload
        )
    )

    patients = list(
        Patient.where(
            (Patient.doctor_id == doctor.id) & (Patient.name == "Repeat Visitor")
        ).all()
    )
    assert len(patients) == 1
    assert patients[0].email == "updated@example.com"


def test_send_intake_link_uses_stored_email(
    authenticated_client: TestClient, sync_celery, monkeypatch
):
    doctor = _authed_doctor()
    sent = _capture_mail(monkeypatch)

    intake = SimpleIncompleteIntakeFactory.build(doctor_id=doctor.id).save()
    appointment = Appointment.one(id=intake.appointment_id)
    patient = Patient.one(id=appointment.patient_id)
    patient.email = "stored@example.com"
    patient.save()

    response = authenticated_client.post(
        api_app.url_path_for("send_intake_link", appointment_id=appointment.id),
        json={},
    )

    assert_status(response)
    assert response.json()["email_sent_to"] == "stored@example.com"
    assert len(sent) == 1
    assert sent[0]["to"] == "stored@example.com"


def test_send_intake_link_with_new_email_persists(
    authenticated_client: TestClient, sync_celery, monkeypatch
):
    doctor = _authed_doctor()
    sent = _capture_mail(monkeypatch)

    intake = SimpleIncompleteIntakeFactory.build(doctor_id=doctor.id).save()
    appointment = Appointment.one(id=intake.appointment_id)

    response = authenticated_client.post(
        api_app.url_path_for("send_intake_link", appointment_id=appointment.id),
        json={"email": "typed-in@example.com"},
    )

    assert_status(response)
    assert response.json()["email_sent_to"] == "typed-in@example.com"
    assert len(sent) == 1

    patient = Patient.one(id=appointment.patient_id)
    assert patient.email == "typed-in@example.com"


def test_send_intake_link_without_any_email(authenticated_client: TestClient):
    doctor = _authed_doctor()

    intake = SimpleIncompleteIntakeFactory.build(doctor_id=doctor.id).save()

    response = authenticated_client.post(
        api_app.url_path_for("send_intake_link", appointment_id=intake.appointment_id),
        json={},
    )

    assert_status(response, status.HTTP_422_UNPROCESSABLE_ENTITY)


def test_send_intake_link_scoped_to_doctor(authenticated_client: TestClient):
    _authed_doctor()

    other_doctor = DoctorFactory.build().save()
    intake = SimpleIncompleteIntakeFactory.build(doctor_id=other_doctor.id).save()

    response = authenticated_client.post(
        api_app.url_path_for("send_intake_link", appointment_id=intake.appointment_id),
        json={"email": "attacker@example.com"},
    )

    assert_status(response, status.HTTP_404_NOT_FOUND)


def test_send_intake_link_expired_appointment(authenticated_client: TestClient):
    doctor = _authed_doctor()

    appointment = AppointmentFactory.build(
        doctor_id=doctor.id,
        appointment_date=AppointmentFactory.__faker__.date_between(
            start_date="-90d", end_date="-1d"
        ),
    ).save()
    SimpleIncompleteIntakeFactory.build(
        doctor_id=doctor.id, appointment_id=appointment.id
    ).save()

    response = authenticated_client.post(
        api_app.url_path_for("send_intake_link", appointment_id=appointment.id),
        json={"email": "late@example.com"},
    )

    assert_status(response, status.HTTP_422_UNPROCESSABLE_ENTITY)
