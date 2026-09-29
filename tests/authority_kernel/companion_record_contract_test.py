"""Offline closed-record and exact saved-version vectors, with invented content."""

import copy
import hashlib

import pytest
from pydantic import ValidationError

from app.services.companion_record_contract import (
    MAX_NOTE_BYTES,
    RecordResponse,
    SavedNote,
    canonical,
    ordered_note_hash,
)

SUFFIX = "_01h45ytscbebyvny4gc8cr8ma2"


def ref(kind):
    return kind + SUFFIX


def sample():
    fields = {"Plan": "Invented plan — 🙂", "History": "Invented history"}
    note = {
        "note_ref": ref("visit_note"),
        "schema_ref": ref("doctor_note_schema"),
        "note_type": "Invented SOAP",
        "schema_deleted": False,
        "transcript_ref": ref("ai_visit_transcript"),
        "fields": fields,
        "field_order": list(fields),
        "note_sha256": ordered_note_hash(fields, tuple(fields)),
        "created_at": "2020-01-01T00:00:00Z",
        "state": "saved",
        "review_state": "not_asserted",
    }
    content = {
        "actor_ref": ref("authority_actor"),
        "tenant_ref": ref("authority_tenant"),
        "doctor_ref": ref("doctor"),
        "practice_ref": ref("practice"),
        "patient": {"patient_ref": ref("patient"), "display_name": "Invented Person"},
        "appointment": {
            "appointment_ref": ref("appointment"),
            "patient_ref": ref("patient"),
            "appointment_date": "2026-09-22T09:00:00",
            "timezone_known": False,
        },
        "notes": [note],
        "transcript_sources": [
            {
                "transcript_ref": ref("ai_visit_transcript"),
                "availability": "retained",
                "created_at": "2020-01-01T00:00:00Z",
                "duration_seconds": 90,
            }
        ],
    }
    content["snapshot_sha256"] = hashlib.sha256(canonical(content)).hexdigest()
    return {
        "contract_version": "1",
        "binding_ref": ref("scribe_capture_binding"),
        "workspace_id": "00000000-0000-4000-8000-000000000001",
        "request_nonce": "00000000-0000-4000-8000-000000000002",
        "checked_at": "2026-09-22T12:00:00Z",
        "record": content,
    }


def rehash(value):
    content = value["record"]
    content["snapshot_sha256"] = hashlib.sha256(
        canonical({k: v for k, v in content.items() if k != "snapshot_sha256"})
    ).hexdigest()
    return value


def test_saved_order_unicode_and_old_retained_source_roundtrip():
    value = RecordResponse.model_validate(sample())
    assert tuple(value.record.notes[0].fields) == ("Plan", "History")
    assert value.record.transcript_sources[0].availability == "retained"
    assert value.record.notes[0].review_state == "not_asserted"
    assert RecordResponse.model_validate_json(value.model_dump_json()) == value
    assert "session_id" not in value.model_dump_json()


@pytest.mark.parametrize(
    "change",
    [
        lambda x: x.update(extra=True),
        lambda x: x["record"]["patient"].update(
            patient_ref="patient_01h45ytscbebyvny4gc8cr8ma3"
        ),
        lambda x: x["record"]["notes"][0].update(field_order=["History", "Plan"]),
        lambda x: x["record"]["notes"][0]["fields"].update(Plan="Changed text"),
        lambda x: x["record"]["notes"][0].update(review_state="reviewed"),
        lambda x: x["record"]["notes"][0].update(state="transferred"),
        lambda x: x["record"]["notes"][0].update(schema_deleted=1),
        lambda x: x["record"]["transcript_sources"][0].update(duration_seconds=True),
        lambda x: x["record"].update(transcript_sources=[]),
        lambda x: x["record"]["transcript_sources"].append(
            copy.deepcopy(x["record"]["transcript_sources"][0])
        ),
        lambda x: x["record"]["notes"].append(copy.deepcopy(x["record"]["notes"][0])),
        lambda x: x["record"]["appointment"].update(timezone_known=True),
    ],
    ids=[
        "unknown",
        "patient",
        "order",
        "text",
        "review",
        "transfer",
        "flag-type",
        "duration-type",
        "missing-source",
        "duplicate-source",
        "duplicate-current",
        "invented-zone",
    ],
)
def test_hostile_or_mismatched_projection_rejected_even_rehashed(change):
    value = sample()
    change(value)
    with pytest.raises((ValidationError, ValueError)):
        RecordResponse.model_validate(rehash(value))


def test_hash_and_field_bytes_use_utf8_and_never_truncate():
    note = sample()["record"]["notes"][0]
    note["fields"] = {"Plan": "🙂" * (MAX_NOTE_BYTES // 4)}
    note["field_order"] = ["Plan"]
    note["note_sha256"] = ordered_note_hash(note["fields"], ("Plan",))
    with pytest.raises(ValidationError):
        SavedNote.model_validate(note)


def test_empty_record_does_not_fabricate_transcript_or_note():
    value = sample()
    value["record"].update(notes=[], transcript_sources=[])
    result = RecordResponse.model_validate(rehash(value))
    assert result.record.notes == result.record.transcript_sources == ()


def test_actual_deletion_can_be_reported_without_removing_saved_note():
    value = sample()
    value["record"]["notes"][0]["schema_deleted"] = True
    value["record"]["transcript_sources"][0]["availability"] = "deleted"
    result = RecordResponse.model_validate(rehash(value))
    assert result.record.notes[0].schema_deleted
    assert result.record.transcript_sources[0].availability == "deleted"


def test_total_response_bound_is_independent_from_per_note_bound():
    value = sample()
    original = value["record"]["notes"][0]
    notes = []
    for index in range(5):
        note = copy.deepcopy(original)
        note["note_ref"] = ref("visit_note")[:-1] + str(index)
        note["schema_ref"] = ref("doctor_note_schema")[:-1] + str(index)
        note["fields"] = {"Plan": "x" * 220_000}
        note["field_order"] = ["Plan"]
        note["note_sha256"] = ordered_note_hash(note["fields"], ("Plan",))
        notes.append(note)
    value["record"]["notes"] = notes
    with pytest.raises(ValidationError, match="record_capacity_exceeded"):
        RecordResponse.model_validate(rehash(value))
