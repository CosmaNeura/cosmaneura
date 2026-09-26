"""Synthetic long visits; bounded transport and real local note persistence."""

import copy
import json
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import ValidationError

from app.services import scribe_capture_note_edits as edits
from app.services import scribe_capture_notes as notes
from app.services import scribe_capture_protocol as protocol
from app.services.scribe_capture_source_contract import MAX_CAPTURE_SOURCE_BYTES
from app.services.scribe_capture_working_contract import (
    MAX_TRANSCRIPT_TOTAL_CONTENT_CHARS,
    MAX_WORKING_BYTES,
    CaptureWorkingTranscript,
    TranscriptCorrection,
    canonical_hash,
)
from app.services.scribe_creator_review_contract import (
    MAX_CREATOR_REVIEW_BYTES,
    CreatorReviewView,
)
from app.services.scribe_workflow import CaptureTranscript

from app.models.ai_visit_note import AIVisitNote
from app.models.ai_visit_transcript import AIVisitTranscript

from tests.authority_kernel.scribe_capture_client_test import (
    client_for,
    status_body,
    transcript_body,
)
from tests.authority_kernel.scribe_capture_notes_test import (
    admission_case,
    capture_engine,
    rows,
)
from tests.authority_kernel.scribe_capture_working_test import (
    setup_working,
    working_receipt,
)
from tests.authority_kernel.scribe_creator_review_test import view_document

__all__ = ["admission_case", "capture_engine"]


def long_raw(session_id, *, count=1800, width=250, unicode=False):
    filler = "🧪" if unicode else "Invented visit statement. "
    return CaptureTranscript.model_validate(
        transcript_body(
            session_id,
            segments=[
                dict(
                    content=(f"Synthetic segment {i:05d}: " + filler * width)[:width],
                    start_ms=i * 10_800_000 // count,
                    end_ms=(i + 1) * 10_800_000 // count,
                    speaker_id="🧪" * 80 if unicode else "synthetic-speaker",
                    speaker_role="patient",
                    confidence=None,
                )
                for i in range(count)
            ],
        )
    )


def long_review(session_id):
    document = view_document(session_id)
    source, working, queue = (
        document["source"],
        document["working"],
        document["review_queue"],
    )
    original, projected, review = (
        source["segments"][0],
        working["segments"][0],
        queue["items"][0],
    )
    source["duration_ms"] = 10_800_000
    source["segments"], working["segments"], working["blockers"], queue["items"] = (
        [],
        [],
        [],
        [],
    )
    for i in range(10_000):
        identity = dict(
            source_segment_id=str(UUID(int=i + 1)),
            start_ms=i * 1080,
            end_ms=(i + 1) * 1080,
        )
        content = "🧪" * 50
        source["segments"].append({**original, **identity, "content": content})
        working["segments"].append({**projected, **identity})
        working["blockers"].append(
            dict(
                source_segment_id=identity["source_segment_id"],
                code="translation_review_required",
            )
        )
        queue["items"].append(
            {
                **review,
                **identity,
                "source_content": content,
                "translated_content": content,
            }
        )
    return document


@pytest.mark.asyncio
async def test_maximum_unicode_review_and_raw_survive_bounded_http_receivers():
    session_id, tenant_id = uuid4(), uuid4()
    document = long_review(session_id)
    review = CreatorReviewView.model_validate(document)
    # JSON escaping costs more than UTF-8 here; both fit without truncation.
    encoded = json.dumps(document).encode()
    assert 8 * 1024 * 1024 < len(encoded) <= MAX_CREATOR_REVIEW_BYTES
    raw = long_raw(session_id, count=10_000, width=50, unicode=True)
    raw_bytes = raw.model_dump_json().encode()
    assert 4 * 1024 * 1024 < len(raw_bytes) <= MAX_CAPTURE_SOURCE_BYTES
    working = working_receipt(raw, "synthetic-practitioner")
    working_bytes = json.dumps(working.model_dump(mode="json")).encode()
    assert len(working_bytes) <= MAX_WORKING_BYTES

    def handler(request):
        if request.url.path == f"/v1/captured-sessions/{session_id}/review":
            return httpx.Response(
                200, content=encoded, headers={"Content-Type": "application/json"}
            )
        if request.url.path.endswith("/transcription-status"):
            return httpx.Response(200, json=status_body(session_id))
        if request.url.path == f"/v1/sessions/{session_id}/working-transcript":
            return httpx.Response(
                200, content=working_bytes, headers={"Content-Type": "application/json"}
            )
        assert request.url.path == f"/v1/sessions/{session_id}/transcript"
        return httpx.Response(
            200, content=raw_bytes, headers={"Content-Type": "application/json"}
        )

    async with httpx.AsyncClient(
        base_url="https://scribe.test.invalid", transport=httpx.MockTransport(handler)
    ) as transport:
        remote = client_for(transport)
        assert (
            await remote.capture_operation(
                tenant_id,
                session_id,
                protocol.ReadOperation(kind="creator_review"),
                idempotency_key=uuid4().hex,
                creator_actor_id="synthetic-creator",
            )
            == review
        )
        assert await remote.get_capture_transcript(tenant_id, session_id) == raw
        assert (
            await remote.capture_operation(
                tenant_id,
                session_id,
                protocol.ReadOperation(kind="working_transcript"),
                idempotency_key=uuid4().hex,
                practitioner_id="doctor_" + "0" * 26,
            )
            == working
        )
    assert review.source is not None and len(review.source.segments) == 10_000


