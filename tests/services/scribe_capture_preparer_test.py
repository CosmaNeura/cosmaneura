from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from app.configuration.desktop_capture import DesktopCaptureSettings
from app.configuration.scribe_workflow import ScribeWorkflowSettings
from app.services.capture_launch_auth import (
    CaptureConsentAttestation,
    CaptureLaunchConflict,
    CaptureLaunchDenied,
    CaptureLaunchUnavailable,
    issue_launch_ticket,
)
from app.services.scribe_capture_preparer import (
    DisabledScribeCaptureSessionPreparer,
    ScribeCaptureSessionPreparer,
    build_scribe_capture_session_preparer,
)
from app.services.scribe_workflow import (
    CaptureSession as ScribeCaptureSession,
)
from app.services.scribe_workflow import (
    ConsentCreate,
    ConsentRecord,
    ScribeWorkflowRejectedError,
    ScribeWorkflowRetryableError,
    SessionCreate,
)

from app.models.capture_coordination import (
    CaptureLaunchAuthorization,
    CaptureOperation,
    CaptureSegment,
    CaptureSourceLease,
)
from app.models.capture_coordination import (
    CaptureSession as LocalCaptureSession,
)
from app.models.doctor import DoctorRole

from tests.factories import AppointmentFactory, DoctorFactory, PatientFactory
from tests.routes.utils import provision_practice_in_force

NOW = datetime(2026, 8, 21, 20, 0, tzinfo=UTC)
REDIRECT_URI = "cosmaneura-capture://oauth/callback"


def _desktop_settings() -> DesktopCaptureSettings:
    return DesktopCaptureSettings(
        enabled=True,
        provider_create_enabled=False,
        recovery_enabled=False,
        redirect_uris=(REDIRECT_URI,),
    )


@dataclass
class FakeScribeClient:
    consent_id: UUID = field(default_factory=uuid4)
    session_id: UUID = field(default_factory=uuid4)
    consent_updates: dict[str, object] = field(default_factory=dict)
    session_updates: dict[str, object] = field(default_factory=dict)
    consent_calls: list[tuple[UUID, ConsentCreate, str]] = field(default_factory=list)
    session_calls: list[tuple[UUID, SessionCreate, str]] = field(default_factory=list)
    consent_error: BaseException | None = None
    session_error: BaseException | None = None
    before_consent_return: Callable[[], Awaitable[None]] | None = None
    before_session_return: Callable[[], Awaitable[None]] | None = None
    closed: bool = False

    async def create_consent(
        self,
        tenant_id: UUID,
        payload: ConsentCreate,
        *,
        idempotency_key: str,
    ) -> ConsentRecord:
        self.consent_calls.append((tenant_id, payload, idempotency_key))
        if self.consent_error is not None:
            raise self.consent_error
        result = ConsentRecord(
            id=self.consent_id,
            policy_version="all-party-v1",
            revoked_at=None,
            **payload.model_dump(),
        )
        if self.before_consent_return is not None:
            await self.before_consent_return()
        return result.model_copy(update=self.consent_updates)

    async def create_session(
        self,
        tenant_id: UUID,
        payload: SessionCreate,
        *,
        idempotency_key: str,
    ) -> ScribeCaptureSession:
        self.session_calls.append((tenant_id, payload, idempotency_key))
        if self.session_error is not None:
            raise self.session_error
        result = ScribeCaptureSession(
            id=self.session_id,
            status="created",
            created_at=NOW,
            **payload.model_dump(),
        )
        if self.before_session_return is not None:
            await self.before_session_return()
        return result.model_copy(update=self.session_updates)

    async def close(self) -> None:
        self.closed = True


def _scope(*, suffix: str = "scribe-prepare"):
    doctor = DoctorFactory.build(
        email=f"{suffix}-{uuid4()}@example.test",
        role=DoctorRole.normal,
        deleted_at=None,
    ).save()
    practice = provision_practice_in_force(doctor)
    appointment = AppointmentFactory.build(
        doctor_id=doctor.id,
        cancelled_at=None,
    ).save()
    return doctor, practice, appointment


