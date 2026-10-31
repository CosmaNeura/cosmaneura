"""Offline existing-container bridge checks; no Core setup, Redis or SDK call."""

import importlib.util
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from types import ModuleType, SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

spec = importlib.util.spec_from_file_location(
    "tested_scribe_bundle", Path(__file__).parents[2] / "app/scribe_bundle.py"
)
assert spec and spec.loader
bundle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bundle)


def environment():
    return {
        bundle.API_CONFIGURATION: "{}",
        bundle.PROVIDER_CONFIGURATION: "{}",
        bundle.PROVIDER_CONTRACT: "a" * 64,
        "DATABASE_URL": "must-never-cross",
        "CLERK_SECRET_KEY": "must-never-cross",
        "AZURE_CLIENT_SECRET": "must-never-cross",
        "IDENTITY_ENDPOINT": "container-managed-identity",
        "IDENTITY_HEADER": "container-header",
        bundle.WORKER_CONFIGURATIONS["speech"]: "speech-config",
        bundle.WORKER_CONFIGURATIONS["translation"]: "translation-config",
    }


def test_disabled_flags_and_schedule_are_unchanged():
    assert not bundle.enabled(bundle.API_FLAG, {})
    assert not bundle.enabled(bundle.WORKER_FLAG, {})
    schedule = {"existing": {"task": "unchanged", "schedule": 17}}
    before = repr(schedule)
    bundle.register_schedule(schedule)
    assert repr(schedule) == before


@pytest.mark.parametrize("value", ["", "1", "yes", "TRUE"])
def test_malformed_flags_fail(value):
    with pytest.raises(bundle.BundleUnavailable):
        bundle.enabled(bundle.API_FLAG, {bundle.API_FLAG: value})


@pytest.mark.parametrize("role", ["api", "speech", "translation"])
def test_child_environment_separates_core_secrets_and_stage_configuration(role):
    values = bundle.child_environment(role, environment())
    assert (
        not {"DATABASE_URL", "CLERK_SECRET_KEY", "AZURE_CLIENT_SECRET"} & values.keys()
    )
    assert values["AZURE_TOKEN_CREDENTIALS"] == "ManagedIdentityCredential"
    assert values["IDENTITY_HEADER"] == "container-header"
    if role != "api":
        assert values["SCRIBE_BUNDLE_WORKER_CONFIGURATION_JSON"] == role + "-config"
        assert bundle.API_CONFIGURATION not in values


def test_no_sibling_or_foreign_paths_are_claimed():
    assert bundle.scribe_path("/provider/v1/catalog")
    assert bundle.scribe_path("/v1/sessions/one/transcript")
    assert not bundle.scribe_path("/v1/sessions-other")
    assert not bundle.scribe_path("/external/v1/intake")
    assert not bundle.scribe_path("/service/v1/agent/scribe/provider-context")
    assert not bundle.scribe_path("/__core_bundle_health")


@pytest.mark.asyncio
async def test_original_core_middleware_and_routes_receive_non_scribe_requests():
    core = FastAPI()

    @core.middleware("http")
    async def marker(request, call_next):
        response = await call_next(request)
        response.headers["X-Core-Middleware"] = "original"
        return response

    @core.get("/healthcheck")
    def health():
        return {"core": True}

    gateway = bundle.CoreBundleGateway(core)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=gateway), base_url="https://core.example"
    ) as client:
        response = await client.get("/healthcheck")
    assert (
        response.status_code == 200
        and response.headers["x-core-middleware"] == "original"
    )


@pytest.mark.asyncio
async def test_tokens_origin_path_stream_and_core_denial_remain_separate():
    calls = []
    core_calls = []
    core = FastAPI()

    @core.post("/provider/v1/catalog")
    def fallback():
        core_calls.append(True)

    async def reply(request):
        calls.append((str(request.url), request.headers, await request.aread()))

        class Stream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b"denied"

        return httpx.Response(
            403, stream=Stream(), headers={"Cache-Control": "no-store"}
        )

    gateway = bundle.CoreBundleGateway(core)
    gateway.process = SimpleNamespace(returncode=None)
    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as child:
        gateway.client = child
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=gateway), base_url="https://core.example"
        ) as client:
            response = await client.post(
                "/provider/v1/catalog?nonce=one",
                content=b"closed-body",
                headers={
                    "Authorization": "Bearer human",
                    "X-Cosmaneura-Workload-Authorization": "Bearer workload",
                    "Origin": "cosmaneura-scribe://native",
                    "X-Forwarded-Proto": "https",
                    "X-Forwarded-Host": "core.example",
                },
            )
    assert (
        response.status_code == 403 and response.content == b"denied" and not core_calls
    )
    url, headers, body = calls[0]
    assert (
        url == bundle.ORIGIN + "/provider/v1/catalog?nonce=one"
        and body == b"closed-body"
    )
    assert (
        headers["authorization"] == "Bearer human"
        and headers["x-cosmaneura-workload-authorization"] == "Bearer workload"
    )
    assert headers["host"] == "core.example" and headers["x-forwarded-proto"] == "https"


