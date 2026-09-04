# Flight plan: credential-neutral Cursor Cloud Agent

## Outcome and boundaries

- Outcome: every repository-managed Cursor Bash entrypoint starts behind
  `/usr/bin/env -i`, cannot read 1Password-backed local overrides, uses
  immutable bootstrap and Compose trust roots, and can mutate only exact local
  development databases.
- In scope: `.cursor/` install/start/server paths, their narrow `.envrc` marker
  branch, immutable installers, and focused policy tests.
- Non-goals: application behavior, shared developer Just recipes, live staging
  or production access, OpenAPI codegen dependency changes, or deployment.
- Affected scope: disposable non-PHI Cursor Cloud Agent development only.

## Risk and dependencies

- Tier 2: this change establishes the security boundary that prevents remote
  development tooling from receiving live credentials or database targets.
- No irreversible decision or cross-repository contract is introduced.
- This is one independently holdable PR based on protected `master`.

## Current deployed state

- Protected `master` already includes PR #257. This follow-up starts from exact
  baseline `8a62f57e456ddf131172b5f6be191c8f6fa02b29`, whose repo-managed
  `.cursor/environment.json` may be consumed by new or rebuilt Cloud Agents.
- No staging or production application runtime consumes this Cloud Agent
  configuration. Deployed application-artifact reconciliation is N/A.

## Feature flag / release control

- N/A: no user-visible application behavior is introduced.

## Schema/data sequence

- N/A: no schema or data change is shipped. Local migration and seed commands
  are existing developer operations wrapped by stricter authorization.

## Rollout and promotion

- Merge only after focused tests, repository CI, and exact-head independent
  review are green; then validate a newly created credential-free Cloud Agent.
- Abort if a sentinel secret reaches a child command, an `op` probe executes,
  a noncanonical database URL reaches a command, or a tag-only image appears in
  the Cursor Compose configuration.
- No staging, production, migration, or founder promotion action is part of
  this rollout.

## Security and recovery

- Residual dev-only trust: signed apt repositories are not snapshot-pinned;
  Mise-managed exact-version tools rely on their upstream channels; Playwright
  fixes Chromium revision 1161 but does not repository-verify the browser
  archive digest. No credentials or live data may enter this environment.
- Cursor's platform-owned outer command interpreter, if present, necessarily
  executes before the configured `/usr/bin/env -i` command and remains outside
  repository control. The Cloud Agent host must itself remain
  credential-neutral.
- Recovery: do not revert to the credential-exposing PR #257 behavior. Disable
  the Cloud Agent entrypoints or roll forward with a reviewed repair, discard
  and rebuild every affected disposable agent, and rotate any potentially
  exposed credential before reuse.
- No irreversible cleanup or observation window applies.

## Completion

- Required evidence: exact-head CI, focused credential/URL/bootstrap/Compose
  tests, shell/static checks, and independent review with all findings closed.
- Follow-up: pin the OpenAPI generator in the package lock before restoring
  automatic code generation; separately evaluate a verified browser artifact.
- Update `.cursor/README.md` with the enforced boundary and residual trust.
