"""Synthetic rule diagnostics and source-only recovery; no model transport."""

import ast
import json
from contextlib import nullcontext
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

import app.services.session_prep_generator as generator
from app.services.session_prep import (
    ImmutableBrief,
    SessionPrepBriefText,
    SessionPrepPatientReported,
    _passes_safety,
)
from app.services.session_prep_note_sources import (
    ReviewedNoteSelection,
    ReviewedNoteSource,
)

from tests.services.session_prep_generator_test import _fake
from tests.services.session_prep_runtime_test import _brief


@pytest.fixture
def selection():
    return ReviewedNoteSelection(
        (
            ReviewedNoteSource(
                source_ref="reviewed_note_synthetic_category_0001",
                source_version=1,
                source_at=datetime(2026, 10, 7, tzinfo=UTC),
                clinician_action_ref="prep_note_finalization_synthetic_category_0001",
                content_sha256="0" * 64,
                content={
                    "Assessment": "Documented assessment: hypertension was under review.",
                    "Plan": "The documented planned dose was 50 mg; documented use was 25 mg. No emergency encounter was documented.",
                },
            ),
        ),
        False,
    )


def safe_brief(selection):
    prepared = _brief(selection)
    prepared.recap.text = (
        "2026-10-07: documented assessment: hypertension was under review. "
        "A planned dose of 50 mg was recorded; documented use was 25 mg. "
        "No emergency encounter was documented."
    )
    return prepared


CASES = [
    (
        "2026-10-07: the patient was diagnosed with hypertension.",
        ("diagnosis_assertion",),
        "output_safety_01",
    ),
    (
        "2026-10-07: the prior instruction to increase the dose to 50 mg was superseded.",
        ("medication_action",),
        "output_safety_04",
    ),
    ("Stop now and review the chart.", ("leading_action",), "output_safety_02"),
    ("Do not delay emergency care.", ("emergency_deferral",), "output_safety_08"),
    (
        "Delay emergency care until the visit.",
        ("emergency_deferral",),
        "output_safety_08",
    ),
    (
        "This confirms a diagnosis. Start taking 50 mg. Delay emergency care.",
        generator.SAFETY_CATEGORIES,
        "output_safety_15",
    ),
]


@pytest.mark.parametrize("text,categories,code", CASES)
def test_rejection_categories_keep_safety_and_closed_metadata(
    selection, text, categories, code
):
    prepared = safe_brief(selection)
    prepared.recap.text = text + " private-rejected-category-sentinel"
    with pytest.raises(generator.SessionPrepGenerationError) as caught:
        generator.validate_generated_brief(prepared, selection)
    error = caught.value
    assert error.code == str(error) == "output_safety_rejected"
    assert error.safety_categories == categories and error.failure_code == code
    assert not _passes_safety(
        ImmutableBrief(
            "synthetic_validation", selection.sources[0].source_at, prepared, ()
        )
    )
    assert "private-rejected-category-sentinel" not in repr(vars(error))
    assert len(error.failure_code) <= 32


@pytest.mark.parametrize("text,categories,code", CASES)
def test_reason_specific_second_call_uses_only_original_sources(
    monkeypatch, selection, text, categories, code
):
    expected = safe_brief(selection)
    rejected = expected.model_copy(deep=True)
    rejected.recap.text = text + " private-rejected-category-sentinel"
    calls = _fake(
        monkeypatch,
        None,
        responses=[
            (rejected.model_dump_json(), "stop", None),
            (expected.model_dump_json(), "stop", None),
        ],
    )
    assert generator.generate_prepared_brief(selection) == expected
    first, second = calls["requests"]
    assert first["messages"][1] == second["messages"][1]
    assert all(len(call["messages"]) == 2 for call in calls["requests"])
    for category in categories:
        assert (
            generator._SAFETY_REGENERATION_INSTRUCTIONS[category]
            in second["messages"][0]["content"]
        )
    for other in set(generator.SAFETY_CATEGORIES) - set(categories):
        assert (
            generator._SAFETY_REGENERATION_INSTRUCTIONS[other]
            not in second["messages"][0]["content"]
        )
    assert "private-rejected-category-sentinel" not in json.dumps(calls["requests"])
    assert all(
        option["max_retries"] == 0 and option["timeout"].read <= 25
        for option in calls["all_options"]
    )


@pytest.mark.parametrize("text,categories,code", CASES)
def test_second_unsafe_draft_still_fails_without_third_call(
    monkeypatch, selection, text, categories, code
):
    rejected = safe_brief(selection)
    rejected.recap.text = text + " private-rejected-category-sentinel"
    calls = _fake(monkeypatch, rejected.model_dump_json())
    with pytest.raises(generator.SessionPrepGenerationError) as caught:
        generator.generate_prepared_brief(selection)
    assert caught.value.failure_code == code
    assert len(calls["requests"]) == 2
    assert "private-rejected-category-sentinel" not in json.dumps(calls["requests"])


