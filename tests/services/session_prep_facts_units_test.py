"""Realistic source units retain context, dated states and literal evidence."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.services.session_prep_facts import (
    MAX_QUOTE_CHARACTERS,
    FactCandidate,
    FactEvidenceError,
    project_explicit_facts,
    validate_fact_candidates,
)

CORRECTION = (
    "Losartan 25 mg daily (continue; supersedes previous erroneous instruction "
    "to increase to 50 mg)"
)
LABS = (
    "CBC order cancelled; no completion or result documented\n"
    "Potassium and creatinine tests not ordered; no results documented"
)
FOOTER = (
    "Greater than 1 minutes was spent in patient care, including review of prior "
    "records, consultation with other providers, and documentation.\n"
    "This note was generated with AI assistance."
)


def _selection(*notes):
    return SimpleNamespace(
        sources=tuple(
            SimpleNamespace(
                source_ref=f"synthetic_units_note_{index:04d}",
                source_version=4,
                source_at=datetime.fromisoformat(date).replace(tzinfo=UTC),
                content=sections,
            )
            for index, (date, sections) in enumerate(notes, 1)
        ),
        truncated=False,
    )


def _single(section, text):
    return _selection(("2026-10-08", {section: text}))


def _verify_spans(result, selection):
    for fact in result.facts:
        source = next(
            s for s in selection.sources if s.source_ref == fact.evidence.source_ref
        )
        evidence = fact.evidence
        assert (
            source.content[evidence.section][evidence.start : evidence.end]
            == evidence.quote
        )
        assert evidence.source_at == source.source_at
        assert evidence.source_version == source.source_version
        assert fact.subject in evidence.quote
        if fact.value is not None:
            assert fact.value in evidence.quote


@pytest.mark.parametrize("separator", ["\n", "\r\n", "\n\n", "; "])
def test_independent_objective_units_recover_office_bp_and_negative_lab_evidence(
    separator,
):
    parts = [
        "Office blood pressure: 148/92 mmHg",
        "No dizziness reported",
        "No potassium or creatinine result documented",
    ]
    selection = _single("O - OBJECTIVE", separator.join(parts))
    result = project_explicit_facts(selection)
    assert {(f.subject, f.value, f.state, f.setting) for f in result.facts} == {
        ("blood pressure", "148/92 mmHg", "measured", "office"),
        ("potassium", None, "result_not_documented", None),
        ("creatinine", None, "result_not_documented", None),
    }
    _verify_spans(result, selection)


@pytest.mark.parametrize(
    "text,value",
    [
        (
            "The patient reports home blood pressures ranging from 128 to 136 systolic and 78 to 84 diastolic since starting losartan.",
            "128 to 136 systolic and 78 to 84 diastolic",
        ),
        (
            "Patient reports home blood pressure readings between 128 and 136/78 to 84.",
            "128 and 136/78 to 84",
        ),
        (
            "Home blood pressure readings: 128-136/78-84 (patient-reported)",
            "128-136/78-84",
        ),
    ],
)
def test_patient_reported_ranges_keep_literal_values_without_normalization_or_medication_inference(
    text, value
):
    selection = _single(
        "S - SUBJECTIVE", text + "\nNo dizziness.\nNo known drug allergies."
    )
    result = project_explicit_facts(selection)
    assert [(f.kind, f.value, f.setting) for f in result.facts] == [
        ("vital", value, "home")
    ]
    _verify_spans(result, selection)


def test_home_observation_and_office_absence_are_independent_but_contradictory_office_is_omitted():
    home = "Home blood pressure: 128-136/78-84 (patient-reported)"
    absence = "Office blood pressure: Not documented"
    result = project_explicit_facts(_single("Objective", home + "\n" + absence))
    assert [(f.value, f.setting) for f in result.facts] == [("128-136/78-84", "home")]
    assert (
        project_explicit_facts(
            _single("Objective", "Office BP 148/92.\n" + absence)
        ).facts
        == []
    )


@pytest.mark.parametrize(
    "text",
    [
        "Office BP 148/92.\nNo dizziness reported.\n" + FOOTER,
        "- Office BP 148/92.\n- No dizziness reported.\n- "
        + FOOTER.replace("\n", "\n- "),
    ],
)
def test_exact_metadata_footer_does_not_drop_or_qualify_independent_patient_evidence(
    text,
):
    selection = _single("Objective", text)
    result = project_explicit_facts(selection)
    assert [(f.kind, f.value) for f in result.facts] == [("vital", "148/92")]
    _verify_spans(result, selection)


def test_two_dated_listed_doses_remain_unconfirmed_and_corrected_dose_binds_last_same_drug():
    selection = _selection(
        ("2026-09-30", {"Medications": "Losartan 25mg once daily (new this visit)"}),
        (
            "2026-10-07",
            {
                "Medications": "Losartan 25 mg daily (prior to this visit)\nLosartan 50 mg daily (new this visit)"
            },
        ),
        ("2026-10-08", {"Medications": CORRECTION}),
    )
    result = project_explicit_facts(selection)
    assert [(f.value, f.state) for f in result.facts] == [
        ("25mg", "documented_listed"),
        ("25 mg", "documented_listed"),
        ("50 mg", "documented_listed"),
        ("25 mg", "documented_continued"),
    ]
    later = result.facts[-1]
    assert later.supersedes == [result.facts[-2].fact_ref]
    assert later.correction_evidence.quote == CORRECTION
    assert later.correction_evidence.source_at.date().isoformat() == "2026-10-08"
    _verify_spans(result, selection)


@pytest.mark.parametrize(
    "prior,date,linked",
    [
        ("Amlodipine 50 mg daily", "2026-10-07", False),
        ("Losartan 25 mg daily", "2026-10-07", False),
        ("Losartan 50 mg daily", "2026-10-08", False),
        ("Losartan 50mg daily", "2026-10-07", True),
    ],
)
def test_compound_correction_does_not_reassign_drug_dose_or_equal_date_sources(
    prior, date, linked
):
    selection = _selection(
        (date, {"Medications": prior}), ("2026-10-08", {"Medications": CORRECTION})
    )
    result = project_explicit_facts(selection)
    correction = result.facts[-1]
    assert bool(correction.supersedes) is linked


def test_compound_correction_cannot_classify_the_embedded_old_dose_as_continued():
    selection = _single("Medications", CORRECTION)
    source = selection.sources[0]
    candidate = FactCandidate(
        kind="medication",
        subject="Losartan",
        value="50 mg",
        state="documented_continued",
        source_ref=source.source_ref,
        source_version=4,
        section="Medications",
        quote=CORRECTION,
    )
    with pytest.raises(FactEvidenceError, match="^fact_status_evidence_invalid$"):
        validate_fact_candidates([candidate], selection)


@pytest.mark.parametrize(
    "text",
    [
        CORRECTION.replace("continue;", "do not continue;"),
        CORRECTION.replace("supersedes", "does not supersede"),
        CORRECTION.replace("Losartan", "Mother"),
        CORRECTION.replace("Losartan", "Neighbor"),
        CORRECTION.replace("previous erroneous", "possibly erroneous"),
        CORRECTION.replace("to 50 mg)", "to 50 mg or 100 mg)"),
        CORRECTION.replace("to 50 mg)", "amlodipine to 50 mg)"),
        CORRECTION + " for a neighbor.",
        "Is " + CORRECTION + "?",
    ],
)
def test_compound_correction_requires_its_whole_assertion_and_supported_subject(text):
    assert project_explicit_facts(_single("Medications", text)).facts == []


def test_lab_cancellation_and_shared_absence_have_dates_without_completed_or_positive_order_inference():
    selection = _single("Labs - fasting, 02/12/2026", LABS)
    result = project_explicit_facts(selection)
    assert {(f.subject.casefold(), f.state) for f in result.facts} == {
        ("cbc", "cancelled"),
        ("cbc", "result_not_documented"),
        ("potassium", "not_ordered"),
        ("potassium", "result_not_documented"),
        ("creatinine", "not_ordered"),
        ("creatinine", "result_not_documented"),
    }
    assert all(
        f.evidence.source_at.date().isoformat() == "2026-10-08" for f in result.facts
    )
    assert all(f.evidence.section == "Labs - fasting, 02/12/2026" for f in result.facts)
    assert not any(f.state in {"ordered", "resulted"} for f in result.facts)
    _verify_spans(result, selection)


@pytest.mark.parametrize(
    "text",
    [
        "Potassium ordered and creatinine tests not ordered; no results documented.",
        "Potassium and creatinine tests not ordered; potassium results documented.",
        "Potassium and creatinine tests not ordered; no CBC results documented.",
        "CBC order allegedly cancelled; no completion or result documented.",
        "CBC order not cancelled; no completion or result documented.",
        "CBC order cancelled if tolerated; no completion or result documented.",
        "CBC order cancelled; normal result documented.",
        "CBC order cancelled; no completion or result documented for a visitor.",
        "No potassium result documented; creatinine resulted?",
    ],
)
def test_shared_absence_and_cancellation_never_borrow_a_foreign_or_mixed_predicate(
    text,
):
    assert project_explicit_facts(_single("Labs", text)).facts == []


def test_direct_lab_candidates_require_exact_test_identity_not_a_subject_substring():
    text = "Potassium and creatinine tests not ordered; no results documented"
    selection = _single("Labs", text)
    source = selection.sources[0]
    for subject in ("tests", "Potassium and creatinine", "CBC"):
        candidate = FactCandidate(
            kind="order",
            subject=subject,
            state="not_ordered",
            source_ref=source.source_ref,
            source_version=4,
            section="Labs",
            quote=text,
        )
        with pytest.raises(FactEvidenceError):
            validate_fact_candidates([candidate], selection)


@pytest.mark.parametrize(
    "text",
    [
        "Order CBC to evaluate ongoing fatigue.",
        "Order CBC.",
        "CBC should be ordered.",
        "CBC order status unknown.",
    ],
)
def test_order_plan_instructions_and_unknown_status_remain_unstructured(text):
    assert project_explicit_facts(_single("Plan", text)).facts == []


@pytest.mark.parametrize(
    "section,positive",
    [
        ("Objective", "Office BP 148/92."),
        ("Subjective", "Home blood pressure: 128-136/78-84 (patient-reported)"),
        ("Medications", CORRECTION),
        ("Labs", "CBC order cancelled; no completion or result documented"),
    ],
)
@pytest.mark.parametrize(
    "qualifier",
    [
        "For a neighbor.",
        "For Ann.",
        "If tolerated.",
        "Status unknown.",
        "This is hypothetical.",
        "No confirmation of use.",
        "Not measured.",
        "A different provider's illustrative example.",
        "...",
        "-",
        "```",
    ],
)
def test_new_independent_unit_coverage_does_not_drop_unknown_enclosing_context(
    section, positive, qualifier
):
    for text in (positive + "\n" + qualifier, qualifier + "\n" + positive):
        assert project_explicit_facts(_single(section, text)).facts == []


def test_oversized_recognized_context_and_indented_benign_lines_veto_whole_section():
    oversized = (
        "Greater than "
        + "1" * MAX_QUOTE_CHARACTERS
        + " minutes was spent in patient care, including review of prior records, consultation with other providers, and documentation."
    )
    for context in (
        oversized,
        "    No dizziness reported.",
        "\tNo known drug allergies.",
    ):
        assert (
            project_explicit_facts(
                _single("Objective", "Office BP 148/92.\n" + context)
            ).facts
            == []
        )


def test_unsupported_newline_qualifier_cannot_be_selected_through_a_direct_substring():
    selection = _single("Objective", "Office BP 148/92\nnot measured for this patient.")
    source = selection.sources[0]
    candidate = FactCandidate(
        kind="vital",
        subject="BP",
        value="148/92",
        state="measured",
        setting="office",
        source_ref=source.source_ref,
        source_version=4,
        section="Objective",
        quote="Office BP 148/92",
    )
    with pytest.raises(FactEvidenceError):
        validate_fact_candidates([candidate], selection)


def test_independent_units_and_decimal_footer_keep_actual_noninitial_quote_offsets():
    text = "No dizziness reported.\nOffice BP 148/92.\n" + FOOTER.replace(
        "than 1 ", "than 1.5 "
    )
    selection = _single("Objective", text)
    result = project_explicit_facts(selection)
    assert result.facts[0].evidence.start == text.index("Office BP 148/92.")
    _verify_spans(result, selection)
