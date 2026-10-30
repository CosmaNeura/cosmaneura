"""Observed synthetic dose-statement errors are not clinical treatment changes."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

import app.services.session_prep_generator as generator
from app.services.session_prep import SessionPrepBriefText
from app.services.session_prep_note_sources import (
    ReviewedNoteSelection,
    ReviewedNoteSource,
)

from tests.services.session_prep_generator_test import (
    _fake,
    _historical_selection,
    _neutral_historical_brief,
)
from tests.services.session_prep_runtime_test import _brief

INITIAL = "Lisinopril 10 mg daily, as stated in the initial fictional recording."
CORRECTED = "Correction: lisinopril 5 mg daily, not 10 mg. The previous dose statement was an error."
FIELDS = (
    "recap",
    "trajectory_and_material_changes",
    "talking_points",
    "open_threads",
    "cautions",
    "suggested_outline",
)


def _selection(
    *, no_change=False, separate_dates=False, initial=INITIAL, corrected=CORRECTED
):
    # These medication clauses are the exact observed fictional Bob source
    # text. The actual fixture contains no statement about a clinical change.
    earlier = datetime(2026, 10, 29, 21, 15, 38, 965000, tzinfo=UTC)
    later = datetime(2026, 10, 29, 21, 25, 17, 297000, tzinfo=UTC)
    if separate_dates:
        later += timedelta(days=1)
    if no_change:
        corrected += " No dose change."
    return ReviewedNoteSelection(
        tuple(
            ReviewedNoteSource(
                source_ref=f"reviewed_note_synthetic_bob_{index}",
                source_version=1,
                source_at=at,
                clinician_action_ref=f"prep_note_finalization_synthetic_bob_{index}",
                content_sha256=str(index) * 64,
                content={"Medications": text},
            )
            for index, (at, text) in enumerate(
                ((earlier, initial), (later, corrected)), 1
            )
        ),
        False,
    )


def _supported(selection):
    prepared = _brief(selection)
    earlier, corrected = selection.sources
    corrected_clauses = generator._clauses(corrected.content["Medications"])
    prepared.recap.text = (
        f"{earlier.source_at.date()}: {earlier.content['Medications']} "
        + " ".join(
            f"{corrected.source_at.date()}: {clause}" for clause in corrected_clauses
        )
    )
    prepared.recap.source_refs = [earlier.source_ref, corrected.source_ref]
    return prepared


def _append(prepared, field, text, selection):
    if field == "recap":
        prepared.recap.text += " " + text
    else:
        getattr(prepared, field).append(
            SessionPrepBriefText(
                text=text,
                source_refs=[source.source_ref for source in selection.sources],
            )
        )


@pytest.mark.parametrize("newest_first", [False, True])
@pytest.mark.parametrize("separate_dates", [False, True])
def test_exact_observed_error_preserves_both_dated_statements_without_inventing_change(
    separate_dates, newest_first
):
    selection = _selection(separate_dates=separate_dates)
    assert selection.sources[0].content["Medications"] == INITIAL
    assert selection.sources[1].content["Medications"] == CORRECTED
    prepared = _supported(selection)
    if newest_first:
        # select_reviewed_note_sources orders descending appointment dates.
        selection = ReviewedNoteSelection(tuple(reversed(selection.sources)), False)
    generator.validate_generated_brief(prepared, selection)
    assert "No dose change" not in prepared.model_dump_json()
    assert "plan" not in prepared.recap.text
    assert len(generator._documented_dose_errors(selection)) == 1


def test_complete_evidence_can_be_split_between_recap_and_material_changes():
    selection = _selection(separate_dates=True)
    earlier, corrected = selection.sources
    prepared = _supported(selection)
    prepared.recap.text = f"{earlier.source_at.date()}: {INITIAL}"
    prepared.recap.source_refs = [earlier.source_ref]
    prepared.trajectory_and_material_changes = [
        SessionPrepBriefText(
            text=" ".join(
                f"{corrected.source_at.date()}: {clause}"
                for clause in generator._clauses(CORRECTED)
            ),
            source_refs=[corrected.source_ref],
        )
    ]
    generator.validate_generated_brief(prepared, selection)


def test_internal_error_descriptor_repr_does_not_disclose_source_clauses():
    error = generator._documented_dose_errors(_selection())[0]
    represented = repr(error).casefold()
    assert "lisinopril" not in represented
    assert "10 mg" not in represented and "5 mg" not in represented
    assert "previous dose statement" not in represented


@pytest.mark.parametrize("newest_first", [False, True])
def test_equal_timestamps_fail_closed_instead_of_using_list_position(newest_first):
    selection = _selection()
    prepared = _supported(selection)
    corrected = replace(selection.sources[1], source_at=selection.sources[0].source_at)
    sources = (selection.sources[0], corrected)
    if newest_first:
        sources = tuple(reversed(sources))
    with pytest.raises(generator.SessionPrepGenerationError) as caught:
        generator.validate_generated_brief(
            prepared, ReviewedNoteSelection(sources, False)
        )
    assert caught.value.code == "output_fidelity_rejected"


def test_tied_previous_statements_do_not_choose_an_arbitrary_source_reference():
    selection = _selection()
    duplicate = replace(
        selection.sources[0], source_ref="reviewed_note_synthetic_bob_tie"
    )
    with pytest.raises(generator.SessionPrepGenerationError) as caught:
        generator.validate_generated_brief(
            _supported(selection),
            ReviewedNoteSelection(
                (*selection.sources, duplicate),
                False,
            ),
        )
    assert caught.value.code == "output_fidelity_rejected"


def test_conflicting_error_corrections_in_one_source_fail_closed():
    corrected = CORRECTED + " Correction: lisinopril 2 mg daily, not 10 mg."
    selection = _selection(corrected=corrected)
    with pytest.raises(generator.SessionPrepGenerationError) as caught:
        generator.validate_generated_brief(_supported(selection), selection)
    assert caught.value.code == "output_fidelity_rejected"


def test_conflicting_corrections_at_one_timestamp_fail_closed():
    selection = _selection()
    duplicate = replace(
        selection.sources[1],
        source_ref="reviewed_note_synthetic_bob_conflict",
        content={"Medications": CORRECTED.replace("5 mg", "2 mg")},
    )
    with pytest.raises(generator.SessionPrepGenerationError) as caught:
        generator.validate_generated_brief(
            _supported(selection),
            ReviewedNoteSelection(
                (*selection.sources, duplicate),
                False,
            ),
        )
    assert caught.value.code == "output_fidelity_rejected"


@pytest.mark.parametrize("field", FIELDS)
@pytest.mark.parametrize(
    "assertion",
    [
        "2026-10-29: lisinopril 5 mg daily superseded the earlier plan.",
        "2026-10-29: the earlier plan was superseded.",
        "2026-10-29: lisinopril 5 mg daily was the documented plan.",
        "2026-10-29: No dose change.",
        "2026-10-29: No dose change was made.",
        "2026-10-29: lisinopril dose changed to 5 mg daily.",
        "2026-10-29: lisinopril dose was unchanged.",
        "2026-10-29: lisinopril changed the dose from 10 mg to 5 mg.",
    ],
)
def test_error_does_not_support_plan_or_either_direction_of_actual_change(
    field, assertion
):
    selection = _selection()
    prepared = _supported(selection)
    _append(prepared, field, assertion, selection)
    with pytest.raises(generator.SessionPrepGenerationError) as caught:
        generator.validate_generated_brief(prepared, selection)
    assert caught.value.code == "output_fidelity_rejected"
    assert caught.value.safety_categories == ()


@pytest.mark.parametrize("omission", ["earlier", "error", "date", "reference"])
def test_correction_cannot_omit_or_misattribute_its_material_source_evidence(omission):
    selection = _selection(separate_dates=True)
    prepared = _supported(selection)
    if omission == "earlier":
        prepared.recap.text = prepared.recap.text.replace(f"2026-10-29: {INITIAL} ", "")
    elif omission == "error":
        prepared.recap.text = prepared.recap.text.replace(
            "2026-10-30: The previous dose statement was an error.", ""
        )
    elif omission == "date":
        prepared.recap.text = prepared.recap.text.replace("2026-10-30:", "2026-10-29:")
    else:
        prepared.recap.source_refs = [selection.sources[1].source_ref]
    with pytest.raises(generator.SessionPrepGenerationError) as caught:
        generator.validate_generated_brief(prepared, selection)
    assert caught.value.code == "output_fidelity_rejected"


def test_explicit_no_change_is_preserved_only_in_the_contrasting_source_that_contains_it():
    selection = _selection(no_change=True)
    prepared = _supported(selection)
    generator.validate_generated_brief(prepared, selection)
    assert "2026-10-29: No dose change." in prepared.recap.text
    prepared.recap.text = prepared.recap.text.replace("2026-10-29: No dose change.", "")
    with pytest.raises(generator.SessionPrepGenerationError):
        generator.validate_generated_brief(prepared, selection)


def test_a_genuine_documented_prior_plan_keeps_the_existing_status_contract():
    selection = _historical_selection()
    assert generator._documented_dose_errors(selection) == []
    prepared = _neutral_historical_brief(selection)
    generator.validate_generated_brief(prepared, selection)
    assert (
        "superseded by the documented 25 mg daily plan"
        in prepared.trajectory_and_material_changes[0].text
    )


def test_a_source_documenting_an_actual_change_does_not_become_a_recording_error():
    changed = "Lisinopril dose changed from 10 mg to 5 mg daily."
    selection = _selection(corrected=changed)
    prepared = _supported(selection)
    generator.validate_generated_brief(prepared, selection)
    assert generator._documented_dose_errors(selection) == []
    assert f"2026-10-29: {changed}" in prepared.recap.text
    assert "previous dose statement was an error" not in prepared.recap.text


def test_other_literal_dated_treatment_status_is_not_blocked_by_bobs_recording_error():
    selection = _selection()
    extra = ReviewedNoteSource(
        source_ref="reviewed_note_synthetic_other_0001",
        source_version=1,
        source_at=datetime(2026, 10, 29, tzinfo=UTC),
        clinician_action_ref="prep_note_finalization_synthetic_other_0001",
        content_sha256="3" * 64,
        content={"Medications": "Losartan dose changed to 25 mg daily."},
    )
    selection = ReviewedNoteSelection((*selection.sources, extra), False)
    prepared = _supported(ReviewedNoteSelection(selection.sources[:2], False))
    prepared.cautions = [
        SessionPrepBriefText(
            text="2026-10-29: Losartan dose changed to 25 mg daily.",
            source_refs=[selection.sources[0].source_ref, extra.source_ref],
        )
    ]
    generator.validate_generated_brief(prepared, selection)


@pytest.mark.parametrize(
    "source",
    [
        '"Correction: lisinopril 5 mg daily, not 10 mg. The previous dose statement was an error."',
        "Correction: lisinopril 5 mg daily, not 10 mg. The previous dose statement may have been an error.",
        CORRECTED + " Whether the current dose was changed is unclear.",
    ],
)
def test_quoted_or_qualified_corrections_do_not_trigger_the_closed_error_guard(source):
    # This scope assertion is not proof that arbitrary ambiguous output is
    # faithful. Such semantics remain subject to the prompt and live review.
    assert generator._documented_dose_errors(_selection(corrected=source)) == []


def test_fidelity_retry_uses_unchanged_observed_sources_and_never_reuses_the_rejected_draft(
    monkeypatch,
):
    selection = _selection()
    expected = _supported(selection)
    rejected = expected.model_copy(deep=True)
    _append(
        rejected,
        "recap",
        "2026-10-29: lisinopril 5 mg was the earlier plan. private-rejected-sentinel",
        selection,
    )
    calls = _fake(
        monkeypatch,
        None,
        responses=[
            (rejected.model_dump_json(), "stop", None),
            (expected.model_dump_json(), "stop", None),
        ],
    )
    result = generator.generate_prepared_brief(selection)
    assert result == expected
    assert len(calls["requests"]) == 2
    assert calls["requests"][0]["messages"][1] == calls["requests"][1]["messages"][1]
    supplied = json.loads(calls["requests"][0]["messages"][1]["content"])
    assert [note["sections"] for note in supplied["reviewed_notes"]] == [
        {"Medications": INITIAL},
        {"Medications": CORRECTED},
    ]
    assert "private-rejected-sentinel" not in json.dumps(calls["requests"])
    assert all(
        options["max_retries"] == 0 and options["timeout"].read == 25
        for options in calls["all_options"]
    )
    assert generator.MAX_MODEL_WORK_SECONDS == 50


def test_repeated_inference_is_rejected_without_a_third_call_or_repair(monkeypatch):
    selection = _selection()
    rejected = _supported(selection)
    _append(rejected, "cautions", "2026-10-29: No dose change was made.", selection)
    before = rejected.model_dump_json()
    calls = _fake(monkeypatch, before)
    with pytest.raises(generator.SessionPrepGenerationError) as caught:
        generator.generate_prepared_brief(selection)
    assert caught.value.code == "output_fidelity_rejected"
    assert rejected.model_dump_json() == before
    assert len(calls["requests"]) == 2
