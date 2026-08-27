import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from app.routes import internal, public_onboarding
from app.server import api_app
from app.services.public_entitlements import (
    MAX_ACTIVE_CLINICIAN_SEATS,
    active_clinician_seat_count,
)
from app.services.public_legal import CURRENT_LEGAL_DOCUMENTS

from activemodel.session_manager import get_session
from app.models.doctor import Doctor
from app.models.practice import ClinicianSeatState, Practice, PracticeMember
from app.models.public_onboarding import PracticeInvitation, PublicLegalAcceptance

from tests.routes.utils import FakeClerkUser, FakeEmailAddress, get_clerk_dev_user


@pytest.fixture(autouse=True)
def _offline_verified_email(monkeypatch: pytest.MonkeyPatch) -> None:
    def current_email(clerk_id: str) -> str | None:
        doctor = Doctor.get(clerk_id=clerk_id)
        return (doctor.email or "").strip().lower() if doctor else None

    monkeypatch.setattr(internal, "verified_primary_email", current_email)
    monkeypatch.setattr(public_onboarding, "verified_primary_email", current_email)
    monkeypatch.setattr(
        "app.services.doctor_access.verified_primary_email",
        current_email,
    )


def _doctor(email: str) -> Doctor:
    return Doctor(
        clerk_id=f"user_public_seat_{secrets.token_hex(8)}",
        email=email,
        biography="",
    ).save()


def _owner_practice() -> tuple[Doctor, object, Practice]:
    _, _, owner_user = get_clerk_dev_user()
    owner = Doctor.find_or_create_by(clerk_id=owner_user.id)
    assert owner.email
    accepted_at = datetime.now(timezone.utc)
    practice = Practice(
        legal_name="Public Seat Policy Practice, PLLC",
        join_code=f"P{secrets.token_hex(4).upper()[:7]}",
        email_domain="example.com",
        origin_channel="direct",
        is_solo=False,
        public_onboarding_required=True,
        baa_accepted_at=accepted_at,
        baa_version="CN-BAA-CUST-CW v2.2",
        stripe_subscription_id=f"sub_public_seat_{secrets.token_hex(8)}",
    ).save()
    PracticeMember(
        practice_id=practice.id,
        doctor_id=owner.id,
        role="owner",
    ).save()
    for document in CURRENT_LEGAL_DOCUMENTS:
        PublicLegalAcceptance(
            practice_id=practice.id,
            document_kind=document.kind.value,
            document_version=document.version,
            content_sha256=document.sha256,
            signer_doctor_id=owner.id,
            signer_email=owner.email,
            signer_role="owner",
            signature_name="Synthetic Owner",
            signature_title="Physician / owner",
            authority_confirmed=True,
            signer_authority="Authorized practice owner",
            accepted_at_utc=accepted_at,
        ).save()
    return owner, owner_user, practice


def _add_active_members(practice: Practice, count: int) -> None:
    for ordinal in range(count):
        doctor = _doctor(
            f"active-{ordinal}-{secrets.token_hex(3)}@{practice.email_domain}"
        )
        PracticeMember(
            practice_id=practice.id,
            doctor_id=doctor.id,
            role="workforce",
        ).save()


def _invitation(
    practice: Practice,
    owner: Doctor,
    invitee: Doctor,
    *,
    token: str,
) -> PracticeInvitation:
    assert invitee.email
    now = datetime.now(timezone.utc)
    return PracticeInvitation(
        practice_id=practice.id,
        invited_by_doctor_id=owner.id,
        token_digest=hashlib.sha256(token.encode()).hexdigest(),
        invitee_email=invitee.email,
        invitee_email_digest=public_onboarding._email_digest(
            practice.id,
            invitee.email,
        ),
        idempotency_key_digest=hashlib.sha256(
            f"idempotency:{token}".encode()
        ).hexdigest(),
        issued_at=now,
        expires_at=now + timedelta(days=7),
    ).save()


def _override_as(client: TestClient, doctor: Doctor) -> None:
    assert doctor.email
    client.override_user(  # type: ignore[attr-defined]
        FakeClerkUser(
            id=doctor.clerk_id,
            email_addresses=[FakeEmailAddress(email_address=doctor.email)],
        )
    )


def _accept(client: TestClient, token: str):
    return client.post(
        api_app.url_path_for("accept_practice_invitation"),
        json={
            "token": token,
            "attestation_name": "Synthetic Invitee",
            "checks": ["workforce", "not-party", "direction", "phi"],
        },
    )


