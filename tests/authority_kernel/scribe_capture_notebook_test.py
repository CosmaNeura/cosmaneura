"""Saved notebook provenance through the real Core draft/version transaction."""

import asyncio
import hashlib
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.services import scribe_capture_note_edits as edits
from app.services import scribe_capture_notes as notes
from app.services.scribe_capture_notebook import ScratchNotes

from activemodel.session_manager import _session_context
from app.models.ai_visit_note import AIVisitNote
from app.models.ai_visit_transcript import AIVisitTranscript
from app.models.scribe_capture_note_context import ScribeCaptureNoteContext
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlmodel import Session, select

from tests.authority_kernel.scribe_capture_notes_test import FakeModel, rows, setup
from tests.authority_kernel.scribe_capture_notes_test import (
    admission_case as _admission_case,
)
from tests.authority_kernel.scribe_capture_notes_test import (
    capture_engine as _capture_engine,
)

admission_case = _admission_case
capture_engine = _capture_engine


def notebook(workspace_id, **updates):
    content = updates.pop(
        "content", "Invented clinician jotting: discuss sleep diary at follow-up."
    )
    return ScratchNotes(
        workspace_id=workspace_id,
        entry_id=uuid4(),
        revision=2,
        content=content,
        content_sha256=hashlib.sha256(content.encode()).hexdigest(),
        **updates,
    )


@pytest.mark.asyncio
async def test_notebook_is_distinct_saved_and_replayed_then_new_revision_replaces_note(
    admission_case, monkeypatch
):
    f = admission_case
    kwargs = await setup(f, monkeypatch)
    scratch = notebook(f.binding.workspace_id)
    original_text = f.client.result.segments[0].content
    first = await notes.capture_note(**kwargs, scratch_notes=scratch)
    assert first.notebook_source == scratch.reference()
    assert len(f.model.calls) == 2
    assert all(
        scratch.content in prompt and "not words recorded" in prompt
        for prompt, _ in f.model.calls
    )
    assert rows(f, AIVisitTranscript)[0].transcript_data[0].content == original_text
    assert await notes.capture_note(**kwargs, scratch_notes=scratch) == first
    assert await notes.capture_note(**{**kwargs, "prepare": False}) == first
    assert len(f.model.calls) == 2
    newer = notebook(
        f.binding.workspace_id,
        content="Invented update: clinician clarified a two-week follow-up.",
    )
    assert (
        await notes.capture_note(**{**kwargs, "prepare": False}, scratch_notes=newer)
    ).status == "not_prepared"
    second = await notes.capture_note(**kwargs, scratch_notes=newer)
    assert (
        second.note_ref != first.note_ref
        and second.transcript_ref == first.transcript_ref
    )
    assert second.notebook_source == newer.reference()
    assert len(rows(f, AIVisitNote)) == 2 and len(rows(f, AIVisitTranscript)) == 1
    with Session(f.engine) as session:
        prior = session.get(AIVisitNote, first.note_ref)
        assert prior is not None
        assert prior.superseded_at and prior.note == first.fields
        record = session.exec(
            select(ScribeCaptureNoteContext).where(
                ScribeCaptureNoteContext.note_id == second.note_ref
            )
        ).one()
        assert ScratchNotes.model_validate(record.scratch_notes) == newer
        with pytest.raises(DBAPIError):
            session.execute(
                text(
                    "UPDATE scribe_capture_note_context SET scratch_notes='{}' WHERE id=:id"
                ),
                {"id": str(record.id)},
            )


@pytest.mark.asyncio
async def test_explicit_edit_inherits_exact_notebook_provenance(
    admission_case, monkeypatch
):
    f = admission_case
    kwargs = await setup(f, monkeypatch)
    scratch = notebook(f.binding.workspace_id)
    first = await notes.capture_note(**kwargs, scratch_notes=scratch)
    assert first.note_ref is not None
    assert first.note_sha256 is not None
    edit = edits.NoteEdit(
        client_key=uuid4(),
        expected_note_ref=first.note_ref,
        expected_note_sha256=first.note_sha256,
        expected_source_sha256=first.source_sha256,
        fields={
            "CC": "Human edited invented complaint",
            "Plan": "Human edited invented plan",
        },
    )
    arguments = {
        key: value for key, value in kwargs.items() if key not in {"prepare", "model"}
    }
    changed = await edits.save_capture_note(**arguments, edit=edit)
    assert changed.notebook_source == first.notebook_source
    assert changed.note_ref != first.note_ref
    assert await edits.save_capture_note(**arguments, edit=edit) == changed


@pytest.mark.asyncio
async def test_provenance_failure_cannot_publish_a_note_without_sources(
    admission_case, monkeypatch
):
    f = admission_case
    kwargs = await setup(f, monkeypatch)
    scratch = notebook(f.binding.workspace_id)

    def fail(*_):
        raise notes.CaptureNoteUnconfirmed

    monkeypatch.setattr(notes, "_save_notebook_context", fail)
    with pytest.raises(notes.CaptureNoteUnconfirmed):
        await notes.capture_note(**kwargs, scratch_notes=scratch)
    assert rows(f, AIVisitNote) == []


@pytest.mark.asyncio
async def test_foreign_notebook_rejected_before_model_or_import(
    admission_case, monkeypatch
):
    f = admission_case
    kwargs = await setup(f, monkeypatch)
    with pytest.raises(notes.CaptureNoteConflict):
        await notes.capture_note(**kwargs, scratch_notes=notebook(uuid4()))
    assert f.model.calls == [] and rows(f, AIVisitTranscript) == []


@pytest.mark.parametrize(
    "updates",
    [
        {"content_sha256": "0" * 64},
        {"revision": True},
        {"revision": 501},
        {"grant_authority": True},
    ],
)
def test_notebook_contract_rejects_modified_text_and_authority(updates):
    source = notebook(uuid4()).model_dump()
    with pytest.raises(ValidationError):
        ScratchNotes.model_validate({**source, **updates})


@pytest.mark.asyncio
async def test_concurrent_notebook_preparation_publishes_one_note_and_source(
    admission_case, monkeypatch
):
    f = admission_case
    kwargs = await setup(f, monkeypatch)
    scratch = notebook(f.binding.workspace_id)
    entered, both = 0, asyncio.Event()

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
                    **{**kwargs, "model": ConcurrentModel()}, scratch_notes=scratch
                )
            finally:
                _session_context.reset(context)

    first, second = await asyncio.gather(run(), run())
    assert first == second and first.notebook_source == scratch.reference()
    assert len(rows(f, AIVisitNote)) == 1
    with Session(f.engine) as session:
        assert (
            len(
                session.exec(
                    select(ScribeCaptureNoteContext).where(
                        ScribeCaptureNoteContext.note_id == first.note_ref
                    )
                ).all()
            )
            == 1
        )
