import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import cast

import pytest
from fastapi import status
from fastapi.exceptions import HTTPException
from fastapi.testclient import TestClient
from structlog.testing import capture_logs

from app.configuration.redis import get_redis
from app.routes import internal
from app.server import api_app
from app.services import stripe_billing
from app.services.public_entitlements import (
    MAX_ACTIVE_CLINICIAN_SEATS,
    ClinicianSeatPolicyError,
    active_clinician_seat_count,
)
from app.utils.email_domain import extract_email_domain

from activemodel.session_manager import get_session
from app.models.baa import (
    BAA_CURRENT_REFERENCE,
    BAA_CURRENT_SHA256,
    BAA_CURRENT_VERSION,
    BAA_DOC_ID,
    BaaAcceptance,
    BaaVersion,
)
from app.models.doctor import Doctor, DoctorRole
from app.models.organization_access import ProviderOrganization
from app.models.pilot_access_grandfather_grant import PilotAccessGrandfatherGrant
from app.models.practice import Practice, PracticeMember
from sqlalchemy import delete, update
from sqlalchemy.exc import DBAPIError
from sqlmodel import SQLModel

from tests.routes.utils import get_clerk_dev_user


def _authed_doctor() -> Doctor:
    _, _, clerk_user = get_clerk_dev_user()
    doctor = Doctor.find_or_create_by(clerk_id=clerk_user.id)
    assert doctor.email
    return doctor


def _create_practice(client: TestClient, **overrides) -> dict:
    body = {
        "legal_name": "Riverside Family Practice, PLLC",
        "is_solo": True,
        "channel": "direct",
        "pastoral_pack": False,
    } | overrides
    resp = client.post(api_app.url_path_for("create_practice"), json=body)
    assert resp.status_code == status.HTTP_200_OK, resp.text
    return resp.json()


def _accept_baa(client: TestClient) -> dict:
    resp = client.post(
        api_app.url_path_for("accept_practice_baa"),
        json={
            "doc_id": BAA_DOC_ID,
            "version": BAA_CURRENT_VERSION,
            "sha256": BAA_CURRENT_SHA256,
            "signature_name": "Pat Owner",
            "signature_title": "Physician / owner",
            "checks": ["baa"],
        },
    )
    assert resp.status_code == status.HTTP_200_OK, resp.text
    return resp.json()


def _own_practice(doctor: Doctor) -> Practice:
    """The practice this test's doctor belongs to. Never query practices or
    acceptances globally: the session seeds provision their own practice."""
    member = PracticeMember.get(doctor_id=doctor.id)
    assert member
    practice = Practice.get(id=member.practice_id)
    assert practice
    return practice


