"""Actual existing app-opening route; synthetic auth/SQL and no external I/O."""

import asyncio
import json
import time
from contextvars import ContextVar
from uuid import uuid4

import httpx
import pytest
from azure.core.credentials import AccessToken
from fastapi import FastAPI, HTTPException

from app.configuration import scribe_service_identity as identity
from app.configuration.scribe_encounters import ScribeEncounterSettings
from app.routes import scribe_encounters as route
from app.routes.embed_context import (
    VerifiedClerkIdentity,
    authenticate_embed_platform_session,
)
from app.services.provider_record_authority import ProviderRecordScopeDenied

from tests.authority_kernel.scribe_encounter_receiver_test import ACTOR, TENANT
from tests.authority_kernel.scribe_service_identity_test import (
    SETTINGS,
    Credential,
    until,
)


def request_body(action):
    body = dict(
        action=action,
        expected_actor_ref=ACTOR,
        expected_tenant_ref=TENANT,
        request_nonce=str(uuid4()),
    )
    if action == "patients":
        body["query"] = "Invented synthetic patient"
    elif action == "lookup":
        body["workspace_id"] = str(uuid4())
    return body


def application(monkeypatch, *, failure=None):
    app = FastAPI()
    route.install(app)
    private = ContextVar[str | None]("synthetic_list_patient_and_bearer", default=None)
    order = []

    def signed_in():
        if failure == "signed_out":
            raise HTTPException(401, headers={"Cache-Control": "no-store"})
        return VerifiedClerkIdentity(
            issuer="https://test.clerk.invalid",
            subject="user_synthetic",
            clerk_session_id="session_synthetic",
        )

    app.dependency_overrides[authenticate_embed_platform_session] = signed_in
    app.dependency_overrides[route.encounter_settings] = (
        lambda: ScribeEncounterSettings(enabled=failure != "disabled")
    )
    monkeypatch.setattr(
        route, "view_as_browser_session_present", lambda _: failure == "view_as"
    )

    def scoped_workflow(**kwargs):
        # This is the existing SQL/provider seam, not a warmup or new RPC.
        assert private.get() is None
        assert (
            kwargs["subject"] == "user_synthetic"
            and kwargs["session_id"] == "session_synthetic"
        )
        assert kwargs["request"].expected_actor_ref == ACTOR
        assert kwargs["request"].expected_tenant_ref == TENANT
        order.append("current_provider_scope")
        if failure == "provider_denied":
            raise ProviderRecordScopeDenied
        result = {"version": "1", "action": kwargs["request"].action, "fixture": True}
        if failure == "oversize_response":
            result["oversize"] = "x" * 1_048_576
        if failure == "serialize_expired":
            result["expires"] = kwargs["deadline"]
        return result

    monkeypatch.setattr(route.scribe_encounters, "encounter_workflow", scoped_workflow)
    original_dumps = json.dumps

    def dumps(value, **kwargs):
        if isinstance(value, dict) and "expires" in value:
            value = dict(value)
            value.pop("expires").cancel()
        return original_dumps(value, **kwargs)

    monkeypatch.setattr(route.json, "dumps", dumps)
    original_warm = route.request_scribe_service_identity_warm

    def trigger(application):
        assert order == ["current_provider_scope"]
        order.append("warm_requested")
        return original_warm(application)

    monkeypatch.setattr(route, "request_scribe_service_identity_warm", trigger)

    async def token():
        assert private.get() is None
        order.append("sdk_service_scope")
        # Credential checks fixed scope/client-loop, with no kwargs.
        return AccessToken("synthetic-service-only", int(time.time()) + 60)

    sdk = Credential(token)
    owner = identity.ScribeServiceIdentityOwner(
        SETTINGS, credential_factory=lambda _: sdk
    )
    app.state.scribe_service_identity = owner
    return app, owner, sdk, order, private


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action,warm", [("list", True), ("patients", True), ("lookup", False)]
)
async def test_successful_current_list_or_patients_only_triggers_service_warm(
    monkeypatch, action, warm
):
    app, owner, sdk, order, private = application(monkeypatch)
    private.set("synthetic-catalog-and-user-bearer")
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            response = await client.post(
                route.PATH,
                json=request_body(action),
                headers={
                    "Authorization": "Bearer synthetic",
                    "X-Cosmaneura-Scribe-Encounter-Version": "1",
                },
            )
        assert response.status_code == 200
        assert response.json() == {"version": "1", "action": action, "fixture": True}
        assert response.headers["cache-control"] == "no-store"
        await until(lambda: owner._warm is None)
        assert sdk.calls == int(warm)
        assert order == (
            ["current_provider_scope", "warm_requested", "sdk_service_scope"]
            if warm
            else ["current_provider_scope"]
        )
        assert (
            "synthetic-catalog" not in response.text
            and "synthetic-service" not in response.text
        )
    finally:
        await owner.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure,status",
    [
        ("signed_out", 401),
        ("disabled", 503),
        ("view_as", 403),
        ("version", 426),
        ("invalid_body", 422),
        ("provider_denied", 403),
        ("oversize_response", 503),
        ("serialize_expired", 504),
    ],
)
async def test_refused_or_expired_list_does_not_warm_or_change_existing_error(
    monkeypatch, failure, status
):
    app, owner, sdk, order, _ = application(monkeypatch, failure=failure)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            response = await client.post(
                route.PATH,
                json=request_body("list")
                | ({"unknown": True} if failure == "invalid_body" else {}),
                headers={
                    "Authorization": "Bearer synthetic",
                    "X-Cosmaneura-Scribe-Encounter-Version": "2"
                    if failure == "version"
                    else "1",
                },
            )
        assert (
            response.status_code == status
            and response.headers["cache-control"] == "no-store"
        )
        await asyncio.sleep(0)
        assert sdk.calls == 0 and owner._warm is None
        assert "warm_requested" not in order
    finally:
        await owner.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("closed", [False, True])
