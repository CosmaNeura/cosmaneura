import json
from types import SimpleNamespace
from unittest.mock import patch

from starlette.requests import Request
from starlette_context import context, request_cycle_context
from structlog.testing import capture_logs
from typeid import TypeID

from app.routes.admin import SESSION_KEY_LOGIN_AS_USER
from app.routes.api import authenticate_api_request_middleware
from app.routes.dependencies.doctor import _fetch_clerk_email, inject_doctor_record
from app.routes.dependencies.login_as import login_as

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
    request.state.auth_state = SimpleNamespace(payload={"sub": clerk_id})
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


def test_clerk_lookup_failure_omits_identifier_and_exception_details() -> None:
    clerk_id = "clerk-private-sentinel"
    provider_error = "https://clerk.invalid/private?email=person@example.test"

    with (
        patch(
            "app.routes.dependencies.doctor.clerk.users.get",
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


def test_login_as_context_and_sentry_use_only_pseudonymous_references() -> None:
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
        session={SESSION_KEY_LOGIN_AS_USER: target.clerk_id},
    )
    request.state.doctor = admin

    with (
        request_cycle_context({"doctor_ref": "prior-admin-ref"}),
        patch("app.routes.dependencies.login_as.Doctor.get", return_value=target),
        patch("app.routes.dependencies.login_as.sentry_sdk.set_extra") as set_extra,
        patch("app.routes.dependencies.login_as.sentry_sdk.set_user") as set_user,
        capture_logs() as logs,
    ):
        login_as(request)
        context_snapshot = dict(context)

    assert request.state.admin_user is admin
    assert request.state.doctor is target
    assert set(context_snapshot) == {"doctor_ref", "admin_ref"}
    assert context_snapshot["doctor_ref"].startswith("hmac:")
    assert context_snapshot["admin_ref"].startswith("hmac:")
    assert context_snapshot["doctor_ref"] != context_snapshot["admin_ref"]

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

    set_extra.assert_called_once_with("admin_ref", context_snapshot["admin_ref"])
    set_user.assert_called_once_with({"id": context_snapshot["doctor_ref"]})


def test_login_as_missing_user_log_omits_clerk_identifier() -> None:
    missing_clerk_id = "missing-clerk-private-sentinel"
    admin = FakeDoctor(
        doctor_id="admin-doctor-private-sentinel",
        clerk_id="admin-clerk-private-sentinel",
        email="private-admin@example.test",
        role=DoctorRole.admin,
    )
    request = _request(
        clerk_id=admin.clerk_id,
        session={SESSION_KEY_LOGIN_AS_USER: missing_clerk_id},
    )
    request.state.doctor = admin

    with (
        request_cycle_context({"doctor_ref": "prior-admin-ref"}),
        patch("app.routes.dependencies.login_as.Doctor.get", return_value=None),
        capture_logs() as logs,
    ):
        login_as(request)

    serialized = json.dumps(logs, sort_keys=True)
    assert missing_clerk_id not in serialized
    assert logs[-1]["event_code"] == "login_as_user_not_found"
    assert logs[-1]["clerk_ref"].startswith("hmac:")


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
