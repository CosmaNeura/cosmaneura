"""HTTP enrollment and PHI gating for organization-roster pilot access."""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import cast

import pytest
from fastapi import HTTPException, status
from fastapi.testclient import TestClient

from app.configuration.redis import get_redis
from app.routes import internal
from app.server import api_app
from app.services.organization_access import (
    RosterApprovalInput,
    configure_provider_organization,
    import_roster_approvals,
    revoke_roster_access,
)

from app.models.baa import (
    BAA_CURRENT_SHA256,
    BAA_CURRENT_VERSION,
    BAA_DOC_ID,
    BaaAcceptance,
)
from app.models.doctor import Doctor
from app.models.organization_access import ProviderOrganization
from app.models.practice import Practice, PracticeMember

from tests.routes.utils import get_clerk_dev_user


def _authed_doctor() -> Doctor:
    _, _, clerk_user = get_clerk_dev_user()
    doctor = Doctor.find_or_create_by(clerk_id=clerk_user.id)
    assert doctor.email
    return doctor


@pytest.fixture(autouse=True)
def _verified_clerk_email_matches_bound_doctor(monkeypatch):
    def _current_email(clerk_id: str) -> str | None:
        doctor = Doctor.get(clerk_id=clerk_id)
        if doctor is None or not doctor.email:
            return None
        return doctor.email.strip().lower()

    monkeypatch.setattr(
        "app.routes.dependencies.doctor.verified_primary_email",
        _current_email,
    )


def _provision_roster_access(doctor: Doctor) -> tuple[Practice, str]:
    assert doctor.email
    local_part = doctor.email.rsplit("@", 1)[0]
    domain = f"route-{secrets.token_hex(5)}.example.test"
    doctor.email = f"{local_part}@{domain}"
    doctor.save()
    now = datetime.now(timezone.utc)
    practice = Practice(
        legal_name="Route Test Pilot Organization",
        join_code=f"R{secrets.token_hex(4).upper()[:7]}",
        email_domain=domain,
        origin_channel="direct",
        is_solo=False,
        negotiated_baa_on_file=True,
        negotiated_baa_effective_date=now - timedelta(days=1),
        negotiated_baa_reference="test://executed-negotiated-baa",
        billing_exempt=True,
    ).save()
    _, code, _ = configure_provider_organization(
        practice,
        legal_name="Route Test Provider Organization",
        slug=f"route-test-{secrets.token_hex(5)}",
        email_domain=domain,
        operator_ref="route-test-operator",
        reason="Configure test organization",
        now=now,
    )
    import_roster_approvals(
        practice,
        [RosterApprovalInput(email=doctor.email)],
        operator_ref="route-test-roster",
        reason="Approve exact test identity",
        source_ref="test://route-roster-v1",
        source_sha256="b" * 64,
        now=now,
    )
    return practice, code


def _isolate_online_attempt_counter(monkeypatch) -> None:
    monkeypatch.setattr(
        internal,
        "_consume_organization_access_attempt",
        lambda _doctor: None,
    )


