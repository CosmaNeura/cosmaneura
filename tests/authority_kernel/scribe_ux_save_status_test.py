"""Exact read-only save checks cannot fence late publication or replace later notes."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.services import authority
from app.services import scribe_capture_note_edits as edits
from app.services import scribe_capture_notes as notes

from app.models.ai_visit_note import AIVisitNote
from app.models.authority import AuthorityActorExternalIdentity
from app.models.scribe_capture_note_edit import (
    ScribeCaptureNoteEdit,
    ScribeCaptureNoteSaveFence,
)
from sqlmodel import Session

from tests.authority_kernel.scribe_capture_note_edits_test import (
    admission_case as _admission_case,
)
from tests.authority_kernel.scribe_capture_note_edits_test import (
    capture_engine as _capture_engine,
)
from tests.authority_kernel.scribe_capture_note_edits_test import editable, rows
from tests.authority_kernel.scribe_capture_note_resolution_test import journal

admission_case = _admission_case
capture_engine = _capture_engine


@pytest.mark.asyncio
async def test_pending_status_is_read_only_and_late_save_can_commit(
    admission_case, monkeypatch
):
    f = admission_case
    first, request = await editable(f, monkeypatch)
    for _ in range(2):
        checked = await edits.check_capture_note_save(**request)
        assert (
            checked.status == "pending"
            and checked.result_note_ref is checked.result_sha256 is None
        )
    assert not journal(f, ScribeCaptureNoteSaveFence) and not journal(
        f, ScribeCaptureNoteEdit
    )
    assert len(rows(f, AIVisitNote)) == 1
    saved = await edits.save_capture_note(**request)
    checked = await edits.check_capture_note_save(**request)
    assert checked.status == "saved" and checked.result_note_ref == saved.note_ref
    assert (
        checked.result_sha256 == saved.note_sha256 and saved.note_ref != first.note_ref
    )
    assert len(rows(f, AIVisitNote)) == 2 and not journal(f, ScribeCaptureNoteSaveFence)
    assert len(f.model.calls) == 1


@pytest.mark.asyncio
async def test_old_save_status_returns_exact_receipt_after_a_later_save(
    admission_case, monkeypatch
):
    f = admission_case
    _, request = await editable(f, monkeypatch)
    first = await edits.save_capture_note(**request)
    second_request = {
        **request,
        "edit": edits.NoteEdit(
            client_key=uuid4(),
            expected_note_ref=first.note_ref,
            expected_note_sha256=first.note_sha256,
            expected_source_sha256=first.source_sha256,
            fields={"CC": "Sleep", "Plan": "Later synthetic edit"},
        ),
    }
    latest = await edits.save_capture_note(**second_request)
    checked = await edits.check_capture_note_save(**request)
    assert checked.status == "saved" and checked.result_note_ref == first.note_ref
    assert (
        checked.result_sha256 == first.note_sha256
        and checked.result_note_ref != latest.note_ref
    )
    current = next(note for note in rows(f, AIVisitNote) if note.superseded_at is None)
    assert str(current.id) == latest.note_ref
    assert len(rows(f, AIVisitNote)) == 3 and not journal(f, ScribeCaptureNoteSaveFence)


@pytest.mark.asyncio
@pytest.mark.parametrize("closed", [False, True])
async def test_receipted_request_rejects_changed_same_key_payload(
    admission_case, monkeypatch, closed
):
    f = admission_case
    _, request = await editable(f, monkeypatch)
    if closed:
        await edits.resolve_capture_note_save(**request)
    else:
        await edits.save_capture_note(**request)
    changed = {
        **request,
        "edit": request["edit"].model_copy(
            update={"fields": {"CC": "Sleep", "Plan": "Changed request"}}
        ),
    }
    with pytest.raises(notes.CaptureNoteConflict):
        await edits.check_capture_note_save(**changed)
    original = await edits.check_capture_note_save(**request)
    assert original.status == ("closed" if closed else "saved")
    assert len(journal(f, ScribeCaptureNoteSaveFence)) == int(closed)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("expected_session_id", None),
        ("schema_ref", "doctor_note_schema_" + "0" * 26),
        ("workspace_id", None),
    ],
)
async def test_foreign_save_check_creates_no_receipt(
    admission_case, monkeypatch, field, value
):
    f = admission_case
    _, request = await editable(f, monkeypatch)
    with pytest.raises((notes.CaptureNoteConflict, authority.AuthorityKernelError)):
        await edits.check_capture_note_save(**{**request, field: value or uuid4()})
    assert not journal(f, ScribeCaptureNoteSaveFence) and not journal(
        f, ScribeCaptureNoteEdit
    )


@pytest.mark.asyncio
async def test_revoked_caller_cannot_read_save_status(admission_case, monkeypatch):
    f = admission_case
    _, request = await editable(f, monkeypatch)
    await edits.save_capture_note(**request)
    with Session(f.engine) as session:
        identity = session.get(AuthorityActorExternalIdentity, f.case.identity.id)
        assert identity is not None
        identity.revoked_at = datetime.now(UTC)
        session.add(identity)
        session.commit()
    with pytest.raises(authority.AuthorityKernelError):
        await edits.check_capture_note_save(**request)
    assert not journal(f, ScribeCaptureNoteSaveFence)
