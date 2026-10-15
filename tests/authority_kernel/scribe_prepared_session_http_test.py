"""Actual HTTP client: fixed request, closed reply, no uncertain fallback."""

from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
from azure.core.credentials import AccessToken

from app.services import scribe_capture_protocol as p
from app.services.scribe_configured_capture_contract import (
    ConfiguredSessionCreate,
    ConfiguredSessionReceipt,
)
from app.services.scribe_creator_review_contract import CreatorReviewView
from app.services.scribe_workflow import HttpScribeWorkflowClient, ScribeWorkflowError

from tests.authority_kernel.scribe_capture_gateway_test import pair
from tests.authority_kernel.scribe_gateway_receiver_test import scope


class Credential:
    async def get_token(self, *_args):
        return AccessToken("synthetic-only", 4102444800)

    async def close(self):
        pass


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault", [None, "large", "creator", "session", "extra", "missing", "lost", "denied"]
)
async def test_combined_http_contract_and_no_fallback(fault):
    payload = ConfiguredSessionCreate(
        schema_version="1.0",
        id=uuid4(),
        appointment_id="synthetic-visit",
        practitioner_id="synthetic-provider",
        patient_id="synthetic-person",
        consent_record_id=uuid4(),
        modality="virtual",
        language_mode="en-US",
    )
    receipt = ConfiguredSessionReceipt(
        **payload.model_dump(),
        creator_actor_id="synthetic-creator",
        created_at=datetime.now(UTC),
        ingest_mode="chunk_upload",
        transcript_contract_version="1.0",
        transcription_adapter_id="azure_ai_speech",
        transcription_model_profile_id="azure_fast_transcription_en_es_v1",
        normalization_profile_id="azure_phrase_en_es_evidence_v1",
        status="admitted",
    )
    metadata = p.PreparationMetadata(
        review=CreatorReviewView(
            schema_version="1.0", session_id=payload.id, status="created"
        ),
        encryption_key=pair("encryption_key", scope())[1],
        capabilities=pair("capabilities", scope())[1],
    )
    if fault == "large":
        # A valid retained review can be larger than a small mutation receipt.
        # It must fit the same metadata allowance as the existing separate read.
        segments = [
            dict(
                source_segment_id=str(uuid4()),
                source_segment_version=1,
                source_locale="en-US",
                speaker_id=str(uuid4()),
                speaker_role="patient",
                start_ms=index * 1000,
                end_ms=(index + 1) * 1000,
            )
            for index in range(5)
        ]
        content = "Fictional test content. " * 150
        working_id = str(uuid4())
        review = CreatorReviewView.model_validate(
            dict(
                schema_version="1.0",
                session_id=str(payload.id),
                status="transcript_ready",
                source=dict(
                    schema_version="1.0",
                    source_transcript_version=1,
                    language_mode="en-US",
                    transcription_adapter_id="synthetic",
                    transcription_model_profile_id="synthetic",
                    normalization_profile_id="synthetic",
                    duration_ms=5000,
                    segments=[dict(**row, content=content) for row in segments],
                ),
                working=dict(
                    schema_version="1.0",
                    working_transcript_id=working_id,
                    working_transcript_version=1,
                    source_transcript_version=1,
                    segments=[
                        dict(**row, resolution="english_source", content=content)
                        for row in segments
                    ],
                    blockers=[],
                    note_ready=True,
                ),
                review_queue=dict(
                    schema_version="1.0",
                    session_id=str(payload.id),
                    source_transcript_version=1,
                    working_transcript_id=working_id,
                    working_transcript_version=1,
                    status="complete",
                    items=[],
                ),
            )
        )
        metadata = metadata.model_copy(update={"review": review})
        assert len(metadata.model_dump_json().encode()) > 16_384
    answer = dict(
        schema_version="1.0",
        admission=receipt.model_dump(mode="json"),
        preparation_metadata=metadata.model_dump(mode="json"),
    )
    if fault == "creator":
        answer["admission"]["creator_actor_id"] = "foreign-creator"
    if fault == "session":
        answer["preparation_metadata"]["review"]["session_id"] = str(uuid4())
    if fault == "extra":
        answer["unexpected"] = "synthetic-private"
    if fault == "missing":
        del answer["preparation_metadata"]
    calls = []

    def handler(request):
        calls.append(request)
        assert request.url.path == "/v1/captured-sessions/prepared"
        assert request.headers["Idempotency-Key"] == "original-session-key"
        assert request.headers["X-Scribe-Creator-Actor-ID"] == "synthetic-creator"
        if fault == "lost":
            raise httpx.ReadError("synthetic-only")
        return httpx.Response(403 if fault == "denied" else 201, json=answer)

    async with httpx.AsyncClient(
        base_url="https://scribe.test.invalid", transport=httpx.MockTransport(handler)
    ) as http:
        client = HttpScribeWorkflowClient(
            base_url="https://scribe.test.invalid",
            scope="synthetic-scope",
            timeout_seconds=5,
            credential=Credential(),
            client=http,
        )

        async def invoke():
            return await client.create_prepared_configured_session(
                uuid4(),
                payload,
                creator_actor_id="synthetic-creator",
                idempotency_key="original-session-key",
            )

        if fault and fault != "large":
            with pytest.raises(ScribeWorkflowError):
                await invoke()
        else:
            result = await invoke()
            assert (
                result.admission == receipt and result.preparation_metadata == metadata
            )
        assert len(calls) == 1
