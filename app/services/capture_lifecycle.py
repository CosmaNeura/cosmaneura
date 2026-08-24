"""Proof-conservative lifecycle transitions for desktop capture.

These operations consume only server-derived authority and durable capability
identifiers.  A desktop heartbeat can prove that an issued local capability
was used, but a desktop stop request cannot prove that a provider resource is
absent or that capture has stopped.
"""

from __future__ import annotations

import hmac
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, cast
from uuid import UUID

from app.services.capture_coordination import (
    ACTIVE_LEASE_WINDOW,
    CREATE_SESSION_OPERATION,
    DESKTOP_CALLER_SCOPE,
    DESKTOP_SOURCE,
    START_SEGMENT_OPERATION,
    capture_session_preparation_request_digest,
)
from app.utils.encryption import pseudonymous_digest

from activemodel.session_manager import get_session
from activemodel.types import TypeIDType
from app.models.appointment import Appointment
from app.models.capture_coordination import (
    CaptureEvent,
    CaptureExternalResource,
    CaptureLaunchAuthorization,
    CaptureOperation,
    CaptureSegment,
    CaptureSession,
    CaptureSourceLease,
)
from app.models.doctor import Doctor, DoctorRole
from app.models.practice import Practice, PracticeMember
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlmodel import Session, select

HEARTBEAT_OPERATION = "record_capture_heartbeat"
STOP_REQUEST_OPERATION = "request_capture_stop"
MAX_LOCAL_SECRET_PURGE_BATCH = 100
MAX_NEVER_STARTED_SCAN_BATCH = 50
_PROVEN_NO_START_ERROR_CODES = frozenset({"provider_disabled", "provider_rejected"})

_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9._~-]{8,255}$")


