"""Actual PG selection of the narrowly scoped, non-authorizing note policy."""

import json
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from typeid import TypeID

from app.jobs import summarize
from app.services import zoom_bot_note_policy as policy

from activemodel.session_manager import _session_context
from app.models.ai_visit_transcript import AIVisitTranscript
from app.models.external_webhook_operation import ExternalWebhookOperation
from app.models.zoom_meeting import ZoomMeeting
from sqlmodel import Session

from tests.authority_kernel.scribe_encounters_test import engine as _engine
from tests.factories import AppointmentFactory, DoctorFactory, DoctorNoteSchemaFactory

engine = _engine


@contextmanager
def isolated_session(engine):
    # Model.save() commits; keep those commits inside savepoints so each test's
    # fixed synthetic cohort identity is removed by this outer rollback.
    with engine.connect() as connection:
        transaction = connection.begin()
        with Session(
            bind=connection,
            join_transaction_mode="create_savepoint",
            expire_on_commit=False,
        ) as session:
            token = _session_context.set(session)
            try:
                yield session
            finally:
                session.rollback()
                _session_context.reset(token)
        transaction.rollback()


@pytest.fixture(params=list(policy._COHORT), ids=["A", "B"])
def candidate(engine, monkeypatch, request):
    with isolated_session(engine) as session:
        doctor_id = request.param
        subject, email = policy._COHORT[doctor_id]
        doctor = DoctorFactory.build(
            id=TypeID.from_string(doctor_id), clerk_id=subject, email=email
        ).save()
        appointment = AppointmentFactory.build(doctor_id=doctor.id).save()
        schema = DoctorNoteSchemaFactory.build(doctor_id=doctor.id).save()
        transcript = AIVisitTranscript(
            doctor_id=doctor.id,
            appointment_id=appointment.id,
            patient_id=appointment.patient_id,
            duration=1,
            transcript_data=[
                {
                    "speaker": "Patient",
                    "content": "Synthetic wrist pain.",
                    "startTime": 0,
                    "endTime": 1000,
                }
            ],
        ).save()
        at = datetime.now(UTC)
        meeting = ZoomMeeting(
            doctor_id=doctor.id,
            zoom_meeting_id="synthetic-" + uuid4().hex,
            topic="Synthetic policy test",
            start_time=at,
            duration=1,
            join_url="https://zoom.invalid/synthetic",
            appointment_id=appointment.id,
            capture_authorization_occurrence_digest="a" * 64,
            capture_authorization_expires_at=at + timedelta(minutes=30),
            capture_authorization_consumed_at=at,
        ).save()
        operation = ExternalWebhookOperation(
            provider="recall",
            operation_type="ingest_transcript",
            provider_resource_digest=uuid4().hex * 2,
            state="succeeded",
            content_sha256="b" * 64,
            result_transcript_id=transcript.id,
        ).save()
        monkeypatch.setattr(policy, "is_staging", lambda: True)
        yield session, doctor, transcript, meeting, operation, schema


def test_actual_cohort_consumed_zoom_and_durable_recall_ingestion_select(candidate):
    _, doctor, transcript, *_ = candidate
    assert policy.eligible(transcript, doctor)


@pytest.mark.parametrize(
    "change",
    [
        "production",
        "subject",
        "email",
        "doctor",
        "deleted",
        "no_meeting",
        "unconsumed",
        "foreign_appointment",
        "no_ingestion",
        "failed_ingestion",
        "other_provider",
        "other_transcript",
        "two_meetings",
    ],
)
def test_every_scope_boundary_falls_back_to_unchanged_default(
    candidate, monkeypatch, change
):
    session, doctor, transcript, meeting, operation, _ = candidate
    if change == "production":
        monkeypatch.setattr(policy, "is_staging", lambda: False)
    elif change in {"subject", "email"}:
        setattr(
            doctor,
            "clerk_id" if change == "subject" else "email",
            "user_foreign_synthetic"
            if change == "subject"
            else "foreign-synthetic@example.invalid",
        )
    elif change == "doctor":
        transcript = transcript.model_copy(update={"doctor_id": TypeID("doctor")})
    elif change == "deleted":
        doctor.deleted_at = datetime.now(UTC)
    elif change == "no_meeting":
        session.delete(meeting)
    elif change == "unconsumed":
        meeting.capture_authorization_consumed_at = None
    elif change == "foreign_appointment":
        meeting.appointment_id = TypeID("appointment")
    elif change == "no_ingestion":
        session.delete(operation)
    elif change == "failed_ingestion":
        operation.state = "failed"
    elif change == "other_provider":
        operation.provider = "zoom"
    elif change == "other_transcript":
        operation.result_transcript_id = None
    else:
        session.add(
            ZoomMeeting(
                doctor_id=doctor.id,
                zoom_meeting_id="another-synthetic-" + uuid4().hex,
                topic="Synthetic ambiguous mapping",
                start_time=meeting.start_time,
                duration=1,
                join_url="https://zoom.invalid/synthetic",
                appointment_id=meeting.appointment_id,
                capture_authorization_occurrence_digest="c" * 64,
                capture_authorization_expires_at=meeting.capture_authorization_expires_at,
                capture_authorization_consumed_at=meeting.capture_authorization_consumed_at,
            )
        )
    session.flush()
    assert not policy.eligible(transcript, doctor)