def test_invitation_accept_rejects_twenty_first_seat_before_commit(
    bare_authenticated_client: TestClient,
) -> None:
    owner, _, practice = _owner_practice()
    _add_active_members(practice, MAX_ACTIVE_CLINICIAN_SEATS - 1)
    practice.stripe_quantity_sync_required = False
    practice.stripe_quantity_sync_target = MAX_ACTIVE_CLINICIAN_SEATS
    practice.stripe_quantity_sync_version = 8
    practice.save()
    invitee = _doctor(f"full-invitee-{secrets.token_hex(3)}@example.com")
    token = secrets.token_urlsafe(32)
    invitation = _invitation(practice, owner, invitee, token=token)
    _override_as(bare_authenticated_client, invitee)

    response = _accept(bare_authenticated_client, token)

    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json()["detail"] == (
        "A practice may have at most 20 active clinicians."
    )
    assert PracticeMember.get(doctor_id=invitee.id) is None
    invitation.refresh()
    assert invitation.accepted_at is None
    assert invitation.accepted_by_doctor_id is None
    practice.refresh()
    assert practice.stripe_quantity_sync_required is False
    assert practice.stripe_quantity_sync_target == MAX_ACTIVE_CLINICIAN_SEATS
    assert practice.stripe_quantity_sync_version == 8
    with get_session() as session:
        assert (
            active_clinician_seat_count(session, practice.id)
            == MAX_ACTIVE_CLINICIAN_SEATS
        )


def test_invitation_replay_cannot_reactivate_and_seat_mutations_record_quantity(
    bare_authenticated_client: TestClient,
) -> None:
    owner, owner_user, practice = _owner_practice()
    invitee = _doctor(f"replay-invitee-{secrets.token_hex(3)}@example.com")
    token = secrets.token_urlsafe(32)
    _invitation(practice, owner, invitee, token=token)
    _override_as(bare_authenticated_client, invitee)

    accepted = _accept(bare_authenticated_client, token)
    assert accepted.status_code == status.HTTP_200_OK, accepted.text
    member_id = accepted.json()["member_id"]
    practice.refresh()
    assert practice.stripe_quantity_sync_required is True
    assert practice.stripe_quantity_sync_target == 2
    assert practice.stripe_quantity_sync_version == 1

    bare_authenticated_client.override_user(owner_user)  # type: ignore[attr-defined]
    deactivated = bare_authenticated_client.post(
        api_app.url_path_for("deactivate_practice_member", member_id=member_id)
    )
    assert deactivated.status_code == status.HTTP_200_OK, deactivated.text
    assert deactivated.json()["seat_state"] == ClinicianSeatState.inactive.value
    practice.refresh()
    assert practice.stripe_quantity_sync_target == 1
    assert practice.stripe_quantity_sync_version == 2

    _override_as(bare_authenticated_client, invitee)
    replayed = _accept(bare_authenticated_client, token)
    assert replayed.status_code == status.HTTP_200_OK, replayed.text
    assert replayed.json()["seat_state"] == ClinicianSeatState.inactive.value
    practice.refresh()
    assert practice.stripe_quantity_sync_target == 1
    assert practice.stripe_quantity_sync_version == 2
    with get_session() as session:
        assert active_clinician_seat_count(session, practice.id) == 1

    bare_authenticated_client.override_user(owner_user)  # type: ignore[attr-defined]
    reactivated = bare_authenticated_client.post(
        api_app.url_path_for("reactivate_practice_member", member_id=member_id)
    )
    assert reactivated.status_code == status.HTTP_200_OK, reactivated.text
    assert reactivated.json()["seat_state"] == ClinicianSeatState.active.value
    practice.refresh()
    assert practice.stripe_quantity_sync_target == 2
    assert practice.stripe_quantity_sync_version == 3

    idempotent = bare_authenticated_client.post(
        api_app.url_path_for("reactivate_practice_member", member_id=member_id)
    )
    assert idempotent.status_code == status.HTTP_200_OK, idempotent.text
    practice.refresh()
    assert practice.stripe_quantity_sync_target == 2
    assert practice.stripe_quantity_sync_version == 3


def test_reactivate_rejects_twenty_first_seat_without_quantity_drift(
    bare_authenticated_client: TestClient,
) -> None:
    _, owner_user, practice = _owner_practice()
    _add_active_members(practice, MAX_ACTIVE_CLINICIAN_SEATS - 1)
    inactive_doctor = _doctor(f"inactive-{secrets.token_hex(3)}@example.com")
    inactive = PracticeMember(
        practice_id=practice.id,
        doctor_id=inactive_doctor.id,
        role="workforce",
        seat_state=ClinicianSeatState.inactive.value,
    ).save()
    practice.stripe_quantity_sync_required = False
    practice.stripe_quantity_sync_target = MAX_ACTIVE_CLINICIAN_SEATS
    practice.stripe_quantity_sync_version = 11
    practice.save()
    bare_authenticated_client.override_user(owner_user)  # type: ignore[attr-defined]

    response = bare_authenticated_client.post(
        api_app.url_path_for("reactivate_practice_member", member_id=inactive.id)
    )

    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json()["detail"] == (
        "A practice may have at most 20 active clinicians."
    )
    inactive.refresh()
    assert inactive.seat_state == ClinicianSeatState.inactive.value
    practice.refresh()
    assert practice.stripe_quantity_sync_required is False
    assert practice.stripe_quantity_sync_target == MAX_ACTIVE_CLINICIAN_SEATS
    assert practice.stripe_quantity_sync_version == 11
