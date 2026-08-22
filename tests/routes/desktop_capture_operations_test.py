from __future__ import annotations

import base64
import hashlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import uuid4

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from structlog.testing import capture_logs
from typeid import TypeID

from app.configuration.desktop_capture import (
    DesktopCaptureSettings,
    get_desktop_capture_settings,
)
from app.routes import desktop_capture_operations as operations_route
from app.routes.desktop_capture_operations import get_desktop_capture_provider
from app.server import api_app
from app.services.capture_launch_auth import (
    AuthorizedCaptureSessionReference,
    ResolvedDesktopAuthority,
    authorize_launch_ticket,
    exchange_authorization_code,
    issue_launch_ticket,
    resolve_desktop_token,
)
from app.services.capture_provider import CaptureObservation, CaptureProvisioning

from activemodel.session_manager import get_session
from activemodel.types import TypeIDType
from app.models.appointment import Appointment
from app.models.capture_coordination import (
    CaptureLaunchAuthorization,
    CaptureOperation,
    CaptureSegment,
    CaptureSession,
    CaptureSourceLease,
)
from app.models.doctor import Doctor
from app.models.patient import Patient
from app.models.practice import Practice, PracticeMember
from sqlmodel import select

from tests.routes.utils import get_clerk_dev_user

REDIRECT_URI = "cosmaneura-capture://oauth/callback"
VERIFIER = "v" * 43
INSTALLATION_THUMBPRINT = "i" * 43
CAPABILITY_SECRET = "sdk_upload_token_for_route_test_001"


def _settings(
    *, enabled: bool = True, provider_create_enabled: bool = True
) -> DesktopCaptureSettings:
    return DesktopCaptureSettings(
        enabled=enabled,
        provider_create_enabled=provider_create_enabled,
        recovery_enabled=False,
        redirect_uris=(REDIRECT_URI,),
    )


