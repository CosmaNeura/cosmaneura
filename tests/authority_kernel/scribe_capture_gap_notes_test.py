"""Gap provenance with real Core persistence and invented receiver/model data."""

import asyncio
from dataclasses import replace
from typing import Any
from uuid import uuid4

import pytest
from typeid import TypeID

from app.commands import scribe_transcript as ingest
from app.jobs import summarize
from app.services import scribe_capture_gap_source as gaps
from app.services import scribe_capture_note_edits as edits
from app.services import scribe_capture_notes as notes
from app.services.scribe_capture_gateway import CaptureOperationUnavailable
from app.services.scribe_capture_recovery_contract import (
    CaptureRecoveryHistory,
    CaptureRecoveryReceipt,
)
from app.services.scribe_capture_working_contract import (
    canonical_document,
    canonical_hash,
)

from app.models.ai_visit_note import AIVisitNote
from app.models.ai_visit_transcript import AIVisitTranscript
from app.models.scribe_capture_gap_manifest import ScribeCaptureGapManifest
from app.models.scribe_capture_source_version import ScribeCaptureSourceVersion
from app.models.scribe_transcript_delivery import ScribeTranscriptDelivery
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlmodel import Session, select

from tests.authority_kernel.scribe_capture_notes_test import (
    FakeModel,
    admission_case,
    capture_engine,
    rows,
)
from tests.authority_kernel.scribe_capture_recovery_test import pair as recovery_pair
from tests.authority_kernel.scribe_capture_source_test import envelope
from tests.authority_kernel.scribe_capture_working_test import (
    setup_working,
    working_receipt,
)

__all__ = ["admission_case", "capture_engine"]


async def setup(f, monkeypatch):
    args = await setup_working(f, monkeypatch)
    f.caller = replace(
        f.caller,
        capture_settings=replace(
            f.caller.capture_settings,
            source_envelopes_enabled=True,
        ),
    )
    args["caller"] = f.caller
    _, receipt = recovery_pair(f.scope)
    assert isinstance(receipt, CaptureRecoveryReceipt)
    f.history = CaptureRecoveryHistory(
        session_id=f.scope.session_id, recoveries=(receipt,)
    )
    prior = f.client.capture_operation
    f.source_calls = 0
    f.after_source = lambda _: None

    async def operation(
        tenant, session, payload, *, idempotency_key, practitioner_id=None
    ):
        if payload.kind != "capture_source":
            return await prior(
                tenant,
                session,
                payload,
                idempotency_key=idempotency_key,
                practitioner_id=practitioner_id,
            )
        assert practitioner_id == f.scope.session_request.practitioner_id
        source = envelope(f.raw, f.working, f.history)
        f.source_calls += 1
        f.after_source(f.source_calls)
        return source

    f.client.capture_operation = operation
    return args


def manifests(f):
    with Session(f.engine) as session:
        return list(
            session.exec(
                select(ScribeCaptureGapManifest).where(
                    ScribeCaptureGapManifest.binding_id == f.binding.binding_ref
                )
            ).all()
        )


