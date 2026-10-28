"""Capture-only role/schema metadata and configured model boundary tests."""

from types import SimpleNamespace

import pytest

from app.services import scribe_capture_notes as notes

from app.models.ai_visit_transcript import TranscriptEntry
from app.models.note_type_customization import NoteTypeCustomization, SubjectiveSource

NO_ROS_FIELDS = ("S - SUBJECTIVE", "O - OBJECTIVE", "A - ASSESSMENT", "P - PLAN")


def test_source_role_context_does_not_infer_from_spoken_introductions():
    entries = [
        TranscriptEntry(
            speaker="Unknown", content="I am Doctor Lane.", startTime=0, endTime=1000
        )
    ]
    context = notes.capture_retention_context(
        entries, NoteTypeCustomization(), field_names=NO_ROS_FIELDS
    )
    assert (
        "0 Patient-labeled entries; 1 entries with unidentified speaker role" in context
    )
    assert "NO role-identified Patient speech" in context
    assert "I am Doctor Lane" not in context


def test_known_patient_and_provider_preferences_remain_distinct():
    entries = [
        TranscriptEntry(
            speaker="Patient", content="My pain is mild.", startTime=0, endTime=1000
        )
    ]
    assert "NO role-identified" not in notes.capture_retention_context(
        entries, NoteTypeCustomization(), field_names=NO_ROS_FIELDS
    )
    entries[0].speaker = "Unknown"
    assert "NO role-identified" not in notes.capture_retention_context(
        entries,
        NoteTypeCustomization(subjective_source=SubjectiveSource.patient_and_doctor),
        field_names=NO_ROS_FIELDS,
    )


def test_no_patient_entries_does_not_erase_known_clinician_role():
    entries = [
        TranscriptEntry(
            speaker="Doctor",
            content="The examination was deferred.",
            startTime=0,
            endTime=1000,
        )
    ]
    context = notes.capture_retention_context(
        entries, NoteTypeCustomization(), field_names=NO_ROS_FIELDS
    )
    assert (
        "0 Patient-labeled entries; 0 entries with unidentified speaker role" in context
    )
    assert "preserve known Doctor and Other roles" in context


@pytest.mark.parametrize("heading", ["ROS:", "Review of Systems", "Systems review"])
def test_existing_ros_field_is_not_described_as_absent(heading):
    context = notes.capture_retention_context(
        [], NoteTypeCustomization(), field_names=(heading,)
    )
    assert "has NO Review of Systems/ROS field" not in context


@pytest.mark.asyncio
@pytest.mark.parametrize("plan_fields", [(), ("P - PLAN",)])
async def test_actual_template_shape_reaches_single_model_request(
    monkeypatch, plan_fields
):
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason="stop", message=SimpleNamespace(content="{}")
                )
            ]
        )

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    monkeypatch.setattr(
        notes, "openai", SimpleNamespace(with_options=lambda **_: client)
    )
    headings = ("S - SUBJECTIVE", "P - PLAN", "CODING, ATTESTATION, SIGNATURE")
    schema = notes.capture_response_schema(headings, plan_fields)
    await notes.ConfiguredNoteModel(response_schema=schema).complete(
        "Synthetic", json_output=True
    )
    assert len(calls) == 1
    contract = calls[0]["response_format"]
    assert (
        contract["type"] == "json_schema" and contract["json_schema"]["strict"] is True
    )
    actual = contract["json_schema"]["schema"]
    fields = actual["properties"]["fields"] if plan_fields else actual
    assert fields["required"] == list(headings)
    assert fields["additionalProperties"] is False
    if plan_fields:
        assert list(actual["properties"]) == ["plan_source_refs", "fields"]
        assert actual["required"] == ["plan_source_refs", "fields"]
        refs = actual["properties"]["plan_source_refs"]
        assert refs["required"] == ["P - PLAN"]
        assert refs["properties"]["P - PLAN"] == {
            "type": "array",
            "items": {"type": "integer"},
        }
