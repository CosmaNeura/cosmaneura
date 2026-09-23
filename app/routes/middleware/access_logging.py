"""Application-owned HTTP access logging with credential-safe URL handling."""

from __future__ import annotations

from time import perf_counter
from typing import NamedTuple

import structlog
from fastapi import FastAPI, Request
from fastapi.routing import iter_route_contexts
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response
from starlette.routing import Match
from starlette.types import Scope
from structlog_config.fastapi_access_logger import is_static_assets_request

log = structlog.get_logger("access_log")

UNMATCHED_ROUTE = "unmatched"


class SafeAccessTarget(NamedTuple):
    path_template: str
    route: str
    target: str


def safe_access_target(app: FastAPI, scope: Scope) -> SafeAccessTarget:
    """Return only developer-defined route metadata, never request targets."""

    route = next(
        (
            candidate
            for candidate in iter_route_contexts(app.router.routes)
            if candidate.matches(scope)[0] == Match.FULL
        ),
        None,
    )
    if route is None:
        return SafeAccessTarget(UNMATCHED_ROUTE, UNMATCHED_ROUTE, UNMATCHED_ROUTE)

    template = getattr(route, "path", None)
    path_template = template if isinstance(template, str) else UNMATCHED_ROUTE
    route_name = route.name
    if not isinstance(route_name, str) or route_name == scope.get("path"):
        route_name = UNMATCHED_ROUTE
    return SafeAccessTarget(path_template, route_name, path_template)


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

        target = safe_access_target(app, scope)
        log_method = log.debug if is_static_assets_request(scope) else log.info
        log_method(
            f"{response.status_code} {scope['method']} {target.target}",
            time=round(elapsed * 1000),
            status=response.status_code,
            method=scope["method"],
            path=target.path_template,
            route=target.route,
        )

        return response
