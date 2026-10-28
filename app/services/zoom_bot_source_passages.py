"""Exact text passages for unusually coarse bot turns, never acoustic speakers.

Only the ephemeral note prompt is subdivided. Persisted transcript text, timing,
source identities and authorization snapshots are untouched. Offsets map every
passage back to its original entry; no model supplies the passage boundaries.
"""

import json
import re
from dataclasses import dataclass

MAX_UNSPLIT_WORDS = 120
_END = re.compile(r"[.!?][\"')\]]*\s+")
_QUALIFIER = re.compile(
    r"(?:but|however|actually|instead|except|rather|not|earlier|correction|unless|otherwise)\b"
    r"|(?:only|even) if\b|(?:that|this) (?:was|is|would)\b"
    r"|i (?:initially|first said|almost said|would correct)\b",
    re.I,
)


@dataclass(frozen=True)
class Passage:
    original_source: int
    start: int
    end: int


def spans(text):
    if len(text.split()) <= MAX_UNSPLIT_WORDS:
        return ((0, len(text)),)
    edges = [0]
    for match in _END.finditer(text):
        prefix = text[: match.start()]
        token = re.search(r"[\w.]+$", prefix)
        # Keep initials, titles, short abbreviations and isolated 'No.' attached.
        if text[match.start()] == "." and token and len(token.group().strip(".")) <= 3:
            continue
        if _QUALIFIER.match(text[match.end() :]):
            continue
        edges.append(match.end())
    edges.append(len(text))
    result = []
    for start, end in zip(edges, edges[1:]):
        while start < end and text[start].isspace():
            start += 1
        while end > start and text[end - 1].isspace():
            end -= 1
        if start < end:
            result.append((start, end))
    # With no subdivision the prompt retains the original entry, including its
    # outer whitespace, so its audit offsets must describe that exact entry.
    return tuple(result) if len(result) > 1 else ((0, len(text)),)


def prepare(transcript, boundaries=()):
    """Return a prompt-only copy, remapped server boundaries and exact offsets."""
    if (
        not isinstance(boundaries, tuple)
        or any(
            type(index) is not int or not 1 <= index <= len(transcript.transcript_data)
            for index in boundaries
        )
        or tuple(sorted(set(boundaries))) != boundaries
    ):
        raise ValueError("invalid_source_boundaries")
    units, mapping, new_boundaries = [], [], []
    changed = False
    for index, entry in enumerate(transcript.transcript_data, 1):
        ranges = (
            ((0, len(entry.content)),) if index in boundaries else spans(entry.content)
        )
        changed = changed or len(ranges) != 1
        if index in boundaries:
            new_boundaries.append(len(units) + 1)
        for start, end in ranges:
            units.append(
                entry.model_copy(update={"content": entry.content[start:end]})
                if len(ranges) != 1
                else entry
            )
            mapping.append(Passage(index, start, end))
            if len(units) > 2000:
                raise ValueError("source_passage_limit")
    if not changed:
        return transcript, boundaries, tuple(mapping)
    return (
        transcript.model_copy(update={"transcript_data": units}),
        tuple(new_boundaries),
        tuple(mapping),
    )


def prompt(mapping):
    if all(part.original_source == index for index, part in enumerate(mapping, 1)):
        return ""
    return (
        "\nSome unusually coarse provider turns are presented as exact text passages "
        "for note selection. They are NOT separate acoustic voices or verified "
        "clinical utterances. Every passage retains the same original role label; "
        "adjacent passages remain one continuous source context. Read all adjacent "
        "qualifiers, negations and corrections before selecting a care decision. "
        "Do not infer a new identity from a passage boundary. References now select "
        "complete exact passages, not whole provider turns. Do not select a passage "
        "while omitting another passage that negates, cancels or corrects it. "
        "The following map gives original source entry and character offsets, "
        "not new times or identities.\n"
        + json.dumps(
            [
                {
                    "source": index,
                    "original_entry": part.original_source,
                    "start": part.start,
                    "end": part.end,
                }
                for index, part in enumerate(mapping, 1)
            ]
        )
        + "\n"
    )
