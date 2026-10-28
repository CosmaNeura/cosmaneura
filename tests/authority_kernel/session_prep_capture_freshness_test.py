"""Saved native corrections exclude old reviewed notes without erasing history.

Run with the isolated authority conftest and an explicitly owned synthetic
PostgreSQL URL; no migrations, shared truncation, external services or Clerk.
"""

import asyncio
import copy
import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.configuration.session_prep import SessionPrepProviderSettings
from app.routes import session_prep_review as review_route
from app.services import authority
from app.services import scribe_capture_admission as admission
from app.services import scribe_capture_gateway as gateway
from app.services import scribe_capture_notes as notes
from app.services import session_prep_capture_freshness as freshness
from app.services.provider_record_authority import validate_provider_record_scope
from app.services.scribe_capture_admission import authorize_admitted_capture
from app.services.scribe_capture_protocol import OPERATION_ADAPTER
from app.services.scribe_creator_edit_contract import CreatorTranscriptEditReceiptV1
from app.services.scribe_workflow import (
    ScribeWorkflowConflictError,
    ScribeWorkflowRetryableError,
)
from app.services.session_prep_note_sources import (
    NoteSourceChanged,
    finalize_note_for_session_prep,
    load_reviewed_note_sources,
    note_content_sha256,
    prior_note_overview,
)

from activemodel.session_manager import _serialize_pydantic_model, _session_context
from app.models.ai_visit_note import AIVisitNote
from app.models.ai_visit_transcript import AIVisitTranscript
from app.models.appointment import Appointment
from app.models.authority import AuthorityActorExternalIdentity
from app.models.doctor_note_schema import DoctorNoteSchema
from app.models.scribe_capture_admission import ScribeCaptureAdmission
from app.models.scribe_capture_correction_frontier import (
    ScribeCaptureCorrectionFrontier,
)
from app.models.scribe_capture_note_context import ScribeCaptureNoteContext
from app.models.session_prep_note_finalization import SessionPrepNoteFinalization
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
from sqlmodel import Session, col, create_engine, select

from tests.authority_kernel.scribe_capture_admission_test import (
    admission_case as _admission_case,
)
from tests.authority_kernel.scribe_capture_scope_test import (
    REAL_VIEW_AS_GUARD,
    SETTINGS,
)
from tests.authority_kernel.scribe_configured_capture_test import note_setup

admission_case = _admission_case


@pytest.fixture
def capture_engine(monkeypatch):
    raw = os.environ.get("SESSION_PREP_NATIVE_FRESHNESS_DATABASE_URL")
    if not raw:
        pytest.skip("Explicit owned synthetic native-freshness database required")
    url = make_url(raw)
    lab = Path("/private/tmp/session-prep-native-freshness-pg-20261021")
    ownership = json.loads((lab / "ownership.json").read_text())
    assert ownership["only_synthetic"] is True and ownership["listen_addresses"] == ""
    assert (
        url.host is None
        and url.username == "session_prep_freshness_owner"
        and isinstance(url.database, str)
        and url.database.startswith("session_prep_native_freshness_20261021_")
        and url.query.get("host") == str(lab / "socket")
        and url.query.get("port") == "55460"
    )
    engine = create_engine(
        url.set(drivername="postgresql+psycopg"),
        json_serializer=_serialize_pydantic_model,
    )
    monkeypatch.setattr(
        "app.services.view_as.mount_context_session_blocked_by_view_as",
        REAL_VIEW_AS_GUARD,
    )
    monkeypatch.setattr(
        authority, "validate_provider_record_scope", validate_provider_record_scope
    )
    yield engine
    engine.dispose()


async def case(f, monkeypatch):
    kwargs = await note_setup(f)
    draft = await notes.capture_note(**kwargs)
    f.scope = authorize_admitted_capture(
        f.caller, f.binding.binding_ref, f.binding.workspace_id
    )
    f.note = f.session.get(AIVisitNote, draft.note_ref)
    assert f.note is not None
    f.target = Appointment(
        doctor_id=f.note.doctor_id,
        patient_id=f.case.patient.id,
        appointment_date=datetime.now(UTC) + timedelta(minutes=1),
        external_id=None,
    )
    f.session.add(f.target)
    f.session.flush()
    f.review = finalize_note_for_session_prep(
        session=f.session,
        doctor_id=f.note.doctor_id,
        note_id=f.note.id,
        expected_content_sha256=note_content_sha256(f.note.note),
        observed_at=datetime.now(UTC),
    )
    f.session.commit()
    # The capture-only fixture starts with an explicitly imported authority
    # binding. Add the reviewed real-record links for this synthetic provider
    # so normal Session Prep token renewal can use the same clinical scope.
    from app.models.authority import AuthorityPersonOrigin
    from app.models.provider_record_authority import (
        ProviderPracticeAuthority,
        ProviderRecordAuthority,
    )

    practice_scope = ProviderPracticeAuthority(
        practice_id=f.case.practice.id, tenant_id=f.case.tenant.id
    )
    f.session.add(practice_scope)
    f.session.flush()
    f.case.person_binding.actor_id = f.case.actor.id
    f.session.add(f.case.person_binding)
    f.session.add(
        AuthorityPersonOrigin(
            person_id=f.case.person.id,
            origin="provider_entry",
            source_ref=str(f.case.patient.id),
        )
    )
    f.session.add(
        ProviderRecordAuthority(
            binding_id=f.case.person_binding.id,
            practice_authority_id=practice_scope.id,
            doctor_id=f.case.doctor.id,
            appointment_id=f.case.appointment.id,
            patient_id=f.case.patient.id,
            context_snapshot_ref="synthetic-capture-context-" + uuid4().hex,
        )
    )
    f.session.commit()
    monkeypatch.setattr(
        review_route.configuration,
        "SESSION_PREP_PROVIDER_SETTINGS",
        SessionPrepProviderSettings(
            enabled=True,
            clerk_subjects=(
                review_route.configuration.SESSION_PREP_PROVIDER_SETTINGS.clerk_subjects
                | frozenset({f.case.doctor.clerk_id})
            ),
        ),
    )
    # The newly reviewed real-record association changes the context digest;
    # exchange a fresh human grant before any subsequent capture operation.
    token = authority.exchange_context_token(
        clerk_issuer=f.case.identity.issuer,
        clerk_subject=f.case.identity.subject,
        requested_surface="provider_assistant",
        record_ref=str(f.case.appointment.id),
        session=f.session,
        settings=SETTINGS,
    )
    turn = uuid4()
    resolved = authority.resolve_mount_context(
        workload_client_id=f.caller.workload_client_id,
        mount_context_token=token.context_token,
        client_turn_id=turn,
        session=f.session,
        settings=SETTINGS,
    )
    f.session.commit()
    f.caller = replace(
        f.caller,
        mount_context_token=token.context_token,
        client_turn_id=turn,
        interaction_ref=resolved.interaction_ref,
    )
    return {**kwargs, "caller": f.caller}