@pytest.mark.asyncio
async def test_initial_gap_draft_replay_retains_raw_and_explicit_review_evidence(
    admission_case, monkeypatch
):
    f = admission_case
    args = await setup(f, monkeypatch)
    preview = await notes.capture_note(**{**args, "prepare": False})
    assert preview.capture_gap_review is not None
    assert (
        preview.status == "not_prepared" and preview.capture_gap_review.review_required
    )
    assert not rows(f, AIVisitTranscript) and not manifests(f)
    first = await notes.capture_note(**args)
    assert first == await notes.capture_note(**args)
    assert first == await notes.capture_note(**{**args, "prepare": False})
    assert first.source_sha256 != notes._source(f.raw, f.scope).digest
    assert len(rows(f, AIVisitTranscript)) == 2 and len(rows(f, AIVisitNote)) == 1
    delivery = rows(f, ScribeTranscriptDelivery)[0]
    manifest = manifests(f)[0]
    assert manifest.raw_core_transcript_id == delivery.transcript_id
    assert str(manifest.initial_transcript_id) == first.transcript_ref
    assert manifest.initial_source_sha256 == first.source_sha256
    assert manifest.recovery_canonical == canonical_document(f.history)
    assert first.capture_gap_review is not None
    assert manifest.recovery_sha256 == first.capture_gap_review.recovery_sha256
    assert first.capture_gap_review is not None
    assert (
        first.capture_gap_review.observations[0].gap_id
        == f.history.recoveries[0].request.gap_id
    )
    assert delivery.payload_sha256 == notes._source(f.raw, f.scope).digest
    assert len(f.model.calls) == 2
    assert all(
        "Do not infer missing speech, gap duration" in call[0] for call in f.model.calls
    )
    assert "capture_gap_review" in first.model_dump(mode="json")


@pytest.mark.asyncio
async def test_qualified_gap_free_source_preserves_legacy_note_hash_and_wire_shape(
    admission_case, monkeypatch
):
    f = admission_case
    args = await setup(f, monkeypatch)
    f.history = CaptureRecoveryHistory(session_id=f.scope.session_id, recoveries=())
    result = await notes.capture_note(**args)
    assert result.source_sha256 == notes._source(f.raw, f.scope).digest
    assert (
        result.capture_gap_review is None
        and "capture_gap_review" not in result.model_dump(mode="json")
    )
    assert len(rows(f, AIVisitTranscript)) == 1 and not manifests(f)


@pytest.mark.asyncio
@pytest.mark.parametrize("initial_version", [1, 4])
async def test_gap_corrections_and_edits_preserve_manifest_and_reject_old_sources(
    admission_case, monkeypatch, initial_version
):
    f = admission_case
    args = await setup(f, monkeypatch)
    f.working = working_receipt(
        f.raw, f.scope.session_request.practitioner_id, initial_version
    )
    first = await notes.capture_note(**args)
    manifest_before = manifests(f)[0].model_dump()
    assert first.note_ref is not None
    assert first.note_sha256 is not None
    edit = edits.NoteEdit(
        client_key=uuid4(),
        expected_note_ref=first.note_ref,
        expected_note_sha256=first.note_sha256,
        expected_source_sha256=first.source_sha256,
        fields={"CC": "Invented review", "Plan": "Invented explicit plan."},
    )
    edit_args = {k: v for k, v in args.items() if k not in {"prepare", "model"}}
    saved = await edits.save_capture_note(**edit_args, edit=edit)
    assert saved == await edits.save_capture_note(**edit_args, edit=edit)
    assert saved.capture_gap_review == first.capture_gap_review
    f.working = working_receipt(
        f.raw,
        f.scope.session_request.practitioner_id,
        initial_version + 1,
        "Invented corrected capture.",
    )
    with pytest.raises(notes.CaptureNoteConflict):
        await edits.save_capture_note(**edit_args, edit=edit)
    assert (
        await notes.capture_note(**{**args, "prepare": False})
    ).status == "not_prepared"
    second = await notes.capture_note(**args)
    assert (
        second.note_ref != saved.note_ref
        and second.source_sha256 != saved.source_sha256
    )
    assert second.capture_gap_review == first.capture_gap_review
    assert manifests(f)[0].model_dump() == manifest_before
    assert (manifest_before["initial_transcript_id"] is None) == (initial_version > 1)
    with Session(f.engine) as session:
        versions = session.exec(
            select(ScribeCaptureSourceVersion).where(
                ScribeCaptureSourceVersion.binding_id == f.binding.binding_ref
            )
        ).all()
        assert len(versions) == (1 if initial_version == 1 else 2)
    f.working = working_receipt(f.raw, f.scope.session_request.practitioner_id)
    with pytest.raises(notes.CaptureNoteConflict):
        await notes.capture_note(**args)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["disable", "missing", "changed"])
