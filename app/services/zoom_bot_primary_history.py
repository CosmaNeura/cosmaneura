"""Source-selected primary history; the model cannot paraphrase protected facts."""

import json
from dataclasses import dataclass

from app.services import zoom_bot_source_constraints as constraints
from app.services.zoom_bot_source_rendering import render_sources

KEY = "primary_history_source_kinds"
KINDS = ("clinical_history", "care_plan", "nonclinical", "redundant_recap")


@dataclass(frozen=True, repr=False)
class PrimaryHistory:
    target: str | None
    sources: tuple
    entries: tuple
    allowed: tuple[int, ...]
    boundaries: tuple[int, ...]
    identities: tuple = ()

    @classmethod
    def build(
        cls,
        fields,
        plan_fields,
        protected_history,
        sources,
        entries,
        *,
        patient_only,
        boundaries,
        identities=(),
    ):
        required = constraints.required_context(entries, boundaries)
        target = constraints.negative_history_field(
            entries, fields, plan_fields, boundaries
        )
        if (
            target in protected_history
            or not required
            or any(entries[index - 1].speaker != "Patient" for index, _ in required)
        ):
            target = None
        roles = {"Patient"} if patient_only else {"Patient", "Doctor"}
        allowed = tuple(
            index
            for index, entry in enumerate(entries, 1)
            if index not in boundaries
            and entry.speaker in roles
            and not all(
                constraints.editing_direction(clause)
                for clause in constraints.sentences(entry.content)
            )
        )
        return cls(target, sources, entries, allowed, boundaries, identities)

    @property
    def fields(self):
        return (self.target,) if self.target else ()

    @property
    def mandatory(self):
        return tuple(
            index
            for index, _ in constraints.required_context(self.entries, self.boundaries)
        )

    @property
    def optional(self):
        return tuple(index for index in self.allowed if index not in self.mandatory)

    def prompt(self):
        if self.target is None:
            return ""
        return (
            "\nPrimary history is source-selected; this overrides the earlier "
            "instruction to copy literal words into this field yourself. The server "
            "already retains the required negative sources below; DO NOT reselect "
            "them. Set the generated string in "
            + json.dumps(self.target)
            + ' to exactly "". '
            "Add primary_history_source_kinds with exactly one property per OPTIONAL "
            "source index below. Choose its kind by reading its actual words and the "
            "whole encounter, rather than including every permitted source. "
            "clinical_history means it adds a symptom, time course, measurement, "
            "function, medication history or necessary factual correction/qualifier. "
            "care_plan means a future care decision or implementation; apply the "
            "separate Plan selection rules to it. nonclinical means standalone "
            "recording/test logistics, markers, introductions without history, "
            "bare acknowledgments or note-writing directions. redundant_recap "
            "means an already represented recap with no new clinical fact, time "
            "distinction, correction, conflict or qualifier. When a clinical fact "
            "shares an entry with nonclinical words, classify the complete entry "
            "as clinical_history if needed; never slice away qualifications. "
            "Only clinical_history entries enter this field. Kind labels are "
            "transient source-selection instructions, never verified findings "
            "or prose in the note. The server renders original words and roles; "
            "it does not validate the clinical meaning of the selection. Read "
            "the entire source, retain positive history as well as negatives, "
            "and retain both original and corrected context where necessary. "
            "Do not paraphrase these facts into other fields. Template examples "
            "guide field purpose but are not encounter evidence. Keep all other "
            "response properties.\n"
            + json.dumps(
                {
                    "primary_history_field": self.target,
                    "automatically_retained_negative_sources": self.mandatory,
                    "optional_sources_to_classify": [
                        {"source": index, "text": self.sources[index - 1].text}
                        for index in self.optional
                    ],
                    "source_kinds": KINDS,
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
        props["fields"]["properties"][self.target]["enum"] = [""]
        indexes = {
            "type": "object",
            "properties": {
                str(index): {"type": "string", "enum": list(KINDS)}
                for index in self.optional
            },
            "required": [str(index) for index in self.optional],
            "additionalProperties": False,
        }
        schema["properties"] = {
            **{key: value for key, value in props.items() if key != "fields"},
            KEY: indexes,
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
        if (
            not isinstance(result, dict)
            or not isinstance(result.get("fields"), dict)
            or result["fields"].get(self.target) != ""
        ):
            raise constraints.BotSourceConstraintInvalid
        kinds = result.pop(KEY)
        if (
            not isinstance(kinds, dict)
            or set(kinds) != {str(index) for index in self.optional}
            or any(
                type(kind) is not str or kind not in KINDS for kind in kinds.values()
            )
        ):
            raise constraints.BotSourceConstraintInvalid
        refs = tuple(
            index for index in self.optional if kinds[str(index)] == "clinical_history"
        )
        if not enveloped:
            if set(result) != {"fields"}:
                raise constraints.BotSourceConstraintInvalid
            result = result["fields"]
        return json.dumps(result, ensure_ascii=False), tuple(refs)

    def selected_refs(self, refs, plan_fields, plan_refs):
        if self.target is None:
            return ()
        planned = constraints.validated_plan_refs(
            self.entries, plan_fields, plan_refs, self.boundaries
        )
        exempt = {
            index
            for index in planned
            if constraints.prospective_negative_only(self.entries[index - 1].content)
        }
        mandatory = {
            index
            for index, _ in constraints.required_context(self.entries, self.boundaries)
        }
        # Only optional history can be deduplicated against validated Plan.
        # Mixed historical negatives retain their primary-history requirement.
        selected = sorted(((set(refs) - planned) | mandatory) - exempt)
        if any(index not in self.allowed for index in selected):
            raise constraints.BotSourceConstraintInvalid
        return tuple(selected)

    def render(self, note, refs, plan_fields, plan_refs):
        if self.target is None:
            return note
        selected = self.selected_refs(refs, plan_fields, plan_refs)
        note[self.target] = render_sources(selected, self.sources, self.identities)
        return note