def receipt(f, version=2, **changes):
    return CreatorTranscriptEditReceiptV1(
        session_id=f.scope.session_id,
        edit_id=uuid4(),
        working_transcript_id=UUID(f.view["working"]["working_transcript_id"]),
        working_transcript_version=version,
        **changes,
    )


async def acknowledge(f, saved, *, error=None, after=None):
    operation = OPERATION_ADAPTER.validate_python(
        dict(
            kind="edit_transcript",
            payload=dict(
                schema_version="1.0",
                expected_source_transcript_version=1,
                expected_working_transcript_id=str(saved.working_transcript_id),
                expected_working_transcript_version=saved.working_transcript_version
                - 1,
                speakers=[
                    dict(
                        speaker_id=f.view["working"]["segments"][0]["speaker_id"],
                        role="patient",
                    )
                ],
            ),
        )
    )
    original = f.client.capture_operation

    async def remote(*args, **kwargs):
        if after:
            after()
        if error:
            raise error
        return saved

    f.client.capture_operation = remote
    try:
        return await gateway.execute_capture_operation(
            caller=f.caller,
            binding_ref=f.binding.binding_ref,
            workspace_id=f.binding.workspace_id,
            operation=operation,
            client_request_key="synthetic-edit-" + str(saved.edit_id),
            client=f.client,
        )
    finally:
        f.client.capture_operation = original


def context(f):
    f.session.expire_all()
    return f.session.exec(
        select(ScribeCaptureNoteContext).where(
            ScribeCaptureNoteContext.note_id == f.note.id
        )
    ).one_or_none()


def frontier(f):
    f.session.expire_all()
    return f.session.exec(
        select(ScribeCaptureCorrectionFrontier).where(
            ScribeCaptureCorrectionFrontier.admission_id == f.scope.operation_ref
        )
    ).one_or_none()


def sources(f):
    return load_reviewed_note_sources(
        session=f.session,
        doctor_id=f.note.doctor_id,
        target_appointment_id=f.target.id,
        observed_at=datetime.now(UTC) + timedelta(minutes=1),
    )


@pytest.mark.asyncio
async def test_saved_ack_excludes_old_review_and_keeps_note_history_and_notebook(
    admission_case, monkeypatch
):
    f = admission_case
    await case(f, monkeypatch)
    before = copy.deepcopy(f.note.note)
    reviewed = copy.deepcopy(f.review.reviewed_content)
    body = "Synthetic separate notebook text"
    notebook = notes.ScratchNotes(
        workspace_id=f.binding.workspace_id,
        entry_id=uuid4(),
        revision=1,
        content=body,
        content_sha256=hashlib.sha256(body.encode()).hexdigest(),
    )
    f.session.add(
        ScribeCaptureNoteContext(
            binding_id=f.binding.binding_ref,
            note_id=f.note.id,
            scratch_notes=notebook.model_dump(mode="json"),
        )
    )
    f.session.commit()
    assert sources(f).sources
    saved = receipt(f)
    assert await acknowledge(f, saved) == saved
    assert sources(f).sources == ()
    assert note_content_sha256(f.note.note) == note_content_sha256(before)
    assert f.note.superseded_at is None and f.note.note == before
    assert (
        f.session.get(SessionPrepNoteFinalization, f.review.id).reviewed_content
        == reviewed
    )
    row = context(f)
    assert row is not None and row.scratch_notes == notebook.model_dump(mode="json")
    assert notes._notebook_context(f.session, f.scope, f.note) == notebook
    assert review_route.note_review_statuses(f.case.doctor, [f.note]) == {}
    overview = prior_note_overview(
        session=f.session,
        doctor_id=f.note.doctor_id,
        target_appointment_id=f.target.id,
        observed_at=datetime.now(UTC) + timedelta(minutes=1),
    )
    assert (
        next(item for item in overview.notes if item.note_id == str(f.note.id)).status
        == "unavailable"
    )
    with pytest.raises(NoteSourceChanged):
        finalize_note_for_session_prep(
            session=f.session,
            doctor_id=f.note.doctor_id,
            note_id=f.note.id,
            expected_content_sha256=note_content_sha256(before),
            observed_at=datetime.now(UTC),
        )
    assert "minimum_working_version" not in json.dumps(
        notebook.reference().model_dump(mode="json")
    )


