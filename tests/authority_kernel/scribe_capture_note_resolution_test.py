"""Durable resolution is mutually exclusive with late publication in PostgreSQL."""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from uuid import UUID, uuid4

import pytest

from app.services import authority
from app.services import scribe_capture_note_edits as edits
from app.services import scribe_capture_notes as notes

from activemodel.session_manager import _session_context
from app.models.ai_visit_note import AIVisitNote
from app.models.authority import AuthorityActorExternalIdentity
from app.models.scribe_capture_note_edit import (
    ScribeCaptureNoteEdit,
    ScribeCaptureNoteSaveFence,
)
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlmodel import Session, select

from tests.authority_kernel.scribe_capture_note_edits_test import (
    admission_case as _admission_case,
)
from tests.authority_kernel.scribe_capture_note_edits_test import (
    capture_engine as _capture_engine,
)
from tests.authority_kernel.scribe_capture_note_edits_test import (
    editable,
    rows,
)

admission_case = _admission_case
capture_engine = _capture_engine


def test_request_hash_matches_numeric_and_unicode_contract():
    fixture = Path(__file__).parents[1] / "fixtures/capture_note_save_resolution.json"
    for case in json.loads(fixture.read_text())["cases"]:
        assert (
            edits.request_hash(
                edits.NoteEdit.model_validate(case["edit"]),
                case["base"]["schema_ref"],
                UUID(case["base"]["session_id"]),
                UUID(case["workspace_id"]),
            )
            == case["receipt"]["request_sha256"]
        )


def journal(f, model):
    with Session(f.engine) as session:
        return session.exec(
            select(model).where(model.binding_id == f.binding.binding_ref)
        ).all()


@pytest.mark.asyncio
async def test_resolve_saved_edit_after_later_version_without_restoring_it(
    admission_case, monkeypatch
):
    f = admission_case
    _, request = await editable(f, monkeypatch)
    first = await edits.save_capture_note(**request)
    assert first.note_ref is not None
    assert first.note_sha256 is not None
    second = await edits.save_capture_note(
        **{
            **request,
            "edit": edits.NoteEdit(
                client_key=uuid4(),
                expected_note_ref=first.note_ref,
                expected_note_sha256=first.note_sha256,
                expected_source_sha256=first.source_sha256,
                fields={"CC": "Sleep", "Plan": "Later invented plan"},
            ),
        }
    )
    with pytest.raises(notes.CaptureNoteConflict):
        await edits.save_capture_note(**request)
    result = await edits.resolve_capture_note_save(**request)
    assert (
        result.status == "saved"
        and result.result_note_ref == first.note_ref
        and result.result_sha256 == first.note_sha256
    )
    assert result == await edits.resolve_capture_note_save(**request)
    assert len(rows(f, AIVisitNote)) == 3 and not journal(f, ScribeCaptureNoteSaveFence)
    current = next(n for n in rows(f, AIVisitNote) if n.superseded_at is None)
    assert str(current.id) == second.note_ref and len(f.model.calls) == 2


