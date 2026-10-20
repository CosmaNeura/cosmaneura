"""Actual native router/Core/SQL; invented identity and downstream Scribe only."""

from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from typeid import TypeID

from app.configuration.scribe_capture import ScribeCaptureSettings
from app.configuration.scribe_encounters import ScribeEncounterSettings
from app.routes import scribe_native_capture as route
from app.routes.dependencies.entra_service import EntraServicePrincipal
from app.routes.embed_context import VerifiedClerkIdentity
from app.services import provider_context_exchange, scribe_encounters
from app.services import scribe_capture_admission as admission
from app.services import scribe_native_capture as service
from app.services.scribe_configured_capture_contract import (
    ConfiguredCaptureSelection,
    ConfiguredSessionReceipt,
)
from app.services.scribe_creator_review_contract import CreatorReviewView

from activemodel import session_manager
from app.models.ai_visit_note import AIVisitNote
from app.models.ai_visit_transcript import AIVisitTranscript
from app.models.appointment import Appointment
from app.models.authority import AuthorityActorExternalIdentity
from app.models.doctor_note_schema import DoctorNoteSchema
from app.models.scribe_capture_binding import (
    ScribeCaptureBinding,
    ScribeCaptureProviderLink,
)
from app.models.superadmin_capability import SuperadminCapabilityEnvironment
from sqlalchemy import event
from sqlmodel import Session, select

from tests.authority_kernel.companion_provider_provisioning_test import SETTINGS, case
from tests.authority_kernel.scribe_capture_admission_test import FakeScribe
from tests.authority_kernel.scribe_encounters_test import engine as _engine
from tests.authority_kernel.scribe_encounters_test import workflow as _workflow
from tests.authority_kernel.scribe_long_visit_contract_test import capabilities

engine = _engine
workflow = _workflow


