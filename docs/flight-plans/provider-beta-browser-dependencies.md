# Provider beta browser dependency remediation

The production frontend dependency scan is clean: 642 packages, zero known
advisories. This work is split into three bounded increments based on Core
master `ddfe047c`: patched React/Clerk identity runtime, patched React Router/
Vite/PostCSS, and patched transitive dependencies through version-scoped
overrides. Each increment stays below the repository change budget.

Each increment passed all 319 existing component tests, TypeScript, and a local
production-style build using synthetic settings and inert telemetry endpoints.
Lifecycle scripts were disabled during installation. Existing development-tool
peer/deprecation and build warnings remain; they are not suppressed. GitHub
Actions is disabled, and no assets or application have been deployed.

Risk: Tier 2 for identity/security compatibility. The domain contracts, provider
cohort, and backend schema do not change. Recovery is the previous frontend
application image and matching lockfile. Independent agent review of each final
pushed increment remains required. The complete beta candidate must also include
and validate the Session Prep feature and backend runtime dependency increments.

The independent Cursor review found that exact override targets could downgrade
already-patched resolutions. Targets now permit compatible updates with floors
at least as new as the prior lock for Babel core/runtime (7.29.7), picomatch
(4.0.7), browserslist (4.29.0), and nanoid (3.3.19). The rebuilt graph passed
319 component tests, TypeScript, and the synthetic local build; its production
advisory scan reports zero known issues across 642 packages.

Cursor also flagged Rollup's optional LZMA native package for provenance review.
The official v4.63.4 source package.json and npm registry manifest both declare
@napi-rs/lzma-linux-x64-gnu 1.5.1, and Rollup's published integrity matches this
lockfile. Read-only source/registry observations are retained with beta evidence.
This resolves the proposed lockfile inconsistency; it is not a blanket supply-
chain attestation. No downloaded package code was executed during that check.
