"""Closed correction history must explain every changed working value."""

from copy import deepcopy
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.services.scribe_capture_protocol import OPERATION_ADAPTER
from app.services.scribe_creator_review_contract import CreatorReviewView

from tests.authority_kernel.scribe_creator_review_test import view_document


def edited_view():
    value = view_document(uuid4())
    value["status"] = "transcript_ready"
    source = value["source"]["segments"][0]
    source.update(source_locale="en-US", content="Synthetic wrist soreness.")
    working = value["working"]
    working.update(working_transcript_version=2, blockers=[], note_ready=True)
    working["segments"][0].update(source)
    working["segments"][0].update(
        resolution="english_source",
        content="Synthetic mild wrist soreness.",
        speaker_role="clinician",
    )
    working["segments"][0].pop("locale_confidence", None)
    value["review_queue"].update(
        working_transcript_version=2, status="complete", items=[]
    )
    request = dict(
        schema_version="1.0",
        expected_source_transcript_version=1,
        expected_working_transcript_id=working["working_transcript_id"],
        expected_working_transcript_version=1,
        corrections=[
            dict(
                source_segment_id=source["source_segment_id"],
                expected_source_segment_version=1,
                content="Synthetic mild wrist soreness.",
            )
        ],
        speakers=[
            dict(
                speaker_id=source["speaker_id"],
                role="clinician",
                display_name="Synthetic Provider",
                detail=None,
            )
        ],
    )
    value.update(
        editing_allowed=True,
        review_edits=[
            dict(
                edit_id=str(uuid4()),
                working_transcript_version=2,
                created_at=datetime.now(UTC).isoformat(),
                request=request,
            )
        ],
    )
    return value


def test_explicit_edit_history_projects_without_changing_source():
    value = edited_view()
    parsed = CreatorReviewView.model_validate(value)
    assert parsed.source.segments[0].content == "Synthetic wrist soreness."
    assert parsed.working.segments[0].content == "Synthetic mild wrist soreness."
    assert parsed.working.segments[0].speaker_role == "clinician"
    assert (
        OPERATION_ADAPTER.validate_python(
            dict(kind="edit_transcript", payload=value["review_edits"][0]["request"])
        ).kind
        == "edit_transcript"
    )


@pytest.mark.parametrize(
    "change",
    [
        "missing_history",
        "wrong_text",
        "wrong_role",
        "wrong_source",
        "future_version",
        "foreign_speaker",
        "foreign_working",
    ],
)
def test_changed_projection_requires_exact_current_evidence(change):
    value = deepcopy(edited_view())
    if change == "missing_history":
        value.pop("review_edits")
    if change == "wrong_text":
        value["working"]["segments"][0]["content"] = "Unevidenced assertion"
    if change == "wrong_role":
        value["working"]["segments"][0]["speaker_role"] = "patient"
    if change == "wrong_source":
        value["review_edits"][0]["request"]["expected_source_transcript_version"] = 2
    if change == "future_version":
        value["review_edits"][0]["working_transcript_version"] = 3
        value["review_edits"][0]["request"]["expected_working_transcript_version"] = 2
    if change == "foreign_speaker":
        value["review_edits"][0]["request"]["speakers"][0]["speaker_id"] = str(uuid4())
    if change == "foreign_working":
        value["review_edits"][0]["request"]["expected_working_transcript_id"] = str(
            uuid4()
        )
    with pytest.raises(ValidationError):
        CreatorReviewView.model_validate(value)


def test_legacy_review_serialization_does_not_add_unknown_fields():
    value = view_document(uuid4())
    result = CreatorReviewView.model_validate(value).model_dump(mode="json")
    assert "review_edits" not in result and "editing_allowed" not in result


@pytest.mark.asyncio
async def test_edit_transport_preserves_creator_scope_key_and_receipt(monkeypatch):
    import json
    from dataclasses import replace
    from typing import cast

    import httpx

    from app.services import scribe_capture_gateway as gateway
    from app.services.scribe_capture_admission import CaptureCaller
    from app.services.scribe_creator_edit_contract import CreatorTranscriptEditReceiptV1
    from app.services.scribe_workflow import HttpScribeWorkflowClient

    from tests.authority_kernel.scribe_gateway_receiver_test import scope
    from tests.services.scribe_workflow_test import StaticCredential

    selected = replace(scope(), creator_actor_id="synthetic_creator")
    request = edited_view()["review_edits"][0]["request"]
    operation = OPERATION_ADAPTER.validate_python(
        dict(kind="edit_transcript", payload=request)
    )
    receipt = CreatorTranscriptEditReceiptV1(
        session_id=selected.session_id,
        edit_id=uuid4(),
        working_transcript_id=request["expected_working_transcript_id"],
        working_transcript_version=2,
    )
    calls = []

    def handler(request):
        calls.append(request)
        assert (
            request.url.path
            == f"/v1/captured-sessions/{selected.session_id}/review/edit"
        )
        assert request.headers["idempotency-key"] == "original-synthetic-edit-key"
        assert request.headers["x-scribe-creator-actor-id"] == selected.creator_actor_id
        assert request.headers["x-tenant-id"] == str(selected.tenant_id)
        assert json.loads(request.content) == operation.payload.model_dump(mode="json")
        return httpx.Response(200, json=receipt.model_dump(mode="json"))

    monkeypatch.setattr(gateway, "authorize_admitted_capture", lambda *_: selected)
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
            client_request_key="original-synthetic-edit-key",
            client=remote,
        )
    assert result == receipt and len(calls) == 1
    with pytest.raises(gateway.CaptureReceiptInvalid):
        gateway.validate_receipt(
            operation,
            receipt.model_copy(update={"working_transcript_version": 3}),
            selected,
            upload_origins=(),
        )
