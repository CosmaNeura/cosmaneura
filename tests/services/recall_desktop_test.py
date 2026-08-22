from __future__ import annotations

import json

import httpx
import pytest

from app.services.capture_provider import (
    CaptureObservation,
    CaptureProviderDisabled,
    CaptureProviderOutcomeUncertain,
    CaptureProviderRejected,
    CaptureProviderResponseInvalid,
)
from app.services.recall_desktop import (
    CORRELATION_METADATA_KEY,
    RECORDING_RETENTION_HOURS,
    RecallDesktopAdapter,
)

CORRELATION_REF = "capture_operation_ref_0123456789abcdef"


def _adapter(
    handler: httpx.MockTransport,
    *,
    enabled: bool = True,
) -> RecallDesktopAdapter:
    return RecallDesktopAdapter(
        api_key="test-only-api-key",
        api_base="https://us-west-2.recall.test/api/v1",
        provider_create_enabled=enabled,
        client=httpx.Client(transport=handler),
    )


def test_create_is_disabled_without_calling_provider() -> None:
    transport = httpx.MockTransport(
        lambda _request: (_ for _ in ()).throw(AssertionError("unexpected call"))
    )

    with pytest.raises(CaptureProviderDisabled):
        _adapter(transport, enabled=False).create_capture(
            correlation_ref=CORRELATION_REF
        )


def test_create_uses_only_audio_retention_and_random_correlation_metadata() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(
            201,
            json={
                "id": "upload_123",
                "status": "pending",
                "recording_id": None,
                "upload_token": "sdk_upload_token_0123456789",
            },
        )

    result = _adapter(httpx.MockTransport(handler)).create_capture(
        correlation_ref=CORRELATION_REF
    )

    request = captured["request"]
    assert request.url.path == "/api/v1/sdk_upload/"
    assert request.headers["authorization"] == "Token test-only-api-key"
    payload = json.loads(request.content)
    assert payload == {
        "recording_config": {
            "video_mixed_mp4": None,
            "audio_mixed_mp3": {},
            "retention": {
                "type": "timed",
                "hours": RECORDING_RETENTION_HOURS,
            },
        },
        "metadata": {CORRELATION_METADATA_KEY: CORRELATION_REF},
    }
    assert result.capture_container_ref == "upload_123"
    assert result.capture_capability == "sdk_upload_token_0123456789"
    assert result.recording_ref is None


@pytest.mark.parametrize("status_code", [400, 401, 403, 422])
def test_deterministic_client_rejection_is_not_replayed(status_code: int) -> None:
    adapter = _adapter(
        httpx.MockTransport(lambda _request: httpx.Response(status_code))
    )

    with pytest.raises(CaptureProviderRejected):
        adapter.create_capture(correlation_ref=CORRELATION_REF)


@pytest.mark.parametrize("status_code", [200, 408, 409, 425, 429, 500, 502, 503])
def test_non_creation_response_is_uncertain(status_code: int) -> None:
    adapter = _adapter(
        httpx.MockTransport(lambda _request: httpx.Response(status_code, json={}))
    )

    with pytest.raises(CaptureProviderOutcomeUncertain):
        adapter.create_capture(correlation_ref=CORRELATION_REF)


def test_transport_failure_is_uncertain() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout", request=request)

    with pytest.raises(CaptureProviderOutcomeUncertain) as raised:
        _adapter(httpx.MockTransport(handler)).create_capture(
            correlation_ref=CORRELATION_REF
        )
    assert raised.value.__cause__ is None


def test_invalid_success_capability_is_uncertain() -> None:
    adapter = _adapter(
        httpx.MockTransport(
            lambda _request: httpx.Response(
                201,
                json={"id": "upload_123", "status": "pending", "upload_token": ""},
            )
        )
    )

    with pytest.raises(CaptureProviderOutcomeUncertain):
        adapter.create_capture(correlation_ref=CORRELATION_REF)


@pytest.mark.parametrize(
    "capability",
    ["token_with_unicode_é_012345", "token with spaces 012345", "x" * 4097],
)
def test_invalid_success_capability_never_crosses_adapter_boundary(
    capability: str,
) -> None:
    adapter = _adapter(
        httpx.MockTransport(
            lambda _request: httpx.Response(
                201,
                json={
                    "id": "upload_123",
                    "status": "pending",
                    "upload_token": capability,
                },
            )
        )
    )

    with pytest.raises(CaptureProviderOutcomeUncertain):
        adapter.create_capture(correlation_ref=CORRELATION_REF)


@pytest.mark.parametrize(
    "api_base",
    [
        "http://us-west-2.recall.test/api/v1",
        "https://user@us-west-2.recall.test/api/v1",
        "https://user:password@us-west-2.recall.test/api/v1",
    ],
)
def test_adapter_rejects_unsafe_api_base(api_base: str) -> None:
    with pytest.raises(ValueError):
        RecallDesktopAdapter(api_key="test-key", api_base=api_base)


def test_reconciliation_is_bounded_and_matches_only_exact_metadata() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "id": "upload_other",
                            "status": "complete",
                            "recording_id": "recording_other",
                            "upload_token": "other_upload_token_0123456789",
                            "metadata": {
                                CORRELATION_METADATA_KEY: "other_ref_0123456789abcdef0123456789"
                            },
                        },
                        {
                            "id": "upload_match",
                            "status": "recording_started",
                            "recording_id": "recording_match",
                            "upload_token": "matching_upload_token_0123456789",
                            "metadata": {CORRELATION_METADATA_KEY: CORRELATION_REF},
                        },
                    ],
                    "next": (
                        "https://us-west-2.recall.test/api/v1/sdk_upload/"
                        "?cursor=cursor_2"
                    ),
                },
            )
        return httpx.Response(200, json={"results": [], "next": None})

    matches = _adapter(httpx.MockTransport(handler)).find_captures(
        correlation_ref=CORRELATION_REF
    )

    assert len(requests) == 2
    assert requests[1].url.params["cursor"] == "cursor_2"
    assert matches == (
        CaptureObservation(
            capture_container_ref="upload_match",
            capture_capability="matching_upload_token_0123456789",
            status="recording_started",
            recording_ref="recording_match",
        ),
    )


def test_reconciliation_rejects_cross_origin_pagination() -> None:
    adapter = _adapter(
        httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                json={
                    "results": [],
                    "next": "https://attacker.invalid/sdk_upload/?cursor=stolen",
                },
            )
        )
    )

    with pytest.raises(CaptureProviderResponseInvalid):
        adapter.find_captures(correlation_ref=CORRELATION_REF)


@pytest.mark.parametrize("correlation_ref", ["", "patient name", "short"])
def test_correlation_reference_is_strict(correlation_ref: str) -> None:
    adapter = _adapter(httpx.MockTransport(lambda _request: httpx.Response(500)))

    with pytest.raises(ValueError, match="correlation"):
        adapter.create_capture(correlation_ref=correlation_ref)
