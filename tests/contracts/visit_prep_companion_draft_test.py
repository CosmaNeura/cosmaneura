"""Synthetic draft-wire compatibility, not receiver authorization acceptance."""

from __future__ import annotations

import ast
import copy
import json
from datetime import datetime
from pathlib import Path

import pytest

from app.routes.visit_prep_packages import VisitPrepPatientSourcesResponse
from app.routes.visit_prep_preparations import VisitPrepCommandStateResponse
from app.routes.visit_prep_synthesis import VisitPrepSynthesisResponse
from app.services.visit_prep_contribution_contract import ContributionBasis
from app.services.visit_prep_synthesis_contract import (
    VisitPrepPreparedBrief,
    VisitPrepSynthesisInvalid,
    VisitPrepSynthesisSelection,
    VisitPrepSynthesisSource,
    validate_generated_brief,
    validate_selection,
)

ROOT = Path(__file__).resolve().parents[2]
DIRECTORY = ROOT / "docs/contracts/visit-prep-companion-v1"


def _read(name):
    return json.loads((DIRECTORY / name).read_text())


def _selection(value):
    sources = value["package"]["sources"]
    preparation = sources["preparation"]
    selected = []
    for topic in sources["topics"]:
        if topic["response_kind"] != "answered":
            continue
        labels = {choice["value"]: choice["label"] for choice in topic["choices"]}
        selected.append(
            VisitPrepSynthesisSource(
                question_key=topic["question_key"],
                position=topic["position"],
                prompt=topic["prompt"],
                kind=topic["kind"],
                source_ref=topic["current_revision_id"]
                or topic["original_response_id"],
                recorded_at=datetime.fromisoformat(topic["recorded_at"]),
                answer=topic["answer"],
                choice_label=labels[topic["answer"]]
                if topic["kind"] == "choice"
                else None,
            )
        )
    return VisitPrepSynthesisSelection(
        package_id=sources["package_id"],
        source_version=sources["source_version"],
        question_set_version_id=preparation["question_set_version_id"],
        basis=ContributionBasis(**sources["basis"]),
        actor_role=sources["actor_role"],
        original_interview_completed=sources["original_interview_completed"],
        current_answers_complete=sources["current_answers_complete"],
        current_answers_reviewed=sources["current_answers_reviewed"],
        applicable_question_keys=tuple(
            topic["question_key"] for topic in sources["topics"]
        ),
        missing_question_keys=tuple(sources["missing_question_keys"]),
        skipped_question_keys=tuple(sources["skipped_question_keys"]),
        sources=tuple(selected),
    )


def _fields(source, name):
    tree = ast.parse((ROOT / source).read_text())
    definition = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == name
    )
    return {
        node.target.id
        for node in definition.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    }


def test_draft_package_uses_current_dashboard_fields_and_exact_roundtrip():
    value = _read("fixtures/result-ready-unlinked.json")
    package = value["package"]
    parsed = VisitPrepSynthesisResponse.model_validate(package)
    assert parsed == VisitPrepSynthesisResponse.model_validate_json(json.dumps(package))
    assert set(package) == set(VisitPrepSynthesisResponse.model_fields)
    assert set(package["sources"]) == set(VisitPrepPatientSourcesResponse.model_fields)
    assert set(package["sources"]["preparation"]) == set(
        VisitPrepCommandStateResponse.model_fields
    )
    assert parsed.sources.package_id == value["preparation_ref"]
    assert parsed.sources.preparation.id == value["preparation_ref"]
    assert parsed.sources.preparation.patient_id is None
    assert parsed.sources.preparation.appointment_id is None
    assert parsed.sources.topics[-1].question_key == "story"
    assert parsed.sources.topics[-1].position == 5
    assert parsed.sources.topics[-1].kind == "story"
    assert parsed.sources.topics[-1].answer is not None
    assert "🙂" in parsed.sources.topics[-1].answer
    selection = _selection(value)
    validate_selection(selection)
    assert parsed.brief is not None
    validate_generated_brief(parsed.brief, selection)


def test_closed_schema_fields_follow_actual_route_and_brief_models():
    schema = _read("result.schema.json")["$defs"]
    classes = [
        ("package", "app/routes/visit_prep_synthesis.py", "VisitPrepSynthesisResponse"),
        (
            "sources",
            "app/routes/visit_prep_packages.py",
            "VisitPrepPatientSourcesResponse",
        ),
        (
            "preparation",
            "app/routes/visit_prep_preparations.py",
            "VisitPrepCommandStateResponse",
        ),
        ("topic", "app/routes/visit_prep_packages.py", "VisitPrepPackageTopicResponse"),
        (
            "choice",
            "app/routes/visit_prep_packages.py",
            "VisitPrepPackageChoiceResponse",
        ),
        ("basis", "app/routes/visit_prep_packages.py", "VisitPrepPackageBasisResponse"),
        (
            "brief",
            "app/services/visit_prep_synthesis_contract.py",
            "VisitPrepPreparedBrief",
        ),
        (
            "briefItem",
            "app/services/visit_prep_synthesis_contract.py",
            "VisitPrepBriefItem",
        ),
        (
            "evidence",
            "app/services/visit_prep_synthesis_contract.py",
            "VisitPrepBriefEvidence",
        ),
    ]
    for name, source, model in classes:
        expected = _fields(source, model)
        assert schema[name]["additionalProperties"] is False
        assert set(schema[name]["required"]) == expected
        assert set(schema[name]["properties"]) == expected


