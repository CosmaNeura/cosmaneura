from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx
import pytest
from azure.core.credentials import AccessToken

from app.configuration.scribe_workflow import ScribeWorkflowSettings
from app.services.scribe_workflow import (
    ConsentCreate,
    HttpScribeWorkflowClient,
    ScribeWorkflowDisabledError,
    ScribeWorkflowRetryableError,
    SessionCreate,
    build_scribe_workflow_client,
)


class StaticCredential:
    def __init__(self) -> None:
        self.scopes: list[str] = []
        self.closed = False

    async def get_token(self, *scopes: str, **kwargs: object) -> AccessToken:
        del kwargs
        self.scopes.extend(scopes)
        return AccessToken(
            "test-access-token", int(datetime.now(UTC).timestamp()) + 300
        )

    async def close(self) -> None:
        self.closed = True


def settings(*, enabled: bool = True) -> ScribeWorkflowSettings:
    return ScribeWorkflowSettings(
        enabled=enabled,
        base_url="https://scribe.internal.example",
        scope="api://scribe-api/.default",
        managed_identity_client_id=str(uuid4()),
        timeout_seconds=5,
    )


def consent_payload() -> ConsentCreate:
    return ConsentCreate(
        appointment_id="appointment_01kzy09rxaepjt63rb0pv1256p",
        practitioner_id="doctor_01kzy09rx9emh9n9q0p0e6neam",
        patient_id="patient_01kzy09rxben2txt3pfy34bn6h",
        modality="virtual",
        participant_regions={"FL", "OH"},
        method="pre_visit_form",
        all_parties_consented=True,
        granted_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_client_uses_workload_scope_and_verified_tenant_header() -> None:
    tenant_id = uuid4()
    credential = StaticCredential()
    seen: list[httpx.Request] = []
    consent_id = uuid4()

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            201,
            json={
                **json.loads(request.content),
                "id": str(consent_id),
                "policy_version": "2026-08-conservative-all-party",
                "revoked_at": None,
            },
        )

    async with httpx.AsyncClient(
        base_url="https://scribe.internal.example",
        transport=httpx.MockTransport(handler),
    ) as http_client:
        client = HttpScribeWorkflowClient(
            base_url="https://scribe.internal.example",
            scope="api://scribe-api/.default",
            timeout_seconds=5,
            credential=credential,
            client=http_client,
        )
        result = await client.create_consent(
            tenant_id,
            consent_payload(),
            idempotency_key="consent-stable-key-1000",
        )
        await client.close()

    assert result.id == consent_id
    assert credential.scopes == ["api://scribe-api/.default"]
    assert credential.closed
    assert len(seen) == 1
    assert seen[0].url.path == "/v1/consents"
    assert seen[0].headers["authorization"] == "Bearer test-access-token"
    assert seen[0].headers["x-tenant-id"] == str(tenant_id)
    assert seen[0].headers["idempotency-key"] == "consent-stable-key-1000"
    body = json.loads(seen[0].content)
    assert "tenant_id" not in body
    assert "scheduling_provider" not in body


