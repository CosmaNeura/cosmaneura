"""Route-level coverage for DELETE /v1/me/account."""

import hashlib
import secrets
from datetime import UTC, datetime

import pytest
from clerk_backend_api import User as ClerkUser
from decouple import config as decouple_config
from fastapi.testclient import TestClient

import app.configuration.account_self_delete as account_self_delete_configuration
import app.routes.me_account as me_account_route
from app.routes.me_account import authenticate_clerk_request_middleware
from app.server import api_app
from app.services.doctor_access import doctor_tombstone, resolve_doctor_access

from app.models.doctor import Doctor
from app.models.pilot_access_grandfather_grant import PilotAccessGrandfatherGrant
from app.models.practice_access_audit import PracticeAccessAction, PracticeAccessAudit

from tests.factories import add_practice_peer, build_self_delete_scope
from tests.routes.utils import MockAuthenticateRequest, base_server_url

VIEW_AS_BINDING_HEADER = "X-Cosmaneura-View-As-Binding"
INACTIVE_VIEW_AS_BINDING = "inactive"


@pytest.fixture
def enable_account_self_delete(monkeypatch):
    monkeypatch.setattr(
        account_self_delete_configuration, "ACCOUNT_SELF_DELETE_ENABLED", True
    )
    monkeypatch.setattr(me_account_route, "ACCOUNT_SELF_DELETE_ENABLED", True)


def _client_for(
    doctor: Doctor | None = None,
    *,
    clerk_id: str | None = None,
    fva: list[int] | None = None,
) -> TestClient:
    subject = clerk_id or (doctor.clerk_id if doctor is not None else None)
    assert subject is not None
    api_app.dependency_overrides[authenticate_clerk_request_middleware] = (
        MockAuthenticateRequest(
            user=ClerkUser.model_construct(id=subject, email_addresses=[]),
            # Fresh first-factor verification (Clerk Delete-account window).
            fva=fva if fva is not None else [0, -1],
        )
    )
    client = TestClient(api_app, base_url=base_server_url())
    client.cookies.clear()
    client.headers[VIEW_AS_BINDING_HEADER] = INACTIVE_VIEW_AS_BINDING
    return client


def _delete(client: TestClient, body=None):
    try:
        return client.request(
            "DELETE", "/v1/me/account", json=body or {"confirm": "DELETE"}
        )
    finally:
        api_app.dependency_overrides = {}


def test_account_self_delete_defaults_off_when_unset(monkeypatch):
    monkeypatch.delenv("ACCOUNT_SELF_DELETE_ENABLED", raising=False)
    assert (
        decouple_config("ACCOUNT_SELF_DELETE_ENABLED", default=False, cast=bool)
        is False
    )
    assert account_self_delete_configuration.ACCOUNT_SELF_DELETE_ENABLED is False


def test_self_delete_off_returns_404_without_clerk_or_db_side_effects(
    monkeypatch, global_session
):
    del global_session
    monkeypatch.setattr(
        account_self_delete_configuration, "ACCOUNT_SELF_DELETE_ENABLED", False
    )
    monkeypatch.setattr(me_account_route, "ACCOUNT_SELF_DELETE_ENABLED", False)
    doctor, practice, member = build_self_delete_scope(
        clerk_prefix="user_test_selfdel", email_prefix="route-selfdel"
    )
    before = (
        doctor.deleted_at,
        doctor.account_deletion_status,
        doctor.email,
        doctor.last_active_at,
        practice.owner_reassignment_needed_at,
        member.access_revoked_at,
        Doctor.count(),
    )
    clerk_calls: list[str] = []
    monkeypatch.setattr(
        "app.services.doctor_self_delete._delete_clerk_identity",
        lambda c: clerk_calls.append(c) or True,
    )
    monkeypatch.setattr(
        me_account_route,
        "delete_own_account",
        lambda **_k: (_ for _ in ()).throw(AssertionError("no delete")),
    )
    assert _delete(_client_for(doctor)).status_code == 404
    assert clerk_calls == []
    doctor.refresh()
    practice.refresh()
    member.refresh()
    assert (
        doctor.deleted_at,
        doctor.account_deletion_status,
        doctor.email,
        doctor.last_active_at,
        practice.owner_reassignment_needed_at,
        member.access_revoked_at,
        Doctor.count(),
    ) == before


def test_unauthenticated_self_delete_is_refused(enable_account_self_delete):
    del enable_account_self_delete
    api_app.dependency_overrides.pop(authenticate_clerk_request_middleware, None)
    with TestClient(api_app, base_url=base_server_url()) as client:
        client.headers[VIEW_AS_BINDING_HEADER] = INACTIVE_VIEW_AS_BINDING
        response = client.request(
            "DELETE", "/v1/me/account", json={"confirm": "DELETE"}
        )
    assert response.status_code in {401, 403}


