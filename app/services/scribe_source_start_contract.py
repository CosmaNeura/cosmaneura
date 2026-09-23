"""Closed native start wire contract. No tenant, creator or session chosen by callers."""

from typing import Literal
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    RootModel,
    SerializerFunctionWrapHandler,
    model_serializer,
)


class NativeStartContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(<protected>)"

    def __str__(self) -> str:
        return self.__repr__()

    @model_serializer(mode="wrap")
    def compatible_position(self, handler: SerializerFunctionWrapHandler):
        result = handler(self)
        if result.get("first_sequence_number") == 0:
            result.pop("first_sequence_number")
        return result


class NativeStartPayload(NativeStartContract):
    schema_version: Literal["1.0"] = "1.0"
    attempt_id: UUID
    source: Literal["desktop_companion", "ios_microphone"]
    owner_proof: str = Field(pattern=r"^[0-9a-f]{64}$", repr=False)
    lease_id: UUID
    lease_receipt_id: UUID
    lease_generation: int = Field(strict=True, ge=1)
    capture_authorized_until: AwareDatetime
    first_sequence_number: int = Field(default=0, strict=True, ge=0, le=999)


class NativeSourceStart(NativeStartContract):
    kind: Literal["source_start"]
    action: Literal["prepare", "authorize", "cancel", "stop"]
    payload: NativeStartPayload


class NativeStartReceipt(NativeStartContract):
    schema_version: Literal["1.0"] = "1.0"
    session_id: UUID
    attempt_id: UUID
    source_start_id: UUID
    creator_actor_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    consent_record_id: UUID
    source: Literal["desktop_companion", "ios_microphone"]
    lease_id: UUID
    lease_receipt_id: UUID
    lease_generation: int = Field(strict=True, ge=1)
    state: Literal["prepared", "issued", "cancelled", "expired", "stopped"]
    preparation_expires_at: AwareDatetime
    capture_authorized_until: AwareDatetime
    issued_at: AwareDatetime | None
    expires_at: AwareDatetime | None
    capability_id: UUID
    first_sequence_number: int = Field(default=0, strict=True, ge=0, le=999)


class NativeStartDecision(NativeStartContract):
    receipt: NativeStartReceipt
    secret: str = Field(pattern=r"^[0-9a-f]{64}$", repr=False)


class NativeUnstartedRetirement(NativeStartContract):
    schema_version: Literal["1.0"] = "1.0"
    state: Literal["unstarted"] = "unstarted"
    retirement_id: UUID
    session_id: UUID
    attempt_id: UUID
    creator_actor_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    consent_record_id: UUID
    source: Literal["desktop_companion", "ios_microphone"]
    owner_proof_sha256: str = Field(pattern=r"^[0-9a-f]{64}$", repr=False)
    lease_id: UUID
    lease_receipt_id: UUID
    lease_generation: int = Field(strict=True, ge=1)
    capture_authorized_until: AwareDatetime
    first_sequence_number: int = Field(default=0, strict=True, ge=0, le=999)
    retired_at: AwareDatetime


class NativeStopResult(RootModel[NativeStartReceipt | NativeUnstartedRetirement]):
    model_config = ConfigDict(frozen=True, hide_input_in_errors=True)

    def __repr__(self) -> str:
        return "NativeStopResult(<protected>)"

    def __str__(self) -> str:
        return self.__repr__()