def _attestation(**updates: object) -> CaptureConsentAttestation:
    values: dict[str, object] = {
        "modality": "virtual",
        "participant_regions": frozenset({"FL", "OH"}),
        "method": "verbal_attestation",
        "all_parties_consented": True,
        "granted_at": NOW,
    }
    values.update(updates)
    return CaptureConsentAttestation(**values)  # type: ignore[arg-type]


def _preparer(
    client: FakeScribeClient | None = None,
    *,
    current: datetime = NOW,
) -> tuple[ScribeCaptureSessionPreparer, FakeScribeClient]:
    fake_client = client or FakeScribeClient()
    return (
        ScribeCaptureSessionPreparer(
            fake_client,
            clock=lambda: current,
        ),
        fake_client,
    )


@pytest.mark.asyncio
async def test_preparer_derives_exact_scope_and_external_transcript_mode() -> None:
    doctor, practice, appointment = _scope()
    preparer, scribe = _preparer()

    reference = await preparer(
        actor_id=doctor.id,
        practice_id=practice.id,
        appointment_id=appointment.id,
        idempotency_key="desktop-scribe-prepare-001",
        attestation=_attestation(),
    )

    assert reference.actor_id == doctor.id
    assert reference.practice_id == practice.id
    assert reference.appointment_id == appointment.id
    assert len(scribe.consent_calls) == 1
    tenant_id, consent, consent_key = scribe.consent_calls[0]
    assert tenant_id == getattr(practice.id, "uuid")
    assert consent.appointment_id == str(appointment.id)
    assert consent.practitioner_id == str(doctor.id)
    assert consent.patient_id == str(appointment.patient_id)
    assert consent.modality == "virtual"
    assert consent.participant_regions == {"FL", "OH"}
    assert consent.method == "verbal_attestation"
    assert consent.all_parties_consented is True
    assert consent.evidence_object_id is None
    assert set(consent.model_dump()) == {
        "appointment_id",
        "practitioner_id",
        "patient_id",
        "modality",
        "participant_regions",
        "method",
        "all_parties_consented",
        "granted_at",
        "evidence_object_id",
    }

    assert len(scribe.session_calls) == 1
    session_tenant, session, session_key = scribe.session_calls[0]
    assert session_tenant == getattr(practice.id, "uuid")
    assert session.appointment_id == str(appointment.id)
    assert session.practitioner_id == str(doctor.id)
    assert session.patient_id == str(appointment.patient_id)
    assert session.consent_record_id == scribe.consent_id
    assert session.modality == "virtual"
    assert session.ingest_mode == "external_async_transcript"
    assert consent_key != session_key
    assert consent_key != "desktop-scribe-prepare-001"
    assert session_key != "desktop-scribe-prepare-001"

    local_session = LocalCaptureSession.get(id=reference.capture_session_id)
    assert local_session is not None
    assert local_session.state == "ready"
    assert local_session.consent_state == "granted"
    assert local_session.scribe_consent_receipt_id == scribe.consent_id
    assert local_session.consent_policy_version == "all-party-v1"
    assert local_session.processing_session_id == scribe.session_id
    lease = CaptureSourceLease.get(id=reference.source_lease_id)
    assert lease is not None
    assert lease.state == "active"
    operation = CaptureOperation.get(session_id=reference.capture_session_id)
    assert operation is not None
    assert operation.state == "succeeded"
    persisted = repr(
        {
            "session": local_session.model_dump(),
            "lease": lease.model_dump(),
            "operation": operation.model_dump(),
        }
    )
    assert "verbal_attestation" not in persisted
    assert NOW.isoformat() not in persisted
    assert "'FL'" not in persisted
    assert "'OH'" not in persisted


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "updates",
    [
        {"all_parties_consented": False},
        {"granted_at": NOW + timedelta(minutes=3)},
        {"granted_at": NOW - timedelta(hours=24, microseconds=1)},
        {"participant_regions": frozenset({"ZZ"})},
        {"participant_regions": frozenset({"us-fl"})},
    ],
)
async def test_invalid_attestation_never_reaches_scribe(
    updates: dict[str, object],
) -> None:
    doctor, practice, appointment = _scope(suffix="invalid-attestation")
    preparer, scribe = _preparer()

    with pytest.raises(CaptureLaunchDenied, match="launch_not_authorized"):
        await preparer(
            actor_id=doctor.id,
            practice_id=practice.id,
            appointment_id=appointment.id,
            idempotency_key="desktop-invalid-attestation-001",
            attestation=_attestation(**updates),
        )

    assert scribe.consent_calls == []
    assert scribe.session_calls == []
    assert LocalCaptureSession.first() is None


