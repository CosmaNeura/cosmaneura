from typing import Any, cast

from fastapi import Request, status
from fastapi.testclient import TestClient

import app.services.authority
from app.configuration.authority import AuthorityKernelSettings
from app.routes.embed_context import (
    VerifiedClerkIdentity,
    authenticate_embed_platform_session,
    get_authority_kernel_settings,
)
from app.server import api_app

from app.models.doctor import Doctor
from app.models.superadmin_capability import (
    SuperadminCapabilityEnvironment,
    SuperadminCapabilityKind,
)
from app.models.view_as import ViewAsAuditEvent, ViewAsSession

from tests.factories import SimpleIncompleteIntakeFactory
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
        "app.routes.embed_context.configured_superadmin_capability_environment",
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

    def current_email(clerk_id: str) -> str | None:
        doctor = Doctor.get(clerk_id=clerk_id)
        return doctor.email.strip().lower() if doctor and doctor.email else None

    monkeypatch.setattr(
        "app.services.view_as.verified_primary_email",
        current_email,
    )


def test_view_as_requires_explicit_capability_even_for_a_role_admin(
    authenticated_client: TestClient,
    monkeypatch,
):
    _staging_environment(monkeypatch)
    response = authenticated_client.get(api_app.url_path_for("view_as_targets"))
    assert response.status_code == status.HTTP_403_FORBIDDEN

    malformed_begin = authenticated_client.post(
        api_app.url_path_for("begin_view_as"),
        json={"target_doctor_id": "not-a-doctor-id"},
    )
    assert malformed_begin.status_code == status.HTTP_403_FORBIDDEN
    assert ViewAsAuditEvent.count() == 0

    valid_target = Doctor(
        clerk_id="user_view_as_unauthorized_target",
        email="view-as-unauthorized-target@example.net",
        biography="",
    ).save()
    denied_begin = authenticated_client.post(
        api_app.url_path_for("begin_view_as"),
        json={"target_doctor_id": str(valid_target.id)},
    )
    assert denied_begin.status_code == status.HTTP_403_FORBIDDEN
    assert ViewAsAuditEvent.count() == 0