def test_view_as_denial_fires_before_last_active_at_write(
    monkeypatch, global_session, enable_account_self_delete
):
    del global_session, enable_account_self_delete
    doctor, _, _ = build_self_delete_scope(
        clerk_prefix="user_test_selfdel", email_prefix="route-selfdel"
    )
    before = doctor.last_active_at
    inject_calls: list[str] = []
    original = me_account_route.inject_doctor_for_self_delete

    def tracking_inject(request):
        inject_calls.append("ran")
        return original(request)

    monkeypatch.setattr(
        "app.routes.dependencies.view_as.view_as_browser_session_present",
        lambda _r: True,
    )
    api_app.dependency_overrides[me_account_route.inject_doctor_for_self_delete] = (
        tracking_inject
    )
    assert _delete(_client_for(doctor)).status_code == 403
    doctor.refresh()
    assert doctor.last_active_at == before and inject_calls == []


def test_self_delete_endpoint_deletes_only_caller(
    monkeypatch, global_session, enable_account_self_delete
):
    del global_session, enable_account_self_delete
    doctor, practice, _ = build_self_delete_scope(
        is_solo=False, clerk_prefix="user_test_selfdel", email_prefix="route-selfdel"
    )
    peer = add_practice_peer(practice, role="workforce")
    deleted: list[str] = []
    monkeypatch.setattr(
        "app.services.doctor_self_delete._delete_clerk_identity",
        lambda c: deleted.append(c) or True,
    )
    response = _delete(_client_for(doctor))
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "deleted"
    assert payload["practice_left_ownerless"] is True
    assert payload["clerk_identity_removed"] is True
    assert deleted == [doctor.clerk_id]
    doctor.refresh()
    peer.refresh()
    practice.refresh()
    assert doctor.account_deletion_status == "deleted"
    assert peer.deleted_at is None and doctor_tombstone(peer) is None
    assert resolve_doctor_access(peer).allowed is True
    assert practice.owner_reassignment_needed_at is not None
    assert (
        PracticeAccessAudit.where(
            PracticeAccessAudit.practice_id == practice.id,
            PracticeAccessAudit.action
            == PracticeAccessAction.doctor_self_deleted.value,
        ).count()
        == 1
    )


def test_self_delete_rejects_foreign_target_field(enable_account_self_delete):
    del enable_account_self_delete
    doctor, _, _ = build_self_delete_scope(
        clerk_prefix="user_test_selfdel", email_prefix="route-selfdel"
    )
    bad = _delete(_client_for(doctor), {"confirm": "DELETE", "user_id": str(doctor.id)})
    assert bad.status_code == 422


def test_self_delete_rejects_stale_clerk_factor_verification(
    monkeypatch, global_session, enable_account_self_delete
):
    del global_session, enable_account_self_delete
    doctor, _, _ = build_self_delete_scope(
        clerk_prefix="user_test_selfdel", email_prefix="route-selfdel"
    )
    clerk_calls: list[str] = []
    monkeypatch.setattr(
        "app.services.doctor_self_delete._delete_clerk_identity",
        lambda c: clerk_calls.append(c) or True,
    )
    # fva[0]=11 → first factor older than the 10-minute Delete-account window.
    response = _delete(_client_for(doctor, fva=[11, -1]))
    assert response.status_code == 403
    assert "Recent authentication required" in response.json()["detail"]
    assert clerk_calls == []
    doctor.refresh()
    assert doctor.account_deletion_status is None
    assert doctor.deleted_at is None


def test_self_delete_privacy_disclosure_names_retained_identifiers():
    import re
    from pathlib import Path

    from app.services.doctor_self_delete import SELF_DELETE_PRIVACY_DISCLOSURE

    assert (
        "Names and email addresses are kept in records"
        in SELF_DELETE_PRIVACY_DISCLOSURE
    )
    assert "HIPAA" in SELF_DELETE_PRIVACY_DISCLOSURE
    assert "BAA" in SELF_DELETE_PRIVACY_DISCLOSURE
    # Public documentation wording ships with the latch-flip change, not this PR.
    runbook = re.sub(
        r"\s+",
        " ",
        Path("docs/self-delete-account-billing-runbook.md").read_text(encoding="utf-8"),
    )
    assert "never cancels a practice Stripe subscription" in runbook


def test_clerk_only_self_delete_rejects_stale_clerk_factor_verification(
    monkeypatch, global_session, enable_account_self_delete
):
    del global_session, enable_account_self_delete
    clerk_only = f"user_missing_clerk_only_{secrets.token_hex(5)}"
    clerk_calls: list[str] = []
    monkeypatch.setattr(
        "app.services.doctor_self_delete._delete_clerk_identity",
        lambda c: clerk_calls.append(c) or True,
    )
    monkeypatch.setattr(
        me_account_route,
        "delete_clerk_only_identity",
        lambda **_k: (_ for _ in ()).throw(AssertionError("clerk delete must not run")),
    )
    response = _delete(_client_for(clerk_id=clerk_only, fva=[11, -1]))
    assert response.status_code == 403
    assert "Recent authentication required" in response.json()["detail"]
    assert clerk_calls == []


