# Note generation and per-version review labels

Risk: Tier 2 (patient-note presentation). One reversible frontend outcome.
Base: accepted Core `4b12f60c97933d21d95729cdd64eb97eaacaa367`.

The note-detail generation badge and arrival message say “AI note ready”.
Generation completion does not claim a clinician review, signature or approval.
The existing Session Prep review component appears immediately below each note
heading, including when its contents are collapsed. Its existing exact note/digest
key, local saved state, explicit action, pending/error behavior and response-digest
check remain intact. Source editing continues to suppress the review control.

Only an existing per-note review metadata entry supplies a review label:
“Not reviewed for Session Prep” or “Reviewed for Session Prep”. Missing metadata
supplies neither label nor action. Core already omits this metadata for unavailable
or ineligible access and ViewAs; this change adds no whole-visit review state.

Qualification uses the three focused existing UI modules, with synthetic mixed,
missing, corrected-version, failed-save and exact-digest saved-review cases.
Normal current-head CI and final independent source review remain required before
publication. Local DOM checks do not prove live account access or clinical fidelity.

Recovery: restore the prior frontend files. Existing saved review records and
clinical note content remain unchanged; no API, schema, role or configuration
change is required. Note-generation fidelity and medication classifications are
separate open work and receive no acceptance claim from these labels.
