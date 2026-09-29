"""Actual provider brief lifecycle against an explicitly owned local PostgreSQL.

Run with --confcutdir=tests/authority_kernel and COMPANION_PREP_TEST_URL.
External token verification and model generation are supplied synthetic values;
Core's authority, reviewed sources, queue, receipts and database are real.
"""

import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.configuration.session_prep as configuration
import app.services.authority as authority
from app.routes.agent import get_agent_authority_settings
from app.routes.companion_session_prep import PATH, router
from app.routes.dependencies.entra_service import (
    EntraServicePrincipal,
    authenticate_companion_session_prep_request,
)
from app.services.authority_conversation import write_conversation_turn
from app.services.provider_record_authority import validate_provider_record_scope
from app.services.session_prep import SessionPrepPreparedBrief
from app.services.session_prep_note_sources import (
    finalize_note_for_session_prep,
    note_content_sha256,
)
from app.services.session_prep_worker import run_one_build

from activemodel.session_manager import (
    SessionManager,
    _serialize_pydantic_model,
    get_session,
)
from app.models.ai_visit_note import AIVisitNote
from app.models.ai_visit_transcript import AIVisitTranscript
from app.models.appointment import Appointment
from app.models.authority import AuthorityActorExternalIdentity
from app.models.baa import BAA_CURRENT_REFERENCE
from app.models.doctor import Doctor
from app.models.doctor_note_schema import DoctorNoteSchema
from app.models.patient import Patient
from app.models.practice import Practice, PracticeMember, lock_practice_seats
from app.models.provider_record_authority import ProviderRecordAuthority
from sqlalchemy.engine import make_url
from sqlmodel import SQLModel, create_engine, select

from tests.authority_kernel.conftest import _AUTHORITY_TABLES
from tests.authority_kernel.scribe_capture_scope_test import (
    REAL_VIEW_AS_GUARD,
    SETTINGS,
)

CALLER = "33333333-3333-4333-8333-333333333333"


@pytest.fixture
def flow(monkeypatch):
    raw = os.environ.get("COMPANION_PREP_TEST_URL")
    if not raw:
        pytest.skip("Explicit owned PostgreSQL URL required")
    url = make_url(raw)
    assert (url.host, url.port, url.username, url.database) == (
        "127.0.0.1",
        55460,
        "companion_synth",
        "companion_prep_test",
    )
    engine = create_engine(url, json_serializer=_serialize_pydantic_model)
    names = {t.name for t in _AUTHORITY_TABLES} | {
        "doctor",
        "patient",
        "appointment",
        "practice",
        "practice_member",
        "pilot_access_grandfather_grant",
        "superadmin_capability_grant",
        "view_as_session",
        "provider_practice_authority",
        "provider_record_authority",
        "ai_visit_note",
        "session_prep_note_finalization",
        "session_prep_artifact",
        "session_prep_build",
        "session_prep_read_receipt",
    }
    tables = {name: SQLModel.metadata.tables[name] for name in names}
    while True:
        before = len(tables)
        for table in list(tables.values()):
            for fk in table.foreign_keys:
                tables[fk.column.table.name] = fk.column.table
        if len(tables) == before:
            break
    SQLModel.metadata.create_all(engine, tables=list(tables.values()))
    manager = SessionManager.get_instance()
    monkeypatch.setattr(manager, "_engine", engine)
    monkeypatch.setattr(
        authority, "validate_provider_record_scope", validate_provider_record_scope
    )
    monkeypatch.setattr(
        "app.services.view_as.mount_context_session_blocked_by_view_as",
        REAL_VIEW_AS_GUARD,
    )
    with engine.connect() as connection:
        transaction = connection.begin()
        monkeypatch.setattr(manager, "session_connection", connection)
        try:
            yield _prepare(monkeypatch)
        finally:
            transaction.rollback()
    engine.dispose()


