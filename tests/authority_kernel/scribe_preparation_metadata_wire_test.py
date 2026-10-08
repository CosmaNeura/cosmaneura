"""One closed workflow request; each nested receipt and authority fence remains."""

from uuid import uuid4

import httpx
import pytest

from app.services import scribe_capture_gateway as gateway
from app.services import scribe_capture_protocol as protocol
from app.services.scribe_capture_admission import CaptureCaller
from app.services.scribe_creator_review_contract import CreatorReviewView
from app.services.scribe_workflow import HttpScribeWorkflowClient

from tests.authority_kernel.scribe_capture_gateway_test import pair
from tests.authority_kernel.scribe_release_review_regressions_test import prepared_start
from tests.services.scribe_workflow_test import StaticCredential


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "malformed", [None, "review", "encryption_key", "capabilities"]
)
async def test_one_closed_workflow_call_validates_all_components(
    monkeypatch, malformed
):
    scope = prepared_start()[0]
    key = pair("encryption_key", scope)[1]
    capabilities = pair("capabilities", scope)[1]
    assert isinstance(key, protocol.EncryptionKey)
    assert isinstance(capabilities, protocol.Capabilities)
    value = protocol.PreparationMetadata(
        review=CreatorReviewView(
            schema_version="1.0", session_id=scope.session_id, status="created"
        ),
        encryption_key=key,
        capabilities=capabilities,
    ).model_dump(mode="json")
    if malformed == "review":
        value["review"]["session_id"] = str(uuid4())
    elif malformed == "encryption_key":
        value["encryption_key"]["algorithm"] = "untrusted"
    elif malformed == "capabilities":
        value["capabilities"]["private_sentinel"] = True
    calls, checks = [], []

    def authorize(*_args):
        checks.append(True)
        return scope

    def wire(request):
        calls.append(request)
        return httpx.Response(200, json=value)

    monkeypatch.setattr(gateway, "authorize_admitted_capture", authorize)
    async with httpx.AsyncClient(
        base_url="https://scribe.synthetic.invalid", transport=httpx.MockTransport(wire)
    ) as connection:
        client = HttpScribeWorkflowClient(
            base_url="https://scribe.synthetic.invalid",
            scope="api://scribe/.default",
            timeout_seconds=5,
            credential=StaticCredential(),
            client=connection,
        )

        async def invoke():
            return await gateway.execute_capture_operation(
                caller=CaptureCaller(
                    workload_client_id="synthetic-workload",
                    mount_context_token="synthetic-context",
                    interaction_ref="synthetic-interaction",
                    client_turn_id=uuid4(),
                ),
                binding_ref="synthetic-binding",
                workspace_id=uuid4(),
                operation=protocol.ReadOperation(kind="preparation_metadata"),
                client_request_key="retained-metadata-key",
                client=client,
            )

        if malformed:
            expected = (
                gateway.CaptureReceiptInvalid
                if malformed == "review"
                else gateway.CaptureOperationUnconfirmed
            )
            with pytest.raises(expected):
                await invoke()
        else:
            assert (await invoke()).model_dump(mode="json") == value
            assert len(checks) == 3
    assert len(calls) == 1
    request = calls[0]
    assert request.method == "GET"
    assert (
        request.url.path
        == f"/v1/captured-sessions/{scope.session_id}/preparation-metadata"
    )
    assert request.headers["x-scribe-creator-actor-id"] == scope.creator_actor_id
    assert request.headers["x-tenant-id"] == str(scope.tenant_id)
    assert request.content == b""
