"""Shared synthetic source/template builders; no test module or pilot dispatch import."""

import json
from pathlib import Path
from types import SimpleNamespace

from app.services import zoom_bot_note_grounding as bot

from app.models.doctor import Terminology

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = json.loads((ROOT / "tools/zoom-note-fidelity/templates.json").read_text())


def inputs(case="R2"):
    fixture = json.loads((ROOT / "tools/capture-retention/fixtures.json").read_text())
    case = next(item for item in fixture["cases"] if item["id"] == case)
    transcript = SimpleNamespace(
        doctor_id="synthetic-doctor",
        transcript_data=[
            SimpleNamespace(speaker=e["speaker"], content=e["content"])
            for e in case["entries"]
        ],
    )
    template = TEMPLATES["templates"][case["id"]]
    schema = SimpleNamespace(fields=template["fields"])
    example = SimpleNamespace(field_examples=template["fieldExamples"])
    doctor = SimpleNamespace(biography=None, terminology=Terminology.doctor_patient)
    return transcript, schema, example, doctor


def row(index, kind, field, context=(), scope="not_question"):
    return {
        "source_index": index,
        "purpose": kind,
        "field": field,
        "answer_scope": scope,
        "context_refs": list(context),
    }


def model_value(value):
    """Encode handcrafted test refs, never repair a retained model response."""
    rows = []
    for row in value[bot.KEY]:
        refs = row["context_refs"]
        rows.append(
            {k: v for k, v in row.items() if k != "context_refs"}
            | {
                "context": {"first_ref": refs[0], "additional_refs": refs[1:]}
                if refs
                else None
            }
        )
    return {bot.KEY: rows}
