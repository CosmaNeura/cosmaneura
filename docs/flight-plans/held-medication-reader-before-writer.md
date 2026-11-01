# Held medication fidelity — Tier 2 private proposal

Temporary holds require a distinct status. No existing stopped/active row is automatically converted, confirmed, or re-extracted. Confirmed medication rows remain patient-wide; unconfirmed suggestions remain actionable only for the open source visit, with prior visits read-only and dated.

## Release sequence

1. Apply generated additive revision 312b217ebba4 after D4. Its autocommit enum expansion retains old values and data; retry is safe. No held producer is enabled by this migration.
2. Release held-capable ORM/API/UI readers to every Core API, worker and scheduler plus generated client. The reader source keeps create/PATCH inputs active/stopped-only and preserves extraction's old active/stopped classification. Complete all reader rollout and source-bound schema/contract checks before advancing.
3. Only then release the writer source: literal held extraction and explicit owner status edits/hold/resume. Confirming a suggestion is a separate provider action. Unknown extraction statuses are omitted, never silently classified active. Per-appointment extraction idempotence and source dates remain unchanged.

No runtime flag is introduced. Native Scribe capture and provider Session Prep paths do not consume PatientMedication in the inspected source; the Core medication API/generated client do. Other external API consumers must accept held before writer release. This proposal does not certify a live model or external integration.

## Recovery

Retain the expanded enum and every patient row. Before held writes, the previous app is conditionally compatible with the dormant enum. After any held row, stop writers and retain held-capable readers; deploy a reviewed forward fix rather than a closed old enum reader. Downgrade does not drop/rebuild the enum. Existing confirmations/history are not backfilled or rewritten.

## Qualification

Normal offline Alembic generation used the qualified cached runtime/dev72 and exclusively owned internal PostgreSQL/Redis; generated ID is not invented. Reader-stage normal PostgreSQL routes/extraction/migration graph: 14 passed, no failures/errors/skips. Reader real DOM: 13 passed. Full normal OpenAPI export and pinned HeyAPI 0.97.3 generation completed; normal route typegen and full TypeScript passed using a task-owned copy of existing dependencies. These are local synthetic/source checks, not hosted CI or model acceptance. Writer checks remain pending. Synthetic fixtures only, no live credentials, no staging/prod schema/config/record writes. Source publication, migration and deployment remain unapproved.
