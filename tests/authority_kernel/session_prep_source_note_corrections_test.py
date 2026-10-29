"""Source edits on a task-owned PostgreSQL socket; never root conftest cleanup.

Run with --confcutdir=tests/authority_kernel and the explicitly owned synthetic
URL. SQLModel schema checks here do not claim an Alembic migration rehearsal.
"""

import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.configuration import session_prep as configuration
from app.configuration.authority import AuthorityKernelSettings
from app.configuration.session_prep import SessionPrepProviderSettings
from app.routes import session_prep_review as routes
from app.services import session_prep_note_corrections as corrections
from app.services import session_prep_worker as worker
from app.services.session_prep import SessionPrepPreparedBrief
from app.services.session_prep_note_sources import (
    NoteSourceChanged,
    NoteSourceDenied,
    finalize_note_for_session_prep,
    load_reviewed_note_sources,
    note_content_sha256,
    prior_note_overview,
)
from app.services.session_prep_runtime import (
    ready_artifact,
    source_digest,
)

from app.models.ai_visit_note import AIVisitNote
from app.models.ai_visit_transcript import AIVisitTranscript
from app.models.appointment import Appointment
from app.models.authority import (
    AuthorityActor,
    AuthorityActorExternalIdentity,
    AuthorityPerson,
    AuthorityPersonBinding,
    AuthorityPersonOrigin,
    AuthorityTenant,
    AuthorityTenantMembership,
)
from app.models.baa import BAA_CURRENT_REFERENCE
from app.models.doctor import Doctor
from app.models.doctor_note_schema import DoctorNoteSchema
from app.models.patient import Patient
from app.models.practice import Practice, PracticeMember, lock_practice_seats
from app.models.provider_record_authority import (
    ProviderPracticeAuthority,
    ProviderRecordAuthority,
)
from app.models.scribe_capture_binding import (
    ScribeCaptureBinding,
    ScribeCaptureProviderLink,
)
from app.models.session_prep_note_finalization import SessionPrepNoteFinalization
from app.models.session_prep_runtime import SessionPrepArtifact, SessionPrepBuild
from app.models.visit_audio_file import VisitAudioFile
from sqlalchemy import func
from sqlalchemy.engine import make_url
from sqlmodel import Session, create_engine, select


@pytest.fixture
def correction_engine():
    raw = os.environ.get("SESSION_PREP_SOURCE_CORRECTION_TEST_URL")
    if not raw:
        pytest.skip("Explicit task-owned synthetic PostgreSQL URL required")
    url = make_url(raw)
    assert (url.database, url.username, url.host, dict(url.query)) == (
        "sp_note_corrections_test",
        "sp_note_synthetic",
        None,
        {"host": "/private/tmp/sp-note-edit-db29/socket", "port": "55561"},
    )
    ownership = json.loads(
        Path("/private/tmp/sp-note-edit-db29/ownership.json").read_text()
    )
    assert ownership == {
        "created_for": "session-prep-web-source-note-corrections-20261029",
        "source": "514c32d3375575ef91ccfbcdbbdae167ec0f205a",
        "database": "sp_note_corrections_test",
        "username": "sp_note_synthetic",
        "socket": "/private/tmp/sp-note-edit-db29/socket",
        "port": 55561,
        "synthetic_only": True,
        "network_listen_addresses": "",
    }
    engine = create_engine(url)
    yield engine
    engine.dispose()