@pytest.mark.asyncio
async def test_stale_restore_or_in_place_old_body_edit_cannot_clear_marker(
    admission_case, monkeypatch
):
    f = admission_case
    await case(f, monkeypatch)
    await acknowledge(f, receipt(f))
    f.note.superseded_at = None
    f.note.supersession_reason = None
    f.note.note = {"CC": "Edited old synthetic body", "Plan": ""}
    f.session.add(f.note)
    f.session.commit()
    with pytest.raises(NoteSourceChanged):
        finalize_note_for_session_prep(
            session=f.session,
            doctor_id=f.note.doctor_id,
            note_id=f.note.id,
            expected_content_sha256=note_content_sha256(f.note.note),
            observed_at=datetime.now(UTC),
        )
    assert sources(f).sources == ()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind",
    [
        "conflict",
        "lost_reply",
        "invalid_session",
        "invalid_increment",
        "revoked_identity",
        "storage_failure",
    ],
)
async def test_unconfirmed_invalid_or_foreign_ack_never_creates_marker(
    admission_case, monkeypatch, kind
):
    f = admission_case
    await case(f, monkeypatch)
    saved = receipt(f)
    error = (
        ScribeWorkflowConflictError("synthetic")
        if kind == "conflict"
        else ScribeWorkflowRetryableError("synthetic")
        if kind == "lost_reply"
        else None
    )
    after = None
    expected = (
        gateway.CaptureOperationUnavailable
        if kind == "conflict"
        else gateway.CaptureOperationUnconfirmed
        if kind in {"lost_reply", "storage_failure"}
        else authority.AuthorityKernelError
        if kind == "revoked_identity"
        else gateway.CaptureReceiptInvalid
    )
    if kind == "invalid_session":
        saved = saved.model_copy(update={"session_id": uuid4()})
    if kind == "invalid_increment":
        original = gateway.validate_receipt
        monkeypatch.setattr(
            gateway,
            "validate_receipt",
            lambda op, value, scope, **kw: original(
                op,
                value.model_copy(update={"working_transcript_version": 99}),
                scope,
                **kw,
            ),
        )
    if kind == "revoked_identity":

        def revoke():
            with Session(f.engine) as session:
                row = session.get(AuthorityActorExternalIdentity, f.case.identity.id)
                assert row is not None
                row.revoked_at = datetime.now(UTC)
                session.add(row)
                session.commit()

        after = revoke
    if kind == "storage_failure":

        def fail(**_kwargs):
            raise OperationalError(
                "synthetic statement", {}, RuntimeError("synthetic-private-sentinel")
            )

        monkeypatch.setattr(gateway, "retain_acknowledged_capture_correction", fail)
    with pytest.raises(expected):
        await acknowledge(f, saved, error=error, after=after)
    assert frontier(f) is None
    assert sources(f).sources


@pytest.mark.asyncio
async def test_old_receipt_replay_never_reduces_frontier_or_marks_newer_note(
    admission_case, monkeypatch
):
    f = admission_case
    kwargs = await case(f, monkeypatch)
    second = receipt(f)
    await acknowledge(f, second)
    third = receipt(f, 3)
    await acknowledge(f, third)
    await acknowledge(f, second)
    marker = frontier(f)
    assert marker.minimum_working_version == 3 and marker.edit_id == third.edit_id
    with pytest.raises(notes.CaptureNoteConflict):
        await notes.capture_note(**{**kwargs, "prepare": False})


@pytest.mark.asyncio
async def test_updated_source_publishes_new_unreviewed_note_and_old_replay_leaves_it_eligible(
    admission_case, monkeypatch
):
    f = admission_case
    kwargs = await case(f, monkeypatch)
    second = receipt(f)
    await acknowledge(f, second)
    with pytest.raises(notes.CaptureNoteConflict):
        await notes.capture_note(**{**kwargs, "prepare": False})
    f.view["working"]["working_transcript_version"] = 2
    f.view["review_queue"]["working_transcript_version"] = 2
    f.view["working"]["segments"][0].update(
        content="Corrected synthetic report.",
        translation_version=2,
        translation_id=str(uuid4()),
        translation_review_id=str(uuid4()),
    )
    f.model.output = '{"CC":"Corrected synthetic concern","Plan":""}'
    newer = await notes.capture_note(**kwargs)
    assert newer.note_ref != str(f.note.id) and newer.review_required
    assert sources(f).sources == ()
    new_note = f.session.get(AIVisitNote, newer.note_ref)
    assert not freshness.note_has_saved_capture_correction(f.session, new_note)
    finalize_note_for_session_prep(
        session=f.session,
        doctor_id=new_note.doctor_id,
        note_id=new_note.id,
        expected_content_sha256=note_content_sha256(new_note.note),
        observed_at=datetime.now(UTC),
    )
    f.session.commit()
    assert [s.source_ref for s in sources(f).sources] == [str(new_note.id)]
    await acknowledge(f, second)
    assert [s.source_ref for s in sources(f).sources] == [str(new_note.id)]
    assert frontier(f).minimum_working_version == 2


