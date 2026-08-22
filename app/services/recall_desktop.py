"""Recall Desktop SDK upload adapter.

The adapter is deliberately small and provider-specific. It receives only a
random operation correlation reference and returns the one-use SDK capability;
patient, clinician, encounter, tenant, and consent data never enter Recall
metadata. Calls remain disabled unless the separate provider-create gate is
explicitly enabled.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

from app.services.capture_provider import (
    CaptureObservation,
    CaptureProviderDisabled,
    CaptureProviderOutcomeUncertain,
    CaptureProviderRejected,
    CaptureProviderResponseInvalid,
    CaptureProvisioning,
)

RECORDING_RETENTION_HOURS = 6
CORRELATION_METADATA_KEY = "cosmas_capture_operation_ref"
_CORRELATION_RE = re.compile(r"^[A-Za-z0-9_-]{32,128}$")
_PROVIDER_REF_RE = re.compile(r"^[A-Za-z0-9_-]{1,256}$")
_CAPABILITY_RE = re.compile(r"^[\x21-\x7e]{16,4096}$")
_CURSOR_RE = re.compile(r"^[A-Za-z0-9_=-]{1,2048}$")
_SDK_UPLOAD_STATES = frozenset(
    {
        "pending",
        "recording_started",
        "recording_ended",
        "uploading",
        "complete",
        "failed",
    }
)
_HTTP_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
_MAX_RECONCILIATION_PAGES = 5


def _validate_correlation_ref(value: str) -> str:
    if not _CORRELATION_RE.fullmatch(value):
        raise ValueError("invalid capture correlation reference")
    return value


def _provider_ref(value: Any, *, required: bool = True) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str) or not _PROVIDER_REF_RE.fullmatch(value):
        raise CaptureProviderResponseInvalid("provider response is invalid")
    return value


def _status(value: Any) -> str:
    if not isinstance(value, str) or value not in _SDK_UPLOAD_STATES:
        raise CaptureProviderResponseInvalid("provider response is invalid")
    return value


def _object(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise CaptureProviderResponseInvalid("provider response is invalid")
    return value


class RecallDesktopAdapter:
    """Provision audio-only Recall SDK uploads behind a hard kill switch."""

    def __init__(
        self,
        *,
        api_key: str,
        api_base: str,
        provider_create_enabled: bool = False,
        client: httpx.Client | None = None,
    ) -> None:
        parsed = urlparse(api_base)
        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("invalid capture provider API base")
        if (
            not api_key
            or len(api_key) > 4096
            or any(character.isspace() for character in api_key)
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError("invalid capture provider credential")
        self._api_key = api_key
        self._api_base = api_base.rstrip("/")
        self._provider_create_enabled = provider_create_enabled
        self._client = client

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Token {self._api_key}",
            "Content-Type": "application/json",
        }

    def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        try:
            if self._client is not None:
                return self._client.request(
                    method, url, headers=self._headers, **kwargs
                )
            with httpx.Client(timeout=_HTTP_TIMEOUT) as client:
                return client.request(method, url, headers=self._headers, **kwargs)
        except httpx.TransportError:
            raise CaptureProviderOutcomeUncertain(
                "capture provider outcome is uncertain"
            ) from None

    def create_capture(self, *, correlation_ref: str) -> CaptureProvisioning:
        correlation_ref = _validate_correlation_ref(correlation_ref)
        if not self._provider_create_enabled:
            raise CaptureProviderDisabled("capture provider creation is disabled")

        payload = {
            "recording_config": {
                "video_mixed_mp4": None,
                "audio_mixed_mp3": {},
                "retention": {
                    "type": "timed",
                    "hours": RECORDING_RETENTION_HOURS,
                },
            },
            "metadata": {CORRELATION_METADATA_KEY: correlation_ref},
        }
        response = self._request(
            "POST",
            f"{self._api_base}/sdk_upload/",
            json=payload,
        )
        # A conflict does not prove that the provider rejected the original
        # create. Treat it like the other retryable/ambiguous responses and
        # reconcile by the pre-committed correlation reference.
        if response.status_code in {408, 409, 425, 429}:
            raise CaptureProviderOutcomeUncertain(
                "capture provider outcome is uncertain"
            )
        if 400 <= response.status_code < 500:
            raise CaptureProviderRejected("capture provider rejected the request")
        if response.status_code != 201:
            raise CaptureProviderOutcomeUncertain(
                "capture provider outcome is uncertain"
            )
        try:
            data = _object(response.json())
            capture_container_ref = _provider_ref(data.get("id")) or ""
            capture_status = _status(data.get("status"))
            recording_ref = _provider_ref(data.get("recording_id"), required=False)
        except (ValueError, TypeError, CaptureProviderResponseInvalid):
            raise CaptureProviderOutcomeUncertain(
                "capture provider outcome is uncertain"
            ) from None

        capability = data.get("upload_token")
        if not isinstance(capability, str) or not _CAPABILITY_RE.fullmatch(capability):
            raise CaptureProviderOutcomeUncertain(
                "capture provider outcome is uncertain"
            )
        return CaptureProvisioning(
            capture_container_ref=capture_container_ref,
            capture_capability=capability,
            status=capture_status,
            recording_ref=recording_ref,
        )

    def _page_cursor(self, next_url: Any) -> str | None:
        if next_url is None:
            return None
        if not isinstance(next_url, str):
            raise CaptureProviderResponseInvalid("provider response is invalid")
        parsed_next = urlparse(next_url)
        parsed_base = urlparse(self._api_base)
        if (
            parsed_next.scheme != parsed_base.scheme
            or parsed_next.netloc != parsed_base.netloc
            or parsed_next.path != f"{parsed_base.path.rstrip('/')}/sdk_upload/"
        ):
            raise CaptureProviderResponseInvalid("provider response is invalid")
        cursors = parse_qs(parsed_next.query).get("cursor", [])
        if len(cursors) != 1 or not _CURSOR_RE.fullmatch(cursors[0]):
            raise CaptureProviderResponseInvalid("provider response is invalid")
        return cursors[0]

    def find_captures(self, *, correlation_ref: str) -> tuple[CaptureObservation, ...]:
        """Bounded lookup used only after an ambiguous create outcome."""

        correlation_ref = _validate_correlation_ref(correlation_ref)
        cursor: str | None = None
        matches: list[CaptureObservation] = []
        for _ in range(_MAX_RECONCILIATION_PAGES):
            params = {"cursor": cursor} if cursor else None
            response = self._request(
                "GET",
                f"{self._api_base}/sdk_upload/",
                params=params,
            )
            if response.status_code != 200:
                raise CaptureProviderOutcomeUncertain(
                    "capture provider reconciliation is unavailable"
                )
            try:
                page = _object(response.json())
                results = page.get("results")
                if not isinstance(results, list):
                    raise CaptureProviderResponseInvalid("provider response is invalid")
                for raw_result in results:
                    result = _object(raw_result)
                    metadata = result.get("metadata")
                    if not isinstance(metadata, dict):
                        continue
                    if metadata.get(CORRELATION_METADATA_KEY) != correlation_ref:
                        continue
                    capability = result.get("upload_token")
                    if not isinstance(capability, str) or not _CAPABILITY_RE.fullmatch(
                        capability
                    ):
                        raise CaptureProviderResponseInvalid(
                            "provider response is invalid"
                        )
                    matches.append(
                        CaptureObservation(
                            capture_container_ref=_provider_ref(result.get("id")) or "",
                            capture_capability=capability,
                            status=_status(result.get("status")),
                            recording_ref=_provider_ref(
                                result.get("recording_id"), required=False
                            ),
                        )
                    )
                cursor = self._page_cursor(page.get("next"))
            except (ValueError, TypeError) as exc:
                if isinstance(exc, CaptureProviderResponseInvalid):
                    raise
                raise CaptureProviderResponseInvalid(
                    "provider response is invalid"
                ) from None
            if cursor is None:
                break
        else:
            raise CaptureProviderOutcomeUncertain(
                "capture provider reconciliation exceeded its bound"
            )
        return tuple(matches)