@pytest.fixture
def case(correction_engine):
    now = datetime.now(UTC)
    with Session(correction_engine, expire_on_commit=False) as session:
        doctor = Doctor(
            clerk_id="user_synthetic_" + uuid4().hex,
            biography=None,
            onboarding_completed_at=now,
        )
        practice = Practice(
            legal_name="Fictional note-edit practice",
            email_domain="synthetic.invalid",
            join_code=uuid4().hex[:16],
            baa_accepted_at=now,
            baa_version=BAA_CURRENT_REFERENCE,
            billing_exempt=True,
        )
        session.add_all([doctor, practice])
        session.flush()
        patient = Patient(
            doctor_id=doctor.id,
            name="Fictional Bob",
            date_of_birth=datetime(1990, 1, 1),
        )
        session.add(patient)
        session.flush()
        appointment = Appointment(
            doctor_id=doctor.id,
            patient_id=patient.id,
            external_id=None,
            appointment_date=(now - timedelta(hours=2)).replace(tzinfo=None),
        )
        target = Appointment(
            doctor_id=doctor.id,
            patient_id=patient.id,
            external_id=None,
            appointment_date=(now - timedelta(hours=1)).replace(tzinfo=None),
        )
        schema = DoctorNoteSchema(
            doctor_id=doctor.id,
            note_type="Fictional template",
            fields={
                "Labs - 02/12/2026": "Labs guidance",
                "Provider": "Provider guidance",
            },
        )
        session.add_all([appointment, target, schema])
        session.flush()
        transcript = AIVisitTranscript(
            doctor_id=doctor.id,
            patient_id=patient.id,
            appointment_id=appointment.id,
            duration=23,
            transcript_data=[],
        )
        upload = VisitAudioFile(
            doctor_id=doctor.id,
            patient_id=patient.id,
            appointment_id=appointment.id,
            azure_file_name="fictional.wav",
        )
        session.add_all([transcript, upload])
        session.flush()
        original = {
            "Labs - 02/12/2026": "BMP pending.",
            "Provider": "Incorrect template identity.",
        }
        note = AIVisitNote(
            doctor_id=doctor.id,
            appointment_id=appointment.id,
            ai_transcript_id=transcript.id,
            doctor_note_schema_id=schema.id,
            note=original,
        )
        assert lock_practice_seats(session, practice.id) is not None
        member = PracticeMember(
            doctor_id=doctor.id, practice_id=practice.id, role="owner", attested_at=now
        )
        session.add_all([note, member])
        session.commit()
        yield SimpleNamespace(
            engine=correction_engine,
            doctor_id=doctor.id,
            practice_id=practice.id,
            patient_id=patient.id,
            appointment_id=appointment.id,
            target_id=target.id,
            schema_id=schema.id,
            transcript_id=transcript.id,
            upload_id=upload.id,
            note_id=note.id,
            original=original,
            digest=note_content_sha256(original),
        )


def edit(session, case, *, fields=None):
    doctor = session.get(Doctor, case.doctor_id)
    assert doctor is not None
    return corrections.correct_source_note(
        session=session,
        doctor=doctor,
        note_id=case.note_id,
        expected_content_sha256=case.digest,
        fields=fields or {"Labs": "BMP cancelled; no result documented."},
    )


def authority_scope(session, case):
    """Retained valid FK graph for the local cache/binding refusal checks."""
    now = datetime.now(UTC)
    doctor = session.get(Doctor, case.doctor_id)
    assert doctor is not None
    actor, tenant, person = (
        AuthorityActor(),
        AuthorityTenant(),
        AuthorityPerson(lifecycle_state="patient"),
    )
    session.add_all([actor, tenant, person])
    session.flush()
    identity = AuthorityActorExternalIdentity(
        actor_id=actor.id,
        issuer="https://test.clerk.invalid",
        subject=doctor.clerk_id,
        verified_at=now,
    )
    binding = AuthorityPersonBinding(
        person_id=person.id,
        actor_id=actor.id,
        tenant_id=tenant.id,
        binding_ref=str(case.target_id),
        binding_kind="provider_record",
        verified_at=now,
    )
    practice_scope = ProviderPracticeAuthority(
        practice_id=case.practice_id, tenant_id=tenant.id
    )
    session.add_all(
        [
            identity,
            binding,
            practice_scope,
            AuthorityPersonOrigin(
                person_id=person.id,
                origin="provider_entry",
                source_ref=str(case.patient_id),
            ),
            AuthorityTenantMembership(
                actor_id=actor.id, tenant_id=tenant.id, membership_kind="provider"
            ),
        ]
    )
    session.flush()
    record = ProviderRecordAuthority(
        binding_id=binding.id,
        practice_authority_id=practice_scope.id,
        doctor_id=case.doctor_id,
        appointment_id=case.target_id,
        patient_id=case.patient_id,
        context_snapshot_ref="synthetic_snapshot_" + uuid4().hex,
    )
    session.add(record)
    session.flush()
    return actor, tenant, identity, binding, record


def synthetic_brief(selection):
    item = {
        "text": "Review the documented labs.",
        "source_refs": [selection.sources[0].source_ref],
    }
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


def run_build(case, monkeypatch, *, generate=synthetic_brief):
    monkeypatch.setattr(worker, "get_session", lambda: Session(case.engine))
    monkeypatch.setattr(
        configuration,
        "SESSION_PREP_PROVIDER_SETTINGS",
        SessionPrepProviderSettings(True),
    )
    return worker.run_one_build(
        generate=generate,
        settings=AuthorityKernelSettings(
            True, 900, 300, clerk_issuer="https://test.clerk.invalid"
        ),
    )


