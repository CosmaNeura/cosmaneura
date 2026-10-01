"""Real request/session/storage journey with owned PostgreSQL and Redis."""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

import app.routes.visit_prep_interviews as route
from app.routes.middleware import allowed_hosts
from app.server import api_app
from app.services.visit_preps import rotate_visit_prep_credential
from app.utils.encryption import decrypt_value

from activemodel.session_manager import get_session
from app.models.visit_prep import VisitPrep
from app.models.visit_prep_response import VisitPrepQuestionResponse
from sqlmodel import select

from tests.services.visit_prep_interviews_test import _fixture


@pytest.mark.parametrize("role", ["patient", "caregiver"])
def test_patient_http_journey_uses_real_domain_sessions_and_rate_limits(
    monkeypatch, owned_rate_limit_redis, role
):
    fixture = _fixture(monkeypatch, "http-journey", recipient_role=role)
    monkeypatch.setattr(
        route.rate_limits, "get_rate_limit_redis", lambda: owned_rate_limit_redis
    )
    client = TestClient(api_app, base_url=allowed_hosts(with_scheme=True)[0])
    headers = {"Authorization": f"Bearer {fixture.access_token}"}

    invited = client.get("/external/v1/visit-prep", headers=headers)
    assert invited.status_code == 200
    assert invited.json()["status"] == "confirmation_required"
    assert invited.json()["physician_display_name"] is None
    confirmed = client.post(
        "/external/v1/visit-prep/confirm",
        headers=headers,
        json={"first_name": "  JOSE\N{COMBINING ACUTE ACCENT}  ", "role": role},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["disposition"] == "confirmed"

    for key, answer in (
        ("reason", "A synthetic reason"),
        ("medications", None),
        ("adherence", "some"),
        ("adherence_barrier", "A synthetic barrier"),
        ("story", "My original synthetic story, in my own words."),
    ):
        resumed = client.get("/external/v1/visit-prep", headers=headers)
        assert resumed.status_code == 200
        assert resumed.json()["current_question"]["key"] == key
        if key == "story":
            assert resumed.json()["current_question"]["kind"] == "story"
            assert resumed.json()["current_question"]["choices"] == []
        response_headers = {**headers, "Idempotency-Key": str(uuid4())}
        payload = {
            "question_key": key,
            "response_kind": "skipped" if answer is None else "answered",
            "answer": answer,
        }
        accepted = client.post(
            "/external/v1/visit-prep/responses",
            headers=response_headers,
            json=payload,
        )
        replayed = client.post(
            "/external/v1/visit-prep/responses",
            headers=response_headers,
            json=payload,
        )
        assert accepted.status_code == replayed.status_code == 200
        assert accepted.json()["disposition"] == "accepted"
        assert replayed.json()["disposition"] == "replayed"
        if key == "reason":
            rotated = rotate_visit_prep_credential(
                owner_practice_member_id=fixture.owner.id,
                owner_doctor_id=fixture.doctor.id,
                practice_id=fixture.practice.id,
                visit_prep_id=fixture.prep_id,
                expected_credential_generation=1,
            )
            assert rotated.access_token is not None
            stale = client.get("/external/v1/visit-prep", headers=headers)
            assert stale.status_code == 404
            headers = {"Authorization": f"Bearer {rotated.access_token}"}
        for response in (resumed, accepted, replayed):
            assert "no-store" in response.headers["Cache-Control"]
            assert response.headers["Referrer-Policy"] == "no-referrer"
            assert fixture.access_token not in response.text

    completed = client.post("/external/v1/visit-prep/complete", headers=headers)
    repeated = client.post("/external/v1/visit-prep/complete", headers=headers)
    assert completed.status_code == repeated.status_code == 200
    assert completed.json()["interview"]["status"] == "completed"
    assert repeated.json()["disposition"] == "completed"
    with get_session() as session:
        responses = session.exec(
            select(VisitPrepQuestionResponse).where(
                VisitPrepQuestionResponse.visit_prep_id == fixture.prep_id
            )
        ).all()
    assert len(responses) == 5
    story = next(
        response for response in responses if response.submission_sequence == 5
    )
    assert story.answer_ciphertext is not None
    assert "My original synthetic story" not in story.answer_ciphertext
    assert "My original synthetic story, in my own words." in decrypt_value(
        story.answer_ciphertext
    )


@pytest.mark.parametrize("confirmed", [False, True])
def test_wrong_recipient_http_report_stops_disclosure_and_replays_only_acknowledgment(
    monkeypatch, owned_rate_limit_redis, confirmed
):
    fixture = _fixture(monkeypatch, "http-misdelivery")
    monkeypatch.setattr(
        route.rate_limits, "get_rate_limit_redis", lambda: owned_rate_limit_redis
    )
    client = TestClient(api_app, base_url=allowed_hosts(with_scheme=True)[0])
    headers = {"Authorization": f"Bearer {fixture.access_token}"}
    if confirmed:
        response = client.post(
            "/external/v1/visit-prep/confirm",
            headers=headers,
            json={
                "first_name": "Jos\N{LATIN SMALL LETTER E WITH ACUTE}",
                "role": "patient",
            },
        )
        assert response.status_code == 200
    for _ in range(2):
        report = client.post("/external/v1/visit-prep/wrong-recipient", headers=headers)
        assert report.status_code == 200
        assert report.json() == {"disposition": "reported"}
        assert "no-store" in report.headers["Cache-Control"]
        assert report.headers["Referrer-Policy"] == "no-referrer"
    resumed = client.get("/external/v1/visit-prep", headers=headers)
    assert resumed.status_code == 404
    assert resumed.json() == {"detail": "Visit Prep link is unavailable."}
    with get_session() as session:
        prep = session.exec(
            select(VisitPrep).where(VisitPrep.id == fixture.prep_id)
        ).one()
        assert prep.revoked_at is not None
        assert prep.lifecycle_state == ("misdirected" if confirmed else "invited")
