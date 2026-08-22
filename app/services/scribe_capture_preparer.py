"""Server-owned consent and Scribe-session preparation for desktop capture.

The browser supplies only a bounded consent attestation.  Tenant, clinician,
appointment, and patient identifiers are resolved from the authenticated
platform scope before Scribe can be called.  Scribe receipts are then checked
against that exact scope before they become local capture authority.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID

from app.configuration.scribe_workflow import (
    SCRIBE_WORKFLOW_SETTINGS,
    ScribeWorkflowSettings,
)
from app.services.capture_coordination import (
    CaptureCoordinationConflict,
    CaptureCoordinationDenied,
    CaptureCoordinationUnavailable,
    CaptureOutcomeUncertain,
    CaptureSessionPreparationReservation,
    PreparedCaptureSession,
    ScribeCaptureAuthorization,
    finalize_capture_session_preparation,
    record_capture_session_preparation_failure,
    reserve_capture_session_preparation,
)
from app.services.capture_launch_auth import (
    AuthorizedCaptureSessionReference,
    CaptureConsentAttestation,
    CaptureLaunchConflict,
    CaptureLaunchDenied,
    CaptureLaunchUnavailable,
)
from app.services.capture_lifecycle import (
    CaptureLifecycleUnavailable,
    release_expired_never_started_session,
)
from app.services.scribe_workflow import (
    CaptureSession as ScribeCaptureSession,
)
from app.services.scribe_workflow import (
    ConsentCreate,
    ConsentRecord,
    ScribeWorkflowClient,
    ScribeWorkflowConflictError,
    ScribeWorkflowDisabledError,
    ScribeWorkflowRejectedError,
    ScribeWorkflowRetryableError,
    SessionCreate,
    build_scribe_workflow_client,
)
from app.utils.encryption import pseudonymous_digest

from activemodel.session_manager import get_session
from activemodel.types import TypeIDType
from app.models.appointment import Appointment
from app.models.doctor import Doctor, DoctorRole
from app.models.patient import Patient
from app.models.practice import Practice, PracticeMember
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import select

CONSENT_ATTESTATION_MAX_AGE = timedelta(hours=24)
CONSENT_ATTESTATION_MAX_FUTURE_SKEW = timedelta(minutes=2)
MAX_PARTICIPANT_REGIONS = 8
EXTERNAL_INGEST_MODE = "external_async_transcript"

_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9._~-]{8,255}$")
_POLICY_VERSION = re.compile(r"^[A-Za-z0-9._~-]{1,32}$")
_US_JURISDICTIONS = frozenset(
    {
        "AK",
        "AL",
        "AR",
        "AS",
        "AZ",
        "CA",
        "CO",
        "CT",
        "DC",
        "DE",
        "FL",
        "GA",
        "GU",
        "HI",
        "IA",
        "ID",
        "IL",
        "IN",
        "KS",
        "KY",
        "LA",
        "MA",
        "MD",
        "ME",
        "MI",
        "MN",
        "MO",
        "MP",
        "MS",
        "MT",
        "NC",
        "ND",
        "NE",
        "NH",
        "NJ",
        "NM",
        "NV",
        "NY",
        "OH",
        "OK",
        "OR",
        "PA",
        "PR",
        "RI",
        "SC",
        "SD",
        "TN",
        "TX",
        "UT",
        "VA",
        "VI",
        "VT",
        "WA",
        "WI",
        "WV",
        "WY",
    }
)


@dataclass(frozen=True)
class _DerivedCaptureScope:
    tenant_id: UUID
    practice_id: TypeIDType
    actor_id: TypeIDType
    appointment_id: TypeIDType
    patient_id: TypeIDType


def _now(value: datetime | None = None) -> datetime:
    current = value or datetime.now(UTC)
    if current.tzinfo is None or current.utcoffset() is None:
        raise CaptureLaunchDenied("launch_not_authorized")
    return current.astimezone(UTC)


def _valid_region(value: str) -> bool:
    if value in _US_JURISDICTIONS:
        return True
    return value.startswith("US-") and value[3:] in _US_JURISDICTIONS


def _validate_attestation(
    attestation: CaptureConsentAttestation,
    *,
    current: datetime,
) -> CaptureConsentAttestation:
    if (
        attestation.modality != "virtual"
        or attestation.method
        not in {"pre_visit_form", "verbal_attestation", "phone_ivr"}
        or attestation.all_parties_consented is not True
        or not 1 <= len(attestation.participant_regions) <= MAX_PARTICIPANT_REGIONS
        or any(not _valid_region(region) for region in attestation.participant_regions)
        or attestation.granted_at.tzinfo is None
        or attestation.granted_at.utcoffset() is None
    ):
        raise CaptureLaunchDenied("launch_not_authorized")

    granted_at = attestation.granted_at.astimezone(UTC)
    if not (
        current - CONSENT_ATTESTATION_MAX_AGE
        <= granted_at
        <= current + CONSENT_ATTESTATION_MAX_FUTURE_SKEW
    ):
        raise CaptureLaunchDenied("launch_not_authorized")
    return CaptureConsentAttestation(
        modality=attestation.modality,
        participant_regions=frozenset(attestation.participant_regions),
        method=attestation.method,
        all_parties_consented=True,
        granted_at=granted_at,
    )


def _typeid_uuid(value: TypeIDType) -> UUID:
    candidate = getattr(value, "uuid", None)
    if not isinstance(candidate, UUID):
        raise CaptureLaunchUnavailable("capture_session_preparation_unavailable")
    return candidate


def _derive_capture_scope(
    *,
    actor_id: TypeIDType,
    practice_id: TypeIDType,
    appointment_id: TypeIDType,
) -> _DerivedCaptureScope:
    try:
        with get_session() as db:
            doctor = db.exec(
                select(Doctor).where(
                    Doctor.id == actor_id,
                    Doctor.role == DoctorRole.normal,
                    cast(Any, Doctor.deleted_at).is_(None),
                )
            ).one_or_none()
            practice = db.exec(
                select(Practice).where(Practice.id == practice_id)
            ).one_or_none()
            membership = db.exec(
                select(PracticeMember).where(
                    PracticeMember.practice_id == practice_id,
                    PracticeMember.doctor_id == actor_id,
                    cast(Any, PracticeMember.attested_at).is_not(None),
                )
            ).one_or_none()
            appointment = db.exec(
                select(Appointment).where(
                    Appointment.id == appointment_id,
                    Appointment.doctor_id == actor_id,
                    cast(Any, Appointment.cancelled_at).is_(None),
                )
            ).one_or_none()
            patient = (
                None
                if appointment is None
                else db.exec(
                    select(Patient).where(
                        Patient.id == appointment.patient_id,
                        Patient.doctor_id == actor_id,
                    )
                ).one_or_none()
            )
    except SQLAlchemyError:
        raise CaptureLaunchUnavailable(
            "capture_session_preparation_unavailable"
        ) from None

    if (
        doctor is None
        or practice is None
        or membership is None
        or appointment is None
        or patient is None
        or not practice.baa_in_force()
        or not practice.entitled()
    ):
        raise CaptureLaunchDenied("launch_not_authorized")

    return _DerivedCaptureScope(
        tenant_id=_typeid_uuid(practice_id),
        practice_id=practice_id,
        actor_id=actor_id,
        appointment_id=appointment_id,
        patient_id=patient.id,
    )


def _operation_key(value: str, *, context: str) -> str:
    if _IDEMPOTENCY_KEY.fullmatch(value) is None:
        raise CaptureLaunchDenied("launch_not_authorized")
    return pseudonymous_digest(value, context=context)


def _request_binding_digest(
    *,
    consent: ConsentCreate,
) -> str:
    """Bind the immutable consent facts independently of retry credentials.

    The downstream Scribe operation keys are deliberately excluded.  A new
    browser idempotency key can therefore prove it is retrying the exact same
    consent request after an unconsumed launch ticket expires, while each new
    Scribe attempt still receives its own domain-separated operation keys.
    """

    canonical = json.dumps(
        {
            "all_parties_consented": consent.all_parties_consented,
            "appointment_id": consent.appointment_id,
            "granted_at": consent.granted_at.isoformat(),
            "ingest_mode": EXTERNAL_INGEST_MODE,
            "method": consent.method,
            "modality": consent.modality,
            "participant_regions": sorted(consent.participant_regions),
            "patient_id": consent.patient_id,
            "practitioner_id": consent.practitioner_id,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return pseudonymous_digest(
        canonical,
        context="capture-scribe-preparation-binding-v1",
    )


def _same_time(left: datetime, right: datetime) -> bool:
    if (
        left.tzinfo is None
        or left.utcoffset() is None
        or right.tzinfo is None
        or right.utcoffset() is None
    ):
        return False
    return left.astimezone(UTC) == right.astimezone(UTC)


def _validate_consent_receipt(
    consent: ConsentRecord,
    expected: ConsentCreate,
) -> None:
    if (
        consent.appointment_id != expected.appointment_id
        or consent.practitioner_id != expected.practitioner_id
        or consent.patient_id != expected.patient_id
        or consent.modality != expected.modality
        or consent.participant_regions != expected.participant_regions
        or consent.method != expected.method
        or consent.all_parties_consented is not True
        or not _same_time(consent.granted_at, expected.granted_at)
        or consent.evidence_object_id != expected.evidence_object_id
        or consent.revoked_at is not None
        or _POLICY_VERSION.fullmatch(consent.policy_version) is None
    ):
        raise CaptureLaunchUnavailable("capture_session_preparation_unavailable")


def _validate_session_receipt(
    session: ScribeCaptureSession,
    expected: SessionCreate,
) -> None:
    if (
        session.appointment_id != expected.appointment_id
        or session.practitioner_id != expected.practitioner_id
        or session.patient_id != expected.patient_id
        or session.consent_record_id != expected.consent_record_id
        or session.modality != expected.modality
        or session.ingest_mode != EXTERNAL_INGEST_MODE
        or session.status != "created"
        or session.created_at.tzinfo is None
        or session.created_at.utcoffset() is None
    ):
        raise CaptureLaunchUnavailable("capture_session_preparation_unavailable")


def _reference(prepared: PreparedCaptureSession) -> AuthorizedCaptureSessionReference:
    return AuthorizedCaptureSessionReference(
        capture_session_id=prepared.capture_session_id,
        practice_id=prepared.practice_id,
        actor_id=prepared.actor_id,
        appointment_id=prepared.appointment_id,
        consent_evidence_id=prepared.consent_evidence_id,
        source_lease_id=prepared.source_lease_id,
        state_version=prepared.state_version,
    )


def _reserve_preparation(
    *,
    scope: _DerivedCaptureScope,
    idempotency_key: str,
    request_binding_digest: str,
    current: datetime,
) -> CaptureSessionPreparationReservation | PreparedCaptureSession:
    """Reserve once, reclaiming only an exact expired pre-PKCE launch."""

    released_expired_launch = False
    while True:
        try:
            return reserve_capture_session_preparation(
                actor_id=scope.actor_id,
                practice_id=scope.practice_id,
                appointment_id=scope.appointment_id,
                patient_id=scope.patient_id,
                idempotency_key=idempotency_key,
                request_binding_digest=request_binding_digest,
                now=current,
            )
        except CaptureCoordinationDenied:
            raise CaptureLaunchDenied("launch_not_authorized") from None
        except CaptureCoordinationConflict:
            if released_expired_launch:
                raise CaptureLaunchConflict(
                    "capture_session_preparation_conflict"
                ) from None
            try:
                released = release_expired_never_started_session(
                    practice_id=scope.practice_id,
                    actor_id=scope.actor_id,
                    appointment_id=scope.appointment_id,
                    request_binding_digest=request_binding_digest,
                    now=current,
                )
            except CaptureLifecycleUnavailable:
                raise CaptureLaunchUnavailable(
                    "capture_session_preparation_unavailable"
                ) from None
            if not released:
                raise CaptureLaunchConflict(
                    "capture_session_preparation_conflict"
                ) from None
            released_expired_launch = True
        except (CaptureCoordinationUnavailable, CaptureOutcomeUncertain):
            raise CaptureLaunchUnavailable(
                "capture_session_preparation_unavailable"
            ) from None


class DisabledScribeCaptureSessionPreparer:
    async def __call__(
        self,
        *,
        actor_id: TypeIDType,
        practice_id: TypeIDType,
        appointment_id: TypeIDType,
        idempotency_key: str,
        attestation: CaptureConsentAttestation,
    ) -> AuthorizedCaptureSessionReference:
        del actor_id, practice_id, appointment_id, idempotency_key, attestation
        raise CaptureLaunchUnavailable("capture_session_preparation_unavailable")

    async def close(self) -> None:
        return None


class ScribeCaptureSessionPreparer:
    def __init__(
        self,
        client: ScribeWorkflowClient,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._client = client
        self._clock = clock or (lambda: datetime.now(UTC))

    async def __call__(
        self,
        *,
        actor_id: TypeIDType,
        practice_id: TypeIDType,
        appointment_id: TypeIDType,
        idempotency_key: str,
        attestation: CaptureConsentAttestation,
    ) -> AuthorizedCaptureSessionReference:
        current = _now(self._clock())
        bounded_attestation = _validate_attestation(attestation, current=current)
        consent_key = _operation_key(
            idempotency_key, context="scribe-consent-create-idempotency-v1"
        )
        session_key = _operation_key(
            idempotency_key, context="scribe-session-create-idempotency-v1"
        )
        scope = _derive_capture_scope(
            actor_id=actor_id,
            practice_id=practice_id,
            appointment_id=appointment_id,
        )
        consent_payload = ConsentCreate(
            appointment_id=str(scope.appointment_id),
            practitioner_id=str(scope.actor_id),
            patient_id=str(scope.patient_id),
            modality=bounded_attestation.modality,
            participant_regions=set(bounded_attestation.participant_regions),
            method=bounded_attestation.method,
            all_parties_consented=True,
            granted_at=bounded_attestation.granted_at,
            evidence_object_id=None,
        )
        request_binding_digest = _request_binding_digest(
            consent=consent_payload,
        )
        reservation_or_prepared = _reserve_preparation(
            scope=scope,
            idempotency_key=idempotency_key,
            request_binding_digest=request_binding_digest,
            current=current,
        )
        if isinstance(reservation_or_prepared, PreparedCaptureSession):
            return _reference(reservation_or_prepared)
        reservation = reservation_or_prepared

        consent: ConsentRecord | None = None
        processing_session: ScribeCaptureSession | None = None
        try:
            consent = await self._client.create_consent(
                scope.tenant_id,
                consent_payload,
                idempotency_key=consent_key,
            )
            _validate_consent_receipt(consent, consent_payload)
            session_payload = SessionCreate(
                appointment_id=str(scope.appointment_id),
                practitioner_id=str(scope.actor_id),
                patient_id=str(scope.patient_id),
                consent_record_id=consent.id,
                modality=bounded_attestation.modality,
                ingest_mode=EXTERNAL_INGEST_MODE,
            )
            processing_session = await self._client.create_session(
                scope.tenant_id,
                session_payload,
                idempotency_key=session_key,
            )
            _validate_session_receipt(processing_session, session_payload)
        except (ScribeWorkflowDisabledError, ScribeWorkflowRetryableError):
            self._record_failure(
                reservation,
                error_code="scribe_outcome_uncertain",
                outcome_ambiguous=True,
                consent=consent,
                processing_session=processing_session,
                current=current,
            )
            raise CaptureLaunchUnavailable(
                "capture_session_preparation_unavailable"
            ) from None
        except ScribeWorkflowConflictError:
            self._record_failure(
                reservation,
                error_code="scribe_conflict",
                outcome_ambiguous=False,
                consent=consent,
                processing_session=processing_session,
                current=current,
            )
            raise CaptureLaunchConflict(
                "capture_session_preparation_conflict"
            ) from None
        except ScribeWorkflowRejectedError:
            self._record_failure(
                reservation,
                error_code="scribe_rejected",
                outcome_ambiguous=False,
                consent=consent,
                processing_session=processing_session,
                current=current,
            )
            raise CaptureLaunchDenied("launch_not_authorized") from None
        except CaptureLaunchUnavailable:
            self._record_failure(
                reservation,
                error_code="scribe_response_mismatch",
                outcome_ambiguous=True,
                consent=consent,
                processing_session=processing_session,
                current=current,
            )
            raise
        except Exception:
            self._record_failure(
                reservation,
                error_code="scribe_outcome_uncertain",
                outcome_ambiguous=True,
                consent=consent,
                processing_session=processing_session,
                current=current,
            )
            raise CaptureLaunchUnavailable(
                "capture_session_preparation_unavailable"
            ) from None

        authorization = ScribeCaptureAuthorization(
            consent_evidence_id=consent.id,
            consent_policy_version=consent.policy_version,
            processing_session_id=processing_session.id,
        )
        try:
            prepared = finalize_capture_session_preparation(
                reservation=reservation,
                authorization=authorization,
                now=current,
            )
        except CaptureCoordinationDenied:
            self._record_failure(
                reservation,
                error_code="local_authority_revoked",
                outcome_ambiguous=False,
                consent=consent,
                processing_session=processing_session,
                current=current,
            )
            raise CaptureLaunchDenied("launch_not_authorized") from None
        except CaptureCoordinationConflict:
            self._record_failure(
                reservation,
                error_code="local_authority_conflict",
                outcome_ambiguous=False,
                consent=consent,
                processing_session=processing_session,
                current=current,
            )
            raise CaptureLaunchConflict(
                "capture_session_preparation_conflict"
            ) from None
        except (CaptureCoordinationUnavailable, CaptureOutcomeUncertain):
            self._record_failure(
                reservation,
                error_code="local_finalize_uncertain",
                outcome_ambiguous=True,
                consent=consent,
                processing_session=processing_session,
                current=current,
            )
            raise CaptureLaunchUnavailable(
                "capture_session_preparation_unavailable"
            ) from None
        return _reference(prepared)

    @staticmethod
    def _record_failure(
        reservation: CaptureSessionPreparationReservation,
        *,
        error_code: str,
        outcome_ambiguous: bool,
        consent: ConsentRecord | None,
        processing_session: ScribeCaptureSession | None,
        current: datetime,
    ) -> None:
        try:
            record_capture_session_preparation_failure(
                reservation=reservation,
                error_code=error_code,
                outcome_ambiguous=outcome_ambiguous,
                observed_consent_evidence_id=(None if consent is None else consent.id),
                observed_consent_policy_version=(
                    None if consent is None else consent.policy_version
                ),
                observed_processing_session_id=(
                    None if processing_session is None else processing_session.id
                ),
                now=current,
            )
        except (
            CaptureCoordinationConflict,
            CaptureCoordinationDenied,
            CaptureCoordinationUnavailable,
            CaptureOutcomeUncertain,
        ):
            # The durable reservation still fences this encounter if recording
            # the more specific outcome itself encounters a storage race.
            return

    async def close(self) -> None:
        await self._client.close()


def build_scribe_capture_session_preparer(
    settings: ScribeWorkflowSettings = SCRIBE_WORKFLOW_SETTINGS,
    *,
    client_factory: Callable[[ScribeWorkflowSettings], ScribeWorkflowClient] = (
        build_scribe_workflow_client
    ),
) -> ScribeCaptureSessionPreparer | DisabledScribeCaptureSessionPreparer:
    """Build a live preparer only from complete, explicitly enabled settings."""

    if not settings.enabled or not settings.is_complete():
        return DisabledScribeCaptureSessionPreparer()
    try:
        client = client_factory(settings)
    except (ValueError, RuntimeError):
        return DisabledScribeCaptureSessionPreparer()
    return ScribeCaptureSessionPreparer(client)