@pytest.mark.asyncio
async def test_cross_tenant_scope_never_reaches_scribe() -> None:
    doctor, practice, _ = _scope(suffix="tenant-owner")
    _, _, other_appointment = _scope(suffix="tenant-other")
    preparer, scribe = _preparer()

    with pytest.raises(CaptureLaunchDenied, match="launch_not_authorized"):
        await preparer(
            actor_id=doctor.id,
            practice_id=practice.id,
            appointment_id=other_appointment.id,
            idempotency_key="desktop-cross-tenant-001",
            attestation=_attestation(),
        )

    assert scribe.consent_calls == []
    assert LocalCaptureSession.first() is None


@pytest.mark.asyncio
async def test_cancelled_appointment_never_reaches_scribe() -> None:
    doctor, practice, appointment = _scope(suffix="cancelled")
    appointment.cancelled_at = NOW
    appointment.save()
    preparer, scribe = _preparer()

    with pytest.raises(CaptureLaunchDenied, match="launch_not_authorized"):
        await preparer(
            actor_id=doctor.id,
            practice_id=practice.id,
            appointment_id=appointment.id,
            idempotency_key="desktop-cancelled-001",
            attestation=_attestation(),
        )

    assert scribe.consent_calls == []
    assert LocalCaptureSession.first() is None


@pytest.mark.asyncio
async def test_retry_reuses_domain_separated_operation_keys() -> None:
    doctor, practice, appointment = _scope(suffix="retry")
    preparer, scribe = _preparer()
    arguments = {
        "actor_id": doctor.id,
        "practice_id": practice.id,
        "appointment_id": appointment.id,
        "idempotency_key": "desktop-scribe-retry-001",
        "attestation": _attestation(),
    }

    first = await preparer(**arguments)
    second = await preparer(**arguments)

    assert first == second
    assert len(scribe.consent_calls) == 1
    assert len(scribe.session_calls) == 1
    assert scribe.consent_calls[0][2] != scribe.session_calls[0][2]
    assert LocalCaptureSession.count() == 1
    assert CaptureSourceLease.count() == 1