@pytest.mark.asyncio
async def test_saved_correction_between_last_remote_check_and_final_publication_blocks_old_draft(
    admission_case, monkeypatch
):
    f = admission_case
    kwargs = await case(f, monkeypatch)
    schema = DoctorNoteSchema(
        doctor_id=f.note.doctor_id,
        note_type="Another synthetic template",
        fields={"CC": "Concern", "Plan": "Plan"},
    )
    f.session.add(schema)
    f.session.commit()
    last_read = notes._current_source
    saved = receipt(f)
    triggered = []

    async def correction_after_read(*args, **kw):
        await last_read(*args, **kw)
        if len(f.model.calls) >= 2 and not triggered:
            triggered.append(True)
            await acknowledge(f, saved)

    monkeypatch.setattr(notes, "_current_source", correction_after_read)
    with pytest.raises(notes.CaptureNoteConflict):
        await notes.capture_note(**{**kwargs, "schema_ref": str(schema.id)})
    assert triggered and frontier(f) is not None
    assert not f.session.exec(
        select(AIVisitNote).where(AIVisitNote.doctor_note_schema_id == schema.id)
    ).all()


@pytest.mark.asyncio
async def test_concurrent_acks_keep_max_version_under_current_appointment_lock(
    admission_case, monkeypatch
):
    f = admission_case
    await case(f, monkeypatch)
    saved = [receipt(f), receipt(f, 3)]

    def record(item):
        with Session(f.engine, expire_on_commit=False) as session:
            token = _session_context.set(session)
            try:
                freshness.retain_acknowledged_capture_correction(
                    caller=f.caller,
                    binding_ref=f.binding.binding_ref,
                    workspace_id=f.binding.workspace_id,
                    scope=f.scope,
                    receipt=item,
                )
            finally:
                _session_context.reset(token)

    def concurrent():
        with ThreadPoolExecutor(max_workers=2) as pool:
            return list(pool.map(record, saved))

    await asyncio.to_thread(concurrent)
    assert frontier(f).minimum_working_version == 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault", ["working_id", "scope_session", "binding", "workspace"]
)
async def test_wrong_scope_cannot_invalidate_an_owned_note(
    admission_case, monkeypatch, fault
):
    f = admission_case
    await case(f, monkeypatch)
    saved = receipt(f)
    args: dict[str, Any] = dict(
        caller=f.caller,
        binding_ref=f.binding.binding_ref,
        workspace_id=f.binding.workspace_id,
        scope=f.scope,
        receipt=saved,
    )
    if fault == "working_id":
        args["receipt"] = saved.model_copy(update={"working_transcript_id": uuid4()})
    elif fault == "scope_session":
        args["scope"] = replace(f.scope, session_id=uuid4())
    elif fault == "binding":
        args["binding_ref"] = "scribe_capture_binding_00000000000000000000000001"
    else:
        args["workspace_id"] = uuid4()
    with pytest.raises(
        (freshness.CaptureSourceCorrected, authority.AuthorityKernelError)
    ):
        freshness.retain_acknowledged_capture_correction(**args)
    assert frontier(f) is None and sources(f).sources


@pytest.mark.parametrize("version", [True, "2", 1, 0, -1])
def test_frontier_requires_strict_valid_version(version):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ScribeCaptureCorrectionFrontier.model_validate(
            dict(
                admission_id="scribe_admission_01j00000000000000000000000",
                binding_id="scribe_capture_binding_01j00000000000000000000000",
                session_id=uuid4(),
                working_transcript_id=uuid4(),
                minimum_working_version=version,
                edit_id=uuid4(),
            )
        )


@pytest.mark.asyncio
async def test_renewed_human_grant_and_duplicate_ack_retain_exact_capture_frontier(
    admission_case, monkeypatch
):
    f = admission_case
    await case(f, monkeypatch)
    saved = receipt(f)
    await acknowledge(f, saved)
    original_frontier = frontier(f).id
    original_scope = f.scope
    token = authority.exchange_context_token(
        clerk_issuer=f.case.identity.issuer,
        clerk_subject=f.case.identity.subject,
        requested_surface="provider_assistant",
        record_ref=str(f.case.appointment.id),
        session=f.session,
        settings=SETTINGS,
    )
    turn = uuid4()
    resolved = authority.resolve_mount_context(
        workload_client_id=f.caller.workload_client_id,
        mount_context_token=token.context_token,
        client_turn_id=turn,
        session=f.session,
        settings=SETTINGS,
    )
    f.session.commit()
    f.caller = replace(
        f.caller,
        mount_context_token=token.context_token,
        client_turn_id=turn,
        interaction_ref=resolved.interaction_ref,
    )
    resumed = await admission.admit_capture(
        caller=f.caller,
        binding_ref=f.binding.binding_ref,
        workspace_id=f.binding.workspace_id,
        client_request_key="synthetic-resume-" + uuid4().hex,
        attestation=None,
        client=f.client,
        expected_session_id=f.scope.session_id,
    )
    assert resumed.operation_ref == original_scope.operation_ref
    f.scope = authorize_admitted_capture(
        f.caller, f.binding.binding_ref, f.binding.workspace_id
    )
    assert f.scope == original_scope
    await acknowledge(f, saved)
    assert frontier(f).id == original_frontier
    assert frontier(f).minimum_working_version == 2 and sources(f).sources == ()


@pytest.mark.asyncio
async def test_correction_before_any_import_or_first_note_blocks_old_prepare(
    admission_case, monkeypatch
):
    f = admission_case
    kwargs = await note_setup(f)
    f.scope = authorize_admitted_capture(
        f.caller, f.binding.binding_ref, f.binding.workspace_id
    )
    await acknowledge(f, receipt(f))
    assert frontier(f) is not None
    assert not f.session.exec(
        select(AIVisitNote).where(AIVisitNote.appointment_id == f.case.appointment.id)
    ).all()
    with pytest.raises(notes.CaptureNoteConflict):
        await notes.capture_note(**kwargs)
    assert not f.model.calls


