from fastapi import status
from fastapi.testclient import TestClient

from app.server import api_app

from tests.routes.utils import get_valid_token


def test_unauthorized_no_credentials(client: TestClient):
    response = client.get(api_app.url_path_for("settings_data"))

    assert response.status_code == status.HTTP_403_FORBIDDEN


def test_authorized_bad_credentials(client: TestClient):
    response = client.get(
        api_app.url_path_for("settings_data"),
        headers={"Authorization": "Bearer BAD_CREDS"},
    )

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


def test_authorized_good_credentials(client: TestClient):
    "Use this test to get a mock of valid authorization credentials"

    token_id = get_valid_token()

    response = client.get(
        api_app.url_path_for("note_list"),
        headers={"Authorization": f"Bearer {token_id}"},
    )

    assert response.status_code == status.HTTP_200_OK
