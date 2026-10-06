"""Actual in-process ASGI and offline signed workload auth; no service/DB I/O."""

import asyncio
import json
import time
from contextvars import ContextVar
from dataclasses import dataclass
from threading import Event
from typing import Annotated

import jwt
import pytest
from companion_record_contract_test import sample
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Depends, FastAPI
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.testclient import TestClient

from app.configuration.companion_records import CompanionRecordSettings
from app.routes import companion_records as route
from app.routes.dependencies.entra_service import EntraServiceAuthenticator
from app.services import authority
from app.services.companion_record_contract import RecordResponse

PATH = route.DEFAULT_PREFIX + route.PATH
CLIENT = "00000000-0000-4000-8000-000000000004"


@pytest.fixture
def transport(monkeypatch):
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    @dataclass
    class Policy:
        enabled = True
        tenant_id = "invented-tenant"
        audience = "api://invented-core"
        issuer = "https://offline.invalid/issuer"
        jwks_url = "https://must-not-be-used.invalid/keys"
        allowed_client_ids = frozenset({CLIENT})
        required_role = "agent.read"
        log_namespace = "invented_record_test"
        require_nbf = True

        def is_complete(self):
            return True

    policy = Policy()

    def decoder(token):
        return jwt.decode(
            token,
            private.public_key(),
            algorithms=["RS256"],
            audience=policy.audience,
            issuer=policy.issuer,
            options={"require": ["aud", "exp", "iat", "nbf", "iss", "sub", "tid"]},
        )

    authenticate = EntraServiceAuthenticator(policy, decoder=decoder)

    def authenticated(
        credentials: Annotated[
            HTTPAuthorizationCredentials | None, Depends(HTTPBearer(auto_error=False))
        ],
    ):
        return authenticate(credentials)

    app = FastAPI()
    route.install(app)
    app.dependency_overrides[route.authenticate_agent_api_request] = authenticated
    app.dependency_overrides[route.record_settings] = lambda: CompanionRecordSettings(
        True
    )
    calls = []

    def read(**kwargs):
        calls.append(kwargs)
        return RecordResponse.model_validate(sample())

    monkeypatch.setattr(route.companion_records, "read_record", read)
    now = int(time.time())
    claims = {
        "aud": policy.audience,
        "iss": policy.issuer,
        "tid": policy.tenant_id,
        "sub": CLIENT,
        "azp": CLIENT,
        "idtyp": "app",
        "roles": [policy.required_role],
        "iat": now,
        "nbf": now - 1,
        "exp": now + 300,
    }

    def token(**changes):
        return jwt.encode({**claims, **changes}, private, algorithm="RS256")

    headers = {
        "Authorization": "Bearer " + token(),
        "Content-Type": "application/json",
        "X-Cosmaneura-Record-Version": "1",
        "X-Cosmaneura-Mount-Context": "invented-context-token",
        "X-Cosmaneura-Interaction-Ref": "invented-interaction",
        "X-Cosmaneura-Client-Turn-Id": "00000000-0000-4000-8000-000000000003",
    }
    request = {k: sample()[k] for k in ("binding_ref", "workspace_id", "request_nonce")}
    with TestClient(app) as client:
        yield app, client, calls, headers, request, token


def assert_private(response, status):
    assert response.status_code == status
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-cosmaneura-record-version"] == "1"


def test_signed_workload_reads_exact_record_without_openapi_surface(transport):
    app, client, calls, headers, request, _ = transport
    response = client.post(PATH, headers=headers, json=request)
    assert_private(response, 200)
    assert RecordResponse.model_validate(response.json()).request_nonce.hex.endswith(
        "2"
    )
    assert len(calls) == 1 and calls[0]["principal_client_id"] == CLIENT
    assert calls[0]["request"].model_dump(mode="json") == request
    assert PATH not in app.openapi()["paths"]


