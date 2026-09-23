"""Synthetic working revisions, real Core authority/transcript/note persistence."""

import asyncio
import copy
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4, uuid5

import pytest
from pydantic import ValidationError

from app.jobs import summarize
from app.services import authority
from app.services import scribe_capture_gateway as gateway
from app.services import scribe_capture_note_edits as edits
from app.services import scribe_capture_notes as notes
from app.services import scribe_capture_protocol as protocol
from app.services.scribe_capture_working_contract import (
    CaptureWorkingTranscript,
    SegmentCorrection,
    canonical_hash,
)
from app.services.scribe_workflow import ScribeWorkflowConflictError

from activemodel.session_manager import _session_context
from app.models.ai_visit_note import AIVisitNote
from app.models.ai_visit_transcript import AIVisitTranscript
from app.models.authority import AuthorityActorExternalIdentity
from app.models.scribe_capture_source_version import ScribeCaptureSourceVersion
from app.models.scribe_transcript_delivery import ScribeTranscriptDelivery
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlmodel import Session, select

from tests.authority_kernel.scribe_capture_notes_test import (
    FakeModel,
    admission_case,
    capture_engine,
    rows,
)
from tests.authority_kernel.scribe_capture_notes_test import (
    setup as setup_note,
)

__all__ = ["admission_case", "capture_engine"]


@pytest.mark.parametrize(
    "content", ["", "  \n", "private_sentinel\0", "private_sentinel\ud800"]
)
def test_invalid_correction_text_is_rejected_before_transport(content):
    with pytest.raises(ValidationError) as error:
        SegmentCorrection(
            source_segment_id=uuid4(), content=content, speaker_role="clinician"
        )
    assert "private_sentinel" not in str(error.value)


def working_receipt(raw, practitioner, version=1, content=None):
    locale = {"en": "en-US", "en-us": "en-US", "es-us": "es-US"}.get(
        raw.language_code.casefold(), "unknown"
    )
    source = {
        "schema_version": "1.0",
        "source_transcript_version": version,
        "language_mode": "en-US+es-US" if locale == "es-US" else "en-US",
        "transcription_adapter_id": "captured_raw_transcript_v1",
        "transcription_model_profile_id": "persisted_capture",
        "normalization_profile_id": "capture_corrections_v1",
        "duration_ms": max(s.end_ms for s in raw.segments),
        "segments": [
            {
                "source_segment_id": str(uuid5(raw.id, f"segment:{i}")),
                "source_segment_version": version,
                "source_locale": locale,
                "speaker_id": str(uuid5(raw.id, f"speaker:{segment.speaker_id}")),
                "speaker_role": segment.speaker_role,
                "content": content
                if i == 0 and content is not None
                else segment.content,
                "start_ms": segment.start_ms,
                "end_ms": segment.end_ms,
                "locale_confidence": None,
            }
            for i, segment in enumerate(raw.segments)
        ],
    }
    source["segments"].sort(
        key=lambda s: (s["start_ms"], s["end_ms"], s["source_segment_id"])
    )
    blockers = []
    last_end = 0
    projected = []
    for segment in source["segments"]:
        if segment["start_ms"] < last_end:
            blockers.append(
                {
                    "source_segment_id": segment["source_segment_id"],
                    "code": "source_overlap",
                }
            )
        last_end = max(last_end, segment["end_ms"])
        if locale != "en-US":
            blockers.append(
                {
                    "source_segment_id": segment["source_segment_id"],
                    "code": "translation_missing"
                    if locale == "es-US"
                    else "source_locale_unknown",
                }
            )
        projected.append(
            {
                **{
                    k: v
                    for k, v in segment.items()
                    if k not in {"content", "locale_confidence"}
                },
                "resolution": "english_source" if locale == "en-US" else "unresolved",
                "content": segment["content"] if locale == "en-US" else None,
                "translation_id": None,
                "translation_version": None,
                "translation_review_id": None,
            }
        )
    raw_hash = canonical_hash(raw)
    return CaptureWorkingTranscript.model_validate(
        {
            "receipt_id": str(uuid5(raw.id, "capture-working-base-v1"))
            if version == 1
            else str(uuid4()),
            "session_id": str(raw.session_id),
            "raw_transcript_id": str(raw.id),
            "raw_transcript_sha256": raw_hash,
            "previous_working_sha256": None if version == 1 else "a" * 64,
            "working_sha256": canonical_hash(
                {"raw_transcript_sha256": raw_hash, "working_source": source}
            ),
            "working_source": source,
            "working_transcript": {
                "schema_version": "1.0",
                "working_transcript_id": str(
                    uuid5(raw.id, "capture-working-transcript-v1")
                ),
                "working_transcript_version": version,
                "source_transcript_version": version,
                "segments": projected,
                "blockers": blockers,
                "note_ready": not blockers,
            },
            "corrected_by_practitioner_id": None if version == 1 else practitioner,
            "created_at": raw.created_at if version == 1 else datetime.now(UTC),
        }
    )


