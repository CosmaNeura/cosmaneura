"""Approved local configured admission/SOAP packet; no live services."""

import os
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from typeid import TypeID

from app.configuration import database
from app.services import authority, view_as
from app.services import scribe_capture_admission as admission
from app.services import scribe_capture_note_edits as edits
from app.services import scribe_capture_notes as notes
from app.services.scribe_configured_capture_contract import (
    ConfiguredCaptureSelection,
    ConfiguredSessionReceipt,
)
from app.services.scribe_creator_review_contract import CreatorReviewView
from app.services.scribe_workflow import ScribeWorkflowRejectedError

from activemodel.session_manager import _serialize_pydantic_model
from app.models.ai_visit_note import AIVisitNote
from app.models.ai_visit_transcript import AIVisitTranscript
from app.models.authority import AuthorityActorExternalIdentity
from app.models.doctor_note_schema import DoctorNoteSchema
from app.models.scribe_capture_reviewed_source import ScribeCaptureReviewedSource
from sqlalchemy import inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlmodel import Session, create_engine, select

from tests.authority_kernel.scribe_capture_admission_test import (
    admission_case as _admission_case,
)
from tests.authority_kernel.scribe_capture_admission_test import (
    call_arguments,
    journal,
)
from tests.authority_kernel.scribe_capture_notes_test import FakeModel, rows
from tests.authority_kernel.scribe_capture_scope_test import REAL_VIEW_AS_GUARD
from tests.authority_kernel.scribe_creator_review_test import view_document

SELECTION = ConfiguredCaptureSelection(
    schema_version="1.0", language_mode="en-US+es-US"
)
admission_case = _admission_case


def owned_url(name):
    raw = os.getenv("SCRIBE_CONFIGURED_CORE_TEST_URL")
    if not raw:
        pytest.skip("Explicit owned configured-capture test database required")
    url = make_url(raw)
    socket_host = url.query.get("host", "")
    private_scope = (
        url.database == "scribe_configured_core_test"
        and url.username == "scribe_configured_owner"
        and url.host is None
        and isinstance(socket_host, str)
        and socket_host.startswith("/private/tmp/scribe-configured-core.")
        and url.query.get("port") == "55458"
    )
    operational_beta_scope = url.database in {
        "scribe_configured_core_test",
        "staging_release_configured_test",
    } and (url.host, url.port, url.username) == ("127.0.0.1", 32768, "beta_local")
    assert private_scope or operational_beta_scope
    if url.database == "staging_release_configured_test":
        name = name.replace("scribe_configured_core", "staging_release_configured")
    return url.set(database=name)


@pytest.fixture(scope="module")
def configured_engine():
    url = owned_url("scribe_configured_core_test")
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(
            database,
            "database_url",
            lambda: url.render_as_string(hide_password=False).replace("%", "%%"),
        )
        command.upgrade(Config("alembic.ini"), "head")
    engine = create_engine(url, json_serializer=_serialize_pydantic_model)
    yield engine
    engine.dispose()


@pytest.fixture
def capture_engine(configured_engine, monkeypatch):
    monkeypatch.setattr(
        view_as, "mount_context_session_blocked_by_view_as", REAL_VIEW_AS_GUARD
    )
    return configured_engine


def install_configured(f):
    f.configured_calls = []

    async def create(tenant, payload, *, creator_actor_id, idempotency_key):
        row = journal(f)
        assert row.configured_capture is not None
        assert row.configured_capture["id"] == str(payload.id)
        assert row.configured_capture is not None
        assert (
            row.configured_capture["creator_actor_id"]
            == creator_actor_id
            == str(f.case.actor.id)
        )
        assert row.lease_phase == "session"
        f.configured_calls.append(
            (tenant, payload.model_dump(mode="json"), creator_actor_id, idempotency_key)
        )
        if idempotency_key not in f.client.sessions:
            f.client.sessions[idempotency_key] = ConfiguredSessionReceipt(
                **payload.model_dump(),
                creator_actor_id=creator_actor_id,
                ingest_mode="chunk_upload",
                transcript_contract_version="1.0",
                transcription_adapter_id="azure_ai_speech",
                transcription_model_profile_id="azure_fast_transcription_en_es_v1",
                normalization_profile_id="azure_phrase_en_es_evidence_v1",
                status="admitted",
                created_at=datetime.now(UTC),
            )
        return f.client._returned("session", f.client.sessions[idempotency_key])

    f.client.create_configured_session = create