def _challenge() -> str:
    digest = hashlib.sha256(VERIFIER.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _ready_reference(suffix: str) -> AuthorizedCaptureSessionReference:
    _, _, clerk_user = get_clerk_dev_user()
    doctor = Doctor.get(clerk_id=clerk_user.id)
    assert doctor is not None
    membership = PracticeMember.get(doctor_id=doctor.id)
    assert membership is not None
    patient = Patient(
        doctor_id=doctor.id,
        name=f"Synthetic Operations {suffix}",
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


def _desktop_token(
    reference: AuthorizedCaptureSessionReference,
    settings: DesktopCaptureSettings,
) -> str:
    actor_binding = "synthetic-clerk-session-binding"
    ticket = issue_launch_ticket(
        reference=reference,
        actor_binding=actor_binding,
        idempotency_key="operations-launch-key-0001",
        settings=settings,
    )
    code = authorize_launch_ticket(
        launch_ticket=ticket.launch_ticket,
        actor_id=reference.actor_id,
        practice_id=reference.practice_id,
        actor_binding=actor_binding,
        code_challenge=_challenge(),
        code_challenge_method="S256",
        redirect_uri=REDIRECT_URI,
        installation_thumbprint=INSTALLATION_THUMBPRINT,
        settings=settings,
    )
    return exchange_authorization_code(
        authorization_code=code.authorization_code,
        code_verifier=VERIFIER,
        redirect_uri=REDIRECT_URI,
        settings=settings,
    ).access_token


class FakeProvider:
    def __init__(self) -> None:
        self.create_refs: list[str] = []

    def create_capture(self, *, correlation_ref: str) -> CaptureProvisioning:
        self.create_refs.append(correlation_ref)
        return CaptureProvisioning(
            capture_container_ref="sdk_upload_route_001",
            capture_capability=CAPABILITY_SECRET,
            status="pending",
        )

    def find_captures(self, *, correlation_ref: str) -> tuple[CaptureObservation, ...]:
        del correlation_ref
        return ()


def _configure(provider: FakeProvider, settings: DesktopCaptureSettings) -> None:
    api_app.dependency_overrides[get_desktop_capture_settings] = lambda: settings
    api_app.dependency_overrides[get_desktop_capture_provider] = lambda: provider


def _url(reference: AuthorizedCaptureSessionReference) -> str:
    return api_app.url_path_for(
        "authorize_desktop_capture_segment_start",
        capture_session_id=reference.capture_session_id,
    )


def _headers(token: str, *, key: str = "operations-start-key-0001") -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Idempotency-Key": key,
    }


def _start_operation_count() -> int:
    with get_session() as db:
        return len(
            db.exec(
                select(CaptureOperation).where(
                    CaptureOperation.operation == "start_capture_segment"
                )
            ).all()
        )


def _heartbeat_url(reference: AuthorizedCaptureSessionReference) -> str:
    return api_app.url_path_for(
        "record_desktop_capture_heartbeat",
        capture_session_id=reference.capture_session_id,
    )


def _stop_url(reference: AuthorizedCaptureSessionReference) -> str:
    return api_app.url_path_for(
        "request_desktop_capture_stop",
        capture_session_id=reference.capture_session_id,
    )


def test_start_authorization_returns_exact_electron_contract_and_no_store_headers(
    authenticated_client: TestClient,
) -> None:
    settings = _settings()
    reference = _ready_reference("success")
    token = _desktop_token(reference, settings)
    provider = FakeProvider()
    _configure(provider, settings)

    with capture_logs() as logs:
        response = authenticated_client.post(
            _url(reference),
            headers=_headers(token),
        )

    assert response.status_code == status.HTTP_200_OK
    assert "no-store" in response.headers["cache-control"]
    assert response.headers["pragma"] == "no-cache"
    payload = response.json()
    assert set(payload) == {
        "schemaVersion",
        "decision",
        "captureSessionId",
        "actorId",
        "consentRecordId",
        "consentStatus",
        "sessionStatus",
        "source",
        "sourceLeaseId",
        "issuedAt",
        "expiresAt",
        "captureCapability",
    }
    assert payload["schemaVersion"] == 1
    assert payload["decision"] == "authorized"
    assert payload["captureSessionId"] == str(reference.capture_session_id)
    assert payload["actorId"] == str(reference.actor_id)
    assert payload["consentRecordId"] == str(reference.consent_evidence_id)
    assert payload["consentStatus"] == "granted"
    assert payload["sessionStatus"] == "authorized"
    assert payload["source"] == "desktop_companion"
    assert payload["sourceLeaseId"] == str(reference.source_lease_id)
    assert payload["captureCapability"]["secret"] == CAPABILITY_SECRET
    assert payload["captureCapability"]["expiresAt"] == payload["expiresAt"]
    assert len(provider.create_refs) == 1
    assert _start_operation_count() == 1

    rendered_logs = repr(logs)
    for sensitive in (
        token,
        CAPABILITY_SECRET,
        str(reference.capture_session_id),
        str(reference.actor_id),
        str(reference.consent_evidence_id),
        str(reference.source_lease_id),
    ):
        assert sensitive not in rendered_logs


def test_successful_retry_replays_capability_without_second_provider_call(
    authenticated_client: TestClient,
) -> None:
    settings = _settings()
    reference = _ready_reference("replay")
    token = _desktop_token(reference, settings)
    provider = FakeProvider()
    _configure(provider, settings)
    headers = _headers(token, key="operations-start-replay-0001")

    first = authenticated_client.post(_url(reference), headers=headers)
    second = authenticated_client.post(_url(reference), headers=headers)

    assert first.status_code == status.HTTP_200_OK
    assert second.status_code == status.HTTP_200_OK
    assert second.json() == first.json()
    assert provider.create_refs and len(provider.create_refs) == 1
    assert _start_operation_count() == 1


@pytest.mark.parametrize(
    ("enabled", "provider_create_enabled"),
    [(False, True), (True, False)],
)
def test_kill_switches_precede_token_resolution_and_capture_mutation(
    authenticated_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    enabled: bool,
    provider_create_enabled: bool,
) -> None:
    reference = _ready_reference(f"gate-{enabled}-{provider_create_enabled}")
    provider = FakeProvider()
    _configure(
        provider,
        _settings(enabled=enabled, provider_create_enabled=provider_create_enabled),
    )

    def unexpected_resolver(**kwargs):
        raise AssertionError(f"token resolver called: {kwargs!r}")

    monkeypatch.setattr(
        operations_route, "resolve_desktop_authority", unexpected_resolver
    )
    response = authenticated_client.post(
        _url(reference),
        headers=_headers("t" * 43),
    )

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert response.json() == {"detail": "Desktop capture is unavailable."}
    assert provider.create_refs == []
    assert _start_operation_count() == 0


def test_missing_provider_precedes_token_resolution_and_capture_mutation(
    authenticated_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reference = _ready_reference("no-provider")
    api_app.dependency_overrides[get_desktop_capture_settings] = lambda: _settings()

    def unexpected_resolver(**kwargs):
        raise AssertionError(f"token resolver called: {kwargs!r}")

    monkeypatch.setattr(
        operations_route, "resolve_desktop_authority", unexpected_resolver
    )
    response = authenticated_client.post(
        _url(reference),
        headers=_headers("t" * 43),
    )

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert _start_operation_count() == 0


@pytest.mark.parametrize(
    "headers",
    [
        {"Idempotency-Key": "operations-start-key-0001"},
        {
            "Authorization": f"Basic {'t' * 43}",
            "Idempotency-Key": "operations-start-key-0001",
        },
        {
            "Authorization": f"Bearer  {'t' * 43}",
            "Idempotency-Key": "operations-start-key-0001",
        },
        {
            "Authorization": "Bearer too-short",
            "Idempotency-Key": "operations-start-key-0001",
        },
        {
            "Authorization": f"Bearer {'t' * 43},Bearer {'u' * 43}",
            "Idempotency-Key": "operations-start-key-0001",
        },
    ],
)
def test_malformed_bearers_are_generic_and_never_reach_token_resolution(
    authenticated_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    headers: dict[str, str],
) -> None:
    reference = _ready_reference("bad-bearer")
    provider = FakeProvider()
    _configure(provider, _settings())

    def unexpected_resolver(**kwargs):
        raise AssertionError(f"token resolver called: {kwargs!r}")

    monkeypatch.setattr(
        operations_route, "resolve_desktop_authority", unexpected_resolver
    )
    response = authenticated_client.post(_url(reference), headers=headers)

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert response.json() == {"detail": "Desktop authorization is invalid."}
    assert response.headers["www-authenticate"] == "Bearer"
    assert provider.create_refs == []
    assert _start_operation_count() == 0


def test_invalid_idempotency_key_is_safe_and_precedes_token_resolution(
    authenticated_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reference = _ready_reference("bad-key")
    provider = FakeProvider()
    _configure(provider, _settings())

    def unexpected_resolver(**kwargs):
        raise AssertionError(f"token resolver called: {kwargs!r}")

    monkeypatch.setattr(
        operations_route, "resolve_desktop_authority", unexpected_resolver
    )
    response = authenticated_client.post(
        _url(reference),
        headers=_headers("t" * 43, key="contains a space"),
    )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert response.json() == {"detail": "Invalid service request."}
    assert provider.create_refs == []
    assert _start_operation_count() == 0


def test_duplicate_authorization_headers_are_rejected_before_token_resolution(
    authenticated_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reference = _ready_reference("duplicate-bearer")
    provider = FakeProvider()
    _configure(provider, _settings())

    def unexpected_resolver(**kwargs):
        raise AssertionError(f"token resolver called: {kwargs!r}")

    monkeypatch.setattr(
        operations_route, "resolve_desktop_authority", unexpected_resolver
    )
    response = authenticated_client.post(
        _url(reference),
        headers=[
            ("Authorization", f"Bearer {'t' * 43}"),
            ("Authorization", f"Bearer {'u' * 43}"),
            ("Idempotency-Key", "operations-start-key-0001"),
        ],
    )

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert response.json() == {"detail": "Desktop authorization is invalid."}
    assert provider.create_refs == []
    assert _start_operation_count() == 0


def test_invalid_token_and_path_mismatch_are_generic_and_do_not_call_provider(
    authenticated_client: TestClient,
) -> None:
    settings = _settings()
    reference = _ready_reference("token-binding")
    token = _desktop_token(reference, settings)
    provider = FakeProvider()
    _configure(provider, settings)

    invalid = authenticated_client.post(
        _url(reference),
        headers=_headers("x" * 43),
    )
    mismatched = authenticated_client.post(
        api_app.url_path_for(
            "authorize_desktop_capture_segment_start",
            capture_session_id=TypeID("capture_session"),
        ),
        headers=_headers(token, key="operations-start-key-0002"),
    )

    assert invalid.status_code == status.HTTP_401_UNAUTHORIZED
    assert invalid.json() == {"detail": "Desktop authorization is invalid."}
    assert mismatched.status_code == status.HTTP_403_FORBIDDEN
    assert mismatched.json() == {"detail": "Capture start authorization was denied."}
    assert provider.create_refs == []
    assert _start_operation_count() == 0


@pytest.mark.parametrize(
    "binding", ["practice", "typed_actor", "actor", "consent", "lease", "source"]
)
def test_every_server_authority_binding_is_revalidated_before_provider_call(
    authenticated_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    binding: str,
) -> None:
    settings = _settings()
    reference = _ready_reference(f"binding-{binding}")
    real_grant = resolve_desktop_token(
        access_token=_desktop_token(reference, settings),
        settings=settings,
    )
    changes = {
        "practice": {},
        "typed_actor": {},
        "actor": {"actor_id": str(TypeID("doctor"))},
        "consent": {"consent_record_id": str(uuid4())},
        "lease": {"source_lease_id": str(TypeID("capture_source_lease"))},
        "source": {"source": "meeting_bot"},
    }[binding]
    resolved = ResolvedDesktopAuthority(
        practice_id=(
            cast(TypeIDType, TypeID("practice"))
            if binding == "practice"
            else reference.practice_id
        ),
        capture_session_id=reference.capture_session_id,
        actor_id=(
            cast(TypeIDType, TypeID("doctor"))
            if binding == "typed_actor"
            else reference.actor_id
        ),
        consent_evidence_id=reference.consent_evidence_id,
        source_lease_id=reference.source_lease_id,
        grant=replace(real_grant, **changes),
    )
    monkeypatch.setattr(
        operations_route,
        "resolve_desktop_authority",
        lambda **kwargs: resolved,
    )
    provider = FakeProvider()
    _configure(provider, settings)

    response = authenticated_client.post(
        _url(reference),
        headers=_headers("t" * 43),
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.json() == {"detail": "Capture start authorization was denied."}
    assert provider.create_refs == []
    assert _start_operation_count() == 0


def test_body_cannot_supply_authority_fields_or_echo_rejected_values(
    authenticated_client: TestClient,
) -> None:
    reference = _ready_reference("body")
    provider = FakeProvider()
    _configure(provider, _settings())
    sensitive = "patient-provided-authority-must-not-be-echoed"

    response = authenticated_client.post(
        _url(reference),
        headers=_headers("t" * 43),
        json={"actorId": sensitive},
    )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert response.json() == {"detail": "Invalid service request."}
    assert sensitive not in response.text
    assert provider.create_refs == []
    assert _start_operation_count() == 0


def test_heartbeat_and_stop_are_provider_free_no_store_and_proof_conservative(
    authenticated_client: TestClient,
) -> None:
    settings = _settings()
    reference = _ready_reference("lifecycle")
    token = _desktop_token(reference, settings)
    provider = FakeProvider()
    _configure(provider, settings)
    start = authenticated_client.post(
        _url(reference),
        headers=_headers(token, key="route-lifecycle-start-0001"),
    )
    assert start.status_code == status.HTTP_200_OK
    capability_id = start.json()["captureCapability"]["capabilityId"]

    # Disabling provider creation must not strand an already-issued capture.
    api_app.dependency_overrides[get_desktop_capture_settings] = lambda: _settings(
        provider_create_enabled=False
    )
    with capture_logs() as logs:
        heartbeat = authenticated_client.post(
            _heartbeat_url(reference),
            headers=_headers(token, key="route-lifecycle-heartbeat-0001"),
            json={"capabilityId": capability_id},
        )
    # The exact stop-only path remains available after every mutable start
    # authority has been withdrawn. It can only narrow state and purge secrets.
    appointment = Appointment.get(id=reference.appointment_id)
    capture_session = CaptureSession.get(id=reference.capture_session_id)
    source_lease = CaptureSourceLease.get(id=reference.source_lease_id)
    membership = PracticeMember.get(doctor_id=reference.actor_id)
    practice = Practice.get(id=reference.practice_id)
    launch_authorization = next(CaptureLaunchAuthorization.all())
    assert appointment is not None
    assert capture_session is not None
    assert source_lease is not None
    assert membership is not None
    assert practice is not None
    revoked_at = datetime.now(UTC)
    appointment.cancelled_at = revoked_at
    appointment.save()
    capture_session.state = "review_required"
    capture_session.consent_state = "revoked"
    capture_session.save()
    source_lease.state = "uncertain"
    source_lease.control_deadline_at = source_lease.acquired_at + timedelta(
        microseconds=1
    )
    source_lease.save()
    membership.attested_at = None
    membership.save()
    practice.baa_accepted_at = None
    practice.negotiated_baa_on_file = False
    practice.billing_exempt = False
    practice.trial_ends_at = revoked_at - timedelta(seconds=1)
    practice.subscription_status = "canceled"
    practice.save()
    launch_authorization.state = "revoked"
    launch_authorization.desktop_token_revoked_at = revoked_at
    launch_authorization.save()
    api_app.dependency_overrides[get_desktop_capture_settings] = lambda: _settings(
        enabled=False,
        provider_create_enabled=False,
    )
    blocked_heartbeat = authenticated_client.post(
        _heartbeat_url(reference),
        headers=_headers(token, key="route-lifecycle-heartbeat-disabled-0001"),
        json={"capabilityId": capability_id},
    )
    with capture_logs() as logs:
        stop_response = authenticated_client.post(
            _stop_url(reference),
            headers=_headers(token, key="route-lifecycle-stop-0001"),
            json={"capabilityId": capability_id},
        )

    assert heartbeat.status_code == status.HTTP_200_OK
    assert heartbeat.json() == {
        "schemaVersion": 1,
        "decision": "accepted",
        "sessionStatus": "recording",
    }
    assert blocked_heartbeat.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert stop_response.status_code == status.HTTP_200_OK
    assert stop_response.json() == {
        "schemaVersion": 1,
        "decision": "accepted",
        "sessionStatus": "stopping",
    }
    for response in (heartbeat, stop_response):
        assert "no-store" in response.headers["cache-control"]
        assert response.headers["pragma"] == "no-cache"

    with get_session() as db:
        capture_session = db.get(CaptureSession, reference.capture_session_id)
        source_lease = db.get(CaptureSourceLease, reference.source_lease_id)
        actual_segment = db.exec(
            select(CaptureSegment).where(
                CaptureSegment.session_id == reference.capture_session_id
            )
        ).one()
        lifecycle_operations = db.exec(
            select(CaptureOperation).where(
                CaptureOperation.session_id == reference.capture_session_id,
                cast(Any, CaptureOperation.operation).in_(
                    ("record_capture_heartbeat", "request_capture_stop")
                ),
            )
        ).all()
    assert capture_session is not None
    assert capture_session.state == "stopping"
    assert capture_session.started_at is not None
    assert capture_session.ended_at is None
    assert source_lease is not None
    assert source_lease.state == "closing"
    assert source_lease.stop_requested_at is not None
    assert source_lease.stop_proved_at is None
    assert source_lease.never_started_proved_at is None
    assert source_lease.closed_at is None
    assert actual_segment.capability_consumed_at is not None
    assert actual_segment.capability_revoked_at is None
    assert actual_segment.encrypted_capability is None
    assert len(lifecycle_operations) == 2
    assert len(provider.create_refs) == 1

    rendered_logs = repr(logs)
    for sensitive in (
        token,
        CAPABILITY_SECRET,
        capability_id,
        str(reference.capture_session_id),
        str(reference.actor_id),
        str(reference.consent_evidence_id),
        str(reference.source_lease_id),
    ):
        assert sensitive not in rendered_logs


def test_lifecycle_global_gate_precedes_token_resolution(
    authenticated_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reference = _ready_reference("lifecycle-gate")
    _configure(FakeProvider(), _settings(enabled=False))

    def unexpected_resolver(**kwargs):
        raise AssertionError(f"control resolver called: {kwargs!r}")

    monkeypatch.setattr(
        operations_route,
        "resolve_desktop_control_authority",
        unexpected_resolver,
    )
    response = authenticated_client.post(
        _heartbeat_url(reference),
        headers=_headers("t" * 43, key="route-lifecycle-gate-0001"),
        json={"capabilityId": str(uuid4())},
    )

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert response.json() == {"detail": "Desktop capture is unavailable."}


def test_lifecycle_rejects_wrong_capability_and_untrusted_body_fields(
    authenticated_client: TestClient,
) -> None:
    settings = _settings()
    reference = _ready_reference("lifecycle-binding")
    token = _desktop_token(reference, settings)
    provider = FakeProvider()
    _configure(provider, settings)
    start = authenticated_client.post(
        _url(reference),
        headers=_headers(token, key="route-lifecycle-binding-start"),
    )
    assert start.status_code == status.HTTP_200_OK

    wrong_capability = authenticated_client.post(
        _heartbeat_url(reference),
        headers=_headers(token, key="route-lifecycle-wrong-cap"),
        json={"capabilityId": str(uuid4())},
    )
    sensitive = "untrusted-patient-value"
    extra_authority = authenticated_client.post(
        _heartbeat_url(reference),
        headers=_headers(token, key="route-lifecycle-extra-body"),
        json={
            "capabilityId": start.json()["captureCapability"]["capabilityId"],
            "actorId": sensitive,
        },
    )

    assert wrong_capability.status_code == status.HTTP_403_FORBIDDEN
    assert wrong_capability.json() == {
        "detail": "Capture lifecycle request was denied."
    }
    assert extra_authority.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert extra_authority.json() == {"detail": "Invalid service request."}
    assert sensitive not in extra_authority.text