@pytest.fixture(autouse=True)
def _verified_clerk_email_matches_bound_doctor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep route tests offline while preserving the live-identity invariant."""

    def current_email(clerk_id: str) -> str | None:
        doctor = Doctor.get(clerk_id=clerk_id)
        return (doctor.email or "").strip().lower() if doctor else None

    monkeypatch.setattr(internal, "verified_primary_email", current_email)


def _grant_pilot_access(
    doctor: Doctor,
    *,
    practice: Practice | None = None,
) -> PilotAccessGrandfatherGrant:
    now = datetime.now(timezone.utc)
    email = doctor.email.lower()
    return PilotAccessGrandfatherGrant(
        clerk_id=doctor.clerk_id,
        doctor_id_snapshot=doctor.id,
        practice_id_snapshot=practice.id if practice else None,
        email_snapshot=email,
        email_sha256=hashlib.sha256(email.encode()).hexdigest(),
        source_ref="test:practice-route",
        source_sha256="a" * 64,
        grant_plan_sha256="b" * 64,
        granted_by_ref="test-suite",
        grant_reason="Test exact-account access",
        granted_at=now - timedelta(minutes=1),
    ).save()


def test_complete_onboarding_requires_practice(
    bare_authenticated_client: TestClient,
):
    doctor = _authed_doctor()
    assert doctor.onboarding_completed_at is None
    assert PracticeMember.get(doctor_id=doctor.id) is None

    resp = bare_authenticated_client.post(api_app.url_path_for("complete_onboarding"))
    assert resp.status_code == status.HTTP_400_BAD_REQUEST
    assert "practice" in resp.json()["detail"].lower()


def test_pilot_grant_cannot_complete_onboarding_without_practice(
    bare_authenticated_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    doctor = _authed_doctor()
    assert doctor.onboarding_completed_at is None
    assert PracticeMember.get(doctor_id=doctor.id) is None
    _grant_pilot_access(doctor)
    monkeypatch.setattr(
        "app.routes.dependencies.doctor.verified_primary_email",
        lambda _clerk_id: doctor.email,
    )

    settings = bare_authenticated_client.get(api_app.url_path_for("settings_data"))
    assert settings.status_code == status.HTTP_200_OK
    assert settings.json()["onboarding_completed"] is False
    assert settings.json()["practice_ready"] is False
    assert settings.json()["billing_recovery_required"] is False

    notes = bare_authenticated_client.get(api_app.url_path_for("note_list"))
    assert notes.status_code == status.HTTP_403_FORBIDDEN

    resp = bare_authenticated_client.post(api_app.url_path_for("complete_onboarding"))
    assert resp.status_code == status.HTTP_400_BAD_REQUEST
    doctor.refresh()
    assert doctor.onboarding_completed_at is None


def test_legacy_role_and_pilot_grant_cannot_bypass_membership(
    bare_authenticated_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    doctor = _authed_doctor()
    doctor.role = DoctorRole.admin
    doctor.save()
    _grant_pilot_access(doctor)
    monkeypatch.setattr(
        "app.routes.dependencies.doctor.verified_primary_email",
        lambda _clerk_id: doctor.email,
    )

    response = bare_authenticated_client.get(api_app.url_path_for("note_list"))

    assert response.status_code == status.HTTP_403_FORBIDDEN


def test_pilot_grant_does_not_activate_before_practice_gates(
    bare_authenticated_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    doctor = _authed_doctor()
    _create_practice(bare_authenticated_client)
    practice = _own_practice(doctor)
    _grant_pilot_access(doctor, practice=practice)
    monkeypatch.setattr(
        "app.routes.dependencies.doctor.verified_primary_email",
        lambda _clerk_id: doctor.email,
    )

    response = bare_authenticated_client.get(api_app.url_path_for("practice_status"))

    assert response.status_code == status.HTTP_200_OK
    payload = response.json()
    assert payload["pilot_access_grant_active"] is False
    assert payload["baa_accepted"] is False
    assert payload["access_authorized"] is False
    assert payload["entitled"] is False


def test_create_practice_stamps_email_domain(bare_authenticated_client: TestClient):
    doctor = _authed_doctor()
    domain = extract_email_domain(doctor.email)
    assert domain

    body = _create_practice(bare_authenticated_client, city="Nashua", region="NH")
    assert body["legal_name"].startswith("Riverside")
    assert body["role"] == "owner"
    assert body["baa_accepted"] is False
    assert body["email_domain"] == domain
    assert body["join_code"] is None
    practice = _own_practice(doctor)
    assert practice.is_solo is True
    assert practice.join_code


def test_group_practice_exposes_join_code_for_owned_domain(
    bare_authenticated_client: TestClient,
):
    doctor = _authed_doctor()

    body = _create_practice(bare_authenticated_client, is_solo=False)

    assert body["join_code"]
    assert _own_practice(doctor).is_solo is False


def test_public_email_domain_can_only_create_solo_practice(
    bare_authenticated_client: TestClient,
):
    doctor = _authed_doctor()
    doctor.email = f"doctor-{secrets.token_hex(4)}@gmail.com"
    doctor.save()

    response = bare_authenticated_client.post(
        api_app.url_path_for("create_practice"),
        json={
            "legal_name": "Public Mail Group",
            "is_solo": False,
            "channel": "direct",
            "pastoral_pack": False,
        },
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "practice-owned work email domain" in response.json()["detail"]
    assert PracticeMember.get(doctor_id=doctor.id) is None


def test_public_email_solo_hides_join_code_and_cannot_enable_future_pack(
    bare_authenticated_client: TestClient,
):
    doctor = _authed_doctor()
    doctor.email = f"doctor-{secrets.token_hex(4)}@gmail.com"
    doctor.save()

    body = _create_practice(bare_authenticated_client, pastoral_pack=True)
    practice = _own_practice(doctor)

    assert body["join_code"] is None
    assert body["pastoral_pack"] is False
    assert practice.join_code
    assert practice.pastoral_pack is False


def test_create_practice_rejects_a_stale_bound_email(
    bare_authenticated_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    doctor = _authed_doctor()
    monkeypatch.setattr(
        internal,
        "verified_primary_email",
        lambda _clerk_id: "replacement@outside.example",
    )

    response = bare_authenticated_client.post(
        api_app.url_path_for("create_practice"),
        json={
            "legal_name": "Stale Identity Practice",
            "is_solo": True,
            "channel": "direct",
            "pastoral_pack": False,
        },
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert "current verified email" in response.json()["detail"]
    assert PracticeMember.get(doctor_id=doctor.id) is None


def test_create_practice_rechecks_live_email_while_rows_are_locked(
    bare_authenticated_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    doctor = _authed_doctor()
    live_emails = iter([doctor.email, "replacement@outside.example"])
    monkeypatch.setattr(
        internal,
        "verified_primary_email",
        lambda _clerk_id: next(live_emails),
    )

    response = bare_authenticated_client.post(
        api_app.url_path_for("create_practice"),
        json={
            "legal_name": "Racing Identity Practice",
            "is_solo": True,
            "channel": "direct",
            "pastoral_pack": False,
        },
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert PracticeMember.get(doctor_id=doctor.id) is None


def test_create_practice_rejects_npi_and_never_persists_it(
    bare_authenticated_client: TestClient,
):
    doctor = _authed_doctor()

    response = bare_authenticated_client.post(
        api_app.url_path_for("create_practice"),
        json={
            "legal_name": "NPI-Free Practice",
            "npi": "1234567893",
            "is_solo": True,
            "channel": "direct",
            "pastoral_pack": False,
        },
    )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert "extra_forbidden" in response.text
    assert PracticeMember.get(doctor_id=doctor.id) is None

    _create_practice(bare_authenticated_client)
    practice = _own_practice(doctor)
    assert practice.npi is None
    assert practice.acquisition_source == "public_self_service"


@pytest.mark.parametrize("reservation", ["bootstrap", "organization"])
def test_organization_reserved_domain_blocks_independent_practice_creation(
    bare_authenticated_client: TestClient,
    reservation: str,
):
    doctor = _authed_doctor()
    domain = extract_email_domain(doctor.email)
    assert domain
    if reservation == "bootstrap":
        Practice(
            legal_name="Administratively Bootstrapped Practice",
            join_code=f"G{secrets.token_hex(4).upper()[:7]}",
            email_domain=domain,
            origin_channel="direct",
            negotiated_baa_required=True,
        ).save()
    else:
        ProviderOrganization(
            legal_name="Reserved Provider Organization",
            slug=f"reserved-{secrets.token_hex(4)}",
            email_domain=domain,
            status="active",
            access_code_salt="a" * 32,
            access_code_digest=secrets.token_hex(32),
            access_code_version=1,
            access_code_created_at=datetime.now(timezone.utc),
        ).save()
    before = Practice.where(Practice.email_domain == domain).count()

    response = bare_authenticated_client.post(
        api_app.url_path_for("create_practice"),
        json={
            "legal_name": "Unauthorized Sibling Practice",
            "is_solo": True,
            "channel": "direct",
            "pastoral_pack": False,
        },
    )

    assert response.status_code == status.HTTP_409_CONFLICT
    assert "organization-managed" in response.json()["detail"]
    assert Practice.where(Practice.email_domain == domain).count() == before
    assert PracticeMember.get(doctor_id=doctor.id) is None


def test_owner_baa_then_complete(bare_authenticated_client: TestClient):
    doctor = _authed_doctor()
    _create_practice(bare_authenticated_client)

    blocked = bare_authenticated_client.post(
        api_app.url_path_for("complete_onboarding")
    )
    assert blocked.status_code == status.HTTP_400_BAD_REQUEST

    accepted = _accept_baa(bare_authenticated_client)
    assert accepted["baa_accepted"] is True
    assert accepted["attested"] is True

    done = bare_authenticated_client.post(api_app.url_path_for("complete_onboarding"))
    assert done.status_code == status.HTTP_200_OK
    doctor.refresh()
    assert doctor.onboarding_completed_at is not None


def test_current_baa_endpoint_serves_the_v23_asset(
    bare_authenticated_client: TestClient,
):
    _authed_doctor()

    response = bare_authenticated_client.get(api_app.url_path_for("baa_current"))

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body["doc_id"] == BAA_DOC_ID
    assert body["version"] == BAA_CURRENT_VERSION
    assert body["sha256"] == BAA_CURRENT_SHA256
    assert "claims preparation and submission support" not in body["html"]


def test_prior_clickwrap_requires_reacceptance_of_current_baa(
    bare_authenticated_client: TestClient,
):
    doctor = _authed_doctor()
    _create_practice(bare_authenticated_client)
    practice = _own_practice(doctor)
    member = PracticeMember.get(doctor_id=doctor.id)
    assert member

    accepted_at = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
    old_version = BaaVersion(
        doc_id=BAA_DOC_ID,
        version="2.2",
        sha256="13448bf42105db05e36e566f2f8ea0190926223e9dbb03efb8778cf8cfc7332e",
        content_path="app/assets/baa/baa_cn-baa-cust-cw_v2.2.html",
        published_at=accepted_at,
        is_current=False,
    ).save()
    old_acceptance = BaaAcceptance(
        practice_id=practice.id,
        baa_version_id=old_version.id,
        covered_entity_legal_name=practice.legal_name,
        accepted_by_doctor_id=doctor.id,
        accepted_by_name="Pat Owner",
        accepted_by_title="Physician / owner",
        accepted_by_email=doctor.email or "test@example.com",
        accepted_at_utc=accepted_at,
        content_sha256=old_version.sha256,
    ).save()
    practice.baa_version = f"{BAA_DOC_ID} v2.2"
    practice.baa_accepted_at = accepted_at
    practice.baa_accepted_by_doctor_id = doctor.id
    practice.baa_signature_name = "Pat Owner"
    practice.baa_signature_title = "Physician / owner"
    practice.save()
    member.attested_at = accepted_at
    member.attestation_name = "Pat Owner"
    member.save()

    stale_status = bare_authenticated_client.get(
        api_app.url_path_for("practice_status")
    )
    assert stale_status.status_code == status.HTTP_200_OK
    assert stale_status.json()["baa_accepted"] is False
    blocked = bare_authenticated_client.post(
        api_app.url_path_for("complete_onboarding")
    )
    assert blocked.status_code == status.HTTP_400_BAD_REQUEST

    reaccepted = _accept_baa(bare_authenticated_client)
    practice.refresh()
    assert reaccepted["baa_accepted"] is True
    assert practice.baa_version == BAA_CURRENT_REFERENCE
    assert BaaAcceptance.get(id=old_acceptance.id) is not None
    current = BaaVersion.get(doc_id=BAA_DOC_ID, version=BAA_CURRENT_VERSION)
    assert current
    assert (
        BaaAcceptance.where(
            (BaaAcceptance.practice_id == practice.id)
            & (BaaAcceptance.baa_version_id == current.id)
        ).count()
        == 1
    )


def test_baa_acceptance_requires_checkbox(bare_authenticated_client: TestClient):
    _authed_doctor()
    _create_practice(bare_authenticated_client)

    resp = bare_authenticated_client.post(
        api_app.url_path_for("accept_practice_baa"),
        json={
            "doc_id": BAA_DOC_ID,
            "version": BAA_CURRENT_VERSION,
            "sha256": BAA_CURRENT_SHA256,
            "signature_name": "Pat Owner",
            "signature_title": "Physician / owner",
            "checks": [],
        },
    )
    assert resp.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.parametrize(
    "identity_override",
    [
        pytest.param({"doc_id": "CN-BAA-CUST-CW-OTHER"}, id="doc-id"),
        pytest.param({"version": "2.2"}, id="version"),
        pytest.param({"sha256": "0" * 64}, id="sha256"),
        pytest.param(
            {
                "version": "2.2",
                "sha256": (
                    "13448bf42105db05e36e566f2f8ea0190926223e9dbb03efb8778cf8cfc7332e"
                ),
            },
            id="stale-v2.2-tab",
        ),
    ],
)
def test_baa_acceptance_rejects_document_identity_mismatch(
    bare_authenticated_client: TestClient,
    identity_override: dict[str, str],
):
    doctor = _authed_doctor()
    _create_practice(bare_authenticated_client)
    practice = _own_practice(doctor)
    body = {
        "doc_id": BAA_DOC_ID,
        "version": BAA_CURRENT_VERSION,
        "sha256": BAA_CURRENT_SHA256,
        "signature_name": "Pat Owner",
        "signature_title": "Physician / owner",
        "checks": ["baa"],
    } | identity_override

    response = bare_authenticated_client.post(
        api_app.url_path_for("accept_practice_baa"),
        json=body,
    )

    assert response.status_code == status.HTTP_409_CONFLICT
    assert "reload and review" in response.json()["detail"].lower()
    assert BaaAcceptance.where(BaaAcceptance.practice_id == practice.id).count() == 0
    practice.refresh()
    assert practice.baa_version is None
    assert practice.baa_accepted_at is None


def test_negotiated_baa_required_blocks_clickwrap_and_legacy_join_paths(
    bare_authenticated_client: TestClient,
):
    doctor = _authed_doctor()
    _create_practice(bare_authenticated_client, is_solo=False)
    practice = _own_practice(doctor)
    practice.negotiated_baa_required = True
    practice.save()

    status_response = bare_authenticated_client.get(
        api_app.url_path_for("practice_status")
    )
    assert status_response.status_code == status.HTTP_200_OK
    assert status_response.json()["join_code"] is None

    clickwrap = bare_authenticated_client.post(
        api_app.url_path_for("accept_practice_baa"),
        json={
            "doc_id": BAA_DOC_ID,
            "version": BAA_CURRENT_VERSION,
            "sha256": BAA_CURRENT_SHA256,
            "signature_name": "Pat Owner",
            "signature_title": "Physician / owner",
            "checks": ["baa"],
        },
    )
    assert clickwrap.status_code == status.HTTP_409_CONFLICT

    lookup = bare_authenticated_client.post(
        api_app.url_path_for("lookup_practice"),
        json={"join_code": practice.join_code},
    )
    assert lookup.status_code == status.HTTP_200_OK
    assert lookup.json() == {"practice": None, "mismatch": False}

    join = bare_authenticated_client.post(
        api_app.url_path_for("join_practice"),
        json={
            "join_code": practice.join_code,
            "attestation_name": "Pat Owner",
            "checks": ["workforce", "not-party", "direction", "phi"],
        },
    )
    assert join.status_code == status.HTTP_409_CONFLICT

    practice.refresh()
    assert BaaAcceptance.where(BaaAcceptance.practice_id == practice.id).count() == 0
    assert practice.baa_version is None
    assert practice.baa_accepted_at is None
    assert practice.baa_accepted_by_doctor_id is None
    assert practice.baa_signature_name is None
    assert practice.baa_signature_title is None
    assert practice.trial_ends_at is None


def test_baa_acceptance_writes_append_only_record(
    bare_authenticated_client: TestClient,
):
    doctor = _authed_doctor()
    _create_practice(bare_authenticated_client)
    practice = _own_practice(doctor)

    def acceptance_count():
        return BaaAcceptance.where(BaaAcceptance.practice_id == practice.id).count()

    assert acceptance_count() == 0

    before = datetime.now(timezone.utc)
    _accept_baa(bare_authenticated_client)

    record = cast(
        BaaAcceptance,
        BaaAcceptance.where(BaaAcceptance.practice_id == practice.id).one(),
    )
    assert record.covered_entity_legal_name.startswith("Riverside")
    assert record.accepted_by_name == "Pat Owner"
    assert record.accepted_by_title == "Physician / owner"
    assert record.accepted_by_email == doctor.email
    assert record.content_sha256 == BAA_CURRENT_SHA256
    # server-generated timestamp, never the client clock
    assert before <= record.accepted_at_utc <= datetime.now(timezone.utc)

    # idempotent per (practice, version): re-accepting is a no-op
    _accept_baa(bare_authenticated_client)
    assert acceptance_count() == 1


def test_public_baa_acceptance_retry_repairs_legal_gate_without_starting_trial(
    bare_authenticated_client: TestClient,
):
    doctor = _authed_doctor()
    _create_practice(bare_authenticated_client)
    _accept_baa(bare_authenticated_client)
    practice = _own_practice(doctor)
    member = PracticeMember.get(doctor_id=doctor.id)
    assert member
    acceptance = cast(
        BaaAcceptance,
        BaaAcceptance.where(BaaAcceptance.practice_id == practice.id).one(),
    )
    original_accepted_at = acceptance.accepted_at_utc

    # Simulate the historical partial-commit failure: legal evidence exists,
    # but the denormalized legal gate and owner attestation are absent.
    practice.baa_version = None
    practice.baa_accepted_at = None
    practice.baa_accepted_by_doctor_id = None
    practice.baa_signature_name = None
    practice.baa_signature_title = None
    practice.trial_ends_at = None
    practice.save()
    member.attested_at = None
    member.attestation_name = None
    member.save()

    _accept_baa(bare_authenticated_client)
    practice.refresh()
    member.refresh()
    assert BaaAcceptance.where(BaaAcceptance.practice_id == practice.id).count() == 1
    assert practice.baa_accepted_at == original_accepted_at
    assert practice.trial_ends_at is None
    assert practice.baa_in_force()
    assert member.attested_at == original_accepted_at
    assert member.attestation_name == acceptance.accepted_by_name


def test_baa_acceptance_has_database_idempotency_constraint():
    table = SQLModel.metadata.tables["baa_acceptance"]
    constraints = {constraint.name for constraint in table.constraints}
    assert "baa_acceptance_practice_version_key" in constraints


def test_baa_acceptance_database_rejects_update_and_delete(
    bare_authenticated_client: TestClient,
):
    doctor = _authed_doctor()
    _create_practice(bare_authenticated_client)
    _accept_baa(bare_authenticated_client)
    practice = _own_practice(doctor)
    acceptance = cast(
        BaaAcceptance,
        BaaAcceptance.where(BaaAcceptance.practice_id == practice.id).one(),
    )
    table = SQLModel.metadata.tables["baa_acceptance"]

    with get_session() as session:
        savepoint = session.begin_nested()
        with pytest.raises(DBAPIError, match="append-only"):
            session.execute(
                update(table)
                .where(table.c.id == acceptance.id)
                .values(accepted_by_name="Mutation forbidden")
            )
        savepoint.rollback()

        savepoint = session.begin_nested()
        with pytest.raises(DBAPIError, match="append-only"):
            session.execute(delete(table).where(table.c.id == acceptance.id))
        savepoint.rollback()


def test_join_requires_matching_email_domain(bare_authenticated_client: TestClient):
    _authed_doctor()
    Practice(
        legal_name="MyCatholicDoctor Nashua",
        join_code="MCDNASHU",
        email_domain="mycatholicdoctor.com",
        origin_channel="mcd",
        pastoral_pack=True,
        is_solo=False,
        baa_accepted_at=datetime.now(timezone.utc),
        baa_version=BAA_CURRENT_REFERENCE,
    ).save()

    lookup = bare_authenticated_client.post(
        api_app.url_path_for("lookup_practice"),
        json={"join_code": "mcd-nashu"},
    )
    assert lookup.status_code == status.HTTP_200_OK, lookup.text
    body = lookup.json()
    assert body["practice"] is None
    # A bare flag. The client derives the signed-in domain from its own session.
    assert body["mismatch"] is True

    joined = bare_authenticated_client.post(
        api_app.url_path_for("join_practice"),
        json={
            "join_code": "MCDNASHU",
            "attestation_name": "Pat Clinician",
            "checks": ["workforce", "not-party", "direction", "phi"],
        },
    )
    assert joined.status_code == status.HTTP_404_NOT_FOUND
    assert joined.json()["detail"] == "No practice matches that join code."


def test_wrong_domain_lookup_and_join_disclose_no_practice_identity(
    bare_authenticated_client: TestClient,
):
    """A join code must not identify the covered entity that issued it.

    Everyone reaching these branches holds a code for a practice they are
    provably not workforce of. A legal name plus an email domain identifies a
    covered entity, so neither may appear in either response — including in an
    error detail, which is the easier of the two to reintroduce by accident.
    """

    _authed_doctor()
    practice = Practice(
        legal_name="MyCatholicDoctor Nashua",
        join_code="MCDNASHU",
        email_domain="mycatholicdoctor.com",
        origin_channel="mcd",
        pastoral_pack=True,
        is_solo=False,
        baa_accepted_at=datetime.now(timezone.utc),
        baa_version=BAA_CURRENT_REFERENCE,
    ).save()

    responses = [
        bare_authenticated_client.post(
            api_app.url_path_for("lookup_practice"),
            json={"join_code": "MCDNASHU"},
        ),
        bare_authenticated_client.post(
            api_app.url_path_for("join_practice"),
            json={
                "join_code": "MCDNASHU",
                "attestation_name": "Pat Clinician",
                "checks": ["workforce", "not-party", "direction", "phi"],
            },
        ),
    ]

    # Bound to the record, not to repeated literals: renaming the fixture must
    # not leave these assertions passing against a name nobody serves.
    for response in responses:
        assert practice.legal_name not in response.text, response.text
        assert practice.email_domain not in response.text, response.text


def test_unknown_and_wrong_domain_join_codes_are_indistinguishable(
    bare_authenticated_client: TestClient,
):
    """Otherwise the 404 body itself confirms which codes are real."""

    _authed_doctor()
    Practice(
        legal_name="MyCatholicDoctor Nashua",
        join_code="MCDNASHU",
        email_domain="mycatholicdoctor.com",
        origin_channel="mcd",
        pastoral_pack=True,
        is_solo=False,
        baa_accepted_at=datetime.now(timezone.utc),
        baa_version=BAA_CURRENT_REFERENCE,
    ).save()

    def join(code: str):
        return bare_authenticated_client.post(
            api_app.url_path_for("join_practice"),
            json={
                "join_code": code,
                "attestation_name": "Pat Clinician",
                "checks": ["workforce", "not-party", "direction", "phi"],
            },
        )

    real_but_wrong_domain = join("MCDNASHU")
    no_such_practice = join("ZZZZZZZZ")

    assert real_but_wrong_domain.status_code == no_such_practice.status_code
    assert real_but_wrong_domain.json() == no_such_practice.json()


def test_solo_practice_code_is_never_disclosed_or_accepted(
    bare_authenticated_client: TestClient,
):
    doctor = _authed_doctor()
    domain = extract_email_domain(doctor.email)
    assert domain
    practice = Practice(
        legal_name="Solo Practice, PLLC",
        join_code=f"S{secrets.token_hex(4).upper()[:7]}",
        email_domain=domain,
        origin_channel="direct",
        pastoral_pack=False,
        is_solo=True,
        baa_accepted_at=datetime.now(timezone.utc),
        baa_version=BAA_CURRENT_REFERENCE,
    ).save()

    lookup = bare_authenticated_client.post(
        api_app.url_path_for("lookup_practice"),
        json={"join_code": practice.join_code},
    )
    assert lookup.status_code == status.HTTP_200_OK
    assert lookup.json() == {"practice": None, "mismatch": False}

    joined = bare_authenticated_client.post(
        api_app.url_path_for("join_practice"),
        json={
            "join_code": practice.join_code,
            "attestation_name": "Pat Clinician",
            "checks": ["workforce", "not-party", "direction", "phi"],
        },
    )
    assert joined.status_code == status.HTTP_404_NOT_FOUND
    assert PracticeMember.get(doctor_id=doctor.id) is None


def test_legacy_public_domain_group_code_is_not_usable(
    bare_authenticated_client: TestClient,
):
    doctor = _authed_doctor()
    doctor.email = f"clinician-{secrets.token_hex(4)}@gmail.com"
    doctor.save()
    practice = Practice(
        legal_name="Legacy Public Mail Group",
        join_code=f"G{secrets.token_hex(4).upper()[:7]}",
        email_domain="gmail.com",
        origin_channel="direct",
        pastoral_pack=False,
        is_solo=False,
        baa_accepted_at=datetime.now(timezone.utc),
        baa_version=BAA_CURRENT_REFERENCE,
    ).save()

    lookup = bare_authenticated_client.post(
        api_app.url_path_for("lookup_practice"),
        json={"join_code": practice.join_code},
    )
    assert lookup.status_code == status.HTTP_200_OK
    assert lookup.json() == {"practice": None, "mismatch": False}

    joined = bare_authenticated_client.post(
        api_app.url_path_for("join_practice"),
        json={
            "join_code": practice.join_code,
            "attestation_name": "Pat Clinician",
            "checks": ["workforce", "not-party", "direction", "phi"],
        },
    )
    assert joined.status_code == status.HTTP_404_NOT_FOUND
    assert PracticeMember.get(doctor_id=doctor.id) is None


def test_lookup_does_not_disclose_practice_to_a_stale_bound_email(
    bare_authenticated_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    doctor = _authed_doctor()
    domain = extract_email_domain(doctor.email)
    assert domain
    practice = Practice(
        legal_name="Confidential Practice Name",
        join_code=f"V{secrets.token_hex(4).upper()[:7]}",
        email_domain=domain,
        origin_channel="direct",
        pastoral_pack=False,
        is_solo=False,
        baa_accepted_at=datetime.now(timezone.utc),
        baa_version=BAA_CURRENT_REFERENCE,
    ).save()
    monkeypatch.setattr(
        internal,
        "verified_primary_email",
        lambda _clerk_id: "replacement@outside.example",
    )

    response = bare_authenticated_client.post(
        api_app.url_path_for("lookup_practice"),
        json={"join_code": practice.join_code},
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert practice.legal_name not in response.text
    assert practice.email_domain not in response.text


def test_join_rechecks_live_email_while_rows_are_locked(
    bare_authenticated_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    doctor = _authed_doctor()
    domain = extract_email_domain(doctor.email)
    assert domain
    practice = Practice(
        legal_name="Live Identity Practice",
        join_code=f"V{secrets.token_hex(4).upper()[:7]}",
        email_domain=domain,
        origin_channel="direct",
        pastoral_pack=False,
        is_solo=False,
        baa_accepted_at=datetime.now(timezone.utc),
        baa_version=BAA_CURRENT_REFERENCE,
    ).save()
    live_emails = iter([doctor.email, "replacement@outside.example"])
    monkeypatch.setattr(
        internal,
        "verified_primary_email",
        lambda _clerk_id: next(live_emails),
    )

    response = bare_authenticated_client.post(
        api_app.url_path_for("join_practice"),
        json={
            "join_code": practice.join_code,
            "attestation_name": "Pat Clinician",
            "checks": ["workforce", "not-party", "direction", "phi"],
        },
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert PracticeMember.get(doctor_id=doctor.id) is None


def test_legacy_join_rate_limit_allows_ten_attempts_and_keeps_ttl():
    doctor = Doctor(
        clerk_id=f"user_join_rate_{secrets.token_hex(8)}",
        email=f"join-rate-{secrets.token_hex(4)}@example.com",
        biography="",
    ).save()
    key_id = hashlib.sha256(str(doctor.id).encode("ascii")).hexdigest()
    key = f"practice-join-attempt:{key_id}"
    redis = get_redis()
    redis.delete(key)
    try:
        for attempt in range(1, 11):
            internal._consume_legacy_join_attempt(doctor)
            assert redis.get(key) == str(attempt).encode()
            assert 0 < cast(int, redis.ttl(key)) <= 15 * 60

        with pytest.raises(HTTPException) as exc_info:
            internal._consume_legacy_join_attempt(doctor)
        assert exc_info.value.status_code == status.HTTP_429_TOO_MANY_REQUESTS
        assert redis.get(key) == b"11"
        assert 0 < cast(int, redis.ttl(key)) <= 15 * 60
    finally:
        redis.delete(key)


def test_join_persists_quantity_repair_without_waiting_for_stripe(
    bare_authenticated_client: TestClient,
    monkeypatch,
):
    doctor = _authed_doctor()
    domain = extract_email_domain(doctor.email)
    assert domain
    practice = Practice(
        legal_name="Fast Join Practice, PLLC",
        join_code="FASTJOIN",
        email_domain=domain,
        origin_channel="direct",
        pastoral_pack=False,
        is_solo=False,
        baa_accepted_at=datetime.now(timezone.utc),
        baa_version=BAA_CURRENT_REFERENCE,
        stripe_customer_id="cus_fast_join",
        stripe_subscription_id="sub_fast_join",
        subscription_status="active",
    ).save()

    def forbidden_remote_sync(*_args, **_kwargs):
        raise AssertionError("join route must not perform Stripe network I/O")

    monkeypatch.setattr(
        stripe_billing, "sync_subscription_quantity", forbidden_remote_sync
    )
    monkeypatch.setattr(
        stripe_billing, "mark_quantity_sync_required", forbidden_remote_sync
    )
    joined = bare_authenticated_client.post(
        api_app.url_path_for("join_practice"),
        json={
            "join_code": "FASTJOIN",
            "attestation_name": "Pat Clinician",
            "checks": ["workforce", "not-party", "direction", "phi"],
        },
    )

    assert joined.status_code == status.HTTP_200_OK, joined.text
    practice.refresh()
    assert practice.stripe_quantity_sync_required is True
    assert practice.stripe_quantity_sync_target == 1


def test_join_rejects_twenty_first_active_clinician_atomically(
    bare_authenticated_client: TestClient,
):
    joining_doctor = _authed_doctor()
    domain = extract_email_domain(joining_doctor.email)
    assert domain
    practice = Practice(
        legal_name="Full Public Practice, PLLC",
        join_code=f"F{secrets.token_hex(4).upper()[:7]}",
        email_domain=domain,
        origin_channel="direct",
        is_solo=False,
        baa_accepted_at=datetime.now(timezone.utc),
        baa_version=BAA_CURRENT_REFERENCE,
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


def test_mcd_channel_without_mcd_email_coerces_to_catholic(
    bare_authenticated_client: TestClient,
):
    doctor = _authed_doctor()
    assert extract_email_domain(doctor.email) != "mycatholicdoctor.com"

    body = _create_practice(
        bare_authenticated_client,
        legal_name="Independent Catholic Practice",
        channel="mcd",
        pastoral_pack=True,
    )
    assert body["origin_channel"] == "catholic"
    assert body["email_domain"] == extract_email_domain(doctor.email)


def test_practice_gate_blocks_fresh_signup(bare_authenticated_client: TestClient):
    _authed_doctor()

    resp = bare_authenticated_client.get(api_app.url_path_for("note_list"))
    assert resp.status_code == status.HTTP_403_FORBIDDEN
    assert "practice" in resp.json()["detail"].lower()


def test_legacy_admin_role_alone_does_not_open_clinical_gate(
    bare_authenticated_client: TestClient,
):
    doctor = _authed_doctor()
    doctor.role = DoctorRole.admin
    doctor.save()
    assert PracticeMember.get(doctor_id=doctor.id) is None

    response = bare_authenticated_client.get(api_app.url_path_for("note_list"))
    settings = bare_authenticated_client.get(api_app.url_path_for("settings_data"))

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert "practice" in response.json()["detail"].lower()
    assert settings.status_code == status.HTTP_200_OK
    assert settings.json()["is_admin"] is False


def test_practice_gate_blocks_doctor_without_practice_even_if_onboarded(
    bare_authenticated_client: TestClient,
):
    """No recorded BAA acceptance means no PHI, with no grandfather exception
    (CLAUDE_BAA_IMPLEMENTATION guardrail 5). Legacy doctors are routed through
    practice creation + acceptance by the frontend."""

    doctor = _authed_doctor()
    doctor.onboarding_completed_at = datetime.now(timezone.utc)
    doctor.save()
    assert PracticeMember.get(doctor_id=doctor.id) is None

    resp = bare_authenticated_client.get(api_app.url_path_for("note_list"))
    assert resp.status_code == status.HTTP_403_FORBIDDEN


def test_public_baa_acceptance_does_not_start_trial_or_open_product_gate(
    bare_authenticated_client: TestClient,
):
    _authed_doctor()

    created = _create_practice(bare_authenticated_client)
    assert created["trial_ends_at"] is None
    assert created["entitled"] is False

    # member without a BAA in force: gate stays closed
    blocked = bare_authenticated_client.get(api_app.url_path_for("note_list"))
    assert blocked.status_code == status.HTTP_403_FORBIDDEN

    body = _accept_baa(bare_authenticated_client)
    assert body["entitled"] is False
    assert body["billing_exempt"] is False
    assert body["trial_ends_at"] is None

    still_blocked = bare_authenticated_client.get(api_app.url_path_for("note_list"))
    assert still_blocked.status_code == status.HTTP_402_PAYMENT_REQUIRED


def test_expired_timed_comp_does_not_create_public_baa_trial(
    bare_authenticated_client: TestClient,
):
    doctor = _authed_doctor()
    _create_practice(bare_authenticated_client)
    practice = _own_practice(doctor)
    practice.billing_exempt = True
    practice.billing_exempt_until = datetime.now(timezone.utc) - timedelta(days=1)
    practice.save()

    body = _accept_baa(bare_authenticated_client)
    assert body["trial_ends_at"] is None
    assert body["entitled"] is False


def test_public_subscription_gate_and_exemption_sources(
    bare_authenticated_client: TestClient,
):
    doctor = _authed_doctor()
    _create_practice(bare_authenticated_client)
    _accept_baa(bare_authenticated_client)

    practice = _own_practice(doctor)

    practice.trial_ends_at = datetime.now(timezone.utc) - timedelta(days=1)
    practice.save()
    lapsed = bare_authenticated_client.get(api_app.url_path_for("note_list"))
    assert lapsed.status_code == status.HTTP_402_PAYMENT_REQUIRED

    # pilot comp (billing_exempt) reopens the gate
    practice.billing_exempt = True
    practice.save()
    comped = bare_authenticated_client.get(api_app.url_path_for("note_list"))
    assert comped.status_code == status.HTTP_200_OK

    # a dated comp that has ended closes it again
    practice.billing_exempt_until = datetime.now(timezone.utc) - timedelta(days=1)
    practice.save()
    ended = bare_authenticated_client.get(api_app.url_path_for("note_list"))
    assert ended.status_code == status.HTTP_402_PAYMENT_REQUIRED

    # an active subscription (mirrored from Stripe) is the third source
    practice.stripe_customer_id = "cus_authoritative_public"
    practice.stripe_subscription_id = "sub_authoritative_public"
    practice.stripe_state_version = 1
    practice.subscription_status = "active"
    practice.save()
    subscribed = bare_authenticated_client.get(api_app.url_path_for("note_list"))
    assert subscribed.status_code == status.HTTP_200_OK


def test_negotiated_baa_on_file_satisfies_the_gate(
    bare_authenticated_client: TestClient,
):
    """Enterprise fallback: a countersigned CN-BAA-CUST on file (internal-admin
    set) satisfies PHI gating without clickwrap acceptance."""

    doctor = _authed_doctor()
    _create_practice(bare_authenticated_client)

    blocked = bare_authenticated_client.get(api_app.url_path_for("note_list"))
    assert blocked.status_code == status.HTTP_403_FORBIDDEN

    practice = _own_practice(doctor)
    practice.negotiated_baa_on_file = True
    practice.negotiated_baa_effective_date = datetime.now(timezone.utc)
    practice.negotiated_baa_reference = "CN-BAA-CUST v2.2 — countersigned"
    practice.billing_exempt = True
    practice.save()

    member = PracticeMember.get(doctor_id=doctor.id)
    assert member
    member.attested_at = datetime.now(timezone.utc)
    member.save()

    opened = bare_authenticated_client.get(api_app.url_path_for("note_list"))
    assert opened.status_code == status.HTTP_200_OK

    done = bare_authenticated_client.post(api_app.url_path_for("complete_onboarding"))
    assert done.status_code == status.HTTP_200_OK


def test_future_negotiated_baa_does_not_open_phi_gate(
    bare_authenticated_client: TestClient,
):
    doctor = _authed_doctor()
    _create_practice(bare_authenticated_client)
    practice = _own_practice(doctor)
    practice.negotiated_baa_on_file = True
    practice.negotiated_baa_effective_date = datetime.now(timezone.utc) + timedelta(
        days=1
    )
    practice.negotiated_baa_reference = "CN-BAA-CUST future-effective"
    practice.billing_exempt = True
    practice.save()

    member = PracticeMember.get(doctor_id=doctor.id)
    assert member
    member.attested_at = datetime.now(timezone.utc)
    member.save()

    blocked = bare_authenticated_client.get(api_app.url_path_for("note_list"))
    assert blocked.status_code == status.HTTP_403_FORBIDDEN

    practice.negotiated_baa_effective_date = datetime.now(timezone.utc) - timedelta(
        seconds=1
    )
    practice.save()
    opened = bare_authenticated_client.get(api_app.url_path_for("note_list"))
    assert opened.status_code == status.HTTP_200_OK


def _add_active_clinicians(practice: Practice, count: int) -> None:
    for _ in range(count):
        clinician = Doctor(
            clerk_id=f"user_seat_{secrets.token_hex(8)}",
            email=f"seat-{secrets.token_hex(4)}@{practice.email_domain}",
            biography="",
        ).save()
        PracticeMember(
            practice_id=practice.id,
            doctor_id=clinician.id,
            role="workforce",
        ).save()


def _bind_subscription(
    practice: Practice,
    *,
    subscription_status: str,
    quantity: int | None,
    sync_required: bool = False,
) -> None:
    suffix = secrets.token_hex(6)
    practice.stripe_customer_id = f"cus_seats_{suffix}"
    practice.stripe_subscription_id = f"sub_seats_{suffix}"
    practice.stripe_state_version = 1
    practice.subscription_status = subscription_status
    practice.stripe_quantity_sync_target = quantity
    practice.stripe_quantity_sync_required = sync_required
    practice.save()


def _seat_summary(client: TestClient) -> dict | None:
    resp = client.get(api_app.url_path_for("practice_status"))
    assert resp.status_code == status.HTTP_200_OK, resp.text
    return resp.json()["seat_summary"]


def test_solo_owner_seat_summary_before_checkout(
    bare_authenticated_client: TestClient,
):
    body = _create_practice(bare_authenticated_client)

    assert body["seat_summary"] == {
        "account_kind": "solo",
        "active_clinicians": 1,
        "billed_seats": None,
        "seat_sync_pending": False,
        "unit_amount": stripe_billing.EXPECTED_PRICE_UNIT_AMOUNT,
        "currency": "usd",
        "interval": "month",
    }


def test_practice_owner_sees_confirmed_billed_seats(
    bare_authenticated_client: TestClient,
):
    doctor = _authed_doctor()
    _create_practice(bare_authenticated_client, is_solo=False)
    practice = _own_practice(doctor)
    _add_active_clinicians(practice, 3)
    _bind_subscription(practice, subscription_status="active", quantity=4)

    summary = _seat_summary(bare_authenticated_client)

    assert summary is not None
    assert summary["account_kind"] == "practice"
    assert summary["active_clinicians"] == 4
    assert summary["billed_seats"] == 4
    assert summary["seat_sync_pending"] is False


def test_outstanding_quantity_sync_reports_pending_without_a_stale_count(
    bare_authenticated_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    def forbidden_remote_read(*_args, **_kwargs):
        raise AssertionError("billing status must not call Stripe")

    monkeypatch.setattr(stripe_billing, "_request", forbidden_remote_read)
    doctor = _authed_doctor()
    _create_practice(bare_authenticated_client, is_solo=False)
    practice = _own_practice(doctor)
    _add_active_clinicians(practice, 3)
    _bind_subscription(
        practice, subscription_status="active", quantity=4, sync_required=True
    )

    summary = _seat_summary(bare_authenticated_client)

    assert summary is not None
    assert summary["active_clinicians"] == 4
    assert summary["billed_seats"] is None
    assert summary["seat_sync_pending"] is True


def test_confirmed_quantity_behind_the_roster_reports_pending(
    bare_authenticated_client: TestClient,
):
    doctor = _authed_doctor()
    _create_practice(bare_authenticated_client, is_solo=False)
    practice = _own_practice(doctor)
    _add_active_clinicians(practice, 3)
    _bind_subscription(practice, subscription_status="active", quantity=3)

    summary = _seat_summary(bare_authenticated_client)

    assert summary is not None
    assert summary["active_clinicians"] == 4
    assert summary["billed_seats"] == 3
    assert summary["seat_sync_pending"] is True


@pytest.mark.parametrize(
    ("subscription_status", "billed_seats"),
    [
        ("trialing", 1),
        ("active", 1),
        ("past_due", 1),
        ("unpaid", 1),
        ("paused", 1),
        ("incomplete", 1),
        ("canceled", None),
        ("incomplete_expired", None),
        ("invalid", None),
        ("pending_verification", None),
    ],
)
def test_billed_seats_follow_subscription_state(
    bare_authenticated_client: TestClient,
    subscription_status: str,
    billed_seats: int | None,
):
    doctor = _authed_doctor()
    _create_practice(bare_authenticated_client)
    _bind_subscription(
        _own_practice(doctor),
        subscription_status=subscription_status,
        quantity=1,
    )

    summary = _seat_summary(bare_authenticated_client)

    assert summary is not None
    assert summary["account_kind"] == "solo"
    assert summary["billed_seats"] == billed_seats
    assert summary["seat_sync_pending"] is False


def test_terminal_subscription_is_never_pending_sync(
    bare_authenticated_client: TestClient,
):
    doctor = _authed_doctor()
    _create_practice(bare_authenticated_client)
    _bind_subscription(
        _own_practice(doctor),
        subscription_status="canceled",
        quantity=None,
        sync_required=True,
    )

    summary = _seat_summary(bare_authenticated_client)

    assert summary is not None
    assert summary["billed_seats"] is None
    assert summary["seat_sync_pending"] is False


def test_exempt_practice_reports_seats_without_billing(
    bare_authenticated_client: TestClient,
):
    doctor = _authed_doctor()
    _create_practice(bare_authenticated_client)
    practice = _own_practice(doctor)
    practice.billing_exempt = True
    practice.save()

    summary = _seat_summary(bare_authenticated_client)

    assert summary is not None
    assert summary["active_clinicians"] == 1
    assert summary["billed_seats"] is None
    assert summary["seat_sync_pending"] is False


def test_invalid_seat_ledger_hides_summary_and_alerts_operators(
    bare_authenticated_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    doctor = _authed_doctor()
    _create_practice(bare_authenticated_client, is_solo=False)
    practice = _own_practice(doctor)
    _bind_subscription(practice, subscription_status="active", quantity=1)

    def invalid_ledger(*_args, **_kwargs):
        raise ClinicianSeatPolicyError("practice seat ledger has an invalid state")

    monkeypatch.setattr(internal, "active_clinician_seat_count", invalid_ledger)

    with capture_logs() as logs:
        summary = _seat_summary(bare_authenticated_client)

    assert summary is None
    assert [
        entry
        for entry in logs
        if entry.get("event_code") == "billing_seat_summary_ledger_invalid"
        and entry["log_level"] == "error"
        and entry["practice_id"] == str(practice.id)
    ]


def test_workforce_member_does_not_receive_seat_summary(
    bare_authenticated_client: TestClient,
):
    doctor = _authed_doctor()
    domain = extract_email_domain(doctor.email)
    assert domain
    practice = Practice(
        legal_name="Workforce Seat Practice, PLLC",
        join_code=f"W{secrets.token_hex(4).upper()[:7]}",
        email_domain=domain,
        origin_channel="direct",
        is_solo=False,
    ).save()
    PracticeMember(
        practice_id=practice.id, doctor_id=doctor.id, role="workforce"
    ).save()
    _bind_subscription(practice, subscription_status="active", quantity=1)

    assert _seat_summary(bare_authenticated_client) is None


def test_join_code_lookup_does_not_disclose_seat_summary(
    bare_authenticated_client: TestClient,
):
    doctor = _authed_doctor()
    domain = extract_email_domain(doctor.email)
    assert domain
    practice = Practice(
        legal_name="Lookup Seat Practice, PLLC",
        join_code=f"L{secrets.token_hex(4).upper()[:7]}",
        email_domain=domain,
        origin_channel="direct",
        is_solo=False,
        baa_accepted_at=datetime.now(timezone.utc),
        baa_version=BAA_CURRENT_REFERENCE,
    ).save()

    resp = bare_authenticated_client.post(
        api_app.url_path_for("lookup_practice"),
        json={"join_code": practice.join_code},
    )

    assert resp.status_code == status.HTTP_200_OK, resp.text
    looked_up = resp.json()["practice"]
    assert looked_up is not None
    assert looked_up["seat_summary"] is None
