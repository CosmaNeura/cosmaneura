import secrets
from datetime import datetime, timezone

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from app.routes import internal
from app.server import api_app
from app.services.public_entitlements import (
    MAX_ACTIVE_CLINICIAN_SEATS,
    active_clinician_seat_count,
)
from app.utils.email_domain import extract_email_domain

from activemodel.session_manager import get_session
from app.models.doctor import Doctor
from app.models.practice import Practice, PracticeMember

from tests.routes.utils import get_clerk_dev_user


@pytest.fixture(autouse=True)
def _verified_clerk_email_matches_bound_doctor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def current_email(clerk_id: str) -> str | None:
        doctor = Doctor.get(clerk_id=clerk_id)
        return (doctor.email or "").strip().lower() if doctor else None

    monkeypatch.setattr(internal, "verified_primary_email", current_email)


def test_public_join_rejects_twenty_first_active_clinician(
    bare_authenticated_client: TestClient,
) -> None:
    _, _, clerk_user = get_clerk_dev_user()
    joining_doctor = Doctor.find_or_create_by(clerk_id=clerk_user.id)
    assert joining_doctor.email
    domain = extract_email_domain(joining_doctor.email)
    assert domain
    practice = Practice(
        legal_name="Full Public Practice, PLLC",
        join_code=f"F{secrets.token_hex(4).upper()[:7]}",
        email_domain=domain,
        origin_channel="direct",
        is_solo=False,
        baa_accepted_at=datetime.now(timezone.utc),
        baa_version="CN-BAA-CUST-CW v2.2",
        stripe_subscription_id=f"sub_join_full_{secrets.token_hex(8)}",
        stripe_quantity_sync_target=MAX_ACTIVE_CLINICIAN_SEATS,
        stripe_quantity_sync_version=3,
    ).save()
    for ordinal in range(MAX_ACTIVE_CLINICIAN_SEATS):
        doctor = Doctor(
            clerk_id=f"user_full_join_{secrets.token_hex(8)}",
            email=f"member-{ordinal}-{secrets.token_hex(3)}@{domain}",
            biography="",
        ).save()
        PracticeMember(
            practice_id=practice.id,
            doctor_id=doctor.id,
            role="workforce",
        ).save()

    response = bare_authenticated_client.post(
        api_app.url_path_for("join_practice"),
        json={
            "join_code": practice.join_code,
            "attestation_name": "Twenty First Clinician",
            "checks": ["workforce", "not-party", "direction", "phi"],
        },
    )

    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json()["detail"] == (
        "A practice may have at most 20 active clinicians."
    )
    assert PracticeMember.get(doctor_id=joining_doctor.id) is None
    with get_session() as session:
        assert (
            active_clinician_seat_count(session, practice.id)
            == MAX_ACTIVE_CLINICIAN_SEATS
        )
    practice.refresh()
    assert practice.stripe_quantity_sync_required is False
    assert practice.stripe_quantity_sync_target == MAX_ACTIVE_CLINICIAN_SEATS
    assert practice.stripe_quantity_sync_version == 3


def test_join_counts_only_active_nonrevoked_seats_and_records_stripe_target(
    bare_authenticated_client: TestClient,
) -> None:
    _, _, clerk_user = get_clerk_dev_user()
    joining_doctor = Doctor.find_or_create_by(clerk_id=clerk_user.id)
    assert joining_doctor.email
    domain = extract_email_domain(joining_doctor.email)
    assert domain
    now = datetime.now(timezone.utc)
    practice = Practice(
        legal_name="One Open Seat Practice, PLLC",
        join_code=f"J{secrets.token_hex(4).upper()[:7]}",
        email_domain=domain,
        origin_channel="direct",
        is_solo=False,
        baa_accepted_at=now,
        baa_version="CN-BAA-CUST-CW v2.2",
        stripe_subscription_id=f"sub_join_open_{secrets.token_hex(8)}",
    ).save()
    for ordinal in range(MAX_ACTIVE_CLINICIAN_SEATS - 1):
        doctor = Doctor(
            clerk_id=f"user_open_join_{secrets.token_hex(8)}",
            email=f"active-{ordinal}-{secrets.token_hex(3)}@{domain}",
            biography="",
        ).save()
        PracticeMember(
            practice_id=practice.id,
            doctor_id=doctor.id,
            role="workforce",
        ).save()

    inactive_doctor = Doctor(
        clerk_id=f"user_open_join_inactive_{secrets.token_hex(8)}",
        email=f"inactive-{secrets.token_hex(3)}@{domain}",
        biography="",
    ).save()
    PracticeMember(
        practice_id=practice.id,
        doctor_id=inactive_doctor.id,
        role="workforce",
        seat_state="inactive",
    ).save()
    revoked_doctor = Doctor(
        clerk_id=f"user_open_join_revoked_{secrets.token_hex(8)}",
        email=f"revoked-{secrets.token_hex(3)}@{domain}",
        biography="",
    ).save()
    PracticeMember(
        practice_id=practice.id,
        doctor_id=revoked_doctor.id,
        role="workforce",
        access_revoked_at=now,
        access_revoked_by_ref="test-seat-cap",
        access_revocation_reason="Synthetic irreversible revocation",
        access_records_retain_until=now.replace(year=now.year + 8),
    ).save()

    response = bare_authenticated_client.post(
        api_app.url_path_for("join_practice"),
        json={
            "join_code": practice.join_code,
            "attestation_name": "Final Active Clinician",
            "checks": ["workforce", "not-party", "direction", "phi"],
        },
    )

    assert response.status_code == status.HTTP_200_OK, response.text
    joined_member = PracticeMember.get(doctor_id=joining_doctor.id)
    assert joined_member is not None
    practice.refresh()
    assert practice.stripe_quantity_sync_required is True
    assert practice.stripe_quantity_sync_target == MAX_ACTIVE_CLINICIAN_SEATS
    assert practice.stripe_quantity_sync_version == 1
    with get_session() as session:
        assert (
            active_clinician_seat_count(session, practice.id)
            == MAX_ACTIVE_CLINICIAN_SEATS
        )
