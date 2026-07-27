from fastapi import status
from fastapi.testclient import TestClient

from app.server import api_app

from app.models.doctor import Doctor

from tests.factories import DoctorFactory
from tests.routes.utils import assert_status, get_clerk_dev_user

AGGREGATE_INSIGHTS = (
    "Mostly adults 40-65, plain-language communicators, high interest in "
    "preventive care; many manage chronic conditions and prefer short, "
    "concrete questions."
)


def _authed_doctor() -> Doctor:
    _, _, clerk_user = get_clerk_dev_user()
    return Doctor.find_or_create_by(clerk_id=clerk_user.id)


def test_update_and_read_population_insights(authenticated_client: TestClient):
    doctor = _authed_doctor()

    response = authenticated_client.put(
        api_app.url_path_for("update_population_insights"),
        json={"insights": f"  {AGGREGATE_INSIGHTS}  "},
    )

    assert_status(response)
    assert response.json() == AGGREGATE_INSIGHTS

    doctor.refresh()
    assert doctor.population_insights == AGGREGATE_INSIGHTS

    settings = authenticated_client.get(api_app.url_path_for("settings_data"))
    assert_status(settings)
    assert settings.json()["population_insights"] == AGGREGATE_INSIGHTS


def test_clearing_population_insights(authenticated_client: TestClient):
    doctor = _authed_doctor()
    doctor.population_insights = AGGREGATE_INSIGHTS
    doctor.save()

    response = authenticated_client.put(
        api_app.url_path_for("update_population_insights"),
        json={"insights": None},
    )

    assert_status(response)
    assert response.json() is None

    doctor.refresh()
    assert doctor.population_insights is None


def test_rejects_overlong_population_insights(authenticated_client: TestClient):
    _authed_doctor()

    response = authenticated_client.put(
        api_app.url_path_for("update_population_insights"),
        json={"insights": "x" * 4001},
    )

    assert_status(response, status.HTTP_422_UNPROCESSABLE_ENTITY)


def test_external_agent_can_push_population_insights(client: TestClient):
    "the separate synthesis agent updates the aggregate description via the API"
    doctor = DoctorFactory.build().save()
    doctor.generate_api_key()
    doctor.save()

    response = client.put(
        api_app.url_path_for("external_update_population_insights"),
        headers={"Authorization": f"Bearer {doctor.api_key}"},
        json={"insights": AGGREGATE_INSIGHTS},
    )

    assert_status(response)
    assert response.json() == {"insights": AGGREGATE_INSIGHTS}

    doctor.refresh()
    assert doctor.population_insights == AGGREGATE_INSIGHTS


def test_external_population_insights_requires_auth(client: TestClient):
    response = client.put(
        api_app.url_path_for("external_update_population_insights"),
        json={"insights": AGGREGATE_INSIGHTS},
    )

    assert_status(response, status.HTTP_401_UNAUTHORIZED)
