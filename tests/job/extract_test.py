from app.jobs.extract import extract

from app.models.doctor_note_file import DoctorNoteFile
from app.models.doctor_note_schema import DoctorNoteSchema
from app.models.doctor_note_example import DoctorNoteExample

import re

def assert_matches(regex, string):
    if not re.search(regex, string):
        raise AssertionError(f"Regex '{regex}' does not match the string '{string}'")

def test_extract():
    file = DoctorNoteFile.get(note_type="Telehealth SOAP Note")
    assert file is not None

    schema = DoctorNoteSchema.get(
        doctor_id=file.doctor_id,
        note_type=file.note_type,
    )
    assert schema is None
    example = DoctorNoteExample.get(doctor_id=file.doctor_id)
    assert example is None

    extract(file.id)

    schema = DoctorNoteSchema.get(
        doctor_id=file.doctor_id,
        note_type=file.note_type,
    )
    assert schema is not None

    field_keys = list(schema.fields.keys())
    assert len(field_keys) == 10
    expected_keys = [
        'CC',
        'Subjective',
        'ROS',
        'Medications',
        'Allergies',
        'Mental/Functional',
        'Vitals',
        'Objective',
        'Assessment',
        'Plan'
    ]
    for key in expected_keys:
        assert key in field_keys
        # It should have instructions for each field.
        # Min length of 15 comes from "Chief complaint".
        assert len(schema.fields[key]) > 15
        # TODO This would be a good place to do some evals.

    example = DoctorNoteExample.get(doctor_note_schema_id=schema.id)
    assert example is not None
    assert example.doctor_id == file.doctor_id

    for key in expected_keys:
        assert key in list(example.field_examples.keys())

    fields = example.field_examples
    assert "Thyroid" == fields['CC']
    assert "No known allergies" == fields['Allergies']
    assert "Mood is good: denies depression, anxiety" == fields['Mental/Functional']
    assert "Hashimoto thyroiditis" in fields['Assessment']

    subjective_contents = [
        "history of thyroid disorder (Hashimoto)",
        "tried levothyroxine when first dx after the birth of her last child",
        "+hair loss",
        "T4 1.21 (direct)",
        "T3 - WNL",
        "TPO nearly 200",
        "sleeps through night 5d per week",
        "kids wake up 1-2 days per week",
        "8h",
        "At dx cut out inflammatory foods",
        "antibodies went down and felt better",
        "Sleep: no snoring",
    ]
    for content in subjective_contents:
        assert content in fields['Subjective'].replace("\n", " ")

    ros_contents = [
        "12 pt ROS performed",
        "including Gen, HEENT, Resp, CV, GI, Skin",
        "nothing additional to HPI",
    ]
    for content in ros_contents:
        assert content in fields['ROS'].replace("\n", " ")

    objective_contents = [
        "GEN: NAD, sitting comfortably facing screen, engaged easily in conversation",
        "HEENT: MMM to observation",
        "NECK: supple, NT, FROM",
        "RESP: nonlabored breathing, no use of accessory of muscles of respiration",
        "DERM: skin warm and dry",
        "EXT: symmetric movement bilat/UE; no cyanosis/clubbing/edema",
        "NEURO: AO x 3, grossly normal coordination and tone",
        "PSYCH: judgment/insight intact, euthymic mood/ congruent affect; good eye contact",
        "Labs Reviewed: Nov 2024",
        "1.2 TSH",
        "T4 1.21 (direct)",
        "T3 - WNL",
        "TPO nearly 200",
        "Imaging: none",
        "Cycle review: none",
    ]
    for content in objective_contents:
        assert content in fields['Objective'].replace("\n", " ")

    plan_contents = [
        "Hashimoto thyroiditis",
        "Clinically, not feeling great and c/o fatigue with unrestorative sleep.",
        "able to work out and gaining stamina and strength",
        "supporting the lab results that indicate adequate thyroid function",
        "History suggests against a sleep disorder, depression or hormonal dysfunction",
        "she may have chronic sleep deprivation that is not yet corrected based on the ages of her kids",
        "may need combined T3 and T4 supplementation for best results",
        "Check thyroid panel in 6 weeks - sent to LabCorps.",
        "Call if feeling significantly worse and need to be seen earlier to reassess medications",
        "schedule follow up for after labs are completed",
        # She mispelled the medication in her note.
        r"Agreed to reduce NP3 to 50mg and add back levoth?yroxine 50mcg.",
        # Parentheses cause issues.
        r"Discussed other ways to optimize immune regulation through diet or other medications .naltrexone. - resources sent",
    ]
    for content in plan_contents:
        assert_matches(content, fields['Plan'].replace("\n", " "))