async def setup_working(f, monkeypatch):
    kwargs = await setup_note(f, monkeypatch)
    f.raw = f.client.result
    f.working = working_receipt(f.raw, f.scope.session_request.practitioner_id)
    f.caller = replace(
        f.caller,
        capture_settings=replace(
            f.caller.capture_settings, working_transcripts_enabled=True
        ),
    )
    kwargs["caller"] = f.caller
    f.working_calls = []
    f.after_working = lambda _phase: None
    f.corrections = {}
    old_operation = f.client.capture_operation

    async def raw_read(tenant, session):
        assert tenant == f.scope.tenant_id and session == f.scope.session_id
        result = f.raw
        f.after_working("raw")
        return result

    async def operation(
        tenant, session, payload, *, idempotency_key, practitioner_id=None
    ):
        if payload.kind not in {"working_transcript", "correct_transcript"}:
            return await old_operation(
                tenant, session, payload, idempotency_key=idempotency_key
            )
        assert practitioner_id == f.scope.session_request.practitioner_id
        f.working_calls.append(payload.kind)
        if payload.kind == "correct_transcript":
            previous = f.corrections.get(idempotency_key)
            if previous:
                if previous[0] != payload:
                    raise ScribeWorkflowConflictError("scribe_conflict")
                result = previous[1]
            else:
                if payload.payload.expected_working_sha256 != f.working.working_sha256:
                    raise ScribeWorkflowConflictError("scribe_conflict")
                edit = payload.payload.corrections[0]
                changed = working_receipt(
                    f.raw,
                    practitioner_id,
                    f.working.working_source.source_transcript_version + 1,
                    edit.content,
                )
                changed = changed.model_copy(
                    update={"previous_working_sha256": f.working.working_sha256}
                )
                f.working = changed
                f.corrections[idempotency_key] = (payload, changed)
                result = changed
        else:
            result = f.working
        f.after_working("operation")
        return result

    f.client.get_capture_transcript = raw_read
    f.client.capture_operation = operation
    return kwargs


def correction(f, text="Invented corrected visit statement."):
    return protocol.CorrectTranscript.model_validate(
        {
            "kind": "correct_transcript",
            "payload": {
                "raw_transcript_id": str(f.raw.id),
                "raw_transcript_sha256": canonical_hash(f.raw),
                "expected_working_version": f.working.working_source.source_transcript_version,
                "expected_working_sha256": f.working.working_sha256,
                "corrections": [
                    {
                        "source_segment_id": str(
                            f.working.working_source.segments[0].source_segment_id
                        ),
                        "content": text,
                        "speaker_role": "clinician",
                    }
                ],
            },
        }
    )


async def operate(f, operation, key="working-core-correction"):
    return await gateway.execute_capture_operation(
        caller=f.caller,
        binding_ref=f.binding.binding_ref,
        workspace_id=f.binding.workspace_id,
        operation=operation,
        client_request_key=key,
        client=f.client,
    )


