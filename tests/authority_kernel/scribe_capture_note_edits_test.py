"""Versioned human edits in the explicitly scoped disposable Core database."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.services import authority
from app.services import scribe_capture_note_edits as edits
from app.services import scribe_capture_notes as notes

from activemodel.session_manager import _session_context
from app.models.ai_visit_note import AIVisitNote
from app.models.authority import AuthorityActorExternalIdentity
from app.models.doctor_note_schema import DoctorNoteSchema
from app.models.scribe_capture_note_edit import ScribeCaptureNoteEdit
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from tests.authority_kernel.scribe_capture_notes_test import (
    admission_case as _admission_case,
)
from tests.authority_kernel.scribe_capture_notes_test import (
    capture_engine as _capture_engine,
)
from tests.authority_kernel.scribe_capture_notes_test import (
    rows,
    setup,
)

admission_case = _admission_case
capture_engine = _capture_engine


async def editable(f, monkeypatch):
    kwargs = await setup(f, monkeypatch)
    draft = await notes.capture_note(**kwargs)
    request = {
        key: value for key, value in kwargs.items() if key not in ("prepare", "model")
    }
    assert draft.note_ref is not None
    assert draft.note_sha256 is not None
    request["edit"] = edits.NoteEdit(
        client_key=uuid4(),
        expected_note_ref=draft.note_ref,
        expected_note_sha256=draft.note_sha256,
        expected_source_sha256=draft.source_sha256,
        fields={"Plan": "Clinician's synthetic correction.", "CC": "Sleep"},
    )
    return draft, request


@pytest.mark.asyncio
async def test_edit_retains_prior_version_and_recovers_lost_response(
    admission_case, monkeypatch
):
    f = admission_case
    first, request = await editable(f, monkeypatch)
    saved = await edits.save_capture_note(**request)
    assert saved == await edits.save_capture_note(**request)
    assert saved.note_ref != first.note_ref and saved.note_sha256 != first.note_sha256
    assert saved.source_sha256 == first.source_sha256 and saved.field_order == (
        "CC",
        "Plan",
    )
    assert saved.fields is not None
    assert (
        saved.fields["Plan"] == "Clinician's synthetic correction."
        and saved.review_required
    )
    versions = rows(f, AIVisitNote)
    prior = next(note for note in versions if str(note.id) == first.note_ref)
    assert len(versions) == 2 and prior.note == first.fields
    assert prior.supersession_reason == "clinician_capture_edit"
    with Session(f.engine) as session:
        journal = session.exec(
            select(ScribeCaptureNoteEdit).where(
                ScribeCaptureNoteEdit.client_key == request["edit"].client_key
            )
        ).one()
        assert (
            str(journal.base_note_id) == first.note_ref
            and str(journal.result_note_id) == saved.note_ref
        )
    read = {key: value for key, value in request.items() if key != "edit"}
    assert await notes.capture_note(**read, prepare=False) == saved
    assert len(f.model.calls) == 1  # Saving edits never invokes the model.


@pytest.mark.asyncio
@pytest.mark.parametrize("same_request", [True, False])
async def test_concurrent_edits_publish_one_version(
    admission_case, monkeypatch, same_request
):
    f = admission_case
    _, request = await editable(f, monkeypatch)
    second = (
        request
        if same_request
        else {
            **request,
            "edit": request["edit"].model_copy(
                update={
                    "client_key": uuid4(),
                    "fields": {"CC": "Sleep", "Plan": "Competing synthetic edit"},
                }
            ),
        }
    )

    def run(arguments):
        with Session(f.engine, expire_on_commit=False) as session:
            context = _session_context.set(session)
            try:
                return asyncio.run(edits.save_capture_note(**arguments))
            except notes.CaptureNoteConflict:
                return None
            finally:
                _session_context.reset(context)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, [request, second]))
    assert len(rows(f, AIVisitNote)) == 2
    if same_request:
        assert results[0] == results[1] and results[0] is not None
    else:
        assert sum(result is None for result in results) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault",
    [
        "key_payload",
        "new_key_old_base",
        "source",
        "note_id",
        "note_hash",
        "fields",
        "in_place_edit",
    ],
)
async def test_stale_or_changed_edit_cannot_replace_current(
    admission_case, monkeypatch, fault
):
    f = admission_case
    first, request = await editable(f, monkeypatch)
    edit = request["edit"]
    if fault in ("key_payload", "new_key_old_base"):
        await edits.save_capture_note(**request)
        edit = edit.model_copy(
            update={"fields": {"CC": "Changed", "Plan": "Changed"}}
            if fault == "key_payload"
            else {"client_key": uuid4()}
        )
    elif fault == "source":
        edit = edit.model_copy(update={"expected_source_sha256": "0" * 64})
    elif fault == "note_hash":
        edit = edit.model_copy(update={"expected_note_sha256": "0" * 64})
    elif fault == "note_id":
        edit = edit.model_copy(
            update={"expected_note_ref": "visit_note_00000000000000000000000000"}
        )
    elif fault == "fields":
        edit = edit.model_copy(update={"fields": {"Unexpected": "Private sentinel"}})
    else:
        with Session(f.engine) as session:
            row = session.get(AIVisitNote, first.note_ref)
            assert row is not None
            row.note = {"CC": "Sleep", "Plan": "Changed elsewhere"}
            session.add(row)
            session.commit()
    count = len(rows(f, AIVisitNote))
    with pytest.raises(notes.CaptureNoteConflict):
        await edits.save_capture_note(**{**request, "edit": edit})
    assert len(rows(f, AIVisitNote)) == count


@pytest.mark.asyncio
async def test_template_rename_preserves_saved_note_edit_structure(
    admission_case, monkeypatch
):
    f = admission_case
    first, request = await editable(f, monkeypatch)
    with Session(f.engine) as session:
        schema = session.get(DoctorNoteSchema, f.schema_ref)
        assert schema is not None
        schema.fields = {"Concern": "Reason for visit", "Next steps": "Discussed plan"}
        session.add(schema)
        session.commit()
    saved = await edits.save_capture_note(**request)
    assert saved.field_order == first.field_order == ("CC", "Plan")
    assert saved.fields == {"CC": "Sleep", "Plan": "Clinician's synthetic correction."}
    assert await edits.save_capture_note(**request) == saved
    versions = rows(f, AIVisitNote)
    assert (
        next(note for note in versions if str(note.id) == first.note_ref).note
        == first.fields
    )
    bad = request["edit"].model_copy(
        update={
            "client_key": uuid4(),
            "expected_note_ref": saved.note_ref,
            "expected_note_sha256": saved.note_sha256,
            "fields": {"Concern": "Sleep", "Next steps": "Synthetic edit"},
        }
    )
    with pytest.raises(notes.CaptureNoteConflict):
        await edits.save_capture_note(**{**request, "edit": bad})
    assert len(rows(f, AIVisitNote)) == 2


@pytest.mark.asyncio
async def test_replay_cannot_restore_an_older_edit(admission_case, monkeypatch):
    f = admission_case
    _, request = await editable(f, monkeypatch)
    saved = await edits.save_capture_note(**request)
    assert saved.note_ref is not None
    assert saved.note_sha256 is not None
    second_request = {
        **request,
        "edit": edits.NoteEdit(
            client_key=uuid4(),
            expected_note_ref=saved.note_ref,
            expected_note_sha256=saved.note_sha256,
            expected_source_sha256=saved.source_sha256,
            fields={"CC": "Sleep", "Plan": "Second deliberate correction"},
        ),
    }
    current = await edits.save_capture_note(**second_request)
    with pytest.raises(notes.CaptureNoteConflict):
        await edits.save_capture_note(**request)
    assert await edits.save_capture_note(**second_request) == current


@pytest.mark.asyncio
async def test_revocation_during_source_read_prevents_save(admission_case, monkeypatch):
    f = admission_case
    _, request = await editable(f, monkeypatch)
    original = f.client.capture_operation

    async def revoked(*args, **kwargs):
        result = await original(*args, **kwargs)
        with Session(f.engine) as session:
            identity = session.get(AuthorityActorExternalIdentity, f.case.identity.id)
            assert identity is not None
            identity.revoked_at = datetime.now(UTC)
            session.add(identity)
            session.commit()
        return result

    f.client.capture_operation = revoked
    with pytest.raises(authority.AuthorityKernelError):
        await edits.save_capture_note(**request)
    assert len(rows(f, AIVisitNote)) == 1


@pytest.mark.asyncio
async def test_failed_journal_insert_rolls_back_version_swap(
    admission_case, monkeypatch
):
    f = admission_case
    _, request = await editable(f, monkeypatch)
    original = Session.add

    def fail(self, value, *args, **kwargs):
        if isinstance(value, ScribeCaptureNoteEdit):
            raise IntegrityError("synthetic journal failure", None, Exception())
        return original(self, value, *args, **kwargs)

    monkeypatch.setattr(Session, "add", fail)
    with pytest.raises(IntegrityError):
        await edits.save_capture_note(**request)
    versions = rows(f, AIVisitNote)
    assert len(versions) == 1 and versions[0].superseded_at is None


@pytest.mark.asyncio
async def test_edit_journal_cannot_be_rewritten(admission_case, monkeypatch):
    f = admission_case
    _, request = await editable(f, monkeypatch)
    await edits.save_capture_note(**request)
    from sqlalchemy.exc import DBAPIError

    for command in (
        "UPDATE scribe_capture_note_edit SET request_sha256 = request_sha256",
        "DELETE FROM scribe_capture_note_edit",
    ):
        with Session(f.engine) as session, pytest.raises(DBAPIError):
            session.execute(
                text(command + " WHERE client_key = :key"),
                {"key": request["edit"].client_key},
            )
            session.commit()


@pytest.mark.parametrize(
    "fields",
    [
        {},
        {"CC": "\0"},
        {"CC": "\ud800"},
        {"": "text"},
        {"CC": "x" * 262145},
        {"CC": {"private": "nested"}},
    ],
)
def test_edit_shape_is_bounded_and_errors_hide_text(fields):
    with pytest.raises(ValidationError) as error:
        edits.NoteEdit(
            client_key=uuid4(),
            expected_note_ref="visit_note_00000000000000000000000000",
            expected_note_sha256="a" * 64,
            expected_source_sha256="b" * 64,
            fields=fields,
        )
    assert "nested" not in str(error.value)
