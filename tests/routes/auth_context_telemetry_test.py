import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException, status
from starlette.requests import Request
from starlette_context import context, request_cycle_context
from structlog.testing import capture_logs
from typeid import TypeID

from app.routes.api import authenticate_api_request_middleware
from app.routes.dependencies.doctor import _fetch_clerk_email, inject_doctor_record
from app.routes.dependencies.view_as import (
    SESSION_KEY_VIEW_AS_ID,
    SESSION_KEY_VIEW_AS_NONCE,
    apply_view_as,
)
from app.services.view_as import ResolvedViewAs, ViewAsResolution

from app.models.doctor import API_KEY_PREFIX, DoctorRole


class FakeDoctor:
    def __init__(
        self,
        *,
        doctor_id: str,
        clerk_id: str,
        email: str,
        role: DoctorRole = DoctorRole.normal,
    ) -> None:
        self.id = doctor_id
        self.clerk_id = clerk_id
        self.email = email
        self.role = role
        self.deleted_at = None
        self.last_active_at = None

    def save(self) -> None:
        return None


def _request(*, clerk_id: str, session: dict | None = None) -> Request:
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/internal/v1/settings",
            "headers": [],
            "session": session or {},
        }
    )
    request.state.auth_state = SimpleNamespace(
        payload={
            "iss": "https://test.clerk.invalid",
            "sub": clerk_id,
            "sid": f"session_{clerk_id}",
        }
    )
    return request


def test_doctor_dependency_keeps_raw_identity_out_of_context_and_sentry() -> None:
    doctor = FakeDoctor(
        doctor_id="doctor-private-sentinel",
        clerk_id="clerk-private-sentinel",
        email="private-doctor@example.test",
    )
    request = _request(clerk_id=doctor.clerk_id)

    with (
        request_cycle_context({}),
        patch("app.routes.dependencies.doctor.Doctor.upsert", return_value=doctor),
        patch("app.routes.dependencies.doctor.sentry_sdk.set_user") as set_user,
    ):
        inject_doctor_record(request)
        context_snapshot = dict(context)

    assert request.state.doctor is doctor
    assert set(context_snapshot) == {"doctor_ref"}
    assert context_snapshot["doctor_ref"].startswith("hmac:")
    assert "doctor-private-sentinel" not in json.dumps(context_snapshot)
    set_user.assert_called_once_with({"id": context_snapshot["doctor_ref"]})


def test_doctor_dependency_blocks_grandfather_tombstone_before_upsert() -> None:
    request = _request(clerk_id="user_revoked_before_first_login")

    with (
        patch(
            "app.routes.dependencies.doctor.revoked_pilot_access_grant",
            return_value=object(),
        ),
        patch("app.routes.dependencies.doctor.Doctor.upsert") as upsert,
        pytest.raises(HTTPException) as exc_info,
    ):
        inject_doctor_record(request)

    assert exc_info.value.status_code == status.HTTP_410_GONE
    upsert.assert_not_called()


def test_clerk_lookup_failure_omits_identifier_and_exception_details() -> None:
    clerk_id = "clerk-private-sentinel"
    provider_error = "https://clerk.invalid/private?email=person@example.test"

    with (
        patch(
            "app.routes.dependencies.doctor.verified_primary_email",
            side_effect=RuntimeError(provider_error),
        ),
        capture_logs() as logs,
    ):
        assert _fetch_clerk_email(clerk_id) is None

    serialized = json.dumps(logs, sort_keys=True)
    assert clerk_id not in serialized
    assert provider_error not in serialized
    assert "person@example.test" not in serialized
    assert logs[-1]["event_code"] == "clerk_email_lookup_failed"
    assert logs[-1]["clerk_ref"].startswith("hmac:")


