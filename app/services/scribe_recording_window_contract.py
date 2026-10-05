"""Closed first-window sequence. Every component retains its original request key."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

RequestKey = Annotated[str, Field(pattern=r"^[A-Za-z0-9._:-]{8,255}$", repr=False)]


class ProtectedWindowContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(<protected>)"

    def __str__(self) -> str:
        return self.__repr__()


class RecordingWindowBinding(ProtectedWindowContract):
    key_version: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    wrapped_dek: str = Field(pattern=r"^[A-Za-z0-9_-]{512}$", repr=False)


class RecordingWindowPayload(ProtectedWindowContract):
    schema_version: Literal["1.0"] = "1.0"
    binding: RecordingWindowBinding
    bind_idempotency_key: RequestKey
    lease_idempotency_key: RequestKey
    reserve_idempotency_key: RequestKey
    channel: Literal["microphone", "system_audio", "mixed"]
    reservation_count: int = Field(strict=True, ge=1, le=90)

    @model_validator(mode="after")
    def distinct_keys(self) -> RecordingWindowPayload:
        if (
            len(
                {
                    self.bind_idempotency_key,
                    self.lease_idempotency_key,
                    self.reserve_idempotency_key,
                }
            )
            != 3
        ):
            raise ValueError("Invalid recording request keys")
        return self


class PrepareRecordingWindow(ProtectedWindowContract):
    kind: Literal["prepare_recording_window"]
    payload: RecordingWindowPayload
