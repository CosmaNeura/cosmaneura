"""Transient, source-linked omissions audit for the bot-only note adapter.

Validated references preserve wording, not semantic completeness. No audit data
or additional fields are stored; only compact context in existing history fields.
"""

import copy
import json
import re

from app.services.scribe_capture_history_grounding import normalized

KEY = "source_review"
ROLE_QUALIFICATION = (
    "Source: model-classified speech; speaker roles are not independently verified."
)
UNKNOWN_QUALIFICATION = (
    "Source: unidentified speech; speaker roles are not established."
)
MIXED_QUALIFICATION = "Source: model-classified and unidentified speech; speaker identities are not independently verified."
MEDICATION_UNSPECIFIED = "Current medication list was not specified in the transcript."
_HISTORY = re.compile(
    r"\b(?:objective|history|histories|ros|medications?|medicines?)\b|\breview of systems\b"
)
_EXCLUDED = re.compile(
    r"\b(?:subjective|assessment|attestation|signature|patient|provider|date|consent)\b"
)
_METADATA = re.compile(
    r"(?:patient|provider|doctor|clinician|therapist)(?: (?:name|id|signature|demographics))?"
    r"|(?:visit|encounter|appointment) (?:date|time|id)"
    r"|date|time|date of birth|dob|mrn|signature|attestation|consent|billing"
    r"|(?:diagnosis|procedure|icd(?: 10)?) codes?"
)


class SourceReviewInvalid(ValueError):
    """An invalid audit cannot silently fall back to unchecked prose."""


def empty(text):
    return text.strip().casefold().rstrip(".") in {
        "",
        "not mentioned",
        "not mentioned in transcript",
    }


def schema(base, *, protected):
    indexes = {"type": "array", "items": {"type": "integer"}}
    audit = {
        "type": "object",
        "properties": {
            "unresolved_questions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "question_ref": {"type": "integer"},
                        "answer_refs": indexes,
                        "unanswered_parts": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "required": ["question_ref", "answer_refs", "unanswered_parts"],
                    "additionalProperties": False,
                },
            },
            "medications": {
                "type": "object",
                "properties": {
                    "current_list_refs": indexes,
                    "change_history_refs": indexes,
                },
                "required": ["current_list_refs", "change_history_refs"],
                "additionalProperties": False,
            },
        },
        "required": ["unresolved_questions", "medications"],
        "additionalProperties": False,
    }
    result = copy.deepcopy(base)
    if not protected:
        result = {
            "type": "object",
            "properties": {"fields": result},
            "required": ["fields"],
            "additionalProperties": False,
        }
    result["properties"][KEY] = audit
    result["required"].append(KEY)
    return result


def prompt():
    return (
        "\nFinal bot response adds source_review to the response envelope; this "
        "supersedes earlier exact envelope-key lists, not the note's field names. "
        "Keep prose concise and use the existing fields. Audit the entire source "
        "for clinically relevant unanswered or partially answered questions. "
        "For each, give question_ref, the exact later answer_refs (empty if none), "
        "and unanswered_parts copied as exact contiguous wording from that question. "
        "For multipart questions, a reply to one alternative does not answer the "
        "others. Check later replies and corrections before calling any part "
        "unanswered; a later complete answer removes that unresolved item. "
        "Select all unresolved parts, including alternatives not mentioned "
        "in a reply. Do not infer an answer from surrounding history. Fully answered "
        "questions need no audit item. This is a focused omissions audit, not a "
        "classification of every source. The server renders selected question/reply "
        "context and unresolved wording in an existing Objective/history field; do "
        "not repeat it in prose or turn it into Assessment or a Plan action. "
        "The medications audit separates current_list_refs (actual current list "
        "reports, including an explicit no-current-medications statement) from "
        "change_history_refs (reported new/started/stopped/changed/unchanged "
        "medicine history). One source may supply both. Prospective prescribing decisions are neither. "
        "A no-new-medicine/no-change statement does not supply a current list. "
        "Denying one drug is not an explicit empty current list. "
        "When only changes are discussed, current_list_refs must be empty and "
        "change_history_refs must retain that evidence; the server adds the "
        "unspecified-list caveat. Do not invent medication names or infer that a "
        "formal reconciliation was or was not performed. Use only complete "
        "transcript source indexes; exclude server recording boundaries. "
        "The server supplies one source-role qualification per unprotected "
        "nonempty narrative, so do not repeat that boilerplate yourself. "
        "Valid audit references do not prove complete or correct selection.\n"
    )


