"""Actual PostgreSQL regression for unused mounts versus claimed Scribe sources."""

from uuid import uuid4

import pytest

from app.jobs import summarize
from app.services import zoom_bot_note_policy as policy
from app.services import zoom_unused_capture_mount as fallback
from app.services.recall_webhook_operations import _content_digest

from app.models.ai_visit_note import AIVisitNote
from app.models.scribe_capture_admission import ScribeCaptureAdmission
from app.models.scribe_capture_binding import ScribeCaptureBinding
from app.models.scribe_transcript_delivery import ScribeTranscriptDelivery
from app.models.zoom_capture_binding import ZoomCaptureBinding

from tests.authority_kernel.scribe_capture_scope_test import make_scope_case
from tests.authority_kernel.zoom_bot_note_policy_test import candidate as _candidate
from tests.authority_kernel.zoom_bot_note_policy_test import engine as _engine

candidate = _candidate
engine = _engine


@pytest.fixture
def unused_mount(candidate, monkeypatch):
    session, doctor, transcript, meeting, operation, schema = candidate
    # A real mounted-binding row with all referenced authority rows present.
    # It is not used to authorize the Zoom source; the consumed durable Zoom
    # binding and canonical Recall receipt independently provide that scope.
    scope = make_scope_case(session)
    binding = ScribeCaptureBinding(
        workspace_id=uuid4(),
        provider_link_id=scope.link.id,
        actor_id=scope.actor.id,
        tenant_id=scope.tenant.id,
        source_identity_id=scope.identity.id,
        person_binding_id=scope.person_binding.id,
        doctor_id=doctor.id,
        practice_id=scope.practice.id,
        patient_id=transcript.patient_id,
        appointment_id=transcript.appointment_id,
        scribe_tenant_id=scope.link.scribe_tenant_id,
        scope_sha256="e" * 64,
    )
    session.add(binding)
    session.add(
        ZoomCaptureBinding(
            zoom_meeting_id=meeting.id,
            doctor_id=doctor.id,
            appointment_id=transcript.appointment_id,
            patient_id=transcript.patient_id,
            occurrence_digest=meeting.capture_authorization_occurrence_digest,
            authorization_consumed_at=meeting.capture_authorization_consumed_at,
            bot_digest="c" * 64,
            recording_digest="d" * 64,
        )
    )
    operation.content_sha256 = _content_digest(
        doctor_id=str(doctor.id),
        appointment_id=str(transcript.appointment_id),
        patient_id=str(transcript.patient_id),
        duration=transcript.duration,
        entries=transcript.transcript_data,
    )
    session.add(operation)
    session.flush()
    monkeypatch.setattr(summarize, "doctor_access_in_force", lambda _: True)
    monkeypatch.setattr(fallback, "doctor_access_in_force", lambda _: True)
    monkeypatch.setattr(
        summarize, "augment_clinical_note", lambda original, **_: original
    )
    yield session, transcript, schema, binding


def _claim_admission(session, binding):
    row = ScribeCaptureAdmission(
        binding_id=binding.id,
        scribe_tenant_id=binding.scribe_tenant_id,
        request_sha256="a" * 64,
        client_key_sha256="b" * 64,
        consent_payload={},
        status="consent_pending",
    )
    session.add(row)
    session.flush()


def test_unused_mount_actual_zoom_source_generates_and_persists(
    unused_mount, monkeypatch
):
    session, transcript, schema, _ = unused_mount
    calls = []
    monkeypatch.setattr(
        summarize,
        "generate_clinical_note",
        lambda **kw: calls.append(kw) or {"Plan": "Synthetic recorded follow-up."},
    )
    summarize.perform(transcript.id, schema.id, note_policy=policy.POLICY)
    session.expire_all()
    note = AIVisitNote.get(appointment_id=transcript.appointment_id)
    assert len(calls) == 1 and calls[0]["bot_fidelity"] is True
    assert note is not None and note.ai_transcript_id == transcript.id
    assert note.note == {"Plan": "Synthetic recorded follow-up."}


def test_even_pending_scribe_admission_blocks_zoom_model(unused_mount, monkeypatch):
    session, transcript, schema, binding = unused_mount
    _claim_admission(session, binding)
    monkeypatch.setattr(
        summarize,
        "generate_clinical_note",
        lambda **_: pytest.fail("claimed Scribe source reached Zoom model"),
    )
    summarize.perform(transcript.id, schema.id, note_policy=policy.POLICY)
    assert AIVisitNote.get(appointment_id=transcript.appointment_id) is None


def test_scribe_admission_claimed_during_model_blocks_publication(
    unused_mount, monkeypatch
):
    session, transcript, schema, binding = unused_mount

    def generate(**_):
        _claim_admission(session, binding)
        return {"Plan": "Must not publish over claimed Scribe source."}

    monkeypatch.setattr(summarize, "generate_clinical_note", generate)
    summarize.perform(transcript.id, schema.id, note_policy=policy.POLICY)
    assert AIVisitNote.get(appointment_id=transcript.appointment_id) is None


def test_existing_human_note_is_preserved(unused_mount, monkeypatch):
    session, transcript, schema, _ = unused_mount
    note = AIVisitNote(
        doctor_id=transcript.doctor_id,
        appointment_id=transcript.appointment_id,
        ai_transcript_id=transcript.id,
        doctor_note_schema_id=schema.id,
        note={"Plan": "Retained clinician edits."},
    ).save()
    monkeypatch.setattr(
        summarize,
        "generate_clinical_note",
        lambda **_: pytest.fail("existing note reached Zoom model"),
    )
    summarize.perform(transcript.id, schema.id, note_policy=policy.POLICY)
    session.expire_all()
    retained = AIVisitNote.get(id=note.id)
    assert retained is not None
    assert retained.note == {"Plan": "Retained clinician edits."}


def test_published_scribe_delivery_blocks_zoom_source(unused_mount, monkeypatch):
    session, transcript, schema, _ = unused_mount
    session.add(
        ScribeTranscriptDelivery(
            caller_client_id=str(uuid4()),
            idempotency_key="synthetic-retained-source",
            scribe_session_id=uuid4(),
            appointment_id=transcript.appointment_id,
            transcript_id=transcript.id,
            payload_sha256="f" * 64,
        )
    )
    session.flush()
    monkeypatch.setattr(
        summarize,
        "generate_clinical_note",
        lambda **_: pytest.fail("published Scribe source reached Zoom model"),
    )
    summarize.perform(transcript.id, schema.id, note_policy=policy.POLICY)
    assert AIVisitNote.get(appointment_id=transcript.appointment_id) is None


def test_policy_hint_alone_cannot_release_unused_mount(unused_mount, monkeypatch):
    _, transcript, schema, _ = unused_mount
    monkeypatch.setattr(
        summarize,
        "generate_clinical_note",
        lambda **_: pytest.fail("unselected source reached Zoom model"),
    )
    summarize.perform(transcript.id, schema.id)
    assert AIVisitNote.get(appointment_id=transcript.appointment_id) is None
