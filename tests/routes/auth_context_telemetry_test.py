import json
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, cast
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
    LEGACY_SESSION_KEY_LOGIN_AS_USER,
    SESSION_KEY_VIEW_AS_ID,
    SESSION_KEY_VIEW_AS_NONCE,
    apply_view_as,
)
from app.services.superadmin_capability import SuperadminCapabilityError
from app.services.view_as import (
    ResolvedViewAs,
    ViewAsActorIdentity,
    ViewAsResolution,
    begin_view_as,
)

from activemodel.session_manager import get_session
from app.models.doctor import API_KEY_PREFIX, Doctor, DoctorRole
from app.models.superadmin_capability import (
    SuperadminCapabilityEnvironment,
    SuperadminCapabilityKind,
)
from app.models.view_as import ViewAsSession

from tests.routes.utils import grant_test_superadmin_capability


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


def _request(
    *,
    clerk_id: str,
    session: dict | None = None,
    method: str = "GET",
    request_binding: str = "inactive",
    clerk_session_id: str | None = None,
) -> Request:
    request = Request(
        {
            "type": "http",
            "method": method,
            "path": "/internal/v1/settings",
            "headers": [
                (
                    b"x-cosmaneura-view-as-binding",
                    request_binding.encode("ascii"),
                )
            ],
            "session": session or {},
        }
    )
    request.state.auth_state = SimpleNamespace(
        payload={
            "iss": "https://test.clerk.invalid",
            "sub": clerk_id,
            "sid": clerk_session_id or f"session_{clerk_id}",
        }
    )
    return request


def test_doctor_dependency_keeps_raw_identity_out_of_context_and_sentry() -> None:
    private_doctor_id = str(TypeID("doctor"))
    doctor = FakeDoctor(
        doctor_id=private_doctor_id,
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
    assert private_doctor_id not in json.dumps(context_snapshot)
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
        request_binding="vab1." + "a" * 64,
    )
    request.state.doctor = admin
    active = ResolvedViewAs(
        session=cast(Any, SimpleNamespace(absolute_expires_at=None)),
        actor=cast(Any, admin),
        target=cast(Any, target),
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
        patch(
            "app.routes.dependencies.view_as.view_as_request_binding",
            return_value="vab1." + "a" * 64,
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
    assert context_snapshot["doctor_ref"] != context_snapshot["view_as_actor_ref"]

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
        pytest.raises(HTTPException) as exc_info,
    ):
        apply_view_as(request)

    assert exc_info.value.status_code == status.HTTP_409_CONFLICT
    assert cast(dict[str, str], exc_info.value.detail)["code"] == (
        "VIEW_AS_BINDING_STALE"
    )
    serialized = json.dumps(logs, sort_keys=True)
    assert stale_session_id not in serialized
    assert private_nonce not in serialized
    assert SESSION_KEY_VIEW_AS_ID not in request.session
    assert SESSION_KEY_VIEW_AS_NONCE not in request.session


def test_view_as_configuration_failure_blocks_cookie_less_mutation() -> None:
    now = datetime.now(timezone.utc)
    actor = Doctor(
        clerk_id="user_config_failure_actor",
        email="config-failure-actor@example.test",
        biography="",
    ).save()
    target = Doctor(
        clerk_id="user_config_failure_target",
        email="config-failure-target@example.test",
        biography="",
    ).save()
    grant_test_superadmin_capability(
        actor,
        SuperadminCapabilityKind.view_as,
        issuer="https://test.clerk.invalid",
    )
    identity = ViewAsActorIdentity(
        issuer="https://test.clerk.invalid",
        subject=actor.clerk_id,
        clerk_session_id=f"session_{actor.clerk_id}",
    )
    with (
        patch(
            "app.services.view_as.verified_primary_email",
            return_value=target.email,
        ),
        get_session() as session,
    ):
        begin_view_as(
            session,
            actor=actor,
            target_doctor_id=target.id,
            identity=identity,
            environment=SuperadminCapabilityEnvironment.staging,
            now=now,
        )

    for clerk_session_id in (
        identity.clerk_session_id,
        "session_reauthenticated_during_config_failure",
    ):
        request = _request(
            clerk_id=actor.clerk_id,
            method="POST",
            clerk_session_id=clerk_session_id,
        )
        request.state.doctor = actor
        with (
            patch(
                "app.routes.dependencies.view_as.configured_superadmin_capability_environment",
                side_effect=SuperadminCapabilityError("private configuration detail"),
            ),
            pytest.raises(HTTPException) as exc_info,
        ):
            apply_view_as(request)

        assert exc_info.value.status_code == status.HTTP_409_CONFLICT
        assert cast(dict[str, str], exc_info.value.detail)["code"] == (
            "VIEW_AS_BINDING_STALE"
        )
        assert ViewAsSession.get(actor_doctor_id=actor.id, ended_at=None) is not None


def test_view_as_configuration_failure_does_not_block_ordinary_mutation() -> None:
    actor = Doctor(
        clerk_id="user_config_failure_ordinary",
        email="config-failure-ordinary@example.test",
        biography="",
    ).save()
    request = _request(clerk_id=actor.clerk_id, method="POST")
    request.state.doctor = actor

    with patch(
        "app.routes.dependencies.view_as.configured_superadmin_capability_environment",
        side_effect=SuperadminCapabilityError("private configuration detail"),
    ):
        apply_view_as(request)

    assert request.state.doctor is actor
    assert request.state.view_as is None


def test_legacy_view_as_state_cannot_reach_an_unsafe_actor_mutation() -> None:
    actor = FakeDoctor(
        doctor_id=str(TypeID("doctor")),
        clerk_id="user_legacy_view_as_actor",
        email="legacy-view-as-actor@example.test",
    )
    request = _request(
        clerk_id=actor.clerk_id,
        method="POST",
        session={LEGACY_SESSION_KEY_LOGIN_AS_USER: "private-legacy-target"},
    )
    request.state.doctor = actor

    with (
        patch(
            "app.routes.dependencies.view_as.configured_superadmin_capability_environment",
            side_effect=SuperadminCapabilityError("private configuration detail"),
        ),
        pytest.raises(HTTPException) as exc_info,
    ):
        apply_view_as(request)

    assert exc_info.value.status_code == status.HTTP_409_CONFLICT
    assert cast(dict[str, str], exc_info.value.detail)["code"] == (
        "VIEW_AS_BINDING_STALE"
    )
    assert LEGACY_SESSION_KEY_LOGIN_AS_USER not in request.session


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
