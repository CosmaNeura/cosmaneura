# Flight plan: credential-neutral Cursor Cloud Agent

- Tier 2 hardening from `master` `8a62f57e`: clear inherited state, bind physical
  config/tool roots, and allow only exact local DB targets.
- Scope: `.cursor/`, its credential-neutral `.envrc` branch, pinned bootstrap
  helpers, and focused tests. No application, shared Just, deployment, live
  environment, or OpenAPI generator change.

- Merge after exact-head CI/review, then verify credential-free install/start.
  Abort on secrets, 1Password, nonlocal DBs, escapes, or mutable images.
- Evidence: boundary/URL/Compose tests, shell checks, CI, and durable exact-SHA
  review. No live migration or deployment is authorized.
- Launchers/images are digest-pinned; tools are exact-version and root-confined.
  Mise/upstream payloads, apt, Playwright, writable dependencies, and the outer
  interpreter remain development trust, not a sandbox.
- On failure, disable entrypoints, rebuild agents, rotate possibly exposed
  credentials, and roll forward; never restore PR #257's exposure. Follow up by
  locking OpenAPI codegen and evaluating a repository-verified browser artifact.
