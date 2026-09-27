import secrets
from datetime import datetime, timedelta, timezone

from fastapi import status
from fastapi.testclient import TestClient

from app.server import api_app
from app.services.public_entitlements import active_clinician_seat_count

from activemodel.session_manager import get_session
from app.models.doctor import Doctor
from app.models.practice import Practice, PracticeMember

from tests.routes.utils import get_clerk_dev_user, provision_practice_in_force

ROSTER_FIELDS = {
    "display_name",
    "role",
    "is_self",
    "seat_active",
    "access_in_force",
    "joined_at",
}


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


def _add_member(
    practice: Practice,
    name: str | None,
    *,
    attested: bool = True,
    **overrides,
) -> Doctor:
    clinician = Doctor(
        clerk_id=f"user_roster_{secrets.token_hex(8)}",
        email=f"roster-{secrets.token_hex(4)}@{practice.email_domain}",
        biography="",
    ).save()
    PracticeMember(
        practice_id=practice.id,
        doctor_id=clinician.id,
        role="workforce",
        attested_at=datetime.now(timezone.utc) if attested else None,
        attestation_name=name,
        **overrides,
    ).save()
    return clinician


def _roster(client: TestClient) -> list[dict]:
    resp = client.get(api_app.url_path_for("practice_roster"))
    assert resp.status_code == status.HTTP_200_OK, resp.text
    return resp.json()["members"]


def test_solo_owner_sees_only_themselves(authenticated_client: TestClient):
    members = _roster(authenticated_client)

    assert len(members) == 1
    assert members[0]["role"] == "owner"
    assert members[0]["display_name"] == "Test Owner"
    assert members[0]["is_self"] is True
    assert members[0]["seat_active"] is True
    assert members[0]["access_in_force"] is True


def test_owner_sees_active_inactive_and_revoked_members(
    authenticated_client: TestClient,
):
    practice = _owned_group_practice()
    _add_member(practice, "Active Clinician")
    _add_member(practice, "Unattested Clinician", attested=False)
    _add_member(practice, "Paused Clinician", seat_state="inactive")
    now = datetime.now(timezone.utc)
    _add_member(
        practice,
        "Revoked Clinician",
        access_revoked_at=now,
        access_revoked_by_ref="test-suite",
        access_revocation_reason="Test revocation",
        access_records_retain_until=now + timedelta(days=366 * 7),
    )

    members = _roster(authenticated_client)
    by_name = {m["display_name"]: m for m in members}

    assert set(by_name) == {
        "Test Owner",
        "Active Clinician",
        "Unattested Clinician",
        "Paused Clinician",
        "Revoked Clinician",
    }
    states = {
        name: (m["role"], m["is_self"], m["seat_active"], m["access_in_force"])
        for name, m in by_name.items()
    }
    assert states == {
        "Test Owner": ("owner", True, True, True),
        "Active Clinician": ("workforce", False, True, True),
        "Unattested Clinician": ("workforce", False, True, False),
        "Paused Clinician": ("workforce", False, False, False),
        "Revoked Clinician": ("workforce", False, False, False),
    }
    with get_session() as session:
        billed = active_clinician_seat_count(session, practice.id)
    assert sum(m["seat_active"] for m in members) == billed


def test_roster_lists_owner_first_then_by_join_date(
    authenticated_client: TestClient,
):
    practice = _owned_group_practice()
    owner = PracticeMember.get(doctor_id=_authed_doctor().id)
    assert owner and owner.created_at
    _add_member(practice, "Second Joiner", created_at=owner.created_at + timedelta(2))
    _add_member(practice, "First Joiner", created_at=owner.created_at + timedelta(1))
    _add_member(practice, "Predates Owner", created_at=owner.created_at - timedelta(1))

    names = [m["display_name"] for m in _roster(authenticated_client)]

    assert names == ["Test Owner", "Predates Owner", "First Joiner", "Second Joiner"]


def test_roster_exposes_no_contact_or_identity_fields(
    authenticated_client: TestClient,
):
    practice = _owned_group_practice()
    clinician = _add_member(practice, "Private Clinician")

    resp = authenticated_client.get(api_app.url_path_for("practice_roster"))

    assert resp.status_code == status.HTTP_200_OK, resp.text
    for member in resp.json()["members"]:
        assert set(member) == ROSTER_FIELDS
    owner = _authed_doctor()
    for secret in (
        clinician.email,
        clinician.clerk_id,
        str(clinician.id),
        owner.email,
        owner.clerk_id,
        str(owner.id),
    ):
        assert secret not in resp.text


def test_roster_is_scoped_to_the_callers_practice(
    authenticated_client: TestClient,
):
    practice = _owned_group_practice()
    _add_member(practice, "Our Clinician")
    other = Practice(
        legal_name="Other Practice, PLLC",
        join_code=f"O{secrets.token_hex(4).upper()[:7]}",
        email_domain=practice.email_domain,
        origin_channel="direct",
        is_solo=False,
        billing_exempt=True,
    ).save()
    _add_member(other, "Other Practice Clinician")

    names = [m["display_name"] for m in _roster(authenticated_client)]

    assert names == ["Test Owner", "Our Clinician"]


def test_lapsed_practice_owner_gets_payment_required(
    authenticated_client: TestClient,
):
    practice = _owned_group_practice()
    practice.billing_exempt = False
    practice.trial_ends_at = datetime.now(timezone.utc) - timedelta(days=1)
    practice.save()

    resp = authenticated_client.get(api_app.url_path_for("practice_roster"))

    assert resp.status_code == status.HTTP_402_PAYMENT_REQUIRED


def test_workforce_member_cannot_view_roster(
    bare_authenticated_client: TestClient,
):
    doctor = _authed_doctor()
    practice = provision_practice_in_force(doctor)
    member = PracticeMember.get(doctor_id=doctor.id)
    assert member
    member.role = "workforce"
    member.save()
    practice.is_solo = False
    practice.save()

    resp = bare_authenticated_client.get(api_app.url_path_for("practice_roster"))

    assert resp.status_code == status.HTTP_403_FORBIDDEN
    assert resp.json() == {
        "detail": "Only the practice owner can view the practice roster."
    }


def test_member_without_practice_cannot_view_roster(
    bare_authenticated_client: TestClient,
):
    assert PracticeMember.get(doctor_id=_authed_doctor().id) is None

    resp = bare_authenticated_client.get(api_app.url_path_for("practice_roster"))

    assert resp.status_code == status.HTTP_403_FORBIDDEN
    assert "Only the practice owner" not in resp.text
