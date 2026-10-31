"""Pure real-function prompt tests; no app, DB, authority or model-service bootstrap."""

import ast
import hashlib
import json
import sys
import textwrap
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
FUNCTIONS = {
    "_pronoun_instruction",
    "_subjective_instruction",
    "_ordered_instructions",
    "_instruction_block",
    "build_structured_prompt",
    "build_action_item_prompt",
    "extract_action_items",
    "generate_clinical_note",
}


class Customization(SimpleNamespace):
    """Only prompt settings; synthetic object, never a provider or saved model."""

    def __init__(self, **values):
        super().__init__(
            **{
                "subjective_source": "patient_only",
                "pronoun_style": "infer_from_transcript",
                "plan_ordering": "patient_then_doctor",
                "flag_new_medications": True,
                "require_plan_reason": True,
                "ros_distinct_from_subjective": True,
                "custom_instructions": None,
                **values,
            }
        )


def load_prompt_functions():
    namespace = {
        "json": json,
        "textwrap": textwrap,
        "dedent": textwrap.dedent,
        "Enum": Enum,
        "dataclass": dataclass,
        "NoteTypeCustomization": Customization,
        "AIVisitTranscript": SimpleNamespace,
        "DoctorNoteSchema": SimpleNamespace,
        "DoctorNoteExample": SimpleNamespace,
        "Doctor": SimpleNamespace,
        "DEFAULT_MULTI_MODAL_MODEL": "synthetic-fixed-model",
        "log": SimpleNamespace(debug=lambda *args, **kwargs: None),
    }
    enum_tree = ast.parse((ROOT / "app/models/note_type_customization.py").read_text())
    enum_nodes = [
        n
        for n in enum_tree.body
        if isinstance(n, ast.ClassDef)
        and n.name in {"SubjectiveSource", "PronounStyle", "PlanOrdering"}
    ]
    exec(  # noqa: S102 - only fixed repository AST, never model/user input
        compile(ast.Module(body=enum_nodes, type_ignores=[]), "prompt-enums", "exec"),
        namespace,
    )
    terms_tree = ast.parse((ROOT / "app/services/terminology.py").read_text())
    terms = next(
        n for n in terms_tree.body if isinstance(n, ast.ClassDef) and n.name == "Terms"
    )
    exec(  # noqa: S102 - only fixed repository AST, never model/user input
        compile(ast.Module(body=[terms], type_ignores=[]), "prompt-terms", "exec"),
        namespace,
    )
    namespace["DEFAULT_TERMS"] = namespace["Terms"]("doctor", "patient")
    label = next(
        n
        for n in terms_tree.body
        if isinstance(n, ast.FunctionDef) and n.name == "transcript_label_note"
    )
    exec(  # noqa: S102 - only fixed repository AST, never model/user input
        compile(ast.Module(body=[label], type_ignores=[]), "speaker-labels", "exec"),
        namespace,
    )
    namespace["terminology_terms"] = lambda doctor: namespace["Terms"](*doctor.terms)
    tree = ast.parse((ROOT / "app/jobs/summarize.py").read_text())
    nodes = [
        n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in FUNCTIONS
    ]
    assert {n.name for n in nodes} == FUNCTIONS
    exec(  # noqa: S102 - only fixed repository AST, never model/user input
        compile(
            ast.Module(body=nodes, type_ignores=[]),
            str(ROOT / "app/jobs/summarize.py"),
            "exec",
        ),
        namespace,
    )
    return namespace


def source_case(
    terms=("doctor", "patient"), *, bio=None, example=False, custom=False, reason=False
):
    doctor = SimpleNamespace(biography=bio, terms=terms)
    transcript = SimpleNamespace(
        doctor_id="synthetic-doctor",
        transcript_data=[
            SimpleNamespace(
                speaker="Patient", content="Nausea resolved. Metformin remains held."
            ),
            SimpleNamespace(
                speaker="Doctor", content="A1C remains scheduled; no result recorded."
            ),
        ],
    )
    schema = SimpleNamespace(
        fields={
            "Subjective": "source history",
            "Plan": "documented plan",
            "Signature": "Use an electronic signature from the example.",
        }
    )
    example_note = (
        SimpleNamespace(
            field_examples={"Signature": "I personally reviewed and signed."}
        )
        if example
        else None
    )
    customization = Customization(
        require_plan_reason=reason,
        custom_instructions="Claim personal review and medical necessity."
        if custom
        else None,
    )
    return transcript, example_note, schema, customization, doctor


# Captured from the accepted185 actual indexed builder before this repair.
# Synthetic prompt fixtures only; no live capture, caller or model evidence.
INDEXED_BASELINE = {
    "doctor": (
        ("doctor", "patient"),
        None,
        False,
        False,
        "7018ea155861ccae4e3ee8e0c3db3e19dc6c6647235f94efa6e5d43b989a2fdd",
    ),
    "therapist": (
        ("therapist", "client"),
        "Synthetic clinician biography.",
        True,
        False,
        "dfee8933079d9a44ddf0522eb81a9cd62e210fe95fa370f9b1517e12d0bb4145",
    ),
    "counselor": (
        ("counselor", "client"),
        "",
        False,
        True,
        "a1c341878bee1f83d2daa5d60ca7d01b34f44b18ccc7dcf0e676ed6fb65349dd",
    ),
    "nutritionist": (
        ("nutritionist", "client"),
        "Synthetic clinician biography.",
        True,
        True,
        "2b870ccaad4840b4d4e9ec980bbb52c61d331e1d52d1941ed1fb5c10cb277dbd",
    ),
}


