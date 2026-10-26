"""Transient source audit fixtures; no model selection or clinical PASS claim."""

import json
from types import SimpleNamespace as NS

import pytest

from app.jobs import summarize
from app.services import zoom_bot_note_grounding as bot
from app.services import zoom_bot_source_review as review

from app.models.note_type_customization import NoteTypeCustomization

FIELDS = {
    "Subjective": "Patient history",
    "Objective": "Observed data",
    "Assessment": "Impression",
    "Plan": "Care",
}


def fixture(*rows):
    transcript = NS(
        transcript_data=[NS(speaker=role, content=text) for role, text in rows]
    )
    g = bot.BotGrounding.build(transcript, FIELDS, patient_only=True)
    value = {
        "fields": {key: "Not mentioned in transcript." for key in FIELDS},
        "plan_source_refs": {"Plan": []},
        "source_review": {
            "unresolved_questions": [],
            "medications": {"current_list_refs": [], "change_history_refs": []},
        },
    }
    if g.history.fields:
        value["reported_history_source_refs"] = {key: [] for key in g.history.fields}
    return g, value


def resolve(g, value):
    return g.resolve(json.dumps(value), FIELDS, require_plan_reason=True)


def partial():
    g, value = fixture(
        ("Doctor", "Was there a fall or another injury?"),
        ("Patient", "There was no fall."),
    )
    value["source_review"]["unresolved_questions"] = [
        {"question_ref": 1, "answer_refs": [2], "unanswered_parts": ["another injury"]}
    ]
    return g, value


def test_partial_answer_renders_complete_question_reply_and_only_source_wording():
    g, value = partial()
    note = resolve(g, value)
    assert note["Objective"] == (
        "Source: model-classified speech; speaker roles are not independently verified.\n\n"
        'Source question needing review: (model-classified Doctor) "Was there a fall or another injury?" '
        'Related reply: (model-classified Patient) "There was no fall.". Answer not established for: "another injury".'
    )
    assert note["Assessment"] == "Not mentioned in transcript."
    assert "no other injury" not in str(note) and "injury" not in note["Plan"]
    assert review.KEY not in note


@pytest.mark.parametrize(
    "change",
    [
        lambda a: a.update(question_ref=True),
        lambda a: a.update(question_ref=0),
        lambda a: a.update(question_ref=3),
        lambda a: a.update(answer_refs=[1]),
        lambda a: a.update(answer_refs=[2, 2]),
        lambda a: a.update(answer_refs=["2"]),
        lambda a: a.update(unanswered_parts=[]),
        lambda a: a.update(unanswered_parts=["no injury"]),
        lambda a: a.update(unanswered_parts=["another injury", "another injury"]),
        lambda a: a.update(unanswered_parts=[" another injury"]),
        lambda a: a.update(diagnosis="injury excluded"),
    ],
)
def test_bad_question_audit_refuses_instead_of_keeping_unchecked_prose(change):
    g, value = partial()
    change(value["source_review"]["unresolved_questions"][0])
    with pytest.raises(bot.BotNoteContractInvalid):
        resolve(g, value)


def test_duplicate_question_ref_is_not_deduped_or_silently_accepted():
    g, value = partial()
    value["source_review"]["unresolved_questions"] *= 2
    with pytest.raises(bot.BotNoteContractInvalid):
        resolve(g, value)


@pytest.mark.parametrize(
    "current,changes,warning",
    [([], [1], True), ([2], [1], False), ([2], [], False), ([], [], False)],
)
def test_medication_list_provided_differs_from_change_history(
    current, changes, warning
):
    g, value = fixture(
        ("Patient", "I have not changed any medicine."),
        ("Patient", "I take the named medicine daily."),
    )
    value["source_review"]["medications"] = {
        "current_list_refs": current,
        "change_history_refs": changes,
    }
    note = resolve(g, value)
    assert (
        "Current medication list was not specified in the transcript."
        in note["Objective"]
    ) is warning
    assert "review was not performed" not in str(note)
    # Handcrafted selections exercise rendering only. A model that omits entry2
    # would still require semantic rejection; valid refs cannot prove absence.


