"""Durable consent/session admission with current authority at every phase.

No microphone action occurs here. Downstream keys and validated receipts survive
timeouts, lease expiry and human revocation. A receipt is not a future grant.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal, cast
from uuid import UUID, uuid4

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    field_serializer,
    model_validator,
)

from app.configuration.authority import (
    AUTHORITY_KERNEL_SETTINGS,
    AuthorityKernelSettings,
)
from app.configuration.scribe_capture import (
    SCRIBE_CAPTURE_SETTINGS,
    ScribeCaptureSettings,
)
from app.configuration.scribe_workflow import SCRIBE_SERVICE_TIMEOUT_SECONDS_LIMIT
from app.services.scribe_capture_scope import authorize_capture_binding
from app.services.scribe_configured_capture_contract import (
    ConfiguredCaptureReservation,
    ConfiguredCaptureSelection,
    ConfiguredSessionCreate,
    ConfiguredSessionReceipt,
)
from app.services.scribe_workflow import (
    CaptureSession,
    ConsentCreate,
    ConsentRecord,
    ScribeWorkflowClient,
    ScribeWorkflowError,
    SessionCreate,
)

from activemodel.session_manager import get_session
from app.models.scribe_capture_admission import ScribeCaptureAdmission
from app.models.scribe_capture_binding import ScribeCaptureBinding
from sqlmodel import Session, select

# Admission must allow the configured transport to finish, while retaining a
# finite whole-phase bound and a longer lease to prevent overlapping claims.
PHASE_TIMEOUT_SECONDS = SCRIBE_SERVICE_TIMEOUT_SECONDS_LIMIT + 5
LEASE_SECONDS = PHASE_TIMEOUT_SECONDS + 25


class AdmissionError(Exception):
    """Fixed, value-free errors for the public facade."""


class AdmissionConsentRequired(AdmissionError):
    pass


class AdmissionConflict(AdmissionError):
    pass


class AdmissionInProgress(AdmissionError):
    pass


class AdmissionUnconfirmed(AdmissionError):
    pass


class AdmissionConsentUnavailable(AdmissionError):
    pass


class ConsentAttestation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    modality: Literal["in_person", "virtual", "phone"]
    participant_regions: frozenset[
        Annotated[str, Field(pattern=r"^[A-Z]{2}(?:-[A-Z0-9]{1,3})?$")]
    ] = Field(max_length=16)
    # Optional location metadata; all-party consent is required in every region.
    # An empty set means not collected, never an inferred or fabricated location.
    method: Literal["verbal_attestation"]
    all_parties_consented: StrictBool
    granted_at: AwareDatetime

    @model_validator(mode="after")
    def explicit_consent(self) -> ConsentAttestation:
        if not self.all_parties_consented:
            raise ValueError("Explicit all-party consent is required")
        return self

    @field_serializer("participant_regions")
    def sorted_regions(self, value: frozenset[str]) -> list[str]:
        return sorted(value)


@dataclass(frozen=True, repr=False)
class CaptureCaller:
    workload_client_id: str
    mount_context_token: str
    interaction_ref: str
    client_turn_id: UUID
    settings: AuthorityKernelSettings = AUTHORITY_KERNEL_SETTINGS
    capture_settings: ScribeCaptureSettings = SCRIBE_CAPTURE_SETTINGS

    def authorize(
        self, session: Session, binding_ref: str, workspace_id: UUID
    ) -> ScribeCaptureBinding:
        return authorize_capture_binding(
            session,
            binding_ref=binding_ref,
            workspace_id=workspace_id,
            workload_client_id=self.workload_client_id,
            mount_context_token=self.mount_context_token,
            interaction_ref=self.interaction_ref,
            client_turn_id=self.client_turn_id,
            settings=self.settings,
            capture_settings=self.capture_settings,
        )


@dataclass(frozen=True, repr=False)
class AdmissionSnapshot:
    operation_ref: str
    binding_ref: str
    workspace_id: UUID
    appointment_ref: str
    consent_record_id: UUID
    session_id: UUID
    status: Literal["admitted"] = "admitted"
    configured_capture: ConfiguredCaptureSelection | None = None


@dataclass(frozen=True, repr=False)
class AdmittedCaptureScope:
    operation_ref: str
    tenant_id: UUID
    session_id: UUID
    session_request: SessionCreate | ConfiguredSessionCreate
    # Reauthorized internal actor, never inferred from a doctor ID or caller header.
    # None keeps legacy injected callers compatible but cannot authorize review.
    creator_actor_id: str | None = None


def authorize_admitted_capture(
    caller: CaptureCaller, binding_ref: str, workspace_id: UUID
) -> AdmittedCaptureScope:
    """Fresh authorization and immutable admitted scope, with no open transaction."""
    with _database() as session:
        result = authorize_admitted_capture_locked(
            session, caller, binding_ref, workspace_id
        )
        session.commit()
        return result


def authorize_admitted_capture_locked(
    session: Session, caller: CaptureCaller, binding_ref: str, workspace_id: UUID
) -> AdmittedCaptureScope:
    """Reuse current admission checks inside an authorized local publication."""
    binding = caller.authorize(session, binding_ref, workspace_id)
    operation = session.exec(
        select(ScribeCaptureAdmission)
        .where(ScribeCaptureAdmission.binding_id == binding.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()
    if operation is None:
        raise AdmissionConsentRequired
    consent_payload = _validate_operation(operation, binding)
    if operation.status == "consent_unavailable":
        raise AdmissionConsentUnavailable
    if operation.status != "ready":
        raise AdmissionUnconfirmed
    try:
        consent = ConsentRecord.model_validate(operation.consent_receipt)
        request = _session_request(operation, consent)
        receipt = (
            ConfiguredSessionReceipt
            if isinstance(request, ConfiguredSessionCreate)
            else CaptureSession
        ).model_validate(operation.session_receipt)
        if (
            consent.id != operation.consent_id
            or not _matches(consent, consent_payload)
            or receipt.id != operation.session_id
            or not _matches(receipt, request)
            or isinstance(receipt, ConfiguredSessionReceipt)
            and receipt.creator_actor_id != str(binding.actor_id)
        ):
            raise ValueError
    except (TypeError, ValueError):
        raise AdmissionConflict from None
    if consent.revoked_at is not None:
        raise AdmissionConsentUnavailable
    result = AdmittedCaptureScope(
        operation_ref=str(operation.id),
        tenant_id=binding.scribe_tenant_id,
        session_id=receipt.id,
        session_request=request,
        creator_actor_id=str(binding.actor_id),
    )
    return result


@dataclass(frozen=True, repr=False)
class _PhaseClaim:
    operation_ref: str
    request_sha256: str
    scribe_tenant_id: UUID
    lease_token: UUID
    phase: Literal["consent", "session"]
    key: str
    payload: ConsentCreate | SessionCreate | ConfiguredSessionCreate
    creator_actor_id: str | None = None


@contextmanager
def _database():
    with get_session() as managed:
        session = cast(Session, managed)
        try:
            yield session
        except BaseException:
            session.rollback()
            raise


def _document(payload: ConsentCreate) -> dict[str, Any]:
    document = payload.model_dump(mode="json")
    document["participant_regions"] = sorted(payload.participant_regions)
    return document


def _digest(
    binding: ScribeCaptureBinding,
    document: dict[str, Any],
    configured: dict[str, Any] | None = None,
) -> str:
    return hashlib.sha256(
        json.dumps(
            {
                "scope_sha256": binding.scope_sha256,
                "consent": document,
                **(
                    {"configured_capture": configured} if configured is not None else {}
                ),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def _consent_payload(
    binding: ScribeCaptureBinding, attestation: ConsentAttestation
) -> ConsentCreate:
    return ConsentCreate(
        appointment_id=str(binding.appointment_id),
        practitioner_id=str(binding.doctor_id),
        patient_id=str(binding.patient_id),
        **attestation.model_dump(),
    )


def _operation(session: Session, operation_ref: str) -> ScribeCaptureAdmission:
    operation = session.exec(
        select(ScribeCaptureAdmission)
        .where(
            ScribeCaptureAdmission.id == operation_ref,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()
    if operation is None:
        raise AdmissionUnconfirmed
    return operation


def _validate_operation(
    operation: ScribeCaptureAdmission, binding: ScribeCaptureBinding
) -> ConsentCreate:
    try:
        consent = ConsentCreate.model_validate(operation.consent_payload)
        if operation.configured_capture is not None:
            reservation = ConfiguredCaptureReservation.model_validate(
                operation.configured_capture
            )
            if reservation.creator_actor_id != str(binding.actor_id):
                raise ValueError
        if (
            operation.binding_id != binding.id
            or operation.scribe_tenant_id != binding.scribe_tenant_id
            or operation.request_sha256
            != _digest(binding, _document(consent), operation.configured_capture)
            or consent.appointment_id != str(binding.appointment_id)
            or consent.practitioner_id != str(binding.doctor_id)
            or consent.patient_id != str(binding.patient_id)
        ):
            raise ValueError
        return consent
    except (TypeError, ValueError):
        raise AdmissionConflict from None


def _session_request(operation: ScribeCaptureAdmission, consent: ConsentRecord):
    fields = dict(
        appointment_id=consent.appointment_id,
        practitioner_id=consent.practitioner_id,
        patient_id=consent.patient_id,
        consent_record_id=consent.id,
        modality=consent.modality,
    )
    if operation.configured_capture is None:
        return SessionCreate.model_validate(fields)
    reserved = ConfiguredCaptureReservation.model_validate(operation.configured_capture)
    return ConfiguredSessionCreate.model_validate(
        {
            **fields,
            "schema_version": reserved.schema_version,
            "id": reserved.id,
            "language_mode": reserved.language_mode,
        }
    )


def validate_resume_request(
    expected_session_id: UUID | None,
    resume_current: bool,
    attestation: ConsentAttestation | None,
    configured_capture: ConfiguredCaptureSelection | None,
) -> None:
    if (
        type(resume_current) is not bool
        or (
            expected_session_id is not None
            and not isinstance(expected_session_id, UUID)
        )
        or (expected_session_id is not None and resume_current)
        or (
            (expected_session_id is not None or resume_current)
            and (attestation is not None or configured_capture is not None)
        )
    ):
        raise ValueError("invalid_capture_resume_selection")


def _prepare(
    caller: CaptureCaller,
    binding_ref: str,
    workspace_id: UUID,
    client_request_key: str,
    attestation: ConsentAttestation | None,
    configured_capture: ConfiguredCaptureSelection | None = None,
    *,
    expected_session_id: UUID | None = None,
    resume_current: bool = False,
) -> str:
    try:
        validate_resume_request(
            expected_session_id, resume_current, attestation, configured_capture
        )
    except ValueError:
        raise AdmissionConflict from None
    if not re.fullmatch(r"[!-~]{8,255}", client_request_key):
        raise AdmissionConflict
    if configured_capture is not None:
        try:
            configured_capture = ConfiguredCaptureSelection.model_validate(
                configured_capture.model_dump()
            )
        except (TypeError, ValueError, AttributeError):
            raise AdmissionConflict from None
    if attestation is not None:
        try:
            attestation = ConsentAttestation.model_validate(attestation.model_dump())
        except (TypeError, ValueError, AttributeError):
            raise AdmissionConsentRequired from None
    with _database() as session:
        binding = caller.authorize(session, binding_ref, workspace_id)
        operation = session.exec(
            select(ScribeCaptureAdmission)
            .where(
                ScribeCaptureAdmission.binding_id == binding.id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        ).one_or_none()
        if operation is None:
            if expected_session_id is not None:
                raise AdmissionConflict
            if attestation is None:
                raise AdmissionConsentRequired
            now = datetime.now(UTC)
            if (
                not now - timedelta(minutes=10)
                <= attestation.granted_at
                <= now + timedelta(seconds=5)
            ):
                raise AdmissionConsentRequired
            document = _document(_consent_payload(binding, attestation))
            configured = (
                None
                if configured_capture is None
                else ConfiguredCaptureReservation(
                    **configured_capture.model_dump(),
                    id=uuid4(),
                    creator_actor_id=str(binding.actor_id),
                ).model_dump(mode="json")
            )
            operation = ScribeCaptureAdmission(
                binding_id=binding.id,
                scribe_tenant_id=binding.scribe_tenant_id,
                request_sha256=_digest(binding, document, configured),
                configured_capture=configured,
                consent_payload=document,
                client_key_sha256=hashlib.sha256(
                    client_request_key.encode()
                ).hexdigest(),
            )
            session.add(operation)
            session.flush()
        else:
            _validate_operation(operation, binding)
            if (
                expected_session_id is not None
                and operation.session_id != expected_session_id
            ):
                raise AdmissionConflict
            if expected_session_id is not None or resume_current:
                # Resume the immutable saved family; never create or select a
                # replacement recording from caller-supplied preferences.
                configured_capture = (
                    ConfiguredCaptureSelection.model_validate(
                        {
                            key: operation.configured_capture[key]
                            for key in ConfiguredCaptureSelection.model_fields
                        }
                    )
                    if operation.configured_capture is not None
                    else None
                )
            # The caller must select the same admission family on every resume.
            if (configured_capture is None) != (operation.configured_capture is None):
                raise AdmissionConflict
            if configured_capture is not None:
                assert operation.configured_capture is not None
                if any(
                    operation.configured_capture.get(k) != v
                    for k, v in configured_capture.model_dump(mode="json").items()
                ):
                    raise AdmissionConflict
            if attestation is not None and operation.request_sha256 != _digest(
                binding,
                _document(_consent_payload(binding, attestation)),
                operation.configured_capture,
            ):
                raise AdmissionConflict
        reference = str(operation.id)
        session.commit()
        return reference


def _claim(
    caller: CaptureCaller, binding_ref: str, workspace_id: UUID, operation_ref: str
) -> _PhaseClaim:
    with _database() as session:
        binding = caller.authorize(session, binding_ref, workspace_id)
        operation = _operation(session, operation_ref)
        consent = _validate_operation(operation, binding)
        now = datetime.now(UTC)
        if operation.lease_expires_at is not None and operation.lease_expires_at > now:
            raise AdmissionInProgress
        if operation.consent_receipt is None:
            phase = "consent"
            key = f"core-consent-{operation.consent_key.hex}"
            payload = consent
        else:
            try:
                record = ConsentRecord.model_validate(operation.consent_receipt)
            except (TypeError, ValueError):
                raise AdmissionConflict from None
            if record.revoked_at is not None or not record.all_parties_consented:
                raise AdmissionConsentUnavailable
            if record.id != operation.consent_id or not _matches(record, consent):
                raise AdmissionConflict
            phase = "session"
            key = f"core-session-{operation.session_key.hex}"
            payload = _session_request(operation, record)
        lease_token = uuid4()
        operation.lease_token = lease_token
        operation.lease_phase = phase
        operation.lease_expires_at = now + timedelta(seconds=LEASE_SECONDS)
        operation.status = f"{phase}_unconfirmed"
        operation.last_error_code = None
        session.add(operation)
        claim = _PhaseClaim(
            operation_ref=operation_ref,
            request_sha256=operation.request_sha256,
            scribe_tenant_id=operation.scribe_tenant_id,
            lease_token=lease_token,
            phase=phase,
            key=key,
            payload=payload,
            creator_actor_id=str(binding.actor_id)
            if operation.configured_capture
            else None,
        )
        session.commit()
        return claim


def _matches(record: BaseModel, payload: BaseModel) -> bool:
    return all(
        getattr(record, name, None) == getattr(payload, name)
        for name in type(payload).model_fields
    )


def _clear_lease(operation: ScribeCaptureAdmission) -> None:
    operation.lease_token = None
    operation.lease_phase = None
    operation.lease_expires_at = None


def _accept_receipt(
    claim: _PhaseClaim,
    receipt: ConsentRecord | CaptureSession | ConfiguredSessionReceipt,
) -> None:
    if isinstance(claim.payload, ConfiguredSessionCreate):
        receipt = ConfiguredSessionReceipt.model_validate(
            receipt.model_dump(mode="json")
        )
    if not _matches(receipt, claim.payload):
        raise AdmissionUnconfirmed
    if claim.phase == "consent":
        if not isinstance(receipt, ConsentRecord) or not re.fullmatch(
            r"[A-Za-z0-9._-]{1,64}", receipt.policy_version
        ):
            raise AdmissionUnconfirmed
        if receipt.revoked_at is not None and receipt.revoked_at.utcoffset() is None:
            raise AdmissionUnconfirmed
    elif (
        not isinstance(receipt, (CaptureSession, ConfiguredSessionReceipt))
        or receipt.created_at.utcoffset() is None
        or isinstance(receipt, ConfiguredSessionReceipt)
        and receipt.creator_actor_id != claim.creator_actor_id
    ):
        raise AdmissionUnconfirmed
    # Journal-only write: current human access is deliberately not required to
    # preserve a validated response to a previously authorized phase. No data or
    # subsequent action is released until _finish / _claim re-authorizes.
    with _database() as session:
        operation = _operation(session, claim.operation_ref)
        expected_key = (
            f"core-{claim.phase}-{getattr(operation, claim.phase + '_key').hex}"
        )
        if (
            operation.request_sha256 != claim.request_sha256
            or expected_key != claim.key
            or operation.scribe_tenant_id != claim.scribe_tenant_id
        ):
            raise AdmissionConflict
        existing_id = getattr(operation, claim.phase + "_id")
        if existing_id is not None and existing_id != receipt.id:
            raise AdmissionConflict
        if claim.phase == "consent":
            existing = operation.consent_receipt
            # A late successful replay cannot erase a recorded revocation.
            if existing is None or existing.get("revoked_at") is None:
                operation.consent_receipt = receipt.model_dump(mode="json")
            operation.consent_id = receipt.id
        else:
            if operation.session_receipt is None:
                operation.session_receipt = receipt.model_dump(mode="json")
            operation.session_id = receipt.id
        if operation.lease_token == claim.lease_token:
            _clear_lease(operation)
            operation.last_error_code = None
        revoked = bool(
            operation.consent_receipt and operation.consent_receipt.get("revoked_at")
        )
        if revoked:
            operation.status = "consent_unavailable"
        elif operation.lease_phase is not None:
            operation.status = f"{operation.lease_phase}_unconfirmed"
        elif operation.session_id is not None:
            operation.status = "ready"
        else:
            operation.status = "session_pending"
        session.add(operation)
        session.commit()


def _failure_code(error: Exception) -> str:
    """Retain a bounded cause without exception text, response bodies or IDs."""
    if isinstance(error, TimeoutError):
        return "scribe_phase_timeout"
    if isinstance(error, ScribeWorkflowError):
        if error.code in {
            "scribe_transport_unavailable",
            "scribe_invalid_response",
            "scribe_request_timeout",
            "scribe_identity_unavailable",
            "scribe_response_too_large",
            "scribe_response_scope_mismatch",
            "scribe_conflict",
        } or re.fullmatch(r"scribe_http_[1-5][0-9]{2}", error.code):
            return error.code
        return "scribe_remote_unconfirmed"
    if isinstance(error, AdmissionUnconfirmed):
        return "scribe_receipt_unconfirmed"
    return "scribe_phase_unconfirmed"


def _record_failure(claim: _PhaseClaim, error: Exception) -> None:
    with _database() as session:
        operation = _operation(session, claim.operation_ref)
        if operation.request_sha256 != claim.request_sha256:
            raise AdmissionConflict
        if operation.lease_token == claim.lease_token:
            _clear_lease(operation)
            operation.last_error_code = _failure_code(error)
            session.add(operation)
        session.commit()


def _finish(
    caller: CaptureCaller, binding_ref: str, workspace_id: UUID, operation_ref: str
) -> AdmissionSnapshot:
    with _database() as session:
        binding = caller.authorize(session, binding_ref, workspace_id)
        operation = _operation(session, operation_ref)
        _validate_operation(operation, binding)
        if operation.consent_receipt and operation.consent_receipt.get("revoked_at"):
            raise AdmissionConsentUnavailable
        if (
            operation.status != "ready"
            or operation.session_id is None
            or operation.consent_id is None
        ):
            raise AdmissionUnconfirmed
        result = AdmissionSnapshot(
            operation_ref=operation_ref,
            binding_ref=binding_ref,
            workspace_id=workspace_id,
            appointment_ref=str(binding.appointment_id),
            consent_record_id=operation.consent_id,
            session_id=operation.session_id,
            configured_capture=(
                ConfiguredCaptureSelection.model_validate(
                    {
                        key: operation.configured_capture[key]
                        for key in ConfiguredCaptureSelection.model_fields
                    }
                )
                if operation.configured_capture is not None
                else None
            ),
        )
        session.commit()
        return result


async def admit_capture(
    *,
    caller: CaptureCaller,
    binding_ref: str,
    workspace_id: UUID,
    client_request_key: str,
    attestation: ConsentAttestation | None,
    client: ScribeWorkflowClient,
    configured_capture: ConfiguredCaptureSelection | None = None,
    expected_session_id: UUID | None = None,
    resume_current: bool = False,
) -> AdmissionSnapshot:
    """Explicitly admit/resume one capture; never start a microphone.

    Even a ready operation replays session admission with the same Scribe key,
    forcing Scribe to check current consent before this receipt is returned.
    """
    operation_ref = _prepare(
        caller,
        binding_ref,
        workspace_id,
        client_request_key,
        attestation,
        configured_capture,
        expected_session_id=expected_session_id,
        resume_current=resume_current,
    )
    for _ in range(2):
        claim = _claim(caller, binding_ref, workspace_id, operation_ref)
        try:
            async with asyncio.timeout(PHASE_TIMEOUT_SECONDS):
                if claim.phase == "consent":
                    receipt = await client.create_consent(
                        claim.scribe_tenant_id,
                        cast(ConsentCreate, claim.payload),
                        idempotency_key=claim.key,
                    )
                elif isinstance(claim.payload, ConfiguredSessionCreate):
                    assert claim.creator_actor_id is not None
                    receipt = await client.create_configured_session(
                        claim.scribe_tenant_id,
                        claim.payload,
                        creator_actor_id=claim.creator_actor_id,
                        idempotency_key=claim.key,
                    )
                else:
                    receipt = await client.create_session(
                        claim.scribe_tenant_id,
                        cast(SessionCreate, claim.payload),
                        idempotency_key=claim.key,
                    )
            _accept_receipt(claim, receipt)
        except asyncio.CancelledError:
            # A process exit may prevent cleanup too. The durable lease expires
            # and the same operation key remains the only safe recovery path.
            raise
        except Exception as error:  # noqa: BLE001 -- sanitize every injected transport failure
            _record_failure(claim, error)
            raise AdmissionUnconfirmed from None
        if claim.phase == "session":
            return _finish(caller, binding_ref, workspace_id, operation_ref)
    raise AdmissionUnconfirmed