@pytest.mark.asyncio
async def test_client_creates_session_without_scheduling_source() -> None:
    tenant_id = uuid4()
    credential = StaticCredential()
    consent_id = uuid4()
    session_id = uuid4()
    payload = SessionCreate(
        appointment_id="appointment_01kzy09rxaepjt63rb0pv1256p",
        practitioner_id="doctor_01kzy09rx9emh9n9q0p0e6neam",
        patient_id="patient_01kzy09rxben2txt3pfy34bn6h",
        consent_record_id=consent_id,
        modality="virtual",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert set(body) == {
            "appointment_id",
            "practitioner_id",
            "patient_id",
            "consent_record_id",
            "modality",
            "ingest_mode",
        }
        assert body["ingest_mode"] == "chunk_upload"
        return httpx.Response(
            201,
            json={
                **body,
                "id": str(session_id),
                "status": "created",
                "created_at": datetime.now(UTC).isoformat(),
            },
        )

    async with httpx.AsyncClient(
        base_url="https://scribe.internal.example",
        transport=httpx.MockTransport(handler),
    ) as http_client:
        client = HttpScribeWorkflowClient(
            base_url="https://scribe.internal.example",
            scope="api://scribe-api/.default",
            timeout_seconds=5,
            credential=credential,
            client=http_client,
        )
        result = await client.create_session(
            tenant_id,
            payload,
            idempotency_key="session-stable-key-1000",
        )

    assert result.id == session_id


@pytest.mark.asyncio
async def test_client_sends_external_async_transcript_mode_explicitly() -> None:
    tenant_id = uuid4()
    credential = StaticCredential()
    consent_id = uuid4()

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["ingest_mode"] == "external_async_transcript"
        return httpx.Response(
            201,
            json={
                **body,
                "id": str(uuid4()),
                "status": "created",
                "created_at": datetime.now(UTC).isoformat(),
            },
        )

    async with httpx.AsyncClient(
        base_url="https://scribe.internal.example",
        transport=httpx.MockTransport(handler),
    ) as http_client:
        client = HttpScribeWorkflowClient(
            base_url="https://scribe.internal.example",
            scope="api://scribe-api/.default",
            timeout_seconds=5,
            credential=credential,
            client=http_client,
        )
        result = await client.create_session(
            tenant_id,
            SessionCreate(
                appointment_id="appointment_01kzy09rxaepjt63rb0pv1256p",
                practitioner_id="doctor_01kzy09rx9emh9n9q0p0e6neam",
                patient_id="patient_01kzy09rxben2txt3pfy34bn6h",
                consent_record_id=consent_id,
                modality="virtual",
                ingest_mode="external_async_transcript",
            ),
            idempotency_key="session-external-mode-1000",
        )

    assert result.ingest_mode == "external_async_transcript"


@pytest.mark.asyncio
async def test_client_does_not_echo_remote_response_bodies() -> None:
    sensitive_body = "Patient Name Must Never Appear"
    credential = StaticCredential()

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text=sensitive_body)

    async with httpx.AsyncClient(
        base_url="https://scribe.internal.example",
        transport=httpx.MockTransport(handler),
    ) as http_client:
        client = HttpScribeWorkflowClient(
            base_url="https://scribe.internal.example",
            scope="api://scribe-api/.default",
            timeout_seconds=5,
            credential=credential,
            client=http_client,
        )
        with pytest.raises(ScribeWorkflowRetryableError) as exc_info:
            await client.create_consent(
                uuid4(),
                consent_payload(),
                idempotency_key="consent-stable-key-2000",
            )

    assert exc_info.value.code == "scribe_http_503"
    assert sensitive_body not in str(exc_info.value)


@pytest.mark.asyncio
async def test_disabled_client_fails_closed() -> None:
    client = build_scribe_workflow_client(settings(enabled=False))

    with pytest.raises(ScribeWorkflowDisabledError):
        await client.create_consent(
            uuid4(),
            consent_payload(),
            idempotency_key="consent-stable-key-3000",
        )


def test_enabled_client_requires_complete_passwordless_configuration() -> None:
    incomplete = ScribeWorkflowSettings(
        enabled=True,
        base_url="http://not-private.example",
        scope="api://scribe-api/Scribe.Workflow.Access",
        managed_identity_client_id="",
        timeout_seconds=5,
    )

    with pytest.raises(ValueError, match="incomplete"):
        build_scribe_workflow_client(incomplete)


def test_credential_factory_receives_only_the_nonsecret_client_id() -> None:
    received: list[str] = []
    credential = StaticCredential()

    def credential_factory(client_id: str) -> StaticCredential:
        received.append(client_id)
        return credential

    configured = settings()
    client = build_scribe_workflow_client(configured, credential_factory)

    assert received == [configured.managed_identity_client_id]
    assert client is not None
    assert isinstance(UUID(configured.managed_identity_client_id), UUID)
