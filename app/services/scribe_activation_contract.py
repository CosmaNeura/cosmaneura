"""Closed native activation request; identity and tenancy come from Core."""

from typing import Literal
from uuid import UUID

from pydantic import field_validator

from app.services.companion_record_contract import Closed


class ActivationRequest(Closed):
    action: Literal["activate"]
    request_nonce: UUID

    @field_validator("request_nonce", mode="before")
    @classmethod
    def canonical_nonce(cls, value):
        parsed = UUID(value) if isinstance(value, str) else value
        if not isinstance(parsed, UUID) or not parsed.int or str(parsed) != str(value):
            raise ValueError("activation_nonce_invalid")
        return parsed


class ActivationResponse(Closed):
    contract_version: Literal["1"] = "1"
    action: Literal["activate"] = "activate"
    request_nonce: UUID
    state: Literal["active"] = "active"
