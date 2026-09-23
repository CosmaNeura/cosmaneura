# Provider beta browser dependency remediation

The production frontend dependency scan is clean: 646 packages, zero known
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
