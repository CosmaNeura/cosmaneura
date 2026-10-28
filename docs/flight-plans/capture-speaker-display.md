# Acoustic speaker display for captured transcripts

Tier 2: patient transcript persistence and the existing note-detail response.
This is an additive JSON payload change; no database migration, historical
backfill, retention change, provider call, or role inference is included.

Scribe can persist two acoustic speaker identities while both semantic roles
remain `unknown`. Core previously projected only the roles, so the dashboard
showed every turn as `Unknown`. New captured entries retain an optional
`source_speaker_id` separately from `speaker`. Raw IDs are namespaced with
`uuid5(raw.id, "speaker:" + raw_speaker_id)`, matching the existing working
contract; working and creator-reviewed entries retain that normalized UUID.
The UUID is not a person identifier, a clinical role, or an authorization claim.

The viewer numbers distinct IDs in first-appearance order within the selected
transcript. It displays `Speaker 1 (role unknown)` when the role is unknown and
keeps an explicitly supplied role separate. Raw UUIDs are not displayed. A turn
key also includes its index so overlapping start times do not collide. Existing
rows without acoustic metadata keep their existing label; this change cannot
recover speaker identity from those rows.

## Compatibility and validation

- Omit absent identity metadata from serialization. The old four-field Scribe
  replay projection and Recall content digest remain byte-compatible.
- Compare known IDs when both retained and incoming entries have them. A
  missing legacy ID is compatible and never causes row enrichment or rewriting.
- An already-matching delivery receipt may point to a retention tombstone.
  Return that row without reimport or dispatch; do not require deleted content.
- Keep model prompts, source indexes, note grounding, source digests, schema
  preferences and model-call counts unchanged. Identity is display metadata.
- Actual local PostgreSQL tests cover raw/working/reviewed projection, JSONB
  refresh, replay in both directions, known-ID conflict, and retention through
  the existing de-identification job with a synthetic provider stub. The
  de-identified dataset receives no acoustic identity. Browser tests cover
  stable numbering, unknown and explicit roles, legacy rows and overlapping
  starts. OpenAPI and the client are regenerated from the actual model.

## Release and recovery

Require independent review after the final substantive push. Before a staging
release, reconcile the then-current merged source, web image, frontend assets,
effective configuration and schema. Preserve worker/scheduler divergence and
all existing features; do not deploy during an active capture or Zoom occurrence.
This PR alone does not authorize a deployment.

Acceptance requires a fresh consented synthetic capture after a reviewed
release, followed by the normal saved-note/dashboard path: exact source and
displayed transcript entries, two stable acoustic groups, and unchanged unknown
roles. Local tests or a reconstruction of an older transcript are not that gate.
Do not regenerate or relabel the original retained note.

If display or replay fails, preserve the failed receipt and stop the affected
test. Repair or revert the change on current trunk and build a newly reviewed
candidate after live reconciliation. Preserve persisted rows and receipts; do
not restore an older whole image, rewrite metadata, or resurrect deleted text.
