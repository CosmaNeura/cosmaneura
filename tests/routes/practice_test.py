from datetime import datetime, timedelta, timezone

from fastapi import status
from fastapi.testclient import TestClient

from app.server import api_app
from app.utils.email_domain import extract_email_domain

from app.models.baa import BAA_CURRENT_SHA256, BaaAcceptance
from app.models.doctor import Doctor
from app.models.practice import Practice, PracticeMember

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
):
    doctor = _authed_doctor()
    doctor.onboarding_completed_at = datetime.now(timezone.utc)
    doctor.save()

    resp = bare_authenticated_client.post(api_app.url_path_for("complete_onboarding"))
    assert resp.status_code == status.HTTP_200_OK


def test_create_practice_stamps_email_domain(bare_authenticated_client: TestClient):
    doctor = _authed_doctor()
    domain = extract_email_domain(doctor.email)
    assert domain

    body = _create_practice(bare_authenticated_client, city="Nashua", region="NH")
    assert body["legal_name"].startswith("Riverside")
    assert body["role"] == "owner"
    assert body["baa_accepted"] is False
    assert body["email_domain"] == domain
    assert body["join_code"]


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


def test_baa_acceptance_writes_append_only_record(
    bare_authenticated_client: TestClient,
):
    doctor = _authed_doctor()
    _create_practice(bare_authenticated_client)
    assert BaaAcceptance.count() == 0

    before = datetime.now(timezone.utc)
    _accept_baa(bare_authenticated_client)

    record = BaaAcceptance.first()
    assert record
    assert record.covered_entity_legal_name.startswith("Riverside")
    assert record.accepted_by_name == "Pat Owner"
    assert record.accepted_by_title == "Physician / owner"
    assert record.accepted_by_email == doctor.email
    assert record.content_sha256 == BAA_CURRENT_SHA256
    # server-generated timestamp, never the client clock
    assert before <= record.accepted_at_utc <= datetime.now(timezone.utc)

    # idempotent per (practice, version): re-accepting is a no-op
    _accept_baa(bare_authenticated_client)
    assert BaaAcceptance.count() == 1


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


def test_expired_trial_closes_the_gate_and_exemption_reopens_it(
    bare_authenticated_client: TestClient,
):
    _authed_doctor()
    _create_practice(bare_authenticated_client)
    _accept_baa(bare_authenticated_client)

    practice = Practice.first()
    assert practice

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

    practice = Practice.first()
    assert practice
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
