# Flight plan: credential-neutral Cursor Cloud Agent

## Outcome and boundaries

- Outcome: every repository-managed Cursor Bash entrypoint starts behind `env -i`,
  cannot read 1Password overrides, uses immutable bootstrap/Compose trust roots,
  and can mutate only exact local development databases.
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

- Protected `master` includes PR #257. This follow-up starts from exact baseline
  `8a62f57e456ddf131172b5f6be191c8f6fa02b29`; new or rebuilt Cloud Agents may
  consume its repo-managed `.cursor/environment.json`.
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
- Abort if a child receives a sentinel secret or noncanonical database URL, an `op`
  probe executes, or the Cursor Compose configuration contains a tag-only image.
- No staging, production, migration, or founder promotion action is part of
  this rollout.

## Security and recovery

- Residual dev-only trust: apt is not snapshot-pinned; Mise-managed exact-version tools
  rely on upstream channels; Playwright fixes Chromium revision 1161 but does not
  repository-verify its archive digest. No credentials or live data may enter.
- A platform-owned outer interpreter, if present, runs before `/usr/bin/env -i` and is
  outside repository control. The Cloud Agent host must remain credential-neutral.
- Direnv uses a dedicated, empty configuration root; persisted global or HOME
  Direnv hooks are forbidden rather than trusted.
- Mise uses dedicated empty user/system configuration roots, isolated system
  data, disabled hooks, an explicit global-config binding, and an ancestor
  discovery ceiling that retains repository config. Only its exact reviewed
  binary is accepted in `.local/bin`; generated shims are not entry paths.
- Recovery: never revert to credential-exposing PR #257 behavior. Disable entrypoints or
  roll forward, discard/rebuild affected agents, and rotate potentially exposed secrets.
- No irreversible cleanup or observation window applies.

## Completion

- Required evidence: exact-head CI, focused credential/URL/bootstrap/Compose
  tests, shell/static checks, and independent review with all findings closed.
- Follow-up: pin the OpenAPI generator in the package lock before restoring
  automatic code generation; separately evaluate a verified browser artifact.
- Update `.cursor/README.md` with the enforced boundary and residual trust.
