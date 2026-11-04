"""The exact fictional Bob correction is an input contract, never output repair."""

import json
from dataclasses import replace

import pytest

import app.services.session_prep_generator as generator
import app.services.session_prep_runtime as runtime
from app.services.session_prep import SessionPrepBriefText
from app.services.session_prep_note_sources import ReviewedNoteSelection

from tests.services.session_prep_documentation_correction_test import (
    CORRECTED,
    INITIAL,
    _selection,
    _supported,
)
from tests.services.session_prep_generator_test import _fake

F01_FIELDS = {
    "Fixture provenance": "Fictional beta test only. No real patient information.",
    "Patient": "Bob.",
    "Recording date": "October 29, 2026.",
    "Symptoms and observations": "Fictional home blood pressure 126/78. No dizziness.",
    "Medications": INITIAL,
    "Laboratory status": "Basic metabolic panel ordered; result pending.",
    "Follow-up": "Follow up in four weeks.",
}
F02_FIELDS = F01_FIELDS | {
    "Medications": CORRECTED,
    "Laboratory status": (
        "Basic metabolic panel was canceled; no new laboratory result was reported."
    ),
}


def _full_bob(*, newest_first=True, no_change=False, separate_dates=False):
    base = _selection(no_change=no_change, separate_dates=separate_dates)
    fields = (
        F01_FIELDS,
        F02_FIELDS | {"Medications": base.sources[1].content["Medications"]},
    )
    notes = tuple(
        replace(note, content=dict(content))
        for note, content in zip(base.sources, fields, strict=True)
    )
    return ReviewedNoteSelection(
        tuple(reversed(notes)) if newest_first else notes, False
    )


def _complete_bob(selection):
    earlier, corrected = sorted(selection.sources, key=lambda note: note.source_at)
    prepared = _supported(ReviewedNoteSelection((earlier, corrected), False))
    prepared.trajectory_and_material_changes = [
        SessionPrepBriefText(
            text=f"{note.source_at.date()}: {note.content['Laboratory status']}",
            source_refs=[note.source_ref],
        )
        for note in (earlier, corrected)
    ]
    prepared.talking_points = [
        SessionPrepBriefText(
            text="Discuss the documented medication history and clarify current use.",
            source_refs=[earlier.source_ref, corrected.source_ref],
        )
    ]
    prepared.open_threads = []
    prepared.cautions = []
    prepared.suggested_outline = [
        SessionPrepBriefText(
            text=f"{corrected.source_at.date()}: {corrected.content['Follow-up']}",
            source_refs=[corrected.source_ref],
        )
    ]
    return prepared


@pytest.mark.parametrize("newest_first", [False, True])
@pytest.mark.parametrize("separate_dates", [False, True])
def test_full_bob_input_exposes_complete_literal_date_and_reference_obligations(
    monkeypatch, newest_first, separate_dates
):
    selection = _full_bob(newest_first=newest_first, separate_dates=separate_dates)
    expected = _complete_bob(selection)
    before = [(note.content.copy(), note.content_sha256) for note in selection.sources]
    calls = _fake(monkeypatch, expected.model_dump_json())
    assert generator.generate_prepared_brief(selection) == expected
    payload = json.loads(calls["request"]["messages"][1]["content"])
    assert set(payload) == {
        "response_schema",
        "reviewed_notes",
        "required_literal_evidence",
    }
    assert [note["sections"] for note in payload["reviewed_notes"]] == [
        note.content for note in selection.sources
    ]
    assert [(note.content, note.content_sha256) for note in selection.sources] == before
    contract = payload["required_literal_evidence"]
    assert contract["allowed_output_fields"] == [
        "recap",
        "trajectory_and_material_changes",
    ]
    earlier, corrected = sorted(selection.sources, key=lambda note: note.source_at)
    assert contract["clauses"] == [
        {
            "complete_clause": INITIAL,
            "source_date": str(earlier.source_at.date()),
            "source_ref": earlier.source_ref,
        },
        {
            "complete_clause": "Correction: lisinopril 5 mg daily, not 10 mg.",
            "source_date": str(corrected.source_at.date()),
            "source_ref": corrected.source_ref,
        },
        {
            "complete_clause": "The previous dose statement was an error.",
            "source_date": str(corrected.source_at.date()),
            "source_ref": corrected.source_ref,
        },
    ]
    assert "No dose change" not in json.dumps(contract)
    assert F02_FIELDS["Laboratory status"] in expected.model_dump_json()
    assert "result pending" in expected.model_dump_json()