def test_verified_primary_email_and_code_open_gate_without_attestation(
    bare_authenticated_client: TestClient,
    monkeypatch,
):
    doctor = _authed_doctor()
    practice, code = _provision_roster_access(doctor)
    assert doctor.email
    monkeypatch.setattr(internal, "verified_primary_email", lambda _id: doctor.email)
    _isolate_online_attempt_counter(monkeypatch)

    opportunity = bare_authenticated_client.get(
        api_app.url_path_for("organization_access_opportunity")
    )
    assert opportunity.status_code == status.HTTP_200_OK
    assert opportunity.json() == {
        "state": "claim",
        "organization_name": "Route Test Provider Organization",
        "email_domain": doctor.email.rsplit("@", 1)[1].lower(),
    }

    legacy_lookup = bare_authenticated_client.post(
        api_app.url_path_for("lookup_practice"),
        json={"join_code": practice.join_code},
    )
    assert legacy_lookup.status_code == status.HTTP_200_OK, legacy_lookup.text
    assert legacy_lookup.json() == {"practice": None, "mismatch": False}
    legacy_join = bare_authenticated_client.post(
        api_app.url_path_for("join_practice"),
        json={
            "join_code": practice.join_code,
            "attestation_name": "Rostered Clinician",
            "checks": ["workforce", "not-party", "direction", "phi"],
        },
    )
    assert legacy_join.status_code == status.HTTP_409_CONFLICT
    assert "organization-managed" in legacy_join.json()["detail"]
    assert PracticeMember.get(doctor_id=doctor.id) is None

    denied = bare_authenticated_client.post(
        api_app.url_path_for("claim_organization_access"),
        json={"access_code": "WRONGCODE123"},
    )
    assert denied.status_code == status.HTTP_403_FORBIDDEN
    assert PracticeMember.get(doctor_id=doctor.id) is None

    claimed = bare_authenticated_client.post(
        api_app.url_path_for("claim_organization_access"),
        json={"access_code": code},
    )
    assert claimed.status_code == status.HTTP_200_OK, claimed.text
    payload = claimed.json()
    assert payload["baa_accepted"] is True
    assert payload["entitled"] is True
    assert payload["join_code"] is None
    assert payload["attested"] is False
    assert payload["access_authorized"] is True
    assert payload["authorization_basis"] == "organization_roster"
    assert payload["access_revoked"] is False

    member = PracticeMember.get(doctor_id=doctor.id)
    assert member is not None
    assert member.attested_at is None
    assert member.attestation_name is None
    assert BaaAcceptance.where(BaaAcceptance.practice_id == practice.id).count() == 0

    ready = bare_authenticated_client.get(
        api_app.url_path_for("organization_access_opportunity")
    )
    assert ready.status_code == status.HTTP_200_OK
    assert ready.json()["state"] == "ready"

    opened = bare_authenticated_client.get(api_app.url_path_for("note_list"))
    assert opened.status_code == status.HTTP_200_OK
    readiness = bare_authenticated_client.get(api_app.url_path_for("practice_status"))
    assert readiness.status_code == status.HTTP_200_OK
    assert readiness.json()["access_authorized"] is True
    assert readiness.json()["join_code"] is None
    completed = bare_authenticated_client.post(
        api_app.url_path_for("complete_onboarding")
    )
    assert completed.status_code == status.HTTP_200_OK

    practice.negotiated_baa_on_file = False
    practice.save()
    no_baa = bare_authenticated_client.get(api_app.url_path_for("note_list"))
    assert no_baa.status_code == status.HTTP_403_FORBIDDEN

    practice.negotiated_baa_on_file = True
    practice.billing_exempt = False
    practice.save()
    not_entitled = bare_authenticated_client.get(api_app.url_path_for("note_list"))
    assert not_entitled.status_code == status.HTTP_402_PAYMENT_REQUIRED


def test_verified_primary_email_must_match_the_exact_rostered_account(
    bare_authenticated_client: TestClient,
    monkeypatch,
):
    doctor = _authed_doctor()
    _, code = _provision_roster_access(doctor)
    _isolate_online_attempt_counter(monkeypatch)
    monkeypatch.setattr(
        internal,
        "verified_primary_email",
        lambda _id: "different-user@example.com",
    )

    opportunity = bare_authenticated_client.get(
        api_app.url_path_for("organization_access_opportunity")
    )
    assert opportunity.status_code == status.HTTP_200_OK
    assert opportunity.json() == {
        "state": "support",
        "organization_name": None,
        "email_domain": doctor.email.rsplit("@", 1)[1].lower(),
    }

    denied = bare_authenticated_client.post(
        api_app.url_path_for("claim_organization_access"),
        json={"access_code": code},
    )
    assert denied.status_code == status.HTTP_403_FORBIDDEN
    assert PracticeMember.get(doctor_id=doctor.id) is None


def test_live_managed_email_drift_routes_to_support_and_blocks_personal_baa(
    bare_authenticated_client: TestClient,
    monkeypatch,
):
    doctor = _authed_doctor()
    assert doctor.email
    practice = Practice(
        legal_name="Legacy Standalone Practice",
        join_code=f"D{secrets.token_hex(4).upper()[:7]}",
        email_domain=doctor.email.rsplit("@", 1)[1].lower(),
        origin_channel="direct",
        is_solo=True,
    ).save()
    PracticeMember(
        practice_id=practice.id,
        doctor_id=doctor.id,
        role="owner",
    ).save()
    live_managed_email = f"moved-{secrets.token_hex(4)}@mycatholicdoctor.com"
    monkeypatch.setattr(
        internal,
        "verified_primary_email",
        lambda _id: live_managed_email,
    )
    before_acceptances = BaaAcceptance.count()

    disposition = bare_authenticated_client.get(
        api_app.url_path_for("organization_access_opportunity")
    )
    assert disposition.status_code == status.HTTP_200_OK
    assert disposition.json() == {
        "state": "support",
        "organization_name": None,
        "email_domain": "mycatholicdoctor.com",
    }

    baa = bare_authenticated_client.post(
        api_app.url_path_for("accept_practice_baa"),
        json={
            "doc_id": BAA_DOC_ID,
            "version": BAA_CURRENT_VERSION,
            "sha256": BAA_CURRENT_SHA256,
            "signature_name": "Must Not Persist",
            "signature_title": "Clinician",
            "checks": ["baa"],
        },
    )
    assert baa.status_code == status.HTTP_403_FORBIDDEN
    assert BaaAcceptance.count() == before_acceptances


