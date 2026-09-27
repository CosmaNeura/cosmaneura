"""Synthetic transport checks, isolated from the repository's external test hooks."""

from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

import httpx
import pytest
from azure.core.credentials import AccessToken

from app.services.scribe_workflow import (
    DisabledScribeWorkflowClient,
    HttpScribeWorkflowClient,
    ScribeWorkflowConflictError,
    ScribeWorkflowDisabledError,
    ScribeWorkflowRejectedError,
    ScribeWorkflowRetryableError,
)


class Credential:
    calls = 0

    async def get_token(self, *scopes, **kwargs):
        assert scopes == ("api://synthetic-scribe/.default",)
        self.calls += 1
        return AccessToken("synthetic-token", 4_000_000_000)

    async def close(self):
        pass


def status_body(session_id, **updates):
    return {
        "session_id": str(session_id),
        "status": "completed",
        "transcript_available": True,
        "updated_at": datetime.now(UTC).isoformat(),
        **updates,
    }


def transcript_body(session_id, **updates):
    return {
        "id": str(uuid4()),
        "session_id": str(session_id),
        "created_at": datetime.now(UTC).isoformat(),
        "language_code": "en-US",
        "stt_provider": "synthetic",
        "segments": [
            {
                "content": "Invented appointment confirmation.",
                "start_ms": 0,
                "end_ms": 1000,
                "speaker_id": "speaker-0",
                "speaker_role": "unknown",
                "confidence": None,
            }
        ],
        **updates,
    }


def client_for(http_client, credential=None):
    return HttpScribeWorkflowClient(
        base_url="https://scribe.test.invalid",
        scope="api://synthetic-scribe/.default",
        timeout_seconds=5,
        credential=credential or Credential(),
        client=http_client,
    )


@pytest.mark.asyncio
async def test_source_transcript_reads_status_first_and_preserves_provenance():
    tenant_id, session_id = uuid4(), uuid4()
    transcript = transcript_body(session_id)
    seen = []
    credential = Credential()

    def handler(request):
        seen.append(request)
        result = status_body(session_id) if len(seen) == 1 else transcript
        return httpx.Response(200, json=result)

    async with httpx.AsyncClient(
        base_url="https://scribe.test.invalid", transport=httpx.MockTransport(handler)
    ) as http_client:
        result = await client_for(http_client, credential).get_capture_transcript(
            tenant_id, session_id
        )

    assert result.created_at == datetime.fromisoformat(transcript["created_at"])
    assert result.model_dump(mode="json", exclude={"created_at"}) == {
        key: value for key, value in transcript.items() if key != "created_at"
    }
    assert credential.calls == 2
    assert [request.url.path for request in seen] == [
        f"/v1/sessions/{session_id}/transcription-status",
        f"/v1/sessions/{session_id}/transcript",
    ]
    for request in seen:
        assert request.method == "GET"
        assert not request.content
        assert request.headers["authorization"] == "Bearer synthetic-token"
        assert request.headers["x-tenant-id"] == str(tenant_id)
        assert request.headers["cache-control"] == "no-store"
        assert "idempotency-key" not in request.headers


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["not_requested", "queued", "processing", "terminal"])
async def test_unready_status_never_fetches_transcript(state):
    session_id = uuid4()
    paths = []

    def handler(request):
        paths.append(request.url.path)
        return httpx.Response(
            200, json=status_body(session_id, status=state, transcript_available=False)
        )

    async with httpx.AsyncClient(
        base_url="https://scribe.test.invalid", transport=httpx.MockTransport(handler)
    ) as http_client:
        with pytest.raises(
            ScribeWorkflowConflictError, match="scribe_transcript_not_ready"
        ):
            await client_for(http_client).get_capture_transcript(uuid4(), session_id)
    assert paths == [f"/v1/sessions/{session_id}/transcription-status"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code,error",
    [
        (401, ScribeWorkflowRejectedError),
        (403, ScribeWorkflowRejectedError),
        (404, ScribeWorkflowRejectedError),
        (409, ScribeWorkflowConflictError),
        (429, ScribeWorkflowRetryableError),
        (503, ScribeWorkflowRetryableError),
    ],
)
async def test_remote_denials_are_opaque_and_not_retried(code, error):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(code, text="synthetic-sensitive-payload")

    async with httpx.AsyncClient(
        base_url="https://scribe.test.invalid", transport=httpx.MockTransport(handler)
    ) as http_client:
        with pytest.raises(error) as exc:
            await client_for(http_client).get_capture_transcript(uuid4(), uuid4())
    assert "synthetic-sensitive-payload" not in str(exc.value)
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "patch",
    [
        {"transcript_available": False},
        {"transcript_available": "true"},
        {"status": "processing"},
        {"status": "new_future_status"},
        {"updated_at": "2026-09-16T12:00:00"},
        {"patient_name": "synthetic-extra"},
    ],
)
async def test_invalid_status_is_not_treated_as_ready(patch):
    session_id = uuid4()
    async with httpx.AsyncClient(
        base_url="https://scribe.test.invalid",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json=status_body(session_id, **patch))
        ),
    ) as http_client:
        with pytest.raises(
            ScribeWorkflowRetryableError, match="scribe_invalid_response"
        ):
            await client_for(http_client).get_capture_status(uuid4(), session_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("resource", ["status", "transcript"])
async def test_foreign_session_response_is_rejected(resource):
    session_id = uuid4()

    def handler(request):
        if request.url.path.endswith("transcription-status"):
            return httpx.Response(
                200, json=status_body(uuid4() if resource == "status" else session_id)
            )
        return httpx.Response(200, json=transcript_body(uuid4()))

    async with httpx.AsyncClient(
        base_url="https://scribe.test.invalid", transport=httpx.MockTransport(handler)
    ) as http_client:
        with pytest.raises(
            ScribeWorkflowRejectedError, match="scribe_response_scope_mismatch"
        ):
            await client_for(http_client).get_capture_transcript(uuid4(), session_id)


@pytest.mark.asyncio
async def test_revoked_consent_between_status_and_transcript_is_denied():
    session_id = uuid4()

    def handler(request):
        if request.url.path.endswith("transcription-status"):
            return httpx.Response(200, json=status_body(session_id))
        return httpx.Response(403, text="synthetic revoked consent")

    async with httpx.AsyncClient(
        base_url="https://scribe.test.invalid", transport=httpx.MockTransport(handler)
    ) as http_client:
        with pytest.raises(ScribeWorkflowRejectedError, match="scribe_http_403"):
            await client_for(http_client).get_capture_transcript(uuid4(), session_id)


@pytest.mark.asyncio
async def test_redirect_is_not_followed_even_for_injected_client():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(307, headers={"Location": "https://foreign.test.invalid"})

    async with httpx.AsyncClient(
        base_url="https://scribe.test.invalid",
        follow_redirects=True,
        transport=httpx.MockTransport(handler),
    ) as http_client:
        with pytest.raises(ScribeWorkflowRejectedError, match="scribe_http_307"):
            await client_for(http_client).get_capture_status(uuid4(), uuid4())
    assert len(seen) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["oversized", "malformed", "non_json", "timeout"])
