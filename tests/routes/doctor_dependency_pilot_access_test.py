import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException, status
from starlette.requests import Request
from starlette_context import request_cycle_context
from structlog.testing import capture_logs
from typeid import TypeID

from app.routes.dependencies.doctor import (
    ACCOUNT_RECOVERY_REQUIRED,
    MANAGED_ORGANIZATION_IDENTITY_ERROR,
    PILOT_IDENTITY_ERROR,
    inject_doctor_record,
)
from app.routes.dependencies.session_prep_account import (
    SESSION_PREP_ACTIVE_ACCOUNT_ROUTE_METHODS,
)

from app.models.doctor import DoctorRole


class FakeDoctor:
    def __init__(self, *, clerk_id: str, email: str) -> None:
        self.id = TypeID("doctor")
        self.clerk_id = clerk_id
        self.email = email
        self.role = DoctorRole.normal
        self.deleted_at: datetime | None = None
        self.last_active_at = None

    def save(self) -> None:
        return None


def _request(
    clerk_id: str,
    *,
    route_name: str = "unrelated_route",
    method: str = "GET",
) -> Request:
    request = Request(
        {
            "type": "http",
            "method": method,
            "path": "/internal/v1/settings",
            "headers": [],
            "session": {},
            "route": SimpleNamespace(name=route_name),
        }
    )
    request.state.auth_state = SimpleNamespace(payload={"sub": clerk_id})
    return request


def _grant(email: str) -> SimpleNamespace:
    return SimpleNamespace(email_snapshot=email)


@pytest.mark.parametrize(
    ("route_name", "method"),
    [
        (route_name, method)
        for route_name, methods in SESSION_PREP_ACTIVE_ACCOUNT_ROUTE_METHODS.items()
        for method in methods
    ],
)
def test_active_session_prep_account_ignores_historic_enrollment_tombstone(
    route_name: str,
    method: str,
) -> None:
    clerk_id = "user_active_session_prep_historic_tombstone"
    doctor = FakeDoctor(clerk_id=clerk_id, email="active-prep@example.test")

    with (
        request_cycle_context({}),
        patch(
            "app.routes.dependencies.doctor.session_prep_configuration."
            "SESSION_PREP_PROVIDER_SETTINGS",
            SimpleNamespace(enabled=True),
        ),
        patch(
            "app.routes.dependencies.doctor.Doctor.get",
            return_value=doctor,
        ) as get_doctor,
        patch(
            "app.routes.dependencies.doctor.revoked_pilot_access_grant"
        ) as revoked_grant,
        patch("app.routes.dependencies.doctor._active_grant_for_clerk") as active_grant,
        patch("app.routes.dependencies.doctor.Doctor.upsert") as upsert,
        patch(
            "app.routes.dependencies.doctor._requires_live_managed_identity"
        ) as managed_identity,
        patch(
            "app.routes.dependencies.doctor._doctors_for_normalized_email",
            return_value=[doctor],
        ),
        patch("app.routes.dependencies.doctor._fetch_clerk_email") as fetch_email,
        patch(
            "app.routes.dependencies.doctor.active_pilot_access_grant"
        ) as validate_grant,
    ):
        request = _request(clerk_id, route_name=route_name, method=method)
        inject_doctor_record(request)

    assert request.state.doctor is doctor
    get_doctor.assert_called_once_with(clerk_id=clerk_id)
    revoked_grant.assert_not_called()
    active_grant.assert_not_called()
    upsert.assert_not_called()
    managed_identity.assert_not_called()
    fetch_email.assert_not_called()
    validate_grant.assert_not_called()


def test_new_session_prep_account_is_created_when_no_tombstone_exists() -> None:
    clerk_id = "user_new_session_prep_account"
    doctor = FakeDoctor(clerk_id=clerk_id, email="new-prep@example.test")

    with (
        request_cycle_context({}),
        patch(
            "app.routes.dependencies.doctor.session_prep_configuration."
            "SESSION_PREP_PROVIDER_SETTINGS",
            SimpleNamespace(enabled=True),
        ),
        patch("app.routes.dependencies.doctor.Doctor.get", return_value=None),
        patch(
            "app.routes.dependencies.doctor.revoked_pilot_access_grant",
            return_value=None,
        ) as revoked_grant,
        patch("app.routes.dependencies.doctor._active_grant_for_clerk") as active_grant,
        patch(
            "app.routes.dependencies.doctor.Doctor.upsert",
            return_value=doctor,
        ) as upsert,
        patch(
            "app.routes.dependencies.doctor._doctors_for_normalized_email",
            return_value=[doctor],
        ),
    ):
        request = _request(clerk_id, route_name="settings_data")
        inject_doctor_record(request)

    assert request.state.doctor is doctor
    revoked_grant.assert_called_once_with(clerk_id)
    active_grant.assert_not_called()
    upsert.assert_called_once_with(data={"clerk_id": clerk_id}, unique_by="clerk_id")


