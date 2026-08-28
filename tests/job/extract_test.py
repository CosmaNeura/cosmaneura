# pyright: reportAttributeAccessIssue=false, reportOptionalMemberAccess=false

import json
import os
import re
from unittest.mock import patch

import pytest
from flexmock import flexmock
from structlog.testing import capture_logs

from app import log
from app.jobs.extract import perform

from app.models.doctor_note_example import DoctorNoteExample
from app.models.doctor_note_file import DoctorNoteFile
from app.models.doctor_note_schema import DoctorNoteSchema

from tests.eval import eval_llm
from tests.factories import DoctorNoteFileFactory

_RUN_LIVE_LLM_TESTS = os.getenv("RUN_LIVE_LLM_TESTS") == "true"
_HAS_AZURE_OPENAI_CREDS = bool(
    os.getenv("AZURE_OPENAI_API_KEY") and os.getenv("AZURE_OPENAI_ENDPOINT")
)
requires_live_llm = pytest.mark.skipif(
    not (_RUN_LIVE_LLM_TESTS and _HAS_AZURE_OPENAI_CREDS),
    reason=(
        "requires live Azure OpenAI access; set RUN_LIVE_LLM_TESTS=true and Azure OpenAI env vars"
    ),
)


@pytest.fixture(autouse=True)
def _allow_doctor_access(monkeypatch):
    monkeypatch.setattr(
        "app.jobs.extract.doctor_capability_access_in_force",
        lambda _doctor, _capability: True,
    )


class AssertionTracker:
    def __init__(self):
        self._total = 0
        self._passed = 0

    def __call__(self, predicate: bool) -> None:
        self._total += 1
        self._passed += 1 if predicate else 0

    def passed_percentage(self) -> float:
        if self._total == 0:
            return 0.0
        return (self._passed / self._total) * 100

    def threshold_met_or_raise(self, threshold_percent: float):
        actual_percent = self.passed_percentage()
        if actual_percent >= threshold_percent:
            log.info(
                "threshold met",
                actual=actual_percent,
                threshold=threshold_percent,
            )
        else:
            log.error(
                "threshold not met",
                passed=self._passed,
                total=self._total,
                actual=actual_percent,
                threshold=threshold_percent,
            )
            raise AssertionError(
                f"Threshold not met: {self.passed_percentage()}% of assertions passed."
            )


def llm_matches(regex, string) -> bool:
    if re.search(regex, string):
        return True
    criteria = f"Should not materially differ from the regex '{regex}' from the perspective of a physician."
    return eval_llm(string, criteria)


def assert_matches(regex, string):
    if not llm_matches(regex, string):
        raise AssertionError(
            f"Regex '{regex}' materially differs from the string '{string}'"
        )


@requires_live_llm
def test_field_order_is_preserved():
    file = DoctorNoteFile.get(note_type="TEST Telehealth SOAP Note")  # Seeded data.
    assert file is not None

    perform(file.id)

    schema = (
        DoctorNoteSchema.where(
            (DoctorNoteSchema.doctor_id == file.doctor_id)
            & (DoctorNoteSchema.note_type == file.note_type)
        )
        .order_by(DoctorNoteSchema.created_at.desc())
        .first()
    )
    assert schema is not None

    field_keys = list(schema.fields.keys())
    expected_keys = [
        "CC",
        "Subjective",
        "ROS",
        "Medications",
        "Allergies",
        "Mental/Functional",
        "Vitals",
        "Objective",
        "Assessment",
        "Plan",
    ]
    if field_keys != expected_keys:
        raise AssertionError(
            f"field order mismatch, expected fields: {expected_keys}, actual fields: {field_keys}"
        )