@pytest.mark.parametrize(
    "claims,status",
    [
        ({"exp": 1}, 401),
        ({"tid": "wrong"}, 401),
        ({"azp": "00000000-0000-4000-8000-000000000005"}, 403),
        ({"roles": []}, 403),
        ({"idtyp": "user"}, 403),
    ],
)
def test_offline_signature_auth_scope_rejections_never_read(transport, claims, status):
    _, client, calls, headers, request, token = transport
    headers["Authorization"] = "Bearer " + token(**claims)
    response = client.post(PATH, headers=headers, json=request)
    assert_private(response, status)
    assert calls == [] and "Invented Person" not in response.text


def test_missing_auth_default_off_and_missing_boundary_fail_closed(transport):
    app, client, calls, headers, request, _ = transport
    unauth = dict(headers)
    unauth.pop("Authorization")
    assert_private(client.post(PATH, headers=unauth, json=request), 401)
    app.dependency_overrides[route.record_settings] = lambda: CompanionRecordSettings()
    assert_private(client.post(PATH, headers=headers, json=request), 503)
    assert calls == []


@pytest.mark.parametrize(
    "kind,status",
    [
        ("missing-version", 426),
        ("wrong-version", 426),
        ("duplicate-version", 426),
        ("query", 422),
        ("encoding", 422),
        ("duplicate-auth", 422),
        ("duplicate-length", 422),
        ("wrong-content", 422),
        ("oversize", 422),
        ("duplicate-json", 422),
        ("extra-scope", 422),
        ("bad-uuid", 422),
        ("bad-context", 422),
        ("utf16", 422),
        ("wrong-length", 422),
    ],
)
def test_strict_body_and_header_bounds_never_call_source(transport, kind, status):
    _, client, calls, headers, request, _ = transport
    path = PATH
    outgoing_headers: dict[str, str] | list[tuple[str, str]] = headers
    kwargs = {"json": request}
    if kind == "missing-version":
        headers.pop("X-Cosmaneura-Record-Version")
    if kind == "wrong-version":
        headers["X-Cosmaneura-Record-Version"] = "2"
    if kind == "duplicate-version":
        outgoing_headers = [*headers.items(), ("X-Cosmaneura-Record-Version", "1")]
    if kind == "duplicate-auth":
        outgoing_headers = [
            *headers.items(),
            ("Authorization", headers["Authorization"]),
        ]
    if kind == "duplicate-length":
        outgoing_headers = [
            *headers.items(),
            ("Content-Length", "2"),
            ("Content-Length", "2"),
        ]
    if kind == "query":
        path += "?patient_ref=forbidden"
    if kind == "encoding":
        headers["Content-Encoding"] = "identity"
    if kind == "wrong-content":
        headers["Content-Type"] = "text/plain"
    if kind == "oversize":
        kwargs = {"content": b"x" * 2049}
    if kind == "duplicate-json":
        kwargs = {"content": b'{"workspace_id":"one","workspace_id":"two"}'}
    if kind == "extra-scope":
        request["doctor_ref"] = "override"
    if kind == "bad-uuid":
        request["workspace_id"] = "not-a-uuid"
    if kind == "bad-context":
        headers["X-Cosmaneura-Mount-Context"] = "whitespace is invalid"
    if kind == "utf16":
        kwargs = {"content": json.dumps(request).encode("utf-16")}
    if kind == "wrong-length":
        headers["Content-Length"] = "0"
    response = client.post(path, headers=outgoing_headers, **kwargs)
    assert_private(response, status)
    assert calls == []


@pytest.mark.parametrize(
    "exception,status",
    [
        (authority.ContextTokenExpiredError, 403),
        (route.companion_records.RecordUnavailable, 503),
        (RuntimeError, 503),
    ],
)
def test_private_source_errors_and_stale_human_session_do_not_disclose(
    transport, monkeypatch, exception, status
):
    _, client, _, headers, request, _ = transport

    def failing(**_kwargs):
        raise exception("private stored text must not escape")

    monkeypatch.setattr(route.companion_records, "read_record", failing)
    response = client.post(PATH, headers=headers, json=request)
    assert_private(response, status)
    assert "private stored text" not in response.text