@pytest.mark.asyncio
async def test_close_unpublished_save_is_durable_and_prevents_retry(
    admission_case, monkeypatch
):
    f = admission_case
    _, request = await editable(f, monkeypatch)
    result = await edits.resolve_capture_note_save(**request)
    assert (
        result.status == "closed"
        and result.result_note_ref is result.result_sha256 is None
    )
    assert result == await edits.resolve_capture_note_save(**request)
    with pytest.raises(notes.CaptureNoteConflict):
        await edits.save_capture_note(**request)
    assert len(rows(f, AIVisitNote)) == 1 and not journal(f, ScribeCaptureNoteEdit)
    assert len(journal(f, ScribeCaptureNoteSaveFence)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("saved", [False, True])
async def test_resolution_rejects_same_key_with_changed_payload(
    admission_case, monkeypatch, saved
):
    f = admission_case
    _, request = await editable(f, monkeypatch)
    if saved:
        await edits.save_capture_note(**request)
    else:
        await edits.resolve_capture_note_save(**request)
    changed = {
        **request,
        "edit": request["edit"].model_copy(
            update={"fields": {"CC": "Sleep", "Plan": "Other text"}}
        ),
    }
    with pytest.raises(notes.CaptureNoteConflict):
        await edits.resolve_capture_note_save(**changed)
    assert (
        len(journal(f, ScribeCaptureNoteEdit))
        + len(journal(f, ScribeCaptureNoteSaveFence))
        == 1
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["base", "schema", "hash", "session"])
async def test_foreign_or_corrupt_resolution_creates_no_fence(
    admission_case, monkeypatch, fault
):
    f = admission_case
    _, request = await editable(f, monkeypatch)
    if fault == "base":
        request["edit"] = request["edit"].model_copy(
            update={"expected_note_ref": "visit_note_" + "0" * 26}
        )
    elif fault == "schema":
        request["schema_ref"] = "doctor_note_schema_" + "0" * 26
    elif fault == "hash":
        request["edit"] = request["edit"].model_copy(
            update={"expected_note_sha256": "f" * 64}
        )
    else:
        request["expected_session_id"] = uuid4()
    with pytest.raises(notes.CaptureNoteConflict):
        await edits.resolve_capture_note_save(**request)
    assert not journal(f, ScribeCaptureNoteSaveFence)


@pytest.mark.asyncio
@pytest.mark.parametrize("saved", [False, True])
async def test_revocation_during_consent_check_withholds_resolution(
    admission_case, monkeypatch, saved
):
    f = admission_case
    _, request = await editable(f, monkeypatch)
    if saved:
        await edits.save_capture_note(**request)
    original = notes._current_consent

    async def revoke(*args):
        await original(*args)
        with Session(f.engine) as session:
            identity = session.get(AuthorityActorExternalIdentity, f.case.identity.id)
            assert identity is not None
            identity.revoked_at = datetime.now(UTC)
            session.add(identity)
            session.commit()

    monkeypatch.setattr(notes, "_current_consent", revoke)
    with pytest.raises(authority.AuthorityKernelError):
        await edits.resolve_capture_note_save(**request)
    assert not journal(f, ScribeCaptureNoteSaveFence)


@pytest.mark.asyncio
async def test_close_fences_save_already_past_remote_source_checks(
    admission_case, monkeypatch
):
    f = admission_case
    _, request = await editable(f, monkeypatch)
    entered, release = Event(), Event()
    original = notes._current_source

    async def pause(*args):
        await original(*args)
        entered.set()
        assert await asyncio.to_thread(release.wait, 10)

    monkeypatch.setattr(notes, "_current_source", pause)

    def save():
        with Session(f.engine, expire_on_commit=False) as session:
            token = _session_context.set(session)
            try:
                with pytest.raises(notes.CaptureNoteConflict):
                    asyncio.run(edits.save_capture_note(**request))
            finally:
                _session_context.reset(token)

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(save)
        try:
            assert await asyncio.to_thread(entered.wait, 10)
            assert (await edits.resolve_capture_note_save(**request)).status == "closed"
        finally:
            release.set()
        pending.result(timeout=10)
    assert len(rows(f, AIVisitNote)) == 1 and not journal(f, ScribeCaptureNoteEdit)


@pytest.mark.asyncio
async def test_database_guards_reject_late_writer_and_fence_mutation(
    admission_case, monkeypatch
):
    f = admission_case
    _, request = await editable(f, monkeypatch)
    await edits.resolve_capture_note_save(**request)
    fence = journal(f, ScribeCaptureNoteSaveFence)[0]
    with Session(f.engine) as session, pytest.raises(DBAPIError):
        session.add(
            ScribeCaptureNoteEdit(
                binding_id=fence.binding_id,
                client_key=fence.client_key,
                session_id=fence.session_id,
                base_note_id=fence.base_note_id,
                result_note_id=fence.base_note_id,
                request_sha256=fence.request_sha256,
                source_sha256=request["edit"].expected_source_sha256,
                result_sha256=request["edit"].expected_note_sha256,
            )
        )
        session.commit()
    for command in (
        "UPDATE scribe_capture_note_save_fence SET request_sha256 = request_sha256",
        "DELETE FROM scribe_capture_note_save_fence",
    ):
        with Session(f.engine) as session, pytest.raises(DBAPIError):
            session.execute(
                text(command + " WHERE client_key = :key"), {"key": fence.client_key}
            )
            session.commit()
    assert len(journal(f, ScribeCaptureNoteSaveFence)) == 1 and not journal(
        f, ScribeCaptureNoteEdit
    )


@pytest.mark.asyncio
async def test_database_cannot_close_an_already_saved_request(
    admission_case, monkeypatch
):
    f = admission_case
    _, request = await editable(f, monkeypatch)
    await edits.save_capture_note(**request)
    saved = journal(f, ScribeCaptureNoteEdit)[0]
    with Session(f.engine) as session, pytest.raises(DBAPIError):
        session.add(
            ScribeCaptureNoteSaveFence(
                binding_id=saved.binding_id,
                client_key=saved.client_key,
                session_id=saved.session_id,
                base_note_id=saved.base_note_id,
                request_sha256=saved.request_sha256,
            )
        )
        session.commit()
    assert not journal(f, ScribeCaptureNoteSaveFence)


@pytest.mark.asyncio
async def test_failed_fence_write_cannot_claim_closed(admission_case, monkeypatch):
    f = admission_case
    _, request = await editable(f, monkeypatch)
    original = Session.add

    def fail(self, value, *args, **kwargs):
        if isinstance(value, ScribeCaptureNoteSaveFence):
            raise IntegrityError("invented fence failure", None, Exception())
        return original(self, value, *args, **kwargs)

    monkeypatch.setattr(Session, "add", fail)
    with pytest.raises(IntegrityError):
        await edits.resolve_capture_note_save(**request)
    assert not journal(f, ScribeCaptureNoteSaveFence)
