"""Default-off Scribe bridge inside the existing Core containers."""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI
from starlette.responses import Response
from starlette.types import ASGIApp, Receive, Scope, Send

API_FLAG = "SCRIBE_BUNDLE_API_ENABLED"
WORKER_FLAG = "SCRIBE_BUNDLE_CAPTURED_WORKER_ENABLED"
PYTHON = "/opt/scribe/runtime/bin/python"
CHILD_SCRIPT = "/app/provider/core_bundle_child.py"
ORIGIN = "http://127.0.0.1:8021"
API_CONFIGURATION = "SCRIBE_BUNDLE_API_CONFIGURATION_JSON"
PROVIDER_CONFIGURATION = "COSMANEURA_SCRIBE_PROVIDER_CONFIGURATION_JSON"
PROVIDER_CONTRACT = "COSMANEURA_SCRIBE_PROVIDER_IMAGE_CONTRACT_SHA256"
WORKER_CONFIGURATIONS = {
    "speech": "SCRIBE_BUNDLE_SPEECH_CONFIGURATION_JSON",
    "translation": "SCRIBE_BUNDLE_TRANSLATION_CONFIGURATION_JSON",
}
_AZURE_ENV = {
    "IDENTITY_ENDPOINT",
    "IDENTITY_HEADER",
    "IDENTITY_SERVER_THUMBPRINT",
    "IMDS_ENDPOINT",
}
_HOP_HEADERS = {
    b"connection",
    b"keep-alive",
    b"proxy-authenticate",
    b"proxy-authorization",
    b"te",
    b"trailer",
    b"transfer-encoding",
    b"upgrade",
}
_SCRIBE_PREFIXES = (
    "/provider/v1/",
    "/v1/sessions",
    "/v1/captured-sessions",
    "/v1/consents",
    "/v1/external-transcript-sessions",
    "/v1/audio-encryption-key",
    "/v1/diagnostics/token",
)


class BundleUnavailable(RuntimeError):
    """Content-free bridge failure; provider/configuration errors never escape."""


def enabled(flag: str, environment: Mapping[str, str] | None = None) -> bool:
    value = (os.environ if environment is None else environment).get(flag, "false")
    if value not in {"false", "true"}:
        raise BundleUnavailable("scribe_core_bundle_flag_invalid")
    return value == "true"


def child_environment(
    role: str, environment: Mapping[str, str] | None = None
) -> dict[str, str]:
    source = os.environ if environment is None else environment
    output = {key: source[key] for key in _AZURE_ENV if key in source}
    output.update(
        PATH="/opt/scribe/runtime/bin:/usr/bin:/bin",
        PYTHONPATH="/app/provider/identity-source:/app/provider",
        PYTHONUNBUFFERED="1",
        PYTHONDONTWRITEBYTECODE="1",
        AZURE_TOKEN_CREDENTIALS="ManagedIdentityCredential",
    )
    if role == "api":
        for key in (API_CONFIGURATION, PROVIDER_CONFIGURATION, PROVIDER_CONTRACT):
            value = source.get(key, "")
            if not value or len(value.encode()) > 32_768:
                raise BundleUnavailable("scribe_core_bundle_configuration_unavailable")
            output[key] = value
    elif role in WORKER_CONFIGURATIONS:
        value = source.get(WORKER_CONFIGURATIONS[role], "")
        if not value or len(value.encode()) > 32_768:
            raise BundleUnavailable("scribe_core_bundle_configuration_unavailable")
        output["SCRIBE_BUNDLE_WORKER_CONFIGURATION_JSON"] = value
    else:
        raise BundleUnavailable("scribe_core_bundle_target_unavailable")
    return output


def scribe_path(path: str) -> bool:
    return any(
        path == prefix
        or path.startswith(prefix if prefix.endswith("/") else prefix + "/")
        for prefix in _SCRIBE_PREFIXES
    )


async def stop_child(process: asyncio.subprocess.Process) -> None:
    if process.returncode is None:
        process.send_signal(signal.SIGTERM)
        try:
            await asyncio.wait_for(process.wait(), 20)
        except TimeoutError:
            process.kill()
            await process.wait()