def test_all_skipped_ready_report_has_empty_brief_not_invented_content():
    value = _read("fixtures/result-ready-unlinked.json")
    sources = value["package"]["sources"]
    for topic in sources["topics"]:
        topic.update(
            response_kind="skipped",
            answer=None,
            initial_response_kind="skipped",
            initial_answer=None,
        )
    sources["skipped_question_keys"] = [
        topic["question_key"] for topic in sources["topics"]
    ]
    value["package"]["brief"] = dict(
        headline=None,
        summary=[],
        visit_priorities=[],
        medication_and_adherence=[],
        barriers=[],
        uncertainties=[],
    )
    parsed = VisitPrepSynthesisResponse.model_validate(value["package"])
    assert parsed.brief is not None
    validate_generated_brief(parsed.brief, _selection(value))
    assert parsed.brief.items() == ()


def test_partial_caregiver_preserves_unanswered_story_without_claiming_completion():
    value = _read("fixtures/result-ready-unlinked.json")
    sources = value["package"]["sources"]
    sources.update(
        actor_role="caregiver",
        original_interview_completed=False,
        current_answers_complete=False,
        current_answers_reviewed=False,
        current_review_id=None,
        reviewed_at=None,
        basis={"original_response_count": 3, "event_sequence": 0},
        missing_question_keys=["story"],
    )
    sources["preparation"]["lifecycle_state"] = "in_progress"
    sources["topics"][-1].update(
        response_kind=None,
        answer=None,
        initial_response_kind=None,
        initial_answer=None,
        original_response_id=None,
        recorded_at=None,
        initial_recorded_at=None,
    )
    value["package"].update(
        status="missing",
        build_status="not_requested",
        artifact_id=None,
        version=None,
        recorded_at=None,
        brief=None,
    )
    parsed = VisitPrepSynthesisResponse.model_validate(value["package"])
    validate_selection(_selection(value))
    assert parsed.sources.actor_role == "caregiver"
    assert parsed.sources.missing_question_keys == ["story"]
    assert parsed.brief is None


def test_corrected_story_preserves_original_words_and_requires_current_citations():
    value = _read("fixtures/result-ready-unlinked.json")
    sources = value["package"]["sources"]
    story = sources["topics"][-1]
    correction = "visit_prep_contribution_01h45ytscbebyvny4gc8cr8mab"
    story.update(
        answer="Invented corrected story: I changed my evening routine.",
        changed=True,
        current_revision_id=correction,
        recorded_at="2026-10-01T11:15:00Z",
    )
    sources.update(
        source_version="c" * 64,
        current_answers_reviewed=False,
        current_review_id=None,
        reviewed_at=None,
        basis={"original_response_count": 4, "event_sequence": 2},
    )
    value["package"].update(
        artifact_id="visit_prep_brief_01h45ytscbebyvny4gc8cr8mac",
        version=2,
        recorded_at="2026-10-01T11:45:00Z",
    )
    headline = {
        "text": "Patient reports a changed evening routine.",
        "evidence": [
            {"source_ref": correction, "quote": "I changed my evening routine."}
        ],
    }
    value["package"]["brief"]["headline"] = headline
    parsed = VisitPrepSynthesisResponse.model_validate(value["package"])
    selection = _selection(value)
    assert parsed.brief is not None
    validate_generated_brief(parsed.brief, selection)
    assert parsed.sources.topics[-1].initial_answer is not None
    assert "morning routine" in parsed.sources.topics[-1].initial_answer
    assert parsed.sources.topics[-1].original_response_id != correction
    headline["evidence"][0]["source_ref"] = story["original_response_id"]
    with pytest.raises(VisitPrepSynthesisInvalid):
        validate_generated_brief(
            VisitPrepPreparedBrief.model_validate(value["package"]["brief"]), selection
        )


@pytest.mark.parametrize(
    "change",
    [
        lambda x: x["package"]["sources"]["topics"].pop(),
        lambda x: x["package"]["sources"].update(missing_question_keys=["reason"]),
        lambda x: x["package"]["sources"].update(current_answers_complete=False),
        lambda x: x["package"]["sources"]["topics"][0].update(position=5),
    ],
)
def test_existing_domain_rejects_structurally_valid_wrong_source_states(change):
    value = _read("fixtures/result-ready-unlinked.json")
    change(value)
    with pytest.raises(VisitPrepSynthesisInvalid):
        validate_selection(_selection(value))


@pytest.mark.parametrize(
    "change",
    [
        lambda x: x["package"]["brief"]["headline"]["evidence"][0].update(
            source_ref="visit_prep_response_01h45ytscbebyvny4gc8cr8ma4"
        ),
        lambda x: x["package"]["brief"]["headline"]["evidence"][0].update(
            quote="Invented unmatched quotation"
        ),
        lambda x: x["package"]["brief"]["headline"].update(
            text="Start taking medication."
        ),
    ],
)
def test_existing_grounding_rejects_wrong_or_directive_brief_content(change):
    value = copy.deepcopy(_read("fixtures/result-ready-unlinked.json"))
    change(value)
    with pytest.raises(VisitPrepSynthesisInvalid):
        validate_generated_brief(
            VisitPrepPreparedBrief.model_validate(value["package"]["brief"]),
            _selection(value),
        )