@pytest.mark.parametrize(
    "managed_domain",
    ["mycatholicdoctor.com", "fiathealth.org", "east.mycatholicdoctor.com"],
)
def test_unprovisioned_managed_identity_gets_pending_and_cannot_self_enroll(
    bare_authenticated_client: TestClient,
    monkeypatch,
    managed_domain: str,
):
    doctor = _authed_doctor()
    assert doctor.email
    local_part = doctor.email.rsplit("@", 1)[0]
    doctor.email = f"{local_part}@{managed_domain}"
    doctor.save()
    monkeypatch.setattr(internal, "verified_primary_email", lambda _id: doctor.email)

    before_practices = Practice.count()
    before_acceptances = BaaAcceptance.count()
    disposition = bare_authenticated_client.get(
        api_app.url_path_for("organization_access_opportunity")
    )
    assert disposition.status_code == status.HTTP_200_OK
    assert disposition.json() == {
        "state": "pending",
        "organization_name": None,
        "email_domain": (
            "mycatholicdoctor.com"
            if managed_domain.endswith("mycatholicdoctor.com")
            else "fiathealth.org"
        ),
    }

    created = bare_authenticated_client.post(
        api_app.url_path_for("create_practice"),
        json={
            "legal_name": "Must Not Exist",
            "is_solo": True,
            "channel": "direct",
            "pastoral_pack": False,
        },
    )
    assert created.status_code == status.HTTP_409_CONFLICT

    lookup = bare_authenticated_client.post(
        api_app.url_path_for("lookup_practice"),
        json={"join_code": "ANYCODE1"},
    )
    assert lookup.status_code == status.HTTP_200_OK
    assert lookup.json() == {"practice": None, "mismatch": False}

    # The managed-path guard runs before the legacy acknowledgement validator;
    # managed users are never invited into that personal/workforce clickwrap.
    joined = bare_authenticated_client.post(
        api_app.url_path_for("join_practice"),
        json={"join_code": "ANYCODE1", "attestation_name": "Doctor", "checks": []},
    )
    assert joined.status_code == status.HTTP_409_CONFLICT
    baa = bare_authenticated_client.post(
        api_app.url_path_for("accept_practice_baa"),
        json={
            "doc_id": BAA_DOC_ID,
            "version": BAA_CURRENT_VERSION,
            "sha256": BAA_CURRENT_SHA256,
            "signature_name": "Doctor",
            "signature_title": "Clinician",
            "checks": ["baa"],
        },
    )
    assert baa.status_code == status.HTTP_409_CONFLICT
    assert Practice.count() == before_practices
    assert PracticeMember.get(doctor_id=doctor.id) is None
    assert BaaAcceptance.count() == before_acceptances


def test_unsafe_managed_coverage_uses_one_support_state_and_cannot_claim(
    bare_authenticated_client: TestClient,
    monkeypatch,
):
    doctor = _authed_doctor()
    practice, code = _provision_roster_access(doctor)
    assert doctor.email
    monkeypatch.setattr(internal, "verified_primary_email", lambda _id: doctor.email)
    _isolate_online_attempt_counter(monkeypatch)

    def disposition() -> dict[str, object]:
        response = bare_authenticated_client.get(
            api_app.url_path_for("organization_access_opportunity")
        )
        assert response.status_code == status.HTTP_200_OK
        return cast(dict[str, object], response.json())

    practice.negotiated_baa_on_file = False
    practice.save()
    assert disposition()["state"] == "support"
    denied_baa = bare_authenticated_client.post(
        api_app.url_path_for("claim_organization_access"),
        json={"access_code": code},
    )
    assert denied_baa.status_code == status.HTTP_403_FORBIDDEN

    practice.negotiated_baa_on_file = True
    practice.billing_exempt = False
    practice.save()
    assert disposition()["state"] == "support"
    denied_entitlement = bare_authenticated_client.post(
        api_app.url_path_for("claim_organization_access"),
        json={"access_code": code},
    )
    assert denied_entitlement.status_code == status.HTTP_403_FORBIDDEN

    practice.billing_exempt = True
    practice.save()
    organization = ProviderOrganization.get(id=practice.organization_id)
    assert organization is not None
    organization.status = "suspended"
    organization.save()
    assert disposition()["state"] == "support"
    denied_suspended = bare_authenticated_client.post(
        api_app.url_path_for("claim_organization_access"),
        json={"access_code": code},
    )
    assert denied_suspended.status_code == status.HTTP_403_FORBIDDEN
    assert PracticeMember.get(doctor_id=doctor.id) is None


