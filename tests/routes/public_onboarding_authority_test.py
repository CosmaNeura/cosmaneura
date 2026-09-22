"""Synthetic database regressions for the preflight's authority facts."""

import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request
from starlette_context import request_cycle_context

from app.routes import public_onboarding
from app.routes.dependencies import doctor as doctor_dependency
from app.services.doctor_access import resolve_doctor_access
from app.services.organization_access import (
    claim_roster_access,
    configure_provider_organization,
)

from activemodel.session_manager import get_session
from app.models.doctor import Doctor
from app.models.organization_access import OrganizationRosterApproval
from app.models.practice import PracticeMember
from app.models.public_onboarding import PublicLegalAcceptance

from tests.routes.utils import provision_practice_in_force


@pytest.fixture
def authorized_preflight(monkeypatch):
    doctor = Doctor(
        clerk_id="user_test_preflight", email="owner@example.test", biography=""
    ).save()
    practice = provision_practice_in_force(doctor)
    practice.public_pilot_cohort = True
    practice.subscription_status = "active"
    practice.save()
    for kind, filename in [
        ("terms", "terms-of-service.html"),
        ("privacy", "privacy-policy.html"),
    ]:
        source = Path(__file__).resolve().parents[2] / "web/public" / filename
        PublicLegalAcceptance(
            practice_id=practice.id,
            document_kind=kind,
            document_version="2026-04-16",
            content_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
            signer_doctor_id=doctor.id,
            signer_email=doctor.email,
            signer_role="owner",
            signature_name="Synthetic Owner",
            signature_title="Owner",
            authority_confirmed=True,
            signer_authority="Synthetic owner authority",
            accepted_at_utc=datetime.now(timezone.utc),
        ).save()
    monkeypatch.setattr(
        public_onboarding, "verified_primary_email", lambda _: "owner@example.test"
    )
    return doctor, practice


def _preflight(doctor):
    with get_session() as session:
        return public_onboarding._capability_entitlement(session, doctor)


def test_authorized_owner_gets_consistent_positive_facts(authorized_preflight):
    doctor, practice = authorized_preflight
    result = _preflight(doctor)
    assert result.actor.practice_id == str(practice.id)
    assert result.gates.identity_verified
    assert result.gates.membership_authorized
    assert result.capabilities["soap_note"]["decision"] == "allowed"


def test_membership_requires_an_authorization_basis(authorized_preflight):
    doctor, _ = authorized_preflight
    member = PracticeMember.get(doctor_id=doctor.id)
    assert member is not None
    member.attested_at = None
    member.save()
    authoritative = resolve_doctor_access(doctor)
    assert not authoritative.allowed
    assert authoritative.denial_reason == "practice_access_not_authorized"
    result = _preflight(doctor)
    assert not result.gates.membership_authorized
    assert "membership_unauthorized" in result.capabilities["soap_note"]["reason_codes"]
    assert result.capabilities["soap_note"]["decision"] == "denied"


@pytest.mark.parametrize("live_email", [None, "different@example.test"])
def test_cached_email_does_not_prove_live_identity(
    authorized_preflight, monkeypatch, live_email
):
    doctor, _ = authorized_preflight
    monkeypatch.setattr(
        public_onboarding, "verified_primary_email", lambda _: live_email
    )
    result = _preflight(doctor)
    assert not result.gates.identity_verified
    assert "identity_unverified" in result.capabilities["soap_note"]["reason_codes"]
    assert result.capabilities["soap_note"]["decision"] == "denied"


def test_failed_email_lookup_through_real_doctor_middleware(
    authorized_preflight, monkeypatch
):
    doctor, _ = authorized_preflight
    doctor.email = None
    doctor.save()
    monkeypatch.setattr(doctor_dependency, "_fetch_clerk_email", lambda _: None)
    monkeypatch.setattr(public_onboarding, "verified_primary_email", lambda _: None)
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/public-onboarding/v1/capabilities",
            "headers": [],
        }
    )
    request.state.auth_state = SimpleNamespace(payload={"sub": doctor.clerk_id})
    with request_cycle_context({}):
        doctor_dependency.inject_doctor_record(request)
    assert request.state.doctor.email is None
    assert not public_onboarding.public_onboarding_capabilities(
        request
    ).gates.identity_verified


@pytest.mark.parametrize("state", ["inactive", "revoked"])
def test_inactive_or_revoked_membership_denies_capabilities(
    authorized_preflight, state
):
    doctor, _ = authorized_preflight
    member = PracticeMember.get(doctor_id=doctor.id)
    assert member is not None
    if state == "revoked":
        member.access_revoked_at = datetime.now(timezone.utc)
        member.access_revoked_by_ref = "test-operator"
        member.access_revocation_reason = "Synthetic revocation"
        member.access_records_retain_until = member.access_revoked_at + timedelta(
            days=8 * 366
        )
    else:
        member.seat_state = state
    member.save()
    result = _preflight(doctor)
    assert result.actor.membership_state == state
    assert result.capabilities["soap_note"]["decision"] == "denied"
    if state == "revoked":
        assert not result.gates.membership_authorized