async def admit(
    f, *, resume=False, selection: ConfiguredCaptureSelection | None = SELECTION
):
    return await admission.admit_capture(
        **call_arguments(f, resume=resume), configured_capture=selection
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["consent", "session"])
async def test_configured_reservation_survives_lost_reply_and_rejects_changed_retry(
    admission_case, phase
):
    f = admission_case
    install_configured(f)
    f.client.lose_response.add(phase)
    with pytest.raises(admission.AdmissionUnconfirmed):
        await admit(f)
    configured_capture = journal(f).configured_capture
    assert configured_capture is not None
    reservation = configured_capture.copy()
    for selection in (
        None,
        ConfiguredCaptureSelection(schema_version="1.0", language_mode="en-US"),
    ):
        with pytest.raises(admission.AdmissionConflict):
            await admit(f, resume=True, selection=selection)
    saved = await admit(f, resume=True)
    assert saved == await admit(f, resume=True)
    assert str(saved.session_id) == reservation["id"]
    assert journal(f).configured_capture == reservation
    assert all(call == f.configured_calls[0] for call in f.configured_calls)
    assert len(f.client.sessions) == len(f.client.consents) == 1
    with (
        Session(f.engine) as session,
        pytest.raises(DBAPIError, match="configured admission is immutable"),
    ):
        session.execute(
            text(
                "UPDATE scribe_capture_admission SET configured_capture = NULL WHERE id = :id"
            ),
            {"id": TypeID.from_string(saved.operation_ref).uuid},
        )
        session.commit()


@pytest.mark.asyncio
async def test_legacy_admission_cannot_convert(admission_case):
    f = admission_case
    await admission.admit_capture(**call_arguments(f))
    with pytest.raises(admission.AdmissionConflict):
        await admit(f, resume=True)
    assert journal(f).configured_capture is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("creator_actor_id", "wrong_creator"),
        ("id", uuid4()),
        ("language_mode", "en-US"),
        ("transcription_adapter_id", "unexpected"),
    ],
)
async def test_mismatched_configured_receipt_is_never_admitted(
    admission_case, field, value
):
    f = admission_case
    install_configured(f)
    f.client.mutate = lambda phase, receipt: (
        receipt.model_copy(update={field: value}) if phase == "session" else receipt
    )
    with pytest.raises(admission.AdmissionUnconfirmed):
        await admit(f)
    assert journal(f).session_id is None


@pytest.mark.asyncio
async def test_new_reviewed_version_preserves_old_source_and_requires_new_note(
    admission_case,
):
    f = admission_case
    kwargs = await note_setup(f)
    first = await notes.capture_note(**kwargs)
    f.view["working"]["working_transcript_version"] += 1
    f.view["review_queue"]["working_transcript_version"] += 1
    f.view["working"]["segments"][0].update(
        content="I have not had a fever.",
        translation_version=2,
        translation_id=str(uuid4()),
        translation_review_id=str(uuid4()),
    )
    current = await notes.capture_note(**{**kwargs, "prepare": False})
    assert current.status != "draft" and current.fields is None
    second = await notes.capture_note(**kwargs)
    assert second.note_ref != first.note_ref and second.review_required
    assert second.reviewed_source is not None
    assert second.reviewed_source.working_version == 2
    assert len(rows(f, AIVisitTranscript)) == len(rows(f, AIVisitNote)) == 2
    assert len(f.model.calls) == 4


