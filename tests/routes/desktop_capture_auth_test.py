from __future__ import annotations

import base64
import hashlib
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from structlog.testing import capture_logs

from app.configuration.desktop_capture import (
    MAX_DESKTOP_TOKEN_TTL_SECONDS,
    DesktopCaptureSettings,
    get_desktop_capture_settings,
)
from app.server import api_app
from app.services.capture_launch_auth import (
    AuthorizedCaptureSessionReference,
    CaptureConsentAttestation,
    CaptureLaunchDenied,
    get_capture_session_preparer,
)

from app.models.appointment import Appointment
from app.models.capture_coordination import CaptureSession, CaptureSourceLease
from app.models.doctor import Doctor
from app.models.patient import Patient
from app.models.practice import PracticeMember

from tests.routes.utils import get_clerk_admin_user, get_clerk_dev_user

REDIRECT_URI = "cosmaneura-capture://oauth/callback"
VERIFIER = "v" * 43
INSTALLATION_THUMBPRINT = "i" * 43


def _attestation() -> dict[str, object]:
    return {
        "modality": "virtual",
        "participant_regions": ["FL", "OH"],
        "method": "verbal_attestation",
        "all_parties_consented": True,
        "granted_at": datetime.now(UTC).isoformat(),
    }


def _settings(*, enabled: bool = True) -> DesktopCaptureSettings:
    return DesktopCaptureSettings(
        enabled=enabled,
        provider_create_enabled=False,
        recovery_enabled=False,
        redirect_uris=(REDIRECT_URI,),
    )


