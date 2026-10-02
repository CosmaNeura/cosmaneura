import hashlib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.configuration import billing_header as config
from app.configuration.clerk import VerifiedProviderSession
from app.routes.billing_header import billing_header_api
from app.services import billing_header_operations as service
from app.services.billing_header_contract import (
    PrepareBillingHeaderOperation,
    project_note,
)

from activemodel.session_manager import aglobal_session, get_session, global_session
from app.models.ai_visit_note import AIVisitNote
from app.models.ai_visit_transcript import AIVisitTranscript
from app.models.appointment import Appointment
from app.models.billing_header_operation import (
    BillingHeaderPreparedOperation as Operation,
)
from app.models.doctor import Doctor
from app.models.doctor_note_schema import DoctorNoteSchema
from app.models.patient import Patient
from app.models.practice import Practice, PracticeMember, lock_practice_seats
from sqlalchemy import text
from sqlmodel import select

VISIT = {
    "patientAge": 31,
    "patientSex": "F",
    "encounterType": "office_established",
    "placeOfService": "11",
    "providerSpecialty": "Family medicine",
    "serviceDate": "2026-09-28",
}
ISSUER = "https://clerk.test.invalid"
NOTE = {"Assessment": "Fictional wellness encounter.", "Plan": "Review in six months."}


@pytest.fixture
def case(billing_database, monkeypatch):
    suffix = uuid4().hex
    now = datetime.now(UTC)
    with get_session() as s:
        doctor = Doctor(
            clerk_id="user_" + suffix, email=suffix + "@example.invalid", biography=""
        )
        practice = Practice(
            legal_name="Synthetic billing practice",
            join_code=suffix[:8],
            email_domain="example.invalid",
            billing_exempt=True,
            negotiated_baa_on_file=True,
            negotiated_baa_effective_date=now - timedelta(days=1),
            negotiated_baa_reference="Synthetic BAA",
        )
        s.add(doctor)
        s.add(practice)
        s.flush()
        lock_practice_seats(s, practice.id)
        s.add(
            PracticeMember(
                doctor_id=doctor.id,
                practice_id=practice.id,
                role="workforce",
                attested_at=now,
                attestation_name="Synthetic provider",
            )
        )
        patient = Patient(doctor_id=doctor.id, name="Fictional patient")
        schema = DoctorNoteSchema(
            doctor_id=doctor.id,
            note_type="Synthetic",
            fields={"Assessment": "", "Plan": ""},
        )
        s.add(patient)
        s.add(schema)
        s.flush()
        appointment = Appointment(
            doctor_id=doctor.id,
            patient_id=patient.id,
            external_id=None,
            appointment_date=now,
        )
        s.add(appointment)
        s.flush()
        transcript = AIVisitTranscript(
            doctor_id=doctor.id,
            appointment_id=appointment.id,
            patient_id=patient.id,
            duration=1200,
            transcript_data=[],
        )
        s.add(transcript)
        s.flush()
        note = AIVisitNote(
            doctor_id=doctor.id,
            appointment_id=appointment.id,
            ai_transcript_id=transcript.id,
            doctor_note_schema_id=schema.id,
            note=dict(NOTE),
        )
        s.add(note)
        s.flush()
        actor = VerifiedProviderSession(
            ISSUER, doctor.clerk_id, hashlib.sha256(b"synthetic-session").hexdigest()
        )
        admission = config.BillingSourceAdmission(
            admissionId="test-" + suffix,
            practiceId=str(practice.id),
            doctorId=str(doctor.id),
            appointmentId=str(appointment.id),
            patientId=str(patient.id),
            noteId=str(note.id),
            contentSha256=project_note(NOTE)[2],
            dataClass="synthetic",
            reviewRef="authored-local-test",
            expiresAt=(now + timedelta(hours=1)).isoformat(),
        )
        ids = SimpleNamespace(
            actor=actor,
            note_id=note.id,
            doctor_id=doctor.id,
            practice_id=practice.id,
            patient_id=patient.id,
            appointment_id=appointment.id,
            admission=admission,
        )
        s.commit()
    monkeypatch.setattr(
        config,
        "BILLING_SOURCE_POLICY",
        config.BillingSourcePolicy(
            enabled=True, clerkIssuer=ISSUER, admissions=(admission,)
        ),
    )
    return ids