def queue_build(session, record, digest):
    now = datetime.now(UTC)
    # Preserve prior committed synthetic retries while claiming this exact
    # scope first on repeated runs against the task-owned disposable lab.
    earliest = session.exec(select(func.min(SessionPrepBuild.requested_at))).one()
    pending = SessionPrepBuild(
        record_scope_id=record.id,
        desired_digest=digest,
        requested_at=min(earliest or now, now) - timedelta(seconds=1),
        retry_at=now,
    )
    session.add(pending)
    session.commit()
    return pending


def family(case):
    with Session(case.engine) as session:
        return session.exec(
            select(AIVisitNote).where(AIVisitNote.appointment_id == case.appointment_id)
        ).all()


def test_correction_retains_original_and_review_but_excludes_it_until_new_review(
    case, monkeypatch
):
    with Session(case.engine) as session:
        old_review = finalize_note_for_session_prep(
            session=session,
            doctor_id=case.doctor_id,
            note_id=case.note_id,
            expected_content_sha256=case.digest,
            observed_at=datetime.now(UTC),
        )
        session.commit()
        old_review_id = old_review.id
        before = prior_note_overview(
            session=session,
            doctor_id=case.doctor_id,
            target_appointment_id=case.target_id,
            observed_at=datetime.now(UTC),
        )
        assert before.included_count == 1
        selection = load_reviewed_note_sources(
            session=session,
            doctor_id=case.doctor_id,
            target_appointment_id=case.target_id,
            observed_at=datetime.now(UTC),
        )
        old_digest = source_digest(selection)
        *_, record = authority_scope(session, case)
        queue_build(session, record, old_digest)
        assert run_build(case, monkeypatch) == "ready"
        artifact = ready_artifact(session, record.id, old_digest)
        assert artifact.result_payload["freshness"] == "current"
        assert ready_artifact(session, record.id, old_digest).id == artifact.id
        saved = edit(session, case)
        saved_id = saved.id
        session.commit()
        assert saved_id != case.note_id
        retained = session.get(AIVisitNote, case.note_id, populate_existing=True)
        assert (
            retained.note == case.original
            and retained.supersession_reason == "clinician_web_source_correction"
        )
        assert (
            session.get(SessionPrepNoteFinalization, old_review_id).content_sha256
            == case.digest
        )
        assert session.get(DoctorNoteSchema, case.schema_id).fields == {
            "Labs - 02/12/2026": "Labs guidance",
            "Provider": "Provider guidance",
        }
        after = prior_note_overview(
            session=session,
            doctor_id=case.doctor_id,
            target_appointment_id=case.target_id,
            observed_at=datetime.now(UTC),
        )
        assert (
            after.included_count == 0
            and after.selected_source_fingerprint != before.selected_source_fingerprint
        )
        assert (
            after.notes[0].note_id == str(saved_id)
            and after.notes[0].status == "needs_review"
        )
        excluded = load_reviewed_note_sources(
            session=session,
            doctor_id=case.doctor_id,
            target_appointment_id=case.target_id,
            observed_at=datetime.now(UTC),
        )
        assert not excluded.sources
        assert source_digest(excluded) != old_digest
        assert ready_artifact(session, record.id, source_digest(excluded)) is None
        assert session.get(SessionPrepArtifact, artifact.id).source_digest == old_digest
        finalize_note_for_session_prep(
            session=session,
            doctor_id=case.doctor_id,
            note_id=saved_id,
            expected_content_sha256=note_content_sha256(saved.note),
            observed_at=datetime.now(UTC),
        )
        session.commit()
        selection = load_reviewed_note_sources(
            session=session,
            doctor_id=case.doctor_id,
            target_appointment_id=case.target_id,
            observed_at=datetime.now(UTC),
        )
        assert len(selection.sources) == 1 and selection.sources[0].source_ref == str(
            saved_id
        )
        assert selection.sources[0].content == {
            "Labs": "BMP cancelled; no result documented."
        }
        assert selection.sources[0].content_sha256 == note_content_sha256(saved.note)
        assert source_digest(selection) != old_digest
        assert ready_artifact(session, record.id, source_digest(selection)) is None
        assert session.get(
            SessionPrepNoteFinalization, old_review_id
        ).reviewed_content == {
            "sections": [[key, value] for key, value in case.original.items()]
        }


def test_competing_writes_publish_one_version_and_stale_retry_cannot_append(case):
    def save(text):
        with Session(case.engine) as session:
            try:
                result = edit(session, case, fields={"Labs": text})
                session.commit()
                return str(result.id)
            except NoteSourceChanged:
                return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                save, ["First fictional correction", "Competing fictional correction"]
            )
        )
    assert sum(result is not None for result in results) == 1
    versions = family(case)
    assert (
        len(versions) == 2 and sum(row.superseded_at is None for row in versions) == 1
    )
    assert save("Stale retry") is None and len(family(case)) == 2


