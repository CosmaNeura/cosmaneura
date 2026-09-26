"""Creator-review authority and closed wire consumption; invented data only."""

import json
from dataclasses import replace
from typing import Any, cast
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError

from app.services import scribe_capture_gateway as gateway
from app.services import scribe_capture_protocol as protocol
from app.services.scribe_capture_admission import CaptureCaller
from app.services.scribe_creator_review_contract import (
    MAX_CREATOR_REVIEW_BYTES,
    CreatorDecisionReceipt,
    CreatorReviewView,
)
from app.services.scribe_workflow import (
    HttpScribeWorkflowClient,
    ScribeWorkflowClient,
    ScribeWorkflowError,
)

from tests.authority_kernel.scribe_gateway_receiver_test import (
    PATH,
    body,
    client,
    request_headers,
    scope,
)
from tests.services.scribe_workflow_test import StaticCredential


def view_document(session_id) -> dict[str, Any]:
    segment = dict(
        source_segment_id=str(uuid4()),
        source_segment_version=1,
        source_locale="es-US",
        speaker_id=str(uuid4()),
        speaker_role="patient",
        start_ms=0,
        end_ms=1000,
    )
    working_id = str(uuid4())
    return dict(
        schema_version="1.0",
        session_id=str(session_id),
        status="review_required",
        source=dict(
            schema_version="1.0",
            source_transcript_version=1,
            language_mode="en-US+es-US",
            transcription_adapter_id="synthetic",
            transcription_model_profile_id="synthetic",
            normalization_profile_id="synthetic",
            duration_ms=1000,
            segments=[
                dict(**segment, content="No tengo fiebre.", locale_confidence=0.9)
            ],
        ),
        working=dict(
            schema_version="1.0",
            working_transcript_id=working_id,
            working_transcript_version=1,
            source_transcript_version=1,
            segments=[
                dict(
                    **segment,
                    resolution="unresolved",
                    content=None,
                    translation_id=None,
                    translation_version=None,
                    translation_review_id=None,
                )
            ],
            blockers=[
                dict(
                    source_segment_id=segment["source_segment_id"],
                    code="translation_review_required",
                )
            ],
            note_ready=False,
        ),
        review_queue=dict(
            schema_version="1.0",
            session_id=str(session_id),
            source_transcript_version=1,
            working_transcript_id=working_id,
            working_transcript_version=1,
            status="review_required",
            items=[
                dict(
                    source_segment_id=segment["source_segment_id"],
                    source_segment_version=1,
                    translation_id=str(uuid4()),
                    translation_version=1,
                    source_locale="es-US",
                    target_locale="en-US",
                    speaker_role="patient",
                    start_ms=0,
                    end_ms=1000,
                    source_content="No tengo fiebre.",
                    translated_content="I do not have a fever.",
                )
            ],
        ),
        translation_progress=[],
    )


def decision_operation(view, decision="accepted"):
    queue = view["review_queue"]
    item = queue["items"][0]
    return protocol.OPERATION_ADAPTER.validate_python(
        dict(
            kind="decide_translation",
            payload=dict(
                schema_version="1.0",
                expected_source_transcript_version=queue["source_transcript_version"],
                source_segment_id=item["source_segment_id"],
                expected_source_segment_version=item["source_segment_version"],
                translation_id=item["translation_id"],
                expected_translation_version=item["translation_version"],
                decision=decision,
            ),
        )
    )


@pytest.mark.parametrize(
    "change",
    [
        "session",
        "source_version",
        "working_id",
        "source_content",
        "speaker",
        "timing",
        "ready",
        "english",
        "duplicate",
        "progress",
        "unknown_field",
        "metadata",
    ],
)
def test_cross_source_or_malformed_review_is_rejected(change):
    data = view_document(uuid4())
    item = data["review_queue"]["items"][0]
    if change == "session":
        data["review_queue"]["session_id"] = str(uuid4())
    elif change == "source_version":
        data["working"]["source_transcript_version"] = 2
    elif change == "working_id":
        data["review_queue"]["working_transcript_id"] = str(uuid4())
    elif change == "source_content":
        item["source_content"] = "foreign synthetic text"
    elif change == "speaker":
        data["working"]["segments"][0]["speaker_id"] = str(uuid4())
    elif change == "timing":
        item["end_ms"] += 1
    elif change == "ready":
        data["working"]["note_ready"] = True
    elif change == "english":
        data["working"]["segments"][0]["resolution"] = "english_source"
    elif change == "duplicate":
        data["review_queue"]["items"].append(item.copy())
    elif change == "progress":
        data["translation_progress"] = [
            dict(
                source_segment_id=item["source_segment_id"],
                source_segment_version=1,
                state="unconfirmed",
            )
        ]
    elif change == "unknown_field":
        item["actor_id"] = "private_sentinel"
    else:
        data["status"] = "handed_off"
    with pytest.raises(ValidationError) as error:
        CreatorReviewView.model_validate(data)
    assert "private_sentinel" not in str(error.value)