class CoreBundleGateway:
    def __init__(self, core: FastAPI):
        self.core = core
        self.client: httpx.AsyncClient | None = None
        self.process: asyncio.subprocess.Process | None = None

        @asynccontextmanager
        async def lifespan(_: FastAPI):
            async with core.router.lifespan_context(core):
                process = await asyncio.create_subprocess_exec(
                    PYTHON,
                    CHILD_SCRIPT,
                    "api",
                    env=child_environment("api"),
                    cwd="/opt/scribe",
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    user=10001,
                    group=10001,
                )
                self.process = process
                try:
                    async with httpx.AsyncClient(
                        base_url=ORIGIN,
                        timeout=60,
                        trust_env=False,
                        follow_redirects=False,
                        limits=httpx.Limits(
                            max_connections=4, max_keepalive_connections=2
                        ),
                    ) as client:
                        for _attempt in range(100):
                            if process.returncode is not None:
                                raise BundleUnavailable(
                                    "scribe_core_bundle_startup_unavailable"
                                )
                            try:
                                response = await client.get(
                                    "/__core_bundle_health", timeout=0.2
                                )
                                if response.status_code == 204:
                                    break
                            except httpx.HTTPError:
                                pass
                            await asyncio.sleep(0.1)
                        else:
                            raise BundleUnavailable(
                                "scribe_core_bundle_startup_unavailable"
                            )
                        self.client = client
                        yield
                finally:
                    self.client = None
                    self.process = None
                    await stop_child(process)

        self.lifecycle = FastAPI(
            lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            await self.lifecycle(scope, receive, send)
        elif not scribe_path(scope.get("path", "")):
            await self.core(scope, receive, send)
        elif scope["type"] != "http":
            await send({"type": "websocket.close", "code": 1008})
        elif (
            self.client is None
            or self.process is None
            or self.process.returncode is not None
        ):
            await Response(status_code=503, headers={"Cache-Control": "no-store"})(
                scope, receive, send
            )
        else:
            await self.forward(scope, receive, send)

    async def forward(self, scope: Scope, receive: Receive, send: Send) -> None:
        client = self.client
        if client is None:
            raise BundleUnavailable("scribe_core_bundle_unavailable")
        headers = list(scope.get("headers", []))
        # Preserve human/workload/origin/ACA provenance headers byte for byte.
        # HTTP hop headers and their declared connection options never cross.
        excluded = set(_HOP_HEADERS)
        for key, value in headers:
            if key.lower() == b"connection":
                excluded.update(token.strip().lower() for token in value.split(b","))
        if excluded & {
            b"authorization",
            b"origin",
            b"host",
            b"x-cosmaneura-workload-authorization",
            b"x-forwarded-host",
            b"x-forwarded-proto",
            b"x-forwarded-for",
        }:
            await Response(status_code=400, headers={"Cache-Control": "no-store"})(
                scope, receive, send
            )
            return
        headers = [
            (key, value) for key, value in headers if key.lower() not in excluded
        ]

        async def body() -> AsyncIterator[bytes]:
            while True:
                message = await receive()
                if message["type"] != "http.request":
                    raise BundleUnavailable("scribe_core_bundle_request_unavailable")
                yield message.get("body", b"")
                if not message.get("more_body", False):
                    break

        started = False
        try:
            # Absolute origin is fixed. Raw path/query supply no scheme, host or
            # port; never use URL joining with a caller-selected relative URL.
            raw_path = scope.get("raw_path", scope["path"].encode())
            query = scope.get("query_string", b"")
            url = httpx.URL(ORIGIN).copy_with(
                raw_path=raw_path + (b"?" + query if query else b"")
            )
            async with client.stream(
                scope["method"], url, headers=headers, content=body()
            ) as response:
                await send(
                    {
                        "type": "http.response.start",
                        "status": response.status_code,
                        "headers": [
                            (key, value)
                            for key, value in response.headers.raw
                            if key.lower() not in _HOP_HEADERS
                        ],
                    }
                )
                started = True
                async for chunk in response.aiter_raw():
                    await send(
                        {"type": "http.response.body", "body": chunk, "more_body": True}
                    )
                await send(
                    {"type": "http.response.body", "body": b"", "more_body": False}
                )
        except (httpx.HTTPError, BundleUnavailable):
            if started:
                raise BundleUnavailable(
                    "scribe_core_bundle_response_unavailable"
                ) from None
            await Response(status_code=503, headers={"Cache-Control": "no-store"})(
                scope, receive, send
            )


def create_app() -> ASGIApp:
    from app.server import api_app

    return CoreBundleGateway(api_app) if enabled(API_FLAG) else api_app


def perform_captured_pickup() -> str:
    if not enabled(WORKER_FLAG):
        return "disabled"
    try:
        # One metadata-only, fixed-name lock in the existing Core Redis service.
        # Durable Scribe SQL claims still fence every selected recording.
        from app.configuration.redis import get_redis

        with get_redis().lock(
            "scribe-core-captured-pickup-v1", timeout=390, blocking=False
        ):
            for stage in ("speech", "translation"):
                process = subprocess.Popen(
                    [PYTHON, CHILD_SCRIPT, stage],
                    env=child_environment(stage),
                    cwd="/opt/scribe",
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    user=10001,
                    group=10001,
                )
                try:
                    if process.wait(timeout=180) != 0:
                        raise BundleUnavailable("scribe_core_bundle_worker_unavailable")
                finally:
                    if process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait()
        return "completed"
    except Exception:  # noqa: BLE001 - provider/config errors contain protected values
        return "unavailable"


def register_schedule(schedule: dict[str, Any]) -> None:
    if enabled(WORKER_FLAG):
        schedule["scribe_core_captured_pickup"] = {
            "task": "app.jobs.scribe_captured.perform",
            "schedule": 60.0,
            "options": {"expires": 60},
        }
