# Capture SOAP medication-list limitation and Plan layout

Risk: **Tier 2**, because this changes clinical draft instructions and presentation.

A saved synthetic SOAP draft generated after normal speaker correction retained
unchanged-medication history but omitted that the current medication list was not
specified. Its Plan displayed repeated transcript blocks. The capture-specific
instruction now explicitly retains the medication-list limitation in an existing
history field even when the schema has no Medications heading and roles are
known. Complete selected Plan entries render as attributed bullets.

This change retains the configured model, one-call shape, schema, preferences,
source roles, complete-entry Plan extraction, negations, qualifications, source
chronology and empty-Plan rejection. It does not alter saved notes, the general
summarizer, Session Prep, persistence, permissions or migrations.

The fixture is the actual invented 39-entry Desktop13 test source and observed
SOAP schema/preferences. Its archived saved draft fails the fixed oracle for
`medication-list-unknown`. One actual configured-model diagnostic of the candidate
retained the limitation and passed that oracle, with no database connection or
saved-note regeneration. This is limited model evidence, not rebuilt-client,
staging or release qualification. The full response and resolved fields remain
in the task's private evidence packet.

The Plan still retains nonclinical chatter when it shares a complete selected
entry with care instructions. This gate stays open. An attempted prefix-clipping
scheme was rejected by independent review because it could discard clinically
important qualifiers; no excerpt channel or clinical-term filter is included.

## Validation and rollout

1. Run focused capture/PlanGrounding tests, including original note rejection,
   known-role/no-medication-heading instructions, complete clinical context and
   rejection of an excerpt channel. Run formatting and lint checks.
2. Obtain independent review of the final pushed commit and record its findings.
3. Reconcile the then-current Core trunk, staging image/configuration and schema
   before selecting a merged candidate for the authorized synthetic staging lane.
   Preserve current Session Prep and intervening migrations. No automatic
   production promotion or deployment is part of this PR.
4. Test a new disposable synthetic note through the normal workflow. Preserve
   existing saved test versions and keep the Plan-chatter gate explicitly open.

## Recovery

Revert this PR's two capture-service changes on current trunk and use the existing
reviewed staging candidate process. This restores the earlier instruction and
full-source layout. No migration, data rollback, backfill or saved-note
regeneration is required or authorized. Notes already saved remain unchanged.
