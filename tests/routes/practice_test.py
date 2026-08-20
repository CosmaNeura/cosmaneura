from datetime import datetime, timezone

from fastapi import status
from fastapi.testclient import TestClient

from app.server import api_app
from app.utils.email_domain import extract_email_domain

from app.models.doctor import Doctor
from app.models.practice import Practice, PracticeMember

from tests.routes.utils import get_clerk_dev_user


def _authed_doctor() -> Doctor:
    _, _, clerk_user = get_clerk_dev_user()
    doctor = Doctor.find_or_create_by(clerk_id=clerk_user.id)
    assert doctor.email
    return doctor


def test_complete_onboarding_requires_practice(authenticated_client: TestClient):
    doctor = _authed_doctor()
    assert doctor.onboarding_completed_at is None
    assert PracticeMember.get(doctor_id=doctor.id) is None

    resp = authenticated_client.post(api_app.url_path_for("complete_onboarding"))
    assert resp.status_code == status.HTTP_400_BAD_REQUEST
    assert "practice" in resp.json()["detail"].lower()


def test_grandfathered_doctor_can_complete_without_practice(
    authenticated_client: TestClient,
):
    doctor = _authed_doctor()
    doctor.onboarding_completed_at = datetime.now(timezone.utc)
    doctor.save()

    resp = authenticated_client.post(api_app.url_path_for("complete_onboarding"))
    assert resp.status_code == status.HTTP_200_OK


def test_create_practice_stamps_email_domain(authenticated_client: TestClient):
    doctor = _authed_doctor()
    domain = extract_email_domain(doctor.email)
    assert domain

    resp = authenticated_client.post(
        api_app.url_path_for("create_practice"),
        json={
            "legal_name": "Riverside Family Practice, PLLC",
            "city": "Nashua",
            "region": "NH",
            "is_solo": True,
            "channel": "direct",
            "pastoral_pack": False,
        },
    )
    assert resp.status_code == status.HTTP_200_OK, resp.text
    body = resp.json()
    assert body["legal_name"].startswith("Riverside")
    assert body["role"] == "owner"
    assert body["baa_accepted"] is False
    assert body["email_domain"] == domain
    assert body["join_code"]


def test_owner_baa_then_complete(authenticated_client: TestClient):
    doctor = _authed_doctor()
    created = authenticated_client.post(
        api_app.url_path_for("create_practice"),
        json={
            "legal_name": "Riverside Family Practice, PLLC",
            "is_solo": True,
            "channel": "direct",
            "pastoral_pack": False,
        },
    )
    assert created.status_code == status.HTTP_200_OK, created.text

    blocked = authenticated_client.post(api_app.url_path_for("complete_onboarding"))
    assert blocked.status_code == status.HTTP_400_BAD_REQUEST

    accepted = authenticated_client.post(
        api_app.url_path_for("accept_practice_baa"),
        json={
            "signature_name": "Pat Owner",
            "signature_title": "Physician / owner",
            "checks": ["authority", "read", "accept", "phi"],
        },
    )
    assert accepted.status_code == status.HTTP_200_OK, accepted.text
    assert accepted.json()["baa_accepted"] is True
    assert accepted.json()["attested"] is True

    done = authenticated_client.post(api_app.url_path_for("complete_onboarding"))
    assert done.status_code == status.HTTP_200_OK
    doctor.refresh()
    assert doctor.onboarding_completed_at is not None


def test_join_requires_matching_email_domain(authenticated_client: TestClient):
    doctor = _authed_doctor()
    Practice(
        legal_name="MyCatholicDoctor Nashua",
        join_code="MCDNASHU",
        email_domain="mycatholicdoctor.com",
        origin_channel="mcd",
        pastoral_pack=True,
        is_solo=False,
        baa_accepted_at=datetime.now(timezone.utc),
        baa_version="2026-08-19-ux-1",
    ).save()

    lookup = authenticated_client.post(
        api_app.url_path_for("lookup_practice"),
        json={"join_code": "mcd-nashu"},
    )
    assert lookup.status_code == status.HTTP_200_OK, lookup.text
    body = lookup.json()
    assert body["practice"] is None
    assert body["mismatch"]["required_domain"] == "mycatholicdoctor.com"
    assert body["mismatch"]["signed_in_domain"] == extract_email_domain(doctor.email)

    joined = authenticated_client.post(
        api_app.url_path_for("join_practice"),
        json={
            "join_code": "MCDNASHU",
            "attestation_name": "Pat Clinician",
            "checks": ["workforce", "not-party", "direction", "phi"],
        },
    )
    assert joined.status_code == status.HTTP_403_FORBIDDEN


def test_mcd_channel_without_mcd_email_coerces_to_catholic(
    authenticated_client: TestClient,
):
    doctor = _authed_doctor()
    assert extract_email_domain(doctor.email) != "mycatholicdoctor.com"

    resp = authenticated_client.post(
        api_app.url_path_for("create_practice"),
        json={
            "legal_name": "Independent Catholic Practice",
            "is_solo": True,
            "channel": "mcd",
            "pastoral_pack": True,
        },
    )
    assert resp.status_code == status.HTTP_200_OK, resp.text
    body = resp.json()
    assert body["origin_channel"] == "catholic"
    assert body["email_domain"] == extract_email_domain(doctor.email)
