"""Closed native continuation wire contract; original stop ownership is retained."""

import hmac
from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from app.services.scribe_source_start_contract import (
    NativeStartContract,
    NativeStartPayload,
)


class NativeExtensionPayload(NativeStartContract):
    schema_version: Literal["1.0"] = "1.0"
    owner: NativeStartPayload
    previous_extension_id: UUID | None = None
    lease_receipt_id: UUID
    lease_generation: int = Field(strict=True, ge=2)
    capture_authorized_until: AwareDatetime
    reservation_batch_id: UUID
    first_sequence_number: int = Field(strict=True, ge=1, le=359)
    reservation_count: int = Field(strict=True, ge=1, le=90)

    @model_validator(mode="after")
    def bounded_extension(self):
        if (
            self.first_sequence_number + self.reservation_count > 360
            or self.lease_generation <= self.owner.lease_generation
            or self.capture_authorized_until <= self.owner.capture_authorized_until
        ):
            raise ValueError("native_extension_invalid")
        return self


class NativeSourceExtension(NativeStartContract):
    kind: Literal["source_extend"]
    payload: NativeExtensionPayload


class NativeExtensionReceipt(NativeStartContract):
    schema_version: Literal["1.0"] = "1.0"
    extension_id: UUID
    source_start_id: UUID
    session_id: UUID
    attempt_id: UUID
    creator_actor_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    consent_record_id: UUID
    source: Literal["desktop_companion", "ios_microphone"]
    lease_id: UUID
    lease_receipt_id: UUID
    lease_generation: int = Field(strict=True, ge=2)
    previous_extension_id: UUID | None
    previous_capture_authorized_until: AwareDatetime
    capture_authorized_until: AwareDatetime
    reservation_batch_id: UUID
    first_sequence_number: int = Field(strict=True, ge=1, le=359)
    maximum_sequence_number: int = Field(strict=True, ge=1, le=359)
    issued_at: AwareDatetime
    expires_at: AwareDatetime
    capability_id: UUID


class NativeExtensionDecision(NativeStartContract):
    receipt: NativeExtensionReceipt
    secret: str = Field(pattern=r"^[0-9a-f]{64}$", repr=False)


def validate_native_extension(
    operation: NativeSourceExtension,
    result: dict,
    session_id: UUID,
    creator_actor_id: str,
    now: datetime,
    consent_record_id: UUID | None = None,
) -> NativeExtensionDecision:
    try:
        checked = NativeExtensionDecision.model_validate(result)
        receipt = checked.receipt
        expected = operation.payload
        owner = expected.owner
        if (
            not hmac.compare_digest(checked.secret, owner.owner_proof)
            or receipt.session_id != session_id
            or receipt.creator_actor_id != creator_actor_id
            or consent_record_id is not None
            and receipt.consent_record_id != consent_record_id
            or receipt.attempt_id != owner.attempt_id
            or receipt.source != owner.source
            or receipt.lease_id != owner.lease_id
            or receipt.lease_receipt_id != expected.lease_receipt_id
            or receipt.lease_generation != expected.lease_generation
            or receipt.previous_extension_id != expected.previous_extension_id
            or receipt.capture_authorized_until != expected.capture_authorized_until
            or receipt.reservation_batch_id != expected.reservation_batch_id
            or receipt.first_sequence_number != expected.first_sequence_number
            or receipt.maximum_sequence_number
            != expected.first_sequence_number + expected.reservation_count - 1
            or not receipt.issued_at <= now < receipt.expires_at
            or receipt.expires_at > receipt.issued_at + timedelta(seconds=15)
            or not receipt.expires_at
            <= receipt.previous_capture_authorized_until
            < receipt.capture_authorized_until
        ):
            raise ValueError
        if expected.previous_extension_id is None:
            if (
                receipt.previous_capture_authorized_until
                != owner.capture_authorized_until
                or receipt.lease_generation != owner.lease_generation + 1
            ):
                raise ValueError
        elif (
            receipt.previous_capture_authorized_until <= owner.capture_authorized_until
            or receipt.lease_generation < owner.lease_generation + 2
        ):
            raise ValueError
        return checked
    except (ValueError, TypeError, AttributeError):
        raise ValueError("invalid_native_extension_receipt") from None
