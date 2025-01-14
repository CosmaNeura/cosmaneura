from app.jobs.extract import extract

from app.models.doctor_note_file import DoctorNoteFile
from app.models.doctor_note_schema import DoctorNoteSchema
from app.models.doctor_note_example import DoctorNoteExample

from app import log
import re

def assert_matches(regex, string):
    if not re.search(regex, string):
        raise AssertionError(f"Regex '{regex}' does not match the string '{string}'")

def test_telehealth_SOAP_note_extraction():
    file = DoctorNoteFile.get(note_type="Telehealth SOAP Note") # Seeded data.
    assert file is not None

    schema = DoctorNoteSchema.get(
        doctor_id=file.doctor_id,
        note_type=file.note_type,
    )
    assert schema is None

    extract(file.id)

    schema = DoctorNoteSchema.get(
        doctor_id=file.doctor_id,
        note_type=file.note_type,
    )
    assert schema is not None

    field_keys = list(schema.fields.keys())
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
    assert len(field_keys) == len(expected_keys)
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
        "Labs Reviewed",
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
        "Check thyroid panel in 6 weeks - sent to",
        "Call if feeling significantly worse and need to be seen earlier to reassess medications",
        "schedule follow up for after labs are completed",
        # She mispelled the medication in her note.
        r"Agreed to reduce NP3 to 50mg and add back levoth?yroxine 50mcg.",
        # Parentheses cause issues.
        r"Discussed other ways to optimize immune regulation through diet or other medications .naltrexone. - resources sent",
    ]
    for content in plan_contents:
        assert_matches(content, fields['Plan'].replace("\n", " "))

def test_telehealth_H_and_P_note_extraction():
    file = DoctorNoteFile.get(note_type="Telehealth H&P Note") # Seeded data.
    assert file is not None

    schema = DoctorNoteSchema.get(
        doctor_id=file.doctor_id,
        note_type=file.note_type,
    )
    assert schema is None

    extract(file.id)

    schema = DoctorNoteSchema.get(
        doctor_id=file.doctor_id,
        note_type=file.note_type,
    )
    assert schema is not None

    field_keys = list(schema.fields.keys())
    expected_keys = [
        "CC",
        "HPI",
        "PMHx",
        "PSHx",
        "FHx",
        "Soc Hx",
        "Ob Preg Hx",
        "ROS",
        "Medications",
        "Allergies",
        "Mental/Functional",
        "Vitals",
        "EXAM",
        "Assessment",
        "Plan",
        "Goals",
        "Health Concerns",
    ]
    assert len(field_keys) == len(expected_keys)
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
    assert fields['CC'] == "Est care: evaluate mid-cycle pain"
    assert fields['PMHx'] == "no dx pmh"
    assert fields['PSHx'] == "Wisdom teeth removed no"
    assert fields['FHx'] == "mother - alive, has Gestational DM"
    assert fields['Soc Hx'] == "married"
    assert fields['Ob Preg Hx'] == "Pregnancy History - G6, T0, P0, A0, L6"
    ros = "12 pt ROS performed including Gen, HEENT, Resp, CV, GI, Skin - nothing additional to HPI"
    assert fields['ROS'].replace("\n", " ") == ros
    assert fields['Medications'] == "No known medications"
    assert fields['Allergies'] == "No known allergies"
    assert fields['Mental/Functional'] == "Normal mood; no c/o significant PMS mood shifts"
    assert fields['Goals'] == "Dx and treat midcycle pain"
    assert fields['Health Concerns'] == "None"

    hpi = fields['HPI'].replace("\n", " ")
    hpi_contents = [
        r"DL is a pleasant 37 yo G6P6 woman seen in IL via telemedicine with synchronous video for mid-cyc?lc?e pain.",
        "Reports regular cycles, 25-26 days long, tracking for about 12 years.",
        "Has noted pain in the left or right lower pelvis around CD 13-14 with bloating for the last 18 months, starting with return of cycles after her 6th child.",
        "Pain described as sharp, worse with activity and high impact.",
        "Occurs every cycle, like clockwork for the last 18 months roughly.",
        "Describes persistent, all-day deep pain that is responsive to deep pressure -could worsen or become somewhat better",
        r"No other GI sx: no n/v/d/flatu(lu)?s or belching. Stable intensity and length .2-3 days.",
        "Not worsening. No evaluation to date.",
        r"May improve with heat, m(e|a)ssage, occasional acetaminophen 600mg once daily if needed.",
        r"Last delivery: NSVD .long labor, 40 hrs..",
        "Healthy pregnancy but severe nausea.",
        r"typical cycle: \+BB 2d after; no midcycle bleeding.",
        "Occasionally very extensive menstrual bleeding.",
        "No intensive menstrual pain- worse midcycle.",
        "No menstrual GI sx. Occasionally very short cycles.",
        "No medications then or now.",
        "PMH no other dx PSH: no pelvic surgeries or other surgeries",
    ]
    for content in hpi_contents:
        assert_matches(content, hpi)

    vitals_contents = [
        "Height/Length",
        "Weight",
        "BMI",
        "Blood",
        "Pressure",
        "Temperature",
        "5' 5\"",
        "135 lbs 0 oz",
        "22.46",
        "Pulse",
        "RR",
        "16",
        "SpO2",
    ]
    for content in vitals_contents:
        assert content in fields['Vitals']

    exam = fields['EXAM'].replace("\n", " ")
    exam_contents = [
        "GEN: NAD, sitting comfortably facing screen, engaged easily in conversation. Appears well.",
        "HEENT: N/C. AT. MMM to observation. Symmetric facies without edema.",
        "NECK: supple, NT, FROM",
        "RESP: nonlabored breathing, no use of accessory of muscles of respiration",
        "DERM: skin warm and dry; no rashes or lesions to observation",
        "EXT: symmetric movement bilat/UE; no cyanosis/clubbing/edema",
        "NEURO: AO x 3, grossly normal coordination and tone",
        "PSYCH: judgment/insight intact, euthymic mood/ congruent affect; good eye contact",
        "Chart Review:",
        "4d menses, 1d BB",
        r"3d menses \+ 2d TEBB",
        r"6d mucus cycle \(MCS 17.2\)",
        r"\+pain CD \d\d-\d\d - no midcycle bleeding",
        "PPP 11 days, no PM BB or spotting",
        "No labs or imaging to review.",
    ]
    for content in exam_contents:
        assert_matches(content, exam)

    assessment = fields['Assessment'].replace("\n", " ")
    assert "15 Nov, 2024" not in assessment # De-identified
    assert "19 Nov, 2024" not in assessment # De-identified
    assessment_contents = [
        r"Abnormal menstrual cycle \(N92.6\) Irregular menstruation",
        r"Lower abdominal pain \(R10.30\) Lower abdominal pain, unspecified",
        r"\(Problem reconciliation performed by .* Last updated\)", # De-identified
    ]
    for content in assessment_contents:
        assert_matches(content, assessment)

    assert "Abnormal menstrual cycle" in fields['Plan']
    assert "Lower abdominal pain" in fields['Plan']
    assert "45 min visit" in fields['Plan']
    assert "Teresa Asam-Jensen" not in fields['Plan'] # De-identified