def test_wrong_response_scope_is_rejected(transport, monkeypatch):
    _, client, _, headers, request, _ = transport
    value = sample()
    value["workspace_id"] = "00000000-0000-4000-8000-000000000005"
    monkeypatch.setattr(
        route.companion_records,
        "read_record",
        lambda **_: RecordResponse.model_validate(value),
    )
    assert_private(client.post(PATH, headers=headers, json=request), 503)


def test_deadline_includes_auth_dependency_and_dispatch_never_starts(
    transport, monkeypatch
):
    app, client, calls, headers, request, _ = transport
    monkeypatch.setattr(route, "DEADLINE_SECONDS", 0.04)

    async def delayed():
        await asyncio.sleep(0.2)

    app.dependency_overrides[route.authenticate_agent_api_request] = delayed
    assert_private(client.post(PATH, headers=headers, json=request), 504)
    assert calls == []


def test_late_worker_cannot_disclose_or_borrow_request_context(transport, monkeypatch):
    _, client, _, headers, request, _ = transport
    marker = ContextVar[str | None]("invented_request_session", default=None)
    marker.set("must not be borrowed")
    started, release, finished = Event(), Event(), Event()
    observed = []

    def delayed(**kwargs):
        observed.append(marker.get())
        started.set()
        release.wait(0.5)
        try:
            route.companion_records.check_deadline(kwargs["deadline"])
        finally:
            finished.set()
        return RecordResponse.model_validate(sample())

    monkeypatch.setattr(route.companion_records, "read_record", delayed)
    monkeypatch.setattr(route, "DEADLINE_SECONDS", 0.04)
    response = client.post(PATH, headers=headers, json=request)
    release.set()
    assert_private(response, 504)
    assert started.is_set() and finished.wait(1)
    assert observed == [None]


def test_delayed_header_send_cannot_begin_private_body_after_deadline(monkeypatch):
    monkeypatch.setattr(route, "DEADLINE_SECONDS", 0.02)
    sent = []

    async def downstream(_scope, _receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"private source body"})

    async def delayed_send(message):
        sent.append(message)
        if message["type"] == "http.response.start":
            await asyncio.sleep(0.06)

    async def receive():
        return {"type": "http.disconnect"}

    asyncio.run(
        route.RecordBoundary(downstream, path=PATH)(
            {"type": "http", "path": PATH}, receive, delayed_send
        )
    )
    assert not any(message.get("body") == b"private source body" for message in sent)


@pytest.mark.parametrize(
    "failure", ["deadline", "unavailable", "unexpected", "timeout"]
)
@pytest.mark.parametrize("resists_cancel", [False, True])
def test_error_output_is_bounded_and_never_resumes_private_body(
    monkeypatch, failure, resists_cancel
):
    monkeypatch.setattr(route, "DEADLINE_SECONDS", 0.02)
    monkeypatch.setattr(route, "ERROR_SEND_SECONDS", 0.01, raising=False)

    async def run():
        release = asyncio.Event()
        entered = asyncio.Event()
        finished = asyncio.Event()
        sent = []

        async def downstream(_scope, _receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"private source body"})
            if failure == "timeout":
                await asyncio.Event().wait()
            elif failure == "deadline":
                raise route.companion_records.RecordDeadline
            elif failure == "unavailable":
                raise route.companion_records.RecordUnavailable
            else:
                raise RuntimeError("private exception must not escape")

        async def blocking_send(message):
            sent.append(message)
            if message["type"] == "http.response.start":
                entered.set()
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    if not resists_cancel:
                        raise
                    await release.wait()
                finally:
                    finished.set()

        async def receive():
            return {"type": "http.disconnect"}

        task = asyncio.create_task(
            route.RecordBoundary(downstream, path=PATH)(
                {"type": "http", "path": PATH}, receive, blocking_send
            )
        )
        try:
            await asyncio.wait_for(entered.wait(), 0.2)
            done, _ = await asyncio.wait({task}, timeout=0.08)
            assert task in done, "static error output exceeded its finite grace"
            assert task.exception() is None
            assert sent[0]["status"] == (
                504 if failure in {"deadline", "timeout"} else 503
            )
            assert not any(message.get("body") for message in sent)
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
            await asyncio.wait_for(finished.wait(), 0.2)
            await asyncio.sleep(0)
        assert len(sent) == 1

    asyncio.run(run())