@pytest.mark.asyncio
async def test_correction_during_first_note_generation_prevents_old_publication(
    admission_case, monkeypatch
):
    f = admission_case
    kwargs = await note_setup(f)
    f.scope = authorize_admitted_capture(
        f.caller, f.binding.binding_ref, f.binding.workspace_id
    )
    last_read = notes._current_source
    saved = receipt(f)
    triggered = []

    async def correction_after_read(*args, **kw):
        await last_read(*args, **kw)
        if f.model.calls and not triggered:
            triggered.append(True)
            await acknowledge(f, saved)

    monkeypatch.setattr(notes, "_current_source", correction_after_read)
    with pytest.raises(notes.CaptureNoteConflict):
        await notes.capture_note(**kwargs)
    assert triggered and frontier(f) is not None
    assert not f.session.exec(
        select(AIVisitNote).where(AIVisitNote.appointment_id == f.case.appointment.id)
    ).all()


@pytest.mark.asyncio
async def test_ordinary_unrelated_note_keeps_review_and_selection(
    admission_case, monkeypatch
):
    f = admission_case
    await case(f, monkeypatch)
    appointment = Appointment(
        doctor_id=f.note.doctor_id,
        patient_id=f.case.patient.id,
        appointment_date=datetime.now(UTC),
        external_id=None,
    )
    f.session.add(appointment)
    f.session.flush()
    transcript = AIVisitTranscript(
        doctor_id=f.note.doctor_id,
        patient_id=f.case.patient.id,
        appointment_id=appointment.id,
        duration=1,
        transcript_data=[],
    )
    f.session.add(transcript)
    f.session.flush()
    ordinary = AIVisitNote(
        doctor_id=f.note.doctor_id,
        appointment_id=appointment.id,
        ai_transcript_id=transcript.id,
        doctor_note_schema_id=f.note.doctor_note_schema_id,
        note={"CC": "Independent synthetic ordinary visit", "Plan": ""},
    )
    f.session.add(ordinary)
    f.session.flush()
    finalize_note_for_session_prep(
        session=f.session,
        doctor_id=ordinary.doctor_id,
        note_id=ordinary.id,
        expected_content_sha256=note_content_sha256(ordinary.note),
        observed_at=datetime.now(UTC),
    )
    f.session.commit()
    await acknowledge(f, receipt(f))
    assert not freshness.note_has_saved_capture_correction(f.session, ordinary)
    assert [s.source_ref for s in sources(f).sources] == [str(ordinary.id)]
    statuses = review_route.note_review_statuses(f.case.doctor, [ordinary])
    assert statuses is not None
    assert statuses[str(ordinary.id)].reviewed_at is not None
    finalize_note_for_session_prep(
        session=f.session,
        doctor_id=ordinary.doctor_id,
        note_id=ordinary.id,
        expected_content_sha256=note_content_sha256(ordinary.note),
        observed_at=datetime.now(UTC),
    )
    f.session.commit()


@pytest.mark.asyncio
async def test_foreign_frontier_does_not_affect_another_capture(
    admission_case, monkeypatch
):
    f = admission_case
    await case(f, monkeypatch)
    from types import SimpleNamespace

    from app.services.scribe_capture_scope import resolve_capture_binding

    from tests.authority_kernel.scribe_capture_scope_test import make_scope_case

    other_case = make_scope_case(f.session)
    binding = resolve_capture_binding(**other_case.arguments)
    other = SimpleNamespace(
        engine=f.engine,
        session=f.session,
        case=other_case,
        binding=binding,
        attestation=f.attestation,
        caller=admission.CaptureCaller(
            **{
                k: v
                for k, v in other_case.arguments.items()
                if k in admission.CaptureCaller.__dataclass_fields__
            }
        ),
        client=type(f.client)(),
    )
    await case(other, monkeypatch)
    await acknowledge(f, receipt(f))
    assert sources(other).sources
    assert not freshness.note_has_saved_capture_correction(f.session, other.note)
    assert frontier(other) is None


def prep_context(f):
    from app.models.provider_record_authority import ProviderRecordAuthority

    exchanged = authority.exchange_context_token(
        clerk_issuer=f.case.identity.issuer,
        clerk_subject=f.case.identity.subject,
        requested_surface="provider_assistant",
        record_ref=str(f.target.id),
        session=f.session,
        settings=SETTINGS,
    )
    f.session.commit()
    record = f.session.exec(
        select(ProviderRecordAuthority).where(
            ProviderRecordAuthority.appointment_id == f.target.id
        )
    ).one()
    # Retain earlier committed cases in this disposable lab; make this exact
    # target the next due build rather than accidentally testing another scope.
    from app.models.session_prep_runtime import SessionPrepBuild

    build = f.session.exec(
        select(SessionPrepBuild).where(SessionPrepBuild.record_scope_id == record.id)
    ).one_or_none()
    if build is not None:
        earliest = f.session.exec(
            select(SessionPrepBuild.requested_at)
            .order_by(col(SessionPrepBuild.requested_at))
            .limit(1)
        ).one()
        build.requested_at = earliest - timedelta(seconds=1)
        f.session.add(build)
        f.session.commit()
    return exchanged.context_token, record


