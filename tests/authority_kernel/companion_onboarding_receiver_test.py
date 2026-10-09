"""Both new human-session APIs install with the existing authentication gate."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routes import companion_appointment_setup, companion_encounters


@pytest.mark.parametrize("route", [companion_encounters, companion_appointment_setup])
def test_missing_human_session_is_rejected_before_processing(route):
    app = FastAPI()
    route.install(app)
    response = TestClient(app).post(
        route.PATH,
        content=b"private invented payload",
        headers={
            "X-Cosmaneura-Record-Version": "1",
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 401
    assert "private invented payload" not in response.text
    assert response.headers["cache-control"] == "no-store"
