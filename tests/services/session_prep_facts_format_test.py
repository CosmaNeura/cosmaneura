"""Format-only support retains source offsets and complete enclosing context."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from app.services.session_prep_facts import (
    FactCandidate,
    FactEvidenceError,
    project_explicit_facts,
    validate_fact_candidates,
)

if TYPE_CHECKING:
    from app.services.session_prep_note_sources import ReviewedNoteSelection


def _selection(section: str, text: str) -> ReviewedNoteSelection:
    # The pure projector consumes these exact fields; avoid application/DB
    # startup from importing the source adapter merely to construct fixtures.
    return cast(
        "ReviewedNoteSelection",
        SimpleNamespace(
            sources=(
                SimpleNamespace(
                    source_ref="synthetic_format_source_0001",
                    source_version=7,
                    source_at=datetime(2026, 10, 22, tzinfo=UTC),
                    content={section: text},
                ),
            ),
            truncated=False,
        ),
    )


@pytest.mark.parametrize(
    "title,text,kind,state",
    [
        (
            "S - SUBJECTIVE",
            "Patient reports taking losartan 25 mg.",
            "medication",
            "documented_taking",
        ),
        ("O-OBJECTIVE", "Office BP 148/92.", "vital", "measured"),
        ("a - assessment", "CBC ordered.", "order", "ordered"),
        ("P - PLAN", "Planned losartan 50 mg daily.", "medication", "planned"),
    ],
)
def test_only_matched_soap_aliases_preserve_original_source_title(
    title, text, kind, state
):
    result = project_explicit_facts(_selection(title, text))
    assert [(f.kind, f.state) for f in result.facts] == [(kind, state)]
    evidence = result.facts[0].evidence
    assert evidence.section == title
    assert evidence.source_at == datetime(2026, 10, 22, tzinfo=UTC)
    assert evidence.source_version == 7
    assert evidence.quote == text


@pytest.mark.parametrize(
    "title",
    [
        "S - OBJECTIVE",
        "S - Family History",
        "Family History",
        "P - PLAN - 2026-01-01",
        "Labs - fasting, 02/13/2026",
        "O : OBJECTIVE",
        "S - SUBJECTIVE / Family History",
        "Custom Plan",
        "O - OBJECTIVE (other person)",
    ],
)
def test_soap_aliases_do_not_strip_unknown_or_dated_title_context(title):
    assert (
        project_explicit_facts(_selection(title, "Taking losartan 25 mg.")).facts == []
    )


@pytest.mark.parametrize("marker", ["-", "+", "*", "1.", "0)", "42)"])
def test_ordinary_markdown_lists_keep_exact_payload_quotes_and_offsets(marker):
    first = "Losartan 25mg once daily (new this visit)"
    second = "Amlodipine 2.5 mg daily"
    text = f"{marker} {first}\n   {marker}\t{second}"
    result = project_explicit_facts(_selection("Medications", text))
    assert [(f.subject, f.state) for f in result.facts] == [
        ("Losartan", "documented_listed"),
        ("Amlodipine", "documented_listed"),
    ]
    assert [(f.evidence.quote, f.evidence.start) for f in result.facts] == [
        (first, len(marker) + 1),
        (second, text.index(second)),
    ]
    for fact in result.facts:
        evidence = fact.evidence
        assert text[evidence.start : evidence.end] == evidence.quote


@pytest.mark.parametrize(
    "text",
    [
        "- Taking losartan 25 mg\nfor Ann.",
        "- Taking losartan 25 mg.\n- For Ann.",
        "For Ann.\n- Taking losartan 25 mg.",
        "- Taking losartan 25 mg.\nfor a neighbor.",
        "- Taking losartan 25 mg.\n- If tolerated.",
        "- Taking losartan 25 mg.\n- Status unknown.",
        "- Taking losartan 25 mg.\n- No confirmation of use.",
        "- Do not continue losartan 25 mg.",
        "- Taking losartan 25 mg before increasing to 50 mg.",
        "1. Medication list for Ann.\n2. Taking losartan 25 mg.",
        "- Taking losartan 25 mg.\n-\nFor Ann.",
        "- Taking losartan 25 mg.\n...",
        "- **Taking losartan 25 mg.**",
        "- [ ] Taking losartan 25 mg.",
        "```\n- Taking losartan 25 mg.\n```",
        "    - Taking losartan 25 mg.",
        "-     Taking losartan 25 mg.",
        "-\t\tTaking losartan 25 mg.",
        "Patient is taking losartan 25 mg. - Taking amlodipine 2.5 mg.",
    ],
)
def test_list_formatting_never_discards_qualifiers_or_unknown_units(text):
    result = project_explicit_facts(_selection("Medications", text))
    assert result.facts == []
    assert result.coverage == "bounded_explicit_mentions"


def test_overbound_list_context_stays_in_whole_section_validation():
    context = "Training illustration " + "neutral " * 190 + "."
    for text in (
        f"- {context}\n- Taking losartan 25 mg.",
        f"- Taking losartan 25 mg.\n{context}",
    ):
        assert project_explicit_facts(_selection("Medications", text)).facts == []


@pytest.mark.parametrize("indent", ["    ", "     ", "\t", " \t "])
@pytest.mark.parametrize(
    "section,quote,kind,subject,value,state,setting",
    [
        (
            "Medications",
            "Taking losartan 25 mg.",
            "medication",
            "losartan",
            "25 mg",
            "documented_taking",
            None,
        ),
        (
            "Medications",
            "Not taking losartan 25 mg.",
            "medication",
            "losartan",
            "25 mg",
            "documented_not_taking",
            None,
        ),
        (
            "Objective",
            "Office BP 148/92.",
            "vital",
            "BP",
            "148/92",
            "measured",
            "office",
        ),
        ("Orders", "CBC resulted.", "order", "CBC", None, "resulted", None),
        (
            "Orders",
            "CBC order not documented.",
            "order",
            "CBC",
            None,
            "order_not_documented",
            None,
        ),
    ],
)
def test_raw_code_indentation_vetoes_all_rows_before_whitespace_stripping(
    indent, section, quote, kind, subject, value, state, setting
):
    # Includes inherited code-only acceptance and the new list+code regression.
    for text in (
        indent + quote,
        indent + quote + "\n\n- CBC ordered.",
        "- CBC ordered.\n\n" + indent + quote,
    ):
        selection = _selection(section, text)
        result = project_explicit_facts(selection)
        assert result.facts == []
        assert result.coverage == "bounded_explicit_mentions"
        candidate = FactCandidate(
            kind=kind,
            subject=subject,
            value=value,
            state=state,
            setting=setting,
            source_ref=selection.sources[0].source_ref,
            source_version=7,
            section=section,
            quote=quote,
        )
        with pytest.raises(FactEvidenceError, match="^fact_status_evidence_invalid$"):
            validate_fact_candidates([candidate], selection)


def test_indented_correction_or_overbound_code_cannot_rescue_the_rest_of_a_list():
    for text in (
        "- Planned losartan 50 mg.\n- Continue losartan 25 mg.\n    This supersedes the previous 50 mg plan.",
        "- CBC ordered.\n\tCBC cancelled.",
        "- Taking losartan 25 mg.\n    " + "neutral " * 190 + ".",
    ):
        assert project_explicit_facts(_selection("P - PLAN", text)).facts == []


def test_three_space_payload_and_blank_indentation_keep_valid_source_offsets():
    text = "   Office BP 148/92.\n    \n \t \n -\tCBC ordered."
    result = project_explicit_facts(_selection("O - OBJECTIVE", text))
    assert [(f.kind, f.state, f.evidence.start) for f in result.facts] == [
        ("vital", "measured", 3),
        ("order", "ordered", text.index("CBC ordered.")),
    ]
    for fact in result.facts:
        evidence = fact.evidence
        assert text[evidence.start : evidence.end] == evidence.quote


def test_list_negation_and_order_absence_keep_distinct_documented_states():
    text = "1. Patient denies taking losartan 25 mg.\n2. CBC order not documented.\n3. K/Cr not ordered."
    result = project_explicit_facts(_selection("P - PLAN", text))
    assert [(f.subject, f.state) for f in result.facts] == [
        ("losartan", "documented_not_taking"),
        ("CBC", "order_not_documented"),
        ("K/Cr", "not_ordered"),
    ]


@pytest.mark.parametrize(
    "text",
    [
        "- CBC ordered.\n- For a visitor.",
        "- Office BP 148/92.\n- Hypothetical illustration.",
        "- There is no evidence that CBC was ordered.",
        "- CBC ordered.\n- Result availability unknown.",
    ],
)
def test_order_and_vital_context_cannot_be_removed_by_list_boundaries(text):
    assert project_explicit_facts(_selection("O - OBJECTIVE", text)).facts == []


def test_list_correction_and_semicolon_evidence_use_original_spans():
    text = "- Planned losartan 50 mg.\n- Continue losartan 25 mg.\n- This supersedes the previous 50 mg plan.\n- CBC ordered; CBC cancelled."
    result = project_explicit_facts(_selection("P - PLAN", text))
    assert sum(bool(f.supersedes) for f in result.facts) == 2
    for fact in result.facts:
        evidence = fact.evidence
        assert text[evidence.start : evidence.end] == evidence.quote
        if fact.correction_evidence is not None:
            assert fact.correction_evidence.quote == text
            prior = next(f for f in result.facts if f.fact_ref in fact.supersedes)
            assert prior.evidence.end <= evidence.start


def test_list_candidate_uses_actual_occurrence_and_cannot_select_a_substring():
    text = "Patient is Taking losartan 25 mg.\n1. Taking losartan 25 mg."
    selection = _selection("Medications", text)
    candidate = FactCandidate(
        kind="medication",
        subject="losartan",
        value="25 mg",
        state="documented_taking",
        source_ref=selection.sources[0].source_ref,
        source_version=7,
        section="Medications",
        quote="Taking losartan 25 mg.",
    )
    result = validate_fact_candidates([candidate], selection)
    assert result.facts[0].evidence.start == text.rindex(candidate.quote)
    qualified = _selection("Medications", "- Taking losartan 25 mg; for Ann.")
    with pytest.raises(FactEvidenceError):
        validate_fact_candidates([candidate], qualified)


def test_actual_rendered_soap_controls_cover_dated_observations_and_correction():
    path = (
        Path(__file__).parents[1]
        / "fixtures/session_prep/rendered_soap_format_controls.json"
    )
    fixture = json.loads(path.read_text())
    for kind in ("plain_sections", "representative_list_sections"):
        sources = tuple(
            SimpleNamespace(
                source_ref=f"synthetic_rendered_visit_{note['visit']}",
                source_version=1,
                source_at=datetime.fromisoformat(
                    note["date_used_for_synthetic_control"]
                ).replace(tzinfo=UTC),
                content=note[kind],
            )
            for note in fixture["notes"]
        )
        result = project_explicit_facts(
            cast(
                "ReviewedNoteSelection",
                SimpleNamespace(sources=sources, truncated=False),
            )
        )
        assert any(
            f.value == "148/92 mmHg"
            and f.setting == "office"
            and f.evidence.source_at.date().isoformat() == "2026-09-30"
            for f in result.facts
        )
        assert any(
            f.value == "128 to 136 systolic and 78 to 84 diastolic"
            and f.setting == "home"
            and f.evidence.source_at.date().isoformat() == "2026-10-07"
            for f in result.facts
        )
        correction = next(f for f in result.facts if f.state == "documented_continued")
        prior = next(f for f in result.facts if f.fact_ref in correction.supersedes)
        assert (correction.value, correction.evidence.source_at.date().isoformat()) == (
            "25 mg",
            "2026-10-08",
        )
        assert (
            prior.value,
            prior.state,
            prior.evidence.source_at.date().isoformat(),
        ) == ("50 mg", "documented_listed", "2026-10-07")
        orders = {
            (f.subject.casefold(), f.state)
            for f in result.facts
            if f.kind == "order"
            and f.evidence.source_at.date().isoformat() == "2026-10-08"
        }
        assert {
            ("cbc", "cancelled"),
            ("cbc", "result_not_documented"),
            ("potassium", "not_ordered"),
            ("potassium", "result_not_documented"),
            ("creatinine", "not_ordered"),
            ("creatinine", "result_not_documented"),
        } <= orders
        assert not any(
            f.state in {"ordered", "documented_taking"} for f in result.facts
        )
        for fact in result.facts:
            source = next(
                s for s in sources if s.source_ref == fact.evidence.source_ref
            )
            text = source.content[fact.evidence.section]
            assert text[fact.evidence.start : fact.evidence.end] == fact.evidence.quote
            assert fact.evidence.source_at == source.source_at
