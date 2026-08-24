from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, TypedDict, cast
from uuid import UUID, uuid4

import pytest
from typeid import TypeID

from app.configuration.desktop_capture import DesktopCaptureSettings
from app.services.capture_coordination import (
    CaptureCoordinationDenied,
    CaptureOutcomeUncertain,
    CaptureSessionPreparationReservation,
    ScribeCaptureAuthorization,
    authorize_segment_start,
    create_authorized_capture_session,
    finalize_capture_session_preparation,
    reserve_capture_session_preparation,
)
from app.services.capture_launch_auth import (
    AuthorizedCaptureSessionReference,
    issue_launch_ticket,
)
from app.services.capture_lifecycle import (
    CaptureLifecycleConflict,
    CaptureLifecycleDenied,
    purge_expired_capture_secrets,
    reconcile_never_started_leases,
    record_capture_heartbeat,
    release_expired_never_started_session,
    request_capture_stop,
)
from app.services.capture_provider import (
    CaptureObservation,
    CaptureProviderOutcomeUncertain,
    CaptureProviderRejected,
    CaptureProvisioning,
)

from activemodel.session_manager import get_session
from activemodel.types import TypeIDType
from app.models.capture_coordination import (
    CaptureEvent,
    CaptureLaunchAuthorization,
    CaptureOperation,
    CaptureSegment,
    CaptureSession,
    CaptureSourceLease,
)
from app.models.doctor import DoctorRole
from sqlmodel import select

from tests.factories import AppointmentFactory, DoctorFactory
from tests.routes.utils import provision_practice_in_force

NOW = datetime(2026, 8, 21, 16, 0, tzinfo=UTC)
CALLER = "desktop-lifecycle-bearer-0123456789abcdef"
CAPABILITY = "desktop-provider-capability-0123456789abcdef"


@dataclass(frozen=True)
class StartedScope:
    practice_id: TypeIDType
    actor_id: TypeIDType
    appointment_id: TypeIDType
    session_id: TypeIDType
    lease_id: TypeIDType
    consent_id: UUID
    capability_id: UUID
    capability_issued_at: datetime
    capability_expires_at: datetime


class LifecycleArguments(TypedDict):
    practice_id: TypeIDType
    actor_id: TypeIDType
    capture_session_id: TypeIDType
    source_lease_id: TypeIDType
    consent_evidence_id: UUID
    capability_id: UUID
    caller_principal: str
    idempotency_key: str


class FakeProvider:
    def create_capture(self, *, correlation_ref: str) -> CaptureProvisioning:
        del correlation_ref
        return CaptureProvisioning(
            capture_container_ref=f"container_{uuid4().hex}",
            capture_capability=CAPABILITY,
            status="pending",
        )

    def find_captures(self, *, correlation_ref: str) -> tuple[CaptureObservation, ...]:
        del correlation_ref
        return ()