def test_view_as_target_list_never_performs_live_clerk_fanout(
    authenticated_client: TestClient,
    monkeypatch,
):
    _staging_environment(monkeypatch)
    _, _, clerk_user = get_clerk_dev_user()
    actor = Doctor.get(clerk_id=clerk_user.id)
    assert actor is not None
    grant_test_superadmin_capability(actor, SuperadminCapabilityKind.view_as)
    Doctor(
        clerk_id="user_view_as_local_list_target",
        email="view-as-local-list-target@example.net",
        biography="",
    ).save()

    def unexpected_live_lookup(_clerk_id: str) -> None:
        raise AssertionError("target enumeration must remain local-only")

    monkeypatch.setattr(
        "app.services.view_as.verified_primary_email",
        unexpected_live_lookup,
    )
    response = authenticated_client.get(api_app.url_path_for("view_as_targets"))

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["targets"]


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
    target_practice = provision_practice_in_force(target)
    target_practice.is_solo = False
    target_practice.save()
    target_intake = SimpleIncompleteIntakeFactory.build(doctor_id=target.id).save()

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
    request_binding = began.json()["request_binding"]
    assert request_binding.startswith("vab1.")
    assert len(request_binding) == len("vab1.") + 64

    stale_tab = authenticated_client.get(api_app.url_path_for("settings_data"))
    assert stale_tab.status_code == status.HTTP_409_CONFLICT
    assert stale_tab.json()["detail"]["code"] == "VIEW_AS_BINDING_STALE"
    authenticated_client.headers["X-Cosmaneura-View-As-Binding"] = request_binding
    read = authenticated_client.get(api_app.url_path_for("settings_data"))
    assert read.status_code == status.HTTP_200_OK, read.text
    assert read.json()["doctor_id"] == str(target.id)

    practice_read = authenticated_client.get(api_app.url_path_for("practice_status"))
    assert practice_read.status_code == status.HTTP_200_OK, practice_read.text
    assert target_practice.join_code
    assert practice_read.json()["join_code"] is None

    intake_read = authenticated_client.get(
        api_app.url_path_for(
            "intake_detail",
            appointment_id=str(target_intake.appointment_id),
        )
    )
    assert intake_read.status_code == status.HTTP_200_OK, intake_read.text
    assert intake_read.json()["intake"]["id"] is None
    assert intake_read.json()["intake"]["public_url"] is None
    assert intake_read.json()["intake"]["intake_questions"]
    assert intake_read.json()["appointment"]["id"] == str(target_intake.appointment_id)

    cache_miss_capable_get = authenticated_client.get(
        api_app.url_path_for(
            "medication_drug_reference",
            patient_id=str(target.id),
        )
    )
    assert cache_miss_capable_get.status_code == status.HTTP_403_FORBIDDEN

    target_write = authenticated_client.post(
        api_app.url_path_for("complete_onboarding")
    )
    assert target_write.status_code == status.HTTP_403_FORBIDDEN

    # Even a grant minted before view-as began cannot be registered while the
    # session is active. These requests are rejected before token lookup,
    # patient lookup, record creation, or job dispatch.
    visit_upload_registration = authenticated_client.post(
        api_app.url_path_for("upload_visit_audio_file"),
        json={
            "registration_token": "A" * 43,
            "appointment_date": "2026-08-24T12:00:00Z",
            "patient_name": "Must not be created",
            "date_of_birth": "1980-01-01T00:00:00Z",
        },
    )
    assert visit_upload_registration.status_code == status.HTTP_403_FORBIDDEN

    lab_upload_registration = authenticated_client.post(
        api_app.url_path_for(
            "lab_panel_upload_complete",
            patient_id=str(target.id),
        ),
        json={"registration_token": "A" * 43},
    )
    assert lab_upload_registration.status_code == status.HTTP_403_FORBIDDEN

    actor_admin_write = authenticated_client.post(
        api_app.url_path_for("webhook_inbox_redrive", delivery_id="unknown")
    )
    assert actor_admin_write.status_code == status.HTTP_403_FORBIDDEN

    ended = authenticated_client.delete(api_app.url_path_for("end_view_as"))
    assert ended.status_code == status.HTTP_200_OK
    assert ended.json()["active"] is False
    assert ended.json()["request_binding"] == "inactive"

    authenticated_client.headers["X-Cosmaneura-View-As-Binding"] = "inactive"
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
    authenticated_client.headers["X-Cosmaneura-View-As-Binding"] = began.json()[
        "request_binding"
    ]

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
    authenticated_client.headers["X-Cosmaneura-View-As-Binding"] = began.json()[
        "request_binding"
    ]

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


def test_deleted_browser_cookie_cannot_bypass_active_server_session(
    authenticated_client: TestClient,
    monkeypatch,
):
    _staging_environment(monkeypatch)
    _, _, clerk_user = get_clerk_dev_user()
    actor = Doctor.get(clerk_id=clerk_user.id)
    assert actor is not None
    grant_test_superadmin_capability(actor, SuperadminCapabilityKind.view_as)
    target = Doctor(
        clerk_id="user_view_as_cookie_target",
        email="view-as-cookie-target@example.net",
        biography="",
    ).save()
    provision_practice_in_force(target)

    began = authenticated_client.post(
        api_app.url_path_for("begin_view_as"),
        json={"target_doctor_id": str(target.id)},
    )
    assert began.status_code == status.HTTP_200_OK
    active_binding = began.json()["request_binding"]
    authenticated_client.headers["X-Cosmaneura-View-As-Binding"] = active_binding
    authenticated_client.cookies.clear()

    for _ in range(2):
        blocked = authenticated_client.post(api_app.url_path_for("complete_onboarding"))
        assert blocked.status_code == status.HTTP_409_CONFLICT
        active = ViewAsSession.get(actor_doctor_id=actor.id, ended_at=None)
        assert active is not None

    # A safe navigation clears the unusable server session and remains on the
    # real actor; it never opens the target's interactive state.
    stale_read = authenticated_client.get(api_app.url_path_for("settings_data"))
    assert stale_read.status_code == status.HTTP_409_CONFLICT
    assert stale_read.json()["detail"]["code"] == "VIEW_AS_BINDING_STALE"
    assert ViewAsSession.get(actor_doctor_id=actor.id, ended_at=None) is None
    authenticated_client.headers["X-Cosmaneura-View-As-Binding"] = "inactive"
    recovered = authenticated_client.get(api_app.url_path_for("settings_data"))
    assert recovered.status_code == status.HTTP_200_OK
    assert recovered.json()["doctor_id"] == str(actor.id)


