"""Engagement scoring service: transcript text -> communication-role ScoreResult.

Thin orchestration layer between cosmaneura and the vendored engine. Keeps the
text-reconstruction policy (patient turns only = the *enacted* read) and the engine
wiring in one place so the Celery job stays small and testable.
"""

from __future__ import annotations

from typing import Any, Iterable, Optional, cast

from app.engagement.azure_client import AzureOpenAILLMClient
from app.engagement.cosmas_engagement.calibration.baseline import (
    RoleBaseline,
    default_baseline,
)
from app.engagement.cosmas_engagement.engine import EngagementEngine
from app.engagement.cosmas_engagement.llm_client import LLMClient
from app.engagement.cosmas_engagement.schemas import ScoreRequest, ScoreResult

# Minimum patient words below which scoring is not worthwhile (returns None).
MIN_PATIENT_WORDS = 5

# R3 recalibration: minimum scored encounters for a doctor before we trust a
# per-practice baseline. Below this, we score on the neutral (absolute) scale.
MIN_BASELINE_SAMPLES = 30


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


def reconstruct_intake_text(intake_questions: Iterable[Any]) -> str:
    """Join the patient's intake ANSWERS -- their pre-visit, first-person words.

    The engine reads this as the *preferred* communication role (least contaminated
    by the encounter). Questions are AI-generated, so only answers are kept.
    """
    lines: list[str] = []
    for q in intake_questions or []:
        answer = _entry_field(q, "answer").strip()
        if answer:
            lines.append(answer)
    return "\n".join(lines)


def build_doctor_baseline(
    doctor_id: Any, *, min_samples: int = MIN_BASELINE_SAMPLES
) -> RoleBaseline:
    """Build a per-practice (per-doctor) recalibration baseline (R3).

    Standardizes the enacted read against this doctor's own population of prior
    scored encounters, countering range restriction (a single practice's patients
    bunch into a narrow band). Falls back to the neutral baseline until enough
    encounters have accrued, so early scores degrade gracefully to absolute scoring.
    """
    # Imported lazily to avoid a model import at module load.
    from app.models.engagement_role import EngagementRole

    rows = cast(
        list[EngagementRole],
        list(
            EngagementRole.where(
                EngagementRole.doctor_id == doctor_id,
                EngagementRole.deleted_at == None,  # noqa: E711 (SQL IS NULL)
            ).all()
        ),
    )
    if len(rows) < min_samples:
        return default_baseline()

    return RoleBaseline.from_samples(
        {
            "decision_control": [r.decision_control for r in rows],
            "information_engagement": [r.information_engagement for r in rows],
        },
        source=f"doctor:{doctor_id} (n={len(rows)})",
    )


def score_patient_text(
    patient_text: str,
    *,
    intake_text: str | None = None,
    patient_ref: str | None = None,
    client: Optional[LLMClient] = None,
    enacted_baseline: Optional[RoleBaseline] = None,
) -> ScoreResult | None:
    """Score reconstructed patient text into a communication role.

    Returns None when there is too little patient speech to be meaningful.
    ``client`` defaults to the Azure OpenAI adapter; tests inject the offline
    heuristic client instead. ``enacted_baseline`` recalibrates the enacted read
    against the practice population (R3); defaults to neutral. When ``intake_text``
    is present the engine also produces the *preferred* read and the
    preference-enactment gap.
    """
    if len(patient_text.split()) < MIN_PATIENT_WORDS:
        return None
    engine = EngagementEngine(
        client=client or AzureOpenAILLMClient(),
        enacted_baseline=enacted_baseline,
    )
    intake = intake_text if (intake_text and intake_text.strip()) else None
    return engine.score(
        ScoreRequest(
            visit_text=patient_text,
            intake_text=intake,
            patient_ref=patient_ref,
        )
    )


__all__ = [
    "MIN_PATIENT_WORDS",
    "MIN_BASELINE_SAMPLES",
    "reconstruct_patient_text",
    "reconstruct_intake_text",
    "build_doctor_baseline",
    "score_patient_text",
]
