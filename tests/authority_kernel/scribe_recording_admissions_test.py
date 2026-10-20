"""Distinct saved clips retain exact consent/session identity in one invented visit."""

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TypedDict
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from pydantic import ValidationError
from typeid import TypeID

from app.configuration import database
from app.routes.scribe_capture import AdmissionRequest, OperationRequest
from app.services import scribe_capture_admission as admission
from app.services import scribe_capture_notes as notes
from app.services.scribe_configured_capture_contract import (
    ConfiguredCaptureSelection,
    ConfiguredSessionReceipt,
)
from app.services.scribe_creator_review_contract import CreatorReviewView
from app.services.scribe_workflow import ScribeWorkflowClient

from activemodel.session_manager import _serialize_pydantic_model, _session_context
from app.models.ai_visit_note import AIVisitNote
from app.models.doctor_note_schema import DoctorNoteSchema
from app.models.scribe_capture_admission import ScribeCaptureAdmission
from app.models.scribe_capture_reviewed_source import ScribeCaptureReviewedSource
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlmodel import Session, create_engine, select

from tests.authority_kernel.scribe_capture_admission_test import (
    CaptureAdmissionArguments,
    call_arguments,
)
from tests.authority_kernel.scribe_capture_notes_test import FakeModel
from tests.authority_kernel.scribe_configured_capture_test import (
    SELECTION,
    owned_url,
    reviewed_document,
)
from tests.authority_kernel.scribe_configured_capture_test import (
    admission_case as _admission_case,
)
from tests.authority_kernel.scribe_configured_capture_test import (
    capture_engine as _capture_engine,
)
from tests.authority_kernel.scribe_configured_capture_test import (
    configured_engine as _configured_engine,
)

admission_case = _admission_case
capture_engine = _capture_engine
configured_engine = _configured_engine