def _prepare(monkeypatch):
    now = datetime.now(UTC)
    with get_session() as s:
        doctor = Doctor(
            clerk_id="user_companion_synthetic_" + uuid4().hex, biography=None
        )
        practice = Practice(
            legal_name="Invented companion practice",
            email_domain="synthetic.invalid",
            join_code=uuid4().hex[:16],
            baa_accepted_at=now,
            baa_version=BAA_CURRENT_REFERENCE,
            billing_exempt=True,
        )
        s.add_all([doctor, practice])
        s.flush()
        patient = Patient(
            doctor_id=doctor.id,
            name="Invented person",
            date_of_birth=datetime(1990, 1, 1, tzinfo=UTC),
        )
        member = PracticeMember(
            practice_id=practice.id, doctor_id=doctor.id, role="owner", attested_at=now
        )
        assert lock_practice_seats(s, practice.id) is not None
        s.add_all([patient, member])
        s.flush()
        target = Appointment(
            doctor_id=doctor.id,
            patient_id=patient.id,
            appointment_date=now,
            external_id=None,
        )
        prior = Appointment(
            doctor_id=doctor.id,
            patient_id=patient.id,
            appointment_date=now - timedelta(days=1),
            external_id=None,
        )
        s.add_all([target, prior])
        s.flush()
        schema = DoctorNoteSchema(
            doctor_id=doctor.id, note_type="SOAP", fields={"Plan": "Plan"}
        )
        transcript = AIVisitTranscript(
            doctor_id=doctor.id,
            patient_id=patient.id,
            appointment_id=prior.id,
            duration=1,
            transcript_data=[],
        )
        s.add_all([schema, transcript])
        s.flush()
        note = AIVisitNote(
            doctor_id=doctor.id,
            appointment_id=prior.id,
            ai_transcript_id=transcript.id,
            doctor_note_schema_id=schema.id,
            note={"Plan": "Review synthetic goals."},
        )
        s.add(note)
        s.flush()
        finalize_note_for_session_prep(
            session=s,
            doctor_id=doctor.id,
            note_id=note.id,
            expected_content_sha256=note_content_sha256(note.note),
            observed_at=now - timedelta(hours=1),
        )
        s.commit()
        doctor_id, subject, target_id, note_id, member_id = (
            doctor.id,
            doctor.clerk_id,
            target.id,
            note.id,
            member.id,
        )
    monkeypatch.setattr(
        configuration,
        "SESSION_PREP_PROVIDER_SETTINGS",
        configuration.SessionPrepProviderSettings(True, frozenset({subject})),
    )
    token = authority.exchange_context_token(
        clerk_issuer=SETTINGS.clerk_issuer,
        clerk_subject=subject,
        requested_surface="provider_assistant",
        record_ref=str(target_id),
        settings=SETTINGS,
    ).context_token
    with get_session() as s:
        scope = s.exec(
            select(ProviderRecordAuthority).where(
                ProviderRecordAuthority.appointment_id == target_id
            )
        ).one()
        context = scope.context_snapshot_ref
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[authenticate_companion_session_prep_request] = (
        lambda: EntraServicePrincipal(CALLER)
    )
    app.dependency_overrides[get_agent_authority_settings] = lambda: SETTINGS
    return SimpleNamespace(
        client=TestClient(app),
        token=token,
        context=context,
        note_id=note_id,
        doctor_id=doctor_id,
        subject=subject,
        member_id=member_id,
    )


def action(f):
    turn = uuid4()
    resolved = authority.resolve_mount_context(
        workload_client_id=CALLER,
        mount_context_token=f.token,
        client_turn_id=turn,
        settings=SETTINGS,
    )
    accepted = write_conversation_turn(
        workload_client_id=CALLER,
        mount_context_token=f.token,
        interaction_ref=resolved.interaction_ref,
        client_turn_id=turn,
        idempotency_key="action-" + str(turn),
        settings=SETTINGS,
        payload={
            "phase": "user_accepted",
            "ingress_kind": "provider_ui_action",
            "action_code": "session-prep.open",
            "message": {"content_type": "text/plain", "content": "Open Session Prep"},
        },
    )
    return turn, resolved.interaction_ref, accepted.turn_ref


