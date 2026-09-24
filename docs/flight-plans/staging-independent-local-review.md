# Accept accurately attributed independent local staging review

The September 23 owner policy permits local validation and independent review
while Actions is disabled. The version-2 staging controller currently accepts
only a report attributed to Cursor. Retained evidence records Cursor's usage
limit; a review from another agent must not be mislabeled as Cursor.

Tier 2: this changes release-evidence validation only. Add a separate
`COSMANEURA_LOCAL_AGENT_REVIEW` envelope for Codex or Claude Code. Retain the
Cursor envelope and the existing `local_cursor` plan-kind spelling so previous
plans and recovery receipts remain valid. Unknown reviewers and mismatched
envelopes fail closed. No review is created or approved by parsing an envelope.

The reviewer must be independent of the implementer, review after the final
substantive push, and record findings and disposition. The release operator
accepts the actual report before publishing its digest-bound envelope. Keep
the founder author, PR identity, exact reviewed head, full report hash, accepted
result, source-tree equivalence, protected-current-master, module integrity,
immutable-artifact and staging-only identity checks unchanged.

This checkpoint is based on Core `ad54a490fec3bbd427857f4cb57224bcc67a8268`.
It preserves Session Prep PR491, labs gate PR492 and all prior Scribe changes.
The feature deltas match their existing reviewed inputs byte-for-byte. Review
this small validator change and the composition using those retained reports;
do not claim a new whole-feature Cursor review. Release requires renewed
applicable tests and independent review of the final checkpoint.

The release sequence remains: canary merged PR492 as the labs-disabled fallback,
then deploy the combined candidate to Core web/worker/scheduler. Keep all lab
flags absent/false, preserve schema `71d6e1ac9b04`, and use fresh hash-bound plans
and observed recovery revisions. Preserve the current Scribe service artifacts
after input-equivalence verification; native Mac8/iPhone12 are separate.

Validation covers the legacy envelope and new attribution, wrong head/author/PR,
edited reports, unknown reviewer, malformed metadata and nonaccepted verdict.
Recovery restores the retained application revision templates without data or
schema rollback. The new envelope does not authorize production or recording,
waive independent review, change branch protections, or permit cross-task work.