@pytest.mark.asyncio
async def test_expand_preserves_old_journal_and_its_retry_identity(monkeypatch):
    from types import SimpleNamespace

    from app.services.scribe_capture_scope import resolve_capture_binding

    from tests.authority_kernel.scribe_capture_admission_test import FakeScribe
    from tests.authority_kernel.scribe_capture_scope_test import make_scope_case

    url = owned_url("scribe_configured_retained_" + uuid4().hex[:16] + "_test")
    assert url.database is not None
    admin = create_engine(owned_url("postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text('CREATE DATABASE "' + url.database + '"'))
    admin.dispose()
    monkeypatch.setattr(
        database, "database_url", lambda: url.render_as_string(hide_password=False)
    )
    command.upgrade(Config("alembic.ini"), "56789df3c14c")
    engine = create_engine(url, json_serializer=_serialize_pydantic_model)
    with Session(engine, expire_on_commit=False) as session:
        context = _session_context.set(session)
        try:
            case = make_scope_case(session)
            binding = resolve_capture_binding(**case.arguments)
            caller = admission.CaptureCaller(
                **{
                    k: v
                    for k, v in case.arguments.items()
                    if k in admission.CaptureCaller.__dataclass_fields__
                }
            )
            attestation = admission.ConsentAttestation(
                modality="in_person",
                participant_regions=frozenset({"US-NY"}),
                method="verbal_attestation",
                all_parties_consented=True,
                granted_at=datetime.now(UTC),
            )
            bound = caller.authorize(session, binding.binding_ref, binding.workspace_id)
            payload = admission._document(
                admission._consent_payload(bound, attestation)
            )
            key = uuid4().hex

            document = {
                "id": str(TypeID("scribe_admission").uuid),
                "created_at": datetime.now(UTC).isoformat(),
                "updated_at": datetime.now(UTC).isoformat(),
                "binding_id": str(bound.id.uuid),
                "scribe_tenant_id": str(bound.scribe_tenant_id),
                "request_sha256": admission._digest(bound, payload),
                "client_key_sha256": hashlib.sha256(key.encode()).hexdigest(),
                "consent_key": str(uuid4()),
                "session_key": str(uuid4()),
                "consent_payload": payload,
                "status": "consent_pending",
            }
            session.execute(
                text(
                    "INSERT INTO scribe_capture_admission SELECT * FROM jsonb_populate_record(NULL::scribe_capture_admission, CAST(:document AS jsonb))"
                ),
                {"document": json.dumps(document)},
            )
            session.commit()
            before = session.execute(
                text("SELECT to_jsonb(a) FROM scribe_capture_admission a")
            ).scalar_one()
            session.commit()
            command.upgrade(Config("alembic.ini"), "head")
            after = session.execute(
                text("SELECT to_jsonb(a) FROM scribe_capture_admission a")
            ).scalar_one()
            assert after.pop("recording_key_sha256") == "legacy" and after == before
            session.commit()
            f = SimpleNamespace(
                caller=caller,
                binding=binding,
                attestation=attestation,
                client=FakeScribe(),
            )
            result = await admission.admit_capture(
                **{**call_arguments(f), "client_request_key": key}
            )
            saved = session.exec(select(ScribeCaptureAdmission)).one()
            assert (
                str(saved.consent_key) == before["consent_key"]
                and str(saved.session_key) == before["session_key"]
            )
            assert (
                saved.request_sha256 == before["request_sha256"]
                and result.session_id == saved.session_id
            )
        finally:
            _session_context.reset(context)
    engine.dispose()


def install_receiver(f):
    async def create(tenant, payload, *, creator_actor_id, idempotency_key):
        assert creator_actor_id == str(f.case.actor.id)
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


def rows(f):
    with Session(f.engine) as s:
        return s.exec(
            select(ScribeCaptureAdmission).where(
                ScribeCaptureAdmission.binding_id == f.binding.binding_ref
            )
        ).all()


class CaptureNoteArguments(TypedDict):
    caller: admission.CaptureCaller
    binding_ref: str
    workspace_id: UUID
    schema_ref: str
    client: ScribeWorkflowClient


class RecordingAdmissionArguments(CaptureAdmissionArguments):
    configured_capture: ConfiguredCaptureSelection
    recording_key_sha256: str


def args(f, key) -> RecordingAdmissionArguments:
    return {
        **call_arguments(f),
        "configured_capture": SELECTION,
        "recording_key_sha256": key,
    }


@pytest.mark.asyncio
async def test_legacy_and_two_clips_keep_three_independent_sessions(admission_case):
    f = admission_case
    install_receiver(f)
    legacy = await admission.admit_capture(
        **call_arguments(f), configured_capture=SELECTION
    )
    first = await admission.admit_capture(**args(f, "a" * 64))
    second = await admission.admit_capture(**args(f, "b" * 64))
    assert len({legacy.session_id, first.session_id, second.session_id}) == 3
    assert len({r.consent_key for r in rows(f)}) == 3
    assert len({r.session_key for r in rows(f)}) == 3
    assert (
        admission.authorize_admitted_capture(
            f.caller, f.binding.binding_ref, f.binding.workspace_id
        ).session_id
        == legacy.session_id
    )
    for saved in (first, second):
        resumed = await admission.admit_capture(
            **{
                **call_arguments(f, resume=True),
                "expected_session_id": saved.session_id,
            }
        )
        assert resumed == saved
        scoped = replace(f.caller, selected_session_id=saved.session_id)
        assert (
            admission.authorize_admitted_capture(
                scoped, f.binding.binding_ref, f.binding.workspace_id
            ).session_id
            == saved.session_id
        )
    with pytest.raises(admission.AdmissionConflict):
        admission.authorize_admitted_capture(
            replace(f.caller, selected_session_id=uuid4()),
            f.binding.binding_ref,
            f.binding.workspace_id,
        )
    assert len(f.client.sessions) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["consent", "session"])
async def test_lost_reply_same_source_reuses_original_keys(admission_case, phase):
    f = admission_case
    install_receiver(f)
    f.client.lose_response.add(phase)
    request = args(f, "c" * 64)
    with pytest.raises(admission.AdmissionUnconfirmed):
        await admission.admit_capture(**request)
    before = rows(f)[0]
    with pytest.raises(admission.AdmissionConflict):
        await admission.admit_capture(
            **{
                **request,
                "attestation": f.attestation.model_copy(
                    update={
                        "granted_at": f.attestation.granted_at + timedelta(seconds=1)
                    }
                ),
            }
        )
    result = await admission.admit_capture(**{**request, "attestation": None})
    after = rows(f)[0]
    assert (
        before.id == after.id
        and before.consent_key == after.consent_key
        and before.session_key == after.session_key
    )
    assert result.session_id == after.session_id and len(f.client.sessions) == 1
    assert len(f.client.consents) == 1


@pytest.mark.asyncio
async def test_expired_new_clip_has_no_journal_or_remote_call(admission_case):
    f = admission_case
    install_receiver(f)
    await admission.admit_capture(**args(f, "d" * 64))
    before = list(f.client.calls)
    with pytest.raises(admission.AdmissionRecordingConsentRequired):
        await admission.admit_capture(
            **{
                **args(f, "e" * 64),
                "client_request_key": "saved:" + "e" * 64,
                "recover_legacy_request": True,
                "attestation": f.attestation.model_copy(
                    update={"granted_at": datetime.now(UTC) - timedelta(hours=1)}
                ),
            }
        )
    assert len(rows(f)) == 1 and f.client.calls == before
    # A later explicit attempt starts this previously unadmitted recording.
    await admission.admit_capture(**args(f, "e" * 64))
    assert len(rows(f)) == 2


@pytest.mark.asyncio
async def test_canonical_old_saved_request_recovers_without_duplicate(admission_case):
    f = admission_case
    install_receiver(f)
    source = "7" * 64
    key = "saved:" + source
    old = {
        **call_arguments(f),
        "configured_capture": SELECTION,
        "client_request_key": key,
    }
    f.client.lose_response.add("session")
    with pytest.raises(admission.AdmissionUnconfirmed):
        await admission.admit_capture(**old)
    snapshot = rows(f)[0]
    recovered = await admission.admit_capture(
        **old, recording_key_sha256=source, recover_legacy_request=True
    )
    assert len(rows(f)) == len(f.client.sessions) == 1 and recovered.session_id in {
        s.id for s in f.client.sessions.values()
    }
    assert (
        rows(f)[0].recording_key_sha256 == "legacy"
        and rows(f)[0].session_key == snapshot.session_key
    )
    changed = {
        **old,
        "attestation": f.attestation.model_copy(
            update={"granted_at": datetime.now(UTC)}
        ),
    }
    with pytest.raises(admission.AdmissionConflict):
        await admission.admit_capture(
            **changed, recording_key_sha256=source, recover_legacy_request=True
        )
    another = {
        **args(f, "8" * 64),
        "client_request_key": "saved:" + "8" * 64,
        "recover_legacy_request": True,
    }
    await admission.admit_capture(**another)
    assert len(rows(f)) == 2


@pytest.mark.asyncio
async def test_recording_identity_cannot_be_rewritten(admission_case):
    f = admission_case
    install_receiver(f)
    await admission.admit_capture(**args(f, "f" * 64))
    row = rows(f)[0]
    with Session(f.engine) as s:
        with pytest.raises(
            DBAPIError, match="recording admission identity is immutable"
        ):
            s.execute(
                text(
                    "UPDATE scribe_capture_admission SET recording_key_sha256=:key WHERE id=:id"
                ),
                {"key": "a" * 64, "id": row.id.uuid},
            )
            s.commit()


@pytest.mark.asyncio
async def test_two_clip_version_one_notes_reopen_their_own_content(admission_case):
    f = admission_case
    install_receiver(f)
    one = await admission.admit_capture(**args(f, "1" * 64))
    two = await admission.admit_capture(**args(f, "2" * 64))
    views = {
        one.session_id: reviewed_document(one.session_id),
        two.session_id: reviewed_document(two.session_id),
    }

    async def read(tenant, session, operation, **kwargs):
        assert operation.kind == "creator_review"
        assert kwargs["creator_actor_id"] == str(f.case.actor.id)
        return CreatorReviewView.model_validate(views[session])

    f.client.capture_operation = read
    caller = replace(
        f.caller,
        capture_settings=replace(f.caller.capture_settings, notes_enabled=True),
    )
    with Session(f.engine) as s:
        schema = DoctorNoteSchema(
            doctor_id=f.case.doctor.id,
            note_type="Synthetic clip",
            fields={"CC": "Chief complaint", "Plan": "Explicit plan"},
        )
        s.add(schema)
        s.commit()
        schema_ref = str(schema.id)
    common: CaptureNoteArguments = {
        "caller": caller,
        "binding_ref": f.binding.binding_ref,
        "workspace_id": f.binding.workspace_id,
        "schema_ref": schema_ref,
        "client": f.client,
    }
    model = FakeModel()
    model.output = '{"CC":"First synthetic clip","Plan":""}'
    first = await notes.capture_note(
        **common, expected_session_id=one.session_id, prepare=True, model=model
    )
    model.output = '{"CC":"Second synthetic clip","Plan":""}'
    second = await notes.capture_note(
        **common, expected_session_id=two.session_id, prepare=True, model=model
    )
    assert first.fields is not None and second.fields is not None
    assert first.note_ref != second.note_ref
    assert (
        first.fields["CC"] == "First synthetic clip"
        and second.fields["CC"] == "Second synthetic clip"
    )
    reopened = await notes.capture_note(
        **common, expected_session_id=one.session_id, prepare=False
    )
    assert reopened.note_ref == first.note_ref and reopened.fields == first.fields
    with Session(f.engine) as s:
        mappings = s.exec(
            select(ScribeCaptureReviewedSource).where(
                ScribeCaptureReviewedSource.binding_id == f.binding.binding_ref
            )
        ).all()
        assert len(mappings) == 2 and {r.working_version for r in mappings} == {1}
        old = s.get(AIVisitNote, first.note_ref)
        assert old is not None
        assert old.note == first.fields and old.superseded_at is not None


def test_admission_selector_rejects_ambiguous_resume():
    base = {
        "binding_ref": "scribe_capture_binding_01j00000000000000000000000",
        "workspace_id": uuid4(),
        "request_nonce": uuid4(),
        "recording_key_sha256": "a" * 64,
    }
    for extra in (
        {"resume_current": True},
        {"expected_session_id": uuid4()},
        {"recording_key_sha256": "bad"},
    ):
        with pytest.raises(ValidationError):
            AdmissionRequest.model_validate({**base, **extra})
    assert (
        OperationRequest.model_validate(
            {
                k: v
                for k, v in {
                    **base,
                    "operation": {"kind": "capabilities"},
                    "expected_session_id": uuid4(),
                }.items()
                if k != "recording_key_sha256"
            }
        ).expected_session_id
        is not None
    )


@pytest.mark.asyncio
async def test_duplicate_concurrent_clip_claim_has_one_remote_session(admission_case):
    import asyncio

    f = admission_case
    install_receiver(f)
    request = args(f, "3" * 64)
    entered = asyncio.Event()
    release = asyncio.Event()
    original = f.client.create_consent

    async def blocked(*a, **kw):
        entered.set()
        await release.wait()
        return await original(*a, **kw)

    f.client.create_consent = blocked
    first = asyncio.create_task(admission.admit_capture(**request))
    await entered.wait()
    with pytest.raises(admission.AdmissionInProgress):
        await admission.admit_capture(**request)
    release.set()
    await first
    assert len(rows(f)) == len(f.client.sessions) == len(f.client.consents) == 1


@pytest.mark.asyncio
async def test_revoked_selected_session_cannot_fall_back_to_other_clip(admission_case):
    f = admission_case
    install_receiver(f)
    first = await admission.admit_capture(**args(f, "4" * 64))
    second = await admission.admit_capture(**args(f, "5" * 64))
    record = next(
        v for v in f.client.consents.values() if v.id == first.consent_record_id
    )
    f.client.consents[
        next(k for k, v in f.client.consents.items() if v.id == record.id)
    ] = record.model_copy(update={"revoked_at": datetime.now(UTC)})
    # Configured receiver rejects current revoked consent just as Scribe does.
    original = f.client.create_configured_session

    async def checked(tenant, payload, **kwargs):
        consent = next(
            v for v in f.client.consents.values() if v.id == payload.consent_record_id
        )
        if consent.revoked_at is not None:
            from app.services.scribe_workflow import ScribeWorkflowRejectedError

            raise ScribeWorkflowRejectedError("synthetic_revoked")
        return await original(tenant, payload, **kwargs)

    f.client.create_configured_session = checked
    with pytest.raises(admission.AdmissionUnconfirmed):
        await admission.admit_capture(
            **{
                **call_arguments(f, resume=True),
                "expected_session_id": first.session_id,
            }
        )
    resumed = await admission.admit_capture(
        **{**call_arguments(f, resume=True), "expected_session_id": second.session_id}
    )
    assert resumed.session_id == second.session_id and len(f.client.sessions) == 2


@pytest.mark.asyncio
async def test_wrong_workspace_selected_session_denied_before_remote(admission_case):
    from app.services.scribe_capture_scope import CaptureScopeDenied

    f = admission_case
    install_receiver(f)
    first = await admission.admit_capture(**args(f, "6" * 64))
    before = list(f.client.calls)
    with pytest.raises(CaptureScopeDenied):
        await admission.admit_capture(
            **{
                **call_arguments(f, resume=True),
                "workspace_id": uuid4(),
                "expected_session_id": first.session_id,
            }
        )
    assert f.client.calls == before and len(rows(f)) == 1
