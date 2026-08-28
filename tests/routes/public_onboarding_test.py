import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from app.routes import internal, public_onboarding
from app.server import api_app
from app.services.public_entitlements import (
    CHECKOUT_TRIAL_CLOCK_POLICY,
    active_clinician_seat_count,
)
from app.services.public_legal import CURRENT_LEGAL_DOCUMENTS

from activemodel.session_manager import get_session
from app.models.baa import BaaAcceptance
from app.models.doctor import Doctor
from app.models.practice import ClinicianSeatState, Practice, PracticeMember
from app.models.practice_access_audit import PracticeAccessAudit
from app.models.public_onboarding import PracticeInvitation, PublicLegalAcceptance
from sqlalchemy.exc import DBAPIError

from tests.routes.utils import FakeClerkUser, FakeEmailAddress, get_clerk_dev_user

ROOT = Path(__file__).parents[2]


@pytest.mark.parametrize(
    ("session_id", "status_value", "trial_end", "policy", "expected"),
    [
        ("cs_test_created", None, None, None, True),
        (None, "incomplete", None, None, True),
        (None, None, datetime(2026, 9, 10, tzinfo=timezone.utc), None, False),
        (
            None,
            None,
            datetime(2026, 9, 10, tzinfo=timezone.utc),
            "legacy-baa-v1",
            False,
        ),
        (None, None, None, CHECKOUT_TRIAL_CLOCK_POLICY, False),
        (
            None,
            None,
            datetime(2026, 9, 10, tzinfo=timezone.utc),
            CHECKOUT_TRIAL_CLOCK_POLICY,
            True,
        ),
    ],
)
def test_checkout_started_requires_durable_current_policy_provenance(
    session_id: str | None,
    status_value: str | None,
    trial_end: datetime | None,
    policy: str | None,
    expected: bool,
) -> None:
    practice = cast(
        Practice,
        SimpleNamespace(
            stripe_checkout_session_id=session_id,
            subscription_status=status_value,
            trial_ends_at=trial_end,
            stripe_trial_clock_policy=policy,
        ),
    )

    assert public_onboarding._checkout_started(practice) is expected


def _authed_doctor() -> tuple[Doctor, object]:
    _, _, clerk_user = get_clerk_dev_user()
    doctor = Doctor.find_or_create_by(clerk_id=clerk_user.id)
    assert doctor.email
    return doctor, clerk_user


def _create_public_practice(client: TestClient, *, solo: bool = True) -> Practice:
    response = client.post(
        api_app.url_path_for("create_practice"),
        json={
            "legal_name": "Synthetic Public Practice, PLLC",
            "is_solo": solo,
            "channel": "direct",
            "pastoral_pack": False,
        },
    )
    assert response.status_code == status.HTTP_200_OK, response.text
    doctor, _ = _authed_doctor()
    member = PracticeMember.get(doctor_id=doctor.id)
    assert member
    practice = Practice.get(id=member.practice_id)
    assert practice
    assert practice.public_onboarding_required
    return practice


def _accept_baa_and_public_legal(client: TestClient) -> None:
    baa = client.post(
        api_app.url_path_for("accept_practice_baa"),
        json={
            "signature_name": "Synthetic Owner",
            "signature_title": "Physician / owner",
            "checks": ["baa"],
        },
    )
    assert baa.status_code == status.HTTP_200_OK, baa.text
    legal = client.post(
        api_app.url_path_for("accept_public_legal"),
        json={
            "signature_name": "Synthetic Owner",
            "signature_title": "Physician / owner",
            "authority_confirmed": True,
            "documents": [
                {
                    "kind": document.kind.value,
                    "version": document.version,
                    "sha256": document.sha256,
                }
                for document in CURRENT_LEGAL_DOCUMENTS
            ],
        },
    )
    assert legal.status_code == status.HTTP_200_OK, legal.text


