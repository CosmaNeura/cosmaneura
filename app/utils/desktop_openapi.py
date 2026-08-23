"""Generated contract boundary for the native desktop capture client."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from fastapi.routing import APIRoute

from app.routes.utils.openapi import simplify_operation_ids

DESKTOP_API_PREFIX = "/desktop/v1"
DESKTOP_OPENAPI_PATH = Path("contracts/desktop-v1.openapi.json")
DESKTOP_OPENAPI_TITLE = "Cosmaneura Desktop Capture API"
DESKTOP_OPENAPI_VERSION = "1.0.0"
DESKTOP_BEARER_SCHEME = "desktopBearer"


def _apply_desktop_auth_contract(schema: dict[str, Any]) -> None:
    """Describe the manually parsed bearer and idempotency headers precisely."""

    components = schema.setdefault("components", {})
    components.setdefault("securitySchemes", {})[DESKTOP_BEARER_SCHEME] = {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "opaque",
        "description": (
            "Short-lived opaque desktop session bearer. Keep it in the desktop "
            "main process and never expose it to the renderer."
        ),
    }

    for path, path_item in schema["paths"].items():
        if path == f"{DESKTOP_API_PREFIX}/oauth/token":
            continue
        for operation in path_item.values():
            if not isinstance(operation, dict) or "operationId" not in operation:
                continue
            operation["security"] = [{DESKTOP_BEARER_SCHEME: []}]
            parameters = operation.get("parameters", [])
            operation["parameters"] = [
                parameter
                for parameter in parameters
                if not (
                    parameter.get("in") == "header"
                    and parameter.get("name", "").lower() == "authorization"
                )
            ]
            for parameter in operation["parameters"]:
                if (
                    parameter.get("in") == "header"
                    and parameter.get("name", "").lower() == "idempotency-key"
                ):
                    parameter["required"] = True
                    parameter["description"] = (
                        "Unique key for this logical operation; reuse it only when "
                        "retrying the same request."
                    )
                    parameter["schema"] = {
                        "type": "string",
                        "minLength": 8,
                        "maxLength": 255,
                        "pattern": "^[A-Za-z0-9._~-]+$",
                        "title": "Idempotency-Key",
                    }


def generate_desktop_openapi_schema(app: FastAPI) -> dict[str, Any]:
    """Generate the exact public contract exposed to the desktop companion."""

    simplify_operation_ids(app)
    routes = [
        route
        for route in app.routes
        if isinstance(route, APIRoute)
        and (
            route.path == DESKTOP_API_PREFIX
            or route.path.startswith(f"{DESKTOP_API_PREFIX}/")
        )
    ]
    if not routes:
        raise RuntimeError(f"no routes found under {DESKTOP_API_PREFIX}")

    schema = get_openapi(
        title=DESKTOP_OPENAPI_TITLE,
        version=DESKTOP_OPENAPI_VERSION,
        routes=routes,
    )
    _apply_desktop_auth_contract(schema)
    return schema


def render_desktop_openapi_schema(app: FastAPI) -> str:
    """Render a stable, reviewable JSON representation of the contract."""

    return (
        json.dumps(
            generate_desktop_openapi_schema(app),
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
