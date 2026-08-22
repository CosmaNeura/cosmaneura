"""Provider-neutral capture session and external-create coordination.

The database receipt is committed before any capture-provider mutation.  A
request that might have reached the provider is reconciled by its random,
non-PHI correlation reference and is never blindly replayed.
"""

from __future__ import annotations

import json
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, cast
from uuid import UUID, uuid4

from app.services.capture_provider import (
    CaptureObservation,
    CaptureProvider,
    CaptureProviderDisabled,
    CaptureProviderOutcomeUncertain,
    CaptureProviderRejected,
    CaptureProvisioning,
)
from app.utils.encryption import decrypt_value, encrypt_value, pseudonymous_digest

from activemodel.session_manager import get_session
from activemodel.types import TypeIDType
from app.models.appointment import Appointment
from app.models.capture_coordination import (
    CaptureEvent,
    CaptureExternalResource,
    CaptureOperation,
    CaptureSegment,
    CaptureSession,
    CaptureSourceLease,
)
from app.models.doctor import Doctor, DoctorRole
from app.models.practice import Practice, PracticeMember
from sqlalchemy import and_, func, or_
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlmodel import Session, select

DESKTOP_SOURCE = "desktop_companion"
DESKTOP_ADAPTER_PROFILE = "desktop-audio-v1"
CREATE_SESSION_OPERATION = "create_capture_session"
START_SEGMENT_OPERATION = "start_capture_segment"
BROWSER_CALLER_SCOPE = "browser"
DESKTOP_CALLER_SCOPE = "desktop_main"
INITIAL_LEASE_WINDOW = timedelta(minutes=10)
ACTIVE_LEASE_WINDOW = timedelta(minutes=2)
CAPABILITY_WINDOW = timedelta(minutes=5)
EXTERNAL_RETENTION_WINDOW = timedelta(hours=6)
RECONCILIATION_RETRY = timedelta(seconds=30)
# Recall reconciliation may traverse five bounded pages at a 30-second HTTP
# timeout each.  Keep the durable claim longer than that worst-case window;
# attempt_count is still the authoritative fence if a worker outlives it.
PROVIDER_OPERATION_LEASE = timedelta(minutes=3)
MAX_RECONCILIATION_SCAN_BATCH = 25
MAX_CLEANUP_SCAN_BATCH = 100
_CLEANUP_BLOCKED_CODE = "provider_delete_not_supported"

_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9._~-]{8,255}$")
_POLICY_VERSION = re.compile(r"^[A-Za-z0-9._~-]{1,32}$")
_ADAPTER_PROFILE = re.compile(r"^[A-Za-z0-9._~-]{1,64}$")
_PROVIDER_REF = re.compile(r"^[A-Za-z0-9_-]{1,256}$")
_CAPABILITY = re.compile(r"^[\x21-\x7e]{16,4096}$")