def _prepared(*, now: datetime = NOW):
    doctor = DoctorFactory.build(
        email=f"lifecycle-{uuid4()}@example.com",
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
    prepared = create_authorized_capture_session(
        actor_id=doctor.id,
        practice_id=practice.id,
        appointment_id=appointment.id,
        idempotency_key=f"lifecycle-session-{uuid4().hex}",
        authorization=authorization,
        now=now,
    )
    return doctor, practice, appointment, authorization, prepared


def _started(*, now: datetime | None = None) -> StartedScope:
    started_at = now or datetime.now(UTC)
    doctor, practice, appointment, authorization, prepared = _prepared(now=started_at)
    authority = authorize_segment_start(
        provider=FakeProvider(),
        practice_id=practice.id,
        actor_id=doctor.id,
        capture_session_id=prepared.capture_session_id,
        source_lease_id=prepared.source_lease_id,
        consent_evidence_id=authorization.consent_evidence_id,
        caller_principal=CALLER,
        idempotency_key=f"lifecycle-start-{uuid4().hex}",
        now=started_at,
    )
    return StartedScope(
        practice_id=practice.id,
        actor_id=doctor.id,
        appointment_id=appointment.id,
        session_id=prepared.capture_session_id,
        lease_id=prepared.source_lease_id,
        consent_id=authorization.consent_evidence_id,
        capability_id=UUID(authority.capability_id),
        capability_issued_at=authority.issued_at,
        capability_expires_at=authority.expires_at,
    )


def _lifecycle_arguments(
    scope: StartedScope,
    *,
    key: str,
    caller: str = CALLER,
) -> LifecycleArguments:
    return {
        "practice_id": scope.practice_id,
        "actor_id": scope.actor_id,
        "capture_session_id": scope.session_id,
        "source_lease_id": scope.lease_id,
        "consent_evidence_id": scope.consent_id,
        "capability_id": scope.capability_id,
        "caller_principal": caller,
        "idempotency_key": key,
    }


def test_first_heartbeat_consumes_capability_purges_replay_and_refreshes_lease() -> (
    None
):
    scope = _started()
    heartbeat_at = scope.capability_issued_at + timedelta(seconds=1)
    arguments = _lifecycle_arguments(scope, key="lifecycle-heartbeat-0001")

    first = record_capture_heartbeat(**arguments, now=heartbeat_at)
    replay = record_capture_heartbeat(
        **arguments,
        now=heartbeat_at + timedelta(seconds=1),
    )

    with get_session() as db:
        capture_session = db.get(CaptureSession, scope.session_id)
        source_lease = db.get(CaptureSourceLease, scope.lease_id)
        segment = db.exec(
            select(CaptureSegment).where(
                CaptureSegment.capability_id == scope.capability_id
            )
        ).one()
        start_operation = db.exec(
            select(CaptureOperation).where(
                CaptureOperation.session_id == scope.session_id,
                CaptureOperation.operation == "start_capture_segment",
            )
        ).one()
        heartbeat_operations = db.exec(
            select(CaptureOperation).where(
                CaptureOperation.session_id == scope.session_id,
                CaptureOperation.operation == "record_capture_heartbeat",
            )
        ).all()

    assert first == replay
    assert first.as_wire_dict() == {
        "schemaVersion": 1,
        "decision": "accepted",
        "captureSessionId": str(scope.session_id),
        "capabilityId": str(scope.capability_id),
        "receiptId": str(heartbeat_operations[0].id),
        "sessionStatus": "recording",
    }
    assert capture_session is not None
    assert capture_session.state == "recording"
    assert capture_session.started_at == heartbeat_at
    assert capture_session.ended_at is None
    assert source_lease is not None
    assert source_lease.state == "active"
    assert source_lease.heartbeat_at == heartbeat_at
    assert source_lease.control_deadline_at == heartbeat_at + timedelta(minutes=2)
    assert segment.state == "capturing"
    assert segment.physical_started_at == heartbeat_at
    assert segment.capability_consumed_at == heartbeat_at
    assert segment.capability_revoked_at is None
    assert segment.encrypted_capability is None
    assert segment.capability_purged_at == heartbeat_at
    assert start_operation.encrypted_replay_response is None
    assert start_operation.replay_response_purged_at == heartbeat_at
    assert len(heartbeat_operations) == 1


def test_new_heartbeat_idempotency_key_refreshes_only_the_active_lease() -> None:
    scope = _started()
    record_capture_heartbeat(
        **_lifecycle_arguments(scope, key="lifecycle-heartbeat-first"),
        now=scope.capability_issued_at + timedelta(seconds=1),
    )
    refreshed_at = scope.capability_issued_at + timedelta(seconds=30)

    receipt = record_capture_heartbeat(
        **_lifecycle_arguments(scope, key="lifecycle-heartbeat-second"),
        now=refreshed_at,
    )

    with get_session() as db:
        capture_session = db.get(CaptureSession, scope.session_id)
        source_lease = db.get(CaptureSourceLease, scope.lease_id)
        segment = db.exec(
            select(CaptureSegment).where(
                CaptureSegment.capability_id == scope.capability_id
            )
        ).one()
    assert receipt.session_status == "recording"
    assert capture_session is not None
    assert capture_session.started_at == scope.capability_issued_at + timedelta(
        seconds=1
    )
    assert source_lease is not None
    assert source_lease.heartbeat_at == refreshed_at
    assert source_lease.control_deadline_at == refreshed_at + timedelta(minutes=2)
    assert segment.capability_consumed_at == scope.capability_issued_at + timedelta(
        seconds=1
    )
    assert segment.physical_started_at == scope.capability_issued_at + timedelta(
        seconds=1
    )


@pytest.mark.parametrize(
    "change",
    ["practice", "actor", "session", "lease", "consent", "capability", "caller"],
)
def test_heartbeat_rejects_every_mismatched_binding(change: str) -> None:
    scope = _started()
    arguments = cast(
        dict[str, Any],
        _lifecycle_arguments(scope, key=f"heartbeat-binding-{change}"),
    )
    replacements: dict[str, object] = {
        "practice": TypeID("practice"),
        "actor": TypeID("doctor"),
        "session": TypeID("capture_session"),
        "lease": TypeID("capture_source_lease"),
        "consent": uuid4(),
        "capability": uuid4(),
        "caller": "different-desktop-bearer-0123456789",
    }
    keys = {
        "practice": "practice_id",
        "actor": "actor_id",
        "session": "capture_session_id",
        "lease": "source_lease_id",
        "consent": "consent_evidence_id",
        "capability": "capability_id",
        "caller": "caller_principal",
    }
    arguments[keys[change]] = replacements[change]

    with pytest.raises(CaptureLifecycleDenied):
        record_capture_heartbeat(
            **arguments,
            now=scope.capability_issued_at + timedelta(seconds=1),
        )

    capture_session = CaptureSession.get(id=scope.session_id)
    assert capture_session is not None
    assert capture_session.state == "starting"


def test_expired_capability_cannot_record_first_heartbeat() -> None:
    scope = _started()
    expired_at = scope.capability_expires_at + timedelta(seconds=1)
    with get_session() as db:
        lease = db.get(CaptureSourceLease, scope.lease_id)
        assert lease is not None
        lease.control_deadline_at = expired_at + timedelta(minutes=1)
        db.add(lease)
        db.commit()

    with pytest.raises(CaptureLifecycleConflict):
        record_capture_heartbeat(
            **_lifecycle_arguments(scope, key="expired-heartbeat-0001"),
            now=expired_at,
        )

    with get_session() as db:
        segment = db.exec(
            select(CaptureSegment).where(
                CaptureSegment.capability_id == scope.capability_id
            )
        ).one()
    assert segment.state == "reserved"
    assert segment.capability_consumed_at is None
    assert segment.encrypted_capability is not None


def test_stop_before_heartbeat_revokes_secrets_without_claiming_stop_proof() -> None:
    scope = _started()
    stopped_at = scope.capability_issued_at + timedelta(seconds=1)

    receipt = request_capture_stop(
        **_lifecycle_arguments(scope, key="lifecycle-stop-before-start"),
        now=stopped_at,
    )

    with get_session() as db:
        capture_session = db.get(CaptureSession, scope.session_id)
        source_lease = db.get(CaptureSourceLease, scope.lease_id)
        segment = db.exec(
            select(CaptureSegment).where(
                CaptureSegment.capability_id == scope.capability_id
            )
        ).one()
        start_operation = db.exec(
            select(CaptureOperation).where(
                CaptureOperation.session_id == scope.session_id,
                CaptureOperation.operation == "start_capture_segment",
            )
        ).one()
    assert receipt.session_status == "stopping"
    assert receipt.capture_session_id == scope.session_id
    assert receipt.capability_id == scope.capability_id
    assert capture_session is not None
    assert capture_session.state == "stopping"
    assert capture_session.started_at is None
    assert capture_session.ended_at is None
    assert source_lease is not None
    assert source_lease.state == "closing"
    assert source_lease.stop_requested_at == stopped_at
    assert source_lease.stop_proved_at is None
    assert source_lease.never_started_proved_at is None
    assert source_lease.closed_at is None
    assert segment.state == "reserved"
    assert segment.capability_consumed_at is None
    assert segment.capability_revoked_at == stopped_at
    assert segment.encrypted_capability is None
    assert segment.capability_purged_at == stopped_at
    assert start_operation.encrypted_replay_response is None
    assert start_operation.replay_response_purged_at == stopped_at


def test_stop_requires_exact_capability_and_original_bearer_binding() -> None:
    scope = _started()
    stopped_at = scope.capability_issued_at + timedelta(seconds=1)
    with pytest.raises(CaptureLifecycleDenied):
        request_capture_stop(
            **_lifecycle_arguments(
                scope,
                key="stop-wrong-caller-0001",
                caller="different-desktop-bearer-0123456789",
            ),
            now=stopped_at,
        )
    wrong_capability = _lifecycle_arguments(scope, key="stop-wrong-capability-0001")
    wrong_capability["capability_id"] = uuid4()
    with pytest.raises(CaptureLifecycleDenied):
        request_capture_stop(**wrong_capability, now=stopped_at)

    capture_session = CaptureSession.get(id=scope.session_id)
    source_lease = CaptureSourceLease.get(id=scope.lease_id)
    assert capture_session is not None
    assert capture_session.state == "starting"
    assert source_lease is not None
    assert source_lease.state == "active"
    assert source_lease.stop_requested_at is None


def test_stop_after_heartbeat_preserves_consumption_and_blocks_new_heartbeat() -> None:
    scope = _started()
    heartbeat_at = scope.capability_issued_at + timedelta(seconds=1)
    stop_at = scope.capability_issued_at + timedelta(seconds=2)
    record_capture_heartbeat(
        **_lifecycle_arguments(scope, key="heartbeat-before-stop-0001"),
        now=heartbeat_at,
    )
    stop_arguments = _lifecycle_arguments(scope, key="stop-after-heartbeat-0001")

    first = request_capture_stop(**stop_arguments, now=stop_at)
    replay = request_capture_stop(
        **stop_arguments,
        now=stop_at + timedelta(seconds=1),
    )
    with pytest.raises(CaptureLifecycleConflict):
        record_capture_heartbeat(
            **_lifecycle_arguments(scope, key="heartbeat-after-stop-0001"),
            now=stop_at + timedelta(seconds=2),
        )

    with get_session() as db:
        capture_session = db.get(CaptureSession, scope.session_id)
        source_lease = db.get(CaptureSourceLease, scope.lease_id)
        segment = db.exec(
            select(CaptureSegment).where(
                CaptureSegment.capability_id == scope.capability_id
            )
        ).one()
    assert first == replay
    assert first.capture_session_id == scope.session_id
    assert first.capability_id == scope.capability_id
    assert capture_session is not None
    assert capture_session.state == "stopping"
    assert capture_session.ended_at is None
    assert source_lease is not None
    assert source_lease.state == "closing"
    assert source_lease.stop_proved_at is None
    assert source_lease.closed_at is None
    assert segment.capability_consumed_at == heartbeat_at
    assert segment.capability_revoked_at is None


def test_expired_local_secrets_are_purged_but_not_before_expiry() -> None:
    scope = _started()
    expires_at = scope.capability_expires_at

    before = purge_expired_capture_secrets(now=expires_at - timedelta(microseconds=1))
    due = purge_expired_capture_secrets(now=expires_at)

    with get_session() as db:
        segment = db.exec(
            select(CaptureSegment).where(
                CaptureSegment.capability_id == scope.capability_id
            )
        ).one()
        operation = db.exec(
            select(CaptureOperation).where(
                CaptureOperation.session_id == scope.session_id,
                CaptureOperation.operation == "start_capture_segment",
            )
        ).one()
    assert before.capabilities_purged == 0
    assert before.replay_responses_purged == 0
    assert due.capabilities_purged == 1
    assert due.replay_responses_purged == 1
    assert segment.encrypted_capability is None
    assert segment.capability_purged_at == expires_at
    assert segment.capability_digest is not None
    assert segment.capability_consumed_at is None
    assert segment.capability_revoked_at is None
    assert operation.encrypted_replay_response is None
    assert operation.replay_response_purged_at == expires_at
    assert operation.replay_response_digest is not None


def test_due_never_started_lease_is_closed_with_positive_local_proof() -> None:
    _, _, _, _, prepared = _prepared()

    result = reconcile_never_started_leases(now=NOW + timedelta(minutes=11))

    with get_session() as db:
        capture_session = db.get(CaptureSession, prepared.capture_session_id)
        source_lease = db.get(CaptureSourceLease, prepared.source_lease_id)
        events = db.exec(
            select(CaptureEvent).where(
                CaptureEvent.session_id == prepared.capture_session_id
            )
        ).all()
    assert result.closed == 1
    assert result.deferred == 0
    assert capture_session is not None
    assert capture_session.state == "cancelled"
    assert capture_session.started_at is None
    assert capture_session.ended_at is None
    assert source_lease is not None
    assert source_lease.state == "closed"
    assert source_lease.stop_requested_at == NOW + timedelta(minutes=11)
    assert source_lease.never_started_proved_at == NOW + timedelta(minutes=11)
    assert source_lease.stop_proved_at is None
    assert source_lease.closed_at == NOW + timedelta(minutes=11)
    assert events[-1].event_type == "capture.never_started_closed"


def test_never_started_reconciler_is_bounded() -> None:
    first = _prepared()[4]
    second = _prepared()[4]

    result = reconcile_never_started_leases(
        now=NOW + timedelta(minutes=11),
        limit=1,
    )

    leases = [
        CaptureSourceLease.get(id=first.source_lease_id),
        CaptureSourceLease.get(id=second.source_lease_id),
    ]
    assert result.examined == 1
    assert result.closed == 1
    assert (
        sum(lease is not None and lease.closed_at is not None for lease in leases) == 1
    )


def test_never_started_reconciler_does_not_close_when_any_segment_exists() -> None:
    _, _, appointment, _, prepared = _prepared()
    CaptureSegment(
        session_id=prepared.capture_session_id,
        practice_id=prepared.practice_id,
        actor_id=prepared.actor_id,
        appointment_id=appointment.id,
        ordinal=0,
        channel="mixed",
        encrypted_capability=None,
    ).save()

    result = reconcile_never_started_leases(now=NOW + timedelta(minutes=11))

    source_lease = CaptureSourceLease.get(id=prepared.source_lease_id)
    assert result.examined == 1
    assert result.closed == 0
    assert result.deferred == 1
    assert source_lease is not None
    assert source_lease.closed_at is None
    assert source_lease.never_started_proved_at is None


def test_never_started_reconciler_closes_deterministically_rejected_start() -> None:
    doctor, practice, _, authorization, prepared = _prepared()

    class RejectedProvider(FakeProvider):
        def create_capture(self, *, correlation_ref: str) -> CaptureProvisioning:
            del correlation_ref
            raise CaptureProviderRejected("bounded")

    with pytest.raises(CaptureCoordinationDenied):
        authorize_segment_start(
            provider=RejectedProvider(),
            practice_id=practice.id,
            actor_id=doctor.id,
            capture_session_id=prepared.capture_session_id,
            source_lease_id=prepared.source_lease_id,
            consent_evidence_id=authorization.consent_evidence_id,
            caller_principal=CALLER,
            idempotency_key="lifecycle-deterministic-reject-0001",
            now=NOW + timedelta(seconds=5),
        )

    result = reconcile_never_started_leases(now=NOW + timedelta(minutes=3))
    source_lease = CaptureSourceLease.get(id=prepared.source_lease_id)
    capture_session = CaptureSession.get(id=prepared.capture_session_id)
    with get_session() as db:
        operation = db.exec(
            select(CaptureOperation).where(
                CaptureOperation.operation == "start_capture_segment"
            )
        ).one()
    assert result.closed == 1
    assert result.deferred == 0
    assert source_lease is not None
    assert source_lease.state == "closed"
    assert source_lease.never_started_proved_at == NOW + timedelta(minutes=3)
    assert capture_session is not None
    assert capture_session.state == "cancelled"
    assert operation.state == "failed"
    assert operation.encrypted_external_correlation_ref is None
    assert operation.correlation_purged_at == NOW + timedelta(minutes=3)


def test_never_started_reconciler_defers_uncertain_start() -> None:
    doctor, practice, _, authorization, prepared = _prepared()

    class UncertainProvider(FakeProvider):
        def create_capture(self, *, correlation_ref: str) -> CaptureProvisioning:
            del correlation_ref
            raise CaptureProviderOutcomeUncertain("bounded")

    with pytest.raises(CaptureOutcomeUncertain):
        authorize_segment_start(
            provider=UncertainProvider(),
            practice_id=practice.id,
            actor_id=doctor.id,
            capture_session_id=prepared.capture_session_id,
            source_lease_id=prepared.source_lease_id,
            consent_evidence_id=authorization.consent_evidence_id,
            caller_principal=CALLER,
            idempotency_key="lifecycle-uncertain-start-0001",
            now=NOW + timedelta(seconds=5),
        )

    result = reconcile_never_started_leases(now=NOW + timedelta(minutes=3))
    source_lease = CaptureSourceLease.get(id=prepared.source_lease_id)
    assert result.closed == 0
    assert result.deferred == 1
    assert source_lease is not None
    assert source_lease.closed_at is None
    assert source_lease.never_started_proved_at is None


def test_never_started_reconciler_defers_uncertain_preparation() -> None:
    doctor = DoctorFactory.build(
        email=f"uncertain-preparation-{uuid4()}@example.com",
        role=DoctorRole.normal,
        deleted_at=None,
    ).save()
    practice = provision_practice_in_force(doctor)
    appointment = (
        cast(Any, AppointmentFactory)
        .build(
            doctor_id=doctor.id,
            cancelled_at=None,
        )
        .save()
    )
    reservation = reserve_capture_session_preparation(
        actor_id=doctor.id,
        practice_id=practice.id,
        appointment_id=appointment.id,
        patient_id=appointment.patient_id,
        idempotency_key="uncertain-preparation-0001",
        request_binding_digest="b" * 64,
        now=NOW,
    )
    assert isinstance(reservation, CaptureSessionPreparationReservation)

    result = reconcile_never_started_leases(now=NOW + timedelta(minutes=11))

    source_lease = CaptureSourceLease.get(id=reservation.source_lease_id)
    assert result.examined == 1
    assert result.closed == 0
    assert result.deferred == 1
    assert source_lease is not None
    assert source_lease.closed_at is None
    assert source_lease.never_started_proved_at is None


def test_expired_pre_pkce_ticket_can_release_only_exact_never_started_session() -> None:
    doctor = DoctorFactory.build(
        email=f"expired-ticket-{uuid4()}@example.com",
        role=DoctorRole.normal,
        deleted_at=None,
    ).save()
    practice = provision_practice_in_force(doctor)
    appointment = (
        cast(Any, AppointmentFactory)
        .build(
            doctor_id=doctor.id,
            cancelled_at=None,
        )
        .save()
    )
    request_binding_digest = "c" * 64
    reservation = reserve_capture_session_preparation(
        actor_id=doctor.id,
        practice_id=practice.id,
        appointment_id=appointment.id,
        patient_id=appointment.patient_id,
        idempotency_key="pre-pkce-reservation-0001",
        request_binding_digest=request_binding_digest,
        now=NOW,
    )
    assert isinstance(reservation, CaptureSessionPreparationReservation)
    authorization = ScribeCaptureAuthorization(
        consent_evidence_id=uuid4(),
        consent_policy_version="consent-v1",
        processing_session_id=uuid4(),
    )
    prepared = finalize_capture_session_preparation(
        reservation=reservation,
        authorization=authorization,
        now=NOW + timedelta(seconds=1),
    )
    reference = AuthorizedCaptureSessionReference(
        capture_session_id=prepared.capture_session_id,
        practice_id=prepared.practice_id,
        actor_id=prepared.actor_id,
        appointment_id=prepared.appointment_id,
        consent_evidence_id=prepared.consent_evidence_id,
        source_lease_id=prepared.source_lease_id,
        state_version=prepared.state_version,
    )
    settings = DesktopCaptureSettings(
        enabled=True,
        provider_create_enabled=False,
        recovery_enabled=False,
        redirect_uris=("cosmaneura-capture://oauth/callback",),
    )
    issued = issue_launch_ticket(
        reference=reference,
        actor_binding="browser-actor-binding",
        idempotency_key="pre-pkce-ticket-0001",
        settings=settings,
        now=NOW + timedelta(seconds=2),
    )
    later_ticket = issue_launch_ticket(
        reference=reference,
        actor_binding="browser-actor-binding",
        idempotency_key="pre-pkce-ticket-0002",
        settings=settings,
        now=NOW + timedelta(minutes=1),
    )

    assert not release_expired_never_started_session(
        practice_id=practice.id,
        actor_id=doctor.id,
        appointment_id=appointment.id,
        request_binding_digest=request_binding_digest,
        now=issued.expires_at - timedelta(microseconds=1),
    )
    assert not release_expired_never_started_session(
        practice_id=practice.id,
        actor_id=doctor.id,
        appointment_id=appointment.id,
        request_binding_digest=request_binding_digest,
        now=issued.expires_at,
    )
    assert release_expired_never_started_session(
        practice_id=practice.id,
        actor_id=doctor.id,
        appointment_id=appointment.id,
        request_binding_digest=request_binding_digest,
        now=later_ticket.expires_at,
    )

    capture_session = CaptureSession.get(id=prepared.capture_session_id)
    source_lease = CaptureSourceLease.get(id=prepared.source_lease_id)
    stored_authorizations = list(CaptureLaunchAuthorization.all())
    assert capture_session is not None
    assert capture_session.state == "cancelled"
    assert source_lease is not None
    assert source_lease.state == "closed"
    assert source_lease.never_started_proved_at == later_ticket.expires_at
    assert {authorization.state for authorization in stored_authorizations} == {
        "expired"
    }
