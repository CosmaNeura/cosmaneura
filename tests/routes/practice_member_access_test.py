import secrets
from datetime import datetime, timedelta, timezone

from fastapi import status
from fastapi.testclient import TestClient
from httpx import Response

from app.server import api_app
from app.services.practice_member_access import access_cutoff_after
from app.services.public_entitlements import MAX_ACTIVE_CLINICIAN_SEATS

from app.models.doctor import Doctor
from app.models.practice import Practice, PracticeMember
from app.models.practice_access_audit import PracticeAccessAudit

from tests.factories import save_practice_member
from tests.routes.utils import get_clerk_dev_user, provision_practice_in_force

SEAT_LIMIT_OWNER_DETAIL = "To add more clinicians, contact us at info@cosmaneura.com."


def _authed_doctor() -> Doctor:
    _, _, clerk_user = get_clerk_dev_user()
    doctor = Doctor.find_or_create_by(clerk_id=clerk_user.id)
    assert doctor.email
    return doctor


def _owned_group_practice() -> Practice:
    practice = provision_practice_in_force(_authed_doctor())
    practice.is_solo = False
    practice.save()
    return practice


def _add_member(practice: Practice, name: str = "Riley Clinician") -> PracticeMember:
    clinician = Doctor(
        clerk_id=f"user_member_access_{secrets.token_hex(8)}",
        email=f"member-access-{secrets.token_hex(4)}@{practice.email_domain}",
        biography="",
    ).save()
    return save_practice_member(
        PracticeMember(
            practice_id=practice.id,
            doctor_id=clinician.id,
            role="workforce",
            attested_at=datetime.now(timezone.utc),
            attestation_name=name,
        )
    )


def _revoke(client: TestClient, member_id) -> Response:
    return client.post(
        api_app.url_path_for("revoke_practice_member", member_id=str(member_id))
    )


def _restore(client: TestClient, member_id) -> Response:
    return client.post(
        api_app.url_path_for("restore_practice_member", member_id=str(member_id))
    )


def _end_grace_period(member: PracticeMember) -> None:
    member = PracticeMember.one(id=member.id)
    member.access_ends_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    save_practice_member(member)


def test_owner_revokes_then_restores_a_clinician(authenticated_client: TestClient):
    practice = _owned_group_practice()
    member = _add_member(practice)

    revoked = _revoke(authenticated_client, member.id)

    assert revoked.status_code == status.HTTP_200_OK, revoked.text
    body = revoked.json()
    assert body["member_id"] == str(member.id)
    assert body["seat_active"] is True
    assert body["access_in_force"] is True
    assert body["revoked"] is False
    ends_at = datetime.fromisoformat(body["access_ends_at"])
    assert ends_at == access_cutoff_after(ends_at - timedelta(hours=1))
    roster = authenticated_client.get(api_app.url_path_for("practice_roster")).json()
    listed = {m["member_id"]: m for m in roster["members"]}[str(member.id)]
    assert listed["access_ends_at"] == body["access_ends_at"]
    assert roster["occupied_seats"] == 2

    restored = _restore(authenticated_client, member.id)

    assert restored.status_code == status.HTTP_200_OK, restored.text
    assert restored.json()["access_ends_at"] is None
    assert restored.json()["seat_active"] is True
    assert (
        PracticeAccessAudit.where(
            PracticeAccessAudit.practice_id == practice.id,
            PracticeAccessAudit.document_reference == str(member.id),
        ).count()
        == 2
    )


def test_after_the_cutoff_the_roster_shows_a_restorable_member(
    authenticated_client: TestClient,
):
    practice = _owned_group_practice()
    member = _add_member(practice)
    _revoke(authenticated_client, member.id)
    _end_grace_period(member)

    roster = authenticated_client.get(api_app.url_path_for("practice_roster")).json()
    listed = {m["member_id"]: m for m in roster["members"]}[str(member.id)]

    assert listed["seat_active"] is False
    assert listed["access_in_force"] is False
    assert listed["revoked"] is False
    assert listed["access_ends_at"] is not None
    assert roster["occupied_seats"] == 1


def test_restore_after_the_cutoff_at_the_cap_asks_the_owner_to_contact_us(
    authenticated_client: TestClient,
):
    practice = _owned_group_practice()
    member = _add_member(practice)
    _revoke(authenticated_client, member.id)
    _end_grace_period(member)
    for ordinal in range(MAX_ACTIVE_CLINICIAN_SEATS - 1):
        _add_member(practice, f"Seat {ordinal}")

    response = _restore(authenticated_client, member.id)

    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json()["detail"] == SEAT_LIMIT_OWNER_DETAIL


def test_the_owner_cannot_revoke_themselves(authenticated_client: TestClient):
    _owned_group_practice()
    owner = PracticeMember.one(doctor_id=_authed_doctor().id)

    response = _revoke(authenticated_client, owner.id)

    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json()["detail"] == "The practice owner's access can't be revoked."


def test_a_member_of_another_practice_is_not_found(authenticated_client: TestClient):
    _owned_group_practice()
    other_owner = Doctor(
        clerk_id=f"user_other_owner_{secrets.token_hex(6)}",
        email=f"other-{secrets.token_hex(4)}@example.org",
        biography="",
    ).save()
    stranger = _add_member(provision_practice_in_force(other_owner))

    for call in (_revoke, _restore):
        response = call(authenticated_client, stranger.id)
        assert response.status_code == status.HTTP_404_NOT_FOUND
    assert PracticeMember.one(id=stranger.id).seat_state == "active"


def test_workforce_cannot_revoke_or_restore(authenticated_client: TestClient):
    practice = _owned_group_practice()
    colleague = _add_member(practice)
    me = PracticeMember.one(doctor_id=_authed_doctor().id)
    me.role = "workforce"
    me.save()

    for call in (_revoke, _restore):
        response = call(authenticated_client, colleague.id)
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json()["detail"] == (
            "Only the practice owner can revoke or restore clinicians."
        )
    assert PracticeMember.one(id=colleague.id).seat_state == "active"


def test_a_revoked_clinician_keeps_access_until_the_cutoff_then_sees_why(
    authenticated_client: TestClient,
):
    practice = _owned_group_practice()
    me = PracticeMember.one(doctor_id=_authed_doctor().id)
    me.role = "workforce"
    me.seat_state = "inactive"
    me.access_ends_at = datetime.now(timezone.utc) + timedelta(hours=1)
    save_practice_member(me)
    assert practice.id == me.practice_id

    during = authenticated_client.get(api_app.url_path_for("note_list"))
    status_during = authenticated_client.get(api_app.url_path_for("practice_status"))

    assert during.status_code == status.HTTP_200_OK, during.text
    assert status_during.json()["access_ends_at"] is not None

    _end_grace_period(me)
    after = authenticated_client.get(api_app.url_path_for("note_list"))

    assert after.status_code == status.HTTP_403_FORBIDDEN
    assert after.json()["detail"] == "Your practice owner ended your access to Cosmas."