def split(raw, sources, *, protected, max_bytes):
    def closed(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise SourceReviewInvalid
            result[key] = value
        return result

    value = json.loads(raw, object_pairs_hook=closed)
    if (
        not isinstance(value, dict)
        or KEY not in value
        or not isinstance(value.get("fields"), dict)
    ):
        raise SourceReviewInvalid
    audit = value.pop(KEY)
    if not isinstance(audit, dict) or set(audit) != {
        "unresolved_questions",
        "medications",
    }:
        raise SourceReviewInvalid

    def indexes(items):
        if (
            not isinstance(items, list)
            or len(items) > 64
            or any(type(x) is not int or not 1 <= x <= len(sources) for x in items)
            or len(set(items)) != len(items)
            or any(sources[x - 1].kind != "transcript" for x in items)
        ):
            raise SourceReviewInvalid
        return sorted(items)

    questions, medication = audit["unresolved_questions"], audit["medications"]
    if not isinstance(questions, list) or len(questions) > 32:
        raise SourceReviewInvalid
    contexts, seen = [], set()
    for item in questions:
        if not isinstance(item, dict) or set(item) != {
            "question_ref",
            "answer_refs",
            "unanswered_parts",
        }:
            raise SourceReviewInvalid
        ref = indexes([item["question_ref"]])[0]
        answers = indexes(item["answer_refs"])
        parts = item["unanswered_parts"]
        if (
            ref in seen
            or any(x <= ref for x in answers)
            or not isinstance(parts, list)
            or not 1 <= len(parts) <= 8
            or any(
                not isinstance(x, str)
                or not x.strip()
                or x != x.strip()
                or len(x) > 512
                or x not in sources[ref - 1].text
                for x in parts
            )
            or len(set(parts)) != len(parts)
        ):
            raise SourceReviewInvalid
        seen.add(ref)

        def quote(index):
            source = sources[index - 1]
            role = source.attribution.removeprefix("Transcript — ").removesuffix(
                " (role not independently verified)"
            )
            return f"({role}) {json.dumps(source.text, ensure_ascii=False)}"

        text = "Source question needing review: " + quote(ref)
        if answers:
            text += " Related reply: " + "; ".join(quote(x) for x in answers)
        limitation = (
            "Answer not established for: "
            + "; ".join(json.dumps(x, ensure_ascii=False) for x in parts)
            + "."
        )
        contexts.append(
            (
                ref,
                {
                    "full": text + ". " + limitation,
                    "limitation": limitation,
                    "sources": [sources[x - 1].text for x in [ref, *answers]],
                },
            )
        )
    if not isinstance(medication, dict) or set(medication) != {
        "current_list_refs",
        "change_history_refs",
    }:
        raise SourceReviewInvalid
    current, changes = (
        indexes(medication["current_list_refs"]),
        indexes(medication["change_history_refs"]),
    )
    if not protected:
        if set(value) != {"fields"}:
            raise SourceReviewInvalid
        value = value["fields"]
    remaining = json.dumps(value, ensure_ascii=False)
    if len(remaining.encode("utf-8")) > max_bytes:
        raise SourceReviewInvalid
    return (
        remaining,
        [text for _, text in sorted(contexts)],
        bool(changes and not current),
    )


def render(note, sources, *, protected, plan_fields, contexts, medication_unspecified):
    note = dict(note)
    eligible = [
        key
        for key in note
        if key not in plan_fields
        and _HISTORY.search(normalized(key))
        and not _EXCLUDED.search(normalized(key))
    ]
    eligible.sort(
        key=lambda key: 0 if re.search(r"\bobjective\b", normalized(key)) else 1
    )
    if contexts or medication_unspecified:
        if not eligible:
            raise SourceReviewInvalid
        key = eligible[0]
        existing = "" if empty(note[key]) else note[key]
        additions = ([MEDICATION_UNSPECIFIED] if medication_unspecified else []) + [
            item["limitation"]
            if key in protected and all(text in existing for text in item["sources"])
            else item["full"]
            for item in contexts
        ]
        # Grounded history already has exact selected wording; do not replace it.
        note[key] = "\n\n".join([*([existing] if existing else []), *additions])
    speech = [x for x in sources if x.kind == "transcript"]
    unknown = sum(x.attribution == "Transcript — Unidentified speaker" for x in speech)
    qualification = (
        UNKNOWN_QUALIFICATION
        if unknown == len(speech)
        else MIXED_QUALIFICATION
        if unknown
        else ROLE_QUALIFICATION
    )
    for key, value in note.items():
        if (
            key not in protected
            and not _METADATA.fullmatch(normalized(key))
            and not empty(value)
            and not value.startswith(qualification)
        ):
            note[key] = qualification + "\n\n" + value
    return note
