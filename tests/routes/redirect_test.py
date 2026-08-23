from fastapi import status
from fastapi.testclient import TestClient

from app.server import api_app

from tests.routes.utils import base_server_url


def test_https_redirection():
    """
    Assumption is upstream proxy server will handle HTTPS redirection
    """

    client = TestClient(
        api_app, base_url=base_server_url("http"), follow_redirects=False
    )

    # The SPA route is intentionally absent when a local frontend build does
    # not exist. Exercise an always-mounted route so this transport policy
    # test does not depend on generated assets.
    response = client.get(api_app.url_path_for("healthcheck"))

    assert response.status_code == status.HTTP_200_OK