@pytest.mark.parametrize(
    "current,changes", [([1], [1]), ([0], []), ([], [True]), ([], [1, 1]), ("1", [])]
)
def test_bad_medication_audit_refuses(current, changes):
    g, value = fixture(("Patient", "No new medicine."))
    value["source_review"]["medications"] = {
        "current_list_refs": current,
        "change_history_refs": changes,
    }
    with pytest.raises(bot.BotNoteContractInvalid):
        resolve(g, value)


def test_absent_and_extra_audit_contracts_refuse_old_response_without_repair():
    g, value = partial()
    del value[review.KEY]
    with pytest.raises(bot.BotNoteContractInvalid):
        resolve(g, value)
    _, value = partial()
    value[review.KEY]["extra"] = "unsupported"
    with pytest.raises(bot.BotNoteContractInvalid):
        resolve(g, value)


def test_server_boundary_cannot_be_question_reply_or_medication_evidence():
    from app.services.scribe_capture_plan_grounding import PlanSource

    sources = (
        PlanSource("transcript", "Transcript — Unidentified speaker", "Any symptoms?"),
        PlanSource(
            "recording_boundary", "Server recording boundary", "[Segment 2 starts]"
        ),
    )
    _, value = partial()
    for audit in [
        {
            "unresolved_questions": [
                {"question_ref": 2, "answer_refs": [], "unanswered_parts": ["Segment"]}
            ],
            "medications": {"current_list_refs": [], "change_history_refs": []},
        },
        value[review.KEY],
        {
            "unresolved_questions": [],
            "medications": {"current_list_refs": [], "change_history_refs": [2]},
        },
    ]:
        value[review.KEY] = audit
        with pytest.raises(review.SourceReviewInvalid):
            review.split(json.dumps(value), sources, protected=True, max_bytes=262144)


@pytest.mark.parametrize(
    "roles,prefix",
    [
        (
            ["Patient"],
            "Source: model-classified speech; speaker roles are not independently verified.",
        ),
        (
            ["Unknown"],
            "Source: unidentified speech; speaker roles are not established.",
        ),
        (
            ["Patient", "Unknown"],
            "Source: model-classified and unidentified speech; speaker identities are not independently verified.",
        ),
    ],
)
def test_role_qualification_is_once_without_upgrading_unknown_or_empty_fields(
    roles, prefix
):
    g, value = fixture(*[(role, "A reported symptom.") for role in roles])
    value["fields"]["Assessment"] = "A model narrative requiring review."
    note = resolve(g, value)
    assert note["Assessment"] == prefix + "\n\nA model narrative requiring review."
    assert note["Subjective"] == "Not mentioned in transcript."
    assert not note["Plan"].startswith("Source:")
    if g.history.fields:
        assert not note["Objective"].startswith("Source:")
    value["fields"]["Assessment"] = note["Assessment"]
    assert resolve(g, value)["Assessment"] == note["Assessment"]


def test_grounded_history_keeps_exact_sources_and_adds_only_missing_limitation():
    g, value = fixture(
        ("Unknown", "Was there a fall or another injury?"),
        ("Unknown", "There was no fall."),
    )
    value["reported_history_source_refs"]["Objective"] = [1, 2]
    value[review.KEY]["unresolved_questions"] = [
        {"question_ref": 1, "answer_refs": [2], "unanswered_parts": ["another injury"]}
    ]
    note = resolve(g, value)
    assert note["Objective"].count("Was there a fall or another injury?") == 1
    assert note["Objective"].count("There was no fall.") == 1
    assert note["Objective"].endswith('Answer not established for: "another injury".')
    assert not note["Objective"].startswith("Source:")