async def test_bad_transport_never_exposes_payload(kind):
    calls = []

    def handler(request):
        calls.append(request)
        if kind == "timeout":
            raise httpx.ReadTimeout("synthetic-sensitive-payload", request=request)
        body = "x" * 65_537 if kind == "oversized" else "synthetic-sensitive-payload"
        return httpx.Response(
            200,
            content=body,
            headers={
                "content-type": "text/html"
                if kind == "non_json"
                else "application/json"
            },
        )

    async with httpx.AsyncClient(
        base_url="https://scribe.test.invalid", transport=httpx.MockTransport(handler)
    ) as http_client:
        with pytest.raises(ScribeWorkflowRetryableError) as exc:
            await client_for(http_client).get_capture_status(uuid4(), uuid4())
    assert "synthetic-sensitive-payload" not in str(exc.value)
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "segment_patch",
    [
        {"end_ms": 0},
        {"start_ms": 1000},
        {"start_ms": True},
        {"speaker_role": "verified_clinician"},
        {"content": ""},
    ],
)
async def test_malformed_transcript_is_rejected(segment_patch):
    session_id = uuid4()
    body = transcript_body(session_id)
    body["segments"][0].update(segment_patch)

    def handler(request):
        return httpx.Response(
            200,
            json=(
                status_body(session_id)
                if request.url.path.endswith("transcription-status")
                else body
            ),
        )

    async with httpx.AsyncClient(
        base_url="https://scribe.test.invalid", transport=httpx.MockTransport(handler)
    ) as http_client:
        with pytest.raises(
            ScribeWorkflowRetryableError, match="scribe_invalid_response"
        ):
            await client_for(http_client).get_capture_transcript(uuid4(), session_id)


@pytest.mark.asyncio
async def test_disabled_reads_and_invalid_ids_never_call_transport():
    disabled = DisabledScribeWorkflowClient()
    for method in (disabled.get_capture_status, disabled.get_capture_transcript):
        with pytest.raises(ScribeWorkflowDisabledError):
            await method(uuid4(), uuid4())

    def forbidden(_):
        raise AssertionError("Transport must not be called")

    async with httpx.AsyncClient(
        base_url="https://scribe.test.invalid", transport=httpx.MockTransport(forbidden)
    ) as http_client:
        with pytest.raises(ScribeWorkflowRejectedError, match="scribe_invalid_scope"):
            await client_for(http_client).get_capture_status(
                uuid4(), cast(Any, "../foreign")
            )