def prep_action(f, token):
    from app.services.authority_conversation import write_conversation_turn

    turn_id = uuid4()
    resolved = authority.resolve_mount_context(
        workload_client_id=f.caller.workload_client_id,
        mount_context_token=token,
        client_turn_id=turn_id,
        session=f.session,
        settings=SETTINGS,
    )
    accepted = write_conversation_turn(
        workload_client_id=f.caller.workload_client_id,
        mount_context_token=token,
        interaction_ref=resolved.interaction_ref,
        client_turn_id=turn_id,
        idempotency_key="synthetic-prep-action-" + str(turn_id),
        settings=SETTINGS,
        payload=dict(
            phase="user_accepted",
            ingress_kind="provider_ui_action",
            action_code="session-prep.open",
            message=dict(content_type="text/plain", content="Open Session Prep"),
        ),
    )
    f.session.commit()
    return turn_id, resolved.interaction_ref, accepted.turn_ref


def prep_read(
    f, token, record, action, key, *, detail="brief", version="1.0.0", commit=True
):
    from app.services.session_prep import authorize_session_prep_read
    from app.services.session_prep_runtime import invoke_provider_session_prep

    turn_id, interaction_ref, turn_ref = action
    authorized = authorize_session_prep_read(
        session=f.session,
        workload_client_id=f.caller.workload_client_id,
        mount_context_token=token,
        interaction_ref=interaction_ref,
        client_turn_id=turn_id,
        source_turn_ref=turn_ref,
        settings=SETTINGS,
    )
    read = invoke_provider_session_prep(
        session=f.session,
        authorization=authorized,
        workload_client_id=f.caller.workload_client_id,
        idempotency_key=key,
        source_turn_ref=turn_ref,
        context_snapshot_ref=record.context_snapshot_ref,
        detail=detail,
        tool_version=version,
        observed_at=datetime.now(UTC),
        settings=SETTINGS,
    )
    if commit:
        f.session.commit()
    return read


def brief(selection):
    from app.services.session_prep import SessionPrepPreparedBrief

    item = dict(
        text="Review the synthetic documented concern.",
        source_refs=[selection.sources[0].source_ref],
    )
    return SessionPrepPreparedBrief.model_validate(
        dict(
            recap=item,
            trajectory_and_material_changes=[],
            talking_points=[item],
            open_threads=[],
            cautions=[],
            suggested_outline=[item],
            patient_reported=None,
        )
    )


@pytest.mark.asyncio
async def test_saved_correction_suppresses_actual_ready_artifact_and_immutable_receipt_replay(
    admission_case, monkeypatch
):
    from app.services.session_prep import SessionPrepScopeDenied
    from app.services.session_prep_worker import run_one_build

    from app.models.session_prep_runtime import SessionPrepArtifact

    f = admission_case
    await case(f, monkeypatch)
    token, record = prep_context(f)
    assert run_one_build(generate=brief, settings=SETTINGS) == "ready"
    action = prep_action(f, token)
    old = prep_read(f, token, record, action, "synthetic-prep-ready-key-0001")
    assert old.response.result.state == "ready"
    await acknowledge(f, receipt(f))
    with pytest.raises(SessionPrepScopeDenied):
        prep_read(f, token, record, action, "synthetic-prep-ready-key-0001")
    f.session.rollback()
    newer = prep_read(
        f, token, record, prep_action(f, token), "synthetic-prep-ready-key-0002"
    )
    assert (
        newer.response.result.state == "unavailable"
        and newer.response.result.prepared_brief is None
    )
    retained = f.session.exec(
        select(SessionPrepArtifact).where(
            SessionPrepArtifact.record_scope_id == record.id
        )
    ).one()
    assert retained.result_payload["state"] == "ready"


@pytest.mark.asyncio
async def test_saved_correction_during_model_call_supersedes_actual_worker_build(
    admission_case, monkeypatch
):
    from app.services.session_prep_worker import run_one_build

    from app.models.session_prep_runtime import SessionPrepArtifact, SessionPrepBuild

    f = admission_case
    await case(f, monkeypatch)
    _, record = prep_context(f)
    saved = receipt(f)

    def corrected(selection):
        freshness.retain_acknowledged_capture_correction(
            caller=f.caller,
            binding_ref=f.binding.binding_ref,
            workspace_id=f.binding.workspace_id,
            scope=f.scope,
            receipt=saved,
        )
        return brief(selection)

    assert run_one_build(generate=corrected, settings=SETTINGS) == "superseded"
    assert not f.session.exec(
        select(SessionPrepArtifact).where(
            SessionPrepArtifact.record_scope_id == record.id
        )
    ).all()
    build = f.session.exec(
        select(SessionPrepBuild).where(SessionPrepBuild.record_scope_id == record.id)
    ).one()
    assert build.state == "failed" and build.failure_code == "no_eligible_sources"


