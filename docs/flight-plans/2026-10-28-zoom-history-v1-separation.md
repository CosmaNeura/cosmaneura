# Preserve the evaluated Zoom history contract

Tier 2: clinical note source selection. This separates the already reviewed
6d2b1752 Zoom pilot history contract from evolving capture-only Objective rules.
It adds no cohort, schema, dependency, identity inference or provider call.

The version-one helper retains the original six history methods and constants.
Bot identity metadata uses its own explicit constructor field. The existing
bot response wrapper retains version-one history strings while leaving its
Plan and patient-only Subjective constraints intact. Capture request, source,
authorization, role, preference and persistence behavior remains unchanged.

Focused checks verify independence from a changed capture factory and history
schema while retaining speaker IDs and source rendering. The existing retained
Zoom request/rendering checks and immutable source comparisons remain required.
These checks do not regrade original model failures or qualify live usability.

Revert this refactor to restore the prior shared helper for future drafts.
Existing notes and edits remain saved; no migration or data rollback is needed.
The final source needs independent review before publication or integration.
Capture note integration and any build/deployment require separate final-source
review and the current user's selected staging workflow.
