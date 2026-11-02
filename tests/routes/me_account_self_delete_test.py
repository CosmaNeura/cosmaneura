"""Route-level coverage for DELETE /v1/me/account."""

import secrets
from datetime import UTC, datetime

from clerk_backend_api import User as ClerkUser
from fastapi.testclient import TestClient

from app.routes.me_account import authenticate_clerk_request_middleware
from app.server import api_app
from app.services.doctor_access import doctor_tombstone, resolve_doctor_access

from app.models.baa import BaaAcceptance
from app.models.doctor import Doctor
from app.models.practice import Practice, PracticeMember
from app.models.practice_access_audit import PracticeAccessAction, PracticeAccessAudit

from tests.factories import save_practice_member
from tests.routes.utils import (
    MockAuthenticateRequest,
    base_server_url,
    ensure_current_baa_version,
)

VIEW_AS_BINDING_HEADER = "X-Cosmaneura-View-As-Binding"
INACTIVE_VIEW_AS_BINDING = "inactive"


def _client_for(doctor: Doctor) -> TestClient:
    user = ClerkUser.model_construct(
        id=doctor.clerk_id,
        email_addresses=[],
    )
    api_app.dependency_overrides[authenticate_clerk_request_middleware] = (
        MockAuthenticateRequest(user=user)
    )
    client = TestClient(api_app, base_url=base_server_url())
    client.cookies.clear()
    client.headers[VIEW_AS_BINDING_HEADER] = INACTIVE_VIEW_AS_BINDING
    return client


def _doctor_with_practice(*, role: str = "owner", is_solo: bool = True):
    suffix = secrets.token_hex(5)
    doctor = Doctor(
        clerk_id=f"user_test_selfdel_{suffix}",
        email=f"route-selfdel-{suffix}@example.com",
        biography="Route profile",
    ).save()
    version = ensure_current_baa_version()
    practice = Practice(
        legal_name=f"Route Self Delete {suffix}",
        join_code=f"R{secrets.token_hex(4).upper()[:7]}",
        email_domain="example.com",
        is_solo=is_solo,
        origin_channel="direct",
        pastoral_pack=False,
        billing_exempt=True,
        baa_version=f"{version.doc_id} v{version.version}",
        baa_accepted_at=datetime(2026, 8, 1, 12, 0, tzinfo=UTC),
        baa_accepted_by_doctor_id=doctor.id,
        baa_signature_name="Synthetic Signer",
        baa_signature_title="Owner",
    ).save()
    BaaAcceptance(
        practice_id=practice.id,
        baa_version_id=version.id,
        covered_entity_legal_name=practice.legal_name,
        accepted_by_doctor_id=doctor.id,
        accepted_by_name="Synthetic Signer",
        accepted_by_title="Owner",
        accepted_by_email=doctor.email or "synthetic@example.com",
        accepted_at_utc=datetime(2026, 8, 1, 12, 0, tzinfo=UTC),
        content_sha256=version.sha256,
    ).save()
    member = save_practice_member(
        PracticeMember(
            practice_id=practice.id,
            doctor_id=doctor.id,
            role=role,
            attested_at=datetime(2026, 8, 1, 12, 0, tzinfo=UTC),
            attestation_name="Synthetic Signer",
        )
    )
    return doctor, practice, member


def test_unauthenticated_self_delete_is_refused():
    api_app.dependency_overrides.pop(authenticate_clerk_request_middleware, None)
    with TestClient(api_app, base_url=base_server_url()) as client:
        client.headers[VIEW_AS_BINDING_HEADER] = INACTIVE_VIEW_AS_BINDING
        response = client.request(
            "DELETE",
            "/v1/me/account",
            json={"confirm": "DELETE"},
        )
    assert response.status_code in {401, 403}


def test_self_delete_endpoint_deletes_only_caller(monkeypatch, global_session):
    del global_session
    doctor, practice, _ = _doctor_with_practice(is_solo=False)
    peer = Doctor(
        clerk_id=f"user_test_selfdel_peer_{secrets.token_hex(5)}",
        email=f"route-peer-{secrets.token_hex(5)}@example.com",
        biography="Peer",
    ).save()
    save_practice_member(
        PracticeMember(
            practice_id=practice.id,
            doctor_id=peer.id,
            role="workforce",
            attested_at=datetime(2026, 8, 2, 12, 0, tzinfo=UTC),
            attestation_name="Peer",
        )
    )
    deleted: list[str] = []
    monkeypatch.setattr(
        "app.services.doctor_self_delete._delete_clerk_identity",
        lambda clerk_id: deleted.append(clerk_id) or True,
    )

    client = _client_for(doctor)
    try:
        response = client.request(
            "DELETE",
            "/v1/me/account",
            json={"confirm": "DELETE"},
        )
    finally:
        api_app.dependency_overrides = {}

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
    assert peer.deleted_at is None
    assert doctor_tombstone(peer) is None
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


def test_self_delete_rejects_foreign_target_field():
    doctor, _, _ = _doctor_with_practice()
    client = _client_for(doctor)
    try:
        response = client.request(
            "DELETE",
            "/v1/me/account",
            json={"confirm": "DELETE", "user_id": str(doctor.id)},
        )
    finally:
        api_app.dependency_overrides = {}
    assert response.status_code == 422


def test_self_delete_sole_owner_ownerless_via_route(monkeypatch, global_session):
    del global_session
    doctor, practice, _ = _doctor_with_practice(is_solo=True)
    monkeypatch.setattr(
        "app.services.doctor_self_delete._delete_clerk_identity",
        lambda _clerk_id: True,
    )
    client = _client_for(doctor)
    try:
        response = client.request(
            "DELETE",
            "/v1/me/account",
            json={"confirm": "DELETE"},
        )
    finally:
        api_app.dependency_overrides = {}
    assert response.status_code == 200
    assert response.json()["practice_left_ownerless"] is True
    practice.refresh()
    assert practice.owner_reassignment_needed_at is not None
    assert Practice.get(id=practice.id) is not None


def test_self_delete_route_allows_deletion_pending_retry(monkeypatch, global_session):
    del global_session
    doctor, _, _ = _doctor_with_practice(is_solo=True)
    calls: list[str] = []

    def fail_once(clerk_id: str) -> bool:
        from app.services.doctor_self_delete import DoctorSelfDeleteError

        calls.append(clerk_id)
        if len(calls) == 1:
            raise DoctorSelfDeleteError("clerk identity could not be deleted")
        return True

    monkeypatch.setattr(
        "app.services.doctor_self_delete._delete_clerk_identity",
        fail_once,
    )
    client = _client_for(doctor)
    try:
        first = client.request(
            "DELETE",
            "/v1/me/account",
            json={"confirm": "DELETE"},
        )
        assert first.status_code == 400
        doctor.refresh()
        assert doctor.account_deletion_status == "deletion_pending"
        second = client.request(
            "DELETE",
            "/v1/me/account",
            json={"confirm": "DELETE"},
        )
    finally:
        api_app.dependency_overrides = {}
    assert second.status_code == 200
    assert second.json()["status"] == "deleted"
    assert len(calls) == 2
    doctor.refresh()
    assert doctor.account_deletion_status == "deleted"