@pytest.mark.asyncio
async def test_new_key_after_expired_pre_pkce_ticket_releases_and_reprepares() -> None:
    doctor, practice, appointment = _scope(suffix="expired-ticket")
    first_preparer, first_scribe = _preparer()
    first_reference = await first_preparer(
        actor_id=doctor.id,
        practice_id=practice.id,
        appointment_id=appointment.id,
        idempotency_key="desktop-expired-ticket-first-001",
        attestation=_attestation(),
    )
    first_ticket = issue_launch_ticket(
        reference=first_reference,
        actor_binding="expired-ticket-actor-binding",
        idempotency_key="desktop-expired-ticket-first-001",
        settings=_desktop_settings(),
        now=NOW,
    )

    retry_at = NOW + timedelta(minutes=3)
    second_preparer, second_scribe = _preparer(current=retry_at)
    second_reference = await second_preparer(
        actor_id=doctor.id,
        practice_id=practice.id,
        appointment_id=appointment.id,
        idempotency_key="desktop-expired-ticket-second-002",
        attestation=_attestation(),
    )
    second_ticket = issue_launch_ticket(
        reference=second_reference,
        actor_binding="expired-ticket-actor-binding",
        idempotency_key="desktop-expired-ticket-second-002",
        settings=_desktop_settings(),
        now=retry_at,
    )

    assert first_reference.capture_session_id != second_reference.capture_session_id
    assert first_reference.source_lease_id != second_reference.source_lease_id
    assert first_ticket.launch_ticket != second_ticket.launch_ticket
    assert len(first_scribe.consent_calls) == len(first_scribe.session_calls) == 1
    assert len(second_scribe.consent_calls) == len(second_scribe.session_calls) == 1
    assert first_scribe.consent_calls[0][2] != second_scribe.consent_calls[0][2]
    assert first_scribe.session_calls[0][2] != second_scribe.session_calls[0][2]

    first_session = LocalCaptureSession.get(id=first_reference.capture_session_id)
    assert first_session is not None
    assert first_session.state == "cancelled"
    first_lease = CaptureSourceLease.get(id=first_reference.source_lease_id)
    assert first_lease is not None
    assert first_lease.state == "closed"
    assert first_lease.stop_requested_at == retry_at
    assert first_lease.never_started_proved_at == retry_at
    first_authorization = CaptureLaunchAuthorization.get(
        session_id=first_reference.capture_session_id
    )
    assert first_authorization is not None
    assert first_authorization.state == "expired"


@pytest.mark.asyncio
async def test_expired_launch_is_not_released_after_a_segment_exists() -> None:
    doctor, practice, appointment = _scope(suffix="expired-ticket-started")
    first_preparer, first_scribe = _preparer()
    first_reference = await first_preparer(
        actor_id=doctor.id,
        practice_id=practice.id,
        appointment_id=appointment.id,
        idempotency_key="desktop-expired-started-first-001",
        attestation=_attestation(),
    )
    issue_launch_ticket(
        reference=first_reference,
        actor_binding="expired-started-actor-binding",
        idempotency_key="desktop-expired-started-first-001",
        settings=_desktop_settings(),
        now=NOW,
    )
    CaptureSegment(
        session_id=first_reference.capture_session_id,
        practice_id=practice.id,
        actor_id=doctor.id,
        appointment_id=appointment.id,
        ordinal=0,
        channel="mixed",
        encrypted_capability=None,
    ).save()

    retry_preparer, retry_scribe = _preparer(current=NOW + timedelta(minutes=3))
    with pytest.raises(
        CaptureLaunchConflict,
        match="capture_session_preparation_conflict",
    ):
        await retry_preparer(
            actor_id=doctor.id,
            practice_id=practice.id,
            appointment_id=appointment.id,
            idempotency_key="desktop-expired-started-second-002",
            attestation=_attestation(),
        )

    assert len(first_scribe.consent_calls) == len(first_scribe.session_calls) == 1
    assert retry_scribe.consent_calls == []
    assert retry_scribe.session_calls == []
    first_session = LocalCaptureSession.get(id=first_reference.capture_session_id)
    first_lease = CaptureSourceLease.get(id=first_reference.source_lease_id)
    assert first_session is not None and first_session.state == "ready"
    assert first_lease is not None and first_lease.closed_at is None


