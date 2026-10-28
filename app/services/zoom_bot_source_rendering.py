"""Compact exact-source rendering, grouped only by original source identity."""

import json
from dataclasses import dataclass
from itertools import groupby

from app.services import scribe_capture_history_grounding as history
from app.services import scribe_capture_plan_grounding as plans


def render_sources(indexes, sources, identities):
    """Keep all selected text and order; never infer matching voices or roles."""

    def identity(index):
        # Missing identity metadata cannot establish that two entries share it.
        original = identities[index - 1] if identities else (index,)
        return original, sources[index - 1].attribution

    blocks = []
    for (_, attribution), group in groupby(indexes, key=identity):
        refs = tuple(group)
        label = attribution.removeprefix("Transcript — ")
        if len(refs) == 1:
            blocks.append(f"- {label}: {sources[refs[0] - 1].text}")
        else:
            blocks.append(
                label
                + ":\n"
                + "\n".join("- " + sources[index - 1].text for index in refs)
            )
    return "\n".join(blocks)


@dataclass(frozen=True, repr=False)
class BotPlanGrounding(plans.PlanGrounding):
    identities: tuple = ()

    def resolve(self, raw, *, max_bytes):
        fields, planned = super().resolve(raw, max_bytes=max_bytes)
        if not self.fields:
            return fields, planned
        refs = json.loads(raw)["plan_source_refs"]
        return fields, {
            heading: render_sources(
                sorted(refs[heading]), self.sources, self.identities
            )
            if refs[heading]
            else planned[heading]
            for heading in self.fields
        }


@dataclass(frozen=True, repr=False)
class BotReportedHistoryGrounding(history.ReportedHistoryGrounding):
    identities: tuple = ()

    def _render(self, indexes):
        return render_sources(indexes, self.sources, self.identities)