@pytest.mark.asyncio
async def test_correction_replaces_note_input_preserves_history_and_invalidates_old_edit(
    admission_case, monkeypatch
):
    f = admission_case
    kwargs = await setup_working(f, monkeypatch)
    first = await notes.capture_note(**kwargs)
    assert first.source_sha256 == notes._source(f.raw, f.scope).digest
    raw_before = f.raw.model_dump_json()
    assert first.note_ref is not None
    assert first.note_sha256 is not None
    old_edit = edits.NoteEdit(
        client_key=uuid4(),
        expected_note_ref=first.note_ref,
        expected_note_sha256=first.note_sha256,
        expected_source_sha256=first.source_sha256,
        fields={"CC": "Invented", "Plan": "Invented old edit."},
    )
    operation = correction(f)
    corrected = await operate(f, operation)
    assert await operate(f, operation) == corrected
    absent = await notes.capture_note(**{**kwargs, "prepare": False})
    assert absent.status == "not_prepared" and absent.note_ref is absent.fields is None
    assert absent.source_sha256 != first.source_sha256
    with pytest.raises(notes.CaptureNoteConflict):
        await edits.save_capture_note(
            **{k: v for k, v in kwargs.items() if k not in {"prepare", "model"}},
            edit=old_edit,
        )
    f.model.output = '{"CC":"Corrected","Plan":"Invented corrected plan."}'
    second = await notes.capture_note(**kwargs)
    assert (
        second.note_ref != first.note_ref
        and second.transcript_ref != first.transcript_ref
    )
    assert "Invented corrected visit statement." in f.model.calls[-1][0]
    assert "Invented visit for a recorder test." not in f.model.calls[-1][0]
    assert f.raw.model_dump_json() == raw_before
    assert len(rows(f, AIVisitTranscript)) == len(rows(f, AIVisitNote)) == 2
    assert len(rows(f, ScribeTranscriptDelivery)) == 1
    historical = next(
        row for row in rows(f, AIVisitNote) if str(row.id) == first.note_ref
    )
    assert historical.superseded_at is not None and historical.note == first.fields
    assert await notes.capture_note(**kwargs) == second
    assert len(f.model.calls) == 4
    await operate(
        f, correction(f, "Invented third source version."), key="working-core-third"
    )
    third = await notes.capture_note(**kwargs)
    assert third.source_sha256 not in {first.source_sha256, second.source_sha256}
    assert len(rows(f, AIVisitTranscript)) == len(rows(f, AIVisitNote)) == 3
    # Retrying a prior Scribe correction recovers its old receipt without making
    # Core expose that old note as current.
    assert await operate(f, operation) == corrected
    assert await notes.capture_note(**{**kwargs, "prepare": False}) == third


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", [1, 2])
async def test_correction_during_model_work_cannot_publish_stale_draft(
    admission_case, monkeypatch, phase
):
    f = admission_case
    kwargs = await setup_working(f, monkeypatch)

    def change(count):
        if count == phase:
            f.working = working_receipt(
                f.raw,
                f.scope.session_request.practitioner_id,
                2,
                "Invented later source.",
            )

    f.model.after = change
    with pytest.raises(notes.CaptureNoteConflict):
        await notes.capture_note(**kwargs)
    assert not rows(f, AIVisitNote)
    f.model.after = None
    result = await notes.capture_note(**kwargs)
    assert result.status == "draft" and len(rows(f, AIVisitTranscript)) == 2


