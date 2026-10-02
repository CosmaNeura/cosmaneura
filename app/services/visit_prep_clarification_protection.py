"""Core-protected single-topic AI input and exact original-word grounding.

No route, service credential or patient authority is granted by these functions.
The future host selects current context under patient authority, runs protection
outside DB locks, then rechecks authority/source before egress and persistence.
Never serialize the original context to Sovereign or a model.
"""

from dataclasses import dataclass, field, replace
from difflib import SequenceMatcher
from typing import Any

from app.services.visit_prep_clarification_contract import (
    PatientClarificationReply,
    PatientQuoteSpan,
    VisitPrepClarificationContext,
    VisitPrepClarificationInvalid,
    validate_clarification_context,
    validate_clarification_reply,
)
from app.services.visit_prep_protected_analysis import (
    PROTECTION_PROFILE,
    VisitPrepProtectionFailed,
    protect_analysis_texts,
)


@dataclass(frozen=True)
class ProtectedClarification:
    original: VisitPrepClarificationContext = field(repr=False)
    protected: VisitPrepClarificationContext = field(repr=False)

    def model_payload(self) -> dict[str, Any]:
        """Closed minimum-necessary wire; no record IDs, contacts or credentials."""
        current = self.protected
        return {
            "contract_version": "1",
            "protection_profile": PROTECTION_PROFILE,
            "actor_role": current.actor_role,
            "kind": current.kind,
            "physician_prompt": current.prompt,
            "reported_words": current.reported_words,
            "turns": [
                {"prompt_code": turn.prompt_code, "patient_words": turn.patient_words}
                for turn in current.turns
            ],
        }


def protect_clarification_context(
    context: VisitPrepClarificationContext,
) -> ProtectedClarification:
    """Apply Core's existing clinical protection to the prompt and patient turns."""
    try:
        validate_clarification_context(context)
        values = protect_analysis_texts(
            (
                context.prompt,
                context.reported_words,
                *(turn.patient_words for turn in context.turns),
            )
        )
        protected = replace(
            context,
            prompt=values[0],
            reported_words=values[1],
            answer=context.answer if context.kind == "choice" else values[1],
            turns=tuple(
                replace(turn, patient_words=value)
                for turn, value in zip(context.turns, values[2:], strict=True)
            ),
        )
        validate_clarification_context(protected)
        return ProtectedClarification(context, protected)
    except Exception:
        raise VisitPrepProtectionFailed() from None


def ground_protected_reply(
    context: ProtectedClarification, reply: PatientClarificationReply
) -> PatientClarificationReply:
    """Accept only unchanged excerpts; never restore substituted PII into output.

    Offsets may shift after de-id. Map an excerpt only when it is entirely inside
    one exact matching block of original/protected patient words. No fuzzy match,
    inferred paraphrase or raw fallback. Core's ordinary proposal validator then
    independently checks the mapped reply against the original current source.
    """
    try:
        validate_clarification_reply(context.protected, reply)
        original = {
            "answer": context.original.reported_words,
            **{
                f"clarification_{i + 1}": turn.patient_words
                for i, turn in enumerate(context.original.turns)
            },
        }
        protected = {
            "answer": context.protected.reported_words,
            **{
                f"clarification_{i + 1}": turn.patient_words
                for i, turn in enumerate(context.protected.turns)
            },
        }
        mapped = []
        for span in reply.statements:
            blocks = SequenceMatcher(
                None, original[span.source], protected[span.source], autojunk=False
            ).get_matching_blocks()
            matches = [
                block
                for block in blocks
                if (
                    block.size
                    and block.b <= span.start < span.end <= block.b + block.size
                )
            ]
            if len(matches) != 1:
                raise VisitPrepClarificationInvalid()
            block = matches[0]
            start = block.a + span.start - block.b
            end = start + span.end - span.start
            # A replacement can share letters with the original identifier.
            # Never treat a surviving fragment inside a changed word as a safe
            # quotation (e.g. the shared suffix of two substituted names).
            for text, left, right in (
                (original[span.source], start, end),
                (protected[span.source], span.start, span.end),
            ):
                if (
                    left > 0
                    and (text[left - 1].isalnum() or text[left - 1] == "_")
                    and (text[left].isalnum() or text[left] == "_")
                    or right < len(text)
                    and (text[right - 1].isalnum() or text[right - 1] == "_")
                    and (text[right].isalnum() or text[right] == "_")
                ):
                    raise VisitPrepClarificationInvalid()
            mapped.append(
                PatientQuoteSpan(
                    source=span.source,
                    start=start,
                    end=end,
                    quote=span.quote,
                )
            )
        result = reply.model_copy(update={"statements": tuple(mapped)})
        validate_clarification_reply(context.original, result)
        return result
    except Exception:
        raise VisitPrepClarificationInvalid() from None