def test_unchanged_save_does_not_create_a_new_version_or_invalidate_review(case):
    with Session(case.engine) as session:
        review = finalize_note_for_session_prep(
            session=session,
            doctor_id=case.doctor_id,
            note_id=case.note_id,
            expected_content_sha256=case.digest,
            observed_at=datetime.now(UTC),
        )
        session.commit()
        assert edit(session, case, fields=case.original).id == case.note_id
        session.commit()
        assert session.get(SessionPrepNoteFinalization, review.id) is not None
        assert (
            prior_note_overview(
                session=session,
                doctor_id=case.doctor_id,
                target_appointment_id=case.target_id,
                observed_at=datetime.now(UTC),
            ).included_count
            == 1
        )
    assert len(family(case)) == 1


def test_worker_contention_refuses_stale_publication_and_commits_retry_state(
    case, monkeypatch
):
    with Session(case.engine) as session:
        finalize_note_for_session_prep(
            session=session,
            doctor_id=case.doctor_id,
            note_id=case.note_id,
            expected_content_sha256=case.digest,
            observed_at=datetime.now(UTC),
        )
        *_, record = authority_scope(session, case)
        selection = load_reviewed_note_sources(
            session=session,
            doctor_id=case.doctor_id,
            target_appointment_id=case.target_id,
            observed_at=datetime.now(UTC),
        )
        pending = queue_build(session, record, source_digest(selection))
        pending_id, record_id = pending.id, record.id
    with Session(case.engine) as editor:

        def corrected_during_model(selection):
            edit(
                editor, case
            )  # Hold actual Appointment/Note locks until after publication.
            return synthetic_brief(selection)

        assert run_build(case, monkeypatch, generate=corrected_during_model) == "failed"
        with Session(case.engine) as observer:
            pending = observer.get(SessionPrepBuild, pending_id)
            assert pending.state == "pending" and pending.failure_code == "source_busy"
            assert not observer.exec(
                select(SessionPrepArtifact).where(
                    SessionPrepArtifact.record_scope_id == record_id
                )
            ).all()
        editor.commit()
    assert len(family(case)) == 2


def test_review_of_original_waits_for_edit_and_cannot_finalize_superseded_body(case):
    started = Event()

    def review_original():
        with Session(case.engine) as reviewer:
            started.set()
            try:
                finalize_note_for_session_prep(
                    session=reviewer,
                    doctor_id=case.doctor_id,
                    note_id=case.note_id,
                    expected_content_sha256=case.digest,
                    observed_at=datetime.now(UTC),
                )
                reviewer.commit()
                return "reviewed"
            except NoteSourceDenied:
                return "denied"

    with Session(case.engine) as editor:
        edit(editor, case)
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(review_original)
            assert started.wait(5)
            editor.commit()
            assert pending.result(timeout=10) == "denied"
    with Session(case.engine) as session:
        assert not session.exec(
            select(SessionPrepNoteFinalization).where(
                SessionPrepNoteFinalization.note_id == case.note_id
            )
        ).all()
    assert len(family(case)) == 2


@pytest.fixture
def endpoint(case, monkeypatch):
    monkeypatch.setattr(routes, "get_session", lambda: Session(case.engine))
    monkeypatch.setattr(
        configuration,
        "SESSION_PREP_PROVIDER_SETTINGS",
        SessionPrepProviderSettings(True),
    )
    app = FastAPI()
    state = {"doctor_id": case.doctor_id, "view_as": False}

    @app.middleware("http")
    async def preverified_actor(request, call_next):
        with Session(case.engine) as session:
            request.state.doctor = session.get(Doctor, state["doctor_id"])
        request.scope["session"] = (
            {"view_as_session_id": "synthetic-view"} if state["view_as"] else {}
        )
        return await call_next(request)

    app.include_router(routes.session_prep_review_api)
    with TestClient(app) as client:
        yield client, f"/notes/revisions/{case.note_id}/source-correction", state


def payload(case):
    return {
        "expected_content_sha256": case.digest,
        "fields": {"Labs": "Corrected synthetic source."},
    }


def test_api_returns_new_unreviewed_id_and_no_store(endpoint, case):
    client, path, _ = endpoint
    result = client.post(path, json=payload(case))
    assert result.status_code == 200 and result.headers["cache-control"] == "no-store"
    assert result.json()["note_id"] != str(case.note_id)
    assert set(result.json()) == {"note_id", "content_sha256"}
    assert client.post(path, json=payload(case)).status_code == 409
    with Session(case.engine) as session:
        assert not session.exec(
            select(SessionPrepNoteFinalization).where(
                SessionPrepNoteFinalization.appointment_id == case.appointment_id
            )
        ).all()