@pytest.mark.parametrize("existing", [False, True])
def test_inactive_session_prep_account_cannot_be_recreated_or_reactivated(
    existing: bool,
) -> None:
    clerk_id = "user_inactive_session_prep_account"
    doctor = FakeDoctor(clerk_id=clerk_id, email="inactive-prep@example.test")
    if existing:
        doctor.deleted_at = datetime.now(UTC)

    with (
        request_cycle_context({}),
        patch(
            "app.routes.dependencies.doctor.session_prep_configuration."
            "SESSION_PREP_PROVIDER_SETTINGS",
            SimpleNamespace(enabled=True),
        ),
        patch(
            "app.routes.dependencies.doctor.Doctor.get",
            return_value=doctor if existing else None,
        ),
        patch(
            "app.routes.dependencies.doctor.revoked_pilot_access_grant",
            return_value=object(),
        ) as revoked_grant,
        patch("app.routes.dependencies.doctor.Doctor.upsert") as upsert,
        pytest.raises(HTTPException) as exc_info,
    ):
        inject_doctor_record(_request(clerk_id, route_name="settings_data"))

    assert exc_info.value.status_code == status.HTTP_410_GONE
    upsert.assert_not_called()
    if existing:
        revoked_grant.assert_not_called()
    else:
        revoked_grant.assert_called_once_with(clerk_id)


@pytest.mark.parametrize(
    ("route_name", "method", "enabled"),
    [
        ("generate_signed_visit_recording_upload_url", "POST", True),
        ("note_detail", "POST", True),
        ("view_as_status", "POST", True),
        ("end_view_as", "POST", True),
        ("settings_data", "GET", False),
    ],
)
def test_non_session_prep_or_disabled_route_keeps_pilot_tombstone(
    route_name: str,
    method: str,
    enabled: bool,
) -> None:
    clerk_id = "user_session_prep_closed_route_tombstone"

    with (
        request_cycle_context({}),
        patch(
            "app.routes.dependencies.doctor.session_prep_configuration."
            "SESSION_PREP_PROVIDER_SETTINGS",
            SimpleNamespace(enabled=enabled),
        ),
        patch("app.routes.dependencies.doctor.Doctor.get") as get_doctor,
        patch(
            "app.routes.dependencies.doctor.revoked_pilot_access_grant",
            return_value=object(),
        ),
        patch("app.routes.dependencies.doctor.Doctor.upsert") as upsert,
        pytest.raises(HTTPException) as exc_info,
    ):
        inject_doctor_record(_request(clerk_id, route_name=route_name, method=method))

    assert exc_info.value.status_code == status.HTTP_410_GONE
    get_doctor.assert_not_called()
    upsert.assert_not_called()


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


def test_roster_authorized_account_revalidates_live_identity() -> None:
    clerk_id = "user_managed_identity_match"
    email = "managed@mycatholicdoctor.com"
    doctor = FakeDoctor(clerk_id=clerk_id, email=email)

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
        patch(
            "app.routes.dependencies.doctor.PracticeMember.get",
            return_value=SimpleNamespace(
                organization_roster_approval_id="organization_roster_approval_private"
            ),
        ),
        patch(
            "app.routes.dependencies.doctor._fetch_clerk_email",
            return_value=email.upper(),
        ) as fetch_email,
    ):
        request = _request(clerk_id)
        inject_doctor_record(request)

    assert request.state.doctor is doctor
    fetch_email.assert_called_once_with(clerk_id)


@pytest.mark.parametrize("live_email", [None, "changed@example.test"])
def test_roster_authorized_account_fails_closed_on_live_identity_drift(
    live_email: str | None,
) -> None:
    clerk_id = "user_managed_identity_changed"
    approved_email = "managed@mycatholicdoctor.com"
    doctor = FakeDoctor(clerk_id=clerk_id, email=approved_email)

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
        patch(
            "app.routes.dependencies.doctor.PracticeMember.get",
            return_value=SimpleNamespace(
                organization_roster_approval_id="organization_roster_approval_private"
            ),
        ),
        patch(
            "app.routes.dependencies.doctor._fetch_clerk_email",
            return_value=live_email,
        ),
        capture_logs() as logs,
        pytest.raises(HTTPException) as exc_info,
    ):
        inject_doctor_record(_request(clerk_id))

    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
    assert exc_info.value.detail == MANAGED_ORGANIZATION_IDENTITY_ERROR
    serialized = json.dumps(logs, sort_keys=True)
    assert clerk_id not in serialized
    assert approved_email not in serialized
    assert live_email is None or live_email not in serialized
    assert logs[-1]["event_code"] == (
        "managed_organization_identity_verification_failed"
    )
    assert logs[-1]["clerk_ref"].startswith("hmac:")


def test_recreated_identity_gets_recovery_message_without_stealing_email() -> None:
    replacement_clerk_id = "user_recreated_identity"
    email = "returning-private@example.test"
    provisional = FakeDoctor(clerk_id=replacement_clerk_id, email="")
    existing = FakeDoctor(clerk_id="user_deleted_identity", email=email.upper())

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
        patch(
            "app.routes.dependencies.doctor._doctors_for_normalized_email",
            return_value=[existing],
        ),
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


def test_case_variant_legacy_shell_requires_recovery() -> None:
    replacement_clerk_id = "user_case_variant_shell"
    email = "returning-private@example.test"
    provisional = FakeDoctor(clerk_id=replacement_clerk_id, email=email.upper())
    existing = FakeDoctor(clerk_id="user_deleted_identity", email=email)

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
        patch(
            "app.routes.dependencies.doctor._doctors_for_normalized_email",
            return_value=[existing, provisional],
        ),
        patch("app.routes.dependencies.doctor._fetch_clerk_email") as fetch_email,
        pytest.raises(HTTPException) as exc_info,
    ):
        inject_doctor_record(_request(replacement_clerk_id))

    assert exc_info.value.status_code == status.HTTP_409_CONFLICT
    assert exc_info.value.detail == ACCOUNT_RECOVERY_REQUIRED
    fetch_email.assert_not_called()
