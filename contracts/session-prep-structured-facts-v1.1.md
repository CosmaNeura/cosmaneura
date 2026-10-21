# Session Prep explicit facts — opt-in receiver 1.1.0

Tier 2: this reads the existing provider record/PHI path. It does not change
authority, schemas/tables, dependencies, environment configuration or cohorts.
There is no migration rung. This receiver is the first rung of a separate,
backward-compatible structured-table rollout.

## Request

Use the existing authenticated `POST /service/v1/agent/tools/invoke` route and
its mount, interaction, turn and idempotency headers. A current canonical
`session-prep.open` provider action is still required. The request is closed:

```json
{
  "tool_name": "session-prep.context.read",
  "tool_version": "1.1.0",
  "subject_ref": null,
  "arguments": {
    "source_turn_ref": "conversation_turn_synthetic_0001",
    "context_snapshot_ref": "context_prep_synthetic_0001",
    "purpose": "session_prep",
    "detail": "structured_facts"
  }
}
```

Version 1.0.0 remains frozen and accepts its original brief/supporting-context
request shape. The provider runtime still restricts that version to brief.
Version 1.1.0 accepts only structured_facts. The companion brief route remains
version 1.0.0 only, and the synthetic demo fallback does not accept 1.1.0.
Provider-question context versions 1.0.0, 1.1.0 and 1.2.0 are unchanged.

## Result and evidence

The closed `result` has schema_version 1.1.0 and these fields:

- `preparation`: the unmodified version 1.0.0 Session Prep result.
- `source_version_digest`: the current reviewed-selection digest.
- `facts_state`: ready if eligible reviewed sources exist, otherwise unavailable.
- `reason_code`: null or no_eligible_sources.
- `structured_facts`: the closed fact set, or null without eligible sources.

A pending/failed model brief does not prevent a facts read. A ready fact set can
contain zero observations: that means no supported explicit mentions were
extracted, not that the chart contains no medications, orders or measurements.
All fact sets carry coverage `bounded_explicit_mentions`, selected source metadata/count,
selection_limited and facts_limited. Clients must show that limited-coverage
label and preserve access to the full reviewed notes for unsupported facts.

Every row has a stable fact_ref, kind, exact source subject/value, documented
state, vital setting and evidence. Evidence contains source_ref, source_version,
server-derived source_at, original section, exact quote and start/end offsets
in that section. Dates are **note dates**, never inferred measurement, medication
start or order dates. Offsets count Unicode code points as Python strings do;
clients should display the quote rather than apply offsets to UTF-16 strings.
The quote and subject/value are checked against the exact selected revision.
An exact span proves provenance; it does not prove clinical truth or entailment.

Supported extraction is deliberately narrow: explicitly worded medication/dose
observations, office/home/unspecified BP, and explicit statuses for common lab
names. Mixed lab sentences and ambiguous multiple medication subjects/doses are
left unstructured. Missing tests do not become orders or normal results.
Medication-list presence is labeled documented_listed, not prescribed or taken.
Prescription/planning does not become confirmed taking. No external services or
model calls are added by the projection. Unsupported negation, conditional or
contrasting predicates, questions, unknown status, other actors and mixed-time/dose
phrases are omitted rather than classified as patient actions. Medication
predicates require a whole supported asserted clause with the named dose;
unsupported actor/status suffixes and evidence-absence prose are omitted.
Medication corrections require a whole asserted same-drug/old-dose clause
immediately following the new observation; an unbound section marker never
establishes a correction;
active “A supersedes B” does not label A as superseded. Correction sections
mentioning a different medication cannot supersede the earlier medication.
Explicit supported non-use and order/result absence remain negative observations.
Only recognized clinical section titles are eligible. The full section is checked
for foreign actors, hypothetical/conditional or uncertain context before fragments
are considered. Newline qualifiers stay attached; semicolons split only when
every component is itself a complete supported assertion. Candidate quotations
must be one of those context-preserving segments, not an arbitrary substring.
Every enclosing section unit must be a supported assertion or correction;
unrecognized surrounding prose cannot qualify a patient fragment. Unknown or
qualified material remains unstructured in the reviewed notes.
Nonblank oversized or punctuation-only units remain visible to context validation:
any unsupported or overbound unit suppresses its whole section, rather than
leaving a later assertion to be classified without its enclosing context.
Evidence offsets identify the selected whole sentence or semicolon assertion,
never a matching substring inside a different sentence.

An explicit later medication correction can link an earlier different planned
or prescribed dose when the correction section identifies the earlier dose.
An explicit cancellation can link a prior order for that same named test.
`supersedes` contains earlier matching observation refs; correction_evidence
retains the exact later source section and date. Equal note dates establish no
ordering across different sources or sections. A same-note correction can link
only a strictly later non-overlapping span in the same source revision/section.
The earlier row retains its
original documented state/date; a client may display “superseded by the dated
correction”. These links never update medication confirmations or lab records.

## Bounds, freshness and recovery flight plan

Selection remains at most five currently reviewed notes, 18,000 characters each,
under the same authority and source adapter. Output is at most 48 facts,
1,200 characters per quote, and 80,000 characters for the fact set. Source
correction/revocation immediately suppresses old facts. Receipt replay rechecks
the current selection digest even when the original brief was pending/failed.
New 1.1 receipt replay also requires that the currently projected fact set matches
the stored observations, so a stricter parser cannot replay older unsafe states.
Receipts remain immutable and expire under the existing 24-hour retention.
No source text or rejected content is logged in validation exceptions.

Deploy this additive receiver first. Existing clients, immutable artifacts,
generator/prompt digests and old/new workers retain their existing behavior.
Then a separately reviewed consumer may opt into version 1.1.0 and render dated
tables. An unavailable/unsupported structured read must offer the existing brief
and reviewed-note path; do not present a failed structured read as absent chart
data. Recovery is to disable the consumer's opt-in/use version 1.0.0. Leave
existing receipts and artifacts untouched; no migration/backfill is needed.

Validation: synthetic exact-span/status/negation/Unicode-date round trips,
same-note correction and replay invalidation, preserved v1 immutable artifacts,
current provider revocation, real receiver closed-version checks, existing
provider-question versions and companion permission/route boundaries.