def command_for(case, **changes):
    source = service.read_source(case.actor, case.note_id).source
    return PrepareBillingHeaderOperation.model_validate(
        dict(
            operationId=str(uuid4()),
            expectedContentSha256=source.contentSha256,
            expectedTextSha256=source.textSha256,
            confirmedComplete=True,
            visit=VISIT,
        )
        | changes
    )


def test_actual_expand_prepare_exact_replay_and_immutable_source(
    case, billing_database, monkeypatch
):
    assert str(service.__file__).startswith(
        str(__import__("pathlib").Path(__file__).resolve().parents[2])
    )
    cmd = command_for(case)
    first = service.prepare_operation(case.actor, case.note_id, cmd)
    assert (
        first.state == "prepared"
        and first.transport.state == "not_configured"
        and not first.replayed
    )
    assert first.sourceRevision == 1
    monkeypatch.setattr(config, "BILLING_SOURCE_POLICY", config.BillingSourcePolicy())
    replay = service.prepare_operation(case.actor, case.note_id, cmd)
    assert (
        replay.model_dump(exclude={"replayed"})
        == first.model_dump(exclude={"replayed"})
        and replay.replayed
    )
    read = service.read_operation(case.actor, UUID(cmd.operationId))
    assert read.replayed
    with get_session() as s:
        row = s.exec(
            select(Operation).where(Operation.operation_id == UUID(cmd.operationId))
        ).one()
        assert (
            row.ordered_content == list(map(list, NOTE.items())) and row.visit == VISIT
        )
        assert (
            row.actor["subject"] == case.actor.subject
            and row.actor["purpose"] == "billing-header.prepare"
        )
        assert row.actor["sessionSha256"] == case.actor.session_sha256
        assert row.admission["contentSha256"] == cmd.expectedContentSha256
        assert "transcript" not in row.projected_text
        assert s.get(AIVisitNote, case.note_id).note == NOTE
    for sql in [
        "UPDATE billing_header_prepared_operation SET source_revision=source_revision",
        "DELETE FROM billing_header_prepared_operation",
        "TRUNCATE billing_header_prepared_operation",
    ]:
        with pytest.raises(Exception, match="immutable"):
            with billing_database.begin() as c:
                c.execute(text(sql))
    with pytest.raises(service.BillingPreparationError, match="not_configured"):
        service.prepare_operation(
            case.actor,
            case.note_id,
            cmd.model_copy(update={"operationId": str(uuid4())}),
        )


def test_changed_context_uuid_conflict_and_explicit_new_revision(case):
    cmd = command_for(case)
    service.prepare_operation(case.actor, case.note_id, cmd)
    changed = PrepareBillingHeaderOperation.model_validate(
        cmd.model_dump() | {"visit": VISIT | {"patientAge": 32}}
    )
    with pytest.raises(service.BillingPreparationError, match="conflict"):
        service.prepare_operation(case.actor, case.note_id, changed)
    new = changed.model_copy(update={"operationId": str(uuid4())})
    assert service.prepare_operation(case.actor, case.note_id, new).sourceRevision == 2


def test_current_actor_member_and_note_are_rechecked(case):
    cmd = command_for(case)
    service.prepare_operation(case.actor, case.note_id, cmd)
    other = VerifiedProviderSession(ISSUER, "user_other", case.actor.session_sha256)
    with pytest.raises(service.BillingPreparationError):
        service.prepare_operation(other, case.note_id, cmd)
    with get_session() as s:
        lock_practice_seats(s, case.practice_id)
        member = s.exec(
            select(PracticeMember).where(PracticeMember.doctor_id == case.doctor_id)
        ).one()
        member.seat_state = "inactive"
        s.add(member)
        s.commit()
    with pytest.raises(service.BillingPreparationError):
        service.prepare_operation(case.actor, case.note_id, cmd)


