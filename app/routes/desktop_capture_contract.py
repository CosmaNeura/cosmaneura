"""Shared OpenAPI-only response types for the native desktop boundary."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class DesktopApiErrorResponse(BaseModel):
    """Value-free error body used by every `/desktop/v1` failure response."""

    model_config = ConfigDict(extra="forbid")

    detail: str


_ERROR_DESCRIPTIONS = {
    400: "The request or authorization grant was rejected.",
    401: "The desktop bearer was missing or invalid.",
    403: "The requested capture transition was denied.",
    409: "The requested capture transition conflicts with current state.",
    422: "The request did not match the service contract.",
    503: "Desktop capture is unavailable.",
}


def desktop_error_responses(*status_codes: int) -> dict[int | str, dict[str, Any]]:
    """Build documented error responses without changing runtime behavior."""

    return {
        status_code: {
            "model": DesktopApiErrorResponse,
            "description": _ERROR_DESCRIPTIONS[status_code],
        }
        for status_code in status_codes
    }
