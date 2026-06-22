"""Unit tests for transcribe.label_speakers — the shared Doctor/Patient speaker
normalization used by both the audio-upload path and the Zoom/Recall path."""

import pytest

import app.jobs.transcribe as transcribe
from app.jobs.transcribe import label_speakers

from app.models.ai_visit_transcript import TranscriptEntry


def _entry(speaker: str, content: str, start: int = 0, end: int = 1) -> TranscriptEntry:
    return TranscriptEntry(
        speaker=speaker, content=content, startTime=start, endTime=end
    )


def _patch_identification(
    monkeypatch: pytest.MonkeyPatch, doctor_synthetic: str | None
):
    # identify_* returns free-text reasoning (ignored); extract_* returns the
    # synthetic A/B label the relabeling keys on. Patch both so no LLM is hit.
    monkeypatch.setattr(transcribe, "identify_doctor_from_transcript", lambda _s: "ok")
    monkeypatch.setattr(
        transcribe, "extract_doctor_classification", lambda _r: doctor_synthetic
    )


def test_relabels_recall_participant_names(monkeypatch: pytest.MonkeyPatch):
    # Zoom/Recall arrives with participant display names. First-seen label -> "A".
    _patch_identification(monkeypatch, "A")  # doctor is the first-appearing speaker
    entries = [
        _entry("Dev Cosmaneura", "What brings you in today?"),
        _entry("Oscar Ortega", "I want to understand my options before deciding."),
        _entry("Dev Cosmaneura", "Sure, let's go through them."),
    ]

    result = label_speakers(entries)

    assert [e.speaker for e in result] == ["Doctor", "Patient", "Doctor"]
    # content/timing preserved
    assert result[1].content == "I want to understand my options before deciding."


def test_patient_turns_are_recoverable_for_scoring(monkeypatch: pytest.MonkeyPatch):
    _patch_identification(monkeypatch, "A")
    entries = [
        _entry("Dev Cosmaneura", "How have you been?"),
        _entry("Oscar Ortega", "Honestly not great, I have questions."),
    ]

    result = label_speakers(entries)

    # reconstruct_patient_text keys on speaker.lower() == "patient"
    patient = [e for e in result if e.speaker.lower() == "patient"]
    assert len(patient) == 1
    assert patient[0].content == "Honestly not great, I have questions."


def test_assemblyai_ab_labels_preserved(monkeypatch: pytest.MonkeyPatch):
    # Audio-upload path already uses A/B; the mapping must still resolve correctly.
    _patch_identification(monkeypatch, "B")  # doctor is the second-appearing label
    entries = [
        _entry("A", "I've done some reading on this."),
        _entry("B", "Good. Here's what the evidence shows."),
    ]

    result = label_speakers(entries)

    assert [e.speaker for e in result] == ["Patient", "Doctor"]


def test_empty_returns_empty(monkeypatch: pytest.MonkeyPatch):
    _patch_identification(monkeypatch, "A")
    assert label_speakers([]) == []


def test_raises_when_identification_fails(monkeypatch: pytest.MonkeyPatch):
    _patch_identification(monkeypatch, None)
    with pytest.raises(ValueError):
        label_speakers([_entry("A", "hi"), _entry("B", "hello")])
