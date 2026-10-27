# Capture preferences follow the selected note schema

Risk: **Tier 2**, because this changes clinical draft instructions.

With ROS separation enabled, the legacy prompt says that symptom-survey negatives
belong only in Review of Systems. A capture SOAP schema without a ROS field still
received that instruction. A later capture-specific retention reminder attempted
to override it, leaving the model with conflicting instructions.

The capture's effective customization now disables ROS separation when its actual
schema has no ROS field. When ROS exists, the saved preference remains effective.
The existing capture override that forbids inferred Plan reasons is handled by
the same helper. Saved preferences and their configuration fingerprint are not
changed. The general summarizer and complete-entry Plan extraction are unchanged.

## Evidence and limits

- 105 focused capture tests and 13 medication-scope oracle regressions passed in
  one disposable PostgreSQL schema; the schema was removed. Ruff checks passed.
- Three matched actual configured-model calls per variant used the same retained
  synthetic 39-entry source, schema, roles, system instructions and one-call
  envelope. Each call used a 30-second timeout and no retries, database connection
  or saved-note regeneration. Requests were identical within each group.
- The original finite checks passed in all three candidate outputs. All three
  current-trunk baselines omitted the unspecified current medication list.
- The stronger prospective-scope check fails all six outputs: the statement that
  no medication changes are planned is broader than not starting a new medicine.
  Retrospective unchanged-medication history cannot replace that future decision.
- Mixed clinical/editorial source text, duplicate content across sections and
  verbose source quotations remain. Two candidate outputs add conventional but
  unspoken blood-pressure units. Full clinical completeness and usability remain
  open; the narrow schema correction is not a release-readiness claim.

The unchanged source is `tools/capture-retention/desktop13-synthetic.json`.
`schema-preferences-validation.json` records result hashes and both oracle
outcomes; `desktop13-plan-expectations.json` supplies separately reviewed finite
expectations. These expectations never enter model prompts. Historical outputs
and their original oracle results remain intact in the retained private packet.

## Rollout and recovery

1. Obtain independent review of the final pushed commit and its limitations.
2. Reconcile current trunk, deployed artifact/configuration and schema before
   selecting a merged candidate for the authorized synthetic staging lane.
   No deployment or workflow dispatch is part of this PR.
3. Qualify a new disposable synthetic note through the normal workflow. Preserve
   existing saved versions and continue the prospective-completeness and usability
   gates. Do not treat the finite subset as complete clinical validation.
4. If recovery is needed, revert this PR's capture preference helper/call-site
   change on then-current trunk. The validation-only oracle changes need no data
   rollback. No migration, backfill or saved-note regeneration is needed.
