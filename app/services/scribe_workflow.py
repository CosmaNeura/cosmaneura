from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any, Literal, Protocol, TypeVar
from uuid import UUID

import httpx
from azure.core.credentials import AccessToken
from azure.core.exceptions import AzureError
from azure.identity.aio import DefaultAzureCredential
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.configuration.scribe_workflow import (
    SCRIBE_WORKFLOW_SETTINGS,
    ScribeWorkflowSettings,
)


class ScribeWorkflowError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class ScribeWorkflowDisabledError(ScribeWorkflowError):
    pass


class ScribeWorkflowRetryableError(ScribeWorkflowError):
    pass


class ScribeWorkflowRejectedError(ScribeWorkflowError):
    pass


class ScribeWorkflowConflictError(ScribeWorkflowError):
    pass


class ConsentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    appointment_id: str = Field(min_length=1, max_length=128)
    practitioner_id: str = Field(min_length=1, max_length=128)
    patient_id: str = Field(min_length=1, max_length=128)
    modality: Literal["in_person", "virtual", "phone"]
    participant_regions: set[str] = Field(min_length=1)
    method: Literal["pre_visit_form", "verbal_attestation", "phone_ivr"]
    all_parties_consented: bool
    granted_at: datetime
    evidence_object_id: UUID | None = None


class ConsentRecord(ConsentCreate):
    id: UUID
    policy_version: str
    revoked_at: datetime | None = None


class SessionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    appointment_id: str = Field(min_length=1, max_length=128)
    practitioner_id: str = Field(min_length=1, max_length=128)
    patient_id: str = Field(min_length=1, max_length=128)
    consent_record_id: UUID
    modality: Literal["in_person", "virtual", "phone"]
    ingest_mode: Literal["chunk_upload", "external_async_transcript"] = "chunk_upload"


class CaptureSession(SessionCreate):
    id: UUID
    status: Literal[
        "created",
        "capturing",
        "uploaded",
        "processing",
        "transcript_ready",
        "handed_off",
        "failed",
    ]
    created_at: datetime


class AsyncTokenCredential(Protocol):
    def get_token(self, *scopes: str, **kwargs: Any) -> Awaitable[AccessToken]: ...

    def close(self) -> Awaitable[None]: ...


class ScribeWorkflowClient(Protocol):
    async def create_consent(
        self,
        tenant_id: UUID,
        payload: ConsentCreate,
        *,
        idempotency_key: str,
    ) -> ConsentRecord: ...

    async def create_session(
        self,
        tenant_id: UUID,
        payload: SessionCreate,
        *,
        idempotency_key: str,
    ) -> CaptureSession: ...

    async def close(self) -> None: ...


class DisabledScribeWorkflowClient:
    async def create_consent(
        self,
        tenant_id: UUID,
        payload: ConsentCreate,
        *,
        idempotency_key: str,
    ) -> ConsentRecord:
        del tenant_id, payload, idempotency_key
        raise ScribeWorkflowDisabledError("scribe_workflow_disabled")

    async def create_session(
        self,
        tenant_id: UUID,
        payload: SessionCreate,
        *,
        idempotency_key: str,
    ) -> CaptureSession:
        del tenant_id, payload, idempotency_key
        raise ScribeWorkflowDisabledError("scribe_workflow_disabled")

    async def close(self) -> None:
        return None


ResponseModel = TypeVar("ResponseModel", bound=BaseModel)


class HttpScribeWorkflowClient:
    """Call Scribe with a short-lived app token from the platform managed identity."""

    def __init__(
        self,
        *,
        base_url: str,
        scope: str,
        timeout_seconds: float,
        credential: AsyncTokenCredential,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._scope = scope
        self._credential = credential
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"), timeout=timeout_seconds
        )

    async def create_consent(
        self,
        tenant_id: UUID,
        payload: ConsentCreate,
        *,
        idempotency_key: str,
    ) -> ConsentRecord:
        return await self._post(
            "/v1/consents",
            tenant_id,
            payload,
            idempotency_key,
            ConsentRecord,
        )

    async def create_session(
        self,
        tenant_id: UUID,
        payload: SessionCreate,
        *,
        idempotency_key: str,
    ) -> CaptureSession:
        return await self._post(
            "/v1/sessions",
            tenant_id,
            payload,
            idempotency_key,
            CaptureSession,
        )

    async def _post(
        self,
        path: str,
        tenant_id: UUID,
        payload: BaseModel,
        idempotency_key: str,
        response_model: type[ResponseModel],
    ) -> ResponseModel:
        try:
            access_token = await self._credential.get_token(self._scope)
            response = await self._client.post(
                path,
                headers={
                    "Authorization": f"Bearer {access_token.token}",
                    "X-Tenant-ID": str(tenant_id),
                    "Idempotency-Key": idempotency_key,
                },
                json=payload.model_dump(mode="json"),
            )
        except (AzureError, httpx.TimeoutException, httpx.NetworkError) as exc:
            raise ScribeWorkflowRetryableError("scribe_transport_unavailable") from exc

        if response.status_code in {200, 201}:
            try:
                return response_model.model_validate(response.json())
            except (ValueError, ValidationError) as exc:
                raise ScribeWorkflowRetryableError("scribe_invalid_response") from exc
        if response.status_code == 409:
            raise ScribeWorkflowConflictError("scribe_conflict")
        if response.status_code in {408, 425, 429} or response.status_code >= 500:
            raise ScribeWorkflowRetryableError(f"scribe_http_{response.status_code}")
        raise ScribeWorkflowRejectedError(f"scribe_http_{response.status_code}")

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()
        await self._credential.close()


def build_scribe_workflow_client(
    settings: ScribeWorkflowSettings = SCRIBE_WORKFLOW_SETTINGS,
    credential_factory: Callable[[str], AsyncTokenCredential] | None = None,
) -> ScribeWorkflowClient:
    if not settings.enabled:
        return DisabledScribeWorkflowClient()
    if not settings.is_complete():
        raise ValueError("Scribe workflow settings are incomplete")

    if credential_factory is None:
        credential: AsyncTokenCredential = DefaultAzureCredential(
            managed_identity_client_id=settings.managed_identity_client_id
        )
    else:
        credential = credential_factory(settings.managed_identity_client_id)

    return HttpScribeWorkflowClient(
        base_url=settings.base_url,
        scope=settings.scope,
        timeout_seconds=settings.timeout_seconds,
        credential=credential,
    )
