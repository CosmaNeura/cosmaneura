"""Azure OpenAI adapter for the vendored engagement engine.

Implements the engine's tiny ``LLMClient`` protocol against the Azure OpenAI client
cosmaneura already uses (``app.configuration.openai``), so the engine runs on the
model we already pay for instead of Anthropic. This is the swap validated in the
phase-0 evaluation.
"""

from __future__ import annotations

from app.configuration.openai import DEFAULT_MULTI_MODAL_MODEL, openai


class AzureOpenAILLMClient:
    """Engine LLMClient backed by cosmaneura's Azure OpenAI deployment."""

    def __init__(self, model: str | None = None, max_tokens: int = 1024) -> None:
        self.model = model or DEFAULT_MULTI_MODAL_MODEL
        self.max_tokens = max_tokens
        self.name = f"azure-openai:{self.model}"

    def complete(self, system: str, prompt: str, raw_text: str) -> str:
        response = openai.chat.completions.create(
            model=self.model,
            max_tokens=self.max_tokens,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        )
        return response.choices[0].message.content or ""
