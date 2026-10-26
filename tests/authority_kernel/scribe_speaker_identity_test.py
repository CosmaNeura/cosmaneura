"""Synthetic acoustic metadata; real local persistence and no provider/model calls."""

import json
from dataclasses import replace
from uuid import UUID, uuid4, uuid5

import pytest
from pydantic import ValidationError

from app.commands import scribe_transcript as ingest
from app.jobs import anonymize, summarize
from app.services import recall_webhook_operations as recall
from app.services import scribe_capture_notes as notes
from app.services import scribe_capture_reviewed_source as reviewed
from app.services.scribe_configured_capture_contract import ConfiguredSessionCreate
from app.services.scribe_creator_review_contract import CreatorReviewView
from app.services.scribe_workflow import CaptureTranscript

from app.models.ai_visit_note import AIVisitNote
from app.models.ai_visit_transcript import AIVisitTranscript, TranscriptEntry
from app.models.ai_visit_transcript_non_phi import AIVisitTranscriptNonPHI
from app.models.doctor_note_schema import DoctorNoteSchema
from sqlalchemy import inspect, select
from sqlmodel import Session

from tests.authority_kernel.scribe_capture_gateway_test import pair
from tests.authority_kernel.scribe_capture_notes_test import (
    admission_case,
    capture_engine,
    rows,
    setup,
)
from tests.authority_kernel.scribe_capture_working_test import working_receipt
from tests.authority_kernel.scribe_configured_capture_test import (
    note_setup as configured_note_setup,
)
from tests.authority_kernel.scribe_configured_capture_test import reviewed_document
from tests.authority_kernel.scribe_gateway_receiver_test import scope

__all__ = ["admission_case", "capture_engine"]


def two_voices(raw):
    original = raw.segments[0]
    return CaptureTranscript.model_validate(
        raw.model_copy(
            update={
                "segments": tuple(
                    original.model_copy(
                        update={
                            "speaker_id": identity,
                            "speaker_role": "unknown",
                            "content": content,
                            "start_ms": i * 1000,
                            "end_ms": (i + 1) * 1000,
                        }
                    )
                    for i, (identity, content) in enumerate(
                        [
                            ("voice-a", "A synthetic question?"),
                            ("voice-b", "A synthetic answer."),
                            ("voice-a", "A synthetic follow-up."),
                        ]
                    )
                )
            }
        ).model_dump(mode="json")
    )


def test_absent_identity_keeps_exact_legacy_json_and_known_uuid_roundtrips():
    old = {"speaker": "Unknown", "content": "Synthetic.", "startTime": 0, "endTime": 1}
    entry = TranscriptEntry(**old)
    assert entry.model_dump() == old
    assert entry.model_dump_json() == json.dumps(old, separators=(",", ":"))
    known = entry.model_copy(update={"source_speaker_id": uuid4()})
    wire = known.model_dump(mode="json")
    assert wire == {**old, "source_speaker_id": str(known.source_speaker_id)}
    assert TranscriptEntry.model_validate(wire) == known
    assert known.legacy_data() == old
    with pytest.raises(ValidationError):
        TranscriptEntry(**old, source_speaker_id="Patient")


def test_raw_working_and_reviewed_preserve_identity_without_assigning_roles():
    admitted = scope()
    _, raw = pair("transcript", admitted)
    raw = two_voices(raw)
    working = working_receipt(raw, admitted.session_request.practitioner_id)
    raw_source = notes._source(raw, admitted)
    working_source = notes._source(raw, admitted, working)
    expected = [uuid5(raw.id, "speaker:" + p.speaker_id) for p in raw.segments]
    assert [e.source_speaker_id for e in raw_source.entries] == expected
    assert [e.source_speaker_id for e in working_source.entries] == expected
    assert len(set(expected)) == 2 and expected[0] == expected[2]
    assert [e.speaker for e in raw_source.entries] == ["Unknown"] * 3
    assert raw_source.digest == working_source.digest

    document = reviewed_document(admitted.session_id)
    document["source"]["segments"][0]["speaker_role"] = "unknown"
    document["working"]["segments"][0]["speaker_role"] = "unknown"
    configured = replace(
        admitted,
        session_request=ConfiguredSessionCreate(
            **admitted.session_request.model_dump(),
            id=admitted.session_id,
            schema_version="1.0",
            language_mode="en-US+es-US",
        ),
    )
    selected = reviewed.project(CreatorReviewView.model_validate(document), configured)
    assert selected.entries[0].source_speaker_id == UUID(
        document["working"]["segments"][0]["speaker_id"]
    )
    assert selected.entries[0].speaker == "Unknown"
    old = [TranscriptEntry(**e.legacy_data()) for e in selected.entries]
    assert selected.payload_digest == reviewed.payload_digest(
        configured, selected.duration, old
    )


