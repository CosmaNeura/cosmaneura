# Release validation repairs

Tier 2 because the existing Zoom lifecycle boundary is in scope. Restore the
release checks after master PRs #841 and #842: narrow synchronous Redis return
types without converting values, annotate synthetic fixture dictionaries, and
assert successful test results and local oracle loaders before using them.
Keep optional schema-generator imports behind their existing qualification
gate, and update a stale prompt assertion to the current source-selection rule.
Regenerate the browser OpenAPI snapshot with the locked server dependencies.
No launch policy, clinical generation, authorization, billing, configuration,
schema, or dependency changes are included.

Require clean full Python type checks, Ruff lint/format, exact server/snapshot
equivalence, existing capture grounding and real disposable Redis/PostgreSQL
regressions, and independent review of the final pushed commit. Keep unrelated
merged changes and existing opt-in lab gates intact.

The owner requested release execution after these repairs. Reconcile deployed
artifacts, configuration and schema before running existing release controls;
retain their infrastructure, provider callback and synthetic-evidence gates.
Recovery is a reviewed forward revert preserving subsequent changes and data.
