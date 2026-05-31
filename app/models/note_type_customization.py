from enum import Enum

from activemodel import BaseModel
from activemodel.mixins import SoftDeletionMixin, TimestampsMixin, TypeIDMixin
from activemodel.types import TypeIDType
from sqlmodel import Field, Relationship

from .doctor_note_schema import DoctorNoteSchema


class SubjectiveSource(str, Enum):
    patient_only = "patient_only"
    patient_and_doctor = "patient_and_doctor"


class PronounStyle(str, Enum):
    infer_from_transcript = "infer_from_transcript"
    neutral = "neutral"


class PlanOrdering(str, Enum):
    patient_then_doctor = "patient_then_doctor"
    as_discussed = "as_discussed"


class NoteTypeCustomization(
    BaseModel,
    TimestampsMixin,
    SoftDeletionMixin,
    TypeIDMixin("note_type_customization"),
    table=True,
):
    """Per-note-type overrides for the configurable note-generation behaviors.

    A missing row (or a row with all defaults) reproduces the platform's
    current note-generation behavior. Edits affect only this one
    DoctorNoteSchema — never another provider or note type.
    """

    doctor_note_schema_id: TypeIDType = DoctorNoteSchema.foreign_key(unique=True)
    doctor_note_schema: DoctorNoteSchema = Relationship(back_populates="customization")

    subjective_source: SubjectiveSource = Field(default=SubjectiveSource.patient_only)
    pronoun_style: PronounStyle = Field(default=PronounStyle.infer_from_transcript)
    plan_ordering: PlanOrdering = Field(default=PlanOrdering.patient_then_doctor)
    flag_new_medications: bool = Field(default=True)
    require_plan_reason: bool = Field(default=True)
    ros_distinct_from_subjective: bool = Field(default=True)

    custom_instructions: str | None = Field(default=None)
    "Free-text provider instructions appended verbatim to the prompt; null = none."


def default_customization() -> NoteTypeCustomization:
    """A transient (unsaved) record carrying the platform defaults.

    Used by callers that need the effective config when no row exists.
    Reading only the preset fields is safe without a schema id. Do NOT
    call .save() on it — doctor_note_schema_id is unset and the NOT NULL
    constraint would raise an IntegrityError.
    """
    return NoteTypeCustomization()