def _challenge() -> str:
    digest = hashlib.sha256(VERIFIER.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _ready_reference(suffix: str = "route") -> AuthorizedCaptureSessionReference:
    _, _, clerk_user = get_clerk_dev_user()
    doctor = Doctor.get(clerk_id=clerk_user.id)
    assert doctor is not None
    membership = PracticeMember.get(doctor_id=doctor.id)
    assert membership is not None
    patient = Patient(
        doctor_id=doctor.id,
        name=f"Synthetic Desktop {suffix}",
        date_of_birth=datetime(2000, 1, 1, tzinfo=UTC),
    ).save()
    appointment = Appointment(
        doctor_id=doctor.id,
        patient_id=patient.id,
        appointment_date=datetime.now(UTC) + timedelta(hours=1),
        external_id=None,
    ).save()
    consent_id = uuid4()
    capture_session = CaptureSession(
        practice_id=membership.practice_id,
        actor_id=doctor.id,
        appointment_id=appointment.id,
        source_kind="desktop_companion",
        state="ready",
        state_version=1,
        consent_state="granted",
        scribe_consent_receipt_id=consent_id,
        consent_policy_version="synthetic-v1",
        processing_session_id=uuid4(),
        authority_origin="server_authorized",
    ).save()
    now = datetime.now(UTC)
    lease = CaptureSourceLease(
        session_id=capture_session.id,
        practice_id=membership.practice_id,
        actor_id=doctor.id,
        appointment_id=appointment.id,
        source_kind="desktop_companion",
        state="active",
        lease_token_digest=hashlib.sha256(suffix.encode()).hexdigest(),
        generation=0,
        acquired_at=now,
        control_deadline_at=now + timedelta(minutes=10),
    ).save()
    return AuthorizedCaptureSessionReference(
        capture_session_id=capture_session.id,
        practice_id=membership.practice_id,
        actor_id=doctor.id,
        appointment_id=appointment.id,
        consent_evidence_id=consent_id,
        source_lease_id=lease.id,
        state_version=1,
    )


class StaticPreparer:
    def __init__(self, reference: AuthorizedCaptureSessionReference) -> None:
        self.reference = reference
        self.calls: list[dict[str, object]] = []

    async def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return self.reference


def _configure_route(
    reference: AuthorizedCaptureSessionReference,
    *,
    enabled: bool = True,
) -> StaticPreparer:
    preparer = StaticPreparer(reference)
    api_app.dependency_overrides[get_desktop_capture_settings] = lambda: _settings(
        enabled=enabled
    )
    api_app.dependency_overrides[get_capture_session_preparer] = lambda: preparer
    return preparer


def _launch_url(reference: AuthorizedCaptureSessionReference) -> str:
    return api_app.url_path_for(
        "create_desktop_capture_launch",
        appointment_id=reference.appointment_id,
    )


def test_browser_launch_and_pkce_exchange_return_exact_tauri_grant(
    authenticated_client: TestClient,
):
    reference = _ready_reference()
    preparer = _configure_route(reference)

    with capture_logs() as logs:
        launched = authenticated_client.post(
            _launch_url(reference),
            headers={"Idempotency-Key": "desktop-route-launch-001"},
            json=_attestation(),
        )
        assert launched.status_code == status.HTTP_201_CREATED
        assert "no-store" in launched.headers["cache-control"]
        assert launched.headers["pragma"] == "no-cache"
        launch_ticket = launched.json()["launch_ticket"]

        authorized = authenticated_client.post(
            api_app.url_path_for("authorize_desktop_capture_launch"),
            json={
                "launch_ticket": launch_ticket,
                "code_challenge": _challenge(),
                "code_challenge_method": "S256",
                "redirect_uri": REDIRECT_URI,
                "installation_thumbprint": INSTALLATION_THUMBPRINT,
            },
        )
        assert authorized.status_code == status.HTTP_200_OK
        assert "no-store" in authorized.headers["cache-control"]
        assert authorized.headers["pragma"] == "no-cache"
        authorization_code = authorized.json()["authorization_code"]

        exchanged = authenticated_client.post(
            api_app.url_path_for("exchange_desktop_capture_code"),
            json={
                "grant_type": "authorization_code",
                "code": authorization_code,
                "code_verifier": VERIFIER,
                "redirect_uri": REDIRECT_URI,
            },
        )

    assert exchanged.status_code == status.HTTP_200_OK
    assert "no-store" in exchanged.headers["cache-control"]
    assert exchanged.headers["pragma"] == "no-cache"
    payload = exchanged.json()
    assert set(payload) == {"access_token", "token_type", "expires_in", "grant"}
    assert payload["token_type"] == "Bearer"
    assert 5 * 60 < payload["expires_in"] <= MAX_DESKTOP_TOKEN_TTL_SECONDS
    assert payload["grant"] == {
        "captureSessionId": str(reference.capture_session_id),
        "actorId": str(reference.actor_id),
        "consentRecordId": str(reference.consent_evidence_id),
        "consentStatus": "granted",
        "sessionStatus": "authorized",
        "source": "desktop_companion",
        "sourceLeaseId": str(reference.source_lease_id),
    }
    assert len(preparer.calls) == 1
    call = preparer.calls[0]
    attestation = call.pop("attestation")
    assert isinstance(attestation, CaptureConsentAttestation)
    assert attestation.modality == "virtual"
    assert attestation.participant_regions == frozenset({"FL", "OH"})
    assert attestation.method == "verbal_attestation"
    assert attestation.all_parties_consented is True
    assert call == {
        "actor_id": reference.actor_id,
        "practice_id": reference.practice_id,
        "appointment_id": reference.appointment_id,
        "idempotency_key": "desktop-route-launch-001",
    }
    rendered_logs = repr(logs)
    for secret_or_identifier in (
        launch_ticket,
        authorization_code,
        payload["access_token"],
        str(reference.capture_session_id),
        str(reference.actor_id),
        str(reference.appointment_id),
    ):
        assert secret_or_identifier not in rendered_logs


def test_disabled_launch_does_not_call_session_preparer(
    authenticated_client: TestClient,
):
    reference = _ready_reference("disabled")
    preparer = _configure_route(reference, enabled=False)

    response = authenticated_client.post(
        _launch_url(reference),
        headers={"Idempotency-Key": "desktop-route-disabled-001"},
        json=_attestation(),
    )

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert preparer.calls == []


def test_launch_body_cannot_assert_identity_or_consent_record_ids(
    authenticated_client: TestClient,
):
    reference = _ready_reference("body-authority")
    preparer = _configure_route(reference)
    body = _attestation()
    body.update(
        {
            "tenant_id": "attacker-selected-tenant",
            "patient_id": "attacker-selected-patient",
            "consent_record_id": str(uuid4()),
            "capture_session_id": "attacker-selected-session",
        }
    )

    response = authenticated_client.post(
        _launch_url(reference),
        headers={"Idempotency-Key": "desktop-route-body-authority-001"},
        json=body,
    )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert response.json() == {"detail": "Invalid service request."}
    assert preparer.calls == []


def test_desktop_pilot_rejects_non_virtual_modality(
    authenticated_client: TestClient,
):
    reference = _ready_reference("in-person")
    preparer = _configure_route(reference)
    body = _attestation()
    body["modality"] = "in_person"

    response = authenticated_client.post(
        _launch_url(reference),
        headers={"Idempotency-Key": "desktop-route-in-person-001"},
        json=body,
    )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert response.json() == {"detail": "Invalid service request."}
    assert preparer.calls == []


def test_consent_flag_must_be_a_json_boolean(
    authenticated_client: TestClient,
):
    reference = _ready_reference("coerced-consent")
    preparer = _configure_route(reference)
    body = _attestation()
    body["all_parties_consented"] = "yes"

    response = authenticated_client.post(
        _launch_url(reference),
        headers={"Idempotency-Key": "desktop-route-coerced-consent-001"},
        json=body,
    )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert response.json() == {"detail": "Invalid service request."}
    assert preparer.calls == []


def test_cross_tenant_preparer_result_is_rejected(
    authenticated_client: TestClient,
):
    real_reference = _ready_reference("real")
    other = Doctor(
        clerk_id="user_test_desktop_cross_tenant",
        email="desktop-cross-tenant@example.test",
        biography=None,
    ).save()
    other_patient = Patient(
        doctor_id=other.id,
        name="Synthetic Other Tenant",
        date_of_birth=datetime(2000, 1, 1, tzinfo=UTC),
    ).save()
    other_appointment = Appointment(
        doctor_id=other.id,
        patient_id=other_patient.id,
        appointment_date=datetime.now(UTC) + timedelta(hours=1),
        external_id=None,
    ).save()
    mismatched = AuthorizedCaptureSessionReference(
        capture_session_id=real_reference.capture_session_id,
        practice_id=real_reference.practice_id,
        actor_id=real_reference.actor_id,
        appointment_id=other_appointment.id,
        consent_evidence_id=real_reference.consent_evidence_id,
        source_lease_id=real_reference.source_lease_id,
        state_version=real_reference.state_version,
    )
    _configure_route(mismatched)

    response = authenticated_client.post(
        _launch_url(real_reference),
        headers={"Idempotency-Key": "desktop-route-cross-tenant-001"},
        json=_attestation(),
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert "Synthetic" not in response.text


@pytest.mark.parametrize("outcome", ["missing", "unauthorized"])
def test_missing_or_unauthorized_appointment_is_generic(
    authenticated_client: TestClient,
    outcome: str,
):
    reference = _ready_reference(outcome)

    def denied_preparer(**kwargs):
        del kwargs
        raise CaptureLaunchDenied("launch_not_authorized")

    api_app.dependency_overrides[get_desktop_capture_settings] = lambda: _settings()
    api_app.dependency_overrides[get_capture_session_preparer] = lambda: denied_preparer

    response = authenticated_client.post(
        _launch_url(reference),
        headers={"Idempotency-Key": f"desktop-route-{outcome}-001"},
        json=_attestation(),
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json() == {
        "detail": "Appointment is unavailable for desktop capture."
    }


def test_cancelled_appointment_is_rejected_after_preparation(
    authenticated_client: TestClient,
):
    reference = _ready_reference("cancelled")
    _configure_route(reference)
    appointment = Appointment.get(id=reference.appointment_id)
    assert appointment is not None
    appointment.cancelled_at = datetime.now(UTC)
    appointment.save()

    response = authenticated_client.post(
        _launch_url(reference),
        headers={"Idempotency-Key": "desktop-route-cancelled-001"},
        json=_attestation(),
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND


def test_admin_and_login_as_sessions_cannot_launch(
    authenticated_client: TestClient,
):
    reference = _ready_reference("admin")
    preparer = _configure_route(reference)
    _, _, admin = get_clerk_admin_user()
    _, _, clinician = get_clerk_dev_user()
    authenticated_client.override_user(admin)  # type: ignore[attr-defined]

    admin_response = authenticated_client.post(
        _launch_url(reference),
        headers={"Idempotency-Key": "desktop-route-admin-001"},
        json=_attestation(),
    )
    assert admin_response.status_code == status.HTTP_403_FORBIDDEN

    login_as = authenticated_client.post(
        api_app.url_path_for("login_as_user", user_id=clinician.id),
    )
    assert login_as.status_code == status.HTTP_200_OK

    delegated_response = authenticated_client.post(
        _launch_url(reference),
        headers={"Idempotency-Key": "desktop-route-login-as-001"},
        json=_attestation(),
    )
    assert delegated_response.status_code == status.HTTP_403_FORBIDDEN
    assert preparer.calls == []


def test_sensitive_validation_values_are_not_echoed(
    authenticated_client: TestClient,
):
    reference = _ready_reference("validation")
    _configure_route(reference)
    secret = "sensitive-ticket-value-that-must-not-be-returned-123456789"

    response = authenticated_client.post(
        api_app.url_path_for("authorize_desktop_capture_launch"),
        json={
            "launch_ticket": secret,
            "code_challenge": "invalid",
            "code_challenge_method": "S256",
            "redirect_uri": REDIRECT_URI,
            "installation_thumbprint": INSTALLATION_THUMBPRINT,
        },
    )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert response.json() == {"detail": "Invalid service request."}
    assert secret not in response.text