@requires_live_llm
def test_telehealth_SOAP_note_extraction():
    file = DoctorNoteFile.get(note_type="TEST Telehealth SOAP Note")  # Seeded data.
    assert file is not None

    perform(file.id)

    schema = (
        DoctorNoteSchema.where(
            (DoctorNoteSchema.doctor_id == file.doctor_id)
            & (DoctorNoteSchema.note_type == file.note_type)
        )
        .order_by(DoctorNoteSchema.created_at.desc())
        .first()
    )
    assert schema is not None

    field_keys = list(schema.fields.keys())
    expected_keys = [
        "CC",
        "Subjective",
        "ROS",
        "Medications",
        "Allergies",
        "Mental/Functional",
        "Vitals",
        "Objective",
        "Assessment",
        "Plan",
    ]
    if field_keys != expected_keys:
        raise AssertionError(
            f"field order mismatch, expected fields: {expected_keys}, actual fields: {field_keys}"
        )
    for key in expected_keys:
        assert key in field_keys
        # It should have instructions for each field.
        # Min length of 15 comes from "Chief complaint".
        assert len(schema.fields[key]) > 15

    example = DoctorNoteExample.get(doctor_note_schema_id=schema.id)
    assert example is not None
    assert example.doctor_id == file.doctor_id

    for key in expected_keys:
        assert key in list(example.field_examples.keys())

    assert_ = AssertionTracker()

    fields = example.field_examples
    assert_("Thyroid" == fields["CC"])
    assert_("No known allergies" == fields["Allergies"])
    assert_("Mood is good: denies depression, anxiety" == fields["Mental/Functional"])
    assert_("Hashimoto thyroiditis" in fields["Assessment"])

    subjective = fields["Subjective"].replace("\n", " ")
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
        assert_(content in subjective)

    ros = fields["ROS"].replace("\n", " ")
    ros_contents = [
        "12 pt ROS performed",
        "including Gen, HEENT, Resp, CV, GI, Skin",
        "nothing additional to HPI",
    ]
    for content in ros_contents:
        assert_(content in ros)

    objective = fields["Objective"].replace("\n", " ")
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
        assert_(content in objective)

    plan = fields["Plan"].replace("\n", " ")
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
        assert_(content in plan)

    assert_.threshold_met_or_raise(0.95)