@pytest.mark.asyncio
async def test_connection_cannot_remove_authority_headers():
    gateway = bundle.CoreBundleGateway(FastAPI())
    gateway.process = SimpleNamespace(returncode=None)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: pytest.fail("no child call"))
    ) as child:
        gateway.client = child
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=gateway), base_url="https://core.example"
        ) as client:
            response = await client.post(
                "/provider/v1/catalog", headers={"Connection": "Authorization"}
            )
    assert response.status_code == 400


def test_lifespan_preserves_core_and_stops_child(monkeypatch):
    events = []

    @asynccontextmanager
    async def lifespan(_):
        events.append("core-start")
        yield
        events.append("core-stop")

    core = FastAPI(lifespan=lifespan)

    class Process:
        returncode = None

        def send_signal(self, _):
            events.append("child-term")
            self.returncode = 0

        async def wait(self):
            return self.returncode

    async def spawn(*arguments, **kwargs):
        assert arguments == (bundle.PYTHON, bundle.CHILD_SCRIPT, "api")
        assert kwargs["user"] == 10001 and kwargs["group"] == 10001
        events.append("child-start")
        return Process()

    client_type = httpx.AsyncClient
    monkeypatch.setattr(bundle.asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(
        bundle.httpx,
        "AsyncClient",
        lambda **kwargs: client_type(
            transport=httpx.MockTransport(lambda _: httpx.Response(204)), **kwargs
        ),
    )
    monkeypatch.setattr(bundle, "child_environment", lambda _: {})
    with TestClient(bundle.CoreBundleGateway(core)):
        pass
    assert events == ["core-start", "child-start", "child-term", "core-stop"]


def test_disabled_worker_does_not_connect_or_spawn(monkeypatch):
    monkeypatch.delenv(bundle.WORKER_FLAG, raising=False)
    monkeypatch.setattr(
        bundle.subprocess, "Popen", lambda *_args, **_kwargs: pytest.fail("no spawn")
    )
    assert bundle.perform_captured_pickup() == "disabled"


def test_enabled_worker_uses_existing_lock_and_exact_two_bounded_children(monkeypatch):
    events = []

    class Lock:
        def __enter__(self):
            events.append("locked")

        def __exit__(self, *_):
            events.append("released")

    class Redis:
        def lock(self, name, **kwargs):
            assert name == "scribe-core-captured-pickup-v1" and kwargs == {
                "timeout": 390,
                "blocking": False,
            }
            return Lock()

    module = ModuleType("app.configuration.redis")
    module.__dict__["get_redis"] = lambda: Redis()
    monkeypatch.setitem(sys.modules, "app.configuration.redis", module)

    class Process:
        def wait(self, **kwargs):
            assert kwargs == {"timeout": 180}
            return 0

        def poll(self):
            return 0

    def spawn(arguments, **kwargs):
        assert arguments[:2] == [bundle.PYTHON, bundle.CHILD_SCRIPT]
        assert kwargs["user"] == 10001 and kwargs["group"] == 10001
        events.append(arguments[-1])
        return Process()

    monkeypatch.setenv(bundle.WORKER_FLAG, "true")
    monkeypatch.setattr(bundle, "child_environment", lambda _: {})
    monkeypatch.setattr(bundle.subprocess, "Popen", spawn)
    assert bundle.perform_captured_pickup() == "completed"
    assert events == ["locked", "speech", "translation", "released"]


def test_enabled_schedule_has_no_actor_tenant_or_recording_arguments(monkeypatch):
    monkeypatch.setenv(bundle.WORKER_FLAG, "true")
    schedule = {"original": {"task": "existing"}}
    bundle.register_schedule(schedule)
    assert schedule["original"] == {"task": "existing"}
    new = schedule["scribe_core_captured_pickup"]
    assert new == {
        "task": "app.jobs.scribe_captured.perform",
        "schedule": 60.0,
        "options": {"expires": 60},
    }
