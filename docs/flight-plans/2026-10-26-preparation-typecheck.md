# Preparation and admission type checks

Tier 2 because the existing provider admission boundary is in scope. The owner
requested narrow release-readiness repairs on October 26. This change supplies
SQLModel column typing and explicit closed generation-error annotations; it
changes no admission policy, query predicates, model prompt, safety rule,
response contract, configuration, cohort or schema.

Validate the generator and safety regressions, plus real disposable PostgreSQL
provider admission, contention and expiry cases. Require zero diagnostics for
both application files and independent review of the final pushed commit.
Keep this repair separate from test-fixture typing and deployment changes.

There is no activation or migration step. Any later application release still
requires exact artifact, configuration and schema reconciliation under the
existing release plan. Recovery is a reviewed PR reverting this repair while
preserving subsequent changes; no database downgrade or credential action is
needed. No production or PHI-bearing rollout is authorized by these checks.
