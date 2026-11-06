"""Phone-sized synthetic link journey: copyable HTTPS → interview → package."""
from __future__ import annotations
from datetime import UTC, datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo
import pytest
from fastapi import status
from fastapi.testclient import TestClient
from app.configuration import visit_prep as visit_prep_config
from app.configuration.visit_prep import VisitPrepSettings
from app.models.doctor import Doctor
from app.models.practice import PracticeMember
from app.server import api_app
from app.services.visit_prep_questions import ensure_default_question_set
from tests.routes.utils import base_server_url, get_clerk_dev_user, provision_practice_in_force
PHONE_VIEWPORT = (390, 844)
ANSWERS = {
    "reason": "Synthetic check-in about sleep and focus.",
    "medications": "Synthetic vitamin D 1000 IU daily",
    "adherence": "yes", "adherence_barrier": "n/a",
    "story": "Synthetic health story for phone journey coverage.",
}
def _enable(monkeypatch: pytest.MonkeyPatch, doctor_id: str) -> VisitPrepSettings:
    settings = VisitPrepSettings(
        enabled=True, public_api_enabled=True, require_synthetic_recipients=True,
        enabled_doctor_ids=frozenset({doctor_id}), email_send_enabled=False, sms_send_enabled=False,
    )
    monkeypatch.setattr(visit_prep_config, "VISIT_PREP_SETTINGS", settings)
    for mod in ("app.routes.visit_prep_core", "app.services.visit_prep_access", "app.services.visit_prep_journey"):
        monkeypatch.setattr(f"{mod}.VISIT_PREP_SETTINGS", settings)
    return settings
@pytest.mark.parametrize("width,height", [PHONE_VIEWPORT])
def test_phone_sized_synthetic_link_journey(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch, width: int, height: int,
):
    assert (width, height) == PHONE_VIEWPORT
    _, _, clerk_user = get_clerk_dev_user()
    doctor = Doctor.get(clerk_id=clerk_user.id)
    assert doctor is not None
    provision_practice_in_force(doctor)
    member = PracticeMember.get(doctor_id=doctor.id)
    assert member is not None
    _enable(monkeypatch, str(doctor.id))
    default = ensure_default_question_set(owner_practice_member_id=member.id)
    assert [q.key for q in default.questions] == ["reason", "medications", "adherence", "adherence_barrier", "story"]
    listed = authenticated_client.get(api_app.url_path_for("visit_prep_question_set_list"))
    assert listed.status_code == status.HTTP_200_OK and listed.json()["question_sets"]
    scheduled = datetime.now(UTC).astimezone(ZoneInfo("UTC")) + timedelta(days=3)
    create_body = {
        "idempotency_key": str(uuid4()),
        "recipient": {"first_name": "Synthetic", "role": "patient", "email": "journey@example.com", "phone": "+15555550123"},
        "physician_display_name": "Dr Synthetic Journey",
        "scheduled_at": scheduled.isoformat(), "timezone_name": "UTC",
        "question_set_version_id": str(default.id),
    }
    create = authenticated_client.post(api_app.url_path_for("visit_prep_create"), json=create_body)
    assert create.status_code == status.HTTP_200_OK, create.text
    body = create.json()
    assert body["access_url"].startswith("https://") and "/visit-prep#access_token=vp1_" in body["access_url"]
    assert body["can_send_email"] is False and body["can_send_sms"] is False
    assert body["email_send_enabled"] is False and body["sms_send_enabled"] is False
    token, prep_id = body["access_token"], body["id"]
    assert token and token.startswith("vp1_")
    rejected = authenticated_client.post(
        api_app.url_path_for("visit_prep_create"),
        json={**create_body, "idempotency_key": str(uuid4()),
              "recipient": {"first_name": "NotSynthetic", "role": "patient", "email": "person@real-hospital.org"}},
    )
    assert rejected.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    public = TestClient(api_app, base_url=base_server_url())
    headers = {"Authorization": f"Bearer {token}"}
    state = public.get("/external/v1/visit-prep", headers=headers)
    assert state.status_code == status.HTTP_200_OK and state.json()["status"] == "confirmation_required"
    confirmed = public.post(
        "/external/v1/visit-prep/confirm", headers=headers,
        json={"first_name": "Synthetic", "role": "patient"},
    )
    assert confirmed.status_code == status.HTTP_200_OK, confirmed.text
    interview = confirmed.json()["interview"]
    assert interview["status"] == "in_progress" and interview["current_question"]["key"] == "reason"
    while interview["status"] == "in_progress":
        question = interview["current_question"]
        assert question is not None
        key = question["key"]
        answer = "yes" if key == "adherence" else ANSWERS[key]  # yes skips barrier
        response = public.post(
            "/external/v1/visit-prep/responses",
            headers={**headers, "Idempotency-Key": str(uuid4())},
            json={"question_key": key, "response_kind": "answered", "answer": answer},
        )
        assert response.status_code == status.HTTP_200_OK, response.text
        interview = response.json()["interview"]
    assert interview["status"] == "ready_to_complete"
    completed = public.post("/external/v1/visit-prep/complete", headers=headers)
    assert completed.status_code == status.HTTP_200_OK
    assert completed.json()["interview"]["status"] == "completed"
    package = authenticated_client.get(api_app.url_path_for("visit_prep_package", prep_id=prep_id))
    assert package.status_code == status.HTTP_200_OK, package.text
    pkg = package.json()
    assert pkg["contract"] == "visit_prep.patient_sources.v1"
    assert pkg["raw_answers_always_available"] is True
    topics = {t["question_key"]: t for t in pkg["topics"]}
    assert topics["reason"]["answer"] == ANSWERS["reason"] and topics["reason"]["recorded_at"]
    assert topics["story"]["answer"] == ANSWERS["story"] and topics["story"]["recorded_at"]
    assert pkg["synthesis"]["status"] == "ready" and pkg["synthesis"]["brief"]["headline"]["text"]
    assert pkg["topics"], "raw verbatim topics must remain"
    dark = VisitPrepSettings(enabled=True, public_api_enabled=False, enabled_doctor_ids=frozenset({str(doctor.id)}))
    monkeypatch.setattr("app.routes.visit_prep_core.VISIT_PREP_SETTINGS", dark)
    assert public.get("/external/v1/visit-prep", headers=headers).status_code == status.HTTP_404_NOT_FOUND