@requires_live_llm
def test_telehealth_H_and_P_note_extraction():
    file = DoctorNoteFile.get(note_type="Telehealth H&P Note")  # Seeded data.
    assert file is not None

    perform(file.id)

    schema = (
        DoctorNoteSchema.where(
            (DoctorNoteSchema.doctor_id == file.doctor_id)
            & (DoctorNoteSchema.note_type == file.note_type)
        )
        .order_by(DoctorNoteSchema.created_at.desc())
        .first()
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
    assert field_keys == expected_keys
    for key in expected_keys:
        assert key in field_keys
        # It should have instructions for each field.
        # Min length of 15 comes from "Chief complaint".
        assert len(schema.fields[key]) > 15

    example = DoctorNoteExample.get(doctor_note_schema_id=schema.id)
    assert example is not None
    assert example.doctor_id == file.doctor_id

    for key in expected_keys:
        assert key in list(example.field_examples.keys())

    fields = example.field_examples
    assert_ = AssertionTracker()

    assert_(fields["CC"] == "Est care: evaluate mid-cycle pain")
    assert_("no" in fields["PMHx"])
    pshx = fields["PSHx"].replace("\n", " ")
    assert_(pshx == "Wisdom teeth removed no")
    assert_(fields["FHx"] == "mother - alive, has Gestational DM")
    assert_(fields["Soc Hx"] == "married")
    assert_(fields["Ob Preg Hx"] == "Pregnancy History - G6, T0, P0, A0, L6")
    ros = "12 pt ROS performed including Gen, HEENT, Resp, CV, GI, Skin - nothing additional to HPI"
    assert_(fields["ROS"].replace("\n", " ") == ros)
    assert_(fields["Medications"] == "No known medications")
    assert_(fields["Allergies"] == "No known allergies")
    assert_(
        fields["Mental/Functional"] == "Normal mood; no c/o significant PMS mood shifts"
    )
    assert_(fields["Goals"] == "Dx and treat midcycle pain")
    assert_(fields["Health Concerns"] == "None")

    hpi = fields["HPI"].replace("\n", " ")
    hpi_contents = [
        r"a pleasant 37 yo G6P6 woman seen in IL via telemedicine with synchronous video for mid-cyc?lc?e pain.",
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
        assert_(llm_matches(content, hpi))

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
        assert_(content in fields["Vitals"])

    exam = fields["EXAM"].replace("\n", " ")
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
        assert_(llm_matches(content, exam))

    assessment = fields["Assessment"].replace("\n", " ")
    assert "15 Nov, 2024" not in assessment  # De-identified
    assert "19 Nov, 2024" not in assessment  # De-identified
    assessment_contents = [
        r"Abnormal menstrual cycle \(N92.6\) Irregular menstruation",
        r"Lower abdominal pain \(R10.30\) Lower abdominal pain, unspecified",
        "Problem reconciliation performed by",
    ]
    for content in assessment_contents:
        assert_(llm_matches(content, assessment))

    plan = fields["Plan"].replace("\n", " ")
    assert_("Abnormal menstrual cycle" in plan)
    assert_("Lower abdominal pain" in plan)
    assert_("45 min visit" in plan)
    assert_("Teresa Asam-Jensen" not in plan)  # Confirm it's been de-identified.

    assert_.threshold_met_or_raise(0.95)


def test_no_duplicates_on_failure():
    """
    If openai fails during the note extraction operation, we need to ensure duplicate note schemas or examples are not created."
    """
    import app.jobs.extract as extract_module

    doctor_note_file = DoctorNoteFileFactory.build().save()

    # Mock download_note so we don't need to touch Azure
    sample_content = "This is a sample doctor note content"
    flexmock(extract_module).should_receive("download_note").and_return(sample_content)

    example_note_fields = [
        "CC",
        "Subjective",
        "ROS",
    ]

    # let's mock out extract_note_fields and explain_note_fields to avoid hitting openai
    flexmock(extract_module).should_receive("extract_note_fields").and_return(
        example_note_fields
    )

    flexmock(extract_module).should_receive("explain_note_fields").and_return(
        {
            "CC": "Chief Complaint",
            "Subjective": "Subjective Information",
            "ROS": "Review of Systems",
        }
    )

    # Mock extract_note_fields to throw an exception
    flexmock(extract_module).should_receive("extract_note_content").and_raise(
        Exception("OpenAI API error")
    )

    # paranoid: let's make sure we don't have any existing schemas or examples
    assert (
        DoctorNoteSchema.where(
            DoctorNoteSchema.doctor_id == doctor_note_file.doctor_id
        ).count()
        == 0
    )
    assert (
        DoctorNoteExample.where(
            DoctorNoteExample.doctor_id == doctor_note_file.doctor_id
        ).count()
        == 0
    )

    # Run the job, which should fail
    with pytest.raises(Exception, match="OpenAI API error"):
        perform(doctor_note_file.id)

    # Verify no new schemas or examples were created
    schema_count = DoctorNoteSchema.where(
        DoctorNoteSchema.doctor_id == doctor_note_file.doctor_id
    ).count()
    example_count = DoctorNoteExample.where(
        DoctorNoteExample.doctor_id == doctor_note_file.doctor_id
    ).count()

    # now that it's failed, let's simulate rerunning the job and ensuring no additional schemas were created
    with pytest.raises(Exception, match="OpenAI API error"):
        perform(doctor_note_file.id)

    schema_count_after = DoctorNoteSchema.where(
        DoctorNoteSchema.doctor_id == doctor_note_file.doctor_id
    ).count()
    example_count_after = DoctorNoteExample.where(
        DoctorNoteExample.doctor_id == doctor_note_file.doctor_id
    ).count()

    assert schema_count == schema_count_after
    assert example_count == example_count_after


def test_extraction_logs_only_pseudonymous_references_and_counts():
    """Clinical note fields, instructions, and examples must stay out of logs."""
    doctor_note_file = DoctorNoteFileFactory.build(
        note_type="Private Note Type Sentinel",
        azure_file_name="https://private.example/clinical-note-sentinel.pdf",
    ).save()
    fields = ["Private Clinical Field Sentinel"]
    instructions = {
        "Private Clinical Field Sentinel": "Private Clinical Instruction Sentinel"
    }
    content = {
        "Private Clinical Field Sentinel": "Deidentified Clinical Output Sentinel"
    }

    with (
        patch("app.jobs.extract.download_note", return_value=["private-page-sentinel"]),
        patch("app.jobs.extract.extract_note_fields", return_value=fields),
        patch("app.jobs.extract.explain_note_fields", return_value=instructions),
        patch("app.jobs.extract.extract_note_content", return_value=content),
        capture_logs() as logs,
    ):
        perform(doctor_note_file.id)

    serialized = json.dumps(logs, sort_keys=True)
    for private_value in (
        str(doctor_note_file.id),
        str(doctor_note_file.doctor_id),
        "Private Note Type Sentinel",
        "clinical-note-sentinel.pdf",
        "private-page-sentinel",
        "Private Clinical Field Sentinel",
        "Private Clinical Instruction Sentinel",
        "Deidentified Clinical Output Sentinel",
    ):
        assert private_value not in serialized
    assert "hmac:" in serialized
    assert any(entry.get("event_code") == "note_extraction_completed" for entry in logs)


def test_multiple_examples_per_note():
    """
    As of 2025-04-02 we don't do anything with multiple notes per schema, but we do want to make sure it is supported.

    let's upload multiple notes for a single schema and check:

    1. example count
    2. no additional schemas created
    """
    import app.jobs.extract as extract_module

    # Create an initial doctor note file
    doctor_note_file1 = DoctorNoteFileFactory.build().save()

    # Mock download_note
    sample_content1 = "This is the first sample doctor note content"
    flexmock(extract_module).should_receive("download_note").and_return(
        sample_content1
    ).once()

    # Mock extraction functions to return consistent data
    # since we are uploading two notes, this will happen twice
    flexmock(extract_module).should_receive("extract_note_fields").and_return(
        ["CC", "Subjective", "ROS"]
    ).times(2)

    flexmock(extract_module).should_receive("explain_note_fields").and_return(
        {
            "CC": "Chief Complaint",
            "Subjective": "Subjective Information",
            "ROS": "Review of Systems",
        }
    ).times(1)

    flexmock(extract_module).should_receive("extract_note_content").and_return(
        {
            "CC": "Headache",
            "Subjective": "Patient reports mild headache for 2 days",
            "ROS": "Otherwise negative",
        }
    ).once()

    # Process the first file
    perform(doctor_note_file1.id)

    # Verify schema and example were created
    initial_schema_count = DoctorNoteSchema.where(
        DoctorNoteSchema.doctor_id == doctor_note_file1.doctor_id
    ).count()
    initial_example_count = DoctorNoteExample.where(
        DoctorNoteExample.doctor_id == doctor_note_file1.doctor_id
    ).count()

    assert initial_schema_count == 1
    assert initial_example_count == 1

    # Get the schema ID for the second note to reference
    schema = DoctorNoteSchema.where(
        DoctorNoteSchema.doctor_id == doctor_note_file1.doctor_id
    ).first()

    # Create a second doctor note file of the same type
    doctor_note_file2 = DoctorNoteFileFactory.build(
        doctor_id=doctor_note_file1.doctor_id, note_type=doctor_note_file1.note_type
    ).save()

    # Mock for second file
    sample_content2 = "This is the second sample doctor note content"
    flexmock(extract_module).should_receive("download_note").and_return(
        sample_content2
    ).once()

    # Don't need to extract fields and explain fields again since we reuse the schema
    flexmock(extract_module).should_receive("extract_note_content").and_return(
        {
            "CC": "Sore throat",
            "Subjective": "Patient reports sore throat for 3 days",
            "ROS": "No other symptoms",
        }
    ).once()

    # Process the second file
    perform(doctor_note_file2.id)

    # Verify a new example was created but no new schema
    final_schema_count = DoctorNoteSchema.where(
        DoctorNoteSchema.doctor_id == doctor_note_file1.doctor_id
    ).count()
    final_example_count = DoctorNoteExample.where(
        DoctorNoteExample.doctor_id == doctor_note_file1.doctor_id
    ).count()

    assert final_schema_count == initial_schema_count  # Should be same as before
    assert final_example_count == initial_example_count + 1  # Should be one more

    # Get both examples and verify they have different content
    examples = list(
        DoctorNoteExample.where(
            DoctorNoteExample.doctor_id == doctor_note_file1.doctor_id
        ).all()
    )

    assert len(examples) == 2

    # Verify the examples have different content
    example_ccs = [example.field_examples["CC"] for example in examples]
    assert "Headache" in example_ccs
    assert "Sore throat" in example_ccs
    assert example_ccs[0] != example_ccs[1]

    # Verify both examples refer to the same schema
    schema_ids = [example.doctor_note_schema_id for example in examples]
    assert schema_ids[0] == schema_ids[1]

    # two examples for one note!
    assert schema.example_count == 2