@pytest.mark.parametrize("hint", [None, "unknown_policy", policy.POLICY])
def test_perform_requires_explicit_hint_and_rechecks_live_scope(
    candidate, monkeypatch, hint
):
    _, doctor, transcript, _, _, schema = candidate
    generated = []
    monkeypatch.setattr(summarize, "doctor_access_in_force", lambda _: True)
    monkeypatch.setattr(
        summarize,
        "generate_clinical_note",
        lambda **kw: generated.append(kw) or {"Plan": "Synthetic action."},
    )
    monkeypatch.setattr(summarize, "_finalize_generated_note", lambda **_: True)
    summarize.perform(str(transcript.id), str(schema.id), note_policy=hint)
    assert len(generated) == 1
    assert generated[0].get("bot_fidelity", False) == (hint == policy.POLICY)
    doctor.email = "changed-synthetic@example.invalid"
    generated.clear()
    summarize.perform(str(transcript.id), str(schema.id), note_policy=hint)
    assert len(generated) == 1 and "bot_fidelity" not in generated[0]


@pytest.mark.parametrize("error_kind", ["contract", "ordinary", "bot_transport"])
def test_real_task_wrapper_excludes_only_bot_contract_failure(
    candidate, monkeypatch, error_kind
):
    from celery.exceptions import Retry

    from app.services.zoom_bot_note_grounding import BotNoteContractInvalid

    from app.models.ai_visit_note import AIVisitNote

    invalid_contract = error_kind == "contract"
    _, _, transcript, _, _, schema = candidate
    schema.fields = {"Plan": "Planned care"}
    schema.save()
    monkeypatch.setattr(summarize, "doctor_access_in_force", lambda _: True)
    task = summarize.perform_celery._get_current_object()
    calls, retries = [], []

    def create(**kwargs):
        calls.append(kwargs)
        if not invalid_contract:
            raise OSError("synthetic ordinary transport error")
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(
                        content='{"fields":{"Plan":"Unchecked prose"},"plan_source_refs":{"Plan":[999]},"source_review":{"unresolved_questions":[],"medications":{"current_list_refs":[],"change_history_refs":[]}}}'
                    ),
                )
            ]
        )

    def retry(**kwargs):
        retries.append(kwargs)
        raise Retry()

    monkeypatch.setattr(summarize.openai.chat.completions, "create", create)
    monkeypatch.setattr(task, "retry", retry)
    monkeypatch.setattr(
        summarize,
        "_finalize_generated_note",
        lambda **_: pytest.fail("invalid note persisted"),
    )
    with pytest.raises(BotNoteContractInvalid if invalid_contract else Retry):
        task.run(
            str(transcript.id),
            str(schema.id),
            note_policy=None if error_kind == "ordinary" else policy.POLICY,
        )
    assert len(calls) == 1
    assert len(retries) == (0 if invalid_contract else 1)
    assert task.autoretry_for == (Exception,) and task.max_retries == 20
    assert task.dont_autoretry_for == (BotNoteContractInvalid,)
    assert AIVisitNote.get(appointment_id=transcript.appointment_id) is None


