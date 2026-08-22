from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

import app.services.capture_coordination as capture_coordination_service
from app.services.capture_coordination import (
    CaptureCoordinationConflict,
    CaptureCoordinationDenied,
    CaptureOutcomeUncertain,
    CaptureSessionPreparationReservation,
    ScribeCaptureAuthorization,
    authorize_segment_start,
    create_authorized_capture_session,
    finalize_capture_session_preparation,
    reconcile_segment_start,
    reserve_capture_session_preparation,
)
from app.services.capture_lifecycle import record_capture_heartbeat
from app.services.capture_provider import (
    CaptureObservation,
    CaptureProviderOutcomeUncertain,
    CaptureProviderRejected,
    CaptureProvisioning,
)
from app.utils.encryption import decrypt_value

from activemodel.session_manager import get_session
from app.models.appointment import Appointment
from app.models.capture_coordination import (
    CaptureEvent,
    CaptureExternalResource,
    CaptureOperation,
    CaptureSegment,
    CaptureSession,
    CaptureSourceLease,
)
from app.models.doctor import DoctorRole
from sqlmodel import select

from tests.factories import AppointmentFactory, DoctorFactory
from tests.routes.utils import provision_practice_in_force

NOW = datetime(2026, 8, 21, 20, 0, tzinfo=UTC)


@dataclass
class FakeProvider:
    create_result: CaptureProvisioning | Exception
    observations: tuple[CaptureObservation, ...] = ()
    create_refs: list[str] = field(default_factory=list)
    find_refs: list[str] = field(default_factory=list)

    def create_capture(self, *, correlation_ref: str) -> CaptureProvisioning:
        self.create_refs.append(correlation_ref)
        if isinstance(self.create_result, Exception):
            raise self.create_result
        return self.create_result

    def find_captures(self, *, correlation_ref: str) -> tuple[CaptureObservation, ...]:
        self.find_refs.append(correlation_ref)
        return self.observations


def _scope():
    doctor = DoctorFactory.build(
        email=f"capture-{uuid4()}@example.com",
        role=DoctorRole.normal,
        deleted_at=None,
    ).save()
    practice = provision_practice_in_force(doctor)
    appointment = AppointmentFactory.build(
        doctor_id=doctor.id,
        cancelled_at=None,
    ).save()
    authorization = ScribeCaptureAuthorization(
        consent_evidence_id=uuid4(),
        consent_policy_version="consent-v1",
        processing_session_id=uuid4(),
    )
    return doctor, practice, appointment, authorization


def _prepare(*, idempotency_key: str = "create-session-key-0001"):
    doctor, practice, appointment, authorization = _scope()
    prepared = create_authorized_capture_session(
        actor_id=doctor.id,
        practice_id=practice.id,
        appointment_id=appointment.id,
        idempotency_key=idempotency_key,
        authorization=authorization,
        now=NOW,
    )
    return doctor, practice, appointment, authorization, prepared


def _success() -> CaptureProvisioning:
    return CaptureProvisioning(
        capture_container_ref="upload_123",
        capture_capability="sdk_upload_token_0123456789",
        status="pending",
    )


def test_create_session_persists_exact_scope_consent_and_exclusive_lease() -> None:
    doctor, practice, appointment, authorization, prepared = _prepare()

    with get_session() as db:
        capture_session = db.exec(
            select(CaptureSession).where(
                CaptureSession.id == prepared.capture_session_id
            )
        ).one()
        lease = db.exec(
            select(CaptureSourceLease).where(
                CaptureSourceLease.id == prepared.source_lease_id
            )
        ).one()
        operation = db.exec(
            select(CaptureOperation).where(
                CaptureOperation.session_id == capture_session.id,
                CaptureOperation.operation == "create_capture_session",
            )
        ).one()
        events = db.exec(
            select(CaptureEvent).where(CaptureEvent.session_id == capture_session.id)
        ).all()

    assert capture_session.practice_id == practice.id
    assert capture_session.actor_id == doctor.id
    assert capture_session.appointment_id == appointment.id
    assert (
        capture_session.scribe_consent_receipt_id == authorization.consent_evidence_id
    )
    assert capture_session.processing_session_id == authorization.processing_session_id
    assert capture_session.consent_state == "granted"
    assert capture_session.state == "ready"
    assert lease.source_kind == "desktop_companion"
    assert lease.closed_at is None
    assert operation.state == "succeeded"
    assert [event.event_type for event in events] == ["capture.session_authorized"]