def test_no_permitted_history_field_refuses_instead_of_creating_field_or_plan_action():
    g, value = partial()
    with pytest.raises(review.SourceReviewInvalid):
        review.render(
            {"Assessment": "", "Plan history": "Safe plan"},
            g.plan.sources,
            protected=("Plan history",),
            plan_fields=("Plan history",),
            contexts=[{"full": "context"}],
            medication_unspecified=False,
        )


def test_bot_reason_replacement_retains_preference_and_refuses_upstream_drift():
    c = NoteTypeCustomization()
    before = c.model_dump()
    t = NS(patient="client", provider="therapist")
    old = summarize._instruction_block(c, t)
    new = bot.source_reason_prompt(old, c, t)
    assert "infer it from the clinical context" not in new
    assert "explicit unavailable-rationale limitation satisfies this preference" in new
    assert "client-actionable" in new and c.model_dump() == before
    assert summarize._instruction_block(c, t) == old
    for invalid in [old + old, old.replace("no exceptions", "some exceptions"), ""]:
        with pytest.raises(bot.BotNoteContractInvalid):
            bot.source_reason_prompt(invalid, c, t)
    c.require_plan_reason = False
    assert bot.source_reason_prompt("unchanged", c, t) == "unchanged"


def test_transient_envelope_does_not_confuse_clinical_field_named_fields():
    base = {
        "type": "object",
        "properties": {"fields": {"type": "string"}},
        "required": ["fields"],
        "additionalProperties": False,
    }
    result = review.schema(base, protected=False)
    assert result["properties"]["fields"] == base and review.KEY in result["required"]


@pytest.mark.parametrize(
    "text",
    [
        "I do not take that one drug.",
        "I take no current medicines.",
        "I take the named medicine daily.",
    ],
)
def test_current_list_refs_cannot_prove_clinical_completeness(text):
    g, value = fixture(("Patient", text))
    value[review.KEY]["medications"]["current_list_refs"] = [1]
    # All three are valid references, but the one-drug denial is an incorrect
    # model selection for a complete list. The resolver makes no empty-list
    # assertion; this distinction remains an independent semantic gate.
    assert "takes no medication" not in str(resolve(g, value))
    assert "Denying one drug is not an explicit empty current list" in review.prompt()


def test_later_answer_and_nonquestion_selection_are_not_structural_semantic_proofs():
    g, value = partial()
    # A valid index is not evidence that this declarative answer was a question.
    value[review.KEY]["unresolved_questions"] = [
        {"question_ref": 2, "answer_refs": [], "unanswered_parts": ["no fall"]}
    ]
    assert "There was no fall." in resolve(g, value)["Objective"]
    assert "a later complete answer removes that unresolved item" in review.prompt()
    # Do not claim this deliberately wrong, structurally valid selection passes
    # clinical review. The actual-model gate must reject such misclassification.


def test_later_complete_reply_can_remove_an_initially_unanswered_question():
    g, value = fixture(
        ("Doctor", "Was there a fall or another injury?"),
        ("Patient", "There was no fall."),
        ("Patient", "There was no other injury either."),
    )
    value["fields"]["Subjective"] = (
        '"There was no fall." "There was no other injury either."'
    )
    note = resolve(g, value)  # Handcrafted complete-response audit, not model proof.
    assert "Answer not established" not in str(note)
    assert '"There was no other injury either."' in note["Subjective"]


@pytest.mark.parametrize(
    "heading",
    ["Patient", "Provider", "Date", "Date of birth", "Signature", "ICD-10 code"],
)
def test_metadata_fields_stay_metadata_while_patient_history_is_narrative(heading):
    g, _ = fixture(("Patient", "A reported symptom."))
    note = review.render(
        {heading: "Retained value", "Patient History": "A reported symptom."},
        g.plan.sources,
        protected=(),
        plan_fields=(),
        contexts=[],
        medication_unspecified=False,
    )
    assert note[heading] == "Retained value"
    assert note["Patient History"].startswith("Source: model-classified speech;")