@pytest.mark.asyncio
async def test_final_source_admission_lock_serializes_ack_until_artifact_commit(
    admission_case, monkeypatch
):
    from concurrent.futures import TimeoutError as FutureTimeout
    from threading import Event

    from app.services.session_prep_runtime import current_source_state

    f = admission_case
    await case(f, monkeypatch)
    _, record = prep_context(f)
    saved = receipt(f)
    started = Event()

    def save():
        with Session(f.engine, expire_on_commit=False) as separate:
            token = _session_context.set(separate)
            try:
                started.set()
                freshness.retain_acknowledged_capture_correction(
                    caller=f.caller,
                    binding_ref=f.binding.binding_ref,
                    workspace_id=f.binding.workspace_id,
                    scope=f.scope,
                    receipt=saved,
                )
            finally:
                _session_context.reset(token)

    with (
        Session(f.engine, expire_on_commit=False) as publisher,
        ThreadPoolExecutor(max_workers=1) as pool,
    ):
        selected, before = current_source_state(
            publisher,
            record,
            observed_at=datetime.now(UTC),
            settings=SETTINGS,
            lock_capture_sources=True,
        )
        assert selected.sources
        pending = pool.submit(save)
        assert started.wait(2)
        with pytest.raises(FutureTimeout):
            pending.result(timeout=0.2)
        publisher.commit()
        pending.result(timeout=5)
        latest, after = current_source_state(
            publisher, record, observed_at=datetime.now(UTC), settings=SETTINGS
        )
        assert latest.sources == () and before != after


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation", ["backward", "scope", "same_version_edit", "delete"]
)
async def test_database_guard_preserves_frontier_against_backward_or_scope_writes(
    admission_case, monkeypatch, mutation
):
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    f = admission_case
    await case(f, monkeypatch)
    await acknowledge(f, receipt(f, 3))
    row = frontier(f)
    original = row.model_dump()
    with Session(f.engine) as separate:
        statement = (
            "UPDATE scribe_capture_correction_frontier SET minimum_working_version=2 WHERE id=:id"
            if mutation == "backward"
            else "UPDATE scribe_capture_correction_frontier SET session_id=:uuid WHERE id=:id"
            if mutation == "scope"
            else "UPDATE scribe_capture_correction_frontier SET edit_id=:uuid WHERE id=:id"
            if mutation == "same_version_edit"
            else "DELETE FROM scribe_capture_correction_frontier WHERE id=:id"
        )
        with pytest.raises(DBAPIError):
            separate.execute(text(statement), dict(id=row.id.uuid, uuid=uuid4()))
            separate.commit()
    assert frontier(f).model_dump() == original


@pytest.mark.asyncio
async def test_another_edit_cannot_claim_same_version_or_clear_saved_frontier(
    admission_case, monkeypatch
):
    f = admission_case
    await case(f, monkeypatch)
    saved = receipt(f)
    await acknowledge(f, saved)
    original = frontier(f).model_dump()
    with pytest.raises(gateway.CaptureReceiptInvalid):
        await acknowledge(f, receipt(f))
    f.session.rollback()
    assert frontier(f).model_dump() == original and sources(f).sources == ()


@pytest.mark.asyncio
async def test_missing_human_grant_cannot_record_a_successful_receipt(
    admission_case, monkeypatch
):
    f = admission_case
    await case(f, monkeypatch)
    with pytest.raises(authority.AuthorityKernelError):
        freshness.retain_acknowledged_capture_correction(
            caller=replace(f.caller, mount_context_token="context_token_not_present"),
            binding_ref=f.binding.binding_ref,
            workspace_id=f.binding.workspace_id,
            scope=f.scope,
            receipt=receipt(f),
        )
    assert frontier(f) is None and sources(f).sources


def question_action(f, token):
    from app.services.authority_conversation import write_conversation_turn

    turn_id = uuid4()
    resolved = authority.resolve_mount_context(
        workload_client_id=f.caller.workload_client_id,
        mount_context_token=token,
        client_turn_id=turn_id,
        session=f.session,
        settings=SETTINGS,
    )
    accepted = write_conversation_turn(
        workload_client_id=f.caller.workload_client_id,
        mount_context_token=token,
        interaction_ref=resolved.interaction_ref,
        client_turn_id=turn_id,
        idempotency_key="synthetic-question-action-" + str(turn_id),
        settings=SETTINGS,
        payload=dict(
            phase="user_accepted",
            ingress_kind="chat_text",
            action_code=None,
            message=dict(
                content_type="text/plain",
                content="What prior synthetic concern was documented?",
            ),
        ),
    )
    f.session.commit()
    return turn_id, resolved.interaction_ref, accepted.turn_ref


def question_read(f, token, action, version):
    from app.services.provider_question_context import (
        ProviderQuestionInvocation,
        read_provider_question_context,
    )

    return read_provider_question_context(
        session=f.session,
        workload_client_id=f.caller.workload_client_id,
        mount_context_token=token,
        interaction_ref=action[1],
        client_turn_id=action[0],
        idempotency_key="synthetic-question-read-" + str(action[0]),
        settings=SETTINGS,
        invocation=ProviderQuestionInvocation.model_validate(
            dict(
                tool_name="provider-question.context.read",
                tool_version=version,
                subject_ref=None,
                arguments=dict(
                    source_turn_ref=action[2],
                    purpose="provider_question",
                    detail="prep_summary" if version == "1.0.0" else "reviewed_sources",
                ),
            )
        ),
    )


def record_separately(f, saved, started):
    with Session(f.engine, expire_on_commit=False) as separate:
        token = _session_context.set(separate)
        try:
            started.set()
            freshness.retain_acknowledged_capture_correction(
                caller=f.caller,
                binding_ref=f.binding.binding_ref,
                workspace_id=f.binding.workspace_id,
                scope=f.scope,
                receipt=saved,
            )
        finally:
            _session_context.reset(token)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["prep", "facts", "qa1.0.0", "qa1.1.0", "qa1.2.0"])