def test_grounded_plan_persists_and_same_transcript_replay_has_no_model_call(
    candidate, monkeypatch
):
    from app.models.ai_visit_note import AIVisitNote
    from app.models.ai_visit_transcript import TranscriptEntry

    session, _, transcript, _, _, schema = candidate
    schema.fields = {
        "Objective": "Examination data",
        "Assessment": "Tentative impression",
        "Plan": "Planned care",
    }
    schema.save()
    transcript.transcript_data = [
        TranscriptEntry(
            speaker="Unknown", content=text, startTime=i * 1000, endTime=(i + 1) * 1000
        ).model_dump()
        for i, text in enumerate(
            [
                "Have you had a fall or another injury?",
                "There was no fall.",
                "We will follow up next week.",
            ]
        )
    ]
    transcript.save()
    monkeypatch.setattr(summarize, "doctor_access_in_force", lambda _: True)
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        content = json.dumps(
            {
                "fields": {
                    "Objective": "Ignored model prose",
                    "Assessment": "Not mentioned in transcript",
                    "Plan": "Ignored model prose",
                },
                "plan_source_refs": {"Plan": [3]},
                "reported_history_source_refs": {"Objective": [1, 2]},
                "source_review": {
                    "unresolved_questions": [
                        {
                            "question_ref": 1,
                            "answer_refs": [2],
                            "unanswered_parts": ["another injury"],
                        }
                    ],
                    "medications": {"current_list_refs": [], "change_history_refs": []},
                },
            }
        )
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason="stop", message=SimpleNamespace(content=content)
                )
            ]
        )

    monkeypatch.setattr(summarize.openai.chat.completions, "create", create)
    summarize.perform(str(transcript.id), str(schema.id), note_policy=policy.POLICY)
    session.expire_all()
    note = AIVisitNote.get(appointment_id=transcript.appointment_id)
    # Entire literal expectation was reviewed independently of resolver constants.
    # Handcrafted selection keeps the partially answered question. It is not
    # proof that a configured model will select or classify that context.
    expected_note = {
        "Objective": (
            "Current medication list is unverified; review the source statements.\n\n"
            "Reported transcript history:\n"
            "Transcript — Unidentified speaker:\nHave you had a fall or another injury?\n\n"
            "Transcript — Unidentified speaker:\nThere was no fall.\n\n"
            'Answer not established for: "another injury".'
        ),
        "Assessment": "Not mentioned in transcript",
        "Plan": (
            "Transcript — Unidentified speaker:\nWe will follow up next week.\n\n"
            "Rationale beyond what the selected source states is not established.\n\n"
            "This note was generated with AI assistance."
        ),
    }
    assert note and note.note == expected_note
    note_id = note.id
    summarize.perform(str(transcript.id), str(schema.id), note_policy=policy.POLICY)
    assert len(calls) == 1
    session.expire_all()
    replayed = AIVisitNote.get(appointment_id=transcript.appointment_id)
    assert replayed and replayed.id == note_id and replayed.note == expected_note


def test_capture_owned_appointment_never_enters_bot_adapter(candidate, monkeypatch):
    _, _, transcript, _, _, schema = candidate
    monkeypatch.setattr(summarize, "doctor_access_in_force", lambda _: True)
    monkeypatch.setattr(summarize, "_capture_owns_appointment", lambda *_: True)
    monkeypatch.setattr(
        summarize,
        "generate_clinical_note",
        lambda **_: pytest.fail("capture source reached bot"),
    )
    summarize.perform(str(transcript.id), str(schema.id), note_policy=policy.POLICY)


def test_perform_marks_only_inserted_merge_boundaries(candidate, monkeypatch):
    session, _, transcript, _, _, schema = candidate
    AIVisitTranscript(
        doctor_id=transcript.doctor_id,
        appointment_id=transcript.appointment_id,
        patient_id=transcript.patient_id,
        duration=1,
        transcript_data=[
            {
                "speaker": "Unknown",
                "content": "Later report.",
                "startTime": 0,
                "endTime": 1000,
            }
        ],
        created_at=transcript.created_at + timedelta(seconds=2),
    ).save()
    seen = []
    monkeypatch.setattr(summarize, "doctor_access_in_force", lambda _: True)
    monkeypatch.setattr(
        summarize,
        "generate_clinical_note",
        lambda **kw: seen.append(kw) or {"Plan": "Synthetic"},
    )
    monkeypatch.setattr(summarize, "_finalize_generated_note", lambda **_: True)
    summarize.perform(str(transcript.id), str(schema.id), note_policy=policy.POLICY)
    assert len(seen) == 1 and seen[0]["bot_segment_boundaries"] == (2,)
    assert [x.content for x in seen[0]["transcript"].transcript_data] == [
        "Synthetic wrist pain.",
        "[Segment 2 starts]",
        "Later report.",
    ]
