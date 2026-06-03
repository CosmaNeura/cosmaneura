"""LLM client layer.

Two interchangeable implementations behind one tiny protocol:

* ``AnthropicLLMClient`` -- production. Calls the Anthropic Messages API with the
  rubric system prompt and returns the model's raw text (expected to be JSON).
* ``HeuristicLLMClient`` -- a deterministic, offline, lexicon-based rater that
  needs no network or API key. Used by the test suite and CI, and as a graceful
  "lite mode" fallback when no API key is configured. Its numbers are a rough
  directional signal, not a substitute for the model.

``build_default_client`` picks Anthropic when ``ANTHROPIC_API_KEY`` is present and
falls back to the heuristic client otherwise.
"""

from __future__ import annotations

import json
import math
import os
import re
from typing import Protocol, runtime_checkable

from .rubric import INDICATOR_MARKERS

DEFAULT_MODEL = os.getenv("COSMAS_MODEL", "claude-sonnet-4-6")
# Markers can be multi-word; match on the lowercased raw text via substring.
_WORD_RE = re.compile(r"[a-z']+")


@runtime_checkable
class LLMClient(Protocol):
    """Anything that can turn (system, prompt, raw_text) into a raw JSON string."""

    name: str

    def complete(
        self, system: str, prompt: str, raw_text: str
    ) -> str:  # pragma: no cover - protocol
        ...


class AnthropicLLMClient:
    """Production client backed by the Anthropic Messages API."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.0,
    ) -> None:
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "The 'anthropic' package is required for AnthropicLLMClient. "
                "Install it with: pip install anthropic"
            ) from exc
        self._client = (
            anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
        )
        self.model = model or DEFAULT_MODEL
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.name = f"anthropic:{self.model}"

    def complete(self, system: str, prompt: str, raw_text: str) -> str:
        resp = self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(
            getattr(block, "text", "")
            for block in resp.content
            if getattr(block, "type", None) == "text"
        )


class HeuristicLLMClient:
    """Deterministic, offline, lexicon-based communication-indicator rater.

    Scores each indicator from the balance of high-pole vs low-pole marker
    phrases present in the text (substring match, so multi-word cues count).
    Confidence rises with the number of matched cues and the text length.
    Returns a JSON string in the exact shape the rubric requests.
    """

    name = "heuristic-offline"

    def complete(self, system: str, prompt: str, raw_text: str) -> str:
        text = (raw_text or "").lower()
        n_words = max(len(_WORD_RE.findall(text)), 1)
        length_factor = max(0.3, min(1.0, n_words / 120.0))
        result: dict[str, dict] = {}

        for indicator, poles in INDICATOR_MARKERS.items():
            high_hits = [p for p in poles["high"] if p in text]
            low_hits = [p for p in poles["low"] if p in text]
            high = len(high_hits)
            low = len(low_hits)

            signal = high - low
            score = max(5.0, min(95.0, 50.0 + 40.0 * math.tanh(signal / 3.0)))

            total_hits = high + low
            base_conf = 0.2 + 0.55 * math.tanh(total_hits / 4.0)
            confidence = round(max(0.15, min(0.85, base_conf * length_factor)), 3)

            cue = (high_hits + low_hits)[:3]
            evidence = ("cues: " + ", ".join(cue)) if cue else "limited lexical signal"

            result[indicator] = {
                "score": int(round(score)),
                "confidence": confidence,
                "evidence": evidence,
            }

        return json.dumps(result)


def build_default_client() -> LLMClient:
    """Return the production client if an API key is set, else the offline client."""
    if os.getenv("ANTHROPIC_API_KEY"):
        return AnthropicLLMClient()
    return HeuristicLLMClient()


__all__ = [
    "LLMClient",
    "AnthropicLLMClient",
    "HeuristicLLMClient",
    "build_default_client",
    "DEFAULT_MODEL",
]
