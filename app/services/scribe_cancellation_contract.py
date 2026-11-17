"""Scribe-only cancellation receipts; no erasure or shared-account authority."""

from dataclasses import dataclass
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, TypeAdapter

from app.services.companion_record_contract import Closed, Digest

PhiScope = Literal["core-and-scribe-clinical-records"]


class CancellationPolicy(Closed):
    # The operator pins the customer's policy; 90 days is the owner-approved default.
    grace_days: Annotated[int, Field(strict=True, ge=30, le=90)]
    phi_scope: PhiScope
    approval_sha256: Digest


@dataclass(frozen=True)
class CancellationSettings:
    enabled: bool = False
    policy: CancellationPolicy | None = None
    # Remains enabled after intake is closed; disabling intake must not lift a fence.
    ledger_ready: bool = False

    def require_policy(self) -> CancellationPolicy:
        if not self.enabled or not self.ledger_ready or self.policy is None:
            raise CancellationUnavailable
        return self.policy


class CancellationUnavailable(Exception):
    pass


class CancellationDenied(Exception):
    pass


class CancellationNotFound(Exception):
    pass


class CancellationConflict(Exception):
    pass


class PreviewRequest(Closed):
    action: Literal["preview"]
    request_nonce: UUID


class AcceptRequest(Closed):
    action: Literal["request"]
    request_nonce: UUID
    preview_sha256: Digest
    confirmation: Literal["CANCEL"]


class StatusRequest(Closed):
    action: Literal["status"]
    request_nonce: UUID
    operation_id: UUID | None = None


CancellationRequest = Annotated[
    PreviewRequest | AcceptRequest | StatusRequest, Field(discriminator="action")
]
REQUEST_ADAPTER = TypeAdapter(CancellationRequest)


class CancellationPreview(Closed):
    contract_version: Literal["1"] = "1"
    action: Literal["preview"] = "preview"
    request_nonce: UUID
    preview_sha256: Digest
    grace_days: int
    phi_scope: PhiScope
    scope: Literal["scribe-only"] = "scribe-only"
    shared_login_preserved: Literal[True] = True
    # An ownership inventory is NOT a verified PHI erasure manifest.
    resource_manifest_status: Literal["unresolved"] = "unresolved"


class CancellationReceipt(Closed):
    contract_version: Literal["1"] = "1"
    action: Literal["request", "status"]
    request_nonce: UUID
    operation_id: UUID
    phase: Literal["read-only-export", "read-only-expired"]
    received_at: AwareDatetime
    export_until: AwareDatetime
    grace_days: int
    phi_scope: PhiScope
    scope: Literal["scribe-only"] = "scribe-only"
    shared_login_preserved: Literal[True] = True
    resource_manifest_status: Literal["unresolved"] = "unresolved"
    access_fence_confirmed: Literal[True] = True
    erasure_confirmed: Literal[False] = False
