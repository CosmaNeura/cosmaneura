from fastapi import status
from fastapi.testclient import TestClient

from app.server import api_app

from app.models.doctor import Doctor

from tests.factories import DoctorFactory
from tests.routes.utils import get_valid_token


def test_unauthorized_no_credentials(client: TestClient):
    response = client.get(api_app.url_path_for("external_api_ping"))

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


def test_authorized_bad_credentials(client: TestClient):
    response = client.get(
        api_app.url_path_for("external_api_ping"),
        headers={"Authorization": "Bearer BAD_CREDS"},
    )

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


def test_authorized_clerk_credentials(client: TestClient):
    token_id = get_valid_token()

    response = client.get(
        api_app.url_path_for("external_api_ping"),
        headers={"Authorization": f"Bearer {token_id}"},
    )

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


def test_authorized_api_credentials(client: TestClient):
    user = DoctorFactory()
    user.generate_api_key()

    response = client.get(
        api_app.url_path_for("external_api_ping"),
        headers={"Authorization": f"Bearer {user.api_key}"},
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"status": "ok"}
