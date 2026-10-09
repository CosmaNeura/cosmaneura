from fastapi import status
from fastapi.testclient import TestClient

from app.server import api_app

from app.models.doctor import Doctor

from tests.routes.utils import (
    get_clerk_dev_user,
    get_valid_token,
    provision_practice_in_force,
)


def test_unauthorized_no_credentials(client: TestClient):
    response = client.get(api_app.url_path_for("settings_data"))

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_authorized_bad_credentials(client: TestClient):
    response = client.get(
        api_app.url_path_for("settings_data"),
        headers={"Authorization": "Bearer BAD_CREDS"},
    )

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


def test_authorized_good_credentials(client: TestClient):
    "Use this test to get a mock of valid authorization credentials"

    # /notes sits behind the practice gate, so the dev doctor needs a
    # practice whose BAA is in force before the request
    _, _, clerk_user = get_clerk_dev_user()
    provision_practice_in_force(Doctor.find_or_create_by(clerk_id=clerk_user.id))

    token_id = get_valid_token()

    response = client.get(
        api_app.url_path_for("note_list"),
        headers={"Authorization": f"Bearer {token_id}"},
    )

    assert response.status_code == status.HTTP_200_OK