@pytest.mark.asyncio
async def test_first_preparation_can_start_from_later_revision_and_flag_off_cannot_revive_raw(
    admission_case, monkeypatch
):
    f = admission_case
    kwargs = await setup_working(f, monkeypatch)
    f.working = working_receipt(
        f.raw, f.scope.session_request.practitioner_id, 4, "Invented fourth snapshot."
    )
    result = await notes.capture_note(**kwargs)
    assert result.status == "draft" and len(rows(f, AIVisitTranscript)) == 2
    disabled = replace(
        f.caller,
        capture_settings=replace(
            f.caller.capture_settings, working_transcripts_enabled=False
        ),
    )
    with pytest.raises(notes.CaptureNoteDisabled):
        await notes.capture_note(**{**kwargs, "caller": disabled, "prepare": False})
    f.working = working_receipt(f.raw, f.scope.session_request.practitioner_id)
    with pytest.raises(notes.CaptureNoteConflict):
        await notes.capture_note(**kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize("language", ["es-US", "fr-FR"])
async def test_unresolved_language_blocks_notes_without_import(
    admission_case, monkeypatch, language
):
    f = admission_case
    kwargs = await setup_working(f, monkeypatch)
    f.raw = f.raw.model_copy(update={"language_code": language})
    f.client.result = f.raw
    f.working = working_receipt(f.raw, f.scope.session_request.practitioner_id)
    with pytest.raises(gateway.CaptureOperationUnavailable):
        await notes.capture_note(**kwargs)
    assert not f.model.calls and not rows(f, AIVisitTranscript)


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["raw", "operation"])
async def test_working_gateway_rechecks_current_human_authority(
    admission_case, monkeypatch, phase
):
    f = admission_case
    await setup_working(f, monkeypatch)

    def revoke(current):
        if current == phase:
            with Session(f.engine) as session:
                identity = session.get(
                    AuthorityActorExternalIdentity, f.case.identity.id
                )
                assert identity is not None
                identity.revoked_at = datetime.now(UTC)
                session.add(identity)
                session.commit()

    f.after_working = revoke
    with pytest.raises(authority.AuthorityKernelError):
        await operate(f, correction(f))
    assert f.working.working_source.source_transcript_version == (
        1 if phase == "raw" else 2
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("tamper", ["hash", "source_id", "origin", "blocker", "text"])
async def test_untrusted_working_receipt_is_revalidated(
    admission_case, monkeypatch, tamper
):
    f = admission_case
    kwargs = await setup_working(f, monkeypatch)
    body = copy.deepcopy(f.working.model_dump(mode="json"))
    if tamper == "hash":
        body["working_sha256"] = "0" * 64
    elif tamper == "source_id":
        body["raw_transcript_id"] = str(uuid4())
    elif tamper == "origin":
        for segment in [
            body["working_source"]["segments"][0],
            body["working_transcript"]["segments"][0],
        ]:
            segment["speaker_id"] = str(uuid4())
    elif tamper == "blocker":
        body["working_transcript"]["note_ready"] = False
    else:
        body["working_transcript"]["segments"][0]["content"] = "Invented substitute."

    # Construct bypasses Pydantic deliberately, as an injected port may do.
    class Unchecked:
        def model_dump(self, **_):
            return body

    f.working = Unchecked()
    with pytest.raises(gateway.CaptureReceiptInvalid):
        await notes.capture_note(**kwargs)
    assert not f.model.calls


@pytest.mark.asyncio
async def test_legacy_background_summary_cannot_merge_corrected_full_snapshots(
    admission_case, monkeypatch
):
    f = admission_case
    kwargs = await setup_working(f, monkeypatch)
    await operate(f, correction(f))
    current = await notes.capture_note(**kwargs)
    monkeypatch.setattr(
        summarize,
        "build_action_item_prompt",
        lambda *_: pytest.fail("legacy merge reached"),
    )
    raw = rows(f, ScribeTranscriptDelivery)[0]
    summarize.perform(str(raw.transcript_id), f.schema_ref)
    assert await notes.capture_note(**{**kwargs, "prepare": False}) == current
    with Session(f.engine) as session:
        assert (
            session.exec(
                select(ScribeCaptureSourceVersion).where(
                    ScribeCaptureSourceVersion.binding_id == f.binding.binding_ref
                )
            )
            .one()
            .working_version
            == 2
        )


@pytest.mark.asyncio
async def test_concurrent_preparation_retains_one_mapping_and_one_current_note(
    admission_case, monkeypatch
):
    f = admission_case
    kwargs = await setup_working(f, monkeypatch)
    await operate(f, correction(f))
    entered = 0
    both = asyncio.Event()

    class ConcurrentModel(FakeModel):
        async def complete(self, prompt, *, json_output):
            nonlocal entered
            if json_output:
                entered += 1
                if entered == 2:
                    both.set()
                await asyncio.wait_for(both.wait(), 5)
            return await super().complete(prompt, json_output=json_output)

    async def run():
        with Session(f.engine, expire_on_commit=False) as session:
            context = _session_context.set(session)
            try:
                return await notes.capture_note(
                    **{**kwargs, "model": ConcurrentModel()}
                )
            finally:
                _session_context.reset(context)

    first, second = await asyncio.gather(run(), run())
    assert first == second
    assert len(rows(f, AIVisitNote)) == 1 and len(rows(f, AIVisitTranscript)) == 2
    with Session(f.engine) as session:
        assert (
            len(
                session.exec(
                    select(ScribeCaptureSourceVersion).where(
                        ScribeCaptureSourceVersion.binding_id == f.binding.binding_ref
                    )
                ).all()
            )
            == 1
        )


@pytest.mark.asyncio
async def test_failed_source_journal_rolls_back_raw_and_replacement(
    admission_case, monkeypatch
):
    f = admission_case
    kwargs = await setup_working(f, monkeypatch)
    await operate(f, correction(f))
    original = Session.add

    def fail(self, value, *args, **kw):
        if isinstance(value, ScribeCaptureSourceVersion):
            raise IntegrityError("synthetic source journal failure", None, Exception())
        return original(self, value, *args, **kw)

    monkeypatch.setattr(Session, "add", fail)
    with pytest.raises(IntegrityError):
        await notes.capture_note(**kwargs)
    assert not rows(f, AIVisitTranscript) and not rows(f, AIVisitNote)
    assert not rows(f, ScribeTranscriptDelivery)
    assert not f.model.calls
    monkeypatch.setattr(Session, "add", original)
    assert (await notes.capture_note(**kwargs)).status == "draft"
    assert len(rows(f, AIVisitTranscript)) == 2


@pytest.mark.asyncio
async def test_source_journal_is_immutable_even_with_database_write_access(
    admission_case, monkeypatch
):
    f = admission_case
    kwargs = await setup_working(f, monkeypatch)
    await operate(f, correction(f))
    current = await notes.capture_note(**kwargs)
    for command in (
        "UPDATE scribe_capture_source_version SET working_version=working_version",
        "DELETE FROM scribe_capture_source_version",
    ):
        with (
            Session(f.engine) as session,
            pytest.raises(DBAPIError, match="history is immutable"),
        ):
            session.execute(
                text(command + " WHERE session_id=:session"),
                {"session": f.scope.session_id},
            )
            session.commit()
    assert await notes.capture_note(**{**kwargs, "prepare": False}) == current


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["session", "origin", "version"])
async def test_source_journal_direct_insert_cannot_change_scope_or_origin(
    admission_case, monkeypatch, mutation
):
    f = admission_case
    kwargs = await setup_working(f, monkeypatch)
    await operate(f, correction(f))
    await notes.capture_note(**kwargs)
    with Session(f.engine) as session:
        first = session.exec(
            select(ScribeCaptureSourceVersion).where(
                ScribeCaptureSourceVersion.binding_id == f.binding.binding_ref
            )
        ).one()
        values = first.model_dump(exclude={"id", "created_at", "updated_at"})
    values["working_version"] = 3
    if mutation == "session":
        values["session_id"] = uuid4()
        message = "admission unavailable"
    else:
        values["raw_source_sha256"] = (
            "0" * 64 if mutation == "origin" else values["raw_source_sha256"]
        )
        values["working_version"] = 2 if mutation == "version" else 3
        message = "origin or version changed"
    with Session(f.engine) as session, pytest.raises(DBAPIError, match=message):
        session.add(ScribeCaptureSourceVersion(**values))
        session.commit()
