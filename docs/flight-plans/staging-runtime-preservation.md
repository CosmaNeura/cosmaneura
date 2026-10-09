# Preserve staging runtime configuration during an application release

Tier 2: staging deployment credentials and configuration. The operational beta
release must retain the current Scribe integration, provider cohort, and runtime
keys while advancing Session Prep. The previous identity fingerprint covered
only some keys, and an API-only comparison missed background-workload drift.

An opt-in `STAGING_PRESERVE_EFFECTIVE_CONFIGURATION=true` mode requires
`EXPECTED_STAGING_EFFECTIVE_CONFIGURATION_SHA256=sha256:...`. The fingerprint
covers the complete effective environment and arguments of Core web, worker and
scheduler, derived from the existing deployment-owned variable lists. The
configuration is resolved once, after which the same in-memory values are used
for fingerprinting, preservation checks, migration configuration and deployment.
Missing or empty values follow the existing removal semantics. Existing v1/v2
release-plan formats and invocations with preservation omitted remain valid.

Before migrations, compare every workload's current public values and resolved
secret references with the exact desired configuration. Before updating each
workload, repeat the check and require its original ready revision. An unexpected
rotation, worker-only difference, missing reference or removal refuses the
release before that step. The bound Azure reader permits secret-list values only
for the three fixed Core apps in the staging subscription/resource group.

Preservation mode skips application secret writes and unused-secret cleanup.
It is for a release that retains configuration, not an intended credential
rotation or capability revocation. Such changes continue through the existing
explicit configuration-change path. The resolver and comparison emit fixed
errors, workload names and fingerprints; no credential values enter evidence.

Recovery retains the three observed application revision templates and existing
database schema/records. Use the same guarded local build, immutable publication,
source and recovery checks as the combined Scribe/Session Prep flight plan.
Independent final review and live preservation/operational acceptance remain
required; fixture tests do not claim that deployment happened.