@pytest.mark.parametrize(
    "field",
    ["creator_actor_id", "reviewer_actor_id", "tenant_id", "session_id", "content"],
)
def test_receiver_rejects_browser_authority_and_content(monkeypatch, field):
    payload = body()
    op = decision_operation(view_document(uuid4())).model_dump(mode="json")
    op["payload"][field] = "private_sentinel"
    payload["operation"] = op
    monkeypatch.setattr(
        gateway, "execute_capture_operation", lambda **_: pytest.fail("gateway reached")
    )
    response = client().post(PATH, headers=request_headers(), json=payload)
    assert (
        response.status_code == 422 and response.headers["cache-control"] == "no-store"
    )
    assert "private_sentinel" not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", [None, "accepted", "rejected"])
async def test_gateway_transport_preserves_authorized_actor_and_exact_key(
    monkeypatch, decision
):
    selected = replace(scope(), creator_actor_id="actor_synthetic_creator")
    view = view_document(selected.session_id)
    operation = (
        protocol.ReadOperation(kind="creator_review")
        if decision is None
        else decision_operation(view, decision)
    )
    receipt = (
        CreatorReviewView.model_validate(view)
        if decision is None
        else CreatorDecisionReceipt(
            schema_version="1.0",
            session_id=selected.session_id,
            review_id=uuid4(),
            saved=True,
        )
    )
    authorizations, calls = [], []

    def authorize(*_):
        authorizations.append(True)
        return selected

    def handler(request):
        calls.append(request)
        assert request.url.path == f"/v1/captured-sessions/{selected.session_id}/review"
        assert request.headers["x-scribe-creator-actor-id"] == selected.creator_actor_id
        assert request.headers["x-tenant-id"] == str(selected.tenant_id)
        assert "x-capture-practitioner" not in request.headers
        assert request.headers["cache-control"] == "no-store"
        if decision:
            assert isinstance(operation, protocol.DecideTranslation)
            assert request.headers["idempotency-key"] == "original-native-decision-key"
            assert json.loads(request.content) == operation.payload.model_dump(
                mode="json"
            )
        return httpx.Response(200, json=receipt.model_dump(mode="json"))

    monkeypatch.setattr(gateway, "authorize_admitted_capture", authorize)
    async with httpx.AsyncClient(
        base_url="https://scribe.invalid", transport=httpx.MockTransport(handler)
    ) as transport:
        remote = HttpScribeWorkflowClient(
            base_url="https://scribe.invalid",
            scope="api://scribe/.default",
            credential=StaticCredential(),
            timeout_seconds=3,
            client=transport,
        )
        result = await gateway.execute_capture_operation(
            caller=cast(CaptureCaller, None),
            binding_ref="synthetic",
            workspace_id=uuid4(),
            operation=operation,
            client_request_key="original-native-decision-key",
            client=remote,
        )
    assert result == receipt and len(authorizations) == 2 and len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["missing_actor", "revoked_after", "changed_scope"])
async def test_review_requires_current_actor_before_and_after_remote(
    monkeypatch, failure
):
    selected = replace(
        scope(),
        creator_actor_id=None if failure == "missing_actor" else "actor_synthetic",
    )
    calls = []
    count = 0

    def authorize(*_):
        nonlocal count
        count += 1
        if count == 2 and failure == "revoked_after":
            raise gateway.CaptureOperationUnavailable
        return (
            replace(selected, creator_actor_id="actor_other")
            if count == 2
            else selected
        )

    class Remote:
        async def capture_operation(self, *_, **__):
            calls.append(True)
            return CreatorReviewView.model_validate(view_document(selected.session_id))

    monkeypatch.setattr(gateway, "authorize_admitted_capture", authorize)
    with pytest.raises(gateway.CaptureGatewayError):
        await gateway.execute_capture_operation(
            caller=cast(CaptureCaller, None),
            binding_ref="synthetic",
            workspace_id=uuid4(),
            operation=protocol.ReadOperation(kind="creator_review"),
            client_request_key=uuid4().hex,
            client=cast(ScribeWorkflowClient, Remote()),
        )
    assert len(calls) == (0 if failure == "missing_actor" else 1)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", ["redirect", "oversized", "invalid", "wrong_session", "lost", "conflict"]
)
async def test_review_transport_is_bounded_and_never_automatically_retries(failure):
    selected = scope()
    calls = []

    def handler(request):
        calls.append(request)
        if failure == "lost":
            raise httpx.ReadError("private_sentinel", request=request)
        if failure in {"redirect", "conflict"}:
            return httpx.Response(
                307 if failure == "redirect" else 409,
                headers={"Location": "https://foreign.invalid"},
            )
        if failure == "oversized":
            return httpx.Response(
                200,
                headers={"Content-Type": "application/json"},
                content=b" " * (MAX_CREATOR_REVIEW_BYTES + 1),
            )
        return httpx.Response(
            200,
            json=view_document(uuid4())
            if failure == "wrong_session"
            else {"private_sentinel": True},
        )

    async with httpx.AsyncClient(
        base_url="https://scribe.invalid",
        transport=httpx.MockTransport(handler),
        follow_redirects=True,
    ) as transport:
        remote = HttpScribeWorkflowClient(
            base_url="https://scribe.invalid",
            scope="api://scribe/.default",
            credential=StaticCredential(),
            timeout_seconds=3,
            client=transport,
        )
        with pytest.raises(ScribeWorkflowError) as error:
            await remote.capture_operation(
                selected.tenant_id,
                selected.session_id,
                protocol.ReadOperation(kind="creator_review"),
                idempotency_key=uuid4().hex,
                creator_actor_id="actor_synthetic",
            )
        assert len(calls) == 1 and "private_sentinel" not in str(error.value)
