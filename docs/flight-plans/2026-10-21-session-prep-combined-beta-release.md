# Combined Session Prep beta release checkpoint

Tier 2 release checkpoint for the combined Core tree at
`37387ec436687f94efe9bdac4d815ff7f9c22404`. Initial beta permits providers to
prepare their own visits; delegated RN access remains outside this release.

The combined implementation contains the independently reviewed changes from
PRs 799, 801 and 800: an isolated default-off staging health-alert client,
bounded structured dated chart observations, and source-only brief v4 with
stricter negative-completion fidelity checks. This checkpoint changes documentation
only. Review the complete combined tree after its final push, and preserve the
individual reviews and prior findings as evidence.

Local composition verification: 273 chart-facts checks, 110 generator checks and
124 health-client checks pass (507 total). The health run intentionally excludes
one existing FastAPI integration test. Prior real PostgreSQL checks cover unchanged
cache, runtime and worker blobs; they do not represent current full-generator
PostgreSQL execution or live model acceptance.

Build Core only from the reviewed exact source tree over the accepted Core
28b4/ed9876 dependency image. Preserve all 188 frontend files from fd8308 and their
existing configuration; do not rebuild Sovereign, Scribe or the public app.
Use genuine current source-pair, database-version, full workload and selected
recovery evidence without extending historical admission deadlines.

Roll out the same immutable image to the Core scheduler, worker and web app in
that order. Preserve configuration, database schema, Scribe apps/jobs and production.
Keep the new health opt-in absent until a separate reviewed configuration action
is authorized; global staging Sentry stays disabled.

After deployment, verify provider-owned fictional visits, current reviewed-source
dates, exact chart excerpts and observation rows, dated dose correction, fresh
v4 brief generation, stale-brief refusal and bounded unsupported-draft recovery.
Repeat the known foreign-owner denial. Native Scribe capture-to-re-review acceptance
requires access to the existing staging-connected app/build.

Recovery: restore the accepted prior Core image and its exact workload configuration,
retaining the selected prior nine recovery revisions plus the current Core three
and Sovereign one. Abort on unexpected source, workload, schema or recovery drift.