def reviewed_document(session_id):
    view = view_document(session_id)
    view["source"].update(
        transcription_adapter_id="azure_ai_speech",
        transcription_model_profile_id="azure_fast_transcription_en_es_v1",
        normalization_profile_id="azure_phrase_en_es_evidence_v1",
    )
    item = view["review_queue"]["items"][0]
    view["status"] = "transcript_ready"
    view["working"]["note_ready"] = True
    view["working"]["blockers"] = []
    view["working"]["segments"][0].update(
        resolution="spanish_translation",
        content=item["translated_content"],
        translation_id=item["translation_id"],
        translation_version=item["translation_version"],
        translation_review_id=str(uuid4()),
    )
    view["review_queue"].update(status="complete", items=[])
    return view


async def note_setup(f) -> dict[str, Any]:
    install_configured(f)
    await admit(f)
    scope = admission.authorize_admitted_capture(
        f.caller, f.binding.binding_ref, f.binding.workspace_id
    )
    f.view = reviewed_document(scope.session_id)

    async def read(tenant, session, operation, **kwargs):
        assert operation.kind == "creator_review"
        assert kwargs["creator_actor_id"] == str(f.case.actor.id)
        if getattr(f, "revoked", False):
            raise ScribeWorkflowRejectedError("synthetic protected sentinel")
        return CreatorReviewView.model_validate(f.view)

    f.client.capture_operation = read
    f.caller = replace(
        f.caller,
        capture_settings=replace(f.caller.capture_settings, notes_enabled=True),
    )
    with Session(f.engine) as session:
        schema = DoctorNoteSchema(
            doctor_id=f.case.doctor.id,
            note_type="Synthetic visit",
            fields={"CC": "Chief complaint", "Plan": "Explicitly discussed plan"},
        )
        session.add(schema)
        session.commit()
        schema_ref = str(schema.id)
    f.model = FakeModel()
    return dict(
        caller=f.caller,
        binding_ref=f.binding.binding_ref,
        workspace_id=f.binding.workspace_id,
        expected_session_id=scope.session_id,
        schema_ref=schema_ref,
        prepare=True,
        client=f.client,
        model=f.model,
    )


@pytest.mark.asyncio
async def test_reviewed_english_import_note_edit_and_lost_reply_recovery(
    admission_case,
):
    f = admission_case
    kwargs = await note_setup(f)
    assert (
        await notes.capture_note(**{**kwargs, "prepare": False})
    ).status == "not_prepared"
    first = await notes.capture_note(**kwargs)
    assert first == await notes.capture_note(**kwargs)
    assert first.reviewed_source is not None
    assert first.review_required and first.reviewed_source.kind == "reviewed_english"
    assert len(f.model.calls) == 2
    assert all("No tengo fiebre" not in prompt for prompt, _ in f.model.calls)
    assert all("I do not have a fever." in prompt for prompt, _ in f.model.calls)
    transcript = rows(f, AIVisitTranscript)[0]
    assert transcript.transcript_data[0].content == "I do not have a fever."
    with Session(f.engine) as session:
        mapping = session.exec(
            select(ScribeCaptureReviewedSource).where(
                ScribeCaptureReviewedSource.binding_id == f.binding.binding_ref
            )
        ).one()
        assert str(mapping.transcript_id) == first.transcript_ref
        with pytest.raises(DBAPIError, match="reviewed source mapping is immutable"):
            session.execute(
                text(
                    "UPDATE scribe_capture_reviewed_source SET source_version = 5 WHERE id = :id"
                ),
                {"id": mapping.id.uuid},
            )
            session.commit()
    request = {k: v for k, v in kwargs.items() if k not in ("prepare", "model")}
    assert first.note_ref is not None
    assert first.note_sha256 is not None
    edit = edits.NoteEdit(
        client_key=uuid4(),
        expected_note_ref=first.note_ref,
        expected_note_sha256=first.note_sha256,
        expected_source_sha256=first.source_sha256,
        fields={"CC": "Fever denied", "Plan": "Clinician synthetic correction."},
    )
    saved = await edits.save_capture_note(**request, edit=edit)
    assert saved == await edits.save_capture_note(**request, edit=edit)
    assert saved == await notes.capture_note(**request, prepare=False)
    assert saved.review_required and len(f.model.calls) == 2
    assert len(rows(f, AIVisitTranscript)) == 1 and len(rows(f, AIVisitNote)) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["pending", "source_changed", "revoked", "identity"])
