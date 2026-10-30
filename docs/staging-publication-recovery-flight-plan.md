# Canonical staging publication recovery

Tier 1: reversible staging CI/deployment control. No application, schema,
production configuration, registry tag replacement, or cohort change.

The ordinary Docker push parser now accepts the actual candidate-tag prefix.
Run 41090712649 validated candidate `24b75868a9a9c02ad035578c070f8a46c5589e43`
and pushed digest `sha256:4a3e720bdc212d80a65842ed6a3d7e87bda66b9aaa36b588963fee874b6fecfb`,
then failed to parse that digest. Publication and the overall original run remain
failed; deployment was skipped.

An optional explicit recovery selector authenticates only that retained run,
attempt, source, founder, original inputs, completed candidate checks, archive,
artifact hashes and failed publication boundary. This mode reuses those exact
validation results and archive without rebuilding or pushing. Fresh protected
configuration must match the archive's original public configuration. The
registry tag descriptor must resolve to the exact published digest, and its
immutable manifest must reference the loaded archive's image ID. Ordinary
existing-tag refusal remains unchanged.

Execution order:

1. Obtain final-head independent review and all required PR checks, then merge
   normally. The code change grants no dispatch or deployment authorization.
2. Select the exact current protected controller, unchanged candidate/PR and
   ten original inputs, plus the narrowly pinned recovery selector. Refresh the
   existing source-idle, custody and configuration bookends before dispatch.
3. Dispatch only after the founder selects the reviewed packet. The existing
   source, identity, configuration, migration, workload and release-evidence
   gates still control staging mutation. Recovery logs retain skipped current
   validation jobs and separately identify the original successful validation.
4. Reconcile the actual release receipt with ready workload source/digests and
   schema, then resume the held synthetic lab workflow.

Stop on any missing/expired/mismatched evidence or changed registry descriptor,
archive, candidate, controller, identity or public configuration. Do not rerun
the old controller, overwrite/delete its tag, or directly deploy the image.
Before deployment, recovery is abandonment of the selected dispatch; after
deployment use the existing staging release recovery digest and guards. The
previous staging digest is
`sha256:cbdfcbe170431a0c7edcc09c26985ebcc751519470ed61a70f2b2416a9269b5e`.

Focused validation executes the actual publication shell with isolated fake
Docker, checks the recovery aggregate boundary, and rejects altered digest or
failed original candidate validation. Official required PR checks supply full
quality checks; live recovery qualification follows the actual dispatch.
