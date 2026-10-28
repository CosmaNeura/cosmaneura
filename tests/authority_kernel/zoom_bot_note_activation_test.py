"""Actual PostgreSQL activation, exact persistence and replay controls."""

from datetime import timedelta

import pytest

from app.jobs import summarize
from app.services import zoom_bot_note_policy as policy

from app.models.ai_visit_transcript import AIVisitTranscript, TranscriptEntry

from tests.authority_kernel.zoom_bot_note_policy_test import candidate as _candidate
from tests.authority_kernel.zoom_bot_note_policy_test import engine as _engine

candidate = _candidate
engine = _engine


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
    later = AIVisitTranscript(
        doctor_id=transcript.doctor_id,
        appointment_id=transcript.appointment_id,
        patient_id=transcript.patient_id,
        duration=1,
        transcript_data=[
            TranscriptEntry(
                speaker="Unknown",
                content="Later report.",
                startTime=0,
                endTime=1000,
            )
        ],
    )
    later.created_at = transcript.created_at + timedelta(seconds=2)
    later.save()
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


@pytest.mark.parametrize(
    "source_mode", ["unverified", "coarse_unverified", "classified"]
)
def test_actual_perform_persists_exact_fields_and_replay_makes_no_model_call(
    candidate, monkeypatch, source_mode
):
    import json
    from types import SimpleNamespace

    from app.services import zoom_bot_source_passages as passages

    from app.models.ai_visit_note import AIVisitNote
    from app.models.ai_visit_transcript import TranscriptEntry

    session, _, transcript, _, _, schema = candidate
    schema.fields = {
        "Objective": "History",
        "Assessment": "Impression",
        "Plan": "Planned care",
    }
    if source_mode == "classified":
        schema.fields = {"Subjective": "Patient history", **schema.fields}
    schema.save()
    transcript.transcript_data = [
        TranscriptEntry(
            speaker="Unverified speaker (Shared participant)",
            content=text,
            startTime=i * 1000,
            endTime=(i + 1) * 1000,
        )
        for i, text in enumerate(
            ["Any falls or other injuries?", "No falls.", "Return in ten days."]
        )
    ]
    if source_mode == "classified":
        transcript.transcript_data = [
            TranscriptEntry(
                speaker=role, content=text, startTime=i * 1000, endTime=(i + 1) * 1000
            )
            for i, (role, text) in enumerate(
                [
                    ("Doctor", "Any falls or other injuries?"),
                    ("Patient", "No falls."),
                    ("Patient", "Symptoms are mild."),
                    ("Doctor", "Return in ten days."),
                ]
            )
        ]
    if source_mode == "coarse_unverified":
        transcript.transcript_data = [
            transcript.transcript_data[0].model_copy(
                update={
                    "content": " ".join(["A neutral introductory sentence."] * 35)
                    + " Any falls or other injuries? No falls. Return in ten days.",
                    "endTime": 3000,
                }
            )
        ]
    transcript.save()
    original_entries = [entry.model_dump() for entry in transcript.transcript_data]
    prepared, _, _ = passages.prepare(transcript)
    plan_refs = [
        index
        for index, entry in enumerate(prepared.transcript_data, 1)
        if entry.content == "Return in ten days."
    ]
    history_refs = [
        index
        for index, entry in enumerate(prepared.transcript_data, 1)
        if entry.content in {"Any falls or other injuries?", "No falls."}
    ]
    assert len(plan_refs) == 1 and len(history_refs) == 2
    monkeypatch.setattr(summarize, "doctor_access_in_force", lambda _: True)
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        payload = {
            "plan_source_refs": {"Plan": plan_refs},
            "fields": {
                key: "Not mentioned in transcript." if key == "Assessment" else ""
                for key in schema.fields
            },
            "source_context_refs": {
                "question_scopes": [
                    {"question_ref": index, "source_refs": []}
                    for index, entry in enumerate(prepared.transcript_data, 1)
                    if "?" in entry.content
                ],
                "other_source_refs": [],
            },
        }
        if source_mode == "classified":
            payload["primary_history_source_kinds"] = {"3": "clinical_history"}
            assert set(
                kwargs["response_format"]["json_schema"]["schema"]["properties"][
                    "primary_history_source_kinds"
                ]["properties"]
            ) == {"3"}
        else:
            payload["reported_history_source_refs"] = {
                "Objective": {"source_refs": history_refs}
            }
        raw = json.dumps(payload)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason="stop", message=SimpleNamespace(content=raw)
                )
            ]
        )

    monkeypatch.setattr(
        summarize.openai,
        "with_options",
        lambda **_: SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        ),
    )
    summarize.perform(str(transcript.id), str(schema.id), note_policy=policy.POLICY)
    session.expire_all()
    note = AIVisitNote.get(appointment_id=transcript.appointment_id)
    expected = {
        "Objective": "Current medication list is unverified; review the source statements.\n\nSelected transcript source context (questions are not findings):\nUnidentified speaker:\n- Any falls or other injuries?\n- No falls.",
        "Assessment": "Not mentioned in transcript.",
        "Plan": "- Unidentified speaker: Return in ten days.\n\nReasons beyond those stated in the selected source are not established.\n\nThis note was generated with AI assistance.",
    }
    if source_mode == "classified":
        expected = {
            "Subjective": "Model-labeled patient:\n- No falls.\n- Symptoms are mild.",
            "Objective": "Speaker roles are model-classified and require review.\n\nCurrent medication list is unverified; review the source statements.\n\nSelected transcript source context (questions are not findings):\n- Model-labeled clinician: Any falls or other injuries?",
            "Assessment": "Not mentioned in transcript.",
            "Plan": "- Model-labeled clinician: Return in ten days.\n\nReasons beyond those stated in the selected source are not established.\n\nThis note was generated with AI assistance.",
        }
    assert note is not None and note.note == expected
    original_id = note.id
    summarize.perform(str(transcript.id), str(schema.id), note_policy=policy.POLICY)
    session.expire_all()
    reopened = AIVisitNote.get(appointment_id=transcript.appointment_id)
    persisted = AIVisitTranscript.get(id=transcript.id)
    assert persisted is not None
    assert [
        entry.model_dump() for entry in persisted.transcript_data
    ] == original_entries
    assert len(calls) == 1
    assert (
        reopened is not None
        and reopened.id == original_id
        and reopened.note == expected
    )
