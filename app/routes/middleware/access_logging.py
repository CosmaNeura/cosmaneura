"""Application-owned HTTP access logging with credential-safe URL handling."""

from __future__ import annotations

from time import perf_counter
from typing import NamedTuple
from urllib.parse import quote

import structlog
from fastapi import FastAPI, Request
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response
from starlette.types import Scope
from structlog_config.fastapi_access_logger import (
    get_client_addr,
    get_route_name,
    is_static_assets_request,
)

log = structlog.get_logger("access_log")

RECALL_ZAK_PATH_PREFIX = "/external/v1/recall/zak/"
RECALL_ZAK_PATH_TEMPLATE = f"{RECALL_ZAK_PATH_PREFIX}{{token}}"
REDACTED_QUERY = "[redacted]"


class SafeAccessTarget(NamedTuple):
    path: str
    query: str
    target: str


def safe_access_target(scope: Scope) -> SafeAccessTarget:
    """Return access-log fields without exposing a Recall ZAK bearer token."""

    path = str(scope.get("path") or "-")
    raw_query = scope.get("query_string", b"")
    if isinstance(raw_query, bytes):
        query = raw_query.decode("ascii", errors="replace")
    else:
        query = str(raw_query)

    if path.startswith(RECALL_ZAK_PATH_PREFIX):
        safe_path = RECALL_ZAK_PATH_TEMPLATE
        safe_query = REDACTED_QUERY if query else ""
        target = safe_path
        if safe_query:
            target = f"{target}?{safe_query}"
        return SafeAccessTarget(safe_path, safe_query, target)

    target = quote(path)
    if query:
        target = f"{target}?{query}"
    return SafeAccessTarget(path, query, target)


def _safe_route_name(route_name: str) -> str:
    if route_name.startswith(RECALL_ZAK_PATH_PREFIX):
        return RECALL_ZAK_PATH_TEMPLATE
    return route_name


def add_middleware(app: FastAPI) -> None:
    """Install the global FastAPI access logger with URL credential redaction."""

    @app.middleware("http")
    async def access_log_middleware(
        request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        scope = request.scope
        if scope["type"] != "http":
            return await call_next(request)

        started_at = perf_counter()
        response = await call_next(request)
        elapsed = perf_counter() - started_at

        target = safe_access_target(scope)
        route_name = _safe_route_name(get_route_name(app, scope))
        log_method = log.debug if is_static_assets_request(scope) else log.info
        log_method(
            f"{response.status_code} {scope['method']} {target.target}",
            time=round(elapsed * 1000),
            status=response.status_code,
            method=scope["method"],
            path=target.path,
            query=target.query,
            client_ip=get_client_addr(scope),
            route=route_name,
        )

        return response