def test_stale_pre_scribe_reservation_can_be_reclaimed_by_same_request() -> None:
    doctor, practice, appointment, _ = _scope()
    arguments = {
        "actor_id": doctor.id,
        "practice_id": practice.id,
        "appointment_id": appointment.id,
        "patient_id": appointment.patient_id,
        "idempotency_key": "pre-scribe-worker-death-001",
        "request_binding_digest": "a" * 64,
    }

    first = reserve_capture_session_preparation(**arguments, now=NOW)
    assert isinstance(first, CaptureSessionPreparationReservation)
    assert first.attempt_generation == 1
    with pytest.raises(CaptureOutcomeUncertain):
        reserve_capture_session_preparation(
            **arguments,
            now=NOW + timedelta(minutes=1),
        )

    reclaimed = reserve_capture_session_preparation(
        **arguments,
        now=NOW + timedelta(minutes=4),
    )
    assert isinstance(reclaimed, CaptureSessionPreparationReservation)
    assert reclaimed.capture_session_id == first.capture_session_id
    assert reclaimed.source_lease_id == first.source_lease_id
    assert reclaimed.attempt_generation == 2
    with get_session() as db:
        operation = db.exec(
            select(CaptureOperation).where(CaptureOperation.id == first.operation_id)
        ).one()
    assert operation.state == "in_flight"
    assert operation.attempt_count == 2


def test_stale_preparation_completion_cannot_overwrite_reclaimed_attempt() -> None:
    doctor, practice, appointment, _ = _scope()
    arguments = {
        "actor_id": doctor.id,
        "practice_id": practice.id,
        "appointment_id": appointment.id,
        "patient_id": appointment.patient_id,
        "idempotency_key": "pre-scribe-stale-completion-001",
        "request_binding_digest": "b" * 64,
    }
    first = reserve_capture_session_preparation(**arguments, now=NOW)
    assert isinstance(first, CaptureSessionPreparationReservation)
    reclaimed = reserve_capture_session_preparation(
        **arguments,
        now=NOW + timedelta(minutes=4),
    )
    assert isinstance(reclaimed, CaptureSessionPreparationReservation)
    winning_authorization = ScribeCaptureAuthorization(
        consent_evidence_id=uuid4(),
        consent_policy_version="consent-v2",
        processing_session_id=uuid4(),
    )
    finalized = finalize_capture_session_preparation(
        reservation=reclaimed,
        authorization=winning_authorization,
        now=NOW + timedelta(minutes=4, seconds=1),
    )

    with pytest.raises(CaptureOutcomeUncertain):
        finalize_capture_session_preparation(
            reservation=first,
            authorization=ScribeCaptureAuthorization(
                consent_evidence_id=uuid4(),
                consent_policy_version="consent-v1",
                processing_session_id=uuid4(),
            ),
            now=NOW + timedelta(minutes=4, seconds=2),
        )

    capture_session = CaptureSession.get(id=finalized.capture_session_id)
    assert capture_session is not None
    assert (
        capture_session.scribe_consent_receipt_id
        == winning_authorization.consent_evidence_id
    )
    assert (
        capture_session.processing_session_id
        == winning_authorization.processing_session_id
    )


def test_create_session_idempotently_replays_without_duplicate_lease() -> None:
    doctor, practice, appointment, authorization, first = _prepare()
    replay = create_authorized_capture_session(
        actor_id=doctor.id,
        practice_id=practice.id,
        appointment_id=appointment.id,
        idempotency_key="create-session-key-0001",
        authorization=authorization,
        now=NOW + timedelta(seconds=1),
    )

    assert replay == first
    with get_session() as db:
        assert len(db.exec(select(CaptureSession)).all()) == 1
        assert len(db.exec(select(CaptureSourceLease)).all()) == 1


def test_create_session_same_key_different_authority_conflicts() -> None:
    doctor, practice, appointment, authorization, _ = _prepare()

    with pytest.raises(CaptureCoordinationConflict):
        create_authorized_capture_session(
            actor_id=doctor.id,
            practice_id=practice.id,
            appointment_id=appointment.id,
            idempotency_key="create-session-key-0001",
            authorization=ScribeCaptureAuthorization(
                consent_evidence_id=authorization.consent_evidence_id,
                consent_policy_version=authorization.consent_policy_version,
                processing_session_id=uuid4(),
            ),
            now=NOW,
        )


def test_create_session_fails_closed_for_cancelled_appointment() -> None:
    doctor, practice, appointment, authorization = _scope()
    appointment.cancelled_at = NOW
    appointment.save()

    with pytest.raises(CaptureCoordinationDenied):
        create_authorized_capture_session(
            actor_id=doctor.id,
            practice_id=practice.id,
            appointment_id=appointment.id,
            idempotency_key="cancelled-appointment-key",
            authorization=authorization,
            now=NOW,
        )

    assert CaptureSession.first() is None


def test_second_source_claim_for_same_appointment_is_rejected() -> None:
    doctor, practice, appointment, _, _ = _prepare()

    with pytest.raises(CaptureCoordinationConflict):
        create_authorized_capture_session(
            actor_id=doctor.id,
            practice_id=practice.id,
            appointment_id=appointment.id,
            idempotency_key="second-session-key-0002",
            authorization=ScribeCaptureAuthorization(
                consent_evidence_id=uuid4(),
                consent_policy_version="consent-v1",
                processing_session_id=uuid4(),
            ),
            now=NOW + timedelta(seconds=1),
        )


