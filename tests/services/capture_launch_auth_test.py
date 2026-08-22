from __future__ import annotations

import base64
import hashlib
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.configuration.desktop_capture import (
    MAX_DESKTOP_TOKEN_TTL_SECONDS,
    DesktopCaptureSettings,
)
from app.services.capture_launch_auth import (
    AuthorizedCaptureSessionReference,
    CaptureLaunchConflict,
    CaptureLaunchDenied,
    CaptureLaunchDisabled,
    authorize_launch_ticket,
    exchange_authorization_code,
    issue_launch_ticket,
    resolve_desktop_control_authority,
    resolve_desktop_stop_authority,
    resolve_desktop_token,
)

from app.models.appointment import Appointment
from app.models.capture_coordination import (
    CaptureLaunchAuthorization,
    CaptureSession,
    CaptureSourceLease,
)
from app.models.doctor import Doctor
from app.models.patient import Patient
from app.models.practice import Practice, PracticeMember

from tests.routes.utils import provision_practice_in_force

REDIRECT_URI = "cosmaneura-capture://oauth/callback"
VERIFIER = "v" * 43
INSTALLATION_THUMBPRINT = "i" * 43


def _settings(*, enabled: bool = True) -> DesktopCaptureSettings:
    return DesktopCaptureSettings(
        enabled=enabled,
        provider_create_enabled=False,
        recovery_enabled=False,
        redirect_uris=(REDIRECT_URI,),
        launch_ticket_ttl_seconds=120,
        authorization_code_ttl_seconds=300,
        desktop_token_ttl_seconds=300,
    )