class CaptureCoordinationError(RuntimeError):
    """Bounded failure that is safe to map to a fixed API response."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class CaptureCoordinationDenied(CaptureCoordinationError):
    pass


class CaptureCoordinationConflict(CaptureCoordinationError):
    pass


class CaptureCoordinationUnavailable(CaptureCoordinationError):
    pass


class CaptureOutcomeUncertain(CaptureCoordinationError):
    pass


@dataclass(frozen=True)
class ScribeCaptureAuthorization:
    """Receipts obtained server-to-server from the authoritative Scribe API."""

    consent_evidence_id: UUID
    consent_policy_version: str
    processing_session_id: UUID


@dataclass(frozen=True)
class PreparedCaptureSession:
    capture_session_id: TypeIDType
    practice_id: TypeIDType
    actor_id: TypeIDType
    appointment_id: TypeIDType
    consent_evidence_id: UUID
    source_lease_id: TypeIDType
    state_version: int


@dataclass(frozen=True)
class CaptureSessionPreparationReservation:
    """Durable pre-Scribe reservation that owns the appointment source lease."""

    operation_id: TypeIDType
    capture_session_id: TypeIDType
    practice_id: TypeIDType
    actor_id: TypeIDType
    appointment_id: TypeIDType
    patient_id: TypeIDType
    source_lease_id: TypeIDType
    request_digest: str
    attempt_generation: int


@dataclass(frozen=True)
class StartAuthority:
    capture_session_id: str
    actor_id: str
    consent_record_id: str
    source_lease_id: str
    issued_at: datetime
    expires_at: datetime
    capability_id: str
    capability_secret: str

    def as_wire_dict(self) -> dict[str, Any]:
        issued_at = self.issued_at.isoformat().replace("+00:00", "Z")
        expires_at = self.expires_at.isoformat().replace("+00:00", "Z")
        return {
            "schemaVersion": 1,
            "decision": "authorized",
            "captureSessionId": self.capture_session_id,
            "actorId": self.actor_id,
            "consentRecordId": self.consent_record_id,
            "consentStatus": "granted",
            "sessionStatus": "authorized",
            "source": DESKTOP_SOURCE,
            "sourceLeaseId": self.source_lease_id,
            "issuedAt": issued_at,
            "expiresAt": expires_at,
            "captureCapability": {
                "capabilityId": self.capability_id,
                "secret": self.capability_secret,
                "expiresAt": expires_at,
            },
        }


@dataclass(frozen=True)
class _StartReservation:
    operation_id: TypeIDType
    correlation_ref: str
    attempt_generation: int
    source_lease_id: TypeIDType
    request_digest: str
    caller_digest: str


@dataclass(frozen=True)
class CleanupVisibility:
    """Count-only cleanup posture safe for job telemetry."""

    newly_blocked: int
    blocked_due: int


def _now(value: datetime | None) -> datetime:
    current = value or datetime.now(UTC)
    if current.tzinfo is None:
        raise ValueError("capture coordination clock must be timezone-aware")
    return current.astimezone(UTC)


def _validate_idempotency_key(value: str) -> str:
    if _IDEMPOTENCY_KEY.fullmatch(value) is None:
        raise CaptureCoordinationDenied("capture_not_authorized")
    return value


def _digest(value: str, *, context: str) -> str:
    return pseudonymous_digest(value, context=context)


def _canonical_digest(value: dict[str, str], *, context: str) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return _digest(canonical, context=context)


def _scope_values(capture_session: CaptureSession) -> dict[str, Any]:
    return {
        "session_id": capture_session.id,
        "practice_id": capture_session.practice_id,
        "actor_id": capture_session.actor_id,
        "appointment_id": capture_session.appointment_id,
    }


def _next_event_sequence(db: Session, session_id: TypeIDType) -> int:
    current = db.exec(
        select(func.max(CaptureEvent.local_sequence)).where(
            CaptureEvent.session_id == session_id
        )
    ).one()
    return 0 if current is None else int(current) + 1


def _event(
    db: Session,
    capture_session: CaptureSession,
    *,
    event_type: str,
    segment_id: TypeIDType | None = None,
    operation_id: TypeIDType | None = None,
    recorded_at: datetime,
) -> None:
    db.add(
        CaptureEvent(
            **_scope_values(capture_session),
            segment_id=segment_id,
            operation_id=operation_id,
            local_sequence=_next_event_sequence(db, capture_session.id),
            event_type=event_type,
            state_version=capture_session.state_version,
            session_state=capture_session.state,
            recorded_at=recorded_at,
        )
    )


def _practice_scope(
    db: Session,
    *,
    practice_id: TypeIDType,
    actor_id: TypeIDType,
    appointment_id: TypeIDType,
) -> tuple[Practice, Appointment]:
    doctor = db.exec(
        select(Doctor)
        .where(
            Doctor.id == actor_id,
            Doctor.role == DoctorRole.normal,
            cast(Any, Doctor.deleted_at).is_(None),
        )
        .with_for_update()
    ).one_or_none()
    practice = db.exec(
        select(Practice).where(Practice.id == practice_id).with_for_update()
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
    appointment = db.exec(
        select(Appointment)
        .where(
            Appointment.id == appointment_id,
            Appointment.doctor_id == actor_id,
            cast(Any, Appointment.cancelled_at).is_(None),
        )
        .with_for_update()
    ).one_or_none()
    if (
        doctor is None
        or practice is None
        or membership is None
        or appointment is None
        or not practice.baa_in_force()
        or not practice.entitled()
    ):
        raise CaptureCoordinationDenied("capture_not_authorized")
    return practice, appointment


def _creation_request_digest(
    *,
    practice_id: TypeIDType,
    actor_id: TypeIDType,
    appointment_id: TypeIDType,
    authorization: ScribeCaptureAuthorization,
) -> str:
    return _canonical_digest(
        {
            "actor_id": str(actor_id),
            "appointment_id": str(appointment_id),
            "consent_evidence_id": str(authorization.consent_evidence_id),
            "consent_policy_version": authorization.consent_policy_version,
            "practice_id": str(practice_id),
            "processing_session_id": str(authorization.processing_session_id),
            "source": DESKTOP_SOURCE,
        },
        context="capture-session-request-v1",
    )


def capture_session_preparation_request_digest(
    *,
    practice_id: TypeIDType,
    actor_id: TypeIDType,
    appointment_id: TypeIDType,
    request_binding_digest: str,
) -> str:
    if re.fullmatch(r"[0-9a-f]{64}", request_binding_digest) is None:
        raise CaptureCoordinationDenied("capture_not_authorized")
    return _canonical_digest(
        {
            "actor_id": str(actor_id),
            "appointment_id": str(appointment_id),
            "practice_id": str(practice_id),
            "request_binding_digest": request_binding_digest,
            "source": DESKTOP_SOURCE,
        },
        context="capture-session-preparation-request-v1",
    )


def _prepared_from_operation(
    db: Session,
    operation: CaptureOperation,
    *,
    request_digest: str,
) -> PreparedCaptureSession:
    if operation.request_digest != request_digest or operation.state != "succeeded":
        raise CaptureCoordinationConflict("capture_request_conflict")
    capture_session = db.exec(
        select(CaptureSession).where(CaptureSession.id == operation.session_id)
    ).one_or_none()
    source_lease = db.exec(
        select(CaptureSourceLease).where(
            CaptureSourceLease.session_id == operation.session_id,
            CaptureSourceLease.practice_id == operation.practice_id,
            CaptureSourceLease.actor_id == operation.actor_id,
            CaptureSourceLease.appointment_id == operation.appointment_id,
            CaptureSourceLease.source_kind == DESKTOP_SOURCE,
            cast(Any, CaptureSourceLease.closed_at).is_(None),
        )
    ).one_or_none()
    if (
        capture_session is None
        or source_lease is None
        or capture_session.scribe_consent_receipt_id is None
    ):
        raise CaptureCoordinationConflict("capture_request_conflict")
    return PreparedCaptureSession(
        capture_session_id=capture_session.id,
        practice_id=capture_session.practice_id,
        actor_id=capture_session.actor_id,
        appointment_id=capture_session.appointment_id,
        consent_evidence_id=capture_session.scribe_consent_receipt_id,
        source_lease_id=source_lease.id,
        state_version=capture_session.state_version,
    )


def _preparation_reservation_from_operation(
    db: Session,
    operation: CaptureOperation,
    *,
    request_digest: str,
    caller_digest: str,
    patient_id: TypeIDType,
    current: datetime,
) -> CaptureSessionPreparationReservation | PreparedCaptureSession:
    if (
        operation.request_digest != request_digest
        or operation.caller_principal_digest != caller_digest
    ):
        raise CaptureCoordinationConflict("capture_request_conflict")
    if operation.state == "succeeded":
        return _prepared_from_operation(db, operation, request_digest=request_digest)
    if operation.state in {"reserved", "in_flight", "uncertain"}:
        capture_session = db.exec(
            select(CaptureSession)
            .where(CaptureSession.id == operation.session_id)
            .with_for_update()
        ).one_or_none()
        source_lease = db.exec(
            select(CaptureSourceLease)
            .where(
                CaptureSourceLease.session_id == operation.session_id,
                CaptureSourceLease.practice_id == operation.practice_id,
                CaptureSourceLease.actor_id == operation.actor_id,
                CaptureSourceLease.appointment_id == operation.appointment_id,
                CaptureSourceLease.source_kind == DESKTOP_SOURCE,
                cast(Any, CaptureSourceLease.closed_at).is_(None),
            )
            .with_for_update()
        ).one_or_none()
        if (
            capture_session is None
            or source_lease is None
            or capture_session.state not in {"awaiting_consent", "review_required"}
            or capture_session.consent_state not in {"pending", "uncertain"}
            or source_lease.state not in {"active", "uncertain"}
        ):
            raise CaptureOutcomeUncertain("capture_preparation_outcome_uncertain")
        retry_at = operation.lease_expires_at or operation.next_attempt_at
        if retry_at is None or retry_at > current:
            # Another attempt may still be calling Scribe. Its exact operation
            # keys are safe to replay only after this durable claim expires.
            raise CaptureOutcomeUncertain("capture_preparation_outcome_uncertain")
        if operation.attempt_count >= operation.max_attempts:
            operation.state = "uncertain"
            operation.last_error_code = "scribe_retry_exhausted"
            operation.next_attempt_at = None
            operation.lease_expires_at = None
            capture_session.state = "review_required"
            capture_session.consent_state = "uncertain"
            capture_session.state_version += 1
            source_lease.state = "uncertain"
            db.add(operation)
            db.add(capture_session)
            db.add(source_lease)
            _event(
                db,
                capture_session,
                event_type="capture.session_preparation_retry_exhausted",
                operation_id=operation.id,
                recorded_at=current,
            )
            db.commit()
            raise CaptureOutcomeUncertain("capture_preparation_outcome_uncertain")

        operation.state = "in_flight"
        operation.attempt_count += 1
        operation.last_attempt_at = current
        operation.next_attempt_at = current + PROVIDER_OPERATION_LEASE
        operation.lease_expires_at = current + PROVIDER_OPERATION_LEASE
        operation.last_error_code = None
        capture_session.state = "awaiting_consent"
        capture_session.consent_state = "pending"
        capture_session.state_version += 1
        source_lease.state = "active"
        source_lease.heartbeat_at = current
        source_lease.control_deadline_at = current + INITIAL_LEASE_WINDOW
        db.add(operation)
        db.add(capture_session)
        db.add(source_lease)
        _event(
            db,
            capture_session,
            event_type="capture.session_preparation_reclaimed",
            operation_id=operation.id,
            recorded_at=current,
        )
        db.commit()
        return CaptureSessionPreparationReservation(
            operation_id=operation.id,
            capture_session_id=capture_session.id,
            practice_id=operation.practice_id,
            actor_id=operation.actor_id,
            appointment_id=operation.appointment_id,
            patient_id=patient_id,
            source_lease_id=source_lease.id,
            request_digest=request_digest,
            attempt_generation=operation.attempt_count,
        )
    raise CaptureCoordinationConflict("capture_request_conflict")


def reserve_capture_session_preparation(
    *,
    actor_id: TypeIDType,
    practice_id: TypeIDType,
    appointment_id: TypeIDType,
    patient_id: TypeIDType,
    idempotency_key: str,
    request_binding_digest: str,
    now: datetime | None = None,
) -> CaptureSessionPreparationReservation | PreparedCaptureSession:
    """Own the appointment lease before any Scribe mutation can be attempted."""

    current = _now(now)
    key = _validate_idempotency_key(idempotency_key)
    idempotency_digest = _digest(key, context="capture-session-idempotency-v1")
    request_digest = capture_session_preparation_request_digest(
        practice_id=practice_id,
        actor_id=actor_id,
        appointment_id=appointment_id,
        request_binding_digest=request_binding_digest,
    )
    caller_digest = _digest(str(actor_id), context="capture-browser-caller-v1")

    with get_session() as db:
        try:
            _practice_scope(
                db,
                practice_id=practice_id,
                actor_id=actor_id,
                appointment_id=appointment_id,
            )
            existing = db.exec(
                select(CaptureOperation)
                .where(
                    CaptureOperation.practice_id == practice_id,
                    CaptureOperation.caller_scope == BROWSER_CALLER_SCOPE,
                    CaptureOperation.operation == CREATE_SESSION_OPERATION,
                    CaptureOperation.idempotency_key_digest == idempotency_digest,
                )
                .with_for_update()
            ).one_or_none()
            if existing is not None:
                return _preparation_reservation_from_operation(
                    db,
                    existing,
                    request_digest=request_digest,
                    caller_digest=caller_digest,
                    patient_id=patient_id,
                    current=current,
                )

            open_lease = db.exec(
                select(CaptureSourceLease)
                .where(
                    CaptureSourceLease.practice_id == practice_id,
                    CaptureSourceLease.appointment_id == appointment_id,
                    cast(Any, CaptureSourceLease.closed_at).is_(None),
                )
                .with_for_update()
            ).one_or_none()
            if open_lease is not None:
                raise CaptureCoordinationConflict("capture_source_in_use")
            latest_generation = db.exec(
                select(CaptureSourceLease.generation)
                .where(
                    CaptureSourceLease.practice_id == practice_id,
                    CaptureSourceLease.appointment_id == appointment_id,
                )
                .order_by(cast(Any, CaptureSourceLease.generation).desc())
                .limit(1)
            ).one_or_none()
            generation = 0 if latest_generation is None else latest_generation + 1

            capture_session = CaptureSession(
                practice_id=practice_id,
                actor_id=actor_id,
                appointment_id=appointment_id,
                source_kind=DESKTOP_SOURCE,
                state="awaiting_consent",
                consent_state="pending",
                authority_origin="server_authorized",
            )
            db.add(capture_session)
            db.flush()
            lease_nonce = secrets.token_urlsafe(32)
            source_lease = CaptureSourceLease(
                **_scope_values(capture_session),
                source_kind=DESKTOP_SOURCE,
                state="active",
                lease_token_digest=_digest(
                    lease_nonce, context="capture-source-lease-token-v1"
                ),
                generation=generation,
                acquired_at=current,
                heartbeat_at=current,
                control_deadline_at=current + INITIAL_LEASE_WINDOW,
            )
            operation = CaptureOperation(
                **_scope_values(capture_session),
                caller_scope=BROWSER_CALLER_SCOPE,
                caller_principal_digest=caller_digest,
                operation=CREATE_SESSION_OPERATION,
                idempotency_key_digest=idempotency_digest,
                request_digest=request_digest,
                state="in_flight",
                attempt_count=1,
                last_attempt_at=current,
                next_attempt_at=current + PROVIDER_OPERATION_LEASE,
                lease_expires_at=current + PROVIDER_OPERATION_LEASE,
            )
            db.add(source_lease)
            db.add(operation)
            db.flush()
            _event(
                db,
                capture_session,
                event_type="capture.session_preparation_reserved",
                operation_id=operation.id,
                recorded_at=current,
            )
            db.commit()
            return CaptureSessionPreparationReservation(
                operation_id=operation.id,
                capture_session_id=capture_session.id,
                practice_id=practice_id,
                actor_id=actor_id,
                appointment_id=appointment_id,
                patient_id=patient_id,
                source_lease_id=source_lease.id,
                request_digest=request_digest,
                attempt_generation=operation.attempt_count,
            )
        except CaptureCoordinationError:
            db.rollback()
            raise
        except IntegrityError:
            db.rollback()
            existing = db.exec(
                select(CaptureOperation).where(
                    CaptureOperation.practice_id == practice_id,
                    CaptureOperation.caller_scope == BROWSER_CALLER_SCOPE,
                    CaptureOperation.operation == CREATE_SESSION_OPERATION,
                    CaptureOperation.idempotency_key_digest == idempotency_digest,
                )
            ).one_or_none()
            if existing is not None:
                return _preparation_reservation_from_operation(
                    db,
                    existing,
                    request_digest=request_digest,
                    caller_digest=caller_digest,
                    patient_id=patient_id,
                    current=current,
                )
            raise CaptureCoordinationConflict("capture_source_in_use") from None
        except SQLAlchemyError:
            db.rollback()
            raise CaptureCoordinationUnavailable(
                "capture_storage_unavailable"
            ) from None


def _load_preparation_records(
    db: Session,
    reservation: CaptureSessionPreparationReservation,
) -> tuple[CaptureOperation, CaptureSession, CaptureSourceLease]:
    operation = db.exec(
        select(CaptureOperation)
        .where(
            CaptureOperation.id == reservation.operation_id,
            CaptureOperation.session_id == reservation.capture_session_id,
            CaptureOperation.practice_id == reservation.practice_id,
            CaptureOperation.actor_id == reservation.actor_id,
            CaptureOperation.appointment_id == reservation.appointment_id,
            CaptureOperation.operation == CREATE_SESSION_OPERATION,
        )
        .with_for_update()
    ).one_or_none()
    capture_session = db.exec(
        select(CaptureSession)
        .where(
            CaptureSession.id == reservation.capture_session_id,
            CaptureSession.practice_id == reservation.practice_id,
            CaptureSession.actor_id == reservation.actor_id,
            CaptureSession.appointment_id == reservation.appointment_id,
        )
        .with_for_update()
    ).one_or_none()
    source_lease = db.exec(
        select(CaptureSourceLease)
        .where(
            CaptureSourceLease.id == reservation.source_lease_id,
            CaptureSourceLease.session_id == reservation.capture_session_id,
            CaptureSourceLease.practice_id == reservation.practice_id,
            CaptureSourceLease.actor_id == reservation.actor_id,
            CaptureSourceLease.appointment_id == reservation.appointment_id,
        )
        .with_for_update()
    ).one_or_none()
    if operation is None or capture_session is None or source_lease is None:
        raise CaptureCoordinationUnavailable("capture_preparation_unavailable")
    if operation.request_digest != reservation.request_digest:
        raise CaptureCoordinationConflict("capture_request_conflict")
    return operation, capture_session, source_lease


def finalize_capture_session_preparation(
    *,
    reservation: CaptureSessionPreparationReservation,
    authorization: ScribeCaptureAuthorization,
    now: datetime | None = None,
) -> PreparedCaptureSession:
    """Promote one durable reservation after exact Scribe receipt validation."""

    current = _now(now)
    if _POLICY_VERSION.fullmatch(authorization.consent_policy_version) is None:
        raise CaptureCoordinationDenied("capture_not_authorized")
    with get_session() as db:
        try:
            operation, capture_session, source_lease = _load_preparation_records(
                db, reservation
            )
            if operation.attempt_count != reservation.attempt_generation:
                raise CaptureOutcomeUncertain("capture_preparation_outcome_uncertain")
            if operation.state == "succeeded":
                if (
                    capture_session.scribe_consent_receipt_id
                    != authorization.consent_evidence_id
                    or capture_session.consent_policy_version
                    != authorization.consent_policy_version
                    or capture_session.processing_session_id
                    != authorization.processing_session_id
                ):
                    raise CaptureCoordinationConflict("capture_request_conflict")
                return _prepared_from_operation(
                    db, operation, request_digest=reservation.request_digest
                )
            if (
                operation.state != "in_flight"
                or capture_session.state != "awaiting_consent"
                or capture_session.consent_state != "pending"
                or source_lease.state != "active"
                or source_lease.closed_at is not None
            ):
                raise CaptureOutcomeUncertain("capture_preparation_outcome_uncertain")

            if (
                (
                    capture_session.scribe_consent_receipt_id is not None
                    and capture_session.scribe_consent_receipt_id
                    != authorization.consent_evidence_id
                )
                or (
                    capture_session.consent_policy_version is not None
                    and capture_session.consent_policy_version
                    != authorization.consent_policy_version
                )
                or (
                    capture_session.processing_session_id is not None
                    and capture_session.processing_session_id
                    != authorization.processing_session_id
                )
            ):
                raise CaptureOutcomeUncertain("capture_preparation_outcome_uncertain")

            _, appointment = _practice_scope(
                db,
                practice_id=reservation.practice_id,
                actor_id=reservation.actor_id,
                appointment_id=reservation.appointment_id,
            )
            if appointment.patient_id != reservation.patient_id:
                raise CaptureCoordinationDenied("capture_not_authorized")
            capture_session.scribe_consent_receipt_id = (
                authorization.consent_evidence_id
            )
            capture_session.consent_policy_version = (
                authorization.consent_policy_version
            )
            capture_session.processing_session_id = authorization.processing_session_id
            capture_session.consent_state = "granted"
            capture_session.state = "ready"
            capture_session.state_version += 1
            operation.state = "succeeded"
            operation.completed_at = current
            operation.last_error_code = None
            operation.next_attempt_at = None
            operation.lease_expires_at = None
            source_lease.heartbeat_at = current
            source_lease.control_deadline_at = current + INITIAL_LEASE_WINDOW
            db.add(capture_session)
            db.add(operation)
            db.add(source_lease)
            _event(
                db,
                capture_session,
                event_type="capture.session_authorized",
                operation_id=operation.id,
                recorded_at=current,
            )
            db.commit()
            return PreparedCaptureSession(
                capture_session_id=capture_session.id,
                practice_id=capture_session.practice_id,
                actor_id=capture_session.actor_id,
                appointment_id=capture_session.appointment_id,
                consent_evidence_id=authorization.consent_evidence_id,
                source_lease_id=source_lease.id,
                state_version=capture_session.state_version,
            )
        except CaptureCoordinationError:
            db.rollback()
            raise
        except (IntegrityError, SQLAlchemyError):
            db.rollback()
            raise CaptureCoordinationUnavailable(
                "capture_preparation_unavailable"
            ) from None


def record_capture_session_preparation_failure(
    *,
    reservation: CaptureSessionPreparationReservation,
    error_code: str,
    outcome_ambiguous: bool,
    observed_consent_evidence_id: UUID | None = None,
    observed_consent_policy_version: str | None = None,
    observed_processing_session_id: UUID | None = None,
    now: datetime | None = None,
) -> None:
    """Fence failure without claiming a provider start or discarding receipts."""

    current = _now(now)
    if re.fullmatch(r"[a-z0-9_]{1,64}", error_code) is None:
        raise CaptureCoordinationUnavailable("capture_preparation_unavailable")
    if (
        observed_consent_policy_version is not None
        and _POLICY_VERSION.fullmatch(observed_consent_policy_version) is None
    ):
        observed_consent_policy_version = None

    with get_session() as db:
        try:
            operation, capture_session, source_lease = _load_preparation_records(
                db, reservation
            )
            if operation.attempt_count != reservation.attempt_generation:
                raise CaptureOutcomeUncertain("capture_preparation_outcome_uncertain")
            if operation.state == "succeeded":
                return
            if operation.state in {"failed", "uncertain"}:
                return
            if operation.state != "in_flight":
                raise CaptureCoordinationConflict("capture_request_conflict")

            capture_session.scribe_consent_receipt_id = observed_consent_evidence_id
            capture_session.consent_policy_version = observed_consent_policy_version
            capture_session.processing_session_id = observed_processing_session_id
            capture_session.state_version += 1
            operation.last_error_code = error_code

            if outcome_ambiguous:
                capture_session.state = "review_required"
                capture_session.consent_state = "uncertain"
                source_lease.state = "uncertain"
                operation.state = "uncertain"
                operation.next_attempt_at = current + RECONCILIATION_RETRY
                operation.lease_expires_at = current + RECONCILIATION_RETRY
                event_type = "capture.session_preparation_uncertain"
            else:
                capture_session.state = "failed"
                capture_session.consent_state = "pending"
                source_lease.state = "closed"
                source_lease.stop_requested_at = current
                source_lease.never_started_proved_at = current
                source_lease.closed_at = current
                operation.state = "failed"
                operation.completed_at = current
                operation.next_attempt_at = None
                operation.lease_expires_at = None
                event_type = "capture.session_preparation_failed"

            db.add(capture_session)
            db.add(source_lease)
            db.add(operation)
            _event(
                db,
                capture_session,
                event_type=event_type,
                operation_id=operation.id,
                recorded_at=current,
            )
            db.commit()
        except CaptureCoordinationError:
            db.rollback()
            raise
        except (IntegrityError, SQLAlchemyError):
            db.rollback()
            raise CaptureCoordinationUnavailable(
                "capture_preparation_unavailable"
            ) from None


def create_authorized_capture_session(
    *,
    actor_id: TypeIDType,
    practice_id: TypeIDType,
    appointment_id: TypeIDType,
    idempotency_key: str,
    authorization: ScribeCaptureAuthorization,
    now: datetime | None = None,
) -> PreparedCaptureSession:
    """Atomically create one consented session and exclusive desktop lease.

    ``authorization`` must come from a trusted server-to-server Scribe adapter,
    never from the browser request body.
    """

    current = _now(now)
    key = _validate_idempotency_key(idempotency_key)
    if _POLICY_VERSION.fullmatch(authorization.consent_policy_version) is None:
        raise CaptureCoordinationDenied("capture_not_authorized")
    idempotency_digest = _digest(key, context="capture-session-idempotency-v1")
    request_digest = _creation_request_digest(
        practice_id=practice_id,
        actor_id=actor_id,
        appointment_id=appointment_id,
        authorization=authorization,
    )
    caller_digest = _digest(str(actor_id), context="capture-browser-caller-v1")

    with get_session() as db:
        try:
            _practice_scope(
                db,
                practice_id=practice_id,
                actor_id=actor_id,
                appointment_id=appointment_id,
            )
            existing = db.exec(
                select(CaptureOperation)
                .where(
                    CaptureOperation.practice_id == practice_id,
                    CaptureOperation.caller_scope == BROWSER_CALLER_SCOPE,
                    CaptureOperation.operation == CREATE_SESSION_OPERATION,
                    CaptureOperation.idempotency_key_digest == idempotency_digest,
                )
                .with_for_update()
            ).one_or_none()
            if existing is not None:
                return _prepared_from_operation(
                    db, existing, request_digest=request_digest
                )

            open_lease = db.exec(
                select(CaptureSourceLease)
                .where(
                    CaptureSourceLease.practice_id == practice_id,
                    CaptureSourceLease.appointment_id == appointment_id,
                    cast(Any, CaptureSourceLease.closed_at).is_(None),
                )
                .with_for_update()
            ).one_or_none()
            if open_lease is not None:
                raise CaptureCoordinationConflict("capture_source_in_use")
            latest_generation = db.exec(
                select(CaptureSourceLease.generation)
                .where(
                    CaptureSourceLease.practice_id == practice_id,
                    CaptureSourceLease.appointment_id == appointment_id,
                )
                .order_by(cast(Any, CaptureSourceLease.generation).desc())
                .limit(1)
            ).one_or_none()
            generation = 0 if latest_generation is None else latest_generation + 1

            capture_session = CaptureSession(
                practice_id=practice_id,
                actor_id=actor_id,
                appointment_id=appointment_id,
                source_kind=DESKTOP_SOURCE,
                state="ready",
                consent_state="granted",
                scribe_consent_receipt_id=authorization.consent_evidence_id,
                consent_policy_version=authorization.consent_policy_version,
                processing_session_id=authorization.processing_session_id,
                authority_origin="server_authorized",
            )
            db.add(capture_session)
            db.flush()
            lease_nonce = secrets.token_urlsafe(32)
            source_lease = CaptureSourceLease(
                **_scope_values(capture_session),
                source_kind=DESKTOP_SOURCE,
                state="active",
                lease_token_digest=_digest(
                    lease_nonce, context="capture-source-lease-token-v1"
                ),
                generation=generation,
                acquired_at=current,
                heartbeat_at=current,
                control_deadline_at=current + INITIAL_LEASE_WINDOW,
            )
            operation = CaptureOperation(
                **_scope_values(capture_session),
                caller_scope=BROWSER_CALLER_SCOPE,
                caller_principal_digest=caller_digest,
                operation=CREATE_SESSION_OPERATION,
                idempotency_key_digest=idempotency_digest,
                request_digest=request_digest,
                state="succeeded",
                attempt_count=1,
                completed_at=current,
            )
            db.add(source_lease)
            db.add(operation)
            db.flush()
            _event(
                db,
                capture_session,
                event_type="capture.session_authorized",
                operation_id=operation.id,
                recorded_at=current,
            )
            db.commit()
            return PreparedCaptureSession(
                capture_session_id=capture_session.id,
                practice_id=practice_id,
                actor_id=actor_id,
                appointment_id=appointment_id,
                consent_evidence_id=authorization.consent_evidence_id,
                source_lease_id=source_lease.id,
                state_version=capture_session.state_version,
            )
        except CaptureCoordinationError:
            db.rollback()
            raise
        except IntegrityError:
            db.rollback()
            existing = db.exec(
                select(CaptureOperation).where(
                    CaptureOperation.practice_id == practice_id,
                    CaptureOperation.caller_scope == BROWSER_CALLER_SCOPE,
                    CaptureOperation.operation == CREATE_SESSION_OPERATION,
                    CaptureOperation.idempotency_key_digest == idempotency_digest,
                )
            ).one_or_none()
            if existing is not None:
                return _prepared_from_operation(
                    db, existing, request_digest=request_digest
                )
            raise CaptureCoordinationConflict("capture_request_conflict") from None
        except SQLAlchemyError:
            db.rollback()
            raise CaptureCoordinationUnavailable(
                "capture_storage_unavailable"
            ) from None


def _start_request_digest(
    *,
    practice_id: TypeIDType,
    actor_id: TypeIDType,
    capture_session_id: TypeIDType,
    source_lease_id: TypeIDType,
    consent_evidence_id: UUID,
    adapter_profile: str,
) -> str:
    return _canonical_digest(
        {
            "actor_id": str(actor_id),
            "adapter_profile": adapter_profile,
            "capture_session_id": str(capture_session_id),
            "consent_evidence_id": str(consent_evidence_id),
            "practice_id": str(practice_id),
            "source": DESKTOP_SOURCE,
            "source_lease_id": str(source_lease_id),
        },
        context="capture-start-request-v1",
    )


def _load_authorized_start_scope(
    db: Session,
    *,
    practice_id: TypeIDType,
    actor_id: TypeIDType,
    capture_session_id: TypeIDType,
    source_lease_id: TypeIDType,
    consent_evidence_id: UUID,
    current: datetime,
) -> tuple[CaptureSession, CaptureSourceLease]:
    capture_session = db.exec(
        select(CaptureSession)
        .where(
            CaptureSession.id == capture_session_id,
            CaptureSession.practice_id == practice_id,
            CaptureSession.actor_id == actor_id,
            CaptureSession.source_kind == DESKTOP_SOURCE,
        )
        .with_for_update()
    ).one_or_none()
    if capture_session is None:
        raise CaptureCoordinationDenied("capture_not_authorized")
    _practice_scope(
        db,
        practice_id=practice_id,
        actor_id=actor_id,
        appointment_id=capture_session.appointment_id,
    )
    source_lease = db.exec(
        select(CaptureSourceLease)
        .where(
            CaptureSourceLease.id == source_lease_id,
            CaptureSourceLease.session_id == capture_session.id,
            CaptureSourceLease.practice_id == practice_id,
            CaptureSourceLease.actor_id == actor_id,
            CaptureSourceLease.appointment_id == capture_session.appointment_id,
            CaptureSourceLease.source_kind == DESKTOP_SOURCE,
            CaptureSourceLease.state == "active",
            cast(Any, CaptureSourceLease.closed_at).is_(None),
        )
        .with_for_update()
    ).one_or_none()
    if (
        source_lease is None
        or capture_session.consent_state != "granted"
        or capture_session.scribe_consent_receipt_id != consent_evidence_id
        or capture_session.processing_session_id is None
        or capture_session.authority_origin != "server_authorized"
    ):
        raise CaptureCoordinationDenied("capture_not_authorized")
    if source_lease.control_deadline_at <= current:
        source_lease.state = "uncertain"
        db.add(source_lease)
        db.commit()
        raise CaptureCoordinationDenied("source_lease_stale")
    return capture_session, source_lease


def _decode_replay(
    operation: CaptureOperation,
    *,
    current: datetime,
) -> StartAuthority:
    if (
        operation.state != "succeeded"
        or operation.encrypted_replay_response is None
        or operation.replay_response_expires_at is None
        or operation.replay_response_expires_at <= current
    ):
        raise CaptureCoordinationConflict("capture_start_not_replayable")
    try:
        replay = decrypt_value(operation.encrypted_replay_response)
        if (
            operation.replay_response_digest is None
            or operation.replay_response_digest
            != _digest(replay, context="capture-start-replay-v1")
        ):
            raise ValueError
        raw = json.loads(replay)
        if not isinstance(raw, dict) or set(raw) != {
            "capture_session_id",
            "actor_id",
            "consent_record_id",
            "source_lease_id",
            "issued_at",
            "expires_at",
            "capability_id",
            "capability_secret",
        }:
            raise ValueError
        if not all(isinstance(value, str) for value in raw.values()):
            raise ValueError
        authority = StartAuthority(
            capture_session_id=raw["capture_session_id"],
            actor_id=raw["actor_id"],
            consent_record_id=raw["consent_record_id"],
            source_lease_id=raw["source_lease_id"],
            issued_at=datetime.fromisoformat(raw["issued_at"]),
            expires_at=datetime.fromisoformat(raw["expires_at"]),
            capability_id=raw["capability_id"],
            capability_secret=raw["capability_secret"],
        )
    except Exception:
        raise CaptureCoordinationUnavailable("capture_replay_unavailable") from None
    if (
        authority.issued_at.tzinfo is None
        or authority.expires_at.tzinfo is None
        or authority.expires_at <= current
        or not _CAPABILITY.fullmatch(authority.capability_secret)
    ):
        raise CaptureCoordinationConflict("capture_start_not_replayable")
    return authority


def _replay_has_terminal_capture_evidence(
    *,
    operation: CaptureOperation,
    capture_session: CaptureSession,
    source_lease: CaptureSourceLease,
    segment: CaptureSegment,
) -> bool:
    """Return true when replay cannot alter an already-advanced capture."""

    return (
        operation.encrypted_replay_response is None
        or operation.replay_response_purged_at is not None
        or segment.capability_consumed_at is not None
        or segment.capability_revoked_at is not None
        or segment.capability_purged_at is not None
        or segment.encrypted_capability is None
        or segment.physical_started_at is not None
        or segment.ended_at is not None
        or segment.state != "reserved"
        or capture_session.started_at is not None
        or capture_session.ended_at is not None
        or capture_session.state
        in {
            "recording",
            "stopping",
            "paused",
            "processing",
            "transcript_ready",
            "cancelled",
            "delivered",
        }
        or source_lease.state in {"closing", "closed"}
        or source_lease.stop_requested_at is not None
        or source_lease.stop_proved_at is not None
        or source_lease.never_started_proved_at is not None
        or source_lease.closed_at is not None
    )


def _revoke_start_replay_authority(
    db: Session,
    *,
    operation: CaptureOperation,
    capture_session: CaptureSession,
    source_lease: CaptureSourceLease,
    segment: CaptureSegment,
    current: datetime,
) -> None:
    """Atomically tombstone an unused capability whose authority changed."""

    segment.capability_revoked_at = current
    segment.encrypted_capability = None
    segment.capability_purged_at = current
    operation.encrypted_replay_response = None
    operation.replay_response_purged_at = current
    if capture_session.state != "review_required":
        capture_session.state = "review_required"
        capture_session.state_version += 1
    source_lease.state = "uncertain"
    db.add(segment)
    db.add(operation)
    db.add(capture_session)
    db.add(source_lease)
    _event(
        db,
        capture_session,
        event_type="capture.start_replay_authority_revoked",
        segment_id=segment.id,
        operation_id=operation.id,
        recorded_at=current,
    )
    db.commit()


def _replay_succeeded_start_locked(
    db: Session,
    *,
    operation_id: TypeIDType,
    expected_source_lease_id: TypeIDType | None,
    expected_request_digest: str | None,
    expected_caller_digest: str | None,
    adapter_profile: str,
    current: datetime,
) -> StartAuthority:
    """Replay one unused capability only after locked, live revalidation."""

    operation = db.exec(
        select(CaptureOperation)
        .where(
            CaptureOperation.id == operation_id,
            CaptureOperation.operation == START_SEGMENT_OPERATION,
        )
        .with_for_update()
    ).one_or_none()
    if (
        operation is None
        or expected_source_lease_id is None
        or expected_request_digest is None
        or expected_caller_digest is None
        or operation.state != "succeeded"
        or operation.caller_scope != DESKTOP_CALLER_SCOPE
        or not secrets.compare_digest(operation.request_digest, expected_request_digest)
        or not secrets.compare_digest(
            operation.caller_principal_digest, expected_caller_digest
        )
    ):
        raise CaptureCoordinationConflict("capture_start_not_replayable")
    capture_session = db.exec(
        select(CaptureSession)
        .where(
            CaptureSession.id == operation.session_id,
            CaptureSession.practice_id == operation.practice_id,
            CaptureSession.actor_id == operation.actor_id,
            CaptureSession.appointment_id == operation.appointment_id,
            CaptureSession.source_kind == DESKTOP_SOURCE,
        )
        .with_for_update()
    ).one_or_none()
    source_lease = db.exec(
        select(CaptureSourceLease)
        .where(
            CaptureSourceLease.id == expected_source_lease_id,
            CaptureSourceLease.session_id == operation.session_id,
            CaptureSourceLease.practice_id == operation.practice_id,
            CaptureSourceLease.actor_id == operation.actor_id,
            CaptureSourceLease.appointment_id == operation.appointment_id,
            CaptureSourceLease.source_kind == DESKTOP_SOURCE,
        )
        .with_for_update()
    ).one_or_none()
    segment = (
        None
        if operation.segment_id is None
        else db.exec(
            select(CaptureSegment)
            .where(
                CaptureSegment.id == operation.segment_id,
                CaptureSegment.session_id == operation.session_id,
                CaptureSegment.practice_id == operation.practice_id,
                CaptureSegment.actor_id == operation.actor_id,
                CaptureSegment.appointment_id == operation.appointment_id,
            )
            .with_for_update()
        ).one_or_none()
    )
    if capture_session is None or source_lease is None or segment is None:
        raise CaptureCoordinationConflict("capture_start_not_replayable")

    # A heartbeat/stop/purge is durable evidence that the one-time authority
    # has already advanced.  Never rewrite those terminal facts merely because
    # an old idempotency request was retried.
    if _replay_has_terminal_capture_evidence(
        operation=operation,
        capture_session=capture_session,
        source_lease=source_lease,
        segment=segment,
    ):
        raise CaptureCoordinationConflict("capture_start_not_replayable")

    capability_binding_is_complete = (
        segment.capability_id is not None
        and segment.capability_digest is not None
        and segment.capability_issued_at is not None
        and segment.capability_expires_at is not None
        and segment.capability_issued_at <= current
        and segment.capability_expires_at > current
        and operation.replay_response_digest is not None
        and operation.replay_response_expires_at is not None
        and operation.replay_response_expires_at > current
        and operation.replay_response_expires_at == segment.capability_expires_at
    )
    live_authority = _start_authority_is_current(
        db,
        operation=operation,
        capture_session=capture_session,
        source_lease=source_lease,
        adapter_profile=adapter_profile,
        current=current,
    )
    if not capability_binding_is_complete or not live_authority:
        if (
            segment.capability_issued_at is None
            or segment.capability_issued_at > current
        ):
            raise CaptureCoordinationConflict("capture_start_not_replayable")
        _revoke_start_replay_authority(
            db,
            operation=operation,
            capture_session=capture_session,
            source_lease=source_lease,
            segment=segment,
            current=current,
        )
        raise CaptureCoordinationDenied("capture_start_replay_authority_changed")

    authority = _decode_replay(operation, current=current)
    try:
        stored_capability = decrypt_value(cast(str, segment.encrypted_capability))
    except Exception:
        raise CaptureCoordinationUnavailable("capture_replay_unavailable") from None
    if (
        authority.capture_session_id != str(capture_session.id)
        or authority.actor_id != str(capture_session.actor_id)
        or authority.consent_record_id != str(capture_session.scribe_consent_receipt_id)
        or authority.source_lease_id != str(source_lease.id)
        or authority.capability_id != str(segment.capability_id)
        or authority.issued_at != segment.capability_issued_at
        or authority.expires_at != segment.capability_expires_at
        or not secrets.compare_digest(
            authority.capability_secret,
            stored_capability,
        )
        or not secrets.compare_digest(
            cast(str, segment.capability_digest),
            _digest(
                authority.capability_secret,
                context="capture-segment-capability-v1",
            ),
        )
    ):
        _revoke_start_replay_authority(
            db,
            operation=operation,
            capture_session=capture_session,
            source_lease=source_lease,
            segment=segment,
            current=current,
        )
        raise CaptureCoordinationDenied("capture_start_replay_authority_changed")
    return authority


def _existing_start_disposition(
    db: Session,
    operation: CaptureOperation,
    *,
    source_lease_id: TypeIDType,
    request_digest: str,
    caller_digest: str,
    adapter_profile: str,
    current: datetime,
) -> StartAuthority:
    if not secrets.compare_digest(
        operation.request_digest, request_digest
    ) or not secrets.compare_digest(
        operation.caller_principal_digest,
        caller_digest,
    ):
        raise CaptureCoordinationConflict("capture_request_conflict")
    if operation.state == "succeeded":
        return _replay_succeeded_start_locked(
            db,
            operation_id=operation.id,
            expected_source_lease_id=source_lease_id,
            expected_request_digest=request_digest,
            expected_caller_digest=caller_digest,
            adapter_profile=adapter_profile,
            current=current,
        )
    if operation.state in {"in_flight", "uncertain"}:
        raise CaptureOutcomeUncertain("capture_provider_outcome_uncertain")
    raise CaptureCoordinationConflict("capture_start_conflict")


def _reserve_segment_start(
    *,
    practice_id: TypeIDType,
    actor_id: TypeIDType,
    capture_session_id: TypeIDType,
    source_lease_id: TypeIDType,
    consent_evidence_id: UUID,
    caller_principal: str,
    idempotency_key: str,
    adapter_profile: str,
    current: datetime,
) -> _StartReservation | StartAuthority:
    key = _validate_idempotency_key(idempotency_key)
    if not caller_principal or len(caller_principal) > 4096:
        raise CaptureCoordinationDenied("capture_not_authorized")
    idempotency_digest = _digest(key, context="capture-start-idempotency-v1")
    caller_digest = _digest(caller_principal, context="capture-desktop-caller-v1")
    request_digest = _start_request_digest(
        practice_id=practice_id,
        actor_id=actor_id,
        capture_session_id=capture_session_id,
        source_lease_id=source_lease_id,
        consent_evidence_id=consent_evidence_id,
        adapter_profile=adapter_profile,
    )

    with get_session() as db:
        try:
            # Resolve a previously committed reservation before revalidating the
            # mutable session/lease state.  An uncertain provider outcome moves
            # both of those records out of their startable states deliberately;
            # retrying the same idempotency key must still return the recorded
            # uncertainty without making a second provider call.
            existing = db.exec(
                select(CaptureOperation)
                .where(
                    CaptureOperation.practice_id == practice_id,
                    CaptureOperation.caller_scope == DESKTOP_CALLER_SCOPE,
                    CaptureOperation.operation == START_SEGMENT_OPERATION,
                    CaptureOperation.idempotency_key_digest == idempotency_digest,
                )
                .with_for_update()
            ).one_or_none()
            if existing is not None:
                if existing.state == "in_flight":
                    existing.state = "uncertain"
                    existing.last_error_code = "provider_outcome_uncertain"
                    existing.next_attempt_at = current + RECONCILIATION_RETRY
                    db.add(existing)
                    db.commit()
                return _existing_start_disposition(
                    db,
                    existing,
                    source_lease_id=source_lease_id,
                    request_digest=request_digest,
                    caller_digest=caller_digest,
                    adapter_profile=adapter_profile,
                    current=current,
                )

            capture_session, source_lease = _load_authorized_start_scope(
                db,
                practice_id=practice_id,
                actor_id=actor_id,
                capture_session_id=capture_session_id,
                source_lease_id=source_lease_id,
                consent_evidence_id=consent_evidence_id,
                current=current,
            )
            existing = db.exec(
                select(CaptureOperation)
                .where(
                    CaptureOperation.practice_id == practice_id,
                    CaptureOperation.caller_scope == DESKTOP_CALLER_SCOPE,
                    CaptureOperation.operation == START_SEGMENT_OPERATION,
                    CaptureOperation.idempotency_key_digest == idempotency_digest,
                )
                .with_for_update()
            ).one_or_none()
            if existing is not None:
                if existing.state == "in_flight":
                    existing.state = "uncertain"
                    existing.last_error_code = "provider_outcome_uncertain"
                    existing.next_attempt_at = current + RECONCILIATION_RETRY
                    db.add(existing)
                    db.commit()
                return _existing_start_disposition(
                    db,
                    existing,
                    source_lease_id=source_lease_id,
                    request_digest=request_digest,
                    caller_digest=caller_digest,
                    adapter_profile=adapter_profile,
                    current=current,
                )
            if capture_session.state != "ready":
                raise CaptureCoordinationConflict("capture_session_not_ready")

            latest_ordinal = db.exec(
                select(CaptureSegment.ordinal)
                .where(CaptureSegment.session_id == capture_session.id)
                .order_by(cast(Any, CaptureSegment.ordinal).desc())
                .limit(1)
            ).one_or_none()
            ordinal = 0 if latest_ordinal is None else latest_ordinal + 1
            segment = CaptureSegment(
                **_scope_values(capture_session),
                ordinal=ordinal,
                channel="mixed",
                state="reserved",
            )
            db.add(segment)
            db.flush()
            correlation_ref = secrets.token_urlsafe(32)
            operation = CaptureOperation(
                **_scope_values(capture_session),
                segment_id=segment.id,
                caller_scope=DESKTOP_CALLER_SCOPE,
                caller_principal_digest=caller_digest,
                operation=START_SEGMENT_OPERATION,
                idempotency_key_digest=idempotency_digest,
                request_digest=request_digest,
                external_correlation_digest=_digest(
                    correlation_ref, context="capture-provider-correlation-v1"
                ),
                encrypted_external_correlation_ref=encrypt_value(correlation_ref),
                state="in_flight",
                attempt_count=1,
                last_attempt_at=current,
                next_attempt_at=current + PROVIDER_OPERATION_LEASE,
                lease_expires_at=current + PROVIDER_OPERATION_LEASE,
            )
            if capture_session.state != "starting":
                capture_session.state = "starting"
                capture_session.state_version += 1
            source_lease.heartbeat_at = current
            source_lease.control_deadline_at = current + ACTIVE_LEASE_WINDOW
            db.add(operation)
            db.add(capture_session)
            db.add(source_lease)
            db.flush()
            _event(
                db,
                capture_session,
                event_type="capture.segment_start_reserved",
                segment_id=segment.id,
                operation_id=operation.id,
                recorded_at=current,
            )
            db.commit()
            return _StartReservation(
                operation.id,
                correlation_ref,
                operation.attempt_count,
                source_lease.id,
                request_digest,
                caller_digest,
            )
        except CaptureCoordinationError:
            db.rollback()
            raise
        except IntegrityError:
            db.rollback()
            existing = db.exec(
                select(CaptureOperation)
                .where(
                    CaptureOperation.practice_id == practice_id,
                    CaptureOperation.caller_scope == DESKTOP_CALLER_SCOPE,
                    CaptureOperation.operation == START_SEGMENT_OPERATION,
                    CaptureOperation.idempotency_key_digest == idempotency_digest,
                )
                .with_for_update()
            ).one_or_none()
            if existing is not None:
                if existing.state == "in_flight":
                    existing.state = "uncertain"
                    existing.last_error_code = "provider_outcome_uncertain"
                    existing.next_attempt_at = current + RECONCILIATION_RETRY
                    db.add(existing)
                    db.commit()
                return _existing_start_disposition(
                    db,
                    existing,
                    source_lease_id=source_lease_id,
                    request_digest=request_digest,
                    caller_digest=caller_digest,
                    adapter_profile=adapter_profile,
                    current=current,
                )
            raise CaptureCoordinationConflict("capture_start_conflict") from None
        except SQLAlchemyError:
            db.rollback()
            raise CaptureCoordinationUnavailable(
                "capture_storage_unavailable"
            ) from None


def _bounded_provider_result(
    result: CaptureProvisioning | CaptureObservation,
) -> CaptureProvisioning:
    if (
        _PROVIDER_REF.fullmatch(result.capture_container_ref) is None
        or _CAPABILITY.fullmatch(result.capture_capability) is None
        or (
            result.recording_ref is not None
            and _PROVIDER_REF.fullmatch(result.recording_ref) is None
        )
        or result.status
        not in {
            "pending",
            "recording_started",
            "recording_ended",
            "uploading",
            "complete",
            "failed",
        }
    ):
        raise CaptureOutcomeUncertain("capture_provider_response_invalid")
    return CaptureProvisioning(
        capture_container_ref=result.capture_container_ref,
        capture_capability=result.capture_capability,
        status=result.status,
        recording_ref=result.recording_ref,
    )


def _load_start_records(
    db: Session, operation_id: TypeIDType
) -> tuple[CaptureOperation, CaptureSession, CaptureSourceLease, CaptureSegment]:
    operation = db.exec(
        select(CaptureOperation)
        .where(
            CaptureOperation.id == operation_id,
            CaptureOperation.operation == START_SEGMENT_OPERATION,
        )
        .with_for_update()
    ).one_or_none()
    if operation is None or operation.segment_id is None:
        raise CaptureCoordinationUnavailable("capture_operation_unavailable")
    capture_session = db.exec(
        select(CaptureSession)
        .where(CaptureSession.id == operation.session_id)
        .with_for_update()
    ).one()
    source_lease = db.exec(
        select(CaptureSourceLease)
        .where(
            CaptureSourceLease.session_id == operation.session_id,
            CaptureSourceLease.practice_id == operation.practice_id,
            CaptureSourceLease.appointment_id == operation.appointment_id,
            cast(Any, CaptureSourceLease.closed_at).is_(None),
        )
        .with_for_update()
    ).one()
    segment = db.exec(
        select(CaptureSegment)
        .where(CaptureSegment.id == operation.segment_id)
        .with_for_update()
    ).one()
    return operation, capture_session, source_lease, segment


def _start_authority_is_current(
    db: Session,
    *,
    operation: CaptureOperation,
    capture_session: CaptureSession,
    source_lease: CaptureSourceLease,
    adapter_profile: str,
    current: datetime,
    recovery_claim: bool = False,
) -> bool:
    """Revalidate every mutable authority fact after an external call."""

    try:
        _practice_scope(
            db,
            practice_id=operation.practice_id,
            actor_id=operation.actor_id,
            appointment_id=operation.appointment_id,
        )
    except CaptureCoordinationDenied:
        return False
    consent_evidence_id = capture_session.scribe_consent_receipt_id
    if consent_evidence_id is None:
        return False
    expected_request_digest = _start_request_digest(
        practice_id=operation.practice_id,
        actor_id=operation.actor_id,
        capture_session_id=operation.session_id,
        source_lease_id=source_lease.id,
        consent_evidence_id=consent_evidence_id,
        adapter_profile=adapter_profile,
    )
    allowed_session_states = {"starting", "failed"} if recovery_claim else {"starting"}
    allowed_lease_states = {"active", "uncertain"} if recovery_claim else {"active"}
    return (
        capture_session.id == operation.session_id
        and capture_session.practice_id == operation.practice_id
        and capture_session.actor_id == operation.actor_id
        and capture_session.appointment_id == operation.appointment_id
        and capture_session.source_kind == DESKTOP_SOURCE
        and capture_session.state in allowed_session_states
        and capture_session.consent_state == "granted"
        and capture_session.processing_session_id is not None
        and capture_session.authority_origin == "server_authorized"
        and capture_session.started_at is None
        and capture_session.ended_at is None
        and source_lease.session_id == operation.session_id
        and source_lease.practice_id == operation.practice_id
        and source_lease.actor_id == operation.actor_id
        and source_lease.appointment_id == operation.appointment_id
        and source_lease.source_kind == DESKTOP_SOURCE
        and source_lease.state in allowed_lease_states
        and source_lease.closed_at is None
        and source_lease.control_deadline_at > current
        and secrets.compare_digest(operation.request_digest, expected_request_digest)
    )


def _fail_start_after_authority_change(
    db: Session,
    *,
    operation: CaptureOperation,
    capture_session: CaptureSession,
    source_lease: CaptureSourceLease,
    segment: CaptureSegment,
    current: datetime,
) -> None:
    operation.state = "failed"
    operation.completed_at = current
    operation.last_error_code = "capture_authority_changed_after_provider"
    operation.next_attempt_at = None
    operation.lease_expires_at = None
    operation.encrypted_external_correlation_ref = None
    operation.correlation_purged_at = current
    segment.state = "failed"
    segment.state_version += 1
    if capture_session.state != "stopping":
        capture_session.state = "review_required"
        capture_session.state_version += 1
    if source_lease.state != "closing":
        source_lease.state = "uncertain"
    db.add(operation)
    db.add(segment)
    db.add(capture_session)
    db.add(source_lease)
    _event(
        db,
        capture_session,
        event_type="capture.authority_changed_after_provider",
        segment_id=segment.id,
        operation_id=operation.id,
        recorded_at=current,
    )


def _start_authority_payload(
    *,
    capture_session: CaptureSession,
    source_lease: CaptureSourceLease,
    issued_at: datetime,
    expires_at: datetime,
    capability_id: UUID,
    capability_secret: str,
) -> StartAuthority:
    consent_id = capture_session.scribe_consent_receipt_id
    if consent_id is None:
        raise CaptureCoordinationUnavailable("capture_authority_unavailable")
    return StartAuthority(
        capture_session_id=str(capture_session.id),
        actor_id=str(capture_session.actor_id),
        consent_record_id=str(consent_id),
        source_lease_id=str(source_lease.id),
        issued_at=issued_at,
        expires_at=expires_at,
        capability_id=str(capability_id),
        capability_secret=capability_secret,
    )


def _encode_replay(authority: StartAuthority) -> str:
    return json.dumps(
        {
            "capture_session_id": authority.capture_session_id,
            "actor_id": authority.actor_id,
            "consent_record_id": authority.consent_record_id,
            "source_lease_id": authority.source_lease_id,
            "issued_at": authority.issued_at.isoformat(),
            "expires_at": authority.expires_at.isoformat(),
            "capability_id": authority.capability_id,
            "capability_secret": authority.capability_secret,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _resource(
    *,
    capture_session: CaptureSession,
    segment: CaptureSegment,
    operation: CaptureOperation,
    role: Literal["capture_container", "recording_media"],
    external_ref: str,
    adapter_profile: str,
    current: datetime,
) -> CaptureExternalResource:
    return CaptureExternalResource(
        **_scope_values(capture_session),
        segment_id=segment.id,
        create_operation_id=operation.id,
        adapter_profile=adapter_profile,
        role=role,
        external_ref_digest=_digest(
            external_ref, context=f"capture-external-{role}-v1"
        ),
        encrypted_external_ref=encrypt_value(external_ref),
        mapping_state="active",
        cleanup_state="pending",
        cleanup_policy="retain_until_expiry",
        cleanup_due_at=current + EXTERNAL_RETENTION_WINDOW,
    )


def _finalize_segment_start(
    *,
    operation_id: TypeIDType,
    result: CaptureProvisioning | CaptureObservation,
    adapter_profile: str,
    expected_attempt: int,
    current: datetime,
    replay_source_lease_id: TypeIDType | None = None,
    replay_request_digest: str | None = None,
    replay_caller_digest: str | None = None,
) -> StartAuthority:
    if _ADAPTER_PROFILE.fullmatch(adapter_profile) is None:
        raise CaptureCoordinationUnavailable("capture_adapter_unavailable")
    provisioning = _bounded_provider_result(result)
    with get_session() as db:
        try:
            existing_operation = db.exec(
                select(CaptureOperation)
                .where(
                    CaptureOperation.id == operation_id,
                    CaptureOperation.operation == START_SEGMENT_OPERATION,
                )
                .with_for_update()
            ).one_or_none()
            if existing_operation is None:
                raise CaptureCoordinationUnavailable("capture_operation_unavailable")
            if existing_operation.state == "succeeded":
                return _replay_succeeded_start_locked(
                    db,
                    operation_id=operation_id,
                    expected_source_lease_id=replay_source_lease_id,
                    expected_request_digest=replay_request_digest,
                    expected_caller_digest=replay_caller_digest,
                    adapter_profile=adapter_profile,
                    current=current,
                )
            operation, capture_session, source_lease, segment = _load_start_records(
                db, operation_id
            )
            if (
                operation.state not in {"in_flight", "uncertain"}
                or operation.attempt_count != expected_attempt
            ):
                raise CaptureOutcomeUncertain("capture_provider_outcome_uncertain")

            # Once an external identifier is known, preserve its encrypted
            # cleanup handle even when its state is not safe for issuing a
            # fresh desktop capability.
            db.add(
                _resource(
                    capture_session=capture_session,
                    segment=segment,
                    operation=operation,
                    role="capture_container",
                    external_ref=provisioning.capture_container_ref,
                    adapter_profile=adapter_profile,
                    current=current,
                )
            )
            if provisioning.recording_ref is not None:
                db.add(
                    _resource(
                        capture_session=capture_session,
                        segment=segment,
                        operation=operation,
                        role="recording_media",
                        external_ref=provisioning.recording_ref,
                        adapter_profile=adapter_profile,
                        current=current,
                    )
                )

            if not _start_authority_is_current(
                db,
                operation=operation,
                capture_session=capture_session,
                source_lease=source_lease,
                adapter_profile=adapter_profile,
                current=current,
            ):
                _fail_start_after_authority_change(
                    db,
                    operation=operation,
                    capture_session=capture_session,
                    source_lease=source_lease,
                    segment=segment,
                    current=current,
                )
                db.commit()
                raise CaptureCoordinationDenied(
                    "capture_authority_changed_after_provider"
                )

            if provisioning.status != "pending":
                operation.state = "failed"
                operation.completed_at = current
                operation.last_error_code = (
                    "provider_capture_failed"
                    if provisioning.status == "failed"
                    else "provider_state_requires_review"
                )
                operation.next_attempt_at = None
                operation.lease_expires_at = None
                operation.encrypted_external_correlation_ref = None
                operation.correlation_purged_at = current
                segment.state = "failed"
                segment.state_version += 1
                capture_session.state = "review_required"
                capture_session.state_version += 1
                source_lease.state = "uncertain"
                db.add(operation)
                db.add(segment)
                db.add(capture_session)
                db.add(source_lease)
                _event(
                    db,
                    capture_session,
                    event_type=(
                        "capture.provider_failed"
                        if provisioning.status == "failed"
                        else "capture.provider_state_requires_review"
                    ),
                    segment_id=segment.id,
                    operation_id=operation.id,
                    recorded_at=current,
                )
                db.commit()
                raise CaptureCoordinationConflict("capture_review_required")

            capability_id = uuid4()
            expires_at = current + CAPABILITY_WINDOW
            authority = _start_authority_payload(
                capture_session=capture_session,
                source_lease=source_lease,
                issued_at=current,
                expires_at=expires_at,
                capability_id=capability_id,
                capability_secret=provisioning.capture_capability,
            )
            replay = _encode_replay(authority)
            segment.capability_id = capability_id
            segment.capability_digest = _digest(
                provisioning.capture_capability,
                context="capture-segment-capability-v1",
            )
            segment.encrypted_capability = encrypt_value(
                provisioning.capture_capability
            )
            segment.capability_issued_at = current
            segment.capability_expires_at = expires_at
            operation.state = "succeeded"
            operation.completed_at = current
            operation.last_error_code = None
            operation.next_attempt_at = None
            operation.lease_expires_at = None
            operation.encrypted_external_correlation_ref = None
            operation.correlation_purged_at = current
            operation.replay_response_digest = _digest(
                replay, context="capture-start-replay-v1"
            )
            operation.encrypted_replay_response = encrypt_value(replay)
            operation.replay_response_expires_at = expires_at
            if capture_session.state != "starting":
                capture_session.state = "starting"
                capture_session.state_version += 1
            source_lease.state = "active"
            source_lease.heartbeat_at = current
            source_lease.control_deadline_at = current + ACTIVE_LEASE_WINDOW
            db.add(operation)
            db.add(segment)
            db.add(capture_session)
            db.add(source_lease)
            _event(
                db,
                capture_session,
                event_type="capture.capability_issued",
                segment_id=segment.id,
                operation_id=operation.id,
                recorded_at=current,
            )
            db.commit()
            return authority
        except CaptureCoordinationError:
            db.rollback()
            raise
        except (IntegrityError, SQLAlchemyError):
            db.rollback()
            raise CaptureOutcomeUncertain(
                "capture_provider_outcome_uncertain"
            ) from None
        except Exception:
            db.rollback()
            raise CaptureOutcomeUncertain(
                "capture_provider_outcome_uncertain"
            ) from None


def _mark_start_failure(
    operation_id: TypeIDType,
    *,
    expected_attempt: int,
    error_code: str,
    uncertain: bool,
    current: datetime,
) -> None:
    with get_session() as db:
        try:
            operation, capture_session, source_lease, segment = _load_start_records(
                db, operation_id
            )
            if (
                operation.state in {"succeeded", "failed"}
                or operation.attempt_count != expected_attempt
            ):
                return
            if uncertain:
                operation.state = "uncertain"
                operation.next_attempt_at = current + RECONCILIATION_RETRY
                operation.lease_expires_at = None
                capture_session.state = "failed"
                source_lease.state = "uncertain"
            else:
                operation.state = "failed"
                operation.completed_at = current
                operation.next_attempt_at = None
                operation.lease_expires_at = None
                segment.state = "failed"
                segment.state_version += 1
                capture_session.state = "ready"
            operation.last_error_code = error_code
            capture_session.state_version += 1
            db.add(operation)
            db.add(segment)
            db.add(capture_session)
            db.add(source_lease)
            _event(
                db,
                capture_session,
                event_type=(
                    "capture.provider_outcome_uncertain"
                    if uncertain
                    else "capture.provider_rejected"
                ),
                segment_id=segment.id,
                operation_id=operation.id,
                recorded_at=current,
            )
            db.commit()
        except SQLAlchemyError:
            db.rollback()


def authorize_segment_start(
    *,
    provider: CaptureProvider,
    practice_id: TypeIDType,
    actor_id: TypeIDType,
    capture_session_id: TypeIDType,
    source_lease_id: TypeIDType,
    consent_evidence_id: UUID,
    caller_principal: str,
    idempotency_key: str,
    adapter_profile: str = DESKTOP_ADAPTER_PROFILE,
    now: datetime | None = None,
) -> StartAuthority:
    """Reserve, externally provision, and durably map one physical start."""

    current = _now(now)
    if _ADAPTER_PROFILE.fullmatch(adapter_profile) is None:
        raise CaptureCoordinationUnavailable("capture_adapter_unavailable")
    reservation = _reserve_segment_start(
        practice_id=practice_id,
        actor_id=actor_id,
        capture_session_id=capture_session_id,
        source_lease_id=source_lease_id,
        consent_evidence_id=consent_evidence_id,
        caller_principal=caller_principal,
        idempotency_key=idempotency_key,
        adapter_profile=adapter_profile,
        current=current,
    )
    if isinstance(reservation, StartAuthority):
        return reservation
    try:
        result = provider.create_capture(correlation_ref=reservation.correlation_ref)
    except CaptureProviderDisabled:
        _mark_start_failure(
            reservation.operation_id,
            expected_attempt=reservation.attempt_generation,
            error_code="provider_disabled",
            uncertain=False,
            current=current,
        )
        raise CaptureCoordinationUnavailable("capture_provider_disabled") from None
    except CaptureProviderRejected:
        _mark_start_failure(
            reservation.operation_id,
            expected_attempt=reservation.attempt_generation,
            error_code="provider_rejected",
            uncertain=False,
            current=current,
        )
        raise CaptureCoordinationDenied("capture_provider_rejected") from None
    except CaptureProviderOutcomeUncertain:
        _mark_start_failure(
            reservation.operation_id,
            expected_attempt=reservation.attempt_generation,
            error_code="provider_outcome_uncertain",
            uncertain=True,
            current=current,
        )
        raise CaptureOutcomeUncertain("capture_provider_outcome_uncertain") from None
    except Exception:
        _mark_start_failure(
            reservation.operation_id,
            expected_attempt=reservation.attempt_generation,
            error_code="provider_outcome_uncertain",
            uncertain=True,
            current=current,
        )
        raise CaptureOutcomeUncertain("capture_provider_outcome_uncertain") from None
    # A supplied clock is a deterministic test/audit boundary. Production
    # calls pass ``None`` and therefore sample the clock again after provider
    # I/O for the post-call authority check.
    finalized_at = _now(now)
    try:
        return _finalize_segment_start(
            operation_id=reservation.operation_id,
            result=result,
            adapter_profile=adapter_profile,
            expected_attempt=reservation.attempt_generation,
            current=finalized_at,
            replay_source_lease_id=reservation.source_lease_id,
            replay_request_digest=reservation.request_digest,
            replay_caller_digest=reservation.caller_digest,
        )
    except CaptureOutcomeUncertain:
        _mark_start_failure(
            reservation.operation_id,
            expected_attempt=reservation.attempt_generation,
            error_code="provider_outcome_uncertain",
            uncertain=True,
            current=finalized_at,
        )
        raise


def reconcile_segment_start(
    *,
    provider: CaptureProvider,
    operation_id: TypeIDType,
    adapter_profile: str = DESKTOP_ADAPTER_PROFILE,
    now: datetime | None = None,
) -> StartAuthority:
    """Resolve one uncertain create by bounded correlation lookup only."""

    current = _now(now)
    with get_session() as db:
        try:
            existing_operation = db.exec(
                select(CaptureOperation)
                .where(
                    CaptureOperation.id == operation_id,
                    CaptureOperation.operation == START_SEGMENT_OPERATION,
                )
                .with_for_update()
            ).one_or_none()
            if existing_operation is None:
                raise CaptureCoordinationUnavailable("capture_operation_unavailable")
            if existing_operation.state == "succeeded":
                return _replay_succeeded_start_locked(
                    db,
                    operation_id=operation_id,
                    expected_source_lease_id=None,
                    expected_request_digest=None,
                    expected_caller_digest=None,
                    adapter_profile=adapter_profile,
                    current=current,
                )
            operation, capture_session, source_lease, _ = _load_start_records(
                db, operation_id
            )
            if operation.state == "in_flight":
                if (
                    operation.lease_expires_at is not None
                    and operation.lease_expires_at > current
                ):
                    raise CaptureOutcomeUncertain("capture_provider_outcome_uncertain")
                operation.state = "uncertain"
                operation.last_error_code = "provider_operation_lease_expired"
                operation.lease_expires_at = None
            if operation.state != "uncertain":
                raise CaptureCoordinationConflict("capture_start_not_uncertain")
            if (
                operation.next_attempt_at is not None
                and operation.next_attempt_at > current
            ):
                raise CaptureOutcomeUncertain("capture_provider_outcome_uncertain")
            if operation.attempt_count >= operation.max_attempts:
                operation.state = "failed"
                operation.completed_at = current
                operation.last_error_code = "provider_reconciliation_exhausted"
                operation.next_attempt_at = None
                operation.lease_expires_at = None
                capture_session.state = "review_required"
                capture_session.state_version += 1
                source_lease.state = "uncertain"
                db.add(operation)
                db.add(capture_session)
                db.add(source_lease)
                _event(
                    db,
                    capture_session,
                    event_type="capture.reconciliation_exhausted",
                    operation_id=operation.id,
                    recorded_at=current,
                )
                db.commit()
                raise CaptureCoordinationConflict("capture_review_required")
            if operation.encrypted_external_correlation_ref is None:
                raise CaptureCoordinationUnavailable("capture_correlation_unavailable")
            correlation_ref = decrypt_value(
                operation.encrypted_external_correlation_ref
            )
            operation.attempt_count += 1
            attempt_generation = operation.attempt_count
            operation.last_attempt_at = current
            operation.state = "in_flight"
            operation.next_attempt_at = current + PROVIDER_OPERATION_LEASE
            operation.lease_expires_at = current + PROVIDER_OPERATION_LEASE
            if _start_authority_is_current(
                db,
                operation=operation,
                capture_session=capture_session,
                source_lease=source_lease,
                adapter_profile=adapter_profile,
                current=current,
                recovery_claim=True,
            ):
                if capture_session.state != "starting":
                    capture_session.state = "starting"
                    capture_session.state_version += 1
                source_lease.state = "active"
                source_lease.heartbeat_at = current
                source_lease.control_deadline_at = current + PROVIDER_OPERATION_LEASE
            db.add(operation)
            db.add(capture_session)
            db.add(source_lease)
            db.commit()
        except CaptureCoordinationError:
            db.rollback()
            raise
        except Exception:
            db.rollback()
            raise CaptureCoordinationUnavailable(
                "capture_reconciliation_unavailable"
            ) from None

    try:
        matches = provider.find_captures(correlation_ref=correlation_ref)
    except Exception:
        failed_at = _now(now)
        _mark_start_failure(
            operation_id,
            expected_attempt=attempt_generation,
            error_code="provider_reconciliation_unavailable",
            uncertain=True,
            current=failed_at,
        )
        raise CaptureOutcomeUncertain("capture_provider_outcome_uncertain") from None
    # Re-sample after provider I/O.  The recovery claim above is only a bounded
    # lookup lease; a slow lookup must not use its stale pre-call timestamp to
    # mint a capability after that lease has expired.
    finalized_at = _now(now)
    if not matches:
        _mark_start_failure(
            operation_id,
            expected_attempt=attempt_generation,
            error_code="provider_resource_not_observed",
            uncertain=True,
            current=finalized_at,
        )
        raise CaptureOutcomeUncertain("capture_provider_outcome_uncertain")
    if len(matches) != 1:
        with get_session() as db:
            operation, capture_session, source_lease, _ = _load_start_records(
                db, operation_id
            )
            if (
                operation.state not in {"in_flight", "uncertain"}
                or operation.attempt_count != attempt_generation
            ):
                raise CaptureOutcomeUncertain("capture_provider_outcome_uncertain")
            operation.state = "failed"
            operation.completed_at = finalized_at
            operation.last_error_code = "provider_multiple_matches"
            operation.next_attempt_at = None
            operation.lease_expires_at = None
            capture_session.state = "review_required"
            capture_session.state_version += 1
            source_lease.state = "uncertain"
            db.add(operation)
            db.add(capture_session)
            db.add(source_lease)
            _event(
                db,
                capture_session,
                event_type="capture.provider_multiple_matches",
                operation_id=operation.id,
                recorded_at=finalized_at,
            )
            db.commit()
        raise CaptureCoordinationConflict("capture_review_required")
    return _finalize_segment_start(
        operation_id=operation_id,
        result=matches[0],
        adapter_profile=adapter_profile,
        expected_attempt=attempt_generation,
        current=finalized_at,
    )


def discover_due_segment_start_operations(
    *,
    now: datetime | None = None,
    limit: int = MAX_RECONCILIATION_SCAN_BATCH,
) -> tuple[TypeIDType, ...]:
    """Return a bounded recovery batch without exposing operation context.

    Discovery uses ``SKIP LOCKED`` so parallel maintenance tasks partition the
    scan while their transactions are open.  ``reconcile_segment_start`` then
    takes the authoritative row lock and installs the durable provider-call
    lease/attempt fence before it performs a lookup.  Consequently, duplicate
    discovery after this short transaction cannot cause duplicate provider
    calls or an unfenced state transition.
    """

    if not 1 <= limit <= MAX_RECONCILIATION_SCAN_BATCH:
        raise ValueError("capture reconciliation batch is outside its bound")
    current = _now(now)
    with get_session() as db:
        operations = db.exec(
            select(CaptureOperation)
            .where(
                CaptureOperation.operation == START_SEGMENT_OPERATION,
                or_(
                    and_(
                        cast(Any, CaptureOperation.state) == "uncertain",
                        or_(
                            CaptureOperation.next_attempt_at.is_(None),  # type: ignore[attr-defined]
                            cast(Any, CaptureOperation.next_attempt_at) <= current,
                        ),
                    ),
                    and_(
                        cast(Any, CaptureOperation.state) == "in_flight",
                        or_(
                            CaptureOperation.lease_expires_at.is_(None),  # type: ignore[attr-defined]
                            cast(Any, CaptureOperation.lease_expires_at) <= current,
                        ),
                    ),
                ),
            )
            .order_by(
                func.coalesce(
                    CaptureOperation.next_attempt_at,
                    CaptureOperation.lease_expires_at,
                    CaptureOperation.created_at,
                ),
                cast(Any, CaptureOperation.created_at),
            )
            .limit(limit)
            .with_for_update(skip_locked=True)
        ).all()
        operation_ids = tuple(operation.id for operation in operations)
        db.commit()
        return operation_ids


def mark_due_cleanup_blocked(
    *,
    now: datetime | None = None,
    limit: int = MAX_CLEANUP_SCAN_BATCH,
) -> CleanupVisibility:
    """Make overdue provider cleanup visible without inventing delete proof.

    The current provider boundary has no delete/absence operation.  Reaching a
    retention deadline therefore records an explicit operator-action state;
    it never clears the encrypted recovery handle or claims that the external
    resource is gone.  A future adapter with a proof-bearing delete contract
    can safely recover these ``failed`` rows.
    """

    if not 1 <= limit <= MAX_CLEANUP_SCAN_BATCH:
        raise ValueError("capture cleanup batch is outside its bound")
    current = _now(now)
    with get_session() as db:
        resources = db.exec(
            select(CaptureExternalResource)
            .where(
                cast(Any, CaptureExternalResource.cleanup_due_at) <= current,
                CaptureExternalResource.encrypted_external_ref.is_not(None),  # type: ignore[attr-defined]
                CaptureExternalResource.reference_purged_at.is_(None),  # type: ignore[attr-defined]
                cast(Any, CaptureExternalResource.cleanup_state).in_(
                    ["not_requested", "pending", "uncertain"]
                ),
            )
            .order_by(
                cast(Any, CaptureExternalResource.cleanup_due_at),
                cast(Any, CaptureExternalResource.created_at),
            )
            .limit(limit)
            .with_for_update(skip_locked=True)
        ).all()
        for resource in resources:
            resource.cleanup_state = "failed"
            resource.last_cleanup_error_code = _CLEANUP_BLOCKED_CODE
            if resource.cleanup_requested_at is None:
                resource.cleanup_requested_at = current
            db.add(resource)
        db.commit()

        blocked_due = db.exec(
            select(func.count())
            .select_from(CaptureExternalResource)
            .where(
                cast(Any, CaptureExternalResource.cleanup_due_at) <= current,
                CaptureExternalResource.encrypted_external_ref.is_not(None),  # type: ignore[attr-defined]
                CaptureExternalResource.reference_purged_at.is_(None),  # type: ignore[attr-defined]
                CaptureExternalResource.cleanup_state != "succeeded",
            )
        ).one()
        return CleanupVisibility(
            newly_blocked=len(resources),
            blocked_due=int(blocked_due),
        )
