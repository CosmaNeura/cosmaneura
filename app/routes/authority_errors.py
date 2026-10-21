from __future__ import annotations

import re
from uuid import uuid4

from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

EMBED_CONTRACT_VERSION = "1.0"


class AuthorityProblemDetails(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str
    title: str
    status: int
    code: str
    detail: str
    trace_id: str
    retryable: bool
    retry_after_seconds: int | None = None


def new_trace_id() -> str:
    return f"trace_{uuid4().hex}"


def normalize_trace_id(trace_id: str | None) -> str:
    """Return a contract-safe caller trace or a fresh opaque replacement."""

    if trace_id is None or re.fullmatch(r"[A-Za-z0-9._~-]{16,128}", trace_id) is None:
        return new_trace_id()
    return trace_id


def _problem_response(
    *,
    status_code: int,
    code: str,
    title: str,
    detail: str,
    retryable: bool,
    trace_id: str | None,
    headers: dict[str, str],
    retry_after_seconds: int | None = None,
) -> JSONResponse:
    if retry_after_seconds is not None:
        if type(retry_after_seconds) is not int or not 1 <= retry_after_seconds <= 60:
            raise ValueError("Invalid bounded retry interval")
        headers = {**headers, "Retry-After": str(retry_after_seconds)}
    resolved_trace_id = trace_id or new_trace_id()
    body = AuthorityProblemDetails(
        type=f"https://contracts.cosmaneura.com/problems/{code.lower().replace('_', '-')}",
        title=title,
        status=status_code,
        code=code,
        detail=detail,
        trace_id=resolved_trace_id,
        retryable=retryable,
        retry_after_seconds=retry_after_seconds,
    )
    return JSONResponse(
        status_code=status_code,
        content=body.model_dump(exclude_none=True),
        media_type="application/problem+json",
        headers={"X-Trace-ID": resolved_trace_id, **headers},
    )


def token_problem_response(
    *,
    status_code: int,
    code: str,
    title: str,
    detail: str,
    retryable: bool = False,
    trace_id: str | None = None,
) -> JSONResponse:
    return _problem_response(
        status_code=status_code,
        code=code,
        title=title,
        detail=detail,
        retryable=retryable,
        trace_id=trace_id,
        headers={
            "X-Cosmaneura-Embed-Version": EMBED_CONTRACT_VERSION,
            "Cache-Control": "no-store",
        },
    )


def agent_problem_response(
    *,
    status_code: int,
    code: str,
    title: str,
    detail: str,
    retryable: bool = False,
    trace_id: str | None = None,
    retry_after_seconds: int | None = None,
) -> JSONResponse:
    return _problem_response(
        status_code=status_code,
        code=code,
        title=title,
        detail=detail,
        retryable=retryable,
        trace_id=trace_id,
        headers={"Cache-Control": "no-store"},
        retry_after_seconds=retry_after_seconds,
    )