async def test_manifest_cannot_be_downgraded_or_rebound(
    admission_case, monkeypatch, change
):
    f = admission_case
    args = await setup(f, monkeypatch)
    await notes.capture_note(**args)
    error = notes.CaptureNoteConflict
    if change == "disable":
        args["caller"] = replace(
            f.caller,
            capture_settings=replace(
                f.caller.capture_settings, source_envelopes_enabled=False
            ),
        )
        error = notes.CaptureNoteDisabled
    elif change == "missing":
        f.history = CaptureRecoveryHistory(session_id=f.scope.session_id, recoveries=())
    else:
        row = f.history.recoveries[0]
        request = row.request.model_copy(
            update={"reason_code": "process_recovery_unknown"}
        )
        row = row.model_copy(
            update={"request": request, "request_sha256": canonical_hash(request)}
        )
        f.history = CaptureRecoveryHistory(
            session_id=f.scope.session_id, recoveries=(row,)
        )
    with pytest.raises(error):
        await notes.capture_note(**{**args, "prepare": False})
    assert len(f.model.calls) == 2 and len(manifests(f)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", [1, 2])
async def test_current_source_change_during_model_work_cannot_publish(
    admission_case, monkeypatch, phase
):
    f = admission_case
    args = await setup(f, monkeypatch)

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
        await notes.capture_note(**args)
    assert not rows(f, AIVisitNote) and len(manifests(f)) == 1
    f.model.after = None
    assert (await notes.capture_note(**args)).status == "draft"


@pytest.mark.asyncio
@pytest.mark.parametrize("language", ["es-US", "fr-FR"])
async def test_gap_acknowledgment_does_not_clear_language_blockers(
    admission_case, monkeypatch, language
):
    f = admission_case
    args = await setup(f, monkeypatch)
    f.raw = f.raw.model_copy(update={"language_code": language})
    f.working = working_receipt(f.raw, f.scope.session_request.practitioner_id)
    with pytest.raises(CaptureOperationUnavailable):
        await notes.capture_note(**args)
    assert not f.model.calls and not rows(f, AIVisitTranscript) and not manifests(f)


@pytest.mark.asyncio
async def test_manifest_failure_rolls_back_raw_delivery_and_effective_source(
    admission_case, monkeypatch
):
    f = admission_case
    args = await setup(f, monkeypatch)
    original = gaps.import_manifest

    def fail(*args):
        assert not rows(f, AIVisitTranscript) and not rows(f, ScribeTranscriptDelivery)
        original(*args)
        assert not rows(f, AIVisitTranscript) and not manifests(f)
        raise notes.CaptureNoteConflict

    monkeypatch.setattr(gaps, "import_manifest", fail)
    with pytest.raises(notes.CaptureNoteConflict):
        await notes.capture_note(**args)
    assert not rows(f, AIVisitTranscript) and not rows(f, ScribeTranscriptDelivery)
    assert not rows(f, AIVisitNote) and not manifests(f) and not f.model.calls
    monkeypatch.setattr(gaps, "import_manifest", original)
    assert (await notes.capture_note(**args)).status == "draft"


@pytest.mark.asyncio
async def test_legacy_raw_delivery_keeps_committed_import_before_dispatch(
    admission_case, monkeypatch
):
    f = admission_case
    await setup(f, monkeypatch)
    raw = notes._source(f.raw, f.scope)
    dispatched = []

    def dispatch(session, delivery, transcript):
        # A distinct connection sees the committed legacy import before queue
        # dispatch. No real broker/model/engagement job runs in this proof.
        assert (
            len(rows(f, ScribeTranscriptDelivery))
            == len(rows(f, AIVisitTranscript))
            == 1
        )
        dispatched.append(str(transcript.id))

    monkeypatch.setattr(ingest, "_dispatch_pipeline_if_needed", dispatch)
    args: dict[str, Any] = dict(
        encounter_id=f.case.appointment.id,
        caller_client_id=f.caller.workload_client_id,
        idempotency_key="invented-legacy-" + str(f.scope.session_id),
        scribe_session_id=f.scope.session_id,
        payload_sha256=raw.digest,
        duration=raw.duration,
        transcript_data=raw.entries,
    )
    with Session(f.engine) as session:
        first = ingest._perform(session=session, **args)
        replay = ingest._perform(session=session, **args)
        assert first.transcript.id == replay.transcript.id
        assert first.disposition == "created" and replay.disposition == "replayed"
    assert len(dispatched) == 2 and not manifests(f)


@pytest.mark.asyncio
async def test_background_summary_skips_both_raw_and_effective_gap_sources(
    admission_case, monkeypatch
):
    f = admission_case
    args = await setup(f, monkeypatch)
    note = await notes.capture_note(**args)
    monkeypatch.setattr(
        summarize,
        "build_action_item_prompt",
        lambda *_: pytest.fail("background model reached"),
    )
    for row in rows(f, AIVisitTranscript):
        summarize.perform(str(row.id), f.schema_ref)
    assert await notes.capture_note(**{**args, "prepare": False}) == note


@pytest.mark.asyncio
async def test_concurrent_gap_preparation_reuses_one_manifest_and_note(
    admission_case, monkeypatch
):
    f = admission_case
    args = await setup(f, monkeypatch)
    entered = 0
    both = asyncio.Event()

    class ConcurrentModel(FakeModel):
        async def complete(self, prompt, *, json_output):
            nonlocal entered
            if json_output:
                entered += 1
                if entered == 2:
                    both.set()
                await asyncio.wait_for(both.wait(), 10)
            return await super().complete(prompt, json_output=json_output)

    args["model"] = ConcurrentModel()
    left, right = await asyncio.gather(
        notes.capture_note(**args), notes.capture_note(**args)
    )
    assert left == right
    assert len(manifests(f)) == 1 and len(rows(f, AIVisitTranscript)) == 2
    assert len(rows(f, AIVisitNote)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["update", "delete"])
async def test_database_prevents_gap_manifest_rewrite(
    admission_case, monkeypatch, mutation
):
    f = admission_case
    args = await setup(f, monkeypatch)
    before = await notes.capture_note(**args)
    manifest = manifests(f)[0]
    statement = (
        "DELETE FROM scribe_capture_gap_manifest WHERE id=:id"
        if mutation == "delete"
        else "UPDATE scribe_capture_gap_manifest SET recovery_sha256=:hash WHERE id=:id"
    )
    with Session(f.engine) as session, pytest.raises(DBAPIError) as error:
        session.execute(
            text(statement),
            {"id": TypeID.from_string(str(manifest.id)).uuid, "hash": "0" * 64},
        )
        session.commit()
    assert "gap manifest history is immutable" in str(error.value.orig)
    assert await notes.capture_note(**{**args, "prepare": False}) == before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation", ["hash", "practitioner", "session", "initial_raw", "scope", "shape"]
)
async def test_database_rejects_invalid_manifest_before_any_unique_conflict(
    admission_case, monkeypatch, mutation
):
    f = admission_case
    args = await setup(f, monkeypatch)
    history = f.history
    f.history = CaptureRecoveryHistory(session_id=f.scope.session_id, recoveries=())
    await notes.capture_note(**args)
    assert not manifests(f)
    raw_id = rows(f, ScribeTranscriptDelivery)[0].transcript_id
    body = history.model_dump(mode="json")
    if mutation == "practitioner":
        body["recoveries"][0]["acknowledged_by_practitioner_id"] = (
            "doctor_00000000000000000000000001"
        )
    if mutation == "scope":
        body["recoveries"][0]["session_id"] = str(uuid4())
    if mutation == "shape":
        del body["recoveries"]
    kwargs: dict[str, Any] = dict(
        binding_id=f.binding.binding_ref,
        session_id=f.scope.session_id,
        raw_source_id=f.raw.id,
        raw_source_sha256=f.working.raw_transcript_sha256,
        recovery_sha256=canonical_hash(body),
        recovery_canonical=canonical_document(body),
        raw_core_transcript_id=raw_id,
    )
    if mutation == "hash":
        kwargs["recovery_sha256"] = "0" * 64
    elif mutation == "session":
        kwargs["session_id"] = uuid4()
    elif mutation == "initial_raw":
        kwargs.update(initial_transcript_id=raw_id, initial_source_sha256="a" * 64)
    with Session(f.engine) as session, pytest.raises(DBAPIError) as error:
        session.add(ScribeCaptureGapManifest(**kwargs))
        session.commit()
    assert "unique" not in str(error.value.orig).lower()
    assert not manifests(f)


