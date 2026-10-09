"""Final review regressions for native receipts and bounded transport failures."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from azure.core.exceptions import AzureError
from fastapi import FastAPI
from pydantic import BaseModel

from app.configuration.scribe_capture import ScribeCaptureSettings
from app.routes.scribe_capture import get_capture_settings
from app.services import authority, encounter_prep
from app.services import scribe_capture_gateway as gateway
from app.services.scribe_configured_capture_contract import ConfiguredSessionCreate
from app.services.scribe_source_start_contract import (
    NativeSourceStart,
    NativeStartPayload,
    NativeStartReceipt,
)
from app.services.scribe_workflow import (
    HttpScribeWorkflowClient,
    ScribeWorkflowRetryableError,
)

from tests.authority_kernel.scribe_capture_receiver_test import client, headers
from tests.authority_kernel.scribe_gateway_receiver_test import scope
from tests.services.scribe_workflow_test import StaticCredential


def prepared_start():
    at = datetime.now(UTC)
    prior = scope()
    current = replace(
        prior,
        creator_actor_id="synthetic_creator",
        session_request=ConfiguredSessionCreate(
            **prior.session_request.model_dump(),
            schema_version="1.0",
            language_mode="en-US",
            id=prior.session_id,
        ),
    )
    payload = NativeStartPayload(
        attempt_id=uuid4(),
        source="desktop_companion",
        owner_proof="a" * 64,
        lease_id=uuid4(),
        lease_receipt_id=uuid4(),
        lease_generation=1,
        capture_authorized_until=at + timedelta(minutes=5),
    )
    operation = NativeSourceStart(
        kind="source_start", action="prepare", payload=payload
    )
    assert current.creator_actor_id is not None
    receipt = NativeStartReceipt(
        **payload.model_dump(exclude={"owner_proof"}),
        session_id=current.session_id,
        creator_actor_id=current.creator_actor_id,
        consent_record_id=current.session_request.consent_record_id,
        source_start_id=uuid4(),
        state="prepared",
        preparation_expires_at=at + timedelta(minutes=1),
        issued_at=None,
        expires_at=None,
        capability_id=uuid4(),
    )
    return current, operation, receipt, at


def test_current_prepared_start_is_accepted():
    current, operation, receipt, at = prepared_start()
    assert gateway._validate_source_start(operation, receipt, current, at) == receipt


@pytest.mark.parametrize("state", ["issued", "cancelled", "expired", "stopped"])
def test_prepare_cannot_report_a_different_start_state(state):
    current, operation, receipt, at = prepared_start()
    changed = receipt.model_copy(update={"state": state})
    if state in {"issued", "stopped"}:
        changed = changed.model_copy(
            update={
                "issued_at": at - timedelta(seconds=1),
                "expires_at": at + timedelta(seconds=10),
            }
        )
    with pytest.raises(gateway.CaptureReceiptInvalid):
        gateway._validate_source_start(operation, changed, current, at)


def test_elapsed_prepare_is_not_a_current_start():
    current, operation, receipt, at = prepared_start()
    with pytest.raises(gateway.CaptureReceiptInvalid):
        gateway._validate_source_start(
            operation,
            receipt.model_copy(update={"preparation_expires_at": at}),
            current,
            at,
        )


class ReadResult(BaseModel):
    value: int


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind,code",
    [
        ("timeout", "scribe_request_timeout"),
        ("identity", "scribe_identity_unavailable"),
    ],
)
async def test_read_failures_keep_bounded_timeout_and_identity_codes(kind, code):
    class Credential(StaticCredential):
        async def get_token(self, *args, **kwargs):
            if kind == "identity":
                raise AzureError("private_sentinel")
            return await super().get_token(*args, **kwargs)

    def remote(request):
        raise httpx.ReadTimeout("private_sentinel", request=request)

    async with httpx.AsyncClient(
        base_url="https://scribe.test.invalid", transport=httpx.MockTransport(remote)
    ) as transport:
        receiver = HttpScribeWorkflowClient(
            base_url="https://scribe.test.invalid",
            scope="api://scribe/.default",
            credential=Credential(),
            timeout_seconds=5,
            client=transport,
        )
        with pytest.raises(ScribeWorkflowRetryableError) as error:
            await receiver._get_resource(
                uuid4(), "/synthetic", ReadResult, max_bytes=1024
            )
    assert error.value.code == code
    assert "private_sentinel" not in str(error.value)


@pytest.mark.asyncio
async def test_read_accepts_case_insensitive_json_media_type():
    async with httpx.AsyncClient(
        base_url="https://scribe.test.invalid",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                content=b'{"value": 7}',
                headers={"content-type": "Application/JSON; charset=utf-8"},
            )
        ),
    ) as transport:
        receiver = HttpScribeWorkflowClient(
            base_url="https://scribe.test.invalid",
            scope="api://scribe/.default",
            credential=StaticCredential(),
            timeout_seconds=5,
            client=transport,
        )
        assert (
            await receiver._get_resource(
                uuid4(), "/synthetic", ReadResult, max_bytes=1024
            )
        ).value == 7


def test_disabled_authority_on_encounter_context_is_retryable(monkeypatch):
    connection = client()
    assert isinstance(connection.app, FastAPI)
    connection.app.dependency_overrides[get_capture_settings] = (
        lambda: ScribeCaptureSettings(enabled=True, prep_enabled=True)
    )

    def disabled(**_):
        raise authority.AuthorityKernelDisabledError("synthetic")

    monkeypatch.setattr(encounter_prep, "read_encounter_prep", disabled)
    result = connection.post(
        "/service/v1/agent/scribe/encounter-context",
        headers=headers(),
        json={
            "binding_ref": "scribe_capture_binding_01j00000000000000000000000",
            "workspace_id": str(uuid4()),
            "request_nonce": str(uuid4()),
        },
    )
    assert result.status_code == 503
    assert result.json()["code"] == "PLATFORM_UNAVAILABLE"