def test_changed_or_superseded_note_rejects_old_retry(case):
    cmd = command_for(case)
    service.prepare_operation(case.actor, case.note_id, cmd)
    with get_session() as s:
        note = s.get(AIVisitNote, case.note_id)
        note.note = NOTE | {"Plan": "Changed source."}
        s.add(note)
        s.commit()
    with pytest.raises(service.BillingPreparationError, match="changed"):
        service.prepare_operation(case.actor, case.note_id, cmd)
    with get_session() as s:
        note = s.get(AIVisitNote, case.note_id)
        note.superseded_at = datetime.now(UTC)
        note.supersession_reason = "test"
        s.add(note)
        s.commit()
    with pytest.raises(service.BillingPreparationError, match="changed"):
        service.read_source(case.actor, case.note_id)


def test_map_uses_exact_displayed_ordered_snapshot_and_does_not_refresh_it(
    case, billing_database
):
    with global_session() as s:
        note = s.get(AIVisitNote, case.note_id)
        assert str(case.note_id) in service.admitted_source_map(case.actor, [note])
        s.rollback()  # release source locks before independent source update
        note = s.get(AIVisitNote, case.note_id)
        original = dict(note.note)
        with billing_database.begin() as c:
            c.execute(
                AIVisitNote.__table__.update()
                .where(AIVisitNote.id == case.note_id)
                .values(note=NOTE | {"Plan": "New current content."})
            )
        assert service.admitted_source_map(case.actor, [note]) == {}
        assert note.note == original


def test_admission_revocation_before_commit_rolls_back_operation(case, monkeypatch):
    cmd = command_for(case)
    original = service._admission
    calls = []

    def revoke(scope, actor):
        calls.append(1)
        return original(scope, actor) if len(calls) == 1 else None

    monkeypatch.setattr(service, "_admission", revoke)
    with pytest.raises(service.BillingPreparationError):
        service.prepare_operation(case.actor, case.note_id, cmd)
    with get_session() as s:
        assert (
            s.exec(select(Operation).where(Operation.note_id == case.note_id)).first()
            is None
        )


def test_real_routes_current_authorization_and_no_store_receipt(case, monkeypatch):
    app = FastAPI()
    app.include_router(
        billing_header_api,
        prefix="/internal/v1",
        dependencies=[Depends(aglobal_session)],
    )
    state = {"signed_in": True, "view_as": None}

    @app.middleware("http")
    async def normalized_fixture(request, call_next):
        request.state.auth_state = SimpleNamespace(
            is_signed_in=state["signed_in"],
            payload={
                "iss": ISSUER,
                "sub": case.actor.subject,
                "sid": "synthetic-session",
            },
        )
        request.state.doctor = SimpleNamespace(clerk_id=case.actor.subject)
        request.state.view_as = state["view_as"]
        return await call_next(request)

    client = TestClient(app)
    path = f"/internal/v1/notes/revisions/{case.note_id}"
    source = client.get(path + "/billing-header-source")
    assert source.status_code == 200 and source.headers["cache-control"] == "no-store"
    cmd = command_for(case)
    saved = client.post(
        path + "/billing-header-operations", json=cmd.model_dump(exclude_unset=True)
    )
    assert (
        saved.status_code == 200 and saved.json()["source"] == source.json()["source"]
    )
    assert "projected_text" not in saved.text and "sessionSha256" not in saved.text
    assert client.get(
        "/internal/v1/billing-header-operations/" + cmd.operationId
    ).json()["replayed"]
    state["signed_in"] = False
    assert client.get(path + "/billing-header-source").status_code == 401
    state["signed_in"] = True
    state["view_as"] = SimpleNamespace()
    assert (
        client.post(
            path + "/billing-header-operations", json=cmd.model_dump(exclude_unset=True)
        ).status_code
        == 403
    )


def test_patient_reassignment_denies_old_receipt_and_capability(case):
    cmd = command_for(case)
    service.prepare_operation(case.actor, case.note_id, cmd)
    with get_session() as s:
        replacement = Patient(
            doctor_id=case.doctor_id, name="Different fictional patient"
        )
        s.add(replacement)
        s.flush()
        appointment = s.get(Appointment, case.appointment_id)
        appointment.patient_id = replacement.id
        s.add(appointment)
        s.commit()
    with pytest.raises(service.BillingPreparationError):
        service.prepare_operation(case.actor, case.note_id, cmd)
    with pytest.raises(service.BillingPreparationError):
        service.read_operation(case.actor, UUID(cmd.operationId))
    source = service.read_source(case.actor, case.note_id)
    assert source.latestOperation is None and source.state == "not_configured"
