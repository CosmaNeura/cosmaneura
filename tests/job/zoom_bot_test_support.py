"""Shared constructors for handcrafted synthetic contract controls."""

import json
from types import SimpleNamespace as NS

from app.services import scribe_capture_history_grounding as history

SOAP = {
    "Subjective": "Reported history",
    "Objective": "Data",
    "Assessment": "Impression",
    "Plan": "Planned care",
}


def transcript(*rows):
    return NS(
        doctor_id="synthetic",
        transcript_data=[
            NS(speaker=role, content=text, startTime=i * 1000, endTime=(i + 1) * 1000)
            for i, (role, text) in enumerate(rows)
        ],
    )


def context_selection(grounding, refs=()):
    if not hasattr(grounding.context, "questions"):
        return list(refs)
    questions = grounding.context.questions
    scoped = set()
    rows = []
    for question in questions:
        selected = list(refs) if question in refs else []
        scoped.update(selected)
        rows.append({"question_ref": question, "source_refs": selected})
    return {
        "question_scopes": rows,
        "other_source_refs": [index for index in refs if index not in scoped],
    }


def primary_selection(grounding, selected=()):
    if hasattr(grounding.primary, "optional"):
        return {
            str(index): "clinical_history" if index in selected else "nonclinical"
            for index in grounding.primary.optional
        }
    return list(selected)


def response(grounding, fields=SOAP, *, plans=(), histories=(), context=(), prose=None):
    result = {
        "fields": dict(prose) if prose is not None else {key: "" for key in fields}
    }
    if grounding.plan.fields:
        result["plan_source_refs"] = {key: list(plans) for key in grounding.plan.fields}
    if grounding.history.fields:
        result[history.KEY] = {
            key: {"source_refs": list(histories)} for key in grounding.history.fields
        }
    if grounding.context.target:
        result["source_context_refs"] = context_selection(grounding, context)
    if getattr(grounding, "primary", None) is not None and grounding.primary.target:
        result["fields"][grounding.primary.target] = ""
        from app.services.zoom_bot_primary_history import KEY

        result[KEY] = primary_selection(
            grounding,
            [index for index in grounding.primary.allowed if index not in plans],
        )
    if (
        not grounding.plan.fields
        and not grounding.history.fields
        and not grounding.context.target
        and not getattr(getattr(grounding, "primary", None), "target", None)
    ):
        result = result["fields"]
    return json.dumps(result)