@pytest.mark.parametrize(
    "field",
    [
        "recap",
        "trajectory_and_material_changes",
        "talking_points",
        "open_threads",
        "cautions",
        "suggested_outline",
    ],
)
@pytest.mark.parametrize("text,categories,code", CASES[:4])
def test_every_egress_field_retains_the_same_rules(
    selection, field, text, categories, code
):
    prepared = safe_brief(selection)
    if field == "recap":
        prepared.recap.text = text
    else:
        setattr(
            prepared,
            field,
            [
                SessionPrepBriefText(
                    text=text, source_refs=[selection.sources[0].source_ref]
                )
            ],
        )
    assert generator._safety_categories(prepared) == categories
    with pytest.raises(generator.SessionPrepGenerationError) as caught:
        generator.validate_generated_brief(prepared, selection)
    assert caught.value.failure_code == code


def test_closed_codes_cover_all_combinations_without_schema_change():
    assert len(set(generator.SAFETY_FAILURE_CODES.values())) == 15
    for mask in range(1, 16):
        categories: tuple[generator.SafetyCategory, ...] = tuple(
            item
            for index, item in enumerate(generator.SAFETY_CATEGORIES)
            if mask & (1 << index)
        )
        assert (
            generator.SessionPrepGenerationError(
                "output_safety_rejected", safety_categories=categories
            ).failure_code
            == f"output_safety_{mask:02d}"
        )
    assert (
        generator.SessionPrepGenerationError("output_safety_rejected").failure_code
        == "output_safety_rejected"
    )
    assert (
        generator.SessionPrepGenerationError("model_timeout").failure_code
        == "model_timeout"
    )


def test_patient_field_rules_and_disabled_patient_prep_remain(selection):
    prepared = safe_brief(selection)
    prepared.patient_reported = SessionPrepPatientReported(
        label="Patient-reported Visit Prep",
        summary="Delay emergency care.",
        source_refs=[selection.sources[0].source_ref],
    )
    assert generator._safety_categories(prepared) == ("emergency_deferral",)
    assert not _passes_safety(
        ImmutableBrief(
            "synthetic_validation", selection.sources[0].source_at, prepared, ()
        )
    )
    with pytest.raises(generator.SessionPrepGenerationError) as caught:
        generator.validate_generated_brief(prepared, selection)
    assert caught.value.code == "output_lineage_invalid"


@pytest.mark.parametrize(
    "code,categories",
    [
        ("private-code", ()),
        ("output_safety_rejected", ("private-category",)),
        ("output_safety_rejected", ("leading_action", "leading_action")),
        ("model_timeout", ("leading_action",)),
        ("output_safety_rejected", ["leading_action"]),
    ],
)
def test_invalid_diagnostic_metadata_cannot_be_retained(code, categories):
    with pytest.raises(ValueError) as caught:
        generator.SessionPrepGenerationError(code, safety_categories=categories)
    assert str(caught.value) == "Invalid closed safety metadata"


def test_category_recovery_keeps_shared_fifty_second_budget(monkeypatch, selection):
    expected = safe_brief(selection)
    rejected = expected.model_copy(deep=True)
    rejected.recap.text = CASES[0][0]
    times = iter([0, 0, 1, 49.9, 49.95, 49.99])
    monkeypatch.setattr(generator, "monotonic", lambda: next(times))
    calls = _fake(
        monkeypatch,
        None,
        responses=[
            (rejected.model_dump_json(), "stop", None),
            (expected.model_dump_json(), "stop", None),
        ],
    )
    assert generator.generate_prepared_brief(selection) == expected
    assert calls["all_options"][1]["timeout"].read == pytest.approx(0.1)
    assert generator.MAX_MODEL_WORK_SECONDS == 50


@pytest.mark.parametrize("mask", range(1, 16))
def test_actual_worker_exception_boundary_preserves_closed_code_without_db(
    mask, selection
):
    """Execute the actual claim-free generation block with a fixed in-memory seam."""
    worker_path = (
        Path(__file__).resolve().parents[2] / "app/services/session_prep_worker.py"
    )
    tree = ast.parse(worker_path.read_bytes())
    worker = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "run_one_build"
    )
    block = next(node for node in worker.body if isinstance(node, ast.Try))
    categories: tuple[generator.SafetyCategory, ...] = tuple(
        item
        for index, item in enumerate(generator.SAFETY_CATEGORIES)
        if mask & (1 << index)
    )
    error = generator.SessionPrepGenerationError(
        "output_safety_rejected", safety_categories=categories
    )

    def generate(_selection):
        raise error

    class ScopeDenied(Exception):
        pass

    namespace = dict(
        get_session=lambda: nullcontext(SimpleNamespace(get=lambda *_args: object())),
        cast=cast,
        Session=object,
        ProviderRecordAuthority=object,
        scope_id="synthetic-scope",
        settings=object(),
        now=selection.sources[0].source_at,
        wanted="synthetic-digest",
        current_source_state=lambda *_args, **_kwargs: (selection, "synthetic-digest"),
        generate=generate,
        SessionPrepScopeDenied=ScopeDenied,
        SessionPrepGenerationError=generator.SessionPrepGenerationError,
    )
    exec(
        compile(ast.Module(body=[block], type_ignores=[]), str(worker_path), "exec"),
        namespace,
    )
    assert namespace["prepared"] is None
    assert namespace["failure"] == f"output_safety_{mask:02d}"