@pytest.mark.asyncio
async def test_expired_launch_is_not_released_for_changed_consent_facts() -> None:
    doctor, practice, appointment = _scope(suffix="expired-ticket-changed")
    first_preparer, _ = _preparer()
    first_reference = await first_preparer(
        actor_id=doctor.id,
        practice_id=practice.id,
        appointment_id=appointment.id,
        idempotency_key="desktop-expired-changed-first-001",
        attestation=_attestation(),
    )
    issue_launch_ticket(
        reference=first_reference,
        actor_binding="expired-changed-actor-binding",
        idempotency_key="desktop-expired-changed-first-001",
        settings=_desktop_settings(),
        now=NOW,
    )

    retry_preparer, retry_scribe = _preparer(current=NOW + timedelta(minutes=3))
    with pytest.raises(CaptureLaunchConflict):
        await retry_preparer(
            actor_id=doctor.id,
            practice_id=practice.id,
            appointment_id=appointment.id,
            idempotency_key="desktop-expired-changed-second-002",
            attestation=_attestation(method="pre_visit_form"),
        )

    assert retry_scribe.consent_calls == []
    assert retry_scribe.session_calls == []
    first_session = LocalCaptureSession.get(id=first_reference.capture_session_id)
    first_lease = CaptureSourceLease.get(id=first_reference.source_lease_id)
    assert first_session is not None and first_session.state == "ready"
    assert first_lease is not None and first_lease.closed_at is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("consent_updates", "session_updates", "expected_session_calls"),
    [
        ({"appointment_id": "appointment_wrong"}, {}, 0),
        ({"revoked_at": NOW}, {}, 0),
        ({}, {"patient_id": "patient_wrong"}, 1),
        ({}, {"ingest_mode": "chunk_upload"}, 1),
    ],
)
async def test_mismatched_scribe_receipt_never_becomes_local_authority(
    consent_updates: dict[str, object],
    session_updates: dict[str, object],
    expected_session_calls: int,
) -> None:
    doctor, practice, appointment = _scope(suffix="mismatch")
    client = FakeScribeClient(
        consent_updates=consent_updates,
        session_updates=session_updates,
    )
    preparer, scribe = _preparer(client)

    with pytest.raises(
        CaptureLaunchUnavailable,
        match="capture_session_preparation_unavailable",
    ):
        await preparer(
            actor_id=doctor.id,
            practice_id=practice.id,
            appointment_id=appointment.id,
            idempotency_key="desktop-response-mismatch-001",
            attestation=_attestation(),
        )

    assert len(scribe.session_calls) == expected_session_calls
    local_session = LocalCaptureSession.first()
    assert local_session is not None
    assert local_session.state == "review_required"
    assert local_session.consent_state == "uncertain"
    lease = CaptureSourceLease.get(session_id=local_session.id)
    assert lease is not None
    assert lease.state == "uncertain"


@pytest.mark.asyncio
async def test_different_key_cannot_reach_scribe_while_first_call_is_in_flight() -> (
    None
):
    doctor, practice, appointment = _scope(suffix="concurrent")
    competing_client = FakeScribeClient()
    competing_preparer, _ = _preparer(competing_client)

    async def attempt_competing_launch() -> None:
        with pytest.raises(CaptureLaunchConflict):
            await competing_preparer(
                actor_id=doctor.id,
                practice_id=practice.id,
                appointment_id=appointment.id,
                idempotency_key="desktop-concurrent-second-002",
                attestation=_attestation(),
            )

    winning_client = FakeScribeClient(before_consent_return=attempt_competing_launch)
    winning_preparer, _ = _preparer(winning_client)
    await winning_preparer(
        actor_id=doctor.id,
        practice_id=practice.id,
        appointment_id=appointment.id,
        idempotency_key="desktop-concurrent-first-001",
        attestation=_attestation(),
    )

    assert len(winning_client.consent_calls) == 1
    assert len(winning_client.session_calls) == 1
    assert competing_client.consent_calls == []
    assert competing_client.session_calls == []
    assert LocalCaptureSession.count() == 1
    assert CaptureSourceLease.count() == 1


