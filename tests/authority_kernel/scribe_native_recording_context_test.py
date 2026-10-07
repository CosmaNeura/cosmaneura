"""Native key namespaces come from existing admitted scope; synthetic only."""

from dataclasses import replace
from typing import cast
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.services import scribe_capture_gateway as gateway
from app.services import scribe_capture_protocol as protocol
from app.services.scribe_capture_admission import CaptureCaller
from app.services.scribe_configured_capture_contract import ConfiguredSessionCreate
from app.services.scribe_creator_review_contract import CreatorReviewView
from app.services.scribe_workflow import (
    ScribeWorkflowClient,
    ScribeWorkflowRetryableError,
)

from tests.authority_kernel.scribe_gateway_receiver_test import (
    PATH,
    body,
    client,
    request_headers,
    scope,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["created", "capturing"])
async def test_recording_context_uses_verified_scribe_scope_only(monkeypatch, status):
    result, calls, authorizations, selected, workspace = await exercise(
        monkeypatch, status=status
    )
    assert result.model_dump(mode="json") == {
        "schema_version": "1.0",
        "workspace_id": str(workspace),
        "tenant_id": str(selected.tenant_id),
        "session_id": str(selected.session_id),
        "status": status,
    }
    assert authorizations == [True, True]
    assert len(calls) == 1
    assert calls[0][0:2] == (selected.tenant_id, selected.session_id)
    assert calls[0][2].kind == "creator_review"
    assert calls[0][3]["creator_actor_id"] == selected.creator_actor_id
    assert str(selected.tenant_id) not in repr(result)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        "legacy",
        "missing_actor",
        "revoked_after",
        "changed_scope",
        "wrong_session",
        "uploaded",
        "processing",
        "speech_failed",
        "lost",
    ],
)
async def test_recording_context_rejects_unconfirmed_or_nonrecordable_session(
    monkeypatch, failure
):
    calls = []
    with pytest.raises(gateway.CaptureGatewayError):
        await exercise(monkeypatch, failure=failure, calls=calls)
    assert len(calls) == (0 if failure in {"legacy", "missing_actor"} else 1)


@pytest.mark.parametrize(
    "field,value",
    [
        ("tenant_id", str(uuid4())),
        ("session_id", str(uuid4())),
        ("workspace_id", str(uuid4())),
        ("profile_receipt_id", str(uuid4())),
    ],
)
def test_context_request_cannot_select_scope(field, value):
    with pytest.raises(ValidationError):
        protocol.OPERATION_ADAPTER.validate_python(
            {
                "kind": "native_recording_context",
                field: value,
            }
        )


def test_context_uses_existing_no_store_facade(monkeypatch):
    payload = body()
    payload["operation"] = {"kind": "native_recording_context"}
    selected = scope()
    expected = protocol.NativeRecordingContext(
        workspace_id=payload["workspace_id"],
        tenant_id=selected.tenant_id,
        session_id=selected.session_id,
        status="created",
    )

    async def execute(**kwargs):
        assert kwargs["operation"].kind == "native_recording_context"
        return expected

    monkeypatch.setattr(gateway, "execute_capture_operation", execute)
    response = client().post(PATH, headers=request_headers(), json=payload)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["receipt"] == expected.model_dump(mode="json")


async def exercise(
    monkeypatch,
    *,
    status="created",
    failure=None,
    calls=None,
    kind="native_recording_context",
):
    selected = scope()
    if failure != "legacy":
        selected = replace(
            selected,
            session_request=ConfiguredSessionCreate(
                **selected.session_request.model_dump(),
                id=selected.session_id,
                schema_version="1.0",
                language_mode="en-US",
            ),
        )
    selected = replace(
        selected,
        creator_actor_id=None if failure == "missing_actor" else "actor_synthetic",
    )
    calls = [] if calls is None else calls
    authorizations = []
    workspace = uuid4()

    def authorize(*_):
        authorizations.append(True)
        if len(authorizations) == 2:
            if failure == "revoked_after":
                raise gateway.CaptureOperationUnavailable
            if failure == "changed_scope":
                return replace(selected, tenant_id=uuid4())
        return selected

    class Remote:
        async def capture_operation(self, tenant, session, operation, **kwargs):
            calls.append((tenant, session, operation, kwargs))
            if failure == "lost":
                raise ScribeWorkflowRetryableError("private_synthetic_sentinel")
            return CreatorReviewView.model_validate(
                {
                    "schema_version": "1.0",
                    "session_id": uuid4() if failure == "wrong_session" else session,
                    "status": failure
                    if failure in {"uploaded", "processing", "speech_failed"}
                    else status,
                }
            )

    monkeypatch.setattr(gateway, "authorize_admitted_capture", authorize)
    result = await gateway.execute_capture_operation(
        caller=cast(CaptureCaller, None),
        binding_ref="synthetic-binding",
        workspace_id=workspace,
        operation=protocol.ReadOperation.model_validate({"kind": kind}),
        client_request_key="synthetic-native-context",
        client=cast(ScribeWorkflowClient, Remote()),
    )
    return result, calls, authorizations, selected, workspace
