"""Engagement scoring service: transcript text -> communication-role ScoreResult.

Thin orchestration layer between cosmaneura and the vendored engine. Keeps the
text-reconstruction policy (patient turns only = the *enacted* read) and the engine
wiring in one place so the Celery job stays small and testable.
"""

from __future__ import annotations

from typing import Any, Iterable, Optional

from app.engagement.azure_client import AzureOpenAILLMClient
from app.engagement.cosmas_engagement.engine import EngagementEngine
from app.engagement.cosmas_engagement.llm_client import LLMClient
from app.engagement.cosmas_engagement.schemas import ScoreRequest, ScoreResult

# Minimum patient words below which scoring is not worthwhile (returns None).
MIN_PATIENT_WORDS = 5


def _entry_field(entry: Any, field: str) -> str:
    """Read a field from a TranscriptEntry whether it's a pydantic model or a dict."""
    if isinstance(entry, dict):
        return str(entry.get(field) or "")
    return str(getattr(entry, field, "") or "")


def reconstruct_patient_text(transcript_data: Iterable[Any]) -> str:
    """Join only the patient's turns -- the enacted, in-encounter voice.

    Doctor/system turns are dropped: they dilute the patient's own communication
    signal (the phase-0 analysis fed mixed voices and got a flatter read).
    """
    lines: list[str] = []
    for entry in transcript_data or []:
        speaker = _entry_field(entry, "speaker").strip()
        content = _entry_field(entry, "content").strip()
        if content and speaker.lower() == "patient":
            lines.append(content)
    return "\n".join(lines)


def score_patient_text(
    patient_text: str,
    *,
    patient_ref: str | None = None,
    client: Optional[LLMClient] = None,
) -> ScoreResult | None:
    """Score reconstructed patient text into a communication role.

    Returns None when there is too little patient speech to be meaningful.
    ``client`` defaults to the Azure OpenAI adapter; tests inject the offline
    heuristic client instead.
    """
    if len(patient_text.split()) < MIN_PATIENT_WORDS:
        return None
    engine = EngagementEngine(client=client or AzureOpenAILLMClient())
    # v1 staging: visit transcript only -> the enacted read, neutral baseline.
    return engine.score(ScoreRequest(visit_text=patient_text, patient_ref=patient_ref))


__all__ = [
    "MIN_PATIENT_WORDS",
    "reconstruct_patient_text",
    "score_patient_text",
]
