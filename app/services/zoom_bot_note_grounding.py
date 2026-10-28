"""Current capture grounding for the narrowly selected Zoom note pilot.

The model selects complete source entries, not the words rendered in protected
fields. Selection and remaining narrative still require independent review.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, cast

from app.services import scribe_capture_history_grounding as history
from app.services import scribe_capture_plan_grounding as plans
from app.services import zoom_bot_source_constraints as constraints
from app.services import zoom_bot_source_passages as passages
from app.services.zoom_bot_primary_history import PrimaryHistory
from app.services.zoom_bot_source_context import SourceContext
from app.services.zoom_bot_source_rendering import (
    BotPlanGrounding,
    BotReportedHistoryGrounding,
)

ROLE_QUALIFICATION = "Speaker roles are model-classified and require review."
PLAN_REASON_LIMITATION = (
    "Reasons beyond those stated in the selected source are not established."
)
BOT_CONTEXT = (
    "\nBot source roles Doctor, Patient and Other are model classifications, not "
    "verified identities. The entry labels remain authoritative for section "
    "preferences; never infer a different identity from content. Unrecognized "
    "labels remain unidentified. A clinical question is not a finding. Retain "
    "clinically relevant requests and actual later replies as source context in "
    "an appropriate history field, including delayed replies and multipart "
    "questions with partial replies. Do not label a selected question unanswered "
    "or declare an answer absent. Preserve the question and actual reply wording "
    "without inferring their relationship. Do not repeat source context already "
    "retained in a protected field. Assessment contains only an explicitly stated "
    "assessment with its source's uncertainty; history alone does not establish "
    "a diagnosis, differential, benign cause or absence of acute pathology. "
    "The server adds one role-qualification notice; do not repeat it.\n"
)
_METADATA = re.compile(
    r"(?:patient|provider|doctor|clinician|therapist)(?: (?:name|id|signature|demographics))?"
    r"|(?:visit|encounter|appointment) (?:date|time|id)"
    r"|date|time|date of birth|dob|mrn|signature|attestation|consent|billing"
    r"|(?:diagnosis|procedure|icd(?: 10)?) codes?"
)


class BotNoteContractInvalid(ValueError):
    """Value-free failure; never publish an unchecked fallback note."""


def segment_boundaries(transcripts) -> tuple[int, ...]:
    total, result = 0, []
    for index, transcript in enumerate(transcripts):
        if index:
            total += 1
            result.append(total)
        total += len(transcript.transcript_data)
    return tuple(result)


@dataclass(frozen=True, repr=False)
class BotGrounding:
    plan: plans.PlanGrounding
    history: BotReportedHistoryGrounding
    boundaries: tuple[int, ...]
    has_classified_roles: bool
    entries: tuple[constraints.Source, ...]
    context: SourceContext
    primary: PrimaryHistory
    empty_subjective: tuple[str, ...]

    @classmethod
    def build(cls, transcript, fields, *, patient_only, boundaries=()):
        entries = transcript.transcript_data
        if (
            not entries
            or len(entries) > 2000
            or not 1 <= len(fields) <= 100
            or not isinstance(boundaries, tuple)
            or any(type(x) is not int or not 1 <= x <= len(entries) for x in boundaries)
            or tuple(sorted(set(boundaries))) != boundaries
        ):
            raise BotNoteContractInvalid
        sources, classified = [], False
        for index, entry in enumerate(entries, 1):
            if (
                not isinstance(entry.content, str)
                or not entry.content.strip()
                or "\x00" in entry.content
            ):
                raise BotNoteContractInvalid
            if index in boundaries:
                if (
                    entry.speaker != "system"
                    or entry.content
                    != f"[Segment {boundaries.index(index) + 2} starts]"
                    or entry.startTime != 0
                    or entry.endTime != 0
                ):
                    raise BotNoteContractInvalid
                sources.append(
                    plans.PlanSource(
                        "recording_boundary", "Recording boundary", entry.content
                    )
                )
                continue
            role = {
                "Doctor": "clinician",
                "Patient": "patient",
                "Other": "other speaker",
            }.get(entry.speaker)
            classified = classified or role is not None
            attribution = (
                f"Transcript — Model-labeled {role}"
                if role
                else "Transcript — Unidentified speaker"
            )
            sources.append(plans.PlanSource("transcript", attribution, entry.content))
        if not any(source.kind == "transcript" for source in sources):
            raise BotNoteContractInvalid
        identities = tuple(
            (entry.speaker, getattr(entry, "source_speaker_id", None))
            for entry in entries
        )
        plan = BotPlanGrounding(
            plans.prospective_fields(fields), tuple(sources), identities
        )
        empty_subjective = tuple(
            field
            for field in fields
            if patient_only
            and field not in plan.fields
            and not any(entry.speaker == "Patient" for entry in entries)
            and constraints.patient_history_field(field)
        )
        retained = BotReportedHistoryGrounding.for_capture(
            tuple(field for field in fields if field not in empty_subjective),
            plan.fields,
            plan.sources,
            patient_only=patient_only,
        )
        retained = BotReportedHistoryGrounding(
            retained.fields,
            retained.medication_fields,
            retained.sources,
            identities=identities,
        )
        literals = tuple(
            constraints.Source(entry.speaker, entry.content) for entry in entries
        )
        primary = PrimaryHistory.build(
            fields,
            plan.fields,
            retained.fields,
            plan.sources,
            literals,
            patient_only=patient_only,
            boundaries=boundaries,
            identities=identities,
        )
        return cls(
            plan,
            retained,
            boundaries,
            classified,
            literals,
            SourceContext.build(
                fields,
                plan.fields,
                (*primary.fields, *empty_subjective),
                plan.sources,
                boundaries,
                identities,
            ),
            primary,
            empty_subjective,
        )

    @property
    def combined_prospective_fields(self):
        return tuple(
            field
            for field in self.plan.fields
            if constraints.patient_history_field(field)
        )

    def response_schema(self, fields):
        from app.services.scribe_capture_notes import capture_response_schema

        schema = capture_response_schema(tuple(fields), self.plan.fields, self.history)
        enveloped = bool(self.plan.fields or self.history.fields)
        field_schema = schema["properties"]["fields"] if enveloped else schema
        # The bot's evaluated v1 history envelope permits strings that its
        # closed source resolver replaces. Keep capture's newer Objective-only
        # empty-string schema from silently changing this provider contract.
        for field in self.history.fields:
            field_schema["properties"][field].pop("enum", None)
        for field in self.empty_subjective:
            field_schema["properties"][field]["enum"] = [""]
        allowed = [
            index
            for index, source in enumerate(self.entries, 1)
            if index not in self.boundaries
            and not constraints.disallowed_plan_source(source.content)
        ]
        for field in self.plan.fields:
            value = schema["properties"]["plan_source_refs"]["properties"][field]
            if field in self.combined_prospective_fields:
                value["description"] = (
                    "Combined heading uses the source-extractive prospective channel; no inferred patient history."
                )
            if allowed:
                value["items"]["enum"] = allowed
            else:
                value["maxItems"] = 0
        schema = self.primary.schema(schema, enveloped=enveloped)
        return self.context.schema(
            schema, enveloped=enveloped or bool(self.primary.target)
        )

    def resolve(self, raw, fields, *, require_plan_reason):
        from app.services.scribe_capture_notes import (
            MAX_MODEL_BYTES,
            CaptureNoteUnconfirmed,
            _validated_note,
        )

        try:
            if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_MODEL_BYTES:
                raise BotNoteContractInvalid
            raw, context_refs = self.context.resolve(
                raw,
                enveloped=bool(
                    self.plan.fields or self.history.fields or self.primary.target
                ),
            )
            raw, primary_refs = self.primary.resolve(
                raw, enveloped=bool(self.plan.fields or self.history.fields)
            )
            original_history_payload = json.loads(raw) if self.history.fields else None
            history_refs = (
                original_history_payload.get(history.KEY, {})
                if original_history_payload
                else {}
            )
            raw, retained = self.history.resolve(raw, max_bytes=MAX_MODEL_BYTES)
            refs = json.loads(raw)["plan_source_refs"] if self.plan.fields else {}
            if self.plan.fields and self.boundaries:
                refs = json.loads(raw)["plan_source_refs"]
                if any(
                    index in self.boundaries
                    for values in refs.values()
                    for index in values
                ):
                    raise BotNoteContractInvalid
            raw, planned = self.plan.resolve(raw, max_bytes=MAX_MODEL_BYTES)
            note = _validated_note(raw, fields)
            if any(note[field] != "" for field in self.empty_subjective):
                raise BotNoteContractInvalid
            if require_plan_reason:
                planned = {
                    key: value + "\n\n" + PLAN_REASON_LIMITATION
                    for key, value in planned.items()
                }
            for field in self.combined_prospective_fields:
                planned[field] = "Prospective source context:\n" + planned[field]
            note.update(planned)
            note.update(retained)
            note = self.primary.render(note, primary_refs, self.plan.fields, refs)
            # Only references that already passed their original resolver can
            # establish that this exact source is present in another field.
            emitted = set(index for values in refs.values() for index in values)
            for categories in history_refs.values():
                emitted.update(
                    index for values in categories.values() for index in values
                )
            emitted.update(
                self.primary.selected_refs(primary_refs, self.plan.fields, refs)
            )
            additional_context = (
                set(context_refs)
                | set(self.context.required_refs(context_refs, emitted))
            ) - emitted
            if self.context.target in self.history.fields and additional_context:
                # The original history and Plan selections already passed their
                # resolvers. Revalidate the additive server refs and render one
                # chronological source union in an existing neutral field.
                categories = cast(dict[str, Any], original_history_payload)[
                    history.KEY
                ][self.context.target]
                key = (
                    "source_context"
                    if self.context.target in self.history.medication_fields
                    else "source_refs"
                )
                categories[key] = sorted(set(categories[key]) | additional_context)
                _, combined = self.history.resolve(
                    json.dumps(original_history_payload, ensure_ascii=False),
                    max_bytes=MAX_MODEL_BYTES,
                )
                note.update(combined)
                emitted.update(additional_context)
            note = self.context.render(
                note, context_refs, self.plan.fields, rendered_source_refs=emitted
            )
            constraints.validate(
                note,
                self.entries,
                self.plan.fields,
                refs,
                (*self.history.fields, *self.primary.fields),
                self.boundaries,
            )
            note = history.with_medication_verification_limit(
                note, plan_fields=self.plan.fields
            )
            if self.has_classified_roles:
                target = next(
                    (
                        key
                        for key, value in note.items()
                        if key
                        not in (
                            *self.plan.fields,
                            *self.history.fields,
                            *self.primary.fields,
                        )
                        and not _METADATA.fullmatch(history.normalized(key))
                        and value.strip().casefold().rstrip(".")
                        not in {"", "not mentioned", "not mentioned in transcript"}
                    ),
                    None,
                )
                if target is not None:
                    note[target] = ROLE_QUALIFICATION + "\n\n" + note[target]
            return _validated_note(json.dumps(note, ensure_ascii=False), fields)
        except (
            plans.PlanGroundingInvalid,
            history.HistoryGroundingInvalid,
            constraints.BotSourceConstraintInvalid,
            CaptureNoteUnconfirmed,
            TypeError,
            KeyError,
            ValueError,
            RecursionError,
            UnicodeError,
        ):
            raise BotNoteContractInvalid from None


def generate_grounded_note(
    transcript, example_note, note_schema, customization, *, boundaries=()
):
    from app.jobs import summarize
    from app.services.scribe_capture_notes import (
        GROUNDING,
        capture_retention_context,
        effective_capture_customization,
    )

    from app.models.note_type_customization import NoteTypeCustomization

    original = customization or NoteTypeCustomization()
    c = effective_capture_customization(
        original.model_dump(), field_names=tuple(note_schema.fields)
    )
    try:
        transcript, boundaries, mapping = passages.prepare(transcript, boundaries)
    except ValueError:
        raise BotNoteContractInvalid from None
    grounding = BotGrounding.build(
        transcript,
        note_schema.fields,
        patient_only=c.subjective_source == "patient_only",
        boundaries=boundaries,
    )
    prompt = summarize.build_structured_prompt(
        transcript,
        example_note,
        note_schema,
        c,
        action_items="",
        doctor=summarize.Doctor.one(transcript.doctor_id),
        source_indexes=True,
    )
    speech = [
        entry
        for index, entry in enumerate(transcript.transcript_data, 1)
        if index not in boundaries
    ]
    prompt += capture_retention_context(
        speech, c, field_names=tuple(note_schema.fields)
    )
    if boundaries:
        prompt += (
            "\nServer recording boundaries are not clinical speech or selectable sources: "
            + json.dumps(boundaries)
            + "\n"
        )
    prompt += constraints.prompt(
        grounding.entries, note_schema.fields, grounding.plan.fields, boundaries
    )
    prompt += grounding.plan.prompt() + grounding.history.prompt(
        has_plans=bool(grounding.plan.fields)
    )
    prompt += (
        passages.prompt(mapping)
        + grounding.primary.prompt()
        + grounding.context.prompt()
    )
    if grounding.empty_subjective:
        prompt += (
            "\nNo source has a Patient label. Keep these patient-only fields exactly empty, without labeling/preference explanations: "
            + json.dumps(grounding.empty_subjective)
            + ". Clinical source context remains in the existing neutral history fields.\n"
        )
    if grounding.combined_prospective_fields:
        prompt += (
            "\nCombined history/prospective headings use the prospective channel only: "
            + json.dumps(grounding.combined_prospective_fields)
            + ". Keep their generated strings empty and select complete actual prospective "
            "sources through plan_source_refs. Do not put patient history or inferred roles "
            "in these fields. The server labels them Prospective source context and keeps "
            "each source's original role uncertainty.\n"
        )
    response = summarize.openai.with_options(
        timeout=30.0, max_retries=0
    ).chat.completions.create(
        model=summarize.DEFAULT_MULTI_MODAL_MODEL,
        messages=[
            {"role": "system", "content": GROUNDING + BOT_CONTEXT},
            {"role": "user", "content": prompt},
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "zoom_capture_grounded_note",
                "strict": True,
                "schema": grounding.response_schema(note_schema.fields),
            },
        },
        temperature=0,
        top_p=0.1,
        max_tokens=16_384,
    )
    if not response.choices or response.choices[0].finish_reason != "stop":
        raise BotNoteContractInvalid
    return grounding.resolve(
        response.choices[0].message.content,
        note_schema.fields,
        require_plan_reason=original.require_plan_reason,
    )
