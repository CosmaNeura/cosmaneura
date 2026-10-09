"""Observed saved records, never clinical review, capture or edit authority."""

import hashlib
import json
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

MAX_REQUEST_BYTES = 2048
MAX_RESPONSE_BYTES = 1_048_576
MAX_NOTE_BYTES = 262_144
MAX_NOTES = 20
DEADLINE_SECONDS = 15
Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$", strict=True)]
Label = Annotated[
    str, Field(min_length=1, max_length=200, strict=True, pattern=r"^[^\x00]+$")
]
TypeSuffix = r"_[0-7][0-9a-hjkmnp-tv-z]{25}$"
BindingRef = Annotated[
    str, Field(pattern=r"^scribe_capture_binding" + TypeSuffix, strict=True)
]
ActorRef = Annotated[str, Field(pattern=r"^authority_actor" + TypeSuffix, strict=True)]
TenantRef = Annotated[
    str, Field(pattern=r"^authority_tenant" + TypeSuffix, strict=True)
]
DoctorRef = Annotated[str, Field(pattern=r"^doctor" + TypeSuffix, strict=True)]
PracticeRef = Annotated[str, Field(pattern=r"^practice" + TypeSuffix, strict=True)]
PatientRef = Annotated[str, Field(pattern=r"^patient" + TypeSuffix, strict=True)]
AppointmentRef = Annotated[
    str, Field(pattern=r"^appointment" + TypeSuffix, strict=True)
]
NoteRef = Annotated[str, Field(pattern=r"^visit_note" + TypeSuffix, strict=True)]
SchemaRef = Annotated[
    str, Field(pattern=r"^doctor_note_schema" + TypeSuffix, strict=True)
]
TranscriptRef = Annotated[
    str, Field(pattern=r"^ai_visit_transcript" + TypeSuffix, strict=True)
]
Text = Annotated[str, Field(strict=True, pattern=r"^[^\x00]*$")]


def canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def ordered_note_hash(fields: dict[str, str], order: tuple[str, ...]) -> str:
    return hashlib.sha256(
        json.dumps(
            [[key, fields[key]] for key in order],
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


class Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class RecordRequest(Closed):
    binding_ref: BindingRef
    workspace_id: UUID
    request_nonce: UUID


class RecordInteraction(Closed):
    mount_context_token: str = Field(
        min_length=16, max_length=8192, pattern=r"^[!-~]+$", repr=False, strict=True
    )
    interaction_ref: str = Field(
        min_length=16, max_length=160, pattern=r"^[A-Za-z0-9._~-]+$", strict=True
    )
    client_turn_id: UUID


class RecordPatient(Closed):
    patient_ref: PatientRef
    display_name: Label = Field(repr=False)


class RecordAppointment(Closed):
    appointment_ref: AppointmentRef
    patient_ref: PatientRef
    appointment_date: datetime
    timezone_known: bool = Field(strict=True)

    @model_validator(mode="after")
    def timezone_matches_source(self):
        if self.timezone_known != (self.appointment_date.utcoffset() is not None):
            raise ValueError("invalid_appointment_timezone")
        return self


class SavedNote(Closed):
    note_ref: NoteRef
    schema_ref: SchemaRef
    note_type: Label = Field(repr=False)
    schema_deleted: bool = Field(strict=True)
    transcript_ref: TranscriptRef
    fields: dict[Label, Text] = Field(min_length=1, max_length=100, repr=False)
    field_order: tuple[Label, ...] = Field(min_length=1, max_length=100)
    note_sha256: Digest
    created_at: datetime | None
    state: Literal["saved"] = "saved"
    review_state: Literal["not_asserted"] = "not_asserted"

    @model_validator(mode="after")
    def exact_saved_content(self):
        if (
            tuple(self.fields) != self.field_order
            or len(canonical(self.fields)) > MAX_NOTE_BYTES
        ):
            raise ValueError("invalid_saved_note")
        if ordered_note_hash(self.fields, self.field_order) != self.note_sha256:
            raise ValueError("invalid_saved_note_digest")
        return self


class TranscriptSource(Closed):
    transcript_ref: TranscriptRef
    availability: Literal["retained", "deleted"]
    created_at: datetime | None
    duration_seconds: int | None = Field(ge=0, strict=True)


class RecordContent(Closed):
    actor_ref: ActorRef
    tenant_ref: TenantRef
    doctor_ref: DoctorRef
    practice_ref: PracticeRef
    patient: RecordPatient
    appointment: RecordAppointment
    notes: tuple[SavedNote, ...] = Field(max_length=MAX_NOTES, repr=False)
    transcript_sources: tuple[TranscriptSource, ...] = Field(max_length=MAX_NOTES)

    @model_validator(mode="after")
    def exact_source_sets(self):
        if self.patient.patient_ref != self.appointment.patient_ref:
            raise ValueError("invalid_record_patient")
        order = [(n.schema_ref, n.note_ref) for n in self.notes]
        if (
            order != sorted(order)
            or len({n.schema_ref for n in self.notes}) != len(self.notes)
            or len({n.note_ref for n in self.notes}) != len(self.notes)
        ):
            raise ValueError("invalid_current_notes")
        sources = [s.transcript_ref for s in self.transcript_sources]
        if sources != sorted(set(sources)) or set(sources) != {
            n.transcript_ref for n in self.notes
        }:
            raise ValueError("invalid_transcript_sources")
        return self


class RecordSnapshot(RecordContent):
    snapshot_sha256: Digest

    @model_validator(mode="after")
    def exact_record_digest(self):
        if (
            hashlib.sha256(
                canonical(self.model_dump(mode="json", exclude={"snapshot_sha256"}))
            ).hexdigest()
            != self.snapshot_sha256
        ):
            raise ValueError("invalid_record_digest")
        return self


class RecordResponse(Closed):
    contract_version: Literal["1"] = "1"
    binding_ref: BindingRef
    workspace_id: UUID
    request_nonce: UUID
    checked_at: AwareDatetime
    record: RecordSnapshot = Field(repr=False)

    @model_validator(mode="after")
    def bounded_response(self):
        if len(self.model_dump_json().encode("utf-8")) > MAX_RESPONSE_BYTES:
            raise ValueError("record_capacity_exceeded")
        return self
