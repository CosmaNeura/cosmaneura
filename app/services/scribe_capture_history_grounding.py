"""Source-bound history for captures with unidentified speech and patient-only S.

The model selects relevant complete entries, not the words stored in grounded
fields. This does not prove selection completeness or clinical classification.
Saved notes, known-role captures, and prospective PlanGrounding are unchanged.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass

from app.services.scribe_capture_plan_grounding import PlanSource

KEY = "reported_history_source_refs"
CONTRACT_START = "\n<reported_history_contract>\n"
CONTRACT_END = "\n</reported_history_contract>\n"
REVIEW_REQUIRED = "No history source entries selected; review the transcript before completing this section."
_HISTORY = re.compile(
    r"\b(?:objective|history|histories|ros|pmh|psh|medications?|medicines?|"
    r"allergies|immunizations?)\b|\b(?:family|social) hx\b|"
    r"\breview of systems\b|\bsystems review\b"
)
_EXCLUDED = re.compile(
    r"\b(?:subjective|assessment|attestation|signature|coding|provider|payer|"
    r"patient|date|dob|mrn|consent)\b"
)
_MEDICATION = re.compile(r"\b(?:medications?|medicines?)\b")


class HistoryGroundingInvalid(ValueError):
    """Value-free failure: an invalid selection must not reach saved fields."""


def normalized(heading: str) -> str:
    return re.sub(r"[\W_]+", " ", unicodedata.normalize("NFKC", heading).casefold())


@dataclass(frozen=True, repr=False)
class ReportedHistoryGrounding:
    fields: tuple[str, ...]
    medication_fields: tuple[str, ...]
    sources: tuple[PlanSource, ...]

    @classmethod
    def for_capture(cls, fields, plan_fields, sources, *, patient_only: bool):
        speech = [source for source in sources if source.kind == "transcript"]
        enabled = (
            patient_only
            and bool(speech)
            and all(
                source.attribution == "Transcript — Unidentified speaker"
                for source in speech
            )
        )
        selected = tuple(
            heading
            for heading in fields
            if enabled
            and heading not in plan_fields
            and _HISTORY.search(normalized(heading))
            and not _EXCLUDED.search(normalized(heading))
        )
        medications = tuple(h for h in selected if _MEDICATION.search(normalized(h)))
        return cls(selected, medications, sources)

    def schema(self):
        indexes = {"type": "array", "items": {"type": "integer"}}
        return {
            "type": "object",
            "properties": {
                field: (
                    {
                        "type": "object",
                        "properties": {
                            "current_list": indexes,
                            "change_history": indexes,
                        },
                        "required": ["current_list", "change_history"],
                        "additionalProperties": False,
                    }
                    if field in self.medication_fields
                    else indexes
                )
                for field in self.fields
            },
            "required": list(self.fields),
            "additionalProperties": False,
        }

    def prompt(self, *, has_plans: bool) -> str:
        if not self.fields:
            return ""
        keys = ["fields", *(["plan_source_refs"] if has_plans else []), KEY]
        return (
            "\nFinal capture response format: the complete JSON object has exactly "
            + json.dumps(keys)
            + ". Keep the existing prospective-plan selection rules unchanged. "
            "The added reported_history_source_refs object uses only the listed "
            "existing history fields. All transcript speaker roles are unidentified; "
            "the saved patient-only Subjective preference still applies. For these "
            "history fields select complete source indexes instead of composing "
            "prose. Their generated fields strings may be empty: the server replaces "
            "them with the exact selected source text and original source attribution. "
            "Select clinically relevant declarative history, symptoms, measurements, "
            "functional status and explicit negatives throughout the entire source. "
            "Include the entries needed to preserve corrections, unresolved conflicts, "
            "and prior/current time distinctions. Do not select only a late recap. "
            "Choose the appropriate existing field for each report; do not dump the "
            "whole transcript into Objective. Omit test markers, filler, source "
            "instructions, unanswered questions and redundant recaps. A question "
            "never supplies its answer. Keep different people's reports distinct. "
            "When using Objective this is reported transcript history, never an "
            "examination you performed. Saved scratch notes are a separate typed "
            "source, not unidentified speech; retain their distinct attribution. "
            "An empty array means no source was selected and will display a review "
            "limitation, not normal findings or proven absence. "
            "For medication fields, current_list selects actual current medication "
            "reports and change_history selects retrospective started/stopped/changed "
            "history. A future decision is not retrospective history or a current "
            "list. An unchanged-treatment statement cannot establish a current list. "
            "If one complete entry contains both list and history, select it once "
            "under current_list. Do not reuse it under change_history. "
            "Do not paraphrase these selected facts elsewhere in the note. "
            "Source indexes appear only in the structured reference properties.\n"
            + CONTRACT_START
            + json.dumps(
                {
                    "reported_history_fields": self.fields,
                    "medication_fields": self.medication_fields,
                    "source_count": len(self.sources),
                }
            )
            + CONTRACT_END
        )

    def _indexes(self, value):
        if (
            not isinstance(value, list)
            or any(
                type(index) is not int or not 1 <= index <= len(self.sources)
                for index in value
            )
            or len(set(value)) != len(value)
        ):
            raise HistoryGroundingInvalid
        for index in value:
            source = self.sources[index - 1]
            if not (
                (
                    source.kind == "transcript"
                    and source.attribution == "Transcript — Unidentified speaker"
                )
                or source.kind == "clinician_scratch_notes"
            ):
                raise HistoryGroundingInvalid
        return sorted(value)

    def _render(self, indexes):
        return "\n\n".join(
            f"{self.sources[index - 1].attribution}:\n{self.sources[index - 1].text}"
            for index in indexes
        )

    def resolve(self, raw: str, *, max_bytes: int) -> tuple[str, dict[str, str]]:
        if not self.fields:
            return raw, {}

        def closed(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise HistoryGroundingInvalid
                result[key] = value
            return result

        try:
            if len(raw.encode("utf-8")) > max_bytes:
                raise HistoryGroundingInvalid
            result = json.loads(raw, object_pairs_hook=closed)
            if not isinstance(result, dict) or KEY not in result:
                raise HistoryGroundingInvalid
            refs = result.pop(KEY)
            if set(result) not in ({"fields"}, {"fields", "plan_source_refs"}):
                raise HistoryGroundingInvalid
            if not isinstance(refs, dict) or set(refs) != set(self.fields):
                raise HistoryGroundingInvalid
            resolved = {}
            for heading in self.fields:
                if heading in self.medication_fields:
                    value = refs[heading]
                    if not isinstance(value, dict) or set(value) != {
                        "current_list",
                        "change_history",
                    }:
                        raise HistoryGroundingInvalid
                    current = self._indexes(value["current_list"])
                    changes = self._indexes(value["change_history"])
                    if set(current) & set(changes):
                        raise HistoryGroundingInvalid
                    # The model's category choice does not establish a current
                    # patient list when recorded speaker roles are unidentified.
                    # Retain named drugs and all temporal statements without
                    # presenting that untrusted classification as a clinical fact.
                    resolved[heading] = (
                        "Current medication list is unverified; review the source statements."
                    )
                    selected = sorted(current + changes)
                    if selected:
                        resolved[heading] += (
                            "\n\nSelected medication source statements:\n"
                            + self._render(selected)
                        )
                else:
                    indexes = self._indexes(refs[heading])
                    resolved[heading] = (
                        "Reported transcript history:\n" + self._render(indexes)
                        if indexes
                        else REVIEW_REQUIRED
                    )
            # Leave the ordinary flat/plan envelope contract to its existing
            # validator; this helper removes only its own extra property.
            remaining = result if "plan_source_refs" in result else result["fields"]
            return json.dumps(remaining, ensure_ascii=False), resolved
        except (ValueError, TypeError, KeyError, RecursionError):
            raise HistoryGroundingInvalid from None
