import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException, status
from starlette.requests import Request
from starlette_context import request_cycle_context
from structlog.testing import capture_logs

from app.routes.dependencies.doctor import (
    ACCOUNT_RECOVERY_REQUIRED,
    PILOT_IDENTITY_ERROR,
    inject_doctor_record,
)

from app.models.doctor import DoctorRole


class FakeDoctor:
    def __init__(self, *, clerk_id: str, email: str) -> None:
        self.id = "doctor-private-sentinel"
        self.clerk_id = clerk_id
        self.email = email
        self.role = DoctorRole.normal
        self.deleted_at = None
        self.last_active_at = None

    def save(self) -> None:
        return None


def _request(clerk_id: str) -> Request:
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/internal/v1/settings",
            "headers": [],
            "session": {},
        }
    )
    request.state.auth_state = SimpleNamespace(payload={"sub": clerk_id})
    return request


def _grant(email: str) -> SimpleNamespace:
    return SimpleNamespace(email_snapshot=email)


def test_active_grant_revalidates_matching_live_verified_primary_email() -> None:
    clerk_id = "user_pilot_identity_match"
    email = "approved-pilot@example.test"
    doctor = FakeDoctor(clerk_id=clerk_id, email=email)
    grant = _grant(email)

    with (
        request_cycle_context({}),
        patch(
            "app.routes.dependencies.doctor.revoked_pilot_access_grant",
            return_value=None,
        ),
        patch(
            "app.routes.dependencies.doctor._active_grant_for_clerk",
            side_effect=[grant, grant],
        ),
        patch("app.routes.dependencies.doctor.Doctor.upsert", return_value=doctor),
        patch(
            "app.routes.dependencies.doctor._fetch_clerk_email",
            return_value=email.upper(),
        ) as fetch_email,
        patch(
            "app.routes.dependencies.doctor.active_pilot_access_grant",
            return_value=grant,
        ),
    ):
        request = _request(clerk_id)
        inject_doctor_record(request)

    assert request.state.doctor is doctor
    fetch_email.assert_called_once_with(clerk_id)


@pytest.mark.parametrize("live_email", [None, "changed-private@example.test"])
def test_active_grant_fails_closed_when_live_identity_does_not_match(
    live_email: str | None,
) -> None:
    clerk_id = "user_pilot_identity_changed"
    approved_email = "approved-private@example.test"
    doctor = FakeDoctor(clerk_id=clerk_id, email=approved_email)
    grant = _grant(approved_email)

    with (
        request_cycle_context({}),
        patch(
            "app.routes.dependencies.doctor.revoked_pilot_access_grant",
            return_value=None,
        ),
        patch(
            "app.routes.dependencies.doctor._active_grant_for_clerk",
            side_effect=[grant, grant],
        ),
        patch("app.routes.dependencies.doctor.Doctor.upsert", return_value=doctor),
        patch(
            "app.routes.dependencies.doctor._fetch_clerk_email",
            return_value=live_email,
        ),
        capture_logs() as logs,
        pytest.raises(HTTPException) as exc_info,
    ):
        inject_doctor_record(_request(clerk_id))

    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
    assert exc_info.value.detail == PILOT_IDENTITY_ERROR
    serialized = json.dumps(logs, sort_keys=True)
    assert clerk_id not in serialized
    assert approved_email not in serialized
    assert live_email is None or live_email not in serialized
    assert logs[-1]["event_code"] == "pilot_access_identity_verification_failed"
    assert logs[-1]["clerk_ref"].startswith("hmac:")


def test_non_grant_account_does_not_incur_live_clerk_email_lookup() -> None:
    clerk_id = "user_ordinary_account"
    doctor = FakeDoctor(clerk_id=clerk_id, email="ordinary@example.test")

    with (
        request_cycle_context({}),
        patch(
            "app.routes.dependencies.doctor.revoked_pilot_access_grant",
            return_value=None,
        ),
        patch(
            "app.routes.dependencies.doctor._active_grant_for_clerk",
            return_value=None,
        ),
        patch("app.routes.dependencies.doctor.Doctor.upsert", return_value=doctor),
        patch("app.routes.dependencies.doctor._fetch_clerk_email") as fetch_email,
        patch(
            "app.routes.dependencies.doctor.active_pilot_access_grant"
        ) as active_grant,
    ):
        inject_doctor_record(_request(clerk_id))

    fetch_email.assert_not_called()
    active_grant.assert_not_called()


def test_recreated_identity_gets_recovery_message_without_stealing_email() -> None:
    replacement_clerk_id = "user_recreated_identity"
    email = "returning-private@example.test"
    provisional = FakeDoctor(clerk_id=replacement_clerk_id, email="")
    existing = FakeDoctor(clerk_id="user_deleted_identity", email=email)
    existing.id = "existing-doctor-private-sentinel"

    with (
        request_cycle_context({}),
        patch(
            "app.routes.dependencies.doctor.revoked_pilot_access_grant",
            return_value=None,
        ),
        patch(
            "app.routes.dependencies.doctor._active_grant_for_clerk",
            return_value=None,
        ),
        patch("app.routes.dependencies.doctor.Doctor.upsert", return_value=provisional),
        patch("app.routes.dependencies.doctor.Doctor.get", return_value=existing),
        patch(
            "app.routes.dependencies.doctor._fetch_clerk_email",
            return_value=email,
        ),
        capture_logs() as logs,
        pytest.raises(HTTPException) as exc_info,
    ):
        inject_doctor_record(_request(replacement_clerk_id))

    assert exc_info.value.status_code == status.HTTP_409_CONFLICT
    assert exc_info.value.detail == ACCOUNT_RECOVERY_REQUIRED
    assert provisional.email == ""
    serialized = json.dumps(logs, sort_keys=True)
    assert replacement_clerk_id not in serialized
    assert email not in serialized
    assert logs[-1]["event_code"] == "account_identity_recovery_required"
