# Activate the reviewed Session Prep panel

Tier 2: the provider dashboard loads this clinical panel. The increment changes
the asset pin, SRI and immutable upstream source reference together. It changes
neither the reviewed script bytes nor the host's authentication or loader code.

The candidate requires merged Core PR804 and dormant-asset PR805. The panel is
the exact reviewed Sovereign PR79 asset from merged source
`f4fa7a12a455ce07ec3e39d1063b1370dbc42456`, SHA256
`37f81d02819783a1249f89c988e23984a52f4026be80b430c59d619e436e22fb`.
All older content-addressed assets and both new formatter exclusions remain.

The public panel/host contract, provider ownership checks, context-token
exchange, clinical citations, safety gates, browser storage restrictions and
server configuration remain unchanged. No Scribe, schema, dependency or
external-contract changes belong to this increment.

Sequence: independently review the final pushed activation head, merge it,
then produce one comprehensive staging candidate from the actual merged Core
and Sovereign sources. The frontend must be rebuilt using the existing accepted
public staging settings and contain the pinned script at its immutable path.
Inspect the built asset bytes and SRI, run independent image/runtime admission,
and verify source-pair compatibility before any separately approved rollout.
There is no deployment between dormant preparation and activation, and this
PR executes no build, migration, configuration change or cloud operation.

Any separately required Core migration or operational beta check retains its
own source-bound plan and evidence; this panel pin authorizes none of them.
The final release must preserve Scribe apps/jobs, existing staging configuration
and selected recoveries. It must demonstrate the actual loaded asset identity
and provider workflow on staging rather than infer them from local tests.

The migration cutover cannot claim zero overlap through manual deactivation in
the current Single revision mode. A separate independently reviewed plan and
concrete user authorization must cover a temporary Core-only transition to
Multiple mode: pause/drain the old web app and scheduler, keep the old worker
only for the fixed approved SQL operation, then stop/drain that worker. Start
the candidate worker and scheduler before the candidate web app, which comes
last. Restore each Core app to Single only after its correct candidate is ready.
Preserve Scribe apps/jobs and selected recoveries; disclose the temporary
downtime for calls that depend on Core. This PR neither approves nor executes
that temporary configuration exception.

Validation uses existing asset identity, pending-to-ready completion, loader
recovery and conversation-history tests with exact private locked dependencies
and synthetic network responses. The completion test also checks the actual
loader's SRI, anonymous cross-origin mode and no-referrer policy. No live model,
database, browser or cloud call is needed for this source checkpoint.

Recovery before rollout restores the previous Core pin/SRI/source reference in
a separately reviewed commit while retaining both assets. Recovery after rollout
uses the selected previously accepted Core/Sovereign image pair and the exact
release recovery plan, preserving configuration and schema state. From the first
candidate web activation onward, Core recovery must understand correction
frontiers; the current 8d84a6e1d00d69912c9e164f57016fbddabaf1e3 image cannot be
an active rollback. Retain the additive frontier table and its committed state:
do not lower or reset frontiers, backfill them, or downgrade an applied migration.
Recovery must not serve an unreviewed source pair.
Local test success is not a claim of live beta completion.
