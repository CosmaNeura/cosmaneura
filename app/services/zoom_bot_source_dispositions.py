"""Mechanical coverage/placement rules; model purpose labels remain unverified."""

from __future__ import annotations

import re
import unicodedata

from app.services.scribe_capture_plan_grounding import prospective_fields

KEY = "source_dispositions"
PURPOSES = (
    "reported_history",
    "observed_data",
    "clinical_impression",
    "care_plan",
    "clinical_question",
    "contextual_correction",
    "nonclinical",
)
SCOPES = ("not_question", "fully_answered", "partial", "unanswered")
ROW_KEYS = {"source_index", "purpose", "field", "answer_scope", "context_refs"}
MODEL_ROW_KEYS = (ROW_KEYS - {"context_refs"}) | {"context"}
CONTEXT_REVIEW = (
    "Source context only; no finding, diagnosis or care decision is inferred from this selection. "
    "The relationship between these statements is not independently verified."
)


class DispositionInvalid(ValueError):
    """No source values in the exception."""


def field_kind(name: str) -> str:
    name = re.sub(
        r"[\W_]+", " ", unicodedata.normalize("NFKC", name).casefold()
    ).strip()
    if name in {
        "assessment",
        "clinical impression",
        "impression",
        "diagnosis",
        "diagnoses",
    }:
        return "assessment"
    if name in {"subjective", "hpi", "history of present illness"}:
        return "subjective"
    if name in {
        "objective",
        "exam",
        "physical exam",
        "physical examination",
        "vital signs",
    }:
        return "objective"
    # Unknown/custom/combined headings remain the exact supplied schema keys.
    # Their semantic placement is an explicit unproved model decision.
    return "custom"


def schema(fields: dict[str, str], speakers: list[str], *, patient_only: bool) -> dict:
    """Constrain known mechanics; cross-row meaning still belongs to resolve/review.

    Use closed objects, nested anyOf and reusable enums only. A required first
    reference enforces nonempty context without unsupported array bounds. Role
    labels restrict placement; they never become verified clinical identities.
    """
    if not fields or not speakers:
        raise DispositionInvalid

    def closed(properties):
        return {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        }

    ids = list(range(1, len(speakers) + 1))
    patients = [index for index in ids if speakers[index - 1] == "Patient"]
    definitions = {"source_id": {"type": "integer", "enum": ids}}
    if patients:
        definitions["patient_source_id"] = {"type": "integer", "enum": patients}
    for role in tuple(definitions):
        definitions[role + "_context"] = closed(
            {
                "first_ref": {"$ref": "#/$defs/" + role},
                "additional_refs": {
                    "type": "array",
                    "items": {"$ref": "#/$defs/" + role},
                },
            }
        )

    def variant(purposes, targets, scopes, role="source_id", context="optional"):
        context_schema = {"$ref": "#/$defs/" + role + "_context"}
        if context == "optional":
            context_schema = {"anyOf": [context_schema, {"type": "null"}]}
        elif context == "none":
            context_schema = {"type": "null"}
        return closed(
            {
                "source_index": {"$ref": "#/$defs/" + role},
                "purpose": {"type": "string", "enum": list(purposes)},
                "field": {"type": ["string", "null"], "enum": list(targets)},
                "answer_scope": {"type": "string", "enum": list(scopes)},
                "context": context_schema,
            }
        )

    variants = [
        variant(["nonclinical"], [None], ["not_question"], context="none"),
        variant(["clinical_question"], [None], ["fully_answered"], context="required"),
    ]
    plans = prospective_fields(fields)
    questions = [
        name
        for name in fields
        if field_kind(name) not in {"subjective", "assessment"} and name not in plans
    ]
    if questions:
        variants.extend(
            [
                variant(
                    ["clinical_question"], questions, ["partial"], context="required"
                ),
                variant(
                    ["clinical_question"], questions, ["unanswered"], context="none"
                ),
            ]
        )
    # Group equivalent headings: schema size depends on field kinds, not one
    # repeated object per source/field. Source words never affect these choices.
    groups = {}
    for name in fields:
        kind = field_kind(name)
        role = (
            "patient_source_id"
            if patient_only and kind == "subjective"
            else "source_id"
        )
        if role == "patient_source_id" and not patients:
            continue
        if name in plans:
            purposes = ("care_plan", "contextual_correction")
        elif kind == "assessment":
            purposes = ("clinical_impression",)
        else:
            purposes = (
                "reported_history",
                "observed_data",
                "clinical_impression",
                "contextual_correction",
            )
            if kind == "custom":
                purposes += ("care_plan",)
        groups.setdefault((purposes, role), []).append(name)
    for (purposes, role), targets in groups.items():
        variants.append(
            variant(
                [p for p in purposes if p != "contextual_correction"],
                targets,
                ["not_question"],
                role,
            )
        )
        if "contextual_correction" in purposes:
            variants.append(
                variant(
                    ["contextual_correction"],
                    targets,
                    ["not_question"],
                    role,
                    context="required",
                )
            )
    result = closed({KEY: {"type": "array", "items": {"anyOf": variants}}})
    result["$defs"] = definitions
    return result


