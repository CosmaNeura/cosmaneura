"""Turn raw patient text into a normalized communication-indicator estimate.

Pure, network-free, testable pieces: ``build_prompt`` (delegates to the rubric),
``parse_response`` (robust JSON extraction), and ``normalize_indicators`` (clamp +
validate). ``estimate_indicators`` wires them to an LLM client.
"""

from __future__ import annotations

import json
import re
from typing import Dict

from .llm_client import LLMClient
from .roles import INDICATOR_ORDER
from .rubric import SYSTEM_PROMPT, build_scoring_prompt

# Cap per-source text length to bound cost/latency. Long transcripts are truncated
# (keeping the head, where the patient's voice/agenda is usually established).
MAX_TEXT_CHARS = 12_000

_JSON_OBJ_RE = re.compile(r"\{.*\}", re.DOTALL)


class ScoringError(RuntimeError):
    """Raised when the model response cannot be parsed into an indicator estimate."""


def build_prompt(text: str) -> str:
    return build_scoring_prompt(text)


def parse_response(raw: str) -> Dict[str, dict]:
    """Extract the JSON object from a raw model completion."""
    if not raw or not raw.strip():
        raise ScoringError("Empty response from model.")
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    match = _JSON_OBJ_RE.search(cleaned)
    if not match:
        raise ScoringError(f"No JSON object found in model response: {raw[:200]!r}")
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise ScoringError(f"Malformed JSON in model response: {exc}") from exc


def _clamp(value, lo, hi, default):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    if v != v:  # NaN
        return default
    return max(lo, min(hi, v))


def normalize_indicators(parsed: Dict[str, dict]) -> Dict[str, dict]:
    """Validate presence of all indicators and clamp values to valid ranges."""
    missing = [i for i in INDICATOR_ORDER if i not in parsed]
    if missing:
        raise ScoringError(f"Model response missing indicators: {missing}")

    out: Dict[str, dict] = {}
    for ind in INDICATOR_ORDER:
        entry = parsed[ind]
        if not isinstance(entry, dict):
            raise ScoringError(f"Indicator '{ind}' is not an object: {entry!r}")
        out[ind] = {
            "score": round(float(_clamp(entry.get("score"), 0, 100, 50.0)), 1),
            "confidence": round(
                float(_clamp(entry.get("confidence"), 0.0, 1.0, 0.3)), 3
            ),
            "evidence": str(entry.get("evidence", ""))[:200],
        }
    return out


def estimate_indicators(text: str, client: LLMClient) -> Dict[str, dict]:
    """Full pipeline: text -> prompt -> model -> parsed, clamped indicator dict."""
    text = (text or "")[:MAX_TEXT_CHARS]
    prompt = build_prompt(text)
    raw = client.complete(SYSTEM_PROMPT, prompt, raw_text=text)
    return normalize_indicators(parse_response(raw))


__all__ = [
    "ScoringError",
    "MAX_TEXT_CHARS",
    "build_prompt",
    "parse_response",
    "normalize_indicators",
    "estimate_indicators",
]
