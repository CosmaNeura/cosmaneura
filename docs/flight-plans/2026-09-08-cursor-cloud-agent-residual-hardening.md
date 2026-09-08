# Flight plan: Cursor Cloud Agent residual host hardening

- Tier 2 security-boundary hardening from protected `master`
  `fd620913fc628957a3a9809f24a2e22e8799ed6c`.
- Scope is limited to the synthetic-only, credential-neutral Cursor Cloud Agent
  bootstrap and focused tests. It changes no application runtime, live schema,
  staging or production resource, credential, PHI path, or deployment workflow.

## Outcome and gates

- Replace the world-writable Docker socket with an exact root/current-group
  `0660` socket and fail closed on an unexpected socket object or metadata.
- Execute the digest-verified Localias binary through a minimal environment and
  trust its generated CA only in the agent's isolated Chromium/NSS profile.
- Reject incomplete or symlinked nested-Docker configuration and missing engine
  components instead of silently accepting them.
- Reconcile both synthetic development and test schemas after branch changes.
- Merge only after focused tests, shell checks, all required CI, and independent
  exact-head review have passed.

## Residual boundary and recovery

The checked-out repository scripts, signed apt repositories, Mise-managed
payloads, Playwright browser archive, writable dependencies, and platform-owned
outer interpreter remain trusted development inputs. This disposable host is
credential-neutral and synthetic-only; it is not an OS sandbox. Direnv approval
does not create a meaningful boundary because the platform has already executed
the same checked-out `.cursor` code before Direnv starts.

On failure, disable the Cursor entrypoints, discard and rebuild affected Cloud
Agents, and roll forward. Never restore PR #257's world-writable socket,
host-wide Localias trust, mutable installers, or credential-bearing execution.
No live migration, staging deployment, or production action is authorized by
this change.