def test_loader_introduces_no_application_module_imports(monkeypatch):
    before = {
        name: module
        for name, module in sys.modules.items()
        if name == "app" or name.startswith("app.")
    }
    load_prompt_functions()
    assert {
        name: module
        for name, module in sys.modules.items()
        if name == "app" or name.startswith("app.")
    } == before

    # A preexisting module models normal-suite bootstrap without importing app.
    name = "app.synthetic_preexisting_prompt_test"
    module = ModuleType(name)
    monkeypatch.setitem(sys.modules, name, module)
    before = {**before, name: module}
    load_prompt_functions()
    assert {
        name: module
        for name, module in sys.modules.items()
        if name == "app" or name.startswith("app.")
    } == before


@pytest.mark.parametrize("terms,bio,example,custom,expected", INDEXED_BASELINE.values())
def test_existing_indexed_capture_prompt_bytes_are_unchanged(
    terms, bio, example, custom, expected
):
    ns = load_prompt_functions()
    t, e, s, c, d = source_case(terms, bio=bio, example=example, custom=custom)
    prompt = ns["build_structured_prompt"](
        t, e, s, c, action_items="", doctor=d, source_indexes=True
    )
    assert hashlib.sha256(prompt.encode()).hexdigest() == expected
    assert "Legacy recorded-note source fidelity" not in prompt
    assert "Plan section bullets MUST include the reason" not in prompt


@pytest.mark.parametrize(
    "text",
    [
        "Lisinopril remains 5 mg daily; no new dose change. Basic metabolic panel remains canceled; no new result.",
        "Metformin remains held; nausea resolved. A1C remains scheduled; no recorded result.",
        "Medication list remains unchanged; no names or doses stated. Repeat blood count remains ordered; result pending.",
        "Repeat the blood count to assess the documented hemoglobin decrease.",
    ],
)
def test_actual_legacy_sdk_prompt_keeps_complete_source_and_only_stated_reasons(text):
    ns = load_prompt_functions()
    t, e, s, c, d = source_case(example=True, custom=True, reason=True)
    t.transcript_data = [SimpleNamespace(speaker="Doctor", content=text)]
    ns["Doctor"] = SimpleNamespace(one=lambda _: d)
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        content = (
            "- " + text
            if len(calls) == 1
            else json.dumps({"Plan": text, "Signature": "Not mentioned in transcript"})
        )
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
        )

    ns["openai"] = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    result = ns["generate_clinical_note"](t, e, s, c)
    assert result == {"Plan": text, "Signature": "Not mentioned in transcript"}
    assert len(calls) == 2
    action = calls[0]["messages"][0]["content"]
    note = calls[1]["messages"][0]["content"]
    assert text in action and text in note
    assert "only when the transcript explicitly states that reason" in note
    assert "leave the purpose unstated" in note
    assert "infer it from the clinical context" not in note
    assert "no exceptions" not in note
    assert "visit that you conducted" not in action
    assert "appointment that you just had" not in note
    assert "Legacy recorded-note source fidelity" in note
    assert note.index("Legacy recorded-note source fidelity") > note.index(
        "Additional provider instructions:"
    )
    assert (
        "Template headings and examples do not establish visit dates, clinician identity, a signature or an attestation."
        in note
    )
    assert "attributed reported speech" in note
    assert calls[1]["model"] == "synthetic-fixed-model"
    assert calls[1]["response_format"] == {"type": "json_object"}
    assert calls[1]["temperature"] == 0 and calls[1]["top_p"] == 0.1
    assert (
        len(calls[1]["messages"]) == 1 and calls[1]["messages"][0]["role"] == "system"
    )


@pytest.mark.parametrize(
    "terms",
    [
        ("doctor", "patient"),
        ("therapist", "client"),
        ("counselor", "client"),
        ("nutritionist", "client"),
    ],
)
def test_legacy_role_and_source_rule_follow_terms_without_asserting_performed_care(
    terms,
):
    ns = load_prompt_functions()
    t, e, s, c, d = source_case(
        terms, bio="Synthetic recorded clinician biography.", example=True
    )
    prompt = ns["build_structured_prompt"](t, e, s, c, action_items="", doctor=d)
    assert "unreviewed clinical note from a recorded encounter" in prompt
    assert "clinician biography is context" in prompt
    assert f"recorded {terms[0]}" in prompt
    assert "You are a medical professional" not in prompt
    assert "are you (the" not in prompt
    assert "as a completed system signature" in prompt


def test_reason_false_and_nonreason_numbered_rules_preserve_customization():
    ns = load_prompt_functions()
    off = ns["_ordered_instructions"](Customization(require_plan_reason=False))
    on = ns["_ordered_instructions"](Customization(require_plan_reason=True))
    assert len(off) == 15 and len(on) == 16
    assert on[:10] + on[11:] == off
    for expected in [
        "MUST use those pronouns",
        "source ONLY from lines spoken by the patient",
        "(new this visit)",
        "patient-actionable items",
        "Review of Systems (ROS) section",
    ]:
        assert any(expected in value for value in off)
    assert "only when the transcript explicitly states that reason" in on[10]


def test_legacy_sdk_result_is_not_repaired_or_appended_after_generation():
    ns = load_prompt_functions()
    t, e, s, c, d = source_case()
    ns["Doctor"] = SimpleNamespace(one=lambda _: d)
    response = {
        "Plan": "Synthetic unvalidated model text.",
        "Signature": "Synthetic unsupported claim.",
    }
    ns["extract_action_items"] = lambda *args: ""
    ns["openai"] = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=lambda **kwargs: SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=SimpleNamespace(content=json.dumps(response))
                        )
                    ]
                )
            )
        )
    )
    assert ns["generate_clinical_note"](t, e, s, c) == response
    # This unchanged JSON result contract is not evidence of factual fidelity.