def test_recall_digest_keeps_the_exact_old_canonical_document(monkeypatch):
    entry = TranscriptEntry(
        speaker="Unknown",
        content="Synthetic.",
        startTime=0,
        endTime=1,
        source_speaker_id=uuid4(),
    )
    captured = []
    monkeypatch.setattr(
        recall,
        "pseudonymous_digest",
        lambda value, *, context: captured.append((value, context)) or "digest",
    )
    args = dict(
        doctor_id="doctor_synthetic",
        appointment_id="appointment_synthetic",
        patient_id="patient_synthetic",
        duration=1,
    )
    assert recall._content_digest(**args, entries=[entry]) == "digest"
    assert captured == [
        (
            json.dumps(
                {**args, "entries": [entry.legacy_data()]},
                sort_keys=True,
                separators=(",", ":"),
            ),
            "recall-transcript-content",
        )
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("with_receipt", [False, True])
@pytest.mark.parametrize("legacy_first", [False, True])
async def test_legacy_and_new_metadata_replay_one_row_without_rewriting(
    admission_case, monkeypatch, with_receipt, legacy_first
):
    f = admission_case
    await setup(f, monkeypatch)
    raw = two_voices(f.client.result)
    source = notes._source(raw, f.scope)
    known = source.entries
    old = [TranscriptEntry(**e.legacy_data()) for e in known]
    initial, incoming = (old, known) if legacy_first else (known, old)
    args = dict(
        encounter_id=f.case.appointment.id,
        caller_client_id=f.caller.workload_client_id,
        idempotency_key="speaker-replay-" + uuid4().hex,
        scribe_session_id=f.scope.session_id,
        payload_sha256=source.digest,
        duration=source.duration,
        dispatch_pipeline=False,
    )
    with Session(f.engine) as session:
        if with_receipt:
            first = ingest._perform(
                session=session, transcript_data=initial, **args
            ).transcript
        else:
            first = AIVisitTranscript(
                doctor_id=f.case.doctor.id,
                patient_id=f.case.patient.id,
                appointment_id=f.case.appointment.id,
                duration=source.duration,
                transcript_data=initial,
            )
            session.add(first)
            session.commit()
        original_id = first.id
        raw_before = (
            session.connection()
            .execute(
                select(AIVisitTranscript.__table__.c.transcript_data).where(
                    AIVisitTranscript.__table__.c.id == original_id
                )
            )
            .scalar_one()
        )
        for _ in range(2):
            result = ingest._perform(session=session, transcript_data=incoming, **args)
            assert (
                result.disposition == "replayed" and result.transcript.id == original_id
            )
        session.expire_all()
        persisted = session.get(AIVisitTranscript, original_id)
        assert persisted.transcript_data == initial
        session.refresh(persisted)
        assert persisted.transcript_data == initial
        assert not inspect(persisted).attrs.transcript_data.history.has_changes()
        assert (
            session.connection()
            .execute(
                select(AIVisitTranscript.__table__.c.transcript_data).where(
                    AIVisitTranscript.__table__.c.id == original_id
                )
            )
            .scalar_one()
            == raw_before
        )
    assert len(rows(f, AIVisitTranscript)) == 1 and not rows(f, AIVisitNote)


@pytest.mark.asyncio
@pytest.mark.parametrize("with_receipt", [False, True])
async def test_known_identity_swap_conflicts_before_dispatch(
    admission_case, monkeypatch, with_receipt
):
    f = admission_case
    await setup(f, monkeypatch)
    source = notes._source(two_voices(f.client.result), f.scope)
    args = dict(
        encounter_id=f.case.appointment.id,
        caller_client_id=f.caller.workload_client_id,
        idempotency_key="speaker-conflict-" + uuid4().hex,
        scribe_session_id=f.scope.session_id,
        payload_sha256=source.digest,
        duration=source.duration,
        dispatch_pipeline=False,
    )
    with Session(f.engine) as session:
        if with_receipt:
            ingest._perform(session=session, transcript_data=source.entries, **args)
        else:
            session.add(
                AIVisitTranscript(
                    doctor_id=f.case.doctor.id,
                    patient_id=f.case.patient.id,
                    appointment_id=f.case.appointment.id,
                    duration=source.duration,
                    transcript_data=source.entries,
                )
            )
            session.commit()
        swapped = [
            e.model_copy(update={"source_speaker_id": uuid4()}) for e in source.entries
        ]
        with pytest.raises(ingest.TranscriptConflictError):
            ingest._perform(
                session=session,
                transcript_data=swapped,
                **{**args, "dispatch_pipeline": True},
            )
    assert len(rows(f, AIVisitTranscript)) == 1 and not rows(f, AIVisitNote)


@pytest.mark.asyncio
async def test_capture_metadata_persists_but_does_not_change_prompts_or_note_calls(
    admission_case, monkeypatch
):
    f = admission_case
    kwargs = await setup(f, monkeypatch)
    f.client.result = two_voices(f.client.result)
    first = await notes.capture_note(**kwargs)
    assert first == await notes.capture_note(**kwargs)
    assert len(f.model.calls) == 1 and len(rows(f, AIVisitNote)) == 1
    stored = rows(f, AIVisitTranscript)[0]
    wire = stored.model_dump(mode="json")["transcript_data"]
    assert len({e["source_speaker_id"] for e in wire}) == 2
    assert {e["speaker"] for e in wire} == {"Unknown"}
    schema = DoctorNoteSchema(
        doctor_id=f.case.doctor.id,
        note_type="Synthetic",
        fields={"Subjective": "Reported history", "Plan": "Stated plan"},
    )
    old = stored.model_copy(
        update={
            "transcript_data": [
                TranscriptEntry(**e.legacy_data()) for e in stored.transcript_data
            ]
        }
    )
    for indexed in (False, True):
        args = dict(
            example_note=None,
            note_schema=schema,
            action_items="",
            doctor=f.case.doctor,
            source_indexes=indexed,
        )
        assert summarize.build_structured_prompt(
            stored, **args
        ) == summarize.build_structured_prompt(old, **args)
    assert all(
        str(e.source_speaker_id) not in f.model.calls[0][0]
        for e in stored.transcript_data
    )
    with Session(f.engine) as session:
        current = session.get(AIVisitTranscript, stored.id)
        current.transcript_data = [
            e.model_copy(update={"source_speaker_id": uuid4()})
            for e in current.transcript_data
        ]
        session.add(current)
        session.commit()
    with pytest.raises(notes.CaptureNoteConflict):
        await notes.capture_note(**kwargs)
    assert len(f.model.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("known_identity", [False, True])
async def test_matching_receipt_survives_real_retention_without_resurrection_or_dispatch(
    admission_case, monkeypatch, known_identity
):
    f = admission_case
    await setup(f, monkeypatch)
    source = notes._source(two_voices(f.client.result), f.scope)
    entries = (
        source.entries
        if known_identity
        else [TranscriptEntry(**e.legacy_data()) for e in source.entries]
    )
    args = dict(
        encounter_id=f.case.appointment.id,
        caller_client_id=f.caller.workload_client_id,
        idempotency_key="speaker-retention-" + uuid4().hex,
        scribe_session_id=f.scope.session_id,
        payload_sha256=source.digest,
        duration=source.duration,
        transcript_data=entries,
    )
    with Session(f.engine) as session:
        initial = ingest._perform(
            session=session, dispatch_pipeline=False, **args
        ).transcript
        identifier = initial.id
    monkeypatch.setattr(
        anonymize, "deidentify", lambda _content: "Deidentified synthetic text."
    )
    anonymize.perform(identifier)
    with Session(f.engine) as session:
        retained = session.get(AIVisitTranscript, identifier)
        deleted_at = retained.deleted_at
        assert deleted_at is not None and retained.transcript_data == []
        for _ in range(2):
            result = ingest._perform(session=session, **args)
            assert (
                result.disposition == "replayed" and result.transcript.id == identifier
            )
            assert result.transcript.deleted_at == deleted_at
            assert result.transcript.transcript_data == []
        deidentified = session.execute(select(AIVisitTranscriptNonPHI)).scalars().all()
        assert deidentified
        assert all(
            "source_speaker_id" not in entry.model_dump()
            for row in deidentified
            for entry in row.data
        )
    assert len(rows(f, AIVisitTranscript)) == 1 and not rows(f, AIVisitNote)


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy_cache", [False, True])
async def test_reviewed_source_cache_accepts_legacy_metadata_but_rejects_known_swap(
    admission_case, monkeypatch, legacy_cache
):
    f = admission_case
    kwargs = await configured_note_setup(f)
    first = await notes.capture_note(**kwargs)
    stored = rows(f, AIVisitTranscript)[0]
    expected_id = UUID(f.view["working"]["segments"][0]["speaker_id"])
    assert stored.transcript_data[0].source_speaker_id == expected_id
    with Session(f.engine) as session:
        row = session.get(AIVisitTranscript, stored.id)
        row.transcript_data = [
            e.model_copy(
                update={"source_speaker_id": None if legacy_cache else uuid4()}
            )
            for e in row.transcript_data
        ]
        session.add(row)
        session.commit()
    if legacy_cache:
        assert await notes.capture_note(**kwargs) == first
        assert (
            rows(f, AIVisitTranscript)[0].transcript_data[0].source_speaker_id is None
        )
    else:
        with pytest.raises(notes.CaptureNoteConflict):
            await notes.capture_note(**kwargs)
    assert len(f.model.calls) == 1 and len(rows(f, AIVisitNote)) == 1