def _create_public_invitation(
    client: TestClient,
    email: str,
) -> tuple[str, PracticeInvitation]:
    response = client.post(
        api_app.url_path_for("create_practice_invitation"),
        json={"email": email, "expires_in_days": 7},
        headers={"Idempotency-Key": f"invite-{secrets.token_hex(12)}"},
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    token = response.json()["invite_token"]
    assert token
    invitation = PracticeInvitation.get(id=response.json()["invitation_id"])
    assert invitation
    return token, invitation


def _public_practice_write_snapshot(practice: Practice) -> dict[str, object]:
    """Capture every Task 3-owned mutable projection for no-write checks."""

    practice.refresh()
    members = cast(
        list[PracticeMember],
        PracticeMember.where(PracticeMember.practice_id == practice.id).all(),
    )
    invitations = cast(
        list[PracticeInvitation],
        PracticeInvitation.where(PracticeInvitation.practice_id == practice.id).all(),
    )
    with get_session() as session:
        active_seats = active_clinician_seat_count(session, practice.id)
    return {
        "practice": (
            practice.is_solo,
            practice.baa_accepted_at,
            practice.baa_accepted_by_doctor_id,
            practice.trial_ends_at,
            practice.stripe_checkout_session_id,
            practice.stripe_customer_id,
            practice.stripe_subscription_id,
            practice.subscription_status,
            practice.stripe_quantity_sync_required,
            practice.stripe_quantity_sync_target,
            practice.stripe_quantity_sync_version,
        ),
        "members": sorted(
            (
                str(member.id),
                str(member.practice_id),
                str(member.doctor_id),
                member.role,
                member.seat_state,
                member.access_revoked_at,
                member.credential_state,
            )
            for member in members
        ),
        "invitations": sorted(
            (
                str(invitation.id),
                invitation.token_digest,
                invitation.accepted_at,
                str(invitation.accepted_by_doctor_id),
                invitation.revoked_at,
                str(invitation.revoked_by_doctor_id),
                invitation.revocation_reason,
            )
            for invitation in invitations
        ),
        "baa_acceptances": BaaAcceptance.where(
            BaaAcceptance.practice_id == practice.id
        ).count(),
        "public_legal_acceptances": PublicLegalAcceptance.where(
            PublicLegalAcceptance.practice_id == practice.id
        ).count(),
        "access_audits": PracticeAccessAudit.where(
            PracticeAccessAudit.practice_id == practice.id
        ).count(),
        "active_seats": active_seats,
    }


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


def test_public_legal_acceptance_is_exact_owner_only_and_idempotent(
    bare_authenticated_client: TestClient,
) -> None:
    doctor, _ = _authed_doctor()
    practice = _create_public_practice(bare_authenticated_client)
    accepted_baa = bare_authenticated_client.post(
        api_app.url_path_for("accept_practice_baa"),
        json={
            "signature_name": "Synthetic Owner",
            "signature_title": "Physician / owner",
            "checks": ["baa"],
        },
    )
    assert accepted_baa.status_code == status.HTTP_200_OK

    # Priority 1 owns removal of the legacy BAA-time trial stamp. Keep this
    # test focused on the Task 3 state machine without modifying that seam.
    practice.refresh()
    practice.trial_ends_at = None
    practice.save()

    before = bare_authenticated_client.get(
        api_app.url_path_for("public_onboarding_state")
    )
    assert before.status_code == status.HTTP_200_OK
    assert before.json()["current_step"] == "legal"
    assert before.json()["checkout_started"] is False

    documents = bare_authenticated_client.get(
        api_app.url_path_for("public_legal_current")
    ).json()["documents"]
    payload = {
        "signature_name": "Synthetic Owner",
        "signature_title": "Physician / owner",
        "authority_confirmed": True,
        "documents": [
            {
                "kind": document.kind.value,
                "version": document.version,
                "sha256": document.sha256,
            }
            for document in CURRENT_LEGAL_DOCUMENTS
        ],
    }
    assert documents == [
        {
            "kind": document.kind.value,
            "version": document.version,
            "sha256": document.sha256,
            "path": document.path,
        }
        for document in CURRENT_LEGAL_DOCUMENTS
    ]

    accepted = bare_authenticated_client.post(
        api_app.url_path_for("accept_public_legal"), json=payload
    )
    assert accepted.status_code == status.HTTP_200_OK, accepted.text
    assert accepted.json()["current_step"] == "checkout"
    assert accepted.json()["phi_access_allowed"] is False
    assert (
        PublicLegalAcceptance.where(
            PublicLegalAcceptance.practice_id == practice.id
        ).count()
        == 2
    )

    retried = bare_authenticated_client.post(
        api_app.url_path_for("accept_public_legal"), json=payload
    )
    assert retried.status_code == status.HTTP_200_OK
    assert (
        PublicLegalAcceptance.where(
            PublicLegalAcceptance.practice_id == practice.id
        ).count()
        == 2
    )

    member = PracticeMember.get(doctor_id=doctor.id)
    assert member and member.credential_state == "pending"

    practice.refresh()
    practice.stripe_checkout_session_id = "cs_test_unfinished_checkout"
    practice.trial_ends_at = datetime.now(timezone.utc) + timedelta(days=14)
    practice.save()
    unfinished_checkout = bare_authenticated_client.get(
        api_app.url_path_for("public_onboarding_state")
    )
    assert unfinished_checkout.status_code == status.HTTP_200_OK
    assert unfinished_checkout.json()["checkout_started"] is True
    assert unfinished_checkout.json()["current_step"] == "checkout"
    assert unfinished_checkout.json()["phi_access_allowed"] is False

    practice.subscription_status = "trialing"
    practice.save()
    unbound_trial = bare_authenticated_client.get(
        api_app.url_path_for("public_onboarding_state")
    )
    assert unbound_trial.status_code == status.HTTP_200_OK
    assert unbound_trial.json()["subscription_state"] == "invalid"
    assert unbound_trial.json()["current_step"] == "checkout"

    practice.stripe_subscription_id = "sub_test_verified_trial"
    practice.stripe_customer_id = "cus_test_verified_trial"
    practice.stripe_state_version = 1
    practice.save()
    awaiting_credential = bare_authenticated_client.get(
        api_app.url_path_for("public_onboarding_state")
    )
    assert awaiting_credential.status_code == status.HTTP_200_OK
    assert awaiting_credential.json()["current_step"] == "credential"
    assert awaiting_credential.json()["phi_access_allowed"] is False


def test_workforce_member_cannot_accept_practice_legal_terms(
    bare_authenticated_client: TestClient,
) -> None:
    owner, owner_user = _authed_doctor()
    practice = _create_public_practice(bare_authenticated_client, solo=False)
    workforce_email = f"workforce-{secrets.token_hex(4)}@{practice.email_domain}"
    workforce_user = FakeClerkUser(
        id=f"user_{secrets.token_hex(8)}",
        email_addresses=[FakeEmailAddress(email_address=workforce_email)],
    )
    workforce = Doctor(
        clerk_id=workforce_user.id,
        email=workforce_email,
        biography="",
    ).save()
    PracticeMember(
        practice_id=practice.id,
        doctor_id=workforce.id,
        role="workforce",
        attested_at=datetime.now(timezone.utc),
        attestation_name="Synthetic Workforce",
    ).save()
    bare_authenticated_client.override_user(workforce_user)  # type: ignore[attr-defined]

    response = bare_authenticated_client.post(
        api_app.url_path_for("accept_public_legal"),
        json={
            "signature_name": "Synthetic Workforce",
            "signature_title": "Clinician",
            "authority_confirmed": True,
            "documents": [
                {
                    "kind": document.kind.value,
                    "version": document.version,
                    "sha256": document.sha256,
                }
                for document in CURRENT_LEGAL_DOCUMENTS
            ],
        },
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert (
        PublicLegalAcceptance.where(
            PublicLegalAcceptance.practice_id == practice.id
        ).count()
        == 0
    )
    with pytest.raises(DBAPIError, match="active practice owner"):
        with get_session() as session:
            document = CURRENT_LEGAL_DOCUMENTS[0]
            session.add(
                PublicLegalAcceptance(
                    practice_id=practice.id,
                    document_kind=document.kind.value,
                    document_version=document.version,
                    content_sha256=document.sha256,
                    signer_doctor_id=workforce.id,
                    signer_email=workforce_email,
                    signer_role="owner",
                    signature_name="Synthetic Workforce",
                    signature_title="Clinician",
                    authority_confirmed=True,
                    signer_authority="Invalid direct database attempt",
                    accepted_at_utc=datetime.now(timezone.utc),
                )
            )
            session.commit()
    bare_authenticated_client.override_user(owner_user)  # type: ignore[attr-defined]
    assert owner.id != workforce.id


_WORKFORCE_OWNER_ONLY_OPERATIONS = (
    "accept_baa",
    "accept_public_legal",
    "list_invitations",
    "create_invitation",
    "revoke_invitation",
    "list_members",
    "deactivate_member",
    "reactivate_member",
    "create_billing_checkout",
    "create_billing_portal",
)


@pytest.mark.parametrize("operation", _WORKFORCE_OWNER_ONLY_OPERATIONS)
def test_workforce_actor_cannot_use_owner_only_public_mutations_without_writes(
    bare_authenticated_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    owner, _ = _authed_doctor()
    practice = _create_public_practice(bare_authenticated_client, solo=False)
    _accept_baa_and_public_legal(bare_authenticated_client)
    _, invitation = _create_public_invitation(
        bare_authenticated_client,
        f"pending-{secrets.token_hex(4)}@example.com",
    )

    actor_email = f"workforce-{secrets.token_hex(4)}@{practice.email_domain}"
    actor_user = FakeClerkUser(
        id=f"user_{secrets.token_hex(8)}",
        email_addresses=[FakeEmailAddress(email_address=actor_email)],
    )
    actor = Doctor(
        clerk_id=actor_user.id,
        email=actor_email,
        biography="",
    ).save()
    PracticeMember(
        practice_id=practice.id,
        doctor_id=actor.id,
        role="workforce",
        attested_at=datetime.now(timezone.utc),
        attestation_name="Synthetic Workforce",
    ).save()

    target = Doctor(
        clerk_id=f"user_{secrets.token_hex(8)}",
        email=f"target-{secrets.token_hex(4)}@{practice.email_domain}",
        biography="",
    ).save()
    target_member = PracticeMember(
        practice_id=practice.id,
        doctor_id=target.id,
        role="workforce",
        seat_state=(
            ClinicianSeatState.inactive.value
            if operation == "reactivate_member"
            else ClinicianSeatState.active.value
        ),
        attested_at=datetime.now(timezone.utc),
        attestation_name="Synthetic Target",
    ).save()

    def forbidden_stripe_call(*_args, **_kwargs):
        raise AssertionError("workforce denial must happen before Stripe I/O")

    monkeypatch.setattr(
        internal.stripe_billing,
        "refresh_subscription_state",
        forbidden_stripe_call,
    )
    monkeypatch.setattr(
        internal.stripe_billing,
        "create_checkout_session",
        forbidden_stripe_call,
    )
    monkeypatch.setattr(
        internal.stripe_billing,
        "create_portal_session",
        forbidden_stripe_call,
    )
    bare_authenticated_client.override_user(actor_user)  # type: ignore[attr-defined]
    before = _public_practice_write_snapshot(practice)

    if operation == "accept_baa":
        response = bare_authenticated_client.post(
            api_app.url_path_for("accept_practice_baa"),
            json={
                "signature_name": "Synthetic Workforce",
                "signature_title": "Clinician",
                "checks": ["baa"],
            },
        )
    elif operation == "accept_public_legal":
        response = bare_authenticated_client.post(
            api_app.url_path_for("accept_public_legal"),
            json={
                "signature_name": "Synthetic Workforce",
                "signature_title": "Clinician",
                "authority_confirmed": True,
                "documents": [
                    {
                        "kind": document.kind.value,
                        "version": document.version,
                        "sha256": document.sha256,
                    }
                    for document in CURRENT_LEGAL_DOCUMENTS
                ],
            },
        )
    elif operation == "list_invitations":
        response = bare_authenticated_client.get(
            api_app.url_path_for("practice_invitations")
        )
    elif operation == "create_invitation":
        response = bare_authenticated_client.post(
            api_app.url_path_for("create_practice_invitation"),
            json={
                "email": f"denied-{secrets.token_hex(4)}@example.com",
                "expires_in_days": 7,
            },
            headers={"Idempotency-Key": f"denied-{secrets.token_hex(12)}"},
        )
    elif operation == "revoke_invitation":
        response = bare_authenticated_client.post(
            api_app.url_path_for(
                "revoke_practice_invitation",
                invitation_id=invitation.id,
            ),
            json={"reason": "Workforce must not revoke invitations."},
        )
    elif operation == "list_members":
        response = bare_authenticated_client.get(
            api_app.url_path_for("practice_members")
        )
    elif operation == "deactivate_member":
        response = bare_authenticated_client.post(
            api_app.url_path_for(
                "deactivate_practice_member",
                member_id=target_member.id,
            )
        )
    elif operation == "reactivate_member":
        response = bare_authenticated_client.post(
            api_app.url_path_for(
                "reactivate_practice_member",
                member_id=target_member.id,
            )
        )
    elif operation == "create_billing_checkout":
        response = bare_authenticated_client.post(
            api_app.url_path_for("create_billing_checkout"),
            json={},
            headers={"origin": "https://cosma.localhost"},
        )
    elif operation == "create_billing_portal":
        response = bare_authenticated_client.post(
            api_app.url_path_for("create_billing_portal"),
            json={},
            headers={"origin": "https://cosma.localhost"},
        )
    else:  # pragma: no cover - the parametrization is the exhaustive dispatch
        raise AssertionError(f"unknown owner-only operation: {operation}")

    assert response.status_code == status.HTTP_403_FORBIDDEN, response.text
    assert _public_practice_write_snapshot(practice) == before


def test_new_membership_cannot_insert_preapproved_credentials(
    bare_authenticated_client: TestClient,
) -> None:
    _authed_doctor()
    practice = _create_public_practice(bare_authenticated_client)
    clinician = Doctor(
        clerk_id=f"user_{secrets.token_hex(8)}",
        email=f"credential-{secrets.token_hex(4)}@example.com",
        biography="",
    ).save()

    with pytest.raises(DBAPIError, match="must begin with pending credentials"):
        with get_session() as session:
            session.add(
                PracticeMember(
                    practice_id=practice.id,
                    doctor_id=clinician.id,
                    role="workforce",
                    credential_state="approved",
                    credential_reviewed_at=datetime.now(timezone.utc),
                    credential_reviewer_ref="unconfigured-reviewer",
                    credential_verification_method="unconfigured-method",
                    credential_evidence_sha256="a" * 64,
                )
            )
            session.commit()

    assert PracticeMember.get(doctor_id=clinician.id) is None


def test_invitation_requires_completed_owner_legal_boundary(
    bare_authenticated_client: TestClient,
) -> None:
    owner, _ = _authed_doctor()
    practice = _create_public_practice(bare_authenticated_client)

    response = bare_authenticated_client.post(
        api_app.url_path_for("create_practice_invitation"),
        json={"email": "not-yet@example.com", "expires_in_days": 7},
        headers={"Idempotency-Key": f"invite-{secrets.token_hex(12)}"},
    )

    assert response.status_code == status.HTTP_409_CONFLICT
    assert (
        PracticeInvitation.where(PracticeInvitation.practice_id == practice.id).count()
        == 0
    )
    assert owner.id


def test_invitation_accept_deactivate_and_reactivate_are_authoritative(
    bare_authenticated_client: TestClient,
) -> None:
    owner, owner_user = _authed_doctor()
    practice = _create_public_practice(bare_authenticated_client)
    _accept_baa_and_public_legal(bare_authenticated_client)
    invitee_email = f"invitee-{secrets.token_hex(4)}@example.com"
    create_body = {"email": invitee_email, "expires_in_days": 7}
    headers = {"Idempotency-Key": f"invite-{secrets.token_hex(12)}"}
    created = bare_authenticated_client.post(
        api_app.url_path_for("create_practice_invitation"),
        json=create_body,
        headers=headers,
    )
    assert created.status_code == status.HTTP_201_CREATED, created.text
    token = created.json()["invite_token"]
    assert token

    retried = bare_authenticated_client.post(
        api_app.url_path_for("create_practice_invitation"),
        json=create_body,
        headers=headers,
    )
    assert retried.status_code == status.HTTP_201_CREATED
    assert retried.json()["invitation_id"] == created.json()["invitation_id"]
    assert retried.json()["invite_token"] == token
    assert (
        PracticeInvitation.where(PracticeInvitation.practice_id == practice.id).count()
        == 1
    )
    conflict = bare_authenticated_client.post(
        api_app.url_path_for("create_practice_invitation"),
        json={"email": invitee_email, "expires_in_days": 8},
        headers=headers,
    )
    assert conflict.status_code == status.HTTP_409_CONFLICT

    duplicate = bare_authenticated_client.post(
        api_app.url_path_for("create_practice_invitation"),
        json=create_body,
        headers={"Idempotency-Key": f"duplicate-{secrets.token_hex(12)}"},
    )
    assert duplicate.status_code == status.HTTP_409_CONFLICT

    self_invite = bare_authenticated_client.post(
        api_app.url_path_for("create_practice_invitation"),
        json={"email": owner.email, "expires_in_days": 7},
        headers={"Idempotency-Key": f"self-{secrets.token_hex(12)}"},
    )
    assert self_invite.status_code == status.HTTP_409_CONFLICT

    invitee_user = FakeClerkUser(
        id=f"user_{secrets.token_hex(8)}",
        email_addresses=[FakeEmailAddress(email_address=invitee_email)],
    )
    invitee = Doctor(
        clerk_id=invitee_user.id,
        email=invitee_email,
        biography="",
    ).save()
    legacy_token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    PracticeInvitation(
        practice_id=practice.id,
        invited_by_doctor_id=owner.id,
        token_digest=hashlib.sha256(legacy_token.encode()).hexdigest(),
        invitee_email=invitee_email,
        invitee_email_digest=public_onboarding._email_digest(
            practice.id, invitee_email
        ),
        idempotency_key_digest=hashlib.sha256(
            f"legacy:{legacy_token}".encode()
        ).hexdigest(),
        issued_at=now,
        expires_at=now + timedelta(days=7),
    ).save()
    bare_authenticated_client.override_user(invitee_user)  # type: ignore[attr-defined]
    accepted = bare_authenticated_client.post(
        api_app.url_path_for("accept_practice_invitation"),
        json={
            "token": token,
            "attestation_name": "Synthetic Invitee",
            "checks": ["workforce", "not-party", "direction", "phi"],
        },
    )
    assert accepted.status_code == status.HTTP_200_OK, accepted.text
    member_id = accepted.json()["member_id"]
    with get_session() as session:
        assert active_clinician_seat_count(session, practice.id) == 2

    bare_authenticated_client.override_user(owner_user)  # type: ignore[attr-defined]
    deactivated = bare_authenticated_client.post(
        api_app.url_path_for("deactivate_practice_member", member_id=member_id)
    )
    assert deactivated.status_code == status.HTTP_200_OK
    assert deactivated.json()["seat_state"] == "inactive"
    with get_session() as session:
        assert active_clinician_seat_count(session, practice.id) == 1

    bare_authenticated_client.override_user(invitee_user)  # type: ignore[attr-defined]
    hidden_evidence = bare_authenticated_client.get(
        api_app.url_path_for("practice_baa_acceptances")
    )
    assert hidden_evidence.status_code == status.HTTP_403_FORBIDDEN

    duplicate_reactivation = bare_authenticated_client.post(
        api_app.url_path_for("accept_practice_invitation"),
        json={
            "token": legacy_token,
            "attestation_name": "Synthetic Invitee",
            "checks": ["workforce", "not-party", "direction", "phi"],
        },
    )
    assert duplicate_reactivation.status_code == status.HTTP_409_CONFLICT
    assert duplicate_reactivation.json()["detail"].startswith(
        "This account is already a practice member"
    )

    replayed = bare_authenticated_client.post(
        api_app.url_path_for("accept_practice_invitation"),
        json={
            "token": token,
            "attestation_name": "Synthetic Invitee",
            "checks": ["workforce", "not-party", "direction", "phi"],
        },
    )
    assert replayed.status_code == status.HTTP_200_OK
    assert replayed.json()["seat_state"] == "inactive"
    with get_session() as session:
        assert active_clinician_seat_count(session, practice.id) == 1

    bare_authenticated_client.override_user(owner_user)  # type: ignore[attr-defined]
    reactivated = bare_authenticated_client.post(
        api_app.url_path_for("reactivate_practice_member", member_id=member_id)
    )
    assert reactivated.status_code == status.HTTP_200_OK
    assert reactivated.json()["seat_state"] == "active"
    assert PracticeMember.get(doctor_id=invitee.id).credential_state == "pending"  # type: ignore[union-attr]
    assert owner.id != invitee.id


@pytest.mark.parametrize("terminal_state", ("revoked", "expired"))
def test_terminal_invitation_cannot_create_membership_or_change_seat_count(
    bare_authenticated_client: TestClient,
    terminal_state: str,
) -> None:
    owner, _ = _authed_doctor()
    practice = _create_public_practice(bare_authenticated_client, solo=False)
    _accept_baa_and_public_legal(bare_authenticated_client)
    suffix = secrets.token_hex(8)
    invitee_email = f"terminal-{suffix}@example.com"

    if terminal_state == "revoked":
        token, invitation = _create_public_invitation(
            bare_authenticated_client,
            invitee_email,
        )
        revoked = bare_authenticated_client.post(
            api_app.url_path_for(
                "revoke_practice_invitation",
                invitation_id=invitation.id,
            ),
            json={"reason": "Invitation withdrawn before acceptance."},
        )
        assert revoked.status_code == status.HTTP_200_OK, revoked.text
        invitation.refresh()
        assert invitation.revoked_at is not None
    else:
        token = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)
        invitation = PracticeInvitation(
            practice_id=practice.id,
            invited_by_doctor_id=owner.id,
            token_digest=hashlib.sha256(token.encode()).hexdigest(),
            invitee_email=invitee_email,
            invitee_email_digest=public_onboarding._email_digest(
                practice.id,
                invitee_email,
            ),
            idempotency_key_digest=hashlib.sha256(
                f"expired:{token}".encode()
            ).hexdigest(),
            issued_at=now - timedelta(days=2),
            expires_at=now - timedelta(days=1),
        ).save()

    invitee_user = FakeClerkUser(
        id=f"user_{suffix}",
        email_addresses=[FakeEmailAddress(email_address=invitee_email)],
    )
    invitee = Doctor(
        clerk_id=invitee_user.id,
        email=invitee_email,
        biography="",
    ).save()
    bare_authenticated_client.override_user(invitee_user)  # type: ignore[attr-defined]
    before = _public_practice_write_snapshot(practice)
    membership_count = PracticeMember.count()

    response = bare_authenticated_client.post(
        api_app.url_path_for("accept_practice_invitation"),
        json={
            "token": token,
            "attestation_name": "Synthetic Invitee",
            "checks": ["workforce", "not-party", "direction", "phi"],
        },
    )

    assert response.status_code == status.HTTP_409_CONFLICT, response.text
    assert response.json()["detail"] == "This invitation is no longer active."
    assert PracticeMember.count() == membership_count
    assert PracticeMember.get(doctor_id=invitee.id) is None
    assert _public_practice_write_snapshot(practice) == before


def test_live_verified_email_mismatch_rejects_public_mutation_before_write(
    bare_authenticated_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _authed_doctor()
    practice = _create_public_practice(bare_authenticated_client)
    _accept_baa_and_public_legal(bare_authenticated_client)
    before = _public_practice_write_snapshot(practice)
    monkeypatch.setattr(
        public_onboarding,
        "verified_primary_email",
        lambda _clerk_id: "different-verified-account@example.com",
    )

    response = bare_authenticated_client.post(
        api_app.url_path_for("create_practice_invitation"),
        json={
            "email": f"must-not-exist-{secrets.token_hex(4)}@example.com",
            "expires_in_days": 7,
        },
        headers={"Idempotency-Key": f"mismatch-{secrets.token_hex(12)}"},
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN, response.text
    assert response.json()["detail"] == (
        "Verify the current account email, then sign in again."
    )
    assert _public_practice_write_snapshot(practice) == before


def test_invitation_cannot_steal_account_bound_to_another_practice(
    bare_authenticated_client: TestClient,
) -> None:
    _authed_doctor()
    invited_practice = _create_public_practice(
        bare_authenticated_client,
        solo=False,
    )
    _accept_baa_and_public_legal(bare_authenticated_client)
    suffix = secrets.token_hex(8)
    invitee_email = f"already-bound-{suffix}@bound-{suffix}.example.com"
    token, invitation = _create_public_invitation(
        bare_authenticated_client,
        invitee_email,
    )

    invitee_user = FakeClerkUser(
        id=f"user_{suffix}",
        email_addresses=[FakeEmailAddress(email_address=invitee_email)],
    )
    invitee = Doctor(
        clerk_id=invitee_user.id,
        email=invitee_email,
        biography="",
    ).save()
    existing_practice = Practice(
        legal_name="Existing Bound Practice, PLLC",
        join_code=f"B{secrets.token_hex(4).upper()[:7]}",
        email_domain=f"bound-{suffix}.example.com",
        origin_channel="direct",
        public_onboarding_required=True,
        public_pilot_cohort=True,
    ).save()
    existing_member = PracticeMember(
        practice_id=existing_practice.id,
        doctor_id=invitee.id,
        role="owner",
        attested_at=datetime.now(timezone.utc),
        attestation_name="Already Bound Owner",
    ).save()
    bare_authenticated_client.override_user(invitee_user)  # type: ignore[attr-defined]
    invited_before = _public_practice_write_snapshot(invited_practice)
    existing_before = _public_practice_write_snapshot(existing_practice)

    response = bare_authenticated_client.post(
        api_app.url_path_for("accept_practice_invitation"),
        json={
            "token": token,
            "attestation_name": "Already Bound Owner",
            "checks": ["workforce", "not-party", "direction", "phi"],
        },
    )

    assert response.status_code == status.HTTP_409_CONFLICT, response.text
    assert response.json()["detail"] == (
        "This account already belongs to another practice."
    )
    existing_member.refresh()
    invitation.refresh()
    assert existing_member.practice_id == existing_practice.id
    assert existing_member.role == "owner"
    assert invitation.accepted_at is None
    assert invitation.accepted_by_doctor_id is None
    assert _public_practice_write_snapshot(invited_practice) == invited_before
    assert _public_practice_write_snapshot(existing_practice) == existing_before


def test_capability_contract_denies_pending_credentials_and_dark_tools(
    bare_authenticated_client: TestClient,
) -> None:
    _authed_doctor()
    practice = _create_public_practice(bare_authenticated_client)
    practice.baa_accepted_at = datetime.now(timezone.utc)
    practice.billing_exempt = True
    practice.save()

    response = bare_authenticated_client.get(
        api_app.url_path_for("public_capabilities")
    )
    assert response.status_code == status.HTTP_200_OK, response.text
    payload = response.json()
    assert payload["contract_version"] == "public-capability-entitlements.v1"
    assert tuple(payload["capabilities"]) == (
        "soap_note",
        "intake_prep",
        "session_prep",
        "scribe",
        "cab",
    )
    assert payload["capabilities"]["soap_note"]["decision"] == "denied"
    assert "credential_pending" in payload["capabilities"]["soap_note"]["reason_codes"]
    for key in ("intake_prep", "session_prep", "scribe", "cab"):
        assert "rollout_disabled" in payload["capabilities"][key]["reason_codes"]
    assert payload["seats"]["maximum_active_clinician_seats"] == 20
    assert payload["seats"]["active_clinician_seats"] == 1
    assert payload["gates"]["credential_state"] == "pending"
    assert payload["gates"]["terms_accepted"] is False
    assert payload["gates"]["privacy_accepted"] is False


def test_capability_endpoint_matches_pending_credential_contract_fixture(
    bare_authenticated_client: TestClient,
) -> None:
    _authed_doctor()
    practice = _create_public_practice(bare_authenticated_client)
    _accept_baa_and_public_legal(bare_authenticated_client)
    practice.refresh()
    practice.subscription_status = "trialing"
    practice.save()

    unbound = bare_authenticated_client.get(api_app.url_path_for("public_capabilities"))
    assert unbound.status_code == status.HTTP_200_OK, unbound.text
    assert unbound.json()["gates"]["subscription_state"] == "invalid"
    assert (
        "subscription_inactive"
        in (unbound.json()["capabilities"]["soap_note"]["reason_codes"])
    )

    practice.stripe_subscription_id = f"sub_test_{secrets.token_hex(8)}"
    practice.stripe_customer_id = f"cus_test_{secrets.token_hex(8)}"
    practice.stripe_state_version = 1
    practice.save()

    response = bare_authenticated_client.get(
        api_app.url_path_for("public_capabilities")
    )

    assert response.status_code == status.HTTP_200_OK, response.text
    fixture = json.loads(
        (
            ROOT
            / "contracts"
            / "public-entitlements"
            / "v1"
            / "synthetic-credential-pending.json"
        ).read_text()
    )
    assert response.json()["capabilities"] == fixture["capabilities"]


def test_capability_endpoint_is_public_only(
    bare_authenticated_client: TestClient,
) -> None:
    doctor, _ = _authed_doctor()
    practice = _create_public_practice(bare_authenticated_client)
    practice.public_onboarding_required = False
    practice.save()

    response = bare_authenticated_client.get(
        api_app.url_path_for("public_capabilities")
    )

    assert response.status_code == status.HTTP_409_CONFLICT
    assert doctor.id


def test_owner_cannot_mutate_member_from_another_practice(
    bare_authenticated_client: TestClient,
) -> None:
    _authed_doctor()
    _create_public_practice(bare_authenticated_client)
    other_practice = Practice(
        legal_name="Other Synthetic Practice",
        join_code=f"O{secrets.token_hex(4).upper()[:7]}",
        email_domain="other.example.com",
        origin_channel="direct",
    ).save()
    other_doctor = Doctor(
        clerk_id=f"user_{secrets.token_hex(8)}",
        email=f"other-{secrets.token_hex(3)}@example.com",
        biography="",
    ).save()
    other_member = PracticeMember(
        practice_id=other_practice.id,
        doctor_id=other_doctor.id,
        role="workforce",
    ).save()

    response = bare_authenticated_client.post(
        api_app.url_path_for("deactivate_practice_member", member_id=other_member.id)
    )
    assert response.status_code == status.HTTP_404_NOT_FOUND
    other_member.refresh()
    assert other_member.seat_state == ClinicianSeatState.active.value


def test_twenty_fresh_accounts_resume_to_the_pending_credential_boundary(
    bare_authenticated_client: TestClient,
) -> None:
    """Exercise the safe public cold start repeatedly without external services.

    Stripe's verified projection is injected after checking the incomplete and
    verification-pending states; no credential decision is fabricated. Each
    account must resume across every persisted boundary and finish at the
    deliberately non-PHI pending-credential state.
    """

    successes = 0
    failures: list[str] = []
    for ordinal in range(20):
        stage = "identity"
        try:
            suffix = secrets.token_hex(8)
            email = f"clinician-{ordinal}@cold-{suffix}.example.com"
            clerk_user = FakeClerkUser(
                id=f"user_cold_start_{suffix}",
                email_addresses=[FakeEmailAddress(email_address=email)],
            )
            doctor = Doctor(
                clerk_id=clerk_user.id,
                email=email,
                biography="",
            ).save()
            bare_authenticated_client.override_user(clerk_user)  # type: ignore[attr-defined]

            initial = bare_authenticated_client.get(
                api_app.url_path_for("public_onboarding_state")
            )
            assert initial.status_code == status.HTTP_200_OK
            assert initial.json()["current_step"] == "practice"

            stage = "practice"
            created = bare_authenticated_client.post(
                api_app.url_path_for("create_practice"),
                json={
                    "legal_name": f"Synthetic Cold Start {ordinal}, PLLC",
                    "is_solo": True,
                    "channel": "direct",
                    "pastoral_pack": False,
                },
            )
            assert created.status_code == status.HTTP_200_OK
            member = PracticeMember.get(doctor_id=doctor.id)
            assert member is not None
            practice = Practice.get(id=member.practice_id)
            assert practice is not None
            resumed = bare_authenticated_client.get(
                api_app.url_path_for("public_onboarding_state")
            )
            assert resumed.json()["current_step"] == "baa"

            stage = "legal"
            _accept_baa_and_public_legal(bare_authenticated_client)
            legal_retry = bare_authenticated_client.post(
                api_app.url_path_for("accept_public_legal"),
                json={
                    "signature_name": "Synthetic Owner",
                    "signature_title": "Physician / owner",
                    "authority_confirmed": True,
                    "documents": [
                        {
                            "kind": document.kind.value,
                            "version": document.version,
                            "sha256": document.sha256,
                        }
                        for document in CURRENT_LEGAL_DOCUMENTS
                    ],
                },
            )
            assert legal_retry.status_code == status.HTTP_200_OK

            stage = "checkout"
            practice.refresh()
            practice.stripe_checkout_session_id = f"cs_test_cold_{suffix}"
            practice.subscription_status = "pending_verification"
            practice.trial_ends_at = datetime.now(timezone.utc) + timedelta(days=14)
            practice.save()
            pending_billing = bare_authenticated_client.get(
                api_app.url_path_for("public_onboarding_state")
            )
            assert pending_billing.json()["current_step"] == "checkout"
            assert pending_billing.json()["phi_access_allowed"] is False

            practice.stripe_subscription_id = f"sub_test_cold_{suffix}"
            practice.stripe_customer_id = f"cus_test_cold_{suffix}"
            practice.stripe_state_version = 1
            practice.subscription_status = "trialing"
            practice.save()
            awaiting_review = bare_authenticated_client.get(
                api_app.url_path_for("public_onboarding_state")
            )
            assert awaiting_review.json()["current_step"] == "credential"
            assert awaiting_review.json()["phi_access_allowed"] is False

            stage = "completion"
            completed = bare_authenticated_client.post(
                api_app.url_path_for("complete_onboarding")
            )
            assert completed.status_code == status.HTTP_200_OK
            retried = bare_authenticated_client.post(
                api_app.url_path_for("complete_onboarding")
            )
            assert retried.status_code == status.HTTP_200_OK
            doctor.refresh()
            assert doctor.onboarding_completed_at is not None

            stage = "capability"
            capabilities = bare_authenticated_client.get(
                api_app.url_path_for("public_capabilities")
            )
            assert capabilities.status_code == status.HTTP_200_OK
            soap = capabilities.json()["capabilities"]["soap_note"]
            assert soap["decision"] == "denied"
            assert soap["data_mode"] == "synthetic_only"
            successes += 1
        except AssertionError:
            failures.append(f"run-{ordinal}:{stage}")

    assert successes >= 19, {"successes": successes, "failures": failures}
