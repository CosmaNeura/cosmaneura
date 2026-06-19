"""The communication-style scoring rubric and prompt construction.

This encodes how we ask a language model to rate a patient's *observable
communication behaviour* from their text -- specifically how much decision control
they seek and how much information they engage with -- plus a worry/anxiety tone
flag. It deliberately does NOT ask the model to infer personality. The same marker
word lists drive the offline heuristic client so the fallback is directionally
consistent with the rubric.

Versioned via version.RUBRIC_VERSION.
"""

from __future__ import annotations

from typing import Dict, List

from .roles import ROLES
from .version import RUBRIC_VERSION

# Short marker word/phrase lists per indicator (high-pole cues, low-pole cues).
# Used by the heuristic client and shown to the model as orienting cues.
INDICATOR_MARKERS: Dict[str, Dict[str, List[str]]] = {
    "decision_control": {
        # high = wants to lead / decide
        "high": [
            "i want",
            "i'd prefer",
            "i've decided",
            "i think i should",
            "my choice",
            "i'd rather",
            "options",
            "alternatives",
            "i plan",
            "i'll decide",
            "second opinion",
            "i researched",
            "weigh",
            "decide for myself",
        ],
        # low = defers / wants to be told
        "low": [
            "whatever you think",
            "you decide",
            "tell me what",
            "up to you",
            "i trust you",
            "just fix",
            "what should i do",
            "your call",
            "i'll do whatever",
            "you're the doctor",
            "follow your advice",
        ],
    },
    "information_engagement": {
        # high = seeks detail/options/rationale
        "high": [
            "why",
            "how does",
            "explain",
            "what are the",
            "evidence",
            "research",
            "understand",
            "details",
            "mechanism",
            "side effects",
            "alternatives",
            "what causes",
            "studies",
            "data",
            "read more",
            "learn",
        ],
        # low = wants minimal information
        "low": [
            "just the basics",
            "keep it simple",
            "don't need details",
            "too much information",
            "short version",
            "i don't want to know",
            "spare me",
            "just tell me if",
        ],
    },
    "expressed_concern": {
        # high = worry/anxiety (tone flag only; not a mapping axis)
        "high": [
            "worried",
            "scared",
            "afraid",
            "anxious",
            "nervous",
            "terrified",
            "panic",
            "stressed",
            "frightened",
            "what if",
            "dread",
            "fear",
            "overwhelmed",
            "can't stop thinking",
        ],
        "low": [
            "fine",
            "no worries",
            "not concerned",
            "calm",
            "relaxed",
            "okay with",
            "no big deal",
            "not bothered",
        ],
    },
}

_INDICATOR_DEFINITIONS = {
    "decision_control": (
        "how much the patient wants to lead the decision vs. defer to the clinician "
        "(0 = wants the clinician to decide; 100 = wants to make the final call themselves)"
    ),
    "information_engagement": (
        "how much information, explanation, and options the patient seeks "
        "(0 = wants minimal detail; 100 = seeks evidence, mechanisms, and options)"
    ),
    "expressed_concern": (
        "how much worry or anxiety the patient expresses about their situation "
        "(0 = calm/unworried; 100 = highly worried/anxious)"
    ),
}

SYSTEM_PROMPT = (
    "You are a careful analyst that rates a patient's OBSERVABLE COMMUNICATION "
    "BEHAVIOUR from their words, to help a clinician tailor how they communicate. "
    "You are NOT assessing personality, NOT diagnosing, and NOT judging the quality "
    "of care. Rate only what the text shows about how the patient engages: how much "
    "they want to lead the decision, how much information they seek, and how much "
    "worry they express. Base every rating strictly on the supplied text. When the "
    "text is thin or ambiguous, lower your confidence rather than guessing. Be "
    "calibrated: 50 means typical/neutral; reserve extreme scores for clear evidence."
)


def _role_reference_block() -> str:
    lines = ["\nFor scale reference, the three target communication roles are:"]
    for r in ROLES.values():
        lines.append(f"- {r.display_name} (CPS '{r.cps_role}'): {r.short_label}.")
    return "\n".join(lines) + "\n"


def build_scoring_prompt(text: str) -> str:
    """Build the user prompt that asks for a strict-JSON indicator rating."""
    ind_lines = "\n".join(
        f"- {name}: {desc}" for name, desc in _INDICATOR_DEFINITIONS.items()
    )
    return f"""Rate the patient's communication behaviour from the text below.

Indicators:
{ind_lines}
{_role_reference_block()}
For EACH indicator return:
- "score": integer 0-100 (50 = typical/neutral)
- "confidence": number 0.0-1.0 (how strongly the text supports the rating)
- "evidence": a short phrase (<= 12 words) quoting or paraphrasing the cue

Rules:
- Use ONLY the text. Do not infer from demographics or assumptions.
- Lower confidence when the text is short, generic, or off-topic.
- Output ONLY a JSON object, no prose, no code fences, in exactly this shape:

{{
  "decision_control": {{"score": 0, "confidence": 0.0, "evidence": ""}},
  "information_engagement": {{"score": 0, "confidence": 0.0, "evidence": ""}},
  "expressed_concern": {{"score": 0, "confidence": 0.0, "evidence": ""}}
}}

TEXT:
\"\"\"
{text}
\"\"\"
"""


__all__ = [
    "RUBRIC_VERSION",
    "INDICATOR_MARKERS",
    "SYSTEM_PROMPT",
    "build_scoring_prompt",
]
