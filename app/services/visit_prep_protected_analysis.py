"""Core de-identification for current Visit Prep analysis, never patient entry.

The one bounded provider request contains only selected text. Local framing
keeps prompts and answers distinct without sending record IDs, contacts,
timestamps, credentials or retained originals. No raw-input fallback exists.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field, replace
from difflib import SequenceMatcher
from time import monotonic
from uuid import uuid4

from app.environments import should_use_test_openai_fallback
from app.services.visit_prep_synthesis_contract import (
    MAX_SOURCE_CHARACTERS,
    VisitPrepPreparedBrief,
    VisitPrepSynthesisInvalid,
    VisitPrepSynthesisSelection,
    VisitPrepSynthesisSource,
    validate_generated_brief,
    validate_selection,
)

PROTECTION_PROFILE = "core_azure_deid_surrogate_2024_11_15_v1"
PROTECTION_TIMEOUT = 12.0
_WORDS = re.compile(r"\w+(?:['’\-]\w+)*", re.UNICODE)


def deidentify(text: str, *, timeout_seconds: float) -> str:
    # Core registers all jobs when app.jobs is imported. Resolve its existing
    # helper only at execution time, after service/job bootstrap is complete.
    from app.jobs.anonymize import deidentify as core_deidentify

    return core_deidentify(text, timeout_seconds=timeout_seconds)


class VisitPrepProtectionFailed(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Visit Prep protected analysis is unavailable")


@dataclass(frozen=True)
class VisitPrepProtectedAnalysis:
    profile: str
    selection: VisitPrepSynthesisSelection = field(repr=False)


def validate_protected_analysis(
    protected: VisitPrepProtectedAnalysis, original: VisitPrepSynthesisSelection
) -> None:
    """Protected text cannot change source identity, ordering or answer status."""
    validate_selection(original)
    if not isinstance(protected, VisitPrepProtectedAnalysis):
        raise VisitPrepSynthesisInvalid()
    selected = protected.selection
    if not isinstance(selected, VisitPrepSynthesisSelection) or any(
        not isinstance(source, VisitPrepSynthesisSource)
        or any(
            not isinstance(text, str)
            or any(
                character != "\n" and unicodedata.category(character).startswith("C")
                for character in text
            )
            for text in (source.prompt, source.reported_text)
        )
        for source in selected.sources
    ):
        raise VisitPrepSynthesisInvalid()
    validate_selection(selected)
    if protected.profile != PROTECTION_PROFILE or len(selected.sources) != len(
        original.sources
    ):
        raise VisitPrepSynthesisInvalid()
    restored = []
    for current, source in zip(selected.sources, original.sources, strict=True):
        if source.kind == "choice" and current.answer != source.answer:
            raise VisitPrepSynthesisInvalid()
        restored.append(
            replace(
                current,
                prompt=source.prompt,
                answer=source.answer,
                choice_label=source.choice_label,
            )
        )
    if replace(selected, sources=tuple(restored)) != original:
        raise VisitPrepSynthesisInvalid()


def validate_protected_brief(
    prepared: VisitPrepPreparedBrief,
    protected: VisitPrepProtectedAnalysis,
    original: VisitPrepSynthesisSelection,
) -> None:
    """No substituted value becomes a quotation or a narrative patient fact.

    Dual grounding protects quotes and numbers. Compare changed word spans as
    well: an unchanged citation cannot justify a surrogate name/date elsewhere
    in the model's narrative. Conservative rejection does not re-identify text
    or assert semantic clinical validity.
    """
    validate_protected_analysis(protected, original)
    validate_generated_brief(prepared, protected.selection)
    validate_generated_brief(prepared, original)
    changed = set()
    for current, source in zip(
        protected.selection.sources, original.sources, strict=True
    ):
        for before, after in (
            (source.prompt, current.prompt),
            (source.reported_text, current.reported_text),
        ):
            old = _WORDS.findall(before.casefold())
            new = _WORDS.findall(after.casefold())
            for tag, _, _, start, end in SequenceMatcher(
                a=old, b=new, autojunk=False
            ).get_opcodes():
                if tag != "equal" and start < end:
                    # A partial name/date is still substituted data. Reject
                    # each changed word, even if the model omits its neighbors.
                    changed.update((word,) for word in new[start:end])
    for item in prepared.items():
        for text in (item.text, *(evidence.quote for evidence in item.evidence)):
            words = _WORDS.findall(text.casefold())
            if any(
                tuple(words[index : index + len(span)]) == span
                for span in changed
                for index in range(len(words) - len(span) + 1)
            ):
                raise VisitPrepSynthesisInvalid()


def protect_patient_analysis(
    selection: VisitPrepSynthesisSelection,
) -> VisitPrepProtectedAnalysis:
    validate_selection(selection)
    if not selection.sources:
        return VisitPrepProtectedAnalysis(PROTECTION_PROFILE, selection)
    # Keep the live provider disabled in tests even if a model is stubbed.
    if should_use_test_openai_fallback():
        raise VisitPrepProtectionFailed()
    texts = [
        text
        for source in selection.sources
        for text in (source.prompt, source.reported_text)
    ]
    marker = f"VP_BOUNDARY_{uuid4().hex}_"
    frames = [(f"{marker}{i}_BEGIN\n", f"\n{marker}{i}_END") for i in range(len(texts))]
    body = "\n".join(
        start + text + end for (start, end), text in zip(frames, texts, strict=True)
    )
    if len(body) > MAX_SOURCE_CHARACTERS:
        raise VisitPrepProtectionFailed()
    started = monotonic()
    try:
        result = deidentify(body, timeout_seconds=PROTECTION_TIMEOUT)
        if (
            monotonic() - started >= PROTECTION_TIMEOUT
            or not isinstance(result, str)
            or len(result) > MAX_SOURCE_CHARACTERS
        ):
            raise VisitPrepProtectionFailed()
        remaining = result
        values = []
        for index, (start, end) in enumerate(frames):
            if not remaining.startswith(start):
                raise VisitPrepProtectionFailed()
            value, separator, remaining = remaining[len(start) :].partition(end)
            if not separator or marker in value:
                raise VisitPrepProtectionFailed()
            values.append(value)
            if index < len(frames) - 1:
                if not remaining.startswith("\n"):
                    raise VisitPrepProtectionFailed()
                remaining = remaining[1:]
        if remaining:
            raise VisitPrepProtectionFailed()
        sources = tuple(
            replace(
                source,
                prompt=values[index * 2],
                answer=source.answer
                if source.kind == "choice"
                else values[index * 2 + 1],
                choice_label=values[index * 2 + 1] if source.kind == "choice" else None,
            )
            for index, source in enumerate(selection.sources)
        )
        protected = VisitPrepProtectedAnalysis(
            PROTECTION_PROFILE, replace(selection, sources=sources)
        )
        validate_protected_analysis(protected, selection)
        return protected
    except Exception:
        # Neither SDK exceptions nor validation input can reach job stores/logs.
        raise VisitPrepProtectionFailed() from None
