import hashlib
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from app.server import api_app

from app.models.doctor import Doctor
from app.models.pilot_access_grandfather_grant import PilotAccessGrandfatherGrant
from app.models.practice import PracticeMember

from tests.routes.utils import get_clerk_dev_user


def test_existing_grandfathered_user_reaches_notes_without_practice_or_baa(
    bare_authenticated_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise the incident path through Clerk binding and the practice gate."""

    _, _, clerk_user = get_clerk_dev_user()
    doctor = Doctor.get(clerk_id=clerk_user.id)
    assert doctor is not None
    assert doctor.email
    assert PracticeMember.get(doctor_id=doctor.id) is None

    doctor.onboarding_completed_at = datetime.now(timezone.utc) - timedelta(days=1)
    doctor.save()
    normalized_email = doctor.email.strip().lower()
    PilotAccessGrandfatherGrant(
        clerk_id=doctor.clerk_id,
        doctor_id_snapshot=doctor.id,
        practice_id_snapshot=None,
        email_snapshot=normalized_email,
        email_sha256=hashlib.sha256(normalized_email.encode()).hexdigest(),
        source_ref="test:legacy-pilot-entry",
        source_sha256="a" * 64,
        grant_plan_sha256="b" * 64,
        granted_by_ref="test-suite",
        grant_reason="Verify existing-user interactive entry",
        granted_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    ).save()
    monkeypatch.setattr(
        "app.routes.dependencies.doctor.verified_primary_email",
        lambda _clerk_id: normalized_email,
    )

    response = bare_authenticated_client.get(api_app.url_path_for("note_list"))

    assert response.status_code == status.HTTP_200_OK
    assert PracticeMember.get(doctor_id=doctor.id) is None
