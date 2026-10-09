"""Closed creator continuation request; the server owns its exact batch count."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from app.services.scribe_capture_limits import MAX_CAPTURE_CHUNKS
from app.services.scribe_recording_window_contract import (
    ProtectedWindowContract,
    RequestKey,
)


class ContinuationWindowPayload(ProtectedWindowContract):
    schema_version: Literal["1.0"] = "1.0"
    channel: Literal["microphone", "mixed"]
    first_sequence_number: int = Field(strict=True, ge=0, lt=MAX_CAPTURE_CHUNKS)
    previous_lease_id: UUID
    previous_lease_generation: int = Field(strict=True, ge=1)
    renew_idempotency_key: RequestKey
    reservation_idempotency_key: RequestKey

    @model_validator(mode="after")
    def distinct_keys(self) -> ContinuationWindowPayload:
        if self.renew_idempotency_key == self.reservation_idempotency_key:
            raise ValueError("Invalid continuation request keys")
        return self


class PrepareContinuationWindow(ProtectedWindowContract):
    kind: Literal["prepare_continuation_window"]
    payload: ContinuationWindowPayload