def read(f, a, key="companion-prep-read-0001", context=None):
    turn, interaction, source = a
    return f.client.post(
        PATH,
        headers={
            "X-Cosmaneura-Mount-Context": f.token,
            "X-Cosmaneura-Interaction-Ref": interaction,
            "X-Cosmaneura-Client-Turn-ID": str(turn),
            "Idempotency-Key": key,
        },
        json={
            "tool_name": "session-prep.context.read",
            "tool_version": "1.0.0",
            "subject_ref": None,
            "arguments": {
                "source_turn_ref": source,
                "context_snapshot_ref": context or f.context,
                "purpose": "session_prep",
                "detail": "brief",
            },
        },
    )


def build():
    def generate(selection):
        cited = {
            "text": "Discuss the synthetic goals.",
            "source_refs": [selection.sources[0].source_ref],
        }
        return SessionPrepPreparedBrief(
            recap=cited,
            trajectory_and_material_changes=[],
            talking_points=[cited],
            open_threads=[],
            cautions=[],
            suggested_outline=[cited],
            patient_reported=None,
        )

    return run_one_build(generate=generate, settings=SETTINGS)


def test_pending_ready_retry_correction_and_new_review(flow):
    f = flow
    a = action(f)
    pending = read(f, a)
    assert pending.status_code == 200 and pending.json()["result"]["state"] == "pending"
    assert build() == "ready"
    # Lost-response retry keeps its receipt; a new read observes completed work.
    replay = read(f, a)
    assert (
        replay.json() == pending.json()
        and replay.headers["Idempotency-Replayed"] == "true"
    )
    ready = read(f, a, "companion-prep-read-0002")
    assert ready.status_code == 200 and ready.json()["result"]["state"] == "ready"
    assert ready.json()["result"]["prepared_brief"]["recap"]["source_refs"] == [
        str(f.note_id)
    ]
    assert ready.headers["Cache-Control"] == "no-store"
    assert read(f, a, "companion-prep-read-0002").json() == ready.json()
    assert (
        read(f, a, "companion-prep-other-001", "forged_context_0001").status_code == 403
    )
    with get_session() as s:
        note = s.get(AIVisitNote, f.note_id)
        note.note = {"Plan": "Corrected synthetic goals."}
        s.add(note)
        s.commit()
    revoked = read(f, a, "companion-prep-read-0002")
    assert (
        revoked.status_code == 403 and "Discuss the synthetic goals" not in revoked.text
    )
    with get_session() as s:
        note = s.get(AIVisitNote, f.note_id)
        finalize_note_for_session_prep(
            session=s,
            doctor_id=f.doctor_id,
            note_id=f.note_id,
            expected_content_sha256=note_content_sha256(note.note),
            observed_at=datetime.now(UTC),
        )
        s.commit()
    next_action = action(f)
    assert read(f, next_action).json()["result"]["state"] == "pending"
    assert build() == "ready"
    newer = read(f, next_action, "companion-prep-read-0002")
    assert newer.status_code == 200 and newer.json()["result"]["brief_version"] == 2
    assert (
        newer.json()["result"]["brief_supersedes_ref"]
        == ready.json()["result"]["brief_ref"]
    )


@pytest.mark.parametrize("revoke", ["identity", "membership", "cohort"])
def test_current_human_permissions_are_rechecked_on_retry(flow, monkeypatch, revoke):
    f = flow
    a = action(f)
    assert build() == "ready"
    assert read(f, a).json()["result"]["state"] == "ready"
    if revoke == "cohort":
        monkeypatch.setattr(
            configuration,
            "SESSION_PREP_PROVIDER_SETTINGS",
            configuration.SessionPrepProviderSettings(True, frozenset()),
        )
    else:
        with get_session() as s:
            if revoke == "identity":
                row = s.exec(
                    select(AuthorityActorExternalIdentity).where(
                        AuthorityActorExternalIdentity.subject == f.subject
                    )
                ).one()
                row.revoked_at = datetime.now(UTC)
            else:
                row = s.get(PracticeMember, f.member_id)
                assert lock_practice_seats(s, row.practice_id) is not None
                row.seat_state = "inactive"
            s.add(row)
            s.commit()
    denied = read(f, a)
    assert denied.status_code in {401, 403}
    assert "Discuss the synthetic goals" not in denied.text