def test_cancelled_request_revokes_worker_budget_before_next_phase(
    transport, monkeypatch
):
    app, _, _, headers, request_body, _ = transport
    started, release, finished = Event(), Event(), Event()
    phases = []

    def delayed(**kwargs):
        started.set()
        try:
            assert release.wait(1)
            route.companion_records.check_deadline(kwargs["deadline"])
            phases.append("forbidden-next-source-phase")
            return RecordResponse.model_validate(sample())
        except route.companion_records.RecordDeadline:
            phases.append("cancelled-before-next-phase")
            raise
        finally:
            finished.set()

    monkeypatch.setattr(route.companion_records, "read_record", delayed)
    monkeypatch.setattr(route, "DEADLINE_SECONDS", 5)

    async def run():
        body_sent = False
        sent = []

        async def receive():
            nonlocal body_sent
            if not body_sent:
                body_sent = True
                return {
                    "type": "http.request",
                    "body": json.dumps(request_body).encode(),
                    "more_body": False,
                }
            await asyncio.Event().wait()

        async def send(message):
            sent.append(message)

        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": PATH,
            "raw_path": PATH.encode(),
            "query_string": b"",
            "headers": [
                (key.lower().encode(), value.encode()) for key, value in headers.items()
            ],
            "client": ("offline", 1),
            "server": ("offline", 80),
        }
        task = asyncio.create_task(app(scope, receive, send))
        try:
            assert await asyncio.to_thread(started.wait, 1)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            release.set()
            assert await asyncio.to_thread(finished.wait, 1)
            assert sent == []
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())
    assert phases == ["cancelled-before-next-phase"]


def test_cancellation_during_error_send_prevents_late_static_chunk(monkeypatch):
    monkeypatch.setattr(route, "ERROR_SEND_SECONDS", 2)

    async def run():
        entered, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()
        sent = []

        async def downstream(_scope, _receive, _send):
            raise route.companion_records.RecordUnavailable

        async def send(message):
            sent.append(message)
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()
            finally:
                finished.set()

        async def receive():
            return {"type": "http.disconnect"}

        boundary = route.RecordBoundary(downstream, path=PATH)
        task = asyncio.create_task(
            boundary({"type": "http", "path": PATH}, receive, send)
        )
        try:
            await asyncio.wait_for(entered.wait(), 0.2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
            await asyncio.wait_for(finished.wait(), 0.2)
            await asyncio.sleep(0)
            await asyncio.sleep(0)
        assert len(sent) == 1 and sent[0]["type"] == "http.response.start"
        assert not boundary._settling

    asyncio.run(run())


def test_cancellation_resistant_application_cannot_extend_deadline_or_disclose(
    monkeypatch,
):
    monkeypatch.setattr(route, "DEADLINE_SECONDS", 0.02)

    async def run():
        release, started, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()
        sent = []

        async def downstream(_scope, _receive, send):
            started.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()
            try:
                await send({"type": "http.response.body", "body": b"private late body"})
            finally:
                finished.set()

        async def send(message):
            sent.append(message)

        async def receive():
            return {"type": "http.disconnect"}

        boundary = route.RecordBoundary(downstream, path=PATH)
        task = asyncio.create_task(
            boundary({"type": "http", "path": PATH}, receive, send)
        )
        try:
            await asyncio.wait_for(started.wait(), 0.2)
            done, _ = await asyncio.wait({task}, timeout=0.08)
            assert task in done and task.exception() is None
            assert sent[0]["status"] == 504
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
            await asyncio.wait_for(finished.wait(), 0.2)
            await asyncio.sleep(0)
            await asyncio.sleep(0)
        assert not any(message.get("body") == b"private late body" for message in sent)
        assert not boundary._settling

    asyncio.run(run())