def test_conflicting_managed_assignment_routes_to_support(
    bare_authenticated_client: TestClient,
    monkeypatch,
):
    doctor = _authed_doctor()
    practice, _ = _provision_roster_access(doctor)
    assert doctor.email
    monkeypatch.setattr(internal, "verified_primary_email", lambda _id: doctor.email)

    conflict_practice = Practice(
        legal_name="Conflicting Practice",
        join_code=f"C{secrets.token_hex(4).upper()[:7]}",
        email_domain="conflict.example.test",
        origin_channel="direct",
        is_solo=True,
    ).save()
    PracticeMember(
        practice_id=conflict_practice.id,
        doctor_id=doctor.id,
        role="owner",
    ).save()

    conflict = bare_authenticated_client.get(
        api_app.url_path_for("organization_access_opportunity")
    )
    assert conflict.status_code == status.HTTP_200_OK
    assert conflict.json()["state"] == "support"


def test_revoked_managed_assignment_routes_to_support(
    bare_authenticated_client: TestClient,
    monkeypatch,
):
    doctor = _authed_doctor()
    practice, _ = _provision_roster_access(doctor)
    assert doctor.email
    monkeypatch.setattr(internal, "verified_primary_email", lambda _id: doctor.email)

    revoke_roster_access(
        practice,
        doctor.email,
        operator_ref="route-test-operator",
        reason="Exercise retained revoked routing state",
    )
    revoked = bare_authenticated_client.get(
        api_app.url_path_for("organization_access_opportunity")
    )
    assert revoked.status_code == status.HTTP_200_OK
    assert revoked.json() == {
        "state": "support",
        "organization_name": None,
        "email_domain": doctor.email.rsplit("@", 1)[1].lower(),
    }


def test_organization_linked_owner_cannot_create_clickwrap_acceptance(
    bare_authenticated_client: TestClient,
):
    doctor = _authed_doctor()
    practice, _ = _provision_roster_access(doctor)
    member = PracticeMember(
        practice_id=practice.id,
        doctor_id=doctor.id,
        role="owner",
    ).save()
    assert BaaAcceptance.where(BaaAcceptance.practice_id == practice.id).count() == 0

    response = bare_authenticated_client.post(
        api_app.url_path_for("accept_practice_baa"),
        json={
            "doc_id": BAA_DOC_ID,
            "version": BAA_CURRENT_VERSION,
            "sha256": BAA_CURRENT_SHA256,
            "signature_name": "Organization Owner",
            "signature_title": "Owner",
            "checks": ["baa"],
        },
    )

    assert response.status_code == status.HTTP_409_CONFLICT
    assert "existing BAA" in response.json()["detail"]
    assert BaaAcceptance.where(BaaAcceptance.practice_id == practice.id).count() == 0
    practice.refresh()
    member.refresh()
    assert practice.baa_accepted_at is None
    assert member.attested_at is None