def test_total_character_boundary_preserves_segment_and_lineage_guards():
    raw = long_raw(uuid4(), count=5, width=100_000)
    receipt = working_receipt(raw, "synthetic-practitioner")
    assert sum(len(s.content) for s in receipt.working_source.segments) == (
        MAX_TRANSCRIPT_TOTAL_CONTENT_CHARS
    )
    correction = dict(
        raw_transcript_id=raw.id,
        raw_transcript_sha256=receipt.raw_transcript_sha256,
        expected_working_version=1,
        expected_working_sha256=receipt.working_sha256,
        corrections=[
            dict(
                source_segment_id=s.source_segment_id,
                content=s.content,
                speaker_role=s.speaker_role,
            )
            for s in receipt.working_source.segments
        ],
    )
    assert len(TranscriptCorrection.model_validate(correction).corrections) == 5
    correction["corrections"].append(
        dict(source_segment_id=uuid4(), content="x", speaker_role="patient")
    )
    with pytest.raises(ValidationError):
        TranscriptCorrection.model_validate(correction)
    body = receipt.model_dump(mode="json")
    original = body["working_source"]["segments"][-1]
    original["end_ms"] -= 1
    body["working_transcript"]["segments"][-1]["end_ms"] -= 1
    extra = {
        **original,
        "source_segment_id": str(uuid4()),
        "content": "x",
        "start_ms": original["end_ms"],
        "end_ms": original["end_ms"] + 1,
    }
    body["working_source"]["segments"].append(extra)
    body["working_transcript"]["segments"].append(
        {
            **body["working_transcript"]["segments"][-1],
            **{k: v for k, v in extra.items() if k != "locale_confidence"},
        }
    )
    body["working_sha256"] = canonical_hash(
        {
            "raw_transcript_sha256": body["raw_transcript_sha256"],
            "working_source": body["working_source"],
        }
    )
    with pytest.raises(ValidationError):
        CaptureWorkingTranscript.model_validate(body)
    body = receipt.model_dump(mode="json")
    body["working_source"]["segments"][-1]["content"] = "Altered final source"
    with pytest.raises(ValidationError):
        CaptureWorkingTranscript.model_validate(body)
    review = long_review(uuid4())
    assert CreatorReviewView.model_validate(review).status == "review_required"
    review["source"]["segments"][-1]["content"] += "x"
    with pytest.raises(ValidationError):
        CreatorReviewView.model_validate(review)
    segment = raw.segments[0].model_dump()
    segment["content"] += "x"
    with pytest.raises(ValidationError):
        type(raw.segments[0]).model_validate(segment)


@pytest.mark.asyncio
async def test_dense_three_hour_source_reaches_prompt_and_saved_reopened_note(
    admission_case, monkeypatch
):
    f = admission_case
    kwargs = await setup_working(f, monkeypatch)
    f.raw = long_raw(f.scope.session_id)
    f.client.result = f.raw
    f.working = working_receipt(f.raw, f.scope.session_request.practitioner_id)
    snapshot = copy.deepcopy(f.raw.model_dump(mode="json"))
    first = await notes.capture_note(**kwargs)
    assert len(f.model.calls) == 1
    for prompt, _ in f.model.calls:
        assert len(prompt.encode()) <= 4_194_304
        assert all(segment.content in prompt for segment in f.raw.segments)
    retained = rows(f, AIVisitTranscript)
    assert len(retained) == 1 and retained[0].duration == 10_800
    assert [s.content for s in retained[0].transcript_data] == [
        s.content for s in f.raw.segments
    ]
    assert first.note_ref is not None and first.note_sha256 is not None
    request = {
        **{k: v for k, v in kwargs.items() if k not in {"prepare", "model"}},
        "edit": edits.NoteEdit(
            client_key=uuid4(),
            expected_note_ref=first.note_ref,
            expected_note_sha256=first.note_sha256,
            expected_source_sha256=first.source_sha256,
            fields={
                "CC": "Synthetic long visit",
                "Plan": "No medication change recorded.",
            },
        ),
    }
    saved = await edits.save_capture_note(**request)
    assert await edits.save_capture_note(**request) == saved
    reopened = await notes.capture_note(**{**kwargs, "prepare": False})
    assert reopened == saved and reopened.source_sha256 == first.source_sha256
    assert len(f.model.calls) == 1 and len(rows(f, AIVisitNote)) == 2
    assert f.raw.model_dump(mode="json") == snapshot