class InventedScribe(FakeScribe):
    async def create_configured_session(
        self, tenant_id, payload, *, creator_actor_id, idempotency_key
    ):
        receipt = ConfiguredSessionReceipt(
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
        self.sessions[idempotency_key] = receipt
        self.tenant = tenant_id
        return receipt

    async def capture_operation(self, tenant_id, session_id, operation, **kwargs):
        saved = next(
            value for value in self.sessions.values() if value.id == session_id
        )
        assert tenant_id == self.tenant
        if operation.kind == "creator_review":
            assert kwargs["creator_actor_id"] == saved.creator_actor_id
            return CreatorReviewView(
                schema_version="1.0", session_id=session_id, status="created"
            )
        assert operation.kind == "capabilities"
        return capabilities(1000, 67108864)


@pytest.fixture
def native(engine, workflow, monkeypatch):
    args, provisioned, request, invoke = workflow
    created = invoke(request())
    row, account = created["encounter"], created["account"]
    # Restore real transaction/context handling, including the real deadline
    # router. Only verified identity and the external Scribe service are fake.
    monkeypatch.setattr(
        scribe_encounters, "global_session", session_manager.global_session
    )
    monkeypatch.setattr(
        session_manager.SessionManager.get_instance(), "_engine", engine
    )
    monkeypatch.setattr(
        provider_context_exchange,
        "configured_superadmin_capability_environment",
        lambda: SuperadminCapabilityEnvironment.staging,
    )
    app = FastAPI()
    app.include_router(route.native_capture_api_app)
    app.dependency_overrides[route.authenticate_native_workload] = (
        lambda: EntraServicePrincipal("11111111-1111-4111-8111-111111111111")
    )
    app.dependency_overrides[route.authenticate_embed_platform_session] = (
        lambda: VerifiedClerkIdentity(
            SETTINGS.clerk_issuer,
            args["verified_clerk_subject"],
            "session_invented_scribe",
        )
    )
    app.dependency_overrides[route.get_capture_authority_settings] = lambda: SETTINGS
    app.dependency_overrides[route.get_capture_settings] = (
        lambda: ScribeCaptureSettings(enabled=True)
    )
    app.dependency_overrides[route.encounter_settings] = (
        lambda: ScribeEncounterSettings(enabled=True)
    )
    remote = InventedScribe()
    app.dependency_overrides[route.get_admission_client_factory] = (
        lambda: lambda: remote
    )

    original = service.execute_capture_operation

    async def prepare_fixture(**kwargs):
        # Explicit synthetic fixture admission through the actual durable Core
        # service; not a fabricated ready row or bypassed authorization result.
        await admission.admit_capture(
            caller=kwargs["caller"],
            binding_ref=kwargs["binding_ref"],
            workspace_id=kwargs["workspace_id"],
            client_request_key="invented-admission",
            attestation=admission.ConsentAttestation(
                modality="in_person",
                participant_regions=frozenset({"US-NY"}),
                method="verbal_attestation",
                all_parties_consented=True,
                granted_at=datetime.now(UTC),
            ),
            configured_capture=ConfiguredCaptureSelection(
                schema_version="1.0", language_mode="en-US"
            ),
            client=remote,
        )
        return await original(**kwargs)

    def body(kind="mount"):
        value: dict[str, object] = dict(
            action="mount" if kind == "mount" else "operation",
            workspace_id=row["workspace_id"],
            request_nonce=str(uuid4()),
            expected_actor_ref=account["actor_ref"],
            expected_tenant_ref=account["tenant_ref"],
        )
        if kind == "mount":
            value["opening_nonce"] = str(uuid4())
        else:
            value.update(binding_ref=binding, operation={"kind": kind})
        return value

    with TestClient(app) as connection:

        def post(kind="mount", **changes):
            return connection.post(
                "/service/v1/agent" + route.PATH,
                json=body(kind) | changes,
                headers={
                    "Content-Type": "application/json",
                    "X-Cosmaneura-Native-Capture-Version": "1",
                    "Idempotency-Key": "invented-status-query",
                },
            )

        monkeypatch.setattr(service, "execute_capture_operation", prepare_fixture)
        initial = post()
        assert initial.status_code == 200, initial.text
        binding = initial.json()["result"]["binding_ref"]
        monkeypatch.setattr(service, "execute_capture_operation", original)
        yield SimpleNamespace(
            connection=connection,
            post=post,
            row=row,
            args=args,
            provisioned=provisioned,
            request=request,
            invoke=invoke,
            remote=remote,
        )
    assert engine.pool.checkedout() == 0


@contextmanager
def query_counts(engine):
    counts = {"all_selects": 0, "status_selects": 0}

    def count(_connection, _cursor, statement, _parameters, _context, _many):
        # SQL shape only; never retain query parameters or row/clinical content.
        normalized = " ".join(statement.lower().split())
        if normalized.startswith("select "):
            counts["all_selects"] += 1
            if any(
                " from " + name + " " in normalized
                for name in ("ai_visit_transcript", "ai_visit_note")
            ):
                counts["status_selects"] += 1

    event.listen(engine, "before_cursor_execute", count)
    try:
        yield counts
    finally:
        event.remove(engine, "before_cursor_execute", count)


@pytest.mark.parametrize(
    "kind",
    [
        "mount",
        "provider_encounter",
        "creator_review",
        "capabilities",
        "native_recording_context",
    ],
)
def test_real_facade_keeps_all_authority_selects_and_removes_only_unused_status(
    native, engine, monkeypatch, kind, record_property
):
    original = scribe_encounters.encounter_workflow
    policies = []

    def previous_enrichment(**kwargs):
        policies.append(kwargs["include_lookup_status"])
        return original(**(kwargs | {"include_lookup_status": True}))

    with monkeypatch.context() as patch:
        patch.setattr(scribe_encounters, "encounter_workflow", previous_enrichment)
        with query_counts(engine) as previous:
            before = native.post(kind)
    with query_counts(engine) as current:
        after = native.post(kind)
    assert before.status_code == after.status_code == 200
    assert previous["status_selects"] == (10 if kind == "mount" else 8)
    assert current["status_selects"] == (2 if kind == "mount" else 0)
    assert previous["all_selects"] - current["all_selects"] == 8
    assert policies == ([False] * 4 + [True] if kind == "mount" else [False] * 4)
    if kind == "mount":
        assert (
            before.json()["result"]["encounter"] == after.json()["result"]["encounter"]
        )
    else:
        assert before.json()["result"]["receipt"] == after.json()["result"]["receipt"]
    record_property(
        "query_counts", {"kind": kind, "previous": previous, "current": current}
    )


def save_status(engine, native, *, draft=False):
    with Session(engine) as session:
        transcript = AIVisitTranscript.model_validate(
            dict(
                id=TypeID("ai_visit_transcript"),
                doctor_id=native.args["doctor_id"],
                appointment_id=TypeID.from_string(native.row["appointment_ref"]),
                patient_id=TypeID.from_string(native.row["patient_ref"]),
                duration=0,
                transcript_data=[],
            )
        )
        session.add(transcript)
        session.flush()
        note = None
        if draft:
            schema = DoctorNoteSchema(
                doctor_id=native.args["doctor_id"],
                note_type="Invented status fixture",
                fields={},
            )
            session.add(schema)
            session.flush()
            note = AIVisitNote(
                doctor_id=native.args["doctor_id"],
                appointment_id=transcript.appointment_id,
                ai_transcript_id=transcript.id,
                doctor_note_schema_id=schema.id,
                note={},
            )
            session.add(note)
            session.flush()
        identities = transcript.id, note.id if note else None
        session.commit()
        return identities


@pytest.mark.parametrize("draft", [False, True])
def test_default_lookup_catalog_and_final_mount_keep_saved_status(
    native, engine, draft
):
    save_status(engine, native, draft=draft)
    expected = "draft_ready" if draft else "recording_saved"
    for action in ("lookup", "list"):
        with query_counts(engine) as counts:
            result = native.invoke(
                native.request(
                    action,
                    **(
                        {"workspace_id": native.row["workspace_id"]}
                        if action == "lookup"
                        else {}
                    ),
                )
            )
        row = result["encounter"] if action == "lookup" else result["encounters"][0]
        assert row["status"] == expected and counts["status_selects"] == 2
    result = native.post()
    assert result.status_code == 200
    assert result.json()["result"]["encounter"]["status"] == expected


def test_final_mount_observes_newly_committed_draft(native, engine, monkeypatch):
    original = service.execute_capture_operation

    async def save_after_read(**kwargs):
        result = await original(**kwargs)
        assert (
            session_manager._session_context.get() is None
            and engine.pool.checkedout() == 0
        )
        save_status(engine, native, draft=True)
        return result

    monkeypatch.setattr(service, "execute_capture_operation", save_after_read)
    result = native.post()
    assert result.status_code == 200
    assert result.json()["result"]["encounter"]["status"] == "draft_ready"


@pytest.mark.parametrize("change", ["deleted_and_superseded", "foreign_doctor"])
def test_final_mount_does_not_reuse_known_status(native, engine, monkeypatch, change):
    transcript_id, note_id = save_status(engine, native, draft=True)
    assert native.post().json()["result"]["encounter"]["status"] == "draft_ready"
    original = service.execute_capture_operation

    async def remove_status_after_read(**kwargs):
        result = await original(**kwargs)
        with Session(engine) as session:
            transcript = session.get(AIVisitTranscript, transcript_id)
            note = session.get(AIVisitNote, note_id)
            if change == "deleted_and_superseded":
                assert note is not None
                assert transcript is not None
                transcript.deleted_at = note.superseded_at = datetime.now(UTC)
                note.supersession_reason = "invented-status-fixture"
            else:
                # Different existing synthetic doctor, never another real row.
                other = case(engine)
                assert note is not None
                assert transcript is not None
                transcript.doctor_id = note.doctor_id = other["doctor_id"]
            session.add_all([transcript, note])
            session.commit()
        return result

    monkeypatch.setattr(service, "execute_capture_operation", remove_status_after_read)
    result = native.post()
    assert result.status_code == 200
    assert "status" not in result.json()["result"]["encounter"]


@pytest.mark.parametrize(
    "change", ["identity", "provider", "record_owner", "capture_binding"]
)
@pytest.mark.parametrize("kind", ["mount", "capabilities"])
def test_committed_revocation_still_wins_after_remote_read(
    native, engine, monkeypatch, change, kind
):
    save_status(engine, native, draft=True)
    original = service.execute_capture_operation
    other = case(engine) if change == "record_owner" else None

    async def revoke_after_read(**kwargs):
        result = await original(**kwargs)
        assert (
            session_manager._session_context.get() is None
            and engine.pool.checkedout() == 0
        )
        with Session(engine) as session:
            if change == "identity":
                row = session.exec(
                    select(AuthorityActorExternalIdentity).where(
                        AuthorityActorExternalIdentity.subject
                        == native.args["verified_clerk_subject"]
                    )
                ).one()
            elif change == "provider":
                row = session.get(
                    ScribeCaptureProviderLink, native.provisioned.provider_link_ref
                )
            elif change == "capture_binding":
                row = session.exec(
                    select(ScribeCaptureBinding).where(
                        ScribeCaptureBinding.workspace_id
                        == UUID(native.row["workspace_id"])
                    )
                ).one()
            else:
                row = session.get(
                    Appointment, TypeID.from_string(native.row["appointment_ref"])
                )
                assert other is not None
                assert row is not None
                row.doctor_id = other["doctor_id"]
            if change != "record_owner":
                assert row is not None
                row.revoked_at = datetime.now(UTC)
            session.add(row)
            session.commit()
        return result

    monkeypatch.setattr(service, "execute_capture_operation", revoke_after_read)
    response = native.post(kind)
    assert response.status_code == 403
    assert "encounter" not in response.json() and "receipt" not in response.json()


def test_wire_cannot_request_internal_status_option(native):
    assert native.post(include_lookup_status=False).status_code == 422