class CaptureLifecycleError(RuntimeError):
    """A bounded lifecycle failure whose code contains no request data."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class CaptureLifecycleDenied(CaptureLifecycleError):
    pass


class CaptureLifecycleConflict(CaptureLifecycleError):
    pass


class CaptureLifecycleUnavailable(CaptureLifecycleError):
    pass


@dataclass(frozen=True)
class CaptureLifecycleReceipt:
    capture_session_id: TypeIDType
    capability_id: UUID
    receipt_id: TypeIDType
    session_status: Literal["recording", "stopping"]

    def as_wire_dict(self) -> dict[str, int | str]:
        return {
            "schemaVersion": 1,
            "decision": "accepted",
            "captureSessionId": str(self.capture_session_id),
            "capabilityId": str(self.capability_id),
            "receiptId": str(self.receipt_id),
            "sessionStatus": self.session_status,
        }


@dataclass(frozen=True)
class LocalSecretPurgeResult:
    capabilities_purged: int
    replay_responses_purged: int


@dataclass(frozen=True)
class NeverStartedReconciliationResult:
    examined: int
    closed: int
    deferred: int


def _now(value: datetime | None) -> datetime:
    current = value or datetime.now(UTC)
    if current.tzinfo is None:
        raise ValueError("capture lifecycle clock must be timezone-aware")
    return current.astimezone(UTC)


def _digest(value: str, *, context: str) -> str:
    return pseudonymous_digest(value, context=context)


def _validate_request(
    *, caller_principal: str, idempotency_key: str
) -> tuple[str, str]:
    if (
        not caller_principal
        or len(caller_principal) > 4096
        or _IDEMPOTENCY_KEY.fullmatch(idempotency_key) is None
    ):
        raise CaptureLifecycleDenied("capture_lifecycle_not_authorized")
    return (
        _digest(caller_principal, context="capture-desktop-caller-v1"),
        _digest(idempotency_key, context="capture-lifecycle-idempotency-v1"),
    )


def _request_digest(
    *,
    operation: str,
    practice_id: TypeIDType,
    actor_id: TypeIDType,
    capture_session_id: TypeIDType,
    source_lease_id: TypeIDType,
    consent_evidence_id: UUID,
    capability_id: UUID,
) -> str:
    canonical = json.dumps(
        {
            "actor_id": str(actor_id),
            "capability_id": str(capability_id),
            "capture_session_id": str(capture_session_id),
            "consent_evidence_id": str(consent_evidence_id),
            "operation": operation,
            "practice_id": str(practice_id),
            "source_lease_id": str(source_lease_id),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return _digest(canonical, context="capture-lifecycle-request-v1")


def _scope_values(capture_session: CaptureSession) -> dict[str, Any]:
    return {
        "session_id": capture_session.id,
        "practice_id": capture_session.practice_id,
        "actor_id": capture_session.actor_id,
        "appointment_id": capture_session.appointment_id,
    }


def _append_event(
    db: Session,
    capture_session: CaptureSession,
    *,
    event_type: str,
    segment_id: TypeIDType | None,
    operation_id: TypeIDType | None,
    recorded_at: datetime,
) -> None:
    last_sequence = db.exec(
        select(func.max(CaptureEvent.local_sequence)).where(
            CaptureEvent.session_id == capture_session.id
        )
    ).one()
    db.add(
        CaptureEvent(
            **_scope_values(capture_session),
            segment_id=segment_id,
            operation_id=operation_id,
            local_sequence=0 if last_sequence is None else int(last_sequence) + 1,
            event_type=event_type,
            state_version=capture_session.state_version,
            session_state=capture_session.state,
            recorded_at=recorded_at,
        )
    )


def _existing_operation(
    db: Session,
    *,
    practice_id: TypeIDType,
    operation: str,
    idempotency_digest: str,
) -> CaptureOperation | None:
    return db.exec(
        select(CaptureOperation)
        .where(
            CaptureOperation.practice_id == practice_id,
            CaptureOperation.caller_scope == DESKTOP_CALLER_SCOPE,
            CaptureOperation.operation == operation,
            CaptureOperation.idempotency_key_digest == idempotency_digest,
        )
        .with_for_update()
    ).one_or_none()


def _existing_receipt(
    operation: CaptureOperation,
    *,
    expected_operation: str,
    request_digest: str,
    caller_digest: str,
    practice_id: TypeIDType,
    actor_id: TypeIDType,
    capture_session_id: TypeIDType,
    segment_id: TypeIDType,
    capability_id: UUID,
) -> CaptureLifecycleReceipt:
    if (
        operation.operation != expected_operation
        or operation.practice_id != practice_id
        or operation.actor_id != actor_id
        or operation.session_id != capture_session_id
        or operation.segment_id != segment_id
        or not hmac.compare_digest(operation.request_digest, request_digest)
        or not hmac.compare_digest(
            operation.caller_principal_digest,
            caller_digest,
        )
        or operation.state != "succeeded"
    ):
        raise CaptureLifecycleConflict("capture_lifecycle_request_conflict")
    return CaptureLifecycleReceipt(
        capture_session_id=capture_session_id,
        capability_id=capability_id,
        receipt_id=operation.id,
        session_status=(
            "recording" if expected_operation == HEARTBEAT_OPERATION else "stopping"
        ),
    )


def _load_heartbeat_scope(
    db: Session,
    *,
    practice_id: TypeIDType,
    actor_id: TypeIDType,
    capture_session_id: TypeIDType,
    source_lease_id: TypeIDType,
    consent_evidence_id: UUID,
    capability_id: UUID,
    caller_digest: str,
    current: datetime,
) -> tuple[CaptureSession, CaptureSourceLease, CaptureSegment, CaptureOperation]:
    doctor = db.exec(
        select(Doctor)
        .where(
            Doctor.id == actor_id,
            Doctor.role == DoctorRole.normal,
            cast(Any, Doctor.deleted_at).is_(None),
        )
        .with_for_update()
    ).one_or_none()
    membership = db.exec(
        select(PracticeMember)
        .where(
            PracticeMember.practice_id == practice_id,
            PracticeMember.doctor_id == actor_id,
            cast(Any, PracticeMember.attested_at).is_not(None),
        )
        .with_for_update()
    ).one_or_none()
    practice = db.exec(
        select(Practice).where(Practice.id == practice_id).with_for_update()
    ).one_or_none()
    capture_session = db.exec(
        select(CaptureSession)
        .where(
            CaptureSession.id == capture_session_id,
            CaptureSession.practice_id == practice_id,
            CaptureSession.actor_id == actor_id,
            CaptureSession.source_kind == DESKTOP_SOURCE,
            cast(Any, CaptureSession.state).in_(("starting", "recording", "stopping")),
            CaptureSession.consent_state == "granted",
            CaptureSession.scribe_consent_receipt_id == consent_evidence_id,
            CaptureSession.authority_origin == "server_authorized",
        )
        .with_for_update()
    ).one_or_none()
    appointment_id = (
        capture_session.appointment_id if capture_session is not None else None
    )
    appointment = db.exec(
        select(Appointment)
        .where(
            Appointment.id == appointment_id,
            Appointment.doctor_id == actor_id,
            cast(Any, Appointment.cancelled_at).is_(None),
        )
        .with_for_update()
    ).one_or_none()
    source_lease = db.exec(
        select(CaptureSourceLease)
        .where(
            CaptureSourceLease.id == source_lease_id,
            CaptureSourceLease.session_id == capture_session_id,
            CaptureSourceLease.practice_id == practice_id,
            CaptureSourceLease.actor_id == actor_id,
            CaptureSourceLease.appointment_id == appointment_id,
            CaptureSourceLease.source_kind == DESKTOP_SOURCE,
            cast(Any, CaptureSourceLease.state).in_(("active", "closing")),
            cast(Any, CaptureSourceLease.closed_at).is_(None),
            CaptureSourceLease.control_deadline_at > current,
        )
        .with_for_update()
    ).one_or_none()
    segment = db.exec(
        select(CaptureSegment)
        .where(
            CaptureSegment.capability_id == capability_id,
            CaptureSegment.session_id == capture_session_id,
            CaptureSegment.practice_id == practice_id,
            CaptureSegment.actor_id == actor_id,
            CaptureSegment.appointment_id == appointment_id,
        )
        .with_for_update()
    ).one_or_none()
    start_operation = (
        None
        if segment is None
        else db.exec(
            select(CaptureOperation)
            .where(
                CaptureOperation.session_id == capture_session_id,
                CaptureOperation.practice_id == practice_id,
                CaptureOperation.actor_id == actor_id,
                CaptureOperation.appointment_id == appointment_id,
                CaptureOperation.segment_id == segment.id,
                CaptureOperation.caller_scope == DESKTOP_CALLER_SCOPE,
                CaptureOperation.operation == START_SEGMENT_OPERATION,
                CaptureOperation.state == "succeeded",
            )
            .with_for_update()
        ).one_or_none()
    )
    if (
        doctor is None
        or membership is None
        or practice is None
        or not practice.baa_in_force()
        or not practice.entitled()
        or appointment is None
        or capture_session is None
        or source_lease is None
        or segment is None
        or start_operation is None
        or not hmac.compare_digest(
            start_operation.caller_principal_digest,
            caller_digest,
        )
    ):
        raise CaptureLifecycleDenied("capture_lifecycle_not_authorized")
    return capture_session, source_lease, segment, start_operation


def _load_stop_scope(
    db: Session,
    *,
    practice_id: TypeIDType,
    actor_id: TypeIDType,
    capture_session_id: TypeIDType,
    source_lease_id: TypeIDType,
    consent_evidence_id: UUID,
    capability_id: UUID,
    caller_digest: str,
) -> tuple[CaptureSession, CaptureSourceLease, CaptureSegment, CaptureOperation]:
    """Load only immutable/exact bindings for the stop-only safety action."""

    capture_session = db.exec(
        select(CaptureSession)
        .where(
            CaptureSession.id == capture_session_id,
            CaptureSession.practice_id == practice_id,
            CaptureSession.actor_id == actor_id,
            CaptureSession.source_kind == DESKTOP_SOURCE,
            cast(Any, CaptureSession.state).in_(
                (
                    "starting",
                    "recording",
                    "stopping",
                    "paused",
                    "review_required",
                    "failed",
                )
            ),
            cast(Any, CaptureSession.ended_at).is_(None),
            CaptureSession.scribe_consent_receipt_id == consent_evidence_id,
            CaptureSession.authority_origin == "server_authorized",
        )
        .with_for_update()
    ).one_or_none()
    appointment_id = (
        capture_session.appointment_id if capture_session is not None else None
    )
    source_lease = db.exec(
        select(CaptureSourceLease)
        .where(
            CaptureSourceLease.id == source_lease_id,
            CaptureSourceLease.session_id == capture_session_id,
            CaptureSourceLease.practice_id == practice_id,
            CaptureSourceLease.actor_id == actor_id,
            CaptureSourceLease.appointment_id == appointment_id,
            CaptureSourceLease.source_kind == DESKTOP_SOURCE,
            cast(Any, CaptureSourceLease.state).in_(("active", "uncertain", "closing")),
            cast(Any, CaptureSourceLease.closed_at).is_(None),
        )
        .with_for_update()
    ).one_or_none()
    segment = db.exec(
        select(CaptureSegment)
        .where(
            CaptureSegment.capability_id == capability_id,
            CaptureSegment.session_id == capture_session_id,
            CaptureSegment.practice_id == practice_id,
            CaptureSegment.actor_id == actor_id,
            CaptureSegment.appointment_id == appointment_id,
        )
        .with_for_update()
    ).one_or_none()
    start_operation = (
        None
        if segment is None
        else db.exec(
            select(CaptureOperation)
            .where(
                CaptureOperation.session_id == capture_session_id,
                CaptureOperation.practice_id == practice_id,
                CaptureOperation.actor_id == actor_id,
                CaptureOperation.appointment_id == appointment_id,
                CaptureOperation.segment_id == segment.id,
                CaptureOperation.caller_scope == DESKTOP_CALLER_SCOPE,
                CaptureOperation.operation == START_SEGMENT_OPERATION,
                CaptureOperation.state == "succeeded",
            )
            .with_for_update()
        ).one_or_none()
    )
    if (
        capture_session is None
        or source_lease is None
        or segment is None
        or start_operation is None
        or not hmac.compare_digest(
            start_operation.caller_principal_digest,
            caller_digest,
        )
    ):
        raise CaptureLifecycleDenied("capture_lifecycle_not_authorized")
    return capture_session, source_lease, segment, start_operation


def _purge_start_replay(operation: CaptureOperation, *, current: datetime) -> None:
    if operation.encrypted_replay_response is not None:
        operation.encrypted_replay_response = None
        operation.replay_response_purged_at = current


def _new_operation(
    *,
    capture_session: CaptureSession,
    segment: CaptureSegment,
    operation: str,
    idempotency_digest: str,
    request_digest: str,
    caller_digest: str,
    current: datetime,
) -> CaptureOperation:
    return CaptureOperation(
        **_scope_values(capture_session),
        segment_id=segment.id,
        caller_scope=DESKTOP_CALLER_SCOPE,
        caller_principal_digest=caller_digest,
        operation=operation,
        idempotency_key_digest=idempotency_digest,
        request_digest=request_digest,
        state="succeeded",
        attempt_count=1,
        max_attempts=1,
        last_attempt_at=current,
        completed_at=current,
    )


def _record_lifecycle_operation(
    *,
    operation_name: Literal["record_capture_heartbeat", "request_capture_stop"],
    practice_id: TypeIDType,
    actor_id: TypeIDType,
    capture_session_id: TypeIDType,
    source_lease_id: TypeIDType,
    consent_evidence_id: UUID,
    capability_id: UUID,
    caller_principal: str,
    idempotency_key: str,
    current: datetime,
) -> CaptureLifecycleReceipt:
    caller_digest, idempotency_digest = _validate_request(
        caller_principal=caller_principal,
        idempotency_key=idempotency_key,
    )
    request_digest = _request_digest(
        operation=operation_name,
        practice_id=practice_id,
        actor_id=actor_id,
        capture_session_id=capture_session_id,
        source_lease_id=source_lease_id,
        consent_evidence_id=consent_evidence_id,
        capability_id=capability_id,
    )

    with get_session() as db:
        try:
            existing = _existing_operation(
                db,
                practice_id=practice_id,
                operation=operation_name,
                idempotency_digest=idempotency_digest,
            )
            if existing is not None:
                if existing.segment_id is None:
                    raise CaptureLifecycleConflict("capture_lifecycle_request_conflict")
                return _existing_receipt(
                    existing,
                    expected_operation=operation_name,
                    request_digest=request_digest,
                    caller_digest=caller_digest,
                    practice_id=practice_id,
                    actor_id=actor_id,
                    capture_session_id=capture_session_id,
                    segment_id=existing.segment_id,
                    capability_id=capability_id,
                )

            if operation_name == HEARTBEAT_OPERATION:
                capture_session, source_lease, segment, start_operation = (
                    _load_heartbeat_scope(
                        db,
                        practice_id=practice_id,
                        actor_id=actor_id,
                        capture_session_id=capture_session_id,
                        source_lease_id=source_lease_id,
                        consent_evidence_id=consent_evidence_id,
                        capability_id=capability_id,
                        caller_digest=caller_digest,
                        current=current,
                    )
                )
            else:
                capture_session, source_lease, segment, start_operation = (
                    _load_stop_scope(
                        db,
                        practice_id=practice_id,
                        actor_id=actor_id,
                        capture_session_id=capture_session_id,
                        source_lease_id=source_lease_id,
                        consent_evidence_id=consent_evidence_id,
                        capability_id=capability_id,
                        caller_digest=caller_digest,
                    )
                )
            existing = _existing_operation(
                db,
                practice_id=practice_id,
                operation=operation_name,
                idempotency_digest=idempotency_digest,
            )
            if existing is not None:
                return _existing_receipt(
                    existing,
                    expected_operation=operation_name,
                    request_digest=request_digest,
                    caller_digest=caller_digest,
                    practice_id=practice_id,
                    actor_id=actor_id,
                    capture_session_id=capture_session_id,
                    segment_id=segment.id,
                    capability_id=capability_id,
                )
            if operation_name == HEARTBEAT_OPERATION and (
                source_lease.heartbeat_at is not None
                and source_lease.heartbeat_at > current
            ):
                raise CaptureLifecycleConflict("capture_lifecycle_clock_conflict")

            if operation_name == HEARTBEAT_OPERATION:
                if source_lease.state != "active" or capture_session.state not in {
                    "starting",
                    "recording",
                }:
                    raise CaptureLifecycleConflict("capture_heartbeat_conflict")
                if capture_session.state == "starting":
                    if (
                        segment.state != "reserved"
                        or segment.physical_started_at is not None
                        or segment.capability_issued_at is None
                        or segment.capability_expires_at is None
                        or segment.capability_issued_at > current
                        or segment.capability_expires_at <= current
                        or segment.capability_consumed_at is not None
                        or segment.capability_revoked_at is not None
                        or segment.capability_purged_at is not None
                        or segment.encrypted_capability is None
                    ):
                        raise CaptureLifecycleConflict("capture_heartbeat_conflict")
                    segment.capability_consumed_at = current
                    segment.encrypted_capability = None
                    segment.capability_purged_at = current
                    segment.physical_started_at = current
                    segment.state = "capturing"
                    segment.state_version += 1
                    capture_session.state = "recording"
                    capture_session.started_at = current
                    capture_session.state_version += 1
                elif (
                    segment.state != "capturing"
                    or segment.physical_started_at is None
                    or segment.capability_consumed_at is None
                    or segment.capability_revoked_at is not None
                    or segment.encrypted_capability is not None
                    or segment.capability_purged_at is None
                ):
                    raise CaptureLifecycleConflict("capture_heartbeat_conflict")
                session_status: Literal["recording", "stopping"] = "recording"
                event_type = "capture.heartbeat_recorded"
            else:
                if capture_session.state not in {
                    "starting",
                    "recording",
                    "stopping",
                    "paused",
                    "review_required",
                    "failed",
                } or source_lease.state not in {"active", "uncertain", "closing"}:
                    raise CaptureLifecycleConflict("capture_stop_conflict")
                if segment.capability_consumed_at is None:
                    if segment.capability_revoked_at is None:
                        segment.capability_revoked_at = current
                    if segment.state != "reserved":
                        raise CaptureLifecycleConflict("capture_stop_conflict")
                elif (
                    segment.capability_revoked_at is not None
                    or segment.state != "capturing"
                    or segment.physical_started_at is None
                ):
                    raise CaptureLifecycleConflict("capture_stop_conflict")
                if segment.encrypted_capability is not None:
                    segment.encrypted_capability = None
                    segment.capability_purged_at = current
                elif segment.capability_purged_at is None:
                    raise CaptureLifecycleConflict("capture_stop_conflict")
                if capture_session.state != "stopping":
                    capture_session.state = "stopping"
                    capture_session.state_version += 1
                source_lease.state = "closing"
                if source_lease.stop_requested_at is None:
                    source_lease.stop_requested_at = current
                session_status = "stopping"
                event_type = "capture.stop_requested"

            _purge_start_replay(start_operation, current=current)
            if operation_name == HEARTBEAT_OPERATION:
                source_lease.heartbeat_at = current
                source_lease.control_deadline_at = current + ACTIVE_LEASE_WINDOW
            operation = _new_operation(
                capture_session=capture_session,
                segment=segment,
                operation=operation_name,
                idempotency_digest=idempotency_digest,
                request_digest=request_digest,
                caller_digest=caller_digest,
                current=current,
            )
            db.add(segment)
            db.add(start_operation)
            db.add(capture_session)
            db.add(source_lease)
            db.add(operation)
            db.flush()
            _append_event(
                db,
                capture_session,
                event_type=event_type,
                segment_id=segment.id,
                operation_id=operation.id,
                recorded_at=current,
            )
            db.commit()
            return CaptureLifecycleReceipt(
                capture_session_id=capture_session.id,
                capability_id=capability_id,
                receipt_id=operation.id,
                session_status=session_status,
            )
        except CaptureLifecycleError:
            db.rollback()
            raise
        except IntegrityError:
            db.rollback()
            existing = _existing_operation(
                db,
                practice_id=practice_id,
                operation=operation_name,
                idempotency_digest=idempotency_digest,
            )
            if existing is None or existing.segment_id is None:
                raise CaptureLifecycleConflict(
                    "capture_lifecycle_request_conflict"
                ) from None
            return _existing_receipt(
                existing,
                expected_operation=operation_name,
                request_digest=request_digest,
                caller_digest=caller_digest,
                practice_id=practice_id,
                actor_id=actor_id,
                capture_session_id=capture_session_id,
                segment_id=existing.segment_id,
                capability_id=capability_id,
            )
        except SQLAlchemyError:
            db.rollback()
            raise CaptureLifecycleUnavailable("capture_lifecycle_unavailable") from None


def record_capture_heartbeat(
    *,
    practice_id: TypeIDType,
    actor_id: TypeIDType,
    capture_session_id: TypeIDType,
    source_lease_id: TypeIDType,
    consent_evidence_id: UUID,
    capability_id: UUID,
    caller_principal: str,
    idempotency_key: str,
    now: datetime | None = None,
) -> CaptureLifecycleReceipt:
    """Consume one start capability and record/refresh an active heartbeat."""

    return _record_lifecycle_operation(
        operation_name=HEARTBEAT_OPERATION,
        practice_id=practice_id,
        actor_id=actor_id,
        capture_session_id=capture_session_id,
        source_lease_id=source_lease_id,
        consent_evidence_id=consent_evidence_id,
        capability_id=capability_id,
        caller_principal=caller_principal,
        idempotency_key=idempotency_key,
        current=_now(now),
    )


def request_capture_stop(
    *,
    practice_id: TypeIDType,
    actor_id: TypeIDType,
    capture_session_id: TypeIDType,
    source_lease_id: TypeIDType,
    consent_evidence_id: UUID,
    capability_id: UUID,
    caller_principal: str,
    idempotency_key: str,
    now: datetime | None = None,
) -> CaptureLifecycleReceipt:
    """Record a stop request without claiming provider stop or session closure."""

    return _record_lifecycle_operation(
        operation_name=STOP_REQUEST_OPERATION,
        practice_id=practice_id,
        actor_id=actor_id,
        capture_session_id=capture_session_id,
        source_lease_id=source_lease_id,
        consent_evidence_id=consent_evidence_id,
        capability_id=capability_id,
        caller_principal=caller_principal,
        idempotency_key=idempotency_key,
        current=_now(now),
    )


def purge_expired_capture_secrets(
    *,
    now: datetime | None = None,
    limit: int = MAX_LOCAL_SECRET_PURGE_BATCH,
) -> LocalSecretPurgeResult:
    """Purge bounded expired ciphertext while retaining keyed tombstones."""

    if not 1 <= limit <= MAX_LOCAL_SECRET_PURGE_BATCH:
        raise ValueError("capture secret purge batch is outside its bound")
    current = _now(now)
    with get_session() as db:
        try:
            segments = db.exec(
                select(CaptureSegment)
                .where(
                    cast(Any, CaptureSegment.capability_expires_at) <= current,
                    CaptureSegment.encrypted_capability.is_not(None),  # type: ignore[attr-defined]
                    CaptureSegment.capability_purged_at.is_(None),  # type: ignore[attr-defined]
                )
                .order_by(
                    cast(Any, CaptureSegment.capability_expires_at),
                    cast(Any, CaptureSegment.created_at),
                )
                .limit(limit)
                .with_for_update(skip_locked=True)
            ).all()
            for segment in segments:
                segment.encrypted_capability = None
                segment.capability_purged_at = current
                db.add(segment)

            operations = db.exec(
                select(CaptureOperation)
                .where(
                    cast(Any, CaptureOperation.replay_response_expires_at) <= current,
                    CaptureOperation.encrypted_replay_response.is_not(None),  # type: ignore[attr-defined]
                    CaptureOperation.replay_response_purged_at.is_(None),  # type: ignore[attr-defined]
                )
                .order_by(
                    cast(Any, CaptureOperation.replay_response_expires_at),
                    cast(Any, CaptureOperation.created_at),
                )
                .limit(limit)
                .with_for_update(skip_locked=True)
            ).all()
            for operation in operations:
                operation.encrypted_replay_response = None
                operation.replay_response_purged_at = current
                db.add(operation)
            db.commit()
            return LocalSecretPurgeResult(
                capabilities_purged=len(segments),
                replay_responses_purged=len(operations),
            )
        except SQLAlchemyError:
            db.rollback()
            raise CaptureLifecycleUnavailable(
                "capture_secret_purge_unavailable"
            ) from None


def _has_never_started_blocker(
    db: Session,
    *,
    capture_session: CaptureSession,
) -> tuple[bool, CaptureOperation | None, tuple[CaptureOperation, ...]]:
    segments = tuple(
        db.exec(
            select(CaptureSegment)
            .where(CaptureSegment.session_id == capture_session.id)
            .with_for_update()
        ).all()
    )
    resource = db.exec(
        select(CaptureExternalResource)
        .where(CaptureExternalResource.session_id == capture_session.id)
        .limit(1)
    ).one_or_none()
    operations = db.exec(
        select(CaptureOperation)
        .where(CaptureOperation.session_id == capture_session.id)
        .with_for_update()
    ).all()
    create_operation = next(
        (
            operation
            for operation in operations
            if operation.operation == CREATE_SESSION_OPERATION
            and operation.state == "succeeded"
        ),
        None,
    )
    start_operations = tuple(
        operation
        for operation in operations
        if operation.operation == START_SEGMENT_OPERATION
    )
    failed_segment_ids = {
        segment.id
        for segment in segments
        if segment.state == "failed"
        and segment.physical_started_at is None
        and segment.ended_at is None
        and segment.capability_id is None
        and segment.capability_digest is None
        and segment.encrypted_capability is None
        and segment.capability_issued_at is None
        and segment.capability_expires_at is None
        and segment.capability_consumed_at is None
        and segment.capability_revoked_at is None
        and segment.capability_purged_at is None
    }
    failed_operation_segment_ids = {
        operation.segment_id
        for operation in start_operations
        if operation.state == "failed"
        and operation.completed_at is not None
        and operation.last_error_code in _PROVEN_NO_START_ERROR_CODES
        and operation.segment_id is not None
    }
    deterministic_failed_start = (
        bool(segments)
        and len(failed_segment_ids) == len(segments)
        and len(failed_operation_segment_ids) == len(start_operations)
        and len(start_operations) == len(segments)
        and failed_operation_segment_ids == failed_segment_ids
    )
    ambiguous_operation = any(
        (
            operation.operation == CREATE_SESSION_OPERATION
            and operation.state in {"reserved", "in_flight", "uncertain"}
        )
        or operation.operation
        not in {
            CREATE_SESSION_OPERATION,
            START_SEGMENT_OPERATION,
        }
        for operation in operations
    )
    return (
        resource is not None
        or ambiguous_operation
        or (bool(segments) or bool(start_operations))
        and not deterministic_failed_start,
        create_operation,
        start_operations if deterministic_failed_start else (),
    )


def _never_started_evidence_clean(
    capture_session: CaptureSession,
    source_lease: CaptureSourceLease,
) -> bool:
    return (
        capture_session.started_at is None
        and capture_session.ended_at is None
        and source_lease.stop_requested_at is None
        and source_lease.stop_proved_at is None
        and source_lease.never_started_proved_at is None
        and source_lease.closed_at is None
    )


def _retire_launch_authorizations(
    db: Session,
    *,
    capture_session: CaptureSession,
    source_lease: CaptureSourceLease,
    current: datetime,
) -> tuple[CaptureLaunchAuthorization, ...]:
    authorizations = tuple(
        db.exec(
            select(CaptureLaunchAuthorization)
            .where(
                CaptureLaunchAuthorization.session_id == capture_session.id,
                CaptureLaunchAuthorization.source_lease_id == source_lease.id,
            )
            .with_for_update()
        ).all()
    )
    for authorization in authorizations:
        if authorization.state not in {"ticket_issued", "code_issued", "token_issued"}:
            continue
        if authorization.desktop_token_digest is not None:
            expires_at = authorization.desktop_token_expires_at
            if expires_at is not None and expires_at <= current:
                authorization.state = "expired"
            else:
                authorization.state = "revoked"
                authorization.desktop_token_revoked_at = current
        elif authorization.authorization_code_digest is not None:
            expires_at = authorization.authorization_code_expires_at
            if expires_at is not None and expires_at <= current:
                authorization.state = "expired"
            else:
                authorization.state = "revoked"
                authorization.authorization_code_revoked_at = current
        elif authorization.ticket_expires_at <= current:
            authorization.state = "expired"
        else:
            authorization.state = "revoked"
            authorization.ticket_revoked_at = current
        authorization.state_version += 1
        db.add(authorization)
    return authorizations


def _close_never_started(
    db: Session,
    *,
    capture_session: CaptureSession,
    source_lease: CaptureSourceLease,
    create_operation: CaptureOperation | None,
    proven_failed_start_operations: tuple[CaptureOperation, ...],
    current: datetime,
) -> None:
    _retire_launch_authorizations(
        db,
        capture_session=capture_session,
        source_lease=source_lease,
        current=current,
    )
    for operation in proven_failed_start_operations:
        if operation.encrypted_external_correlation_ref is not None:
            operation.encrypted_external_correlation_ref = None
            operation.correlation_purged_at = current
            db.add(operation)
    source_lease.state = "closed"
    source_lease.stop_requested_at = current
    source_lease.never_started_proved_at = current
    source_lease.closed_at = current
    capture_session.state = "cancelled"
    capture_session.state_version += 1
    db.add(source_lease)
    db.add(capture_session)
    _append_event(
        db,
        capture_session,
        event_type="capture.never_started_closed",
        segment_id=None,
        operation_id=None if create_operation is None else create_operation.id,
        recorded_at=current,
    )


def release_expired_never_started_session(
    *,
    practice_id: TypeIDType,
    actor_id: TypeIDType,
    appointment_id: TypeIDType,
    request_binding_digest: str,
    now: datetime | None = None,
) -> bool:
    """Release an exact ready session only after its pre-PKCE ticket expires."""

    current = _now(now)
    expected_request_digest = capture_session_preparation_request_digest(
        practice_id=practice_id,
        actor_id=actor_id,
        appointment_id=appointment_id,
        request_binding_digest=request_binding_digest,
    )
    with get_session() as db:
        try:
            row = db.exec(
                select(CaptureSession, CaptureSourceLease)
                .join(
                    CaptureSourceLease,
                    cast(Any, CaptureSourceLease.session_id)
                    == cast(Any, CaptureSession.id),
                )
                .where(
                    CaptureSession.practice_id == practice_id,
                    CaptureSession.actor_id == actor_id,
                    CaptureSession.appointment_id == appointment_id,
                    CaptureSession.source_kind == DESKTOP_SOURCE,
                    CaptureSession.state == "ready",
                    CaptureSession.consent_state == "granted",
                    CaptureSourceLease.practice_id == practice_id,
                    CaptureSourceLease.actor_id == actor_id,
                    CaptureSourceLease.appointment_id == appointment_id,
                    CaptureSourceLease.source_kind == DESKTOP_SOURCE,
                    CaptureSourceLease.state == "active",
                    cast(Any, CaptureSourceLease.closed_at).is_(None),
                )
                .with_for_update()
            ).one_or_none()
            if row is None:
                return False
            capture_session, source_lease = row
            blocked, create_operation, proven_failed_starts = (
                _has_never_started_blocker(
                    db,
                    capture_session=capture_session,
                )
            )
            if (
                blocked
                or create_operation is None
                or not _never_started_evidence_clean(capture_session, source_lease)
                or not hmac.compare_digest(
                    create_operation.request_digest,
                    expected_request_digest,
                )
            ):
                return False
            authorizations = tuple(
                db.exec(
                    select(CaptureLaunchAuthorization)
                    .where(
                        CaptureLaunchAuthorization.session_id == capture_session.id,
                        CaptureLaunchAuthorization.source_lease_id == source_lease.id,
                    )
                    .with_for_update()
                ).all()
            )
            if not authorizations or not all(
                authorization.authorization_code_digest is None
                and authorization.desktop_token_digest is None
                and authorization.ticket_expires_at <= current
                and authorization.state in {"ticket_issued", "expired"}
                for authorization in authorizations
            ):
                return False
            _close_never_started(
                db,
                capture_session=capture_session,
                source_lease=source_lease,
                create_operation=create_operation,
                proven_failed_start_operations=proven_failed_starts,
                current=current,
            )
            db.commit()
            return True
        except SQLAlchemyError:
            db.rollback()
            raise CaptureLifecycleUnavailable(
                "capture_never_started_release_unavailable"
            ) from None


def reconcile_never_started_leases(
    *,
    now: datetime | None = None,
    limit: int = MAX_NEVER_STARTED_SCAN_BATCH,
) -> NeverStartedReconciliationResult:
    """Close only deadline-expired leases with durable proof of no start."""

    if not 1 <= limit <= MAX_NEVER_STARTED_SCAN_BATCH:
        raise ValueError("never-started reconciliation batch is outside its bound")
    current = _now(now)
    with get_session() as db:
        try:
            rows = db.exec(
                select(CaptureSession, CaptureSourceLease)
                .join(
                    CaptureSourceLease,
                    cast(Any, CaptureSourceLease.session_id)
                    == cast(Any, CaptureSession.id),
                )
                .where(
                    CaptureSession.source_kind == DESKTOP_SOURCE,
                    cast(Any, CaptureSession.state).in_(
                        (
                            "awaiting_consent",
                            "ready",
                            "review_required",
                            "failed",
                            "cancelled",
                        )
                    ),
                    CaptureSourceLease.source_kind == DESKTOP_SOURCE,
                    cast(Any, CaptureSourceLease.state).in_(("active", "uncertain")),
                    cast(Any, CaptureSourceLease.closed_at).is_(None),
                    CaptureSourceLease.control_deadline_at <= current,
                )
                .order_by(
                    cast(Any, CaptureSourceLease.control_deadline_at),
                    cast(Any, CaptureSourceLease.created_at),
                )
                .limit(limit)
                .with_for_update(skip_locked=True)
            ).all()
            closed = 0
            deferred = 0
            for capture_session, source_lease in rows:
                blocked, create_operation, proven_failed_starts = (
                    _has_never_started_blocker(
                        db,
                        capture_session=capture_session,
                    )
                )
                if blocked:
                    deferred += 1
                    continue
                if not _never_started_evidence_clean(capture_session, source_lease):
                    deferred += 1
                    continue
                _close_never_started(
                    db,
                    capture_session=capture_session,
                    source_lease=source_lease,
                    create_operation=create_operation,
                    proven_failed_start_operations=proven_failed_starts,
                    current=current,
                )
                closed += 1
            db.commit()
            return NeverStartedReconciliationResult(
                examined=len(rows),
                closed=closed,
                deferred=deferred,
            )
        except SQLAlchemyError:
            db.rollback()
            raise CaptureLifecycleUnavailable(
                "capture_never_started_reconciliation_unavailable"
            ) from None