@pytest.mark.parametrize("explicit", [False, True])
def test_no_change_clause_is_required_only_if_the_original_source_says_so(
    monkeypatch, explicit
):
    selection = _full_bob(no_change=explicit)
    calls = _fake(monkeypatch, _complete_bob(selection).model_dump_json())
    generator.generate_prepared_brief(selection)
    evidence = json.loads(calls["request"]["messages"][1]["content"])[
        "required_literal_evidence"
    ]["clauses"]
    assert len(evidence) == (4 if explicit else 3)
    assert (
        any(item["complete_clause"] == "No dose change." for item in evidence)
        is explicit
    )


@pytest.mark.parametrize(
    "omission", ["original", "correction", "reason", "date", "reference"]
)
def test_incomplete_correction_regenerates_from_original_input_without_salvage(
    monkeypatch, omission
):
    selection = _full_bob()
    expected = _complete_bob(selection)
    rejected = expected.model_copy(deep=True)
    replacements = {
        "original": (INITIAL, ""),
        "correction": ("Correction: lisinopril 5 mg daily, not 10 mg.", ""),
        "reason": ("The previous dose statement was an error.", ""),
        "date": ("2026-10-29:", ""),
    }
    if omission == "reference":
        rejected.recap.source_refs = [selection.sources[0].source_ref]
    else:
        old, new = replacements[omission]
        rejected.recap.text = rejected.recap.text.replace(old, new)
    rejected.recap.text += " private-rejected-literal-sentinel."
    rejected_before = rejected.model_dump_json()
    calls = _fake(
        monkeypatch,
        None,
        responses=[
            (rejected_before, "stop", None),
            (expected.model_dump_json(), "stop", None),
        ],
    )
    assert generator.generate_prepared_brief(selection) == expected
    first, second = calls["requests"]
    assert first["messages"][1] == second["messages"][1]
    assert "required_literal_evidence" in first["messages"][1]["content"]
    assert "private-rejected-literal-sentinel" not in json.dumps(calls["requests"])
    assert rejected.model_dump_json() == rejected_before
    assert len(calls["requests"]) == 2


def test_canceled_bmp_cannot_be_rewritten_as_unperformed_on_either_attempt(monkeypatch):
    selection = _full_bob()
    rejected = _complete_bob(selection)
    rejected.trajectory_and_material_changes[
        -1
    ].text = "2026-10-29: Basic metabolic panel was not performed."
    before = rejected.model_dump_json()
    calls = _fake(monkeypatch, before)
    with pytest.raises(generator.SessionPrepGenerationError) as error:
        generator.generate_prepared_brief(selection)
    assert error.value.code == "output_fidelity_rejected"
    assert rejected.model_dump_json() == before
    assert len(calls["requests"]) == 2
    assert calls["requests"][0]["messages"][1] == calls["requests"][1]["messages"][1]


def test_added_contract_is_counted_in_the_existing_input_bound(monkeypatch):
    selection = _selection(initial=INITIAL.rstrip(".") + " benign" * 7000 + ".")
    calls = _fake(monkeypatch, "{}")
    with pytest.raises(generator.SessionPrepGenerationError) as error:
        generator.generate_prepared_brief(selection)
    assert error.value.code == "source_bound_exceeded"
    assert calls == {}


def test_ambiguous_original_evidence_refuses_before_the_sdk(monkeypatch):
    selection = _selection()
    sources = (
        selection.sources[0],
        replace(selection.sources[0], source_ref="reviewed_note_synthetic_tie"),
        selection.sources[1],
    )
    calls = _fake(monkeypatch, "{}")
    with pytest.raises(generator.SessionPrepGenerationError) as error:
        generator.generate_prepared_brief(ReviewedNoteSelection(sources, False))
    assert error.value.code == "output_fidelity_rejected"
    assert calls == {}


def test_prompt_v8_changes_source_digest_without_mutating_reviewed_sources(monkeypatch):
    selection = _full_bob()
    current = runtime.source_digest(selection)
    assert generator.PROMPT_VERSION == runtime.PROMPT_VERSION == "source_only_prep_v8"
    monkeypatch.setattr(runtime, "PROMPT_VERSION", "source_only_prep_v7")
    assert runtime.source_digest(selection) != current
