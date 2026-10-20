from __future__ import annotations

import json
import traceback
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx
import pytest
from azure.core.credentials import AccessToken
from pydantic import ValidationError

from app.configuration.scribe_workflow import ScribeWorkflowSettings
from app.services import scribe_workflow
from app.services.scribe_workflow import (
    ConsentCreate,
    ExternalTranscriptSessionCreate,
    HttpScribeWorkflowClient,
    ScribeWorkflowDisabledError,
    ScribeWorkflowRejectedError,
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


def external_session_payload() -> ExternalTranscriptSessionCreate:
    return ExternalTranscriptSessionCreate(
        appointment_id="appointment_01kzy09rxaepjt63rb0pv1256p",
        practitioner_id="doctor_01kzy09rx9emh9n9q0p0e6neam",
        patient_id="patient_01kzy09rxben2txt3pfy34bn6h",
        consent_record_id=uuid4(),
        modality="virtual",
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
@pytest.mark.parametrize("response_mode", [None, "chunk_upload"])
async def test_client_creates_session_without_scheduling_source(
    response_mode: str | None,
) -> None:
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
        assert request.url.path == "/v1/sessions"
        body = json.loads(request.content)
        assert set(body) == {
            "appointment_id",
            "practitioner_id",
            "patient_id",
            "consent_record_id",
            "modality",
        }
        response_body = {
            **body,
            "id": str(session_id),
            "status": "created",
            "created_at": datetime.now(UTC).isoformat(),
        }
        if response_mode is not None:
            response_body["ingest_mode"] = response_mode
        return httpx.Response(201, json=response_body)

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
    assert result.ingest_mode == "chunk_upload"


@pytest.mark.asyncio
async def test_ordinary_session_response_rejects_external_mode() -> None:
    payload = SessionCreate(
        appointment_id="appointment_01kzy09rxaepjt63rb0pv1256p",
        practitioner_id="doctor_01kzy09rx9emh9n9q0p0e6neam",
        patient_id="patient_01kzy09rxben2txt3pfy34bn6h",
        consent_record_id=uuid4(),
        modality="virtual",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            201,
            json={
                **json.loads(request.content),
                "id": str(uuid4()),
                "ingest_mode": "external_async_transcript",
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
            credential=StaticCredential(),
            client=http_client,
        )
        with pytest.raises(ScribeWorkflowRetryableError) as exc_info:
            await client.create_session(
                uuid4(),
                payload,
                idempotency_key="session-stable-key-2000",
            )

    assert exc_info.value.code == "scribe_invalid_response"


@pytest.mark.asyncio
async def test_external_session_uses_existing_workload_boundary() -> None:
    tenant_id = uuid4()
    credential = StaticCredential()
    session_id = uuid4()
    payload = external_session_payload()
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        body = json.loads(request.content)
        assert set(body) == {
            "appointment_id",
            "practitioner_id",
            "patient_id",
            "consent_record_id",
            "modality",
        }
        assert {
            "tenant_id",
            "ingest_mode",
            "patient_name",
            "date_of_birth",
            "scheduling_provider",
            "transcript",
        }.isdisjoint(body)
        return httpx.Response(
            201,
            json={
                **body,
                "id": str(session_id),
                "ingest_mode": "external_async_transcript",
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
        result = await client.create_external_transcript_session(
            tenant_id,
            payload,
            idempotency_key="external-session-stable-key-1000",
        )

    assert result.id == session_id
    assert result.ingest_mode == "external_async_transcript"
    assert credential.scopes == ["api://scribe-api/.default"]
    assert len(seen) == 1
    assert seen[0].url.path == "/v1/external-transcript-sessions"
    assert seen[0].headers["authorization"] == "Bearer test-access-token"
    assert seen[0].headers["x-tenant-id"] == str(tenant_id)
    assert seen[0].headers["idempotency-key"] == "external-session-stable-key-1000"


@pytest.mark.parametrize(
    "response_patch",
    [
        {},
        {"ingest_mode": "chunk_upload"},
        {
            "ingest_mode": "external_async_transcript",
            "patient_name": "Synthetic Patient",
        },
    ],
    ids=["missing-mode", "wrong-mode", "unknown-field"],
)
@pytest.mark.asyncio
async def test_external_session_response_requires_server_pinned_mode(
    response_patch: dict[str, str],
) -> None:
    payload = external_session_payload()

    def handler(request: httpx.Request) -> httpx.Response:
        response_body = {
            **json.loads(request.content),
            "id": str(uuid4()),
            "status": "created",
            "created_at": datetime.now(UTC).isoformat(),
            **response_patch,
        }
        return httpx.Response(201, json=response_body)

    async with httpx.AsyncClient(
        base_url="https://scribe.internal.example",
        transport=httpx.MockTransport(handler),
    ) as http_client:
        client = HttpScribeWorkflowClient(
            base_url="https://scribe.internal.example",
            scope="api://scribe-api/.default",
            timeout_seconds=5,
            credential=StaticCredential(),
            client=http_client,
        )
        with pytest.raises(ScribeWorkflowRetryableError) as exc_info:
            await client.create_external_transcript_session(
                uuid4(),
                payload,
                idempotency_key="external-session-stable-key-2000",
            )

    assert exc_info.value.code == "scribe_invalid_response"
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__suppress_context__
    rendered_traceback = "".join(traceback.format_exception(exc_info.value))
    assert "Synthetic Patient" not in rendered_traceback


@pytest.mark.asyncio
async def test_external_session_opaque_admission_denial_is_rejected() -> None:
    sensitive_body = "admission details must remain opaque"

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text=sensitive_body)

    async with httpx.AsyncClient(
        base_url="https://scribe.internal.example",
        transport=httpx.MockTransport(handler),
    ) as http_client:
        client = HttpScribeWorkflowClient(
            base_url="https://scribe.internal.example",
            scope="api://scribe-api/.default",
            timeout_seconds=5,
            credential=StaticCredential(),
            client=http_client,
        )
        with pytest.raises(ScribeWorkflowRejectedError) as exc_info:
            await client.create_external_transcript_session(
                uuid4(),
                external_session_payload(),
                idempotency_key="external-session-stable-key-3000",
            )

    assert exc_info.value.code == "scribe_http_404"
    assert sensitive_body not in str(exc_info.value)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("tenant_id", "00000000-0000-0000-0000-000000000001"),
        ("ingest_mode", "external_async_transcript"),
        ("patient_name", "Synthetic Patient"),
        ("date_of_birth", "2000-01-01"),
        ("scheduling_provider", "synthetic_scheduler"),
        ("transcript", "Synthetic transcript text"),
    ],
)
def test_external_session_request_rejects_authority_and_phi_fields(
    field: str,
    value: str,
) -> None:
    payload = external_session_payload().model_dump(mode="json")
    payload[field] = value

    with pytest.raises(ValidationError):
        ExternalTranscriptSessionCreate.model_validate(payload)


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

    with pytest.raises(ScribeWorkflowDisabledError):
        await client.create_external_transcript_session(
            uuid4(),
            external_session_payload(),
            idempotency_key="external-session-stable-key-4000",
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


@pytest.mark.parametrize("timeout_seconds", [0, 60.1])
def test_settings_enforce_request_timeout_contract(timeout_seconds: float) -> None:
    with pytest.raises(ValueError, match="between 1 and 60 seconds"):
        ScribeWorkflowSettings(
            enabled=True,
            base_url="https://scribe.internal.example",
            scope="api://scribe-api/.default",
            managed_identity_client_id=str(uuid4()),
            timeout_seconds=timeout_seconds,
        )


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


@pytest.mark.asyncio
async def test_builder_applies_scale_to_zero_request_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_options: dict[str, object] = {}
    real_async_client = httpx.AsyncClient

    def client_factory(
        *,
        base_url: str,
        timeout: float,
        follow_redirects: bool,
        trust_env: bool,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> httpx.AsyncClient:
        assert transport is None
        captured_options.update(
            base_url=base_url,
            timeout=timeout,
            follow_redirects=follow_redirects,
            trust_env=trust_env,
        )
        return real_async_client(
            base_url=base_url,
            timeout=timeout,
            follow_redirects=follow_redirects,
            trust_env=trust_env,
            transport=httpx.MockTransport(lambda _: httpx.Response(500)),
        )

    monkeypatch.setattr(scribe_workflow.httpx, "AsyncClient", client_factory)
    configured = ScribeWorkflowSettings(
        enabled=True,
        base_url="https://scribe.internal.example",
        scope="api://scribe-api/.default",
        managed_identity_client_id=str(uuid4()),
        timeout_seconds=60,
    )
    credential = StaticCredential()

    client = build_scribe_workflow_client(configured, lambda _: credential)
    await client.close()

    assert captured_options == {
        "base_url": "https://scribe.internal.example",
        "timeout": 60,
        "follow_redirects": False,
        "trust_env": False,
    }
    assert credential.closed


@pytest.mark.parametrize(
    "invalid_origin",
    [
        "https://scribe.internal.example/path",
        "https://scribe.internal.example:8443",
        "https://user:password@scribe.internal.example",
        "https://scribe.internal.example?token=x",
        "https://scribe.internal.example/#fragment",
        "https://[invalid",
    ],
)
def test_invalid_origin_is_rejected_before_creating_a_credential(invalid_origin):
    from dataclasses import replace

    configured = replace(settings(), base_url=invalid_origin)
    assert not configured.is_complete()
    with pytest.raises(ValueError, match="incomplete"):
        build_scribe_workflow_client(
            configured, lambda _: pytest.fail("credential allocated for invalid origin")
        )


@pytest.mark.asyncio
async def test_timeout_is_retryable_without_automatic_mutation_retry() -> None:
    credential = StaticCredential()
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        raise httpx.ReadTimeout("synthetic timeout", request=request)

    async with httpx.AsyncClient(
        base_url="https://scribe.internal.example",
        transport=httpx.MockTransport(handler),
    ) as http_client:
        client = HttpScribeWorkflowClient(
            base_url="https://scribe.internal.example",
            scope="api://scribe-api/.default",
            timeout_seconds=60,
            credential=credential,
            client=http_client,
        )
        with pytest.raises(ScribeWorkflowRetryableError) as exc_info:
            await client.create_consent(
                uuid4(),
                consent_payload(),
                idempotency_key="consent-stable-key-timeout-1000",
            )
        await client.close()

    assert exc_info.value.code == "scribe_request_timeout"
    assert credential.closed
    assert len(requests) == 1
    assert requests[0].headers["idempotency-key"] == ("consent-stable-key-timeout-1000")