async def test_unready_or_changed_source_cannot_publish_note(admission_case, fault):
    f = admission_case
    kwargs = await note_setup(f)
    if fault == "pending":
        f.view = view_document(kwargs["expected_session_id"])
    else:

        def change(count):
            if count != 1:
                return
            if fault == "source_changed":
                f.view["working"]["segments"][0]["content"] = (
                    "Changed English proposal."
                )
            elif fault == "revoked":
                f.revoked = True
            else:
                with Session(f.engine) as session:
                    identity = session.get(
                        AuthorityActorExternalIdentity, f.case.identity.id
                    )
                    assert identity is not None
                    identity.revoked_at = datetime.now(UTC)
                    session.add(identity)
                    session.commit()

        f.model.after = change
    from app.services.scribe_capture_gateway import CaptureOperationUnavailable

    with pytest.raises(
        (
            notes.CaptureNoteConflict,
            CaptureOperationUnavailable,
            authority.AuthorityKernelError,
        )
    ):
        await notes.capture_note(**kwargs)
    assert not rows(f, AIVisitNote)
    assert len(f.model.calls) == (0 if fault == "pending" else 1)


def test_fresh_and_incremental_migration_match(configured_engine):
    name = "scribe_configured_incremental_" + uuid4().hex[:16] + "_test"
    assert len(name) <= 63 and name.endswith("_test")
    url = owned_url(name)
    admin = create_engine(owned_url("postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as connection:
            connection.execute(text('CREATE DATABASE "' + name + '"'))
    finally:
        admin.dispose()
    engine = create_engine(url, json_serializer=_serialize_pydantic_model)
    try:
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(
                database,
                "database_url",
                lambda: url.render_as_string(hide_password=False).replace("%", "%%"),
            )
            command.upgrade(Config("alembic.ini"), "9172abd84a3d")
            assert "configured_capture" not in {
                c["name"]
                for c in inspect(engine).get_columns("scribe_capture_admission")
            }
            command.upgrade(Config("alembic.ini"), "head")
        assert set(inspect(engine).get_table_names()) == set(
            inspect(configured_engine).get_table_names()
        )
        for table in ("scribe_capture_admission", "scribe_capture_reviewed_source"):

            def columns(target):
                return [
                    (c["name"], str(c["type"]), c["nullable"], c["default"])
                    for c in inspect(target).get_columns(table)
                ]

            assert columns(engine) == columns(configured_engine)
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
                == "56f2b01ac226"
            )
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_database_rejects_reviewed_source_after_binding_revocation(
    admission_case,
):
    f = admission_case
    kwargs = await note_setup(f)
    await notes.capture_note(**kwargs)
    with Session(f.engine) as session:
        old = session.exec(
            select(ScribeCaptureReviewedSource).where(
                ScribeCaptureReviewedSource.binding_id == f.binding.binding_ref
            )
        ).one()
        prior = session.get(AIVisitTranscript, old.transcript_id)
        assert prior is not None
        extra = AIVisitTranscript(
            doctor_id=prior.doctor_id,
            patient_id=prior.patient_id,
            appointment_id=prior.appointment_id,
            duration=prior.duration,
            transcript_data=prior.transcript_data,
        )
        session.add(extra)
        session.commit()
        session.refresh(extra)
        mapping = ScribeCaptureReviewedSource(
            **{
                **old.model_dump(exclude={"id", "created_at", "updated_at"}),
                "working_version": old.working_version + 1,
                "transcript_id": extra.id,
            }
        )
        session.execute(
            text("UPDATE scribe_capture_binding SET revoked_at=now() WHERE id=:id"),
            {"id": TypeID.from_string(f.binding.binding_ref).uuid},
        )
        session.commit()
        with pytest.raises(DBAPIError, match="reviewed source binding unavailable"):
            session.add(mapping)
            session.commit()