def test_self_delete_route_allows_deletion_pending_retry(
    monkeypatch, global_session, enable_account_self_delete
):
    del global_session, enable_account_self_delete
    doctor, _, _ = build_self_delete_scope(
        is_solo=True, clerk_prefix="user_test_selfdel", email_prefix="route-selfdel"
    )
    calls: list[str] = []

    def fail_once(clerk_id: str) -> bool:
        from app.services.doctor_self_delete import DoctorSelfDeleteError

        calls.append(clerk_id)
        if len(calls) == 1:
            raise DoctorSelfDeleteError(
                "clerk identity could not be deleted", status_code=503
            )
        return True

    monkeypatch.setattr(
        "app.services.doctor_self_delete._delete_clerk_identity", fail_once
    )
    client = _client_for(doctor)
    try:
        first = client.request("DELETE", "/v1/me/account", json={"confirm": "DELETE"})
        assert first.status_code == 503
        doctor.refresh()
        assert doctor.account_deletion_status == "deletion_pending"
        second = client.request("DELETE", "/v1/me/account", json={"confirm": "DELETE"})
    finally:
        api_app.dependency_overrides = {}
    assert second.status_code == 200 and second.json()["status"] == "deleted"
    assert len(calls) == 2


def test_self_delete_capability_verification_failure_returns_409(
    monkeypatch, global_session, enable_account_self_delete
):
    del global_session, enable_account_self_delete
    doctor, _, _ = build_self_delete_scope(
        clerk_prefix="user_test_selfdel", email_prefix="route-selfdel"
    )

    def boom(*_a, **_k):
        from app.services.superadmin_capability import SuperadminCapabilityError

        raise SuperadminCapabilityError("synthetic capability verification failure")

    monkeypatch.setattr(
        "app.services.doctor_self_delete.get_active_runtime_superadmin_capability_kinds",
        boom,
    )
    monkeypatch.setattr(
        "app.services.doctor_self_delete._delete_clerk_identity", lambda _c: True
    )
    assert _delete(_client_for(doctor)).status_code == 409
    doctor.refresh()
    assert doctor.account_deletion_status is None


def test_missing_doctor_revoked_pilot_410_clerk_only_200_and_422_no_upsert(
    monkeypatch, global_session, enable_account_self_delete
):
    del global_session, enable_account_self_delete
    suffix = secrets.token_hex(5)
    revoked_id = f"user_missing_revoked_{suffix}"
    email = f"revoked-{suffix}@example.com"
    PilotAccessGrandfatherGrant(
        clerk_id=revoked_id,
        email_snapshot=email,
        email_sha256=hashlib.sha256(email.encode()).hexdigest(),
        source_ref="controlled://self-delete/revoked",
        source_sha256="a" * 64,
        grant_plan_sha256="b" * 64,
        granted_by_ref="change:self-delete",
        grant_reason="Synthetic revoked grant",
        granted_at=datetime(2026, 8, 1, 12, 0, tzinfo=UTC),
        revoked_at=datetime(2026, 9, 1, 12, 0, tzinfo=UTC),
        revoked_by_ref="change:self-delete-revoke",
        revocation_reason="Synthetic revocation",
        revocation_plan_sha256="c" * 64,
        retain_until=datetime(2033, 9, 1, 12, 0, tzinfo=UTC),
    ).save()
    before = Doctor.count()
    monkeypatch.setattr(
        "app.services.doctor_self_delete._delete_clerk_identity",
        lambda _c: (_ for _ in ()).throw(AssertionError("no clerk")),
    )
    assert _delete(_client_for(clerk_id=revoked_id)).status_code == 410
    assert Doctor.count() == before and Doctor.get(clerk_id=revoked_id) is None

    clerk_only = f"user_missing_clerk_only_{secrets.token_hex(5)}"
    deleted: list[str] = []
    monkeypatch.setattr(
        "app.services.doctor_self_delete._delete_clerk_identity",
        lambda c: deleted.append(c) or True,
    )
    response = _delete(_client_for(clerk_id=clerk_only))
    assert response.status_code == 200
    assert response.json() == {
        "status": "deleted",
        "practice_left_ownerless": False,
        "clerk_identity_removed": True,
    }
    assert deleted == [clerk_only] and Doctor.get(clerk_id=clerk_only) is None

    bad_id = f"user_missing_422_{secrets.token_hex(5)}"
    monkeypatch.setattr(
        "app.services.doctor_self_delete._delete_clerk_identity",
        lambda _c: (_ for _ in ()).throw(AssertionError("no clerk")),
    )
    assert _delete(_client_for(clerk_id=bad_id), {"confirm": "nope"}).status_code == 422
    assert Doctor.get(clerk_id=bad_id) is None
