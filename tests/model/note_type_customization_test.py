import pytest
from sqlalchemy.exc import IntegrityError

from app.models.doctor_note_schema import DoctorNoteSchema
from app.models.note_type_customization import (
    NoteTypeCustomization,
    PlanOrdering,
    PronounStyle,
    SubjectiveSource,
    default_customization,
)
from tests.factories import DoctorFactory


def _schema(doctor_id, note_type: str) -> DoctorNoteSchema:
    return DoctorNoteSchema(
        doctor_id=doctor_id, note_type=note_type, fields={"CC": "chief complaint"}
    ).save()


def test_defaults_match_current_behavior():
    c = default_customization()
    assert c.subjective_source == SubjectiveSource.patient_only
    assert c.pronoun_style == PronounStyle.infer_from_transcript
    assert c.plan_ordering == PlanOrdering.patient_then_doctor
    assert c.flag_new_medications is True
    assert c.require_plan_reason is True
    assert c.ros_distinct_from_subjective is True
    assert c.custom_instructions is None


def test_persists_and_reads_back():
    doctor = DoctorFactory.build().save()
    schema = _schema(doctor.id, "SOAP")
    NoteTypeCustomization(
        doctor_note_schema_id=schema.id,
        require_plan_reason=False,
        custom_instructions="Always use metric units.",
    ).save()

    loaded = NoteTypeCustomization.get(doctor_note_schema_id=schema.id)
    assert loaded is not None
    assert loaded.require_plan_reason is False
    assert loaded.custom_instructions == "Always use metric units."
    assert loaded.subjective_source == SubjectiveSource.patient_only
    assert type(loaded.subjective_source) is SubjectiveSource


def test_one_to_one_per_schema():
    doctor = DoctorFactory.build().save()
    schema = _schema(doctor.id, "SOAP")
    NoteTypeCustomization(doctor_note_schema_id=schema.id).save()
    with pytest.raises(IntegrityError):
        NoteTypeCustomization(doctor_note_schema_id=schema.id).save()


def test_rows_are_isolated_across_note_types():
    doctor = DoctorFactory.build().save()
    schema_a = _schema(doctor.id, "Type A")
    schema_b = _schema(doctor.id, "Type B")
    NoteTypeCustomization(
        doctor_note_schema_id=schema_a.id, require_plan_reason=False
    ).save()
    NoteTypeCustomization(doctor_note_schema_id=schema_b.id).save()

    a = NoteTypeCustomization.get(doctor_note_schema_id=schema_a.id)
    b = NoteTypeCustomization.get(doctor_note_schema_id=schema_b.id)
    assert a.require_plan_reason is False
    assert b.require_plan_reason is True