def test_start_commits_receipt_before_provider_and_returns_exact_wire_contract() -> (
    None
):
    doctor, practice, _, authorization, prepared = _prepare()

    class CommitObservingProvider(FakeProvider):
        def create_capture(self, *, correlation_ref: str) -> CaptureProvisioning:
            with get_session() as db:
                operation = db.exec(
                    select(CaptureOperation).where(
                        CaptureOperation.operation == "start_capture_segment"
                    )
                ).one()
                segment = db.exec(
                    select(CaptureSegment).where(
                        CaptureSegment.id == operation.segment_id
                    )
                ).one()
            assert operation.state == "in_flight"
            assert operation.lease_expires_at is not None
            assert operation.next_attempt_at == operation.lease_expires_at
            assert segment.state == "reserved"
            return super().create_capture(correlation_ref=correlation_ref)

    provider = CommitObservingProvider(_success())
    authority = authorize_segment_start(
        provider=provider,
        practice_id=practice.id,
        actor_id=doctor.id,
        capture_session_id=prepared.capture_session_id,
        source_lease_id=prepared.source_lease_id,
        consent_evidence_id=authorization.consent_evidence_id,
        caller_principal="desktop-token-0123456789",
        idempotency_key="segment-start-key-0001",
        now=NOW + timedelta(seconds=5),
    )

    assert len(provider.create_refs) == 1
    assert set(authority.as_wire_dict()) == {
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
    assert authority.as_wire_dict()["captureCapability"] == {
        "capabilityId": authority.capability_id,
        "secret": "sdk_upload_token_0123456789",
        "expiresAt": authority.expires_at.isoformat().replace("+00:00", "Z"),
    }
    with get_session() as db:
        segment = db.exec(select(CaptureSegment)).one()
        resources = db.exec(select(CaptureExternalResource)).all()
        operation = db.exec(
            select(CaptureOperation).where(
                CaptureOperation.operation == "start_capture_segment"
            )
        ).one()
    assert segment.encrypted_capability != authority.capability_secret
    assert (
        decrypt_value(segment.encrypted_capability or "") == authority.capability_secret
    )
    assert operation.encrypted_external_correlation_ref is None
    assert operation.correlation_purged_at is not None
    assert len(resources) == 1
    assert resources[0].encrypted_external_ref != "upload_123"
    assert decrypt_value(resources[0].encrypted_external_ref or "") == "upload_123"


def test_successful_start_idempotency_replays_without_second_provider_call() -> None:
    doctor, practice, _, authorization, prepared = _prepare()
    provider = FakeProvider(_success())
    arguments = {
        "provider": provider,
        "practice_id": practice.id,
        "actor_id": doctor.id,
        "capture_session_id": prepared.capture_session_id,
        "source_lease_id": prepared.source_lease_id,
        "consent_evidence_id": authorization.consent_evidence_id,
        "caller_principal": "desktop-token-0123456789",
        "idempotency_key": "segment-start-key-0001",
    }

    first = authorize_segment_start(**arguments, now=NOW + timedelta(seconds=5))
    second = authorize_segment_start(**arguments, now=NOW + timedelta(seconds=6))

    assert first == second
    assert len(provider.create_refs) == 1


def test_successful_start_replay_revokes_secret_after_appointment_cancellation() -> (
    None
):
    doctor, practice, appointment, authorization, prepared = _prepare()
    provider = FakeProvider(_success())
    arguments = {
        "provider": provider,
        "practice_id": practice.id,
        "actor_id": doctor.id,
        "capture_session_id": prepared.capture_session_id,
        "source_lease_id": prepared.source_lease_id,
        "consent_evidence_id": authorization.consent_evidence_id,
        "caller_principal": "desktop-token-0123456789",
        "idempotency_key": "segment-start-replay-cancelled-0001",
    }
    authorize_segment_start(**arguments, now=NOW + timedelta(seconds=5))
    appointment.cancelled_at = NOW + timedelta(seconds=6)
    appointment.save()

    with pytest.raises(
        CaptureCoordinationDenied,
        match="capture_start_replay_authority_changed",
    ):
        authorize_segment_start(**arguments, now=NOW + timedelta(seconds=7))

    with get_session() as db:
        operation = db.exec(
            select(CaptureOperation).where(
                CaptureOperation.operation == "start_capture_segment"
            )
        ).one()
        segment = db.get(CaptureSegment, operation.segment_id)
        capture_session = db.get(CaptureSession, prepared.capture_session_id)
        source_lease = db.get(CaptureSourceLease, prepared.source_lease_id)
        resources = db.exec(select(CaptureExternalResource)).all()
    assert len(provider.create_refs) == 1
    assert operation.encrypted_replay_response is None
    assert operation.replay_response_purged_at == NOW + timedelta(seconds=7)
    assert segment is not None
    assert segment.encrypted_capability is None
    assert segment.capability_revoked_at == NOW + timedelta(seconds=7)
    assert segment.capability_purged_at == NOW + timedelta(seconds=7)
    assert capture_session is not None
    assert capture_session.state == "review_required"
    assert source_lease is not None
    assert source_lease.state == "uncertain"
    assert len(resources) == 1
    assert decrypt_value(resources[0].encrypted_external_ref or "") == "upload_123"


def test_successful_start_replay_revokes_secret_after_consent_revocation() -> None:
    doctor, practice, _, authorization, prepared = _prepare()
    provider = FakeProvider(_success())
    arguments = {
        "provider": provider,
        "practice_id": practice.id,
        "actor_id": doctor.id,
        "capture_session_id": prepared.capture_session_id,
        "source_lease_id": prepared.source_lease_id,
        "consent_evidence_id": authorization.consent_evidence_id,
        "caller_principal": "desktop-token-0123456789",
        "idempotency_key": "segment-start-replay-revoked-0001",
    }
    authorize_segment_start(**arguments, now=NOW + timedelta(seconds=5))
    capture_session = CaptureSession.get(id=prepared.capture_session_id)
    assert capture_session is not None
    capture_session.state = "review_required"
    capture_session.consent_state = "revoked"
    capture_session.save()

    with pytest.raises(
        CaptureCoordinationDenied,
        match="capture_start_replay_authority_changed",
    ):
        authorize_segment_start(**arguments, now=NOW + timedelta(seconds=7))

    with get_session() as db:
        operation = db.exec(
            select(CaptureOperation).where(
                CaptureOperation.operation == "start_capture_segment"
            )
        ).one()
        segment = db.get(CaptureSegment, operation.segment_id)
    assert len(provider.create_refs) == 1
    assert operation.encrypted_replay_response is None
    assert segment is not None
    assert segment.encrypted_capability is None
    assert segment.capability_revoked_at == NOW + timedelta(seconds=7)


def test_successful_start_replay_revokes_secret_after_lease_expiry() -> None:
    doctor, practice, _, authorization, prepared = _prepare()
    provider = FakeProvider(_success())
    arguments = {
        "provider": provider,
        "practice_id": practice.id,
        "actor_id": doctor.id,
        "capture_session_id": prepared.capture_session_id,
        "source_lease_id": prepared.source_lease_id,
        "consent_evidence_id": authorization.consent_evidence_id,
        "caller_principal": "desktop-token-0123456789",
        "idempotency_key": "segment-start-replay-expired-0001",
    }
    authorize_segment_start(**arguments, now=NOW + timedelta(seconds=5))

    with pytest.raises(
        CaptureCoordinationDenied,
        match="capture_start_replay_authority_changed",
    ):
        authorize_segment_start(**arguments, now=NOW + timedelta(seconds=126))

    with get_session() as db:
        operation = db.exec(
            select(CaptureOperation).where(
                CaptureOperation.operation == "start_capture_segment"
            )
        ).one()
        segment = db.get(CaptureSegment, operation.segment_id)
    assert len(provider.create_refs) == 1
    assert operation.encrypted_replay_response is None
    assert segment is not None
    assert segment.encrypted_capability is None
    assert segment.capability_revoked_at == NOW + timedelta(seconds=126)


def test_successful_start_replay_after_heartbeat_is_conflict_without_rewrite() -> None:
    doctor, practice, _, authorization, prepared = _prepare()
    provider = FakeProvider(_success())
    caller = "desktop-token-0123456789"
    arguments = {
        "provider": provider,
        "practice_id": practice.id,
        "actor_id": doctor.id,
        "capture_session_id": prepared.capture_session_id,
        "source_lease_id": prepared.source_lease_id,
        "consent_evidence_id": authorization.consent_evidence_id,
        "caller_principal": caller,
        "idempotency_key": "segment-start-replay-consumed-0001",
    }
    authority = authorize_segment_start(
        **arguments,
        now=NOW + timedelta(seconds=5),
    )
    record_capture_heartbeat(
        practice_id=practice.id,
        actor_id=doctor.id,
        capture_session_id=prepared.capture_session_id,
        source_lease_id=prepared.source_lease_id,
        consent_evidence_id=authorization.consent_evidence_id,
        capability_id=UUID(authority.capability_id),
        caller_principal=caller,
        idempotency_key="capture-heartbeat-consumes-0001",
        now=NOW + timedelta(seconds=6),
    )
    with get_session() as db:
        before_session = db.get(CaptureSession, prepared.capture_session_id)
        before_lease = db.get(CaptureSourceLease, prepared.source_lease_id)
        operation = db.exec(
            select(CaptureOperation).where(
                CaptureOperation.operation == "start_capture_segment"
            )
        ).one()
        segment = db.get(CaptureSegment, operation.segment_id)
        assert before_session is not None
        assert before_lease is not None
        assert segment is not None
        before_state = (
            before_session.state,
            before_session.state_version,
            before_lease.state,
            before_lease.control_deadline_at,
            segment.state,
            segment.state_version,
            segment.capability_consumed_at,
            segment.capability_purged_at,
        )

    with pytest.raises(
        CaptureCoordinationConflict,
        match="capture_start_not_replayable",
    ):
        authorize_segment_start(**arguments, now=NOW + timedelta(seconds=7))

    with get_session() as db:
        after_session = db.get(CaptureSession, prepared.capture_session_id)
        after_lease = db.get(CaptureSourceLease, prepared.source_lease_id)
        after_segment = db.get(CaptureSegment, operation.segment_id)
        assert after_session is not None
        assert after_lease is not None
        assert after_segment is not None
        after_state = (
            after_session.state,
            after_session.state_version,
            after_lease.state,
            after_lease.control_deadline_at,
            after_segment.state,
            after_segment.state_version,
            after_segment.capability_consumed_at,
            after_segment.capability_purged_at,
        )
    assert len(provider.create_refs) == 1
    assert after_state == before_state


def test_concurrent_finalize_revalidates_succeeded_replay_before_returning() -> None:
    doctor, practice, appointment, authorization, prepared = _prepare()

    class ConcurrentFinalizingProvider(FakeProvider):
        def create_capture(self, *, correlation_ref: str) -> CaptureProvisioning:
            with get_session() as db:
                operation = db.exec(
                    select(CaptureOperation).where(
                        CaptureOperation.operation == "start_capture_segment"
                    )
                ).one()
            capture_coordination_service._finalize_segment_start(
                operation_id=operation.id,
                result=_success(),
                adapter_profile="desktop-audio-v1",
                expected_attempt=operation.attempt_count,
                current=NOW + timedelta(seconds=5),
            )
            current_appointment = Appointment.get(id=appointment.id)
            assert current_appointment is not None
            current_appointment.cancelled_at = NOW + timedelta(seconds=6)
            current_appointment.save()
            return super().create_capture(correlation_ref=correlation_ref)

    provider = ConcurrentFinalizingProvider(_success())
    with pytest.raises(
        CaptureCoordinationDenied,
        match="capture_start_replay_authority_changed",
    ):
        authorize_segment_start(
            provider=provider,
            practice_id=practice.id,
            actor_id=doctor.id,
            capture_session_id=prepared.capture_session_id,
            source_lease_id=prepared.source_lease_id,
            consent_evidence_id=authorization.consent_evidence_id,
            caller_principal="desktop-token-0123456789",
            idempotency_key="segment-start-concurrent-finalize-0001",
            now=NOW + timedelta(seconds=5),
        )

    with get_session() as db:
        operation = db.exec(
            select(CaptureOperation).where(
                CaptureOperation.operation == "start_capture_segment"
            )
        ).one()
        segment = db.get(CaptureSegment, operation.segment_id)
        resources = db.exec(select(CaptureExternalResource)).all()
    assert len(provider.create_refs) == 1
    assert operation.encrypted_replay_response is None
    assert segment is not None
    assert segment.encrypted_capability is None
    assert segment.capability_revoked_at == NOW + timedelta(seconds=5)
    assert len(resources) == 1


def test_reconciliation_does_not_replay_a_concurrently_succeeded_operation() -> None:
    doctor, practice, _, authorization, prepared = _prepare()
    provider = FakeProvider(_success())
    authorize_segment_start(
        provider=provider,
        practice_id=practice.id,
        actor_id=doctor.id,
        capture_session_id=prepared.capture_session_id,
        source_lease_id=prepared.source_lease_id,
        consent_evidence_id=authorization.consent_evidence_id,
        caller_principal="desktop-token-0123456789",
        idempotency_key="segment-start-reconcile-succeeded-0001",
        now=NOW + timedelta(seconds=5),
    )
    with get_session() as db:
        operation = db.exec(
            select(CaptureOperation).where(
                CaptureOperation.operation == "start_capture_segment"
            )
        ).one()

    with pytest.raises(
        CaptureCoordinationConflict,
        match="capture_start_not_replayable",
    ):
        reconcile_segment_start(
            provider=provider,
            operation_id=operation.id,
            now=NOW + timedelta(seconds=6),
        )
    assert provider.find_refs == []


def test_provider_result_is_quarantined_if_appointment_is_cancelled_during_call():
    doctor, practice, appointment, authorization, prepared = _prepare()

    class CancellingProvider(FakeProvider):
        def create_capture(self, *, correlation_ref: str) -> CaptureProvisioning:
            current_appointment = Appointment.get(id=appointment.id)
            assert current_appointment is not None
            current_appointment.cancelled_at = NOW + timedelta(seconds=6)
            current_appointment.save()
            return super().create_capture(correlation_ref=correlation_ref)

    provider = CancellingProvider(_success())
    with pytest.raises(
        CaptureCoordinationDenied,
        match="capture_authority_changed_after_provider",
    ):
        authorize_segment_start(
            provider=provider,
            practice_id=practice.id,
            actor_id=doctor.id,
            capture_session_id=prepared.capture_session_id,
            source_lease_id=prepared.source_lease_id,
            consent_evidence_id=authorization.consent_evidence_id,
            caller_principal="desktop-token-0123456789",
            idempotency_key="segment-start-cancel-race-0001",
            now=NOW + timedelta(seconds=5),
        )

    with get_session() as db:
        operation = db.exec(
            select(CaptureOperation).where(
                CaptureOperation.operation == "start_capture_segment"
            )
        ).one()
        segment = db.get(CaptureSegment, operation.segment_id)
        capture_session = db.get(CaptureSession, prepared.capture_session_id)
        source_lease = db.get(CaptureSourceLease, prepared.source_lease_id)
        resources = db.exec(select(CaptureExternalResource)).all()
    assert operation.state == "failed"
    assert operation.last_error_code == "capture_authority_changed_after_provider"
    assert operation.encrypted_external_correlation_ref is None
    assert operation.correlation_purged_at is not None
    assert segment is not None
    assert segment.state == "failed"
    assert segment.capability_id is None
    assert segment.encrypted_capability is None
    assert capture_session is not None
    assert capture_session.state == "review_required"
    assert source_lease is not None
    assert source_lease.state == "uncertain"
    assert len(resources) == 1
    assert decrypt_value(resources[0].encrypted_external_ref or "") == "upload_123"


def test_uncertain_create_is_never_replayed_and_reconciles_one_match() -> None:
    doctor, practice, _, authorization, prepared = _prepare()
    provider = FakeProvider(CaptureProviderOutcomeUncertain("bounded"))
    arguments = {
        "provider": provider,
        "practice_id": practice.id,
        "actor_id": doctor.id,
        "capture_session_id": prepared.capture_session_id,
        "source_lease_id": prepared.source_lease_id,
        "consent_evidence_id": authorization.consent_evidence_id,
        "caller_principal": "desktop-token-0123456789",
        "idempotency_key": "segment-start-key-0001",
    }
    with pytest.raises(CaptureOutcomeUncertain):
        authorize_segment_start(**arguments, now=NOW + timedelta(seconds=5))
    with pytest.raises(CaptureOutcomeUncertain):
        authorize_segment_start(**arguments, now=NOW + timedelta(seconds=6))
    assert len(provider.create_refs) == 1

    with get_session() as db:
        operation = db.exec(
            select(CaptureOperation).where(
                CaptureOperation.operation == "start_capture_segment"
            )
        ).one()
    provider.observations = (
        CaptureObservation(
            capture_container_ref="upload_recovered",
            capture_capability="sdk_upload_token_recovered_012345",
            status="pending",
        ),
    )
    authority = reconcile_segment_start(
        provider=provider,
        operation_id=operation.id,
        now=NOW + timedelta(seconds=35),
    )
    assert authority.capability_secret == "sdk_upload_token_recovered_012345"
    assert len(provider.find_refs) == 1
    assert provider.find_refs[0] == provider.create_refs[0]
    with get_session() as db:
        recovered_session = db.exec(
            select(CaptureSession).where(
                CaptureSession.id == prepared.capture_session_id
            )
        ).one()
        recovered_lease = db.exec(
            select(CaptureSourceLease).where(
                CaptureSourceLease.id == prepared.source_lease_id
            )
        ).one()
        recovered_operation = db.exec(
            select(CaptureOperation).where(CaptureOperation.id == operation.id)
        ).one()
    assert recovered_session.state == "starting"
    assert recovered_lease.state == "active"
    assert recovered_operation.lease_expires_at is None
    assert recovered_operation.next_attempt_at is None


def test_reconciliation_quarantines_result_if_consent_is_revoked_during_lookup():
    doctor, practice, _, authorization, prepared = _prepare()
    provider = FakeProvider(CaptureProviderOutcomeUncertain("bounded"))
    with pytest.raises(CaptureOutcomeUncertain):
        authorize_segment_start(
            provider=provider,
            practice_id=practice.id,
            actor_id=doctor.id,
            capture_session_id=prepared.capture_session_id,
            source_lease_id=prepared.source_lease_id,
            consent_evidence_id=authorization.consent_evidence_id,
            caller_principal="desktop-token-0123456789",
            idempotency_key="segment-start-consent-race-0001",
            now=NOW + timedelta(seconds=5),
        )
    with get_session() as db:
        operation = db.exec(
            select(CaptureOperation).where(
                CaptureOperation.operation == "start_capture_segment"
            )
        ).one()

    class RevokingLookupProvider(FakeProvider):
        def find_captures(
            self, *, correlation_ref: str
        ) -> tuple[CaptureObservation, ...]:
            current_session = CaptureSession.get(id=prepared.capture_session_id)
            assert current_session is not None
            current_session.state = "review_required"
            current_session.consent_state = "revoked"
            current_session.save()
            return super().find_captures(correlation_ref=correlation_ref)

    recovery = RevokingLookupProvider(
        _success(),
        observations=(
            CaptureObservation(
                capture_container_ref="upload_recovered_after_revocation",
                capture_capability="sdk_upload_token_revoked_012345",
                status="pending",
            ),
        ),
    )
    with pytest.raises(
        CaptureCoordinationDenied,
        match="capture_authority_changed_after_provider",
    ):
        reconcile_segment_start(
            provider=recovery,
            operation_id=operation.id,
            now=NOW + timedelta(seconds=35),
        )

    with get_session() as db:
        failed_operation = db.get(CaptureOperation, operation.id)
        segment = db.get(CaptureSegment, operation.segment_id)
        capture_session = db.get(CaptureSession, prepared.capture_session_id)
        source_lease = db.get(CaptureSourceLease, prepared.source_lease_id)
        resources = db.exec(select(CaptureExternalResource)).all()
    assert failed_operation is not None
    assert failed_operation.state == "failed"
    assert (
        failed_operation.last_error_code == "capture_authority_changed_after_provider"
    )
    assert segment is not None
    assert segment.capability_id is None
    assert segment.encrypted_capability is None
    assert capture_session is not None
    assert capture_session.state == "review_required"
    assert capture_session.consent_state == "revoked"
    assert source_lease is not None
    assert source_lease.state == "uncertain"
    assert len(resources) == 1
    assert (
        decrypt_value(resources[0].encrypted_external_ref or "")
        == "upload_recovered_after_revocation"
    )


def test_stale_in_flight_create_is_mapped_but_cannot_mint_after_lease_expiry() -> None:
    doctor, practice, _, authorization, prepared = _prepare()

    class WorkerExitProvider(FakeProvider):
        def create_capture(self, *, correlation_ref: str) -> CaptureProvisioning:
            self.create_refs.append(correlation_ref)
            raise SystemExit("synthetic worker exit")

    provider = WorkerExitProvider(_success())
    arguments = {
        "provider": provider,
        "practice_id": practice.id,
        "actor_id": doctor.id,
        "capture_session_id": prepared.capture_session_id,
        "source_lease_id": prepared.source_lease_id,
        "consent_evidence_id": authorization.consent_evidence_id,
        "caller_principal": "desktop-token-0123456789",
        "idempotency_key": "segment-start-worker-exit-001",
    }
    with pytest.raises(SystemExit, match="synthetic worker exit"):
        authorize_segment_start(**arguments, now=NOW + timedelta(seconds=5))

    with get_session() as db:
        operation = db.exec(
            select(CaptureOperation).where(
                CaptureOperation.operation == "start_capture_segment"
            )
        ).one()
        assert operation.state == "in_flight"
        assert operation.lease_expires_at == NOW + timedelta(seconds=185)

    provider.observations = (
        CaptureObservation(
            capture_container_ref="upload_after_worker_exit",
            capture_capability="sdk_upload_token_after_worker_exit_012345",
            status="pending",
        ),
    )
    with pytest.raises(CaptureOutcomeUncertain):
        reconcile_segment_start(
            provider=provider,
            operation_id=operation.id,
            now=NOW + timedelta(seconds=184),
        )
    with pytest.raises(
        CaptureCoordinationDenied,
        match="capture_authority_changed_after_provider",
    ):
        reconcile_segment_start(
            provider=provider,
            operation_id=operation.id,
            now=NOW + timedelta(seconds=185),
        )

    assert len(provider.create_refs) == 1
    assert len(provider.find_refs) == 1
    with get_session() as db:
        failed_operation = db.get(CaptureOperation, operation.id)
        segment = db.get(CaptureSegment, operation.segment_id)
        resources = db.exec(select(CaptureExternalResource)).all()
    assert failed_operation is not None
    assert (
        failed_operation.last_error_code == "capture_authority_changed_after_provider"
    )
    assert segment is not None
    assert segment.capability_id is None
    assert segment.encrypted_capability is None
    assert len(resources) == 1


def test_reconciliation_resamples_clock_after_slow_provider_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    doctor, practice, _, authorization, prepared = _prepare()
    provider = FakeProvider(CaptureProviderOutcomeUncertain("bounded"))
    with pytest.raises(CaptureOutcomeUncertain):
        authorize_segment_start(
            provider=provider,
            practice_id=practice.id,
            actor_id=doctor.id,
            capture_session_id=prepared.capture_session_id,
            source_lease_id=prepared.source_lease_id,
            consent_evidence_id=authorization.consent_evidence_id,
            caller_principal="desktop-token-0123456789",
            idempotency_key="segment-start-slow-recovery-0001",
            now=NOW + timedelta(seconds=5),
        )
    with get_session() as db:
        operation = db.exec(
            select(CaptureOperation).where(
                CaptureOperation.operation == "start_capture_segment"
            )
        ).one()

    provider.observations = (
        CaptureObservation(
            capture_container_ref="upload_after_slow_lookup",
            capture_capability="sdk_upload_token_after_slow_lookup_012345",
            status="pending",
        ),
    )
    sampled_times = iter(
        (
            NOW + timedelta(seconds=35),
            NOW + timedelta(seconds=216),
        )
    )
    monkeypatch.setattr(
        "app.services.capture_coordination._now",
        lambda _supplied: next(sampled_times),
    )
    with pytest.raises(
        CaptureCoordinationDenied,
        match="capture_authority_changed_after_provider",
    ):
        reconcile_segment_start(provider=provider, operation_id=operation.id)

    with get_session() as db:
        failed_operation = db.get(CaptureOperation, operation.id)
        segment = db.get(CaptureSegment, operation.segment_id)
        source_lease = db.get(CaptureSourceLease, prepared.source_lease_id)
        resources = db.exec(select(CaptureExternalResource)).all()
    assert failed_operation is not None
    assert (
        failed_operation.last_error_code == "capture_authority_changed_after_provider"
    )
    assert segment is not None
    assert segment.capability_id is None
    assert segment.encrypted_capability is None
    assert source_lease is not None
    assert source_lease.state == "uncertain"
    assert len(resources) == 1


def test_reconciliation_with_multiple_matches_requires_review() -> None:
    doctor, practice, _, authorization, prepared = _prepare()
    provider = FakeProvider(CaptureProviderOutcomeUncertain("bounded"))
    with pytest.raises(CaptureOutcomeUncertain):
        authorize_segment_start(
            provider=provider,
            practice_id=practice.id,
            actor_id=doctor.id,
            capture_session_id=prepared.capture_session_id,
            source_lease_id=prepared.source_lease_id,
            consent_evidence_id=authorization.consent_evidence_id,
            caller_principal="desktop-token-0123456789",
            idempotency_key="segment-start-key-0001",
            now=NOW + timedelta(seconds=5),
        )
    with get_session() as db:
        operation = db.exec(
            select(CaptureOperation).where(
                CaptureOperation.operation == "start_capture_segment"
            )
        ).one()
    provider.observations = (
        CaptureObservation("upload_1", "sdk_upload_token_1111111111", "pending"),
        CaptureObservation("upload_2", "sdk_upload_token_2222222222", "pending"),
    )

    with pytest.raises(CaptureCoordinationConflict, match="review"):
        reconcile_segment_start(
            provider=provider,
            operation_id=operation.id,
            now=NOW + timedelta(seconds=35),
        )
    with get_session() as db:
        capture_session = db.exec(
            select(CaptureSession).where(
                CaptureSession.id == prepared.capture_session_id
            )
        ).one()
        lease = db.exec(
            select(CaptureSourceLease).where(
                CaptureSourceLease.id == prepared.source_lease_id
            )
        ).one()
    assert capture_session.state == "review_required"
    assert lease.state == "uncertain"


def test_provider_rejection_has_no_external_mapping_and_allows_new_attempt() -> None:
    doctor, practice, _, authorization, prepared = _prepare()
    rejected = FakeProvider(CaptureProviderRejected("bounded"))
    with pytest.raises(CaptureCoordinationDenied):
        authorize_segment_start(
            provider=rejected,
            practice_id=practice.id,
            actor_id=doctor.id,
            capture_session_id=prepared.capture_session_id,
            source_lease_id=prepared.source_lease_id,
            consent_evidence_id=authorization.consent_evidence_id,
            caller_principal="desktop-token-0123456789",
            idempotency_key="segment-start-key-0001",
            now=NOW + timedelta(seconds=5),
        )
    succeeding = FakeProvider(_success())
    authority = authorize_segment_start(
        provider=succeeding,
        practice_id=practice.id,
        actor_id=doctor.id,
        capture_session_id=prepared.capture_session_id,
        source_lease_id=prepared.source_lease_id,
        consent_evidence_id=authorization.consent_evidence_id,
        caller_principal="desktop-token-0123456789",
        idempotency_key="segment-start-key-0002",
        now=NOW + timedelta(seconds=6),
    )
    assert authority.capability_secret == _success().capture_capability


def test_cancelled_appointment_blocks_start_before_provider_call() -> None:
    doctor, practice, appointment, authorization, prepared = _prepare()
    appointment = Appointment.get(id=appointment.id)
    assert appointment is not None
    appointment.cancelled_at = NOW + timedelta(seconds=1)
    appointment.save()
    provider = FakeProvider(_success())

    with pytest.raises(CaptureCoordinationDenied):
        authorize_segment_start(
            provider=provider,
            practice_id=practice.id,
            actor_id=doctor.id,
            capture_session_id=prepared.capture_session_id,
            source_lease_id=prepared.source_lease_id,
            consent_evidence_id=authorization.consent_evidence_id,
            caller_principal="desktop-token-0123456789",
            idempotency_key="segment-start-key-0001",
            now=NOW + timedelta(seconds=5),
        )
    assert provider.create_refs == []