def decode_model_rows(value: object) -> list[dict]:
    """Decode the new wire shape only; unchanged resolve still checks every ref.

    Never supply a missing anchor, repair old responses or infer a relationship.
    """
    if not isinstance(value, list):
        raise DispositionInvalid
    decoded = []
    for row in value:
        if not isinstance(row, dict) or set(row) != MODEL_ROW_KEYS:
            raise DispositionInvalid
        context = row["context"]
        if row["answer_scope"] == "unanswered" and context is not None:
            raise DispositionInvalid
        refs = []
        if context is not None:
            if (
                not isinstance(context, dict)
                or set(context) != {"first_ref", "additional_refs"}
                or not isinstance(context["additional_refs"], list)
                or len(context["additional_refs"]) > 99
            ):
                raise DispositionInvalid
            refs = [context["first_ref"], *context["additional_refs"]]
        decoded.append(
            {k: v for k, v in row.items() if k != "context"} | {"context_refs": refs}
        )
    return decoded


def resolve(
    value: object, fields: dict[str, str], speakers: list[str], *, patient_only: bool
):
    """Return field refs plus refs that must be labeled context, never free prose.

    Every source is classified once; omission cannot be represented by a missing
    row. Classification itself can be wrong and is not made true by this check.
    No duplicate exclusion: equal words/role labels do not identify one person.
    """
    if not isinstance(value, list) or len(value) != len(speakers):
        raise DispositionInvalid
    rows = {}
    plans = prospective_fields(fields)
    for row in value:
        if not isinstance(row, dict) or set(row) != ROW_KEYS:
            raise DispositionInvalid
        index, purpose, field = row["source_index"], row["purpose"], row["field"]
        scope, related = row["answer_scope"], row["context_refs"]
        if (
            type(index) is not int
            or not 1 <= index <= len(speakers)
            or index in rows
            or not isinstance(purpose, str)
            or purpose not in PURPOSES
            or not isinstance(scope, str)
            or scope not in SCOPES
            or field is not None
            and (not isinstance(field, str) or field not in fields)
            or not isinstance(related, list)
            or len(related) > 100
            or any(
                type(ref) is not int or not 1 <= ref <= len(speakers) or ref == index
                for ref in related
            )
            or len(set(related)) != len(related)
        ):
            raise DispositionInvalid
        if purpose == "nonclinical":
            if field is not None or scope != "not_question" or related:
                raise DispositionInvalid
        elif purpose == "clinical_question":
            if scope == "not_question" or scope != "unanswered" and not related:
                raise DispositionInvalid
            if (field is None) != (scope == "fully_answered"):
                raise DispositionInvalid
        elif field is None or scope != "not_question":
            raise DispositionInvalid
        if purpose == "contextual_correction" and not related:
            raise DispositionInvalid
        if field is not None:
            kind = field_kind(field)
            if kind == "assessment" and purpose != "clinical_impression":
                raise DispositionInvalid
            if kind == "subjective" and (
                purpose == "clinical_question"
                or patient_only
                and speakers[index - 1] != "Patient"
            ):
                raise DispositionInvalid
            if field in plans and purpose not in {"care_plan", "contextual_correction"}:
                raise DispositionInvalid
            if purpose == "care_plan" and field not in plans and kind != "custom":
                raise DispositionInvalid
        rows[index] = row

    refs = {field: set() for field in fields}
    context = {field: set() for field in fields}
    for index, row in rows.items():
        field, purpose = row["field"], row["purpose"]
        if (
            field in plans
            and purpose == "contextual_correction"
            and not any(
                rows[ref]["purpose"] == "care_plan" and rows[ref]["field"] == field
                for ref in row["context_refs"]
            )
        ):
            # History may supply context to a real care decision; it cannot
            # create that decision merely by being labeled a correction.
            raise DispositionInvalid
        if field is not None:
            refs[field].add(index)
            if purpose == "clinical_question" or (
                field_kind(field) == "objective" and purpose != "observed_data"
            ):
                context[field].add(index)
        for ref in row["context_refs"]:
            anchor = rows[ref]
            # A claimed complete answer cannot be hidden in an excluded row.
            # Context is always retained, but its claimed relationship is not
            # treated as a verified clinical answer/person identity.
            if anchor["field"] is None or anchor["purpose"] in {
                "nonclinical",
                "clinical_question",
            }:
                raise DispositionInvalid
            if field is None:  # Fully answered question: retained replies suffice.
                continue
            if (
                field_kind(field) == "subjective"
                and patient_only
                and speakers[ref - 1] != "Patient"
            ):
                raise DispositionInvalid
            refs[field].add(ref)
            context[field].add(ref)
    return ({field: sorted(values) for field, values in refs.items()}, context)