def _challenge(verifier: str = VERIFIER) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _authorized_reference(
    *, suffix: str = "owner"
) -> tuple[AuthorizedCaptureSessionReference, Doctor, Appointment]:
    now = datetime.now(UTC)
    doctor = Doctor(
        clerk_id=f"user_test_desktop_{suffix}",
        email=f"desktop-{suffix}@example.test",
        biography=None,
    ).save()
    practice = provision_practice_in_force(doctor)
    patient = Patient(
        doctor_id=doctor.id,
        name=f"Synthetic Patient {suffix}",
        date_of_birth=datetime(2000, 1, 1, tzinfo=UTC),
    ).save()
    appointment = Appointment(
        doctor_id=doctor.id,
        patient_id=patient.id,
        appointment_date=now + timedelta(hours=1),
        external_id=None,
    ).save()
    consent_id = uuid4()
    capture_session = CaptureSession(
        practice_id=practice.id,
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
    source_lease = CaptureSourceLease(
        session_id=capture_session.id,
        practice_id=practice.id,
        actor_id=doctor.id,
        appointment_id=appointment.id,
        source_kind="desktop_companion",
        state="active",
        lease_token_digest=hashlib.sha256(suffix.encode()).hexdigest(),
        generation=0,
        acquired_at=now,
        control_deadline_at=now + timedelta(minutes=10),
    ).save()
    return (
        AuthorizedCaptureSessionReference(
            capture_session_id=capture_session.id,
            practice_id=practice.id,
            actor_id=doctor.id,
            appointment_id=appointment.id,
            consent_evidence_id=consent_id,
            source_lease_id=source_lease.id,
            state_version=1,
        ),
        doctor,
        appointment,
    )


def _authorize(
    reference: AuthorizedCaptureSessionReference,
    actor_binding: str,
    launch_ticket: str,
    *,
    now: datetime | None = None,
):
    return authorize_launch_ticket(
        launch_ticket=launch_ticket,
        actor_id=reference.actor_id,
        practice_id=reference.practice_id,
        actor_binding=actor_binding,
        code_challenge=_challenge(),
        code_challenge_method="S256",
        redirect_uri=REDIRECT_URI,
        installation_thumbprint=INSTALLATION_THUMBPRINT,
        settings=_settings(),
        now=now,
    )


def test_full_pkce_exchange_returns_exact_provider_neutral_grant():
    reference, doctor, _ = _authorized_reference()
    issued = issue_launch_ticket(
        reference=reference,
        actor_binding=doctor.clerk_id,
        idempotency_key="desktop-launch-request-001",
        settings=_settings(),
    )

    assert len(issued.launch_ticket) >= 43
    stored = next(CaptureLaunchAuthorization.all())
    assert stored.launch_ticket_digest != issued.launch_ticket
    assert stored.authorization_code_digest is None
    assert stored.desktop_token_digest is None
    assert stored.pkce_code_challenge is None
    assert stored.redirect_uri_digest is None
    assert stored.installation_thumbprint_digest is None

    code = _authorize(reference, doctor.clerk_id, issued.launch_ticket)
    assert code.redirect_uri == REDIRECT_URI
    stored.refresh()
    assert stored.state == "code_issued"
    assert stored.authorization_code_digest != code.authorization_code
    assert stored.ticket_consumed_at is not None
    assert stored.pkce_code_challenge == _challenge()
    assert stored.redirect_uri_digest != REDIRECT_URI
    assert stored.installation_thumbprint_digest != INSTALLATION_THUMBPRINT

    exchanged = exchange_authorization_code(
        authorization_code=code.authorization_code,
        code_verifier=VERIFIER,
        redirect_uri=REDIRECT_URI,
        settings=_settings(),
    )
    assert exchanged.access_token not in str(stored.model_dump())
    assert exchanged.grant == (
        type(exchanged.grant)(
            capture_session_id=str(reference.capture_session_id),
            actor_id=str(reference.actor_id),
            consent_record_id=str(reference.consent_evidence_id),
            consent_status="granted",
            session_status="authorized",
            source="desktop_companion",
            source_lease_id=str(reference.source_lease_id),
        )
    )
    assert (
        resolve_desktop_token(
            access_token=exchanged.access_token,
            settings=_settings(),
        )
        == exchanged.grant
    )

    stored.refresh()
    assert stored.state == "token_issued"
    assert stored.authorization_code_consumed_at is not None
    assert stored.desktop_token_digest != exchanged.access_token
    with pytest.raises(CaptureLaunchDenied, match="invalid_grant"):
        exchange_authorization_code(
            authorization_code=code.authorization_code,
            code_verifier=VERIFIER,
            redirect_uri=REDIRECT_URI,
            settings=_settings(),
        )


def test_launch_ticket_is_idempotent_without_persisting_the_bearer():
    reference, doctor, _ = _authorized_reference(suffix="idempotent")
    kwargs = {
        "reference": reference,
        "actor_binding": doctor.clerk_id,
        "idempotency_key": "desktop-launch-idempotent-001",
        "settings": _settings(),
    }

    first = issue_launch_ticket(**kwargs)
    second = issue_launch_ticket(**kwargs)

    assert first == second
    assert CaptureLaunchAuthorization.count() == 1
    assert first.launch_ticket not in str(
        next(CaptureLaunchAuthorization.all()).model_dump()
    )


def test_idempotent_retry_cannot_replay_consumed_ticket():
    reference, doctor, _ = _authorized_reference(suffix="consumed-retry")
    kwargs = {
        "reference": reference,
        "actor_binding": doctor.clerk_id,
        "idempotency_key": "desktop-launch-consumed-retry-001",
        "settings": _settings(),
    }
    issued = issue_launch_ticket(**kwargs)
    _authorize(reference, doctor.clerk_id, issued.launch_ticket)

    with pytest.raises(CaptureLaunchConflict, match="launch_conflict"):
        issue_launch_ticket(**kwargs)


def test_ticket_is_bound_to_same_real_clinician_and_practice():
    reference, doctor, _ = _authorized_reference(suffix="bound")
    other_reference, other_doctor, _ = _authorized_reference(suffix="other")
    ticket = issue_launch_ticket(
        reference=reference,
        actor_binding=doctor.clerk_id,
        idempotency_key="desktop-launch-bound-001",
        settings=_settings(),
    )

    with pytest.raises(CaptureLaunchDenied, match="launch_not_authorized"):
        authorize_launch_ticket(
            launch_ticket=ticket.launch_ticket,
            actor_id=other_reference.actor_id,
            practice_id=other_reference.practice_id,
            actor_binding=other_doctor.clerk_id,
            code_challenge=_challenge(),
            code_challenge_method="S256",
            redirect_uri=REDIRECT_URI,
            installation_thumbprint=INSTALLATION_THUMBPRINT,
            settings=_settings(),
        )

    stored = next(CaptureLaunchAuthorization.all())
    assert stored.ticket_consumed_at is None
    _authorize(reference, doctor.clerk_id, ticket.launch_ticket)


def test_wrong_pkce_or_redirect_cannot_consume_authorization_code():
    reference, doctor, _ = _authorized_reference(suffix="pkce")
    ticket = issue_launch_ticket(
        reference=reference,
        actor_binding=doctor.clerk_id,
        idempotency_key="desktop-launch-pkce-001",
        settings=_settings(),
    )
    code = _authorize(reference, doctor.clerk_id, ticket.launch_ticket)

    for verifier, redirect_uri in (
        ("x" * 43, REDIRECT_URI),
        (VERIFIER, "cosmaneura-capture://oauth/not-allowed"),
    ):
        with pytest.raises(
            CaptureLaunchDenied, match="invalid_grant|launch_not_authorized"
        ):
            exchange_authorization_code(
                authorization_code=code.authorization_code,
                code_verifier=verifier,
                redirect_uri=redirect_uri,
                settings=_settings(),
            )

    stored = next(CaptureLaunchAuthorization.all())
    assert stored.authorization_code_consumed_at is None
    exchange_authorization_code(
        authorization_code=code.authorization_code,
        code_verifier=VERIFIER,
        redirect_uri=REDIRECT_URI,
        settings=_settings(),
    )


def test_expired_ticket_and_cancelled_appointment_fail_closed():
    reference, doctor, appointment = _authorized_reference(suffix="expiry")
    issued_at = datetime.now(UTC)
    ticket = issue_launch_ticket(
        reference=reference,
        actor_binding=doctor.clerk_id,
        idempotency_key="desktop-launch-expiry-001",
        settings=_settings(),
        now=issued_at,
    )

    with pytest.raises(CaptureLaunchDenied, match="launch_not_authorized"):
        _authorize(
            reference,
            doctor.clerk_id,
            ticket.launch_ticket,
            now=issued_at + timedelta(seconds=120),
        )
    stored = next(CaptureLaunchAuthorization.all())
    assert stored.state == "expired"
    with pytest.raises(CaptureLaunchConflict, match="launch_conflict"):
        issue_launch_ticket(
            reference=reference,
            actor_binding=doctor.clerk_id,
            idempotency_key="desktop-launch-expiry-001",
            settings=_settings(),
            now=issued_at + timedelta(seconds=120),
        )

    second = issue_launch_ticket(
        reference=reference,
        actor_binding=doctor.clerk_id,
        idempotency_key="desktop-launch-cancelled-002",
        settings=_settings(),
    )
    appointment.cancelled_at = datetime.now(UTC)
    appointment.save()
    with pytest.raises(CaptureLaunchDenied, match="launch_not_authorized"):
        _authorize(reference, doctor.clerk_id, second.launch_ticket)
    rows = tuple(CaptureLaunchAuthorization.all())
    assert any(row.state == "revoked" for row in rows)
    with pytest.raises(CaptureLaunchConflict, match="launch_conflict"):
        issue_launch_ticket(
            reference=reference,
            actor_binding=doctor.clerk_id,
            idempotency_key="desktop-launch-cancelled-002",
            settings=_settings(),
        )


def test_attestation_is_revalidated_before_ticket_issuance():
    reference, doctor, _ = _authorized_reference(suffix="attestation-issue")
    membership = PracticeMember.get(doctor_id=doctor.id)
    assert membership is not None
    membership.attested_at = None
    membership.save()

    with pytest.raises(CaptureLaunchDenied, match="launch_not_authorized"):
        issue_launch_ticket(
            reference=reference,
            actor_binding=doctor.clerk_id,
            idempotency_key="desktop-launch-attestation-issue-001",
            settings=_settings(),
        )


def test_baa_is_revalidated_before_browser_authorization():
    reference, doctor, _ = _authorized_reference(suffix="baa-authorize")
    issued = issue_launch_ticket(
        reference=reference,
        actor_binding=doctor.clerk_id,
        idempotency_key="desktop-launch-baa-authorize-001",
        settings=_settings(),
    )
    practice = Practice.get(id=reference.practice_id)
    assert practice is not None
    practice.baa_accepted_at = None
    practice.negotiated_baa_on_file = False
    practice.save()

    with pytest.raises(CaptureLaunchDenied, match="launch_not_authorized"):
        _authorize(reference, doctor.clerk_id, issued.launch_ticket)
    assert next(CaptureLaunchAuthorization.all()).state == "revoked"


def test_entitlement_is_revalidated_before_code_exchange():
    reference, doctor, _ = _authorized_reference(suffix="entitlement-exchange")
    issued = issue_launch_ticket(
        reference=reference,
        actor_binding=doctor.clerk_id,
        idempotency_key="desktop-launch-entitlement-exchange-001",
        settings=_settings(),
    )
    code = _authorize(reference, doctor.clerk_id, issued.launch_ticket)
    practice = Practice.get(id=reference.practice_id)
    assert practice is not None
    practice.billing_exempt = False
    practice.billing_exempt_until = None
    practice.subscription_status = None
    practice.trial_ends_at = None
    practice.save()

    with pytest.raises(CaptureLaunchDenied, match="invalid_grant"):
        exchange_authorization_code(
            authorization_code=code.authorization_code,
            code_verifier=VERIFIER,
            redirect_uri=REDIRECT_URI,
            settings=_settings(),
        )
    assert next(CaptureLaunchAuthorization.all()).state == "revoked"


def test_attestation_is_revalidated_when_desktop_token_is_resolved():
    reference, doctor, _ = _authorized_reference(suffix="attestation-token")
    issued = issue_launch_ticket(
        reference=reference,
        actor_binding=doctor.clerk_id,
        idempotency_key="desktop-launch-attestation-token-001",
        settings=_settings(),
    )
    code = _authorize(reference, doctor.clerk_id, issued.launch_ticket)
    token = exchange_authorization_code(
        authorization_code=code.authorization_code,
        code_verifier=VERIFIER,
        redirect_uri=REDIRECT_URI,
        settings=_settings(),
    )
    membership = PracticeMember.get(doctor_id=doctor.id)
    assert membership is not None
    membership.attested_at = None
    membership.save()

    with pytest.raises(CaptureLaunchDenied, match="invalid_token"):
        resolve_desktop_token(
            access_token=token.access_token,
            settings=_settings(),
        )
    assert next(CaptureLaunchAuthorization.all()).state == "revoked"


def test_launch_is_disabled_before_any_database_or_provider_work():
    reference, doctor, _ = _authorized_reference(suffix="disabled")
    before = CaptureLaunchAuthorization.count()

    with pytest.raises(CaptureLaunchDisabled, match="desktop_capture_disabled"):
        issue_launch_ticket(
            reference=reference,
            actor_binding=doctor.clerk_id,
            idempotency_key="desktop-launch-disabled-001",
            settings=_settings(enabled=False),
        )

    assert CaptureLaunchAuthorization.count() == before


def test_control_authority_enforces_ttl_and_stop_has_bounded_grace() -> None:
    reference, doctor, _ = _authorized_reference(suffix="control-token-expiry")
    issued_at = datetime.now(UTC)
    ticket = issue_launch_ticket(
        reference=reference,
        actor_binding=doctor.clerk_id,
        idempotency_key="desktop-control-token-expiry-001",
        settings=_settings(),
        now=issued_at,
    )
    code = _authorize(
        reference,
        doctor.clerk_id,
        ticket.launch_ticket,
        now=issued_at + timedelta(seconds=1),
    )
    token = exchange_authorization_code(
        authorization_code=code.authorization_code,
        code_verifier=VERIFIER,
        redirect_uri=REDIRECT_URI,
        settings=_settings(),
        now=issued_at + timedelta(seconds=2),
    )
    capture_session = CaptureSession.get(id=reference.capture_session_id)
    assert capture_session is not None
    capture_session.state = "starting"
    capture_session.save()

    resolved = resolve_desktop_control_authority(
        access_token=token.access_token,
        settings=_settings(),
        now=token.expires_at - timedelta(microseconds=1),
    )
    assert resolved.capture_session_id == reference.capture_session_id
    with pytest.raises(CaptureLaunchDenied, match="invalid_token"):
        resolve_desktop_control_authority(
            access_token=token.access_token,
            settings=_settings(),
            now=token.expires_at,
        )
    assert next(CaptureLaunchAuthorization.all()).state == "expired"

    stop_authority = resolve_desktop_stop_authority(
        access_token=token.access_token,
        now=token.expires_at + timedelta(minutes=15) - timedelta(microseconds=1),
    )
    assert stop_authority.capture_session_id == reference.capture_session_id
    with pytest.raises(CaptureLaunchDenied, match="invalid_token"):
        resolve_desktop_stop_authority(
            access_token=token.access_token,
            now=token.expires_at + timedelta(minutes=15),
        )


def test_desktop_bearer_hard_max_is_six_hours() -> None:
    settings = DesktopCaptureSettings(
        enabled=True,
        provider_create_enabled=False,
        recovery_enabled=False,
        redirect_uris=(REDIRECT_URI,),
        desktop_token_ttl_seconds=MAX_DESKTOP_TOKEN_TTL_SECONDS,
    )
    assert settings.desktop_token_ttl_seconds == 6 * 60 * 60
    with pytest.raises(ValueError, match="desktop token lifetime"):
        DesktopCaptureSettings(
            enabled=True,
            provider_create_enabled=False,
            recovery_enabled=False,
            redirect_uris=(REDIRECT_URI,),
            desktop_token_ttl_seconds=MAX_DESKTOP_TOKEN_TTL_SECONDS + 1,
        )