def test_view_as_context_and_sentry_use_only_pseudonymous_references() -> None:
    admin = FakeDoctor(
        doctor_id="admin-doctor-private-sentinel",
        clerk_id="admin-clerk-private-sentinel",
        email="private-admin@example.test",
        role=DoctorRole.admin,
    )
    target = FakeDoctor(
        doctor_id="target-doctor-private-sentinel",
        clerk_id="target-clerk-private-sentinel",
        email="private-target@example.test",
    )
    request = _request(
        clerk_id=admin.clerk_id,
        session={
            SESSION_KEY_VIEW_AS_ID: "view_as_session_01k2a3b4c5d6e7f8g9h0j1k2m3",
            SESSION_KEY_VIEW_AS_NONCE: "private-browser-nonce",
        },
    )
    request.state.doctor = admin
    active = ResolvedViewAs(
        session=SimpleNamespace(absolute_expires_at=None),
        actor=admin,
        target=target,
        diagnostic_code=None,
    )

    with (
        request_cycle_context({"doctor_ref": "prior-admin-ref"}),
        patch(
            "app.routes.dependencies.view_as.configured_superadmin_capability_environment",
            return_value=SimpleNamespace(value="staging"),
        ),
        patch(
            "app.routes.dependencies.view_as.resolve_view_as",
            return_value=ViewAsResolution(active, False, False),
        ),
        patch("app.routes.dependencies.view_as.sentry_sdk.set_extra") as set_extra,
        patch("app.routes.dependencies.view_as.sentry_sdk.set_user") as set_user,
        capture_logs() as logs,
    ):
        apply_view_as(request)
        context_snapshot = dict(context)

    assert request.state.view_as_actor is admin
    assert request.state.doctor is target
    assert set(context_snapshot) == {"doctor_ref", "view_as_actor_ref"}
    assert context_snapshot["doctor_ref"].startswith("hmac:")
    assert context_snapshot["view_as_actor_ref"].startswith("hmac:")
    assert (
        context_snapshot["doctor_ref"] != context_snapshot["view_as_actor_ref"]
    )

    serialized = json.dumps([context_snapshot, *logs], sort_keys=True)
    for private_value in (
        admin.id,
        admin.clerk_id,
        admin.email,
        target.id,
        target.clerk_id,
        target.email,
    ):
        assert private_value not in serialized

    set_extra.assert_called_once_with(
        "view_as_actor_ref", context_snapshot["view_as_actor_ref"]
    )
    set_user.assert_called_once_with({"id": context_snapshot["doctor_ref"]})


def test_stale_view_as_state_is_cleared_without_logging_identifiers() -> None:
    stale_session_id = "view_as_session_private-sentinel"
    private_nonce = "private-browser-nonce"
    admin = FakeDoctor(
        doctor_id="admin-doctor-private-sentinel",
        clerk_id="admin-clerk-private-sentinel",
        email="private-admin@example.test",
        role=DoctorRole.admin,
    )
    request = _request(
        clerk_id=admin.clerk_id,
        session={
            SESSION_KEY_VIEW_AS_ID: stale_session_id,
            SESSION_KEY_VIEW_AS_NONCE: private_nonce,
        },
    )
    request.state.doctor = admin

    with (
        request_cycle_context({"doctor_ref": "prior-admin-ref"}),
        patch(
            "app.routes.dependencies.view_as.configured_superadmin_capability_environment",
            return_value=SimpleNamespace(value="staging"),
        ),
        patch(
            "app.routes.dependencies.view_as.resolve_view_as",
            return_value=ViewAsResolution(None, True, False),
        ),
        capture_logs() as logs,
    ):
        apply_view_as(request)

    serialized = json.dumps(logs, sort_keys=True)
    assert stale_session_id not in serialized
    assert private_nonce not in serialized
    assert SESSION_KEY_VIEW_AS_ID not in request.session
    assert SESSION_KEY_VIEW_AS_NONCE not in request.session


def test_api_auth_context_and_sentry_use_only_pseudonymous_reference() -> None:
    api_user = FakeDoctor(
        doctor_id="api-doctor-private-sentinel",
        clerk_id="api-clerk-private-sentinel",
        email="private-api-user@example.test",
    )
    request = _request(clerk_id=api_user.clerk_id)
    token = str(TypeID(API_KEY_PREFIX))

    with (
        request_cycle_context({}),
        patch("app.routes.api.Doctor.get", return_value=api_user),
        patch("app.routes.api.doctor_access_in_force", return_value=True),
        patch("app.routes.api.sentry_sdk.set_user") as set_user,
        patch("app.routes.api.sentry_sdk.set_extra") as set_extra,
    ):
        authenticate_api_request_middleware(request, token)
        context_snapshot = dict(context)

    assert request.state.api_user is api_user
    assert set(context_snapshot) == {"api_user_ref"}
    assert context_snapshot["api_user_ref"].startswith("hmac:")

    serialized = json.dumps(context_snapshot, sort_keys=True)
    for private_value in (api_user.id, api_user.clerk_id, api_user.email, token):
        assert private_value not in serialized

    set_user.assert_called_once_with({"id": context_snapshot["api_user_ref"]})
    set_extra.assert_called_once_with("api_user", True)