async def test_full_canonical_receipt_commit_serializes_saved_correction(
    admission_case, monkeypatch, kind
):
    from concurrent.futures import TimeoutError as FutureTimeout
    from threading import Event

    from app.services import (
        provider_question_context as qa,
    )
    from app.services import (
        session_prep_runtime as runtime,
    )
    from app.services.authority_conversation import write_conversation_turn
    from app.services.session_prep import SessionPrepScopeDenied
    from app.services.session_prep_worker import run_one_build

    f = admission_case
    await case(f, monkeypatch)
    token, record = prep_context(f)
    assert run_one_build(generate=brief, settings=SETTINGS) == "ready"
    action = (
        question_action(f, token) if kind.startswith("qa") else prep_action(f, token)
    )
    saved, started, futures = receipt(f), Event(), []
    service = qa if kind.startswith("qa") else runtime
    original = service.ready_artifact
    with ThreadPoolExecutor(max_workers=1) as pool:

        def after_source_read(*args, **kwargs):
            if not futures:
                futures.append(pool.submit(record_separately, f, saved, started))
                assert started.wait(2)
                with pytest.raises(FutureTimeout):
                    futures[0].result(timeout=0.15)
            return original(*args, **kwargs)

        monkeypatch.setattr(service, "ready_artifact", after_source_read)
        try:
            read = (
                question_read(f, token, action, kind[2:])
                if kind.startswith("qa")
                else prep_read(
                    f,
                    token,
                    record,
                    action,
                    "synthetic-canonical-race-key-0001",
                    detail="structured_facts" if kind == "facts" else "brief",
                    version="1.1.0" if kind == "facts" else "1.0.0",
                    commit=False,
                )
            )
            result = read.response.result
            if kind == "facts":
                assert isinstance(result, runtime.SessionPrepFactsResult)
                assert result.preparation.state == "ready"
            else:
                assert result.state == "ready"
            f.session.commit()
            futures[0].result(timeout=5)
            assert frontier(f).minimum_working_version == 2
            if kind.startswith("qa"):
                with pytest.raises((qa.ProviderContextChanged, SessionPrepScopeDenied)):
                    write_conversation_turn(
                        workload_client_id=f.caller.workload_client_id,
                        mount_context_token=token,
                        interaction_ref=action[1],
                        client_turn_id=action[0],
                        idempotency_key="synthetic-final-after-correction-"
                        + str(action[0]),
                        settings=SETTINGS,
                        payload=dict(
                            phase="final_response",
                            outcome="completed",
                            provider_context_ref=read.response.tool_call_ref,
                            response=dict(
                                content_type="text/plain",
                                content="Fictional bounded answer.",
                            ),
                            presentation=dict(
                                mode="standard", suppress=[], human_contact=None
                            ),
                        ),
                    )
        finally:
            f.session.rollback()


@pytest.mark.asyncio
async def test_busy_source_admission_refuses_without_aborting_surrounding_transaction(
    admission_case, monkeypatch
):
    from app.services.session_prep import SessionPrepScopeDenied
    from app.services.session_prep_runtime import current_source_state

    from sqlalchemy import text

    f = admission_case
    await case(f, monkeypatch)
    _, record = prep_context(f)
    with Session(f.engine) as holder, Session(f.engine) as reader:
        holder.exec(
            select(ScribeCaptureAdmission)
            .where(ScribeCaptureAdmission.id == f.scope.operation_ref)
            .with_for_update()
        ).one()
        with pytest.raises(SessionPrepScopeDenied):
            current_source_state(
                reader,
                record,
                observed_at=datetime.now(UTC),
                settings=SETTINGS,
                lock_capture_sources=True,
            )
        assert reader.execute(text("SELECT 1")).scalar_one() == 1
        reader.rollback()
        holder.rollback()


@pytest.mark.asyncio
async def test_actual_worker_nowait_contention_keeps_transaction_and_bounded_retry(
    admission_case, monkeypatch
):
    from app.services.session_prep_worker import run_one_build

    from app.models.session_prep_runtime import SessionPrepArtifact, SessionPrepBuild

    f = admission_case
    await case(f, monkeypatch)
    _, record = prep_context(f)
    now = datetime.now(UTC)
    with Session(f.engine) as correction_holder:
        correction_holder.exec(
            select(ScribeCaptureAdmission)
            .where(ScribeCaptureAdmission.id == f.scope.operation_ref)
            .with_for_update()
        ).one()
        assert (
            run_one_build(generate=brief, settings=SETTINGS, clock=lambda: now)
            == "failed"
        )
        f.session.expire_all()
        build = f.session.exec(
            select(SessionPrepBuild).where(
                SessionPrepBuild.record_scope_id == record.id
            )
        ).one()
        assert (
            build.state == "pending"
            and build.failure_code == "source_busy"
            and build.lease_until is None
        )
        assert not f.session.exec(
            select(SessionPrepArtifact).where(
                SessionPrepArtifact.record_scope_id == record.id
            )
        ).all()
        correction_holder.rollback()
    assert (
        run_one_build(
            generate=brief, settings=SETTINGS, clock=lambda: now + timedelta(seconds=16)
        )
        == "ready"
    )
    f.session.expire_all()
    assert f.session.get(SessionPrepBuild, build.id).failure_code is None


@pytest.mark.asyncio
async def test_ordinary_legacy_admission_keeps_its_existing_receipt_contract(
    admission_case,
):
    f = admission_case
    admitted = await admission.admit_capture(
        caller=f.caller,
        binding_ref=f.binding.binding_ref,
        workspace_id=f.binding.workspace_id,
        client_request_key="synthetic-legacy-admission-" + uuid4().hex,
        attestation=f.attestation,
        client=f.client,
    )
    f.scope = authorize_admitted_capture(
        f.caller, f.binding.binding_ref, f.binding.workspace_id
    )
    assert f.scope.operation_ref == admitted.operation_ref
    f.view = dict(
        working=dict(
            working_transcript_id=str(uuid4()), segments=[dict(speaker_id=str(uuid4()))]
        )
    )
    saved = receipt(f)
    assert await acknowledge(f, saved) == saved
    assert frontier(f) is None