@pytest.mark.asyncio
async def test_concurrent_first_gap_blocks_a_foreign_working_origin(
    admission_case, monkeypatch
):
    from concurrent.futures import ThreadPoolExecutor
    from queue import Queue
    from time import monotonic, sleep

    f = admission_case
    args = await setup(f, monkeypatch)
    history = f.history
    f.history = CaptureRecoveryHistory(session_id=f.scope.session_id, recoveries=())
    await notes.capture_note(**args)
    assert not manifests(f)
    with Session(f.engine) as check:
        assert not check.exec(
            select(ScribeCaptureSourceVersion).where(
                ScribeCaptureSourceVersion.binding_id == f.binding.binding_ref
            )
        ).all()
    raw = rows(f, AIVisitTranscript)[0]
    effective = AIVisitTranscript(
        doctor_id=raw.doctor_id,
        patient_id=raw.patient_id,
        appointment_id=raw.appointment_id,
        duration=raw.duration,
        transcript_data=raw.transcript_data,
    ).save()
    manifest = ScribeCaptureGapManifest(
        binding_id=f.binding.binding_ref,
        session_id=f.scope.session_id,
        raw_source_id=f.raw.id,
        raw_source_sha256=f.working.raw_transcript_sha256,
        recovery_sha256=canonical_hash(history),
        recovery_canonical=canonical_document(history),
        raw_core_transcript_id=raw.id,
    )
    source: dict[str, Any] = dict(
        binding_id=f.binding.binding_ref,
        session_id=f.scope.session_id,
        raw_source_id=uuid4(),
        raw_source_sha256=f.working.raw_transcript_sha256,
        working_transcript_id=uuid4(),
        working_version=2,
        working_sha256="b" * 64,
        source_sha256="c" * 64,
        raw_core_transcript_id=raw.id,
        transcript_id=effective.id,
    )
    started = Queue()

    def insert_working():
        with Session(f.engine) as session:
            started.put(session.execute(text("SELECT pg_backend_pid()")).scalar_one())
            try:
                session.add(ScribeCaptureSourceVersion(**source))
                session.commit()
            except DBAPIError as error:
                session.rollback()
                return str(error.orig)
        return None

    owner = Session(f.engine)
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        owner.add(manifest)
        owner.flush()
        future = pool.submit(insert_working)
        pid = started.get(timeout=5)
        deadline = monotonic() + 5
        blocked = False
        while monotonic() < deadline:
            with f.engine.connect() as connection:
                blocked = bool(
                    connection.execute(
                        text("SELECT cardinality(pg_blocking_pids(:pid)) > 0"),
                        {"pid": pid},
                    ).scalar_one()
                )
            if blocked:
                break
            sleep(0.01)
        assert blocked, "working insert must wait on the gap publisher's binding lock"
        owner.commit()
        assert "working source gap origin mismatch" in (future.result(timeout=5) or "")
        with Session(f.engine) as check:
            assert not check.exec(
                select(ScribeCaptureSourceVersion).where(
                    ScribeCaptureSourceVersion.binding_id == f.binding.binding_ref
                )
            ).all()
    finally:
        owner.rollback()
        owner.close()
        pool.shutdown(wait=True)
