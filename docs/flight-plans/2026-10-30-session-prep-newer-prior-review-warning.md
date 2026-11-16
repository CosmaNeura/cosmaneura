# Session Prep newer prior note review warning

Tier 1: additive discovery metadata and reversible presentation within the
existing Session Prep cohort. No schema, environment, flag, deployment workflow,
review authority, source grant, or provider-panel asset changes.

When a current owned prior-visit note needs review and is newer than the newest
selected reviewed source, Core discovery reports
`newer_prior_note_awaiting_review`. The existing bounded prior-note preview owns
that decision; the browser does not infer a clinical correction from note text.
The open visit, future visits, other patients, superseded notes, unusable notes,
and older or same-date unreviewed notes do not supply this warning.

Core renders the warning prominently in its existing record-context portal
inside the Session Prep panel. Useful dated reviewed history stays available,
including its source links. Awaiting-review notes remain excluded; the reviewed
source loader and selected-source fingerprint are unchanged. Explicit review
clears the warning when the newest selected source covers the newer visit, and
the existing fingerprint change prompts a fresh preparation.

Regression evidence exercises the real selection and discovery source against
isolated in-memory SQLite rows: reviewed F01 remains available after reviewed
F02 is retained and replaced by an unreviewed correction; the warning clears
only after the replacement receives matching review evidence. Host tests cover
warning updates without invalidating unchanged reviewed history, clearing after
review, accessible source links, and unchanged record-only authority inputs.

Recovery is to revert the warning commit. Retained notes, reviews, source
selection and artifacts require no data repair. Publication and deployment wait
for review. The separately observed factual Q03 acceptance failure remains open;
this source-coverage warning does not qualify clinical response accuracy.
