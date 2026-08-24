import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import cast

import pytest
from fastapi import status
from fastapi.exceptions import HTTPException
from fastapi.testclient import TestClient

from app.configuration.redis import get_redis
from app.routes import internal
from app.server import api_app
from app.services import stripe_billing
from app.utils.email_domain import extract_email_domain

from activemodel.session_manager import get_session
from app.models.baa import BAA_CURRENT_SHA256, BaaAcceptance
from app.models.doctor import Doctor
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


def test_grandfathered_doctor_can_complete_without_practice(
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
    assert settings.json()["onboarding_completed"] is True
    assert settings.json()["practice_ready"] is True
    assert settings.json()["billing_recovery_required"] is False

    notes = bare_authenticated_client.get(api_app.url_path_for("note_list"))
    assert notes.status_code == status.HTTP_200_OK

    resp = bare_authenticated_client.post(api_app.url_path_for("complete_onboarding"))
    assert resp.status_code == status.HTTP_200_OK
    doctor.refresh()
    assert doctor.onboarding_completed_at is not None


def test_grandfathered_practice_payload_keeps_facts_and_exposes_bypass(
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
    assert payload["pilot_access_grant_active"] is True
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


@pytest.mark.parametrize(
    ("npi", "message"),
    [
        ("12345678901", "exactly 10 digits"),
        ("12345A7893", "exactly 10 digits"),
        ("1234567890", "checksum is invalid"),
    ],
)
def test_create_practice_rejects_invalid_npi_without_truncating(
    bare_authenticated_client: TestClient,
    npi: str,
    message: str,
):
    doctor = _authed_doctor()

    response = bare_authenticated_client.post(
        api_app.url_path_for("create_practice"),
        json={
            "legal_name": "NPI Validation Practice",
            "npi": npi,
            "is_solo": True,
            "channel": "direct",
            "pastoral_pack": False,
        },
    )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert message in response.text
    assert PracticeMember.get(doctor_id=doctor.id) is None


def test_create_practice_accepts_valid_npi(bare_authenticated_client: TestClient):
    doctor = _authed_doctor()

    _create_practice(bare_authenticated_client, npi="1234567893")

    assert _own_practice(doctor).npi == "1234567893"


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


def test_baa_acceptance_requires_checkbox(bare_authenticated_client: TestClient):
    _authed_doctor()
    _create_practice(bare_authenticated_client)

    resp = bare_authenticated_client.post(
        api_app.url_path_for("accept_practice_baa"),
        json={
            "signature_name": "Pat Owner",
            "signature_title": "Physician / owner",
            "checks": [],
        },
    )
    assert resp.status_code == status.HTTP_400_BAD_REQUEST


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
    assert lookup.json() == {"practice": None, "mismatch": None}

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


def test_baa_acceptance_retry_repairs_gate_from_original_legal_timestamp(
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
    # but the denormalized gate/trial and owner attestation are absent.
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
    assert practice.trial_ends_at == original_accepted_at + timedelta(days=14)
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
    doctor = _authed_doctor()
    Practice(
        legal_name="MyCatholicDoctor Nashua",
        join_code="MCDNASHU",
        email_domain="mycatholicdoctor.com",
        origin_channel="mcd",
        pastoral_pack=True,
        is_solo=False,
        baa_accepted_at=datetime.now(timezone.utc),
        baa_version="CN-BAA-CUST-CW v2.2",
    ).save()

    lookup = bare_authenticated_client.post(
        api_app.url_path_for("lookup_practice"),
        json={"join_code": "mcd-nashu"},
    )
    assert lookup.status_code == status.HTTP_200_OK, lookup.text
    body = lookup.json()
    assert body["practice"] is None
    assert body["mismatch"]["required_domain"] == "mycatholicdoctor.com"
    assert body["mismatch"]["signed_in_domain"] == extract_email_domain(doctor.email)

    joined = bare_authenticated_client.post(
        api_app.url_path_for("join_practice"),
        json={
            "join_code": "MCDNASHU",
            "attestation_name": "Pat Clinician",
            "checks": ["workforce", "not-party", "direction", "phi"],
        },
    )
    assert joined.status_code == status.HTTP_403_FORBIDDEN


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
        baa_version="CN-BAA-CUST-CW v2.2",
    ).save()

    lookup = bare_authenticated_client.post(
        api_app.url_path_for("lookup_practice"),
        json={"join_code": practice.join_code},
    )
    assert lookup.status_code == status.HTTP_200_OK
    assert lookup.json() == {"practice": None, "mismatch": None}

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
        baa_version="CN-BAA-CUST-CW v2.2",
    ).save()

    lookup = bare_authenticated_client.post(
        api_app.url_path_for("lookup_practice"),
        json={"join_code": practice.join_code},
    )
    assert lookup.status_code == status.HTTP_200_OK
    assert lookup.json() == {"practice": None, "mismatch": None}

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
        baa_version="CN-BAA-CUST-CW v2.2",
        stripe_customer_id="cus_fast_join",
        stripe_subscription_id="sub_fast_join",
        subscription_status="active",
    ).save()

    def forbidden_remote_sync(*_args, **_kwargs):
        raise AssertionError("join route must not perform Stripe network I/O")

    monkeypatch.setattr(
        stripe_billing, "sync_subscription_quantity", forbidden_remote_sync
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


def test_baa_acceptance_starts_trial_and_opens_the_gate(
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
    assert body["entitled"] is True
    assert body["billing_exempt"] is False
    assert body["trial_ends_at"] is not None

    trial_ends_at = datetime.fromisoformat(body["trial_ends_at"])
    trial_left = trial_ends_at - datetime.now(timezone.utc)
    assert timedelta(days=13) < trial_left <= timedelta(days=14)

    opened = bare_authenticated_client.get(api_app.url_path_for("note_list"))
    assert opened.status_code == status.HTTP_200_OK


def test_expired_timed_comp_does_not_suppress_trial_at_baa_acceptance(
    bare_authenticated_client: TestClient,
):
    doctor = _authed_doctor()
    _create_practice(bare_authenticated_client)
    practice = _own_practice(doctor)
    practice.billing_exempt = True
    practice.billing_exempt_until = datetime.now(timezone.utc) - timedelta(days=1)
    practice.save()

    body = _accept_baa(bare_authenticated_client)
    assert body["trial_ends_at"] is not None
    assert body["entitled"] is True


def test_expired_trial_closes_the_gate_and_exemption_reopens_it(
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
