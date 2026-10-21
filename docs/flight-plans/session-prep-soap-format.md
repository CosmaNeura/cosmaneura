# Session Prep SOAP headings and ordinary Markdown lists

Tier 2: provider reviewed-source projection. This is a small format-only follow-on
stacked on Core PR790 at `4da05b091ac4fd8763d91d4939d687b4704fb785`.
It adds no clinical predicate, source selection, authority, schema/table,
dependency, environment or cohort change. No model calls or migration apply.

## Provider behavior and strict coverage

The four closed SOAP title aliases are S-SUBJECTIVE, O-OBJECTIVE, A-ASSESSMENT
and P-PLAN, with case-insensitive matching and optional horizontal spaces around
the hyphen. Only the corresponding letter/name pair is recognized. Original
section keys remain in evidence. Custom or dated headings, Family History and
unknown titles are not stripped to make them eligible. Dates still derive from
the selected source's source_at, never a heading or a numbered list marker.

An ordinary line-start Markdown list marker may identify a new assertion block:
`-`, `+`, `*`, or a one-to-nine-digit number followed by `.` or `)`, with zero to
three leading spaces and one-to-four following spaces or a single tab before
same-line content. Only that marker/indentation is syntax outside the evidence
span. The full clinical payload remains an exact contiguous source substring
with original offsets. No text is rewritten in the note or evidence quote.

Continuation lines remain attached until another supported list marker begins
a new block. Every nonblank payload, unrecognized surrounding paragraph,
marker-only line, punctuation and oversized unit remains subject to the existing
whole-section validation. Unknown, foreign, conditional, uncertain, negated or
mixed-dose text is never removed to rescue another row. Task boxes, emphasis,
fences, code indentation and inline pseudo-markers are not normalized. All
clinical assertion and correction predicates remain unchanged.

Before any trimming or segmentation, any nonblank raw line with four or more
leading ASCII spaces or a tab in its leading indentation rejects the entire
section. This also conservatively rejects ambiguous indented list continuations.
Whitespace-only lines are harmless; a tab after a recognized list marker is
syntax rather than leading indentation. No code block is dropped while other
rows are retained. This closes both inherited code-only acceptance in PR790 and
new code-plus-list acceptance found in independent review of the first format
commit. PR790 alone must not activate the new facts consumer: its receiver must
include the final accepted descendant of this format correction.

## Applicability evidence and limits

The committed fixture mechanically preserves displayed strings from existing
fictional A/B/D staging SOAP captures. Original Markdown bytes and reviewed-note
revision timestamps were not captured. Its format controls apply representative
markers only to observed rendered list items; source dates are assigned from the
displayed visit labels for synthetic local tests. They are not live evidence
receipts or rewritten idealized clinical notes.

The controls recover A's straight `Losartan 25mg once daily (new this visit)`
listing as documented_listed, without asserting prescribed or taken status.
B/D still produce no supported rows: their mixed-dose correction, explanatory
indications, multi-analyte absence and patient-reported BP prose need separately
defined subject/status evidence rules. This change does not claim that the
nine-row hand-shaped consumer fixture represents those realistic notes.
Limited-coverage messaging and dated reviewed-source cards remain essential.

## Verification, dependency and recovery

Run the existing pure fact suite plus format-specific source-offset, alias,
list continuation, unknown/foreign/conditional/negation, oversize, code/punctuation,
order/result absence, same-note correction, candidate-substring and rendered-note
controls. Tests load only the pure stdlib/Pydantic module, bypass application
startup and use no database, .env, credentials, network or model.

The initial format commit passed 158 pure tests, but independent review found
the indented-code context gap above. Final correction validation must include
bare-code med/BP/order positive and negative examples, code-only/code-plus-list
and mixed tab/space forms, direct candidate rejection, indented corrections and
valid three-space/blank-line source-offset controls. Actual A/B/D format controls
retain exact displayed prose and recover only A's listed-unconfirmed medication.

The correction passes 180 pure tests (111 existing safety cases and 69 format/
applicability cases), including the original review reproductions and direct
candidate rejection across positive/negative med/BP/order code payloads.
Changed-file Ruff, Pyright and whitespace checks pass. Renew independent review
on this final pushed descendant before any new structured-facts activation.

Independent same-task agent review is required after the final substantive push.
The reviewer must be different from this change's implementer. PR790 must land
first; retarget this follow-on to master and reconcile its exact parent before
merge. Parent coordinates any later authorized staging rollout. No deployment is
part of this source checkpoint, and agent review is not GitHub approval.

Version 1.0 artifacts, generator/source digests and source revisions remain
unchanged. Existing 1.1 receipt replay already recomputes the projection and
denies a stale fact set, so a newly recognized list can require a fresh provider
action/read instead of replaying an old empty receipt. Receipts stay immutable.
Recovery restores the previous merged Core parser/image consistently, retaining
notes, artifacts and receipts. New projected receipts may likewise require a
fresh action after recovery. No destructive cleanup or schema rollback applies.