def test_existing_member_must_claim_roster_before_legacy_enrollment_returns_access(
    bare_authenticated_client: TestClient,
    monkeypatch,
):
    doctor = _authed_doctor()
    practice, code = _provision_roster_access(doctor)
    existing_member = PracticeMember(
        practice_id=practice.id,
        doctor_id=doctor.id,
        role="owner",
    ).save()
    assert doctor.email
    monkeypatch.setattr(internal, "verified_primary_email", lambda _id: doctor.email)
    _isolate_online_attempt_counter(monkeypatch)

    create_response = bare_authenticated_client.post(
        api_app.url_path_for("create_practice"),
        json={
            "legal_name": "Should Not Be Created",
            "is_solo": True,
            "channel": "direct",
            "pastoral_pack": False,
        },
    )
    assert create_response.status_code == status.HTTP_409_CONFLICT

    join_response = bare_authenticated_client.post(
        api_app.url_path_for("join_practice"),
        json={
            "join_code": practice.join_code,
            "attestation_name": "Existing Owner",
            "checks": ["workforce", "not-party", "direction", "phi"],
        },
    )
    assert join_response.status_code == status.HTTP_409_CONFLICT

    history_response = bare_authenticated_client.get(
        api_app.url_path_for("practice_baa_acceptances")
    )
    assert history_response.status_code == status.HTTP_403_FORBIDDEN

    claim_response = bare_authenticated_client.post(
        api_app.url_path_for("claim_organization_access"),
        json={"access_code": code},
    )
    assert claim_response.status_code == status.HTTP_200_OK
    existing_member.refresh()
    assert existing_member.role == "owner"
    assert existing_member.organization_roster_approval_id is not None
    history_after_claim = bare_authenticated_client.get(
        api_app.url_path_for("practice_baa_acceptances")
    )
    assert history_after_claim.status_code == status.HTTP_200_OK


def test_rostered_identity_cannot_escape_into_legacy_create_or_join(
    bare_authenticated_client: TestClient,
    monkeypatch,
):
    doctor = _authed_doctor()
    _, _ = _provision_roster_access(doctor)
    assert doctor.email
    domain = doctor.email.rsplit("@", 1)[1].lower()
    standalone = Practice(
        legal_name="Unrelated Standalone Practice",
        join_code=f"L{secrets.token_hex(4).upper()[:7]}",
        email_domain=domain,
        origin_channel="direct",
        is_solo=False,
        negotiated_baa_on_file=True,
        negotiated_baa_effective_date=datetime.now(timezone.utc) - timedelta(days=1),
        negotiated_baa_reference="test://unrelated-baa",
    ).save()
    # A Clerk outage must not reopen the legacy paths for an identity already
    # reserved by an unrevoked organization roster assignment.
    monkeypatch.setattr(internal, "verified_primary_email", lambda _id: None)

    lookup = bare_authenticated_client.post(
        api_app.url_path_for("lookup_practice"),
        json={"join_code": standalone.join_code},
    )
    assert lookup.status_code == status.HTTP_200_OK
    assert lookup.json() == {"practice": None, "mismatch": False}

    joined = bare_authenticated_client.post(
        api_app.url_path_for("join_practice"),
        json={
            "join_code": standalone.join_code,
            "attestation_name": "Rostered Clinician",
            "checks": ["workforce", "not-party", "direction", "phi"],
        },
    )
    assert joined.status_code == status.HTTP_409_CONFLICT
    assert "organization-managed" in joined.json()["detail"]

    created = bare_authenticated_client.post(
        api_app.url_path_for("create_practice"),
        json={
            "legal_name": "Escape Practice",
            "is_solo": True,
            "channel": "direct",
            "pastoral_pack": False,
        },
    )
    assert created.status_code == status.HTTP_409_CONFLICT
    assert "organization-managed" in created.json()["detail"]
    assert PracticeMember.get(doctor_id=doctor.id) is None
    assert Practice.where(Practice.legal_name == "Escape Practice").count() == 0


def test_organization_access_rate_limit_allows_eight_and_ninth_keeps_ttl():
    doctor = Doctor(
        clerk_id=f"user_rate_{secrets.token_hex(8)}",
        email=f"rate-{secrets.token_hex(4)}@example.com",
        biography="",
    ).save()
    key_id = hashlib.sha256(str(doctor.id).encode("ascii")).hexdigest()
    key = f"organization-access-attempt:{key_id}"
    redis = get_redis()
    redis.delete(key)
    try:
        for attempt in range(1, 9):
            internal._consume_organization_access_attempt(doctor)
            assert redis.get(key) == str(attempt).encode()
            assert 0 < cast(int, redis.ttl(key)) <= 15 * 60

        with pytest.raises(HTTPException) as exc_info:
            internal._consume_organization_access_attempt(doctor)
        assert exc_info.value.status_code == status.HTTP_429_TOO_MANY_REQUESTS
        assert redis.get(key) == b"9"
        assert 0 < cast(int, redis.ttl(key)) <= 15 * 60
    finally:
        redis.delete(key)
