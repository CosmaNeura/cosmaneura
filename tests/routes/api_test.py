import hashlib
from datetime import datetime, timedelta, timezone

from fastapi import status
from fastapi.testclient import TestClient

from app.server import api_app

from app.models.pilot_access_grandfather_grant import PilotAccessGrandfatherGrant

from tests.factories import DoctorFactory
from tests.routes.utils import get_valid_token, provision_practice_in_force


def _grant_pilot_access(user, *, revoked: bool = False):
    now = datetime.now(timezone.utc)
    email = user.email.lower()
    return PilotAccessGrandfatherGrant(
        clerk_id=user.clerk_id,
        doctor_id_snapshot=user.id,
        email_snapshot=email,
        email_sha256=hashlib.sha256(email.encode()).hexdigest(),
        source_ref="test:external-api",
        source_sha256="a" * 64,
        grant_plan_sha256="b" * 64,
        granted_by_ref="test-suite",
        grant_reason="Test exact-account API access",
        granted_at=now - timedelta(minutes=1),
        revoked_at=now if revoked else None,
        revoked_by_ref="test-suite" if revoked else None,
        revocation_reason="Test API offboarding" if revoked else None,
        revocation_plan_sha256="c" * 64 if revoked else None,
        retain_until=now + timedelta(days=365 * 8) if revoked else None,
    ).save()


def test_unauthorized_no_credentials(client: TestClient):
    response = client.get(api_app.url_path_for("external_api_ping"))

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


def test_authorized_bad_credentials(client: TestClient):
    response = client.get(
        api_app.url_path_for("external_api_ping"),
        headers={"Authorization": "Bearer BAD_CREDS"},
    )

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


def test_authorized_no_bearer(client: TestClient):
    response = client.get(
        api_app.url_path_for("external_api_ping"),
        headers={"Authorization": "BAD_CREDS"},
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
    # TODO should allow factories to automatically persist API key and the model itself
    user = DoctorFactory.build()
    user.generate_api_key()
    user.save()
    provision_practice_in_force(user)

    response = client.get(
        api_app.url_path_for("external_api_ping"),
        headers={"Authorization": f"Bearer {user.api_key}"},
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"status": "ok"}


def test_grandfather_grant_cannot_bypass_practice_for_api_credentials(
    client: TestClient,
):
    user = DoctorFactory.build(email="grandfather-api@example.com")
    user.generate_api_key()
    user.save()
    _grant_pilot_access(user)

    response = client.get(
        api_app.url_path_for("external_api_ping"),
        headers={"Authorization": f"Bearer {user.api_key}"},
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN


def test_revoked_grandfather_grant_denies_api_credentials(client: TestClient):
    user = DoctorFactory.build(email="revoked-api@example.com")
    user.generate_api_key()
    user.save()
    _grant_pilot_access(user, revoked=True)

    response = client.get(
        api_app.url_path_for("external_api_ping"),
        headers={"Authorization": f"Bearer {user.api_key}"},
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN


def test_deleted_doctor_denies_api_credentials(client: TestClient):
    user = DoctorFactory.build(deleted_at=datetime.now(timezone.utc))
    user.generate_api_key()
    user.save()

    response = client.get(
        api_app.url_path_for("external_api_ping"),
        headers={"Authorization": f"Bearer {user.api_key}"},
    )

    assert response.status_code == status.HTTP_410_GONE
