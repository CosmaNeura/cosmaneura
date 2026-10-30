# Session Prep aggregate normalization refusal

Tier 2: production eligibility evidence touches billing and organization authority; runtime access, billing projection, schema, rollout, and deployment controls are unchanged.

The aggregate preflight could count an otherwise authorized public practice with a trailing-tab Stripe ID as eligible, while runtime Python `strip()` rejects that binding. Space-only SQL trimming also differs for roster email/domain inputs, and PostgreSQL casing depends on collation.

Before classifying any provider, the existing aggregate integrity query checks every stored doctor email, roster email, organization domain, and Stripe binding ID. Any character outside U+0020–U+007E refuses the whole report with the existing generic failure and no partial counts. SQL casing, normalized email comparisons/history grouping, and Stripe binding comparisons use explicit `C` collation. Reviewed normalizer AST hashes refuse changed runtime semantics without importing the application. The existing fixed count schema and canonical report digest remain unchanged.

Validation uses fictional values, reviewed pure function ASTs, and mocked aggregate connections, including trailing controls/Unicode whitespace, bounded printable ASCII cases, whole-report refusal before classification, and changed-source refusal before connection. These checks do not query production or establish Unicode semantic parity. Actual all-provider production eligibility proof remains open until an authorized exact-source read completes; unsupported inputs require separate reviewed remediation, never silent exclusion or normalization of stored data.

Recovery: stop using this evidence command or revert this source-only checkpoint. Never restore the prior report as trusted evidence for unsupported normalization. There is no data, entitlement, cohort, or deployment rollback. Independent nonimplementer review is required after the final substantive push before promotion; this local checkpoint is not approval or activation authority.