async def test_successful_list_without_usable_owner_has_no_factory_or_token(
    monkeypatch, closed
):
    app, owner, sdk, order, _ = application(monkeypatch)
    if closed:
        await owner.close()
    else:
        del app.state.scribe_service_identity
    monkeypatch.setattr(
        identity,
        "create_scribe_workload_credential",
        lambda _: pytest.fail("legacy factory"),
    )
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            response = await client.post(
                route.PATH,
                json=request_body("list"),
                headers={
                    "Authorization": "Bearer synthetic",
                    "X-Cosmaneura-Scribe-Encounter-Version": "1",
                },
            )
        assert response.status_code == 200 and sdk.calls == 0
        assert order == ["current_provider_scope", "warm_requested"]
    finally:
        await owner.close()


@pytest.mark.asyncio
async def test_warm_sdk_failure_does_not_delay_or_change_current_list_response(
    monkeypatch,
):
    app, owner, sdk, order, _ = application(monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()

    async def fails_later():
        entered.set()
        await release.wait()
        raise ValueError("synthetic-private-sdk-diagnostic")

    sdk.handler = fails_later
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            response = await client.post(
                route.PATH,
                json=request_body("list"),
                headers={
                    "Authorization": "Bearer synthetic",
                    "X-Cosmaneura-Scribe-Encounter-Version": "1",
                },
            )
        await entered.wait()
        assert response.status_code == 200 and response.json() == {
            "version": "1",
            "action": "list",
            "fixture": True,
        }
        assert (
            response.headers["cache-control"] == "no-store" and owner._warm is not None
        )
        release.set()
        await until(lambda: owner._warm is None)
        assert sdk.calls == 1 and order == ["current_provider_scope", "warm_requested"]
        assert "diagnostic" not in response.text and "token" not in response.text
    finally:
        release.set()
        await owner.close()
