"""
Regression coverage for `DoctorNoteSchema.example_count`.

Before the fix, the implementation passed the `examples` relationship as a
positional filter to `.where(...)`, which left both `doctor_note_schema` and
`doctor_note_example` in the FROM clause with no JOIN condition. SQLAlchemy
emitted a SAWarning ("SELECT statement has a cartesian product…") and the
count was wrong for any schema that didn't own every example in the table.
"""

import warnings

from app.models.doctor_note_example import DoctorNoteExample
from app.models.doctor_note_schema import DoctorNoteSchema
from sqlalchemy.exc import SAWarning

from tests.factories import DoctorFactory


def _make_schema(doctor_id, note_type: str) -> DoctorNoteSchema:
    return DoctorNoteSchema(
        doctor_id=doctor_id,
        note_type=note_type,
        fields={"CC": "chief complaint"},
    ).save()


def _add_example(schema: DoctorNoteSchema) -> DoctorNoteExample:
    return DoctorNoteExample(
        doctor_id=schema.doctor_id,
        doctor_note_schema_id=schema.id,
        field_examples={"CC": "headache"},
    ).save()


def test_example_count_returns_examples_for_this_schema_only():
    doctor = DoctorFactory.build().save()

    schema_a = _make_schema(doctor.id, "Type A")
    schema_b = _make_schema(doctor.id, "Type B")

    _add_example(schema_a)
    _add_example(schema_a)
    _add_example(schema_b)

    assert schema_a.example_count == 2
    assert schema_b.example_count == 1


def test_example_count_emits_no_cartesian_product_warning():
    doctor = DoctorFactory.build().save()
    schema = _make_schema(doctor.id, "Type")
    _add_example(schema)

    with warnings.catch_warnings():
        warnings.simplefilter("error", SAWarning)
        # Cartesian-product detection would raise SAWarning before the fix.
        assert schema.example_count == 1


def test_example_count_is_zero_when_no_examples_exist():
    doctor = DoctorFactory.build().save()
    schema = _make_schema(doctor.id, "Empty Type")

    assert schema.example_count == 0
