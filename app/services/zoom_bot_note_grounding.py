"""Reuse capture generation for the narrowly selected Zoom note pilot.

Eligibility and publication remain in the ordinary bot pipeline. These helpers
constrain source wording and response shape, not clinical truth or completeness.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.services import scribe_capture_history_grounding as history
from app.services import scribe_capture_plan_grounding as plans

if TYPE_CHECKING:
    from app.models.ai_visit_transcript import AIVisitTranscript
    from app.models.doctor_note_example import DoctorNoteExample
    from app.models.doctor_note_schema import DoctorNoteSchema
    from app.models.note_type_customization import NoteTypeCustomization

ROLE_AND_QUESTION_CONTRACT = (
    "\nBot source qualifications: Doctor, Patient and Other are stored model-classified "
    "roles, not independently verified identities. Preserve these labels for the "
    "saved source/section preference; never upgrade them through a spoken name, "
    "first-person wording or the signed-in account. Mark each narrative section "
    "using such speech as based on model-classified roles. Unknown speech remains "
    "unidentified in every field. A clinical question is not a finding: retain "
    "clinically relevant unanswered questions as questions in a permitted history "
    "or Objective field, explicitly saying the answer was not stated. For a "
    "multi-part question, retain the original question and exact answered portion "
    "while stating which remainder was not answered. A no-fall reply does not "
    "answer other injury. Do not move the unresolved question into Assessment "
    "or turn it into a Plan action. Keep different people's statements separate. "
    "Instructions about writing a note are not care decisions or clinical "
    "impressions. A symptom recap is still history, not an Assessment. Use concise "
    "prose for unprotected fields, retaining clinically relevant details. Source "
    "fidelity takes precedence over an example, style request or inferred reason. "
    "If a source states no reason, do not infer one. The quoted source and explicit "
    "rationale limitation in protected Plan fields implement that boundary.\n"
)
PLAN_REASON_LIMITATION = (
    "Rationale beyond what the selected source states is not established."
)
_OMIT_QUESTIONS = (
    "Omit test markers, filler, source instructions, unanswered questions and "
    "redundant recaps. A question never supplies its answer."
)
_KEEP_QUESTIONS = (
    "Omit nonclinical filler and note-construction instructions, not clinical "
    "assertions within the same complete entry. Retain clinically relevant "
    "unanswered or partially answered questions together with any stated answer, "
    "as unresolved source context, never as an affirmative or negative finding. "
    "A question never supplies its answer."
)


class BotNoteContractInvalid(ValueError):
    """Value-free terminal contract failure, excluded from task autoretry."""


def segment_boundaries(transcripts) -> tuple[int, ...]:
    """Positions introduced by summarize._merge_transcripts, not text guesses."""
    total = 0
    boundaries = []
    for index, transcript in enumerate(transcripts):
        if index:
            total += 1
            boundaries.append(total)
        total += len(transcript.transcript_data)
    return tuple(boundaries)


@dataclass(frozen=True, repr=False)
class BotGrounding:
    plan: plans.PlanGrounding
    history: history.ReportedHistoryGrounding
    boundaries: tuple[int, ...]

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
        sources = []
        for index, entry in enumerate(entries, 1):
            try:
                if not entry.content.strip() or "\x00" in entry.content:
                    raise BotNoteContractInvalid
                entry.content.encode("utf-8")
            except (AttributeError, UnicodeError):
                raise BotNoteContractInvalid from None
            if index in boundaries:
                ordinal = boundaries.index(index) + 2
                if (
                    entry.speaker != "system"
                    or entry.content != f"[Segment {ordinal} starts]"
                    or entry.startTime != 0
                    or entry.endTime != 0
                ):
                    raise BotNoteContractInvalid
                sources.append(
                    plans.PlanSource(
                        "recording_boundary", "Server recording boundary", entry.content
                    )
                )
                continue
            attribution = (
                f"Transcript — model-classified {entry.speaker} (role not independently verified)"
                if entry.speaker in {"Doctor", "Patient", "Other"}
                else "Transcript — Unidentified speaker"
            )
            sources.append(plans.PlanSource("transcript", attribution, entry.content))
        if not any(source.kind == "transcript" for source in sources):
            raise BotNoteContractInvalid
        plan = plans.PlanGrounding(plans.prospective_fields(fields), tuple(sources))
        grounding = history.ReportedHistoryGrounding.for_capture(
            tuple(fields), plan.fields, plan.sources, patient_only=patient_only
        )
        return cls(plan, grounding, boundaries)

    def history_prompt(self):
        prompt = self.history.prompt(has_plans=bool(self.plan.fields))
        if not prompt:
            return prompt
        # Capture's existing instruction stays byte-identical. Only this bot
        # request resolves its contradiction with the explicit question contract.
        if prompt.count(_OMIT_QUESTIONS) != 1:
            raise BotNoteContractInvalid
        return prompt.replace(_OMIT_QUESTIONS, _KEEP_QUESTIONS)

    def resolve(self, raw, fields, *, require_plan_reason):
        from app.services.scribe_capture_notes import (
            MAX_MODEL_BYTES,
            CaptureNoteUnconfirmed,
            _validated_note,
        )

        try:
            if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_MODEL_BYTES:
                raise BotNoteContractInvalid
            raw, grounded_history = self.history.resolve(raw, max_bytes=MAX_MODEL_BYTES)
            # The existing Plan resolver accepts whole sources of any kind. A
            # server merge boundary is structurally known not to be speech.
            if self.plan.fields and self.boundaries:
                refs = json.loads(raw)["plan_source_refs"]
                if any(
                    index in self.boundaries
                    for values in refs.values()
                    for index in values
                ):
                    raise BotNoteContractInvalid
            raw_fields, grounded_plans = self.plan.resolve(
                raw, max_bytes=MAX_MODEL_BYTES
            )
            note = _validated_note(raw_fields, fields)
            if require_plan_reason:
                grounded_plans = {
                    key: value + "\n\n" + PLAN_REASON_LIMITATION
                    for key, value in grounded_plans.items()
                }
            note.update(grounded_plans)
            note.update(grounded_history)
            return _validated_note(json.dumps(note, ensure_ascii=False), fields)
        except (
            plans.PlanGroundingInvalid,
            history.HistoryGroundingInvalid,
            CaptureNoteUnconfirmed,
            TypeError,
            KeyError,
            AttributeError,
            ValueError,
            RecursionError,
            UnicodeError,
        ):
            raise BotNoteContractInvalid from None


def generate_grounded_note(
    transcript: AIVisitTranscript,
    example_note: DoctorNoteExample | None,
    note_schema: DoctorNoteSchema,
    customization: NoteTypeCustomization | None,
    *,
    boundaries: tuple[int, ...] = (),
) -> dict[str, str]:
    from app.jobs import summarize
    from app.services.scribe_capture_notes import (
        GROUNDING,
        capture_response_schema,
        capture_retention_context,
    )

    from app.models.note_type_customization import NoteTypeCustomization

    c = customization or NoteTypeCustomization()
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
        source_indexes=bool(grounding.plan.fields or grounding.history.fields),
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
            "\nServer-inserted recording boundaries at source indexes "
            + json.dumps(boundaries)
            + " preserve sequence but are not clinical speech or plan/history sources.\n"
        )
    prompt += grounding.plan.prompt() + grounding.history_prompt()
    response = summarize.openai.chat.completions.create(
        model=summarize.DEFAULT_MULTI_MODAL_MODEL,
        messages=[
            {"role": "system", "content": GROUNDING + ROLE_AND_QUESTION_CONTRACT},
            {"role": "user", "content": prompt},
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "zoom_capture_grounded_note",
                "strict": True,
                "schema": capture_response_schema(
                    tuple(note_schema.fields), grounding.plan.fields, grounding.history
                ),
            },
        },
        temperature=0,
        top_p=0.1,
        max_tokens=16_384,
    )
    if not response.choices or response.choices[0].finish_reason != "stop":
        raise BotNoteContractInvalid
    raw = response.choices[0].message.content
    if not isinstance(raw, str):
        raise BotNoteContractInvalid
    return grounding.resolve(
        raw, note_schema.fields, require_plan_reason=c.require_plan_reason
    )
