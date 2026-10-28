"""Compact, exact source context without an answer-status classifier."""

import json
import re
from dataclasses import dataclass

from app.services import scribe_capture_history_grounding as history
from app.services import zoom_bot_source_constraints as constraints
from app.services.zoom_bot_source_rendering import render_sources

KEY = "source_context_refs"


@dataclass(frozen=True, repr=False)
class SourceContext:
    target: str | None
    sources: tuple
    boundaries: tuple
    identities: tuple = ()

    @classmethod
    def build(
        cls, fields, plan_fields, protected_history, sources, boundaries, identities=()
    ):
        # Patient-only primary fields cannot receive another source's questions.
        # Neutral unidentified history may receive deduplicated source context.
        candidates = tuple(
            field
            for field in constraints.history_fields(fields, plan_fields)
            if field not in protected_history
        )
        target = next(
            (
                field
                for field in candidates
                if re.search(
                    r"\b(?:objective|history|context|ros)\b|review of systems",
                    history.normalized(field),
                )
            ),
            None,
        )
        return cls(target, sources, boundaries, identities)

    @property
    def questions(self):
        return tuple(
            index
            for index, source in enumerate(self.sources, 1)
            if index not in self.boundaries and "?" in source.text
        )

    @property
    def required_questions(self):
        # This is a conservative structural candidate, not an answer-status or
        # clinical-topic classifier. "And" may join a single predicate. Retain
        # the complete source question anyway rather than infer full coverage.
        return tuple(
            index
            for index in self.questions
            if any(
                "?" in clause
                and re.search(r"\b(?:and|or)\b", clause, re.I)
                and not constraints.editing_direction(clause)
                for clause in constraints.sentences(self.sources[index - 1].text)
            )
        )

    def required_refs(self, model_refs=(), rendered_source_refs=()):
        required = set(self.required_questions)
        selected = set(model_refs) | set(rendered_source_refs)
        for question in self.required_questions:
            stop = next(
                (index for index in self.questions if index > question),
                len(self.sources) + 1,
            )
            following = {
                index
                for index in selected
                if question < index < stop and index not in self.boundaries
            }
            if following:
                required.update(following)
                continue
            # If no selected following context exists, retain the next actual
            # non-directive speech entry in this bounded source window. This is
            # adjacent source context, never proof that it answers the question.
            candidate = next(
                (
                    index
                    for index in range(question + 1, stop)
                    if index not in self.boundaries
                    and not all(
                        constraints.editing_direction(clause)
                        for clause in constraints.sentences(
                            self.sources[index - 1].text
                        )
                    )
                ),
                None,
            )
            if candidate is not None:
                required.add(candidate)
        return tuple(sorted(required))

    def prompt(self):
        if self.target is None:
            return ""
        return (
            "\nFinal response format additionally requires source_context_refs with "
            "question_scopes and other_source_refs. Review EVERY listed question "
            "explicitly: question_scopes has exactly one row per question_ref, "
            "with source_refs selecting its complete question and relevant actual "
            "replies when neutral context is needed. A nonempty row must include "
            "its own question_ref. An empty row selects no additional note context; "
            "it never proves an answer or its absence. other_source_refs can select "
            "additional conflicts/requests, but not a listed question. "
            "Select only clinical requests whose scope is "
            "not fully represented by the actual later reply, conflicting reports, "
            "or other context needed to avoid a misleading account. Include the "
            "complete multipart request and its actual partial reply. General "
            "scope example: 'Any cough or wheeze?' followed only by 'No cough' "
            "does not establish whether there is wheeze. Select both source entries "
            "as neutral context; never write 'no wheeze' or declare an answer missing. "
            "By contrast, the same question followed by 'No cough and no wheeze' "
            "has both requested details explicitly represented; choose an empty "
            "source_refs row and retain those clinical facts in history. Do not "
            "copy an ordinary question merely because its actual reply is a "
            "denial. These examples are not encounter evidence and must not enter the note. "
            "Apply this scope check across alternatives even when a narrow denial "
            "already appears in the narrative. Include the "
            "relevant actual replies; do not classify answer status, invent missing "
            "answers or supply an answer from a question. The server retains these "
            "exact source words as neutral context in " + json.dumps(self.target) + ". "
            "Do not duplicate selected context in generated prose. Ordinary fully "
            "answered questions need not be copied; retain their clinical facts "
            "in the narrative. Empty selection is valid. This is a compact note, "
            "not a transcript dump. Keep all existing response properties; when "
            "there are no other reference properties, nest the note in fields.\n"
            + json.dumps(
                {
                    "questions_to_review": [
                        {"question_ref": index, "text": self.sources[index - 1].text}
                        for index in self.questions
                    ]
                }
            )
            + "\n"
        )

    def schema(self, schema, *, enveloped):
        if self.target is None:
            return schema
        if not enveloped:
            schema = {
                "type": "object",
                "properties": {"fields": schema},
                "required": ["fields"],
                "additionalProperties": False,
            }
        props = schema["properties"]
        # Prospective decisions still precede prose. Context is selected before
        # fields so actual partial replies remain visible while composing them.
        indexes = {
            "type": "array",
            "items": {"type": "integer"},
            "maxItems": min(64, len(self.sources)),
        }
        question_ref = {"type": "integer"}
        if self.questions:
            question_ref["enum"] = list(self.questions)
        context = {
            "type": "object",
            "properties": {
                "question_scopes": {
                    "type": "array",
                    "minItems": len(self.questions),
                    "maxItems": len(self.questions),
                    "items": {
                        "type": "object",
                        "properties": {
                            "question_ref": question_ref,
                            "source_refs": indexes,
                        },
                        "required": ["question_ref", "source_refs"],
                        "additionalProperties": False,
                    },
                },
                "other_source_refs": indexes,
            },
            "required": ["question_scopes", "other_source_refs"],
            "additionalProperties": False,
        }
        schema["properties"] = {
            **{key: value for key, value in props.items() if key != "fields"},
            KEY: context,
            "fields": props["fields"],
        }
        schema["required"].append(KEY)
        return schema

    def resolve(self, raw, *, enveloped):
        if self.target is None:
            return raw, ()

        def closed(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise constraints.BotSourceConstraintInvalid
                result[key] = value
            return result

        result = json.loads(raw, object_pairs_hook=closed)
        if not isinstance(result, dict):
            raise constraints.BotSourceConstraintInvalid
        value = result.pop(KEY)
        if not isinstance(value, dict) or set(value) != {
            "question_scopes",
            "other_source_refs",
        }:
            raise constraints.BotSourceConstraintInvalid

        def indexes(refs):
            if (
                not isinstance(refs, list)
                or len(refs) > min(64, len(self.sources))
                or any(
                    type(index) is not int
                    or not 1 <= index <= len(self.sources)
                    or index in self.boundaries
                    for index in refs
                )
                or len(set(refs)) != len(refs)
            ):
                raise constraints.BotSourceConstraintInvalid
            return set(refs)

        refs = indexes(value["other_source_refs"])
        if refs.intersection(self.questions):
            raise constraints.BotSourceConstraintInvalid
        rows, seen = value["question_scopes"], set()
        if not isinstance(rows, list) or len(rows) != len(self.questions):
            raise constraints.BotSourceConstraintInvalid
        for row in rows:
            if not isinstance(row, dict) or set(row) != {"question_ref", "source_refs"}:
                raise constraints.BotSourceConstraintInvalid
            question = row["question_ref"]
            if (
                type(question) is not int
                or question not in self.questions
                or question in seen
            ):
                raise constraints.BotSourceConstraintInvalid
            seen.add(question)
            selected = indexes(row["source_refs"])
            if selected and question not in selected:
                raise constraints.BotSourceConstraintInvalid
            refs.update(selected)
        if len(refs) > 64:
            raise constraints.BotSourceConstraintInvalid
        if not enveloped:
            if set(result) != {"fields"}:
                raise constraints.BotSourceConstraintInvalid
            result = result["fields"]
        return json.dumps(result, ensure_ascii=False), tuple(sorted(refs))

    def render(self, note, refs, plan_fields, *, rendered_source_refs=()):
        if self.target is None:
            return note
        # Keep valid model selections intact. Add the server-owned conservative
        # question refs; do not invent replies or label an answer missing. Actual
        # selected reply/history refs remain available and deduplicate by source.
        refs = tuple(
            sorted(set(refs) | set(self.required_refs(refs, rendered_source_refs)))
        )
        if not refs:
            return note
        # Deduplicate proven source identities, never equal words spoken by
        # different sources or an accidental match in generated narrative.
        selected = [index for index in refs if index not in rendered_source_refs]
        context = render_sources(selected, self.sources, self.identities)
        if context:
            note[self.target] += (
                ("\n\n" if note[self.target] else "")
                + (
                    ""
                    if history.CONTEXT_HEADING in note[self.target]
                    else history.CONTEXT_HEADING
                )
                + context
            )
        return note