@pytest.mark.parametrize("revoked", [False, True])
def test_active_or_retained_revoked_capture_binding_closes_web_edit_path(
    endpoint, case, revoked
):
    client, path, _ = endpoint
    with Session(case.engine) as session:
        actor, tenant, identity, binding, _ = authority_scope(session, case)
        now = datetime.now(UTC)
        scribe_tenant = uuid4()
        link = ScribeCaptureProviderLink(
            actor_id=actor.id,
            tenant_id=tenant.id,
            source_identity_id=identity.id,
            doctor_id=case.doctor_id,
            practice_id=case.practice_id,
            scribe_tenant_id=scribe_tenant,
            reviewed_at=now,
            review_ref="synthetic-capture-review",
            revoked_at=now if revoked else None,
        )
        session.add(link)
        session.flush()
        capture = ScribeCaptureBinding(
            workspace_id=uuid4(),
            provider_link_id=link.id,
            actor_id=actor.id,
            tenant_id=tenant.id,
            source_identity_id=identity.id,
            person_binding_id=binding.id,
            doctor_id=case.doctor_id,
            practice_id=case.practice_id,
            patient_id=case.patient_id,
            appointment_id=case.appointment_id,
            scribe_tenant_id=scribe_tenant,
            scope_sha256="b" * 64,
            revoked_at=now if revoked else None,
        )
        session.add(capture)
        session.commit()
    assert client.post(path, json=payload(case)).status_code == 404
    assert len(family(case)) == 1


@pytest.mark.parametrize(
    "fault",
    [
        "foreign_actor",
        "foreign_patient",
        "foreign_schema",
        "foreign_transcript",
        "foreign_upload",
        "missing_upload",
        "source_deleted",
        "off",
        "view_as",
        "stale",
    ],
)
def test_foreign_inconsistent_disabled_or_stale_edit_is_refused(
    endpoint, case, monkeypatch, fault
):
    client, path, state = endpoint
    body = payload(case)
    expected = 404
    with Session(case.engine) as session:
        other = Doctor(clerk_id="user_synthetic_other_" + uuid4().hex, biography=None)
        session.add(other)
        session.flush()
        if fault == "foreign_actor":
            state["doctor_id"] = other.id
        elif fault == "foreign_patient":
            session.get(Patient, case.patient_id).doctor_id = other.id
        elif fault == "foreign_schema":
            session.get(DoctorNoteSchema, case.schema_id).doctor_id = other.id
        elif fault == "foreign_transcript":
            session.get(AIVisitTranscript, case.transcript_id).doctor_id = other.id
        elif fault == "foreign_upload":
            session.get(VisitAudioFile, case.upload_id).doctor_id = other.id
        elif fault == "missing_upload":
            session.delete(session.get(VisitAudioFile, case.upload_id))
        elif fault == "source_deleted":
            session.get(
                AIVisitTranscript, case.transcript_id
            ).deleted_at = datetime.now(UTC)
        elif fault == "off":
            monkeypatch.setattr(
                configuration,
                "SESSION_PREP_PROVIDER_SETTINGS",
                SessionPrepProviderSettings(False),
            )
        elif fault == "view_as":
            state["view_as"], expected = True, 403
        elif fault == "stale":
            body["expected_content_sha256"], expected = "0" * 64, 409
        session.commit()
    assert client.post(path, json=body).status_code == expected
    assert len(family(case)) == 1


def test_access_revoked_while_waiting_for_practice_lock_is_refused(
    endpoint, case, monkeypatch
):
    client, path, _ = endpoint
    reached = Event()
    lock = corrections.lock_practice_seats

    def observe(session, identifier):
        reached.set()
        return lock(session, identifier)

    monkeypatch.setattr(corrections, "lock_practice_seats", observe)
    with Session(case.engine) as revoker:
        practice = lock_practice_seats(revoker, case.practice_id)
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(client.post, path, json=payload(case))
            assert reached.wait(5)
            practice.billing_exempt = False
            revoker.commit()
            assert pending.result(timeout=10).status_code == 404
    assert len(family(case)) == 1


@pytest.mark.parametrize(
    "fields",
    [
        {},
        {"": "Invalid"},
        {"Plan": 42},
        {"Plan": "\0"},
        {"x" * 201: "Invalid"},
        {"Plan": "x" * 18001},
    ],
)
def test_correction_payload_bounds_and_types(fields):
    with pytest.raises(ValidationError):
        routes.CorrectSessionPrepSourceNoteRequest(
            expected_content_sha256="a" * 64, fields=fields
        )