@pytest.mark.asyncio
async def test_deterministic_scribe_rejection_closes_with_never_started_proof() -> None:
    doctor, practice, appointment = _scope(suffix="rejected")
    client = FakeScribeClient(
        consent_error=ScribeWorkflowRejectedError("scribe_http_422")
    )
    preparer, _ = _preparer(client)

    with pytest.raises(CaptureLaunchDenied, match="launch_not_authorized"):
        await preparer(
            actor_id=doctor.id,
            practice_id=practice.id,
            appointment_id=appointment.id,
            idempotency_key="desktop-scribe-rejected-001",
            attestation=_attestation(),
        )

    local_session = LocalCaptureSession.first()
    assert local_session is not None
    assert local_session.state == "failed"
    lease = CaptureSourceLease.get(session_id=local_session.id)
    assert lease is not None
    assert lease.state == "closed"
    assert lease.stop_requested_at == NOW
    assert lease.never_started_proved_at == NOW
    assert lease.stop_proved_at is None
    assert lease.closed_at == NOW
    operation = CaptureOperation.get(session_id=local_session.id)
    assert operation is not None
    assert operation.state == "failed"
    assert operation.last_error_code == "scribe_rejected"


@pytest.mark.asyncio
async def test_session_rejection_preserves_consent_receipt_and_never_started_proof() -> (
    None
):
    doctor, practice, appointment = _scope(suffix="session-rejected")
    client = FakeScribeClient(
        session_error=ScribeWorkflowRejectedError("scribe_http_409")
    )
    preparer, _ = _preparer(client)

    with pytest.raises(CaptureLaunchDenied, match="launch_not_authorized"):
        await preparer(
            actor_id=doctor.id,
            practice_id=practice.id,
            appointment_id=appointment.id,
            idempotency_key="desktop-scribe-session-rejected-001",
            attestation=_attestation(),
        )

    local_session = LocalCaptureSession.first()
    assert local_session is not None
    assert local_session.state == "failed"
    assert local_session.scribe_consent_receipt_id == client.consent_id
    assert local_session.consent_policy_version == "all-party-v1"
    assert local_session.processing_session_id is None
    lease = CaptureSourceLease.get(session_id=local_session.id)
    assert lease is not None
    assert lease.state == "closed"
    assert lease.never_started_proved_at == NOW


@pytest.mark.asyncio
async def test_cancelled_during_scribe_call_maps_external_session_before_closing() -> (
    None
):
    doctor, practice, appointment = _scope(suffix="cancel-during-call")

    async def cancel_appointment() -> None:
        appointment.cancelled_at = NOW
        appointment.save()

    client = FakeScribeClient(before_session_return=cancel_appointment)
    preparer, _ = _preparer(client)

    with pytest.raises(CaptureLaunchDenied, match="launch_not_authorized"):
        await preparer(
            actor_id=doctor.id,
            practice_id=practice.id,
            appointment_id=appointment.id,
            idempotency_key="desktop-cancel-during-scribe-001",
            attestation=_attestation(),
        )

    local_session = LocalCaptureSession.first()
    assert local_session is not None
    assert local_session.state == "failed"
    assert local_session.scribe_consent_receipt_id == client.consent_id
    assert local_session.processing_session_id == client.session_id
    lease = CaptureSourceLease.get(session_id=local_session.id)
    assert lease is not None
    assert lease.state == "closed"
    assert lease.never_started_proved_at == NOW


@pytest.mark.asyncio
async def test_patient_change_during_scribe_call_fails_closed() -> None:
    doctor, practice, appointment = _scope(suffix="patient-change")
    replacement_patient = PatientFactory.build(doctor_id=doctor.id).save()

    async def replace_patient() -> None:
        appointment.patient_id = replacement_patient.id
        appointment.save()

    client = FakeScribeClient(before_session_return=replace_patient)
    preparer, _ = _preparer(client)

    with pytest.raises(CaptureLaunchDenied, match="launch_not_authorized"):
        await preparer(
            actor_id=doctor.id,
            practice_id=practice.id,
            appointment_id=appointment.id,
            idempotency_key="desktop-patient-change-001",
            attestation=_attestation(),
        )

    local_session = LocalCaptureSession.first()
    assert local_session is not None
    assert local_session.state == "failed"
    assert local_session.consent_state == "pending"
    assert local_session.processing_session_id == client.session_id
    assert LocalCaptureSession.count() == 1
    lease = CaptureSourceLease.get(session_id=local_session.id)
    assert lease is not None
    assert lease.state == "closed"
    assert lease.never_started_proved_at == NOW