def test_unaffiliated_actor_cannot_read_another_practice(authorized_preflight):
    _, practice = authorized_preflight
    stranger = Doctor(
        clerk_id="user_test_unaffiliated", email="stranger@example.test", biography=""
    ).save()
    with pytest.raises(HTTPException) as denied:
        _preflight(stranger)
    assert denied.value.status_code == 403
    assert str(practice.id) not in str(denied.value.detail)


@pytest.mark.parametrize("invalid_authority", ["revoked", "expired"])
def test_managed_practice_requires_current_exact_roster(
    authorized_preflight, invalid_authority
):
    doctor, practice = authorized_preflight
    now = datetime.now(timezone.utc) - timedelta(days=2)
    practice.negotiated_baa_on_file = True
    practice.negotiated_baa_effective_date = now - timedelta(days=1)
    practice.negotiated_baa_reference = "synthetic:executed-baa"
    practice.save()
    _, code, _ = configure_provider_organization(
        practice,
        legal_name="Synthetic Organization",
        slug="synthetic-preflight",
        email_domain="example.test",
        operator_ref="test-operator",
        reason="Synthetic roster test",
        now=now,
    )
    # The pre-cutover attestation cannot substitute for an exact roster row.
    assert not _preflight(doctor).gates.membership_authorized
    # Construct historical provenance with its original expiry. Current roster
    # imports disallow scheduled expiry and persisted provenance is immutable.
    approval = OrganizationRosterApproval(
        organization_id=practice.organization_id,
        target_practice_id=practice.id,
        approved_email=doctor.email or "",
        approved_at=now,
        approved_by_ref="test-operator",
        source_ref="test://historical-roster",
        source_sha256="a" * 64,
        expires_at=now + timedelta(days=1) if invalid_authority == "expired" else None,
    ).save()
    claim_roster_access(doctor, supplied_code=code, now=now + timedelta(seconds=1))
    if invalid_authority == "revoked":
        assert _preflight(doctor).gates.membership_authorized
        # Revoke roster evidence independently of the still-active membership.
        approval.revoked_at = datetime.now(timezone.utc)
        approval.revoked_by_ref = "test-operator"
        approval.revocation_reason = "Synthetic revocation"
        approval.retain_until = approval.revoked_at + timedelta(days=8 * 366)
        approval.save()
    result = _preflight(doctor)
    assert not result.gates.membership_authorized
    assert result.capabilities["soap_note"]["decision"] == "denied"


@pytest.mark.parametrize(
    "subscription_state",
    [None, "past_due", "canceled", "pending_verification", "invalid"],
)
def test_billing_exemption_cannot_bypass_public_subscription_contract(
    authorized_preflight, subscription_state
):
    doctor, practice = authorized_preflight
    assert practice.billing_exempt
    practice.subscription_status = subscription_state
    practice.save()
    result = _preflight(doctor)
    assert result.gates.subscription_state == (subscription_state or "none")
    assert all(value["decision"] == "denied" for value in result.capabilities.values())
    assert "subscription_inactive" in result.capabilities["soap_note"]["reason_codes"]


@pytest.mark.parametrize("subscription_state", ["active", "trialing"])
def test_allowed_preflight_satisfies_frozen_contract_gates(
    authorized_preflight, subscription_state
):
    import json

    doctor, practice = authorized_preflight
    practice.subscription_status = subscription_state
    practice.trial_ends_at = datetime.now(timezone.utc) + timedelta(days=1)
    practice.save()
    result = _preflight(doctor)
    assert result.capabilities["soap_note"]["decision"] == "allowed"
    contract = json.loads(
        (
            Path(__file__).resolve().parents[2]
            / "contracts/public-entitlements/v1/capability-entitlement.schema.json"
        ).read_text()
    )
    required_gates = contract["allOf"][0]["then"]["properties"]["gates"]["properties"]
    gates = result.gates.model_dump()
    for name, constraint in required_gates.items():
        if "const" in constraint:
            assert gates[name] == constraint["const"]
        if "enum" in constraint:
            assert gates[name] in constraint["enum"]


def test_preflight_rejects_changed_legal_bytes(
    authorized_preflight, tmp_path, monkeypatch
):
    from app.services import public_legal

    doctor, _ = authorized_preflight
    source = tmp_path / "web/public"
    source.mkdir(parents=True)
    for document in public_legal.CURRENT_LEGAL_DOCUMENTS:
        content = document.source.read_bytes()
        if document.kind.value == "terms":
            content += b"\n<!-- New legal edition with the same version string. -->\n"
        (source / document.filename).write_bytes(content)
    monkeypatch.setattr(public_legal, "root", tmp_path)
    result = _preflight(doctor)
    assert not result.gates.terms_accepted
    assert result.gates.privacy_accepted
    assert result.capabilities["soap_note"]["decision"] == "denied"
    assert "legal_incomplete" in result.capabilities["soap_note"]["reason_codes"]
