from fastapi import status
from fastapi.testclient import TestClient

from app.server import api_app

from app.models.doctor import Doctor

from tests.routes.utils import assert_status, get_clerk_dev_user


def _authed_doctor() -> Doctor:
    _, _, clerk_user = get_clerk_dev_user()
    return Doctor.find_or_create_by(clerk_id=clerk_user.id)


def test_update_and_read_intake_questions(authenticated_client: TestClient):
    doctor = _authed_doctor()

    response = authenticated_client.put(
        api_app.url_path_for("update_intake_questions"),
        json={
            "questions": [
                "  What brings you in today? ",
                "What medications are you taking?",
                "Any allergies?",
            ]
        },
    )

    assert_status(response)
    assert response.json() == [
        "What brings you in today?",
        "What medications are you taking?",
        "Any allergies?",
    ]

    doctor.refresh()
    assert doctor.custom_intake_questions == [
        "What brings you in today?",
        "What medications are you taking?",
        "Any allergies?",
    ]

    settings = authenticated_client.get(api_app.url_path_for("settings_data"))
    assert_status(settings)
    assert settings.json()["custom_intake_questions"] == [
        "What brings you in today?",
        "What medications are you taking?",
        "Any allergies?",
    ]


def test_clearing_intake_questions(authenticated_client: TestClient):
    doctor = _authed_doctor()
    doctor.custom_intake_questions = ["Old question?"]
    doctor.save()

    response = authenticated_client.put(
        api_app.url_path_for("update_intake_questions"),
        json={"questions": []},
    )

    assert_status(response)
    assert response.json() == []

    doctor.refresh()
    # cleared means "revert to AI-generated questions"
    assert doctor.custom_intake_questions is None


def test_rejects_more_than_five_questions(authenticated_client: TestClient):
    _authed_doctor()

    response = authenticated_client.put(
        api_app.url_path_for("update_intake_questions"),
        json={"questions": [f"Question {n}?" for n in range(6)]},
    )

    assert_status(response, status.HTTP_422_UNPROCESSABLE_ENTITY)


def test_rejects_duplicate_questions(authenticated_client: TestClient):
    _authed_doctor()

    response = authenticated_client.put(
        api_app.url_path_for("update_intake_questions"),
        json={"questions": ["Any allergies?", "any allergies?"]},
    )

    assert_status(response, status.HTTP_422_UNPROCESSABLE_ENTITY)


def test_rejects_empty_questions(authenticated_client: TestClient):
    _authed_doctor()

    response = authenticated_client.put(
        api_app.url_path_for("update_intake_questions"),
        json={"questions": ["   "]},
    )

    assert_status(response, status.HTTP_422_UNPROCESSABLE_ENTITY)