@pytest.mark.asyncio
async def test_ambiguous_scribe_failure_retains_uncertain_lease() -> None:
    doctor, practice, appointment = _scope(suffix="uncertain")
    client = FakeScribeClient(
        consent_error=ScribeWorkflowRetryableError("scribe_transport_unavailable")
    )
    preparer, _ = _preparer(client)

    with pytest.raises(CaptureLaunchUnavailable):
        await preparer(
            actor_id=doctor.id,
            practice_id=practice.id,
            appointment_id=appointment.id,
            idempotency_key="desktop-scribe-uncertain-001",
            attestation=_attestation(),
        )

    local_session = LocalCaptureSession.first()
    assert local_session is not None
    assert local_session.state == "review_required"
    assert local_session.consent_state == "uncertain"
    lease = CaptureSourceLease.get(session_id=local_session.id)
    assert lease is not None
    assert lease.state == "uncertain"
    assert lease.closed_at is None
    assert lease.never_started_proved_at is None
    operation = CaptureOperation.get(session_id=local_session.id)
    assert operation is not None
    assert operation.state == "uncertain"
    assert operation.lease_expires_at == NOW + timedelta(seconds=30)


@pytest.mark.asyncio
async def test_same_key_reclaims_stale_attempt_after_consent_before_session() -> None:
    doctor, practice, appointment = _scope(suffix="stale-consent")
    client = FakeScribeClient(session_error=asyncio.CancelledError())
    first, _ = _preparer(client)
    arguments = {
        "actor_id": doctor.id,
        "practice_id": practice.id,
        "appointment_id": appointment.id,
        "idempotency_key": "desktop-stale-consent-001",
        "attestation": _attestation(),
    }

    with pytest.raises(asyncio.CancelledError):
        await first(**arguments)

    operation = CaptureOperation.first()
    assert operation is not None
    assert operation.state == "in_flight"
    assert operation.attempt_count == 1
    client.session_error = None
    reclaimed, _ = _preparer(client, current=NOW + timedelta(minutes=4))
    reference = await reclaimed(**arguments)

    assert len(client.consent_calls) == 2
    assert len(client.session_calls) == 2
    assert client.consent_calls[0][2] == client.consent_calls[1][2]
    assert client.session_calls[0][2] == client.session_calls[1][2]
    operation.refresh()
    assert operation.state == "succeeded"
    assert operation.attempt_count == 2
    local_session = LocalCaptureSession.get(id=reference.capture_session_id)
    assert local_session is not None
    assert local_session.processing_session_id == client.session_id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "settings",
    [
        ScribeWorkflowSettings(
            enabled=False,
            base_url="",
            scope="",
            managed_identity_client_id="",
            timeout_seconds=5,
        ),
        ScribeWorkflowSettings(
            enabled=True,
            base_url="http://not-private.example",
            scope="not-a-default-scope",
            managed_identity_client_id="",
            timeout_seconds=5,
        ),
    ],
)
async def test_disabled_or_incomplete_configuration_builds_no_client(
    settings: ScribeWorkflowSettings,
) -> None:
    doctor, practice, appointment = _scope(suffix="disabled")
    client_factory_calls: list[ScribeWorkflowSettings] = []

    def client_factory(value: ScribeWorkflowSettings) -> FakeScribeClient:
        client_factory_calls.append(value)
        return FakeScribeClient()

    preparer = build_scribe_capture_session_preparer(
        settings,
        client_factory=client_factory,
    )

    assert isinstance(preparer, DisabledScribeCaptureSessionPreparer)
    assert client_factory_calls == []
    with pytest.raises(CaptureLaunchUnavailable):
        await preparer(
            actor_id=doctor.id,
            practice_id=practice.id,
            appointment_id=appointment.id,
            idempotency_key="desktop-disabled-001",
            attestation=_attestation(),
        )
    assert LocalCaptureSession.first() is None