def test_deleted_browser_cookie_cannot_issue_sovereign_context_token(
    authenticated_client: TestClient,
    monkeypatch,
):
    _staging_environment(monkeypatch)
    _, _, clerk_user = get_clerk_dev_user()
    actor = Doctor.get(clerk_id=clerk_user.id)
    assert actor is not None
    grant_test_superadmin_capability(actor, SuperadminCapabilityKind.view_as)
    target = Doctor(
        clerk_id="user_view_as_context_target",
        email="view-as-context-target@example.net",
        biography="",
    ).save()
    provision_practice_in_force(target)
    began = authenticated_client.post(
        api_app.url_path_for("begin_view_as"),
        json={"target_doctor_id": str(target.id)},
    )
    assert began.status_code == status.HTTP_200_OK
    authenticated_client.headers["X-Cosmaneura-View-As-Binding"] = began.json()[
        "request_binding"
    ]
    authenticated_client.cookies.clear()
    clerk_subject = clerk_user.id
    assert clerk_subject is not None

    api_app.dependency_overrides[authenticate_embed_platform_session] = lambda: (
        VerifiedClerkIdentity(
            issuer=MOCK_CLERK_ISSUER,
            subject=clerk_subject,
            clerk_session_id="sess_2raFxvjuCPNPEQqhO0vQr25gr9o",
        )
    )
    api_app.dependency_overrides[get_authority_kernel_settings] = lambda: (
        AuthorityKernelSettings(
            enabled=True,
            token_ttl_seconds=900,
            token_refresh_after_seconds=300,
            clerk_issuer=MOCK_CLERK_ISSUER,
        )
    )

    def unexpected_exchange(**_kwargs):
        raise AssertionError("view-as must block before context-token issuance")

    monkeypatch.setattr(
        app.services.authority,
        "exchange_context_token",
        unexpected_exchange,
    )
    response = authenticated_client.post(
        "/embed/v1/context-tokens/exchange",
        headers={"X-Cosmaneura-Embed-Version": "1.0"},
        json={
            "requested_surface": "provider_assistant",
            "record_ref": "record_provider_001",
        },
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.json()["code"] == "REQUEST_DENIED"
    assert ViewAsSession.get(actor_doctor_id=actor.id, ended_at=None) is not None


def test_malformed_target_is_value_minimally_audited_for_capable_actor(
    authenticated_client: TestClient,
    monkeypatch,
):
    _staging_environment(monkeypatch)
    _, _, clerk_user = get_clerk_dev_user()
    actor = Doctor.get(clerk_id=clerk_user.id)
    assert actor is not None
    grant_test_superadmin_capability(actor, SuperadminCapabilityKind.view_as)

    response = authenticated_client.post(
        api_app.url_path_for("begin_view_as"),
        json={"target_doctor_id": "private-malformed-target-value"},
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    [audit] = ViewAsAuditEvent.all()
    assert audit.event_type == "rejected"
    assert audit.reason_code == "target_unavailable"
    assert audit.target_doctor_id is None
