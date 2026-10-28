"""Prompt-only passage extraction cannot rewrite persisted source evidence."""

import json
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel

from app.services import zoom_bot_note_grounding as bot
from app.services import zoom_bot_source_passages as passages

from app.models.ai_visit_transcript import TranscriptEntry

from tests.job.zoom_bot_test_support import SOAP, response

ROOT = Path(__file__).resolve().parents[2]


class PromptTranscript(BaseModel):
    # Pure passage tests do not depend on ORM mapper initialization order. The
    # separate PostgreSQL perform test checks the actual persisted model.
    doctor_id: str
    transcript_data: list[TranscriptEntry]


def source_for(text):
    return PromptTranscript(
        doctor_id="synthetic",
        transcript_data=[
            TranscriptEntry(
                speaker="Unverified speaker (raw A)",
                content=text,
                startTime=123,
                endTime=9000,
                source_speaker_id=UUID(int=1),
            )
        ],
    )


def test_unsplit_long_turn_keeps_exact_outer_whitespace_offsets():
    text = "  " + " ".join(["unpunctuated context"] * 70) + " \n"
    assert passages.spans(text) == ((0, len(text)),)
    source = source_for(text)
    prepared, _, mapping = passages.prepare(source)
    assert prepared is source
    assert (
        prepared.transcript_data[0].content == text[mapping[0].start : mapping[0].end]
    )


def test_passages_preserve_text_offsets_identity_original_times_and_source():
    text = (
        " ".join(["A long neutral contextual sentence."] * 30)
        + " Dr. K. said we will stop lifting. Actually, that instruction is cancelled."
    )
    source = source_for(text)
    before = source.model_dump()
    prepared, boundaries, mapping = passages.prepare(source)
    assert len(prepared.transcript_data) > 1 and not boundaries
    assert source.model_dump() == before
    original = source.transcript_data[0]
    for entry, mapped in zip(prepared.transcript_data, mapping):
        assert entry.content == text[mapped.start : mapped.end]
        assert mapped.original_source == 1
        assert entry.model_dump(exclude={"content"}) == original.model_dump(
            exclude={"content"}
        )
    assert " ".join(entry.content for entry in prepared.transcript_data) == text
    assert prepared.transcript_data[-1].content.endswith(
        "Dr. K. said we will stop lifting. Actually, that instruction is cancelled."
    )
    assert "NOT separate acoustic voices" in passages.prompt(mapping)


def test_short_entries_keep_existing_indexes_and_boundary_remaps_after_split():
    source = source_for(" ".join(["A long neutral contextual sentence."] * 30))
    source.transcript_data.extend(
        [
            TranscriptEntry(
                speaker="system", content="[Segment 2 starts]", startTime=0, endTime=0
            ),
            TranscriptEntry(
                speaker="Unverified speaker (raw B)",
                content="Return next week.",
                startTime=11,
                endTime=12,
            ),
        ]
    )
    prepared, boundaries, mapping = passages.prepare(source, (2,))
    assert boundaries == (len(prepared.transcript_data) - 1,)
    assert prepared.transcript_data[-1] is source.transcript_data[-1]
    assert mapping[-1].original_source == 3
    bot.BotGrounding.build(prepared, SOAP, patient_only=True, boundaries=boundaries)


def test_actual_r7_shape_can_select_plan_without_entire_392_word_transcript():
    fixture = json.loads(
        (
            ROOT / "tools/zoom-note-fidelity/r7-provider-input-equivalent.json"
        ).read_text()
    )
    entries = [TranscriptEntry(**entry) for entry in fixture["candidateEntries"]]
    source = PromptTranscript(doctor_id="synthetic", transcript_data=entries)
    assert len(entries) == 1 and len(entries[0].content.split()) == 392
    before = source.model_dump()
    prepared, boundaries, mapping = passages.prepare(source)
    g = bot.BotGrounding.build(prepared, SOAP, patient_only=True, boundaries=boundaries)
    selected = [
        i
        for i, entry in enumerate(prepared.transcript_data, 1)
        if "Follow up should be in 10 days, not 10 weeks." in entry.content
    ]
    assert len(selected) == 1
    # Handcrafted selection proves mechanics only; this is not a model result.
    note = g.resolve(
        response(
            g,
            plans=selected,
            histories=list(range(1, len(prepared.transcript_data) + 1)),
        ),
        SOAP,
        require_plan_reason=False,
    )
    assert len(note["Plan"].split()) < 45
    assert "10 days, not 10 weeks" in note["Plan"]
    assert "orange telescope" not in note["Plan"]
    assert note["Subjective"] == ""
    assert all(
        entry.speaker == entries[0].speaker for entry in prepared.transcript_data
    )
    assert "11872, not 18072" in note["Objective"]
    assert source.model_dump() == before
    assert all(item.original_source == 1 for item in mapping)
