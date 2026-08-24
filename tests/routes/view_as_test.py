from typing import Any, cast

from fastapi import Request, status
from fastapi.testclient import TestClient

from app.server import api_app

from app.models.doctor import Doctor
from app.models.superadmin_capability import (
    SuperadminCapabilityEnvironment,
    SuperadminCapabilityKind,
)

from tests.routes.utils import (
    MOCK_CLERK_ISSUER,
    get_clerk_dev_user,
    grant_test_superadmin_capability,
    provision_practice_in_force,
)


def _staging_environment(monkeypatch) -> None:
    def staging():
        return SuperadminCapabilityEnvironment.staging

    monkeypatch.setattr(
        "app.routes.dependencies.view_as.configured_superadmin_capability_environment",
        staging,
    )
    monkeypatch.setattr(
        "app.routes.view_as.configured_superadmin_capability_environment",
        staging,
    )
    monkeypatch.setattr(
        "app.services.superadmin_capability.configured_superadmin_capability_environment",
        staging,
    )
    monkeypatch.setattr(
        "app.services.superadmin_capability.configured_superadmin_clerk_issuer",
        lambda: MOCK_CLERK_ISSUER,
    )


def test_view_as_requires_explicit_capability_even_for_a_role_admin(
    authenticated_client: TestClient,
    monkeypatch,
):
    _staging_environment(monkeypatch)
    response = authenticated_client.get(api_app.url_path_for("view_as_targets"))
    assert response.status_code == status.HTTP_403_FORBIDDEN


def test_view_as_reads_target_but_blocks_target_and_actor_mutations(
    authenticated_client: TestClient,
    monkeypatch,
):
    _staging_environment(monkeypatch)
    _, _, clerk_user = get_clerk_dev_user()
    actor = Doctor.get(clerk_id=clerk_user.id)
    assert actor is not None
    grant_test_superadmin_capability(actor, SuperadminCapabilityKind.view_as)

    target = Doctor(
        clerk_id="user_view_as_route_target",
        email="view-as-route-target@example.net",
        biography="",
    ).save()
    provision_practice_in_force(target)

    initial = authenticated_client.get(api_app.url_path_for("view_as_status"))
    assert initial.status_code == status.HTTP_200_OK
    assert initial.json()["can_view_as"] is True
    assert initial.json()["active"] is False

    began = authenticated_client.post(
        api_app.url_path_for("begin_view_as"),
        json={"target_doctor_id": str(target.id)},
    )
    assert began.status_code == status.HTTP_200_OK, began.text
    assert began.json()["active"] is True
    assert began.json()["read_only"] is True
    assert began.json()["target"]["doctor_id"] == str(target.id)

    read = authenticated_client.get(api_app.url_path_for("settings_data"))
    assert read.status_code == status.HTTP_200_OK, read.text
    assert read.json()["doctor_id"] == str(target.id)

    target_write = authenticated_client.post(
        api_app.url_path_for("complete_onboarding")
    )
    assert target_write.status_code == status.HTTP_403_FORBIDDEN

    actor_admin_write = authenticated_client.post(
        api_app.url_path_for("webhook_inbox_redrive", delivery_id="unknown")
    )
    assert actor_admin_write.status_code == status.HTTP_403_FORBIDDEN

    ended = authenticated_client.delete(api_app.url_path_for("end_view_as"))
    assert ended.status_code == status.HTTP_200_OK
    assert ended.json()["active"] is False

    actor_read = authenticated_client.get(api_app.url_path_for("settings_data"))
    assert actor_read.status_code == status.HTTP_200_OK
    assert actor_read.json()["doctor_id"] == str(actor.id)


def test_unready_target_returns_diagnostic_not_onboarding_forms(
    authenticated_client: TestClient,
    monkeypatch,
):
    _staging_environment(monkeypatch)
    _, _, clerk_user = get_clerk_dev_user()
    actor = Doctor.get(clerk_id=clerk_user.id)
    assert actor is not None
    grant_test_superadmin_capability(actor, SuperadminCapabilityKind.view_as)
    target = Doctor(
        clerk_id="user_view_as_pending_target",
        email="view-as-pending-target@example.net",
        biography="",
    ).save()

    began = authenticated_client.post(
        api_app.url_path_for("begin_view_as"),
        json={"target_doctor_id": str(target.id)},
    )
    assert began.status_code == status.HTTP_200_OK, began.text
    assert began.json()["diagnostic_code"] == "target_access_pending"

    status_response = authenticated_client.get(api_app.url_path_for("view_as_status"))
    assert status_response.status_code == status.HTTP_200_OK
    assert status_response.json()["diagnostic_code"] == "target_access_pending"

    for route_name in ("settings_data", "practice_status", "baa_current"):
        response = authenticated_client.get(api_app.url_path_for(route_name))
        assert response.status_code == status.HTTP_409_CONFLICT


def test_stale_view_as_cookie_cannot_fall_through_to_actor_write(
    authenticated_client: TestClient,
    monkeypatch,
):
    _staging_environment(monkeypatch)
    _, _, clerk_user = get_clerk_dev_user()
    actor = Doctor.get(clerk_id=clerk_user.id)
    assert actor is not None
    grant_test_superadmin_capability(actor, SuperadminCapabilityKind.view_as)
    target = Doctor(
        clerk_id="user_view_as_stale_target",
        email="view-as-stale-target@example.net",
        biography="",
    ).save()
    provision_practice_in_force(target)

    began = authenticated_client.post(
        api_app.url_path_for("begin_view_as"),
        json={"target_doctor_id": str(target.id)},
    )
    assert began.status_code == status.HTTP_200_OK

    # A new Clerk session id invalidates the support session. The first unsafe
    # request is rejected instead of silently writing as the real actor.
    from app.routes.internal import authenticate_clerk_request_middleware

    from tests.routes.utils import MockAuthenticateRequest

    replacement = MockAuthenticateRequest(cast(Any, clerk_user))
    original_call = replacement.__call__

    async def changed_session(request: Request):
        state = await original_call(request)
        assert state.payload is not None
        state.payload["sid"] = "sess_reauthenticated"
        return state

    api_app.dependency_overrides[authenticate_clerk_request_middleware] = (
        changed_session
    )
    blocked = authenticated_client.post(api_app.url_path_for("complete_onboarding"))
    assert blocked.status_code == status.HTTP_409_CONFLICT
